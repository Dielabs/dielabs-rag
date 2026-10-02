"""Risposta con citazioni su una KB (Blocco 6, ADR-0003, ADR-0004, ADR-0007).

Ricerca (search.py), sezioni intere ricostruite da Qdrant, prompt con sezioni numerate, GLM 5.3 Flash
su OpenRouter (solo provider FP8), controllo delle citazioni, log verboso in data/logs/answer/.
"""
import argparse
import json
import re
import time
import urllib.request
from datetime import datetime, timezone

from qdrant_client import QdrantClient, models

from load import kb_filter, load_config
from search import search
from sources import ROOT, load as load_source

LOG_DIR = ROOT / "data" / "logs" / "answer"

SYSTEM = """You answer questions about the official documentation of one software, one version.
Use ONLY the numbered documentation sections provided. Cite them inline with their numbers, like [2] or [1][3],
every time you state a fact taken from them. If the sections do not contain the answer, say so plainly and do
not guess. Do not mention sections that you did not use. Answer in the language of the question."""


def read_env() -> dict:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def fetch_section(client: QdrantClient, cfg: dict, software: str, version: str, section_id: str) -> str:
    """Testo intero della sezione: tutte le parti in ordine, senza la sovrapposizione."""
    flt = kb_filter(software, version)
    flt.must.append(models.FieldCondition(key="section_id", match=models.MatchValue(value=section_id)))
    points, _ = client.scroll(cfg["qdrant"]["collection"], scroll_filter=flt, limit=64, with_payload=True)
    parts = sorted((p.payload for p in points), key=lambda pl: pl["section_part"])
    text = parts[0]["text"]
    for pl in parts[1:]:
        text += "\n\n" + pl["text"][pl["overlap"]:]
    return text


def build_context(results: list[dict], client: QdrantClient, cfg: dict, software: str, version: str) -> list[dict]:
    a = cfg["answer"]
    name = load_source(software).get("display_name", software)
    sections, total = [], 0
    for r in results[: a["sections"]]:
        text = fetch_section(client, cfg, software, version, r["section_id"])
        truncated = False
        if len(text) > a["max_chars_section"]:
            text, truncated = text[: a["max_chars_section"]] + "\n[sezione tagliata]", True
        if total + len(text) > a["max_chars_total"]:
            room = a["max_chars_total"] - total
            if room < 500:
                break
            text, truncated = text[:room] + "\n[sezione tagliata]", True
        total += len(text)
        sections.append({
            "n": len(sections) + 1, "software": name, "version": version, "page": r["title"],
            "section": " > ".join(r["headings"][1:]) or r["title"], "url": r["section_url"],
            "section_id": r["section_id"],
            "chars": len(text), "truncated": truncated, "rerank_score": r["rerank_score"], "text": text,
        })
    return sections


def make_prompt(query: str, sections: list[dict]) -> str:
    blocks = []
    for s in sections:
        blocks.append(f"[{s['n']}] {s['software']} {s['version']} — page \"{s['page']}\", section \"{s['section']}\"\n"
                      f"{s['text']}")
    return "Documentation sections:\n\n" + "\n\n-----\n\n".join(blocks) + f"\n\nQuestion: {query}"


def call_openrouter(prompt: str, cfg: dict, key: str) -> dict:
    o = cfg["openrouter"]
    body = {
        "model": o["model"],
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "max_tokens": o["max_tokens"], "temperature": o["temperature"],
        "provider": {"quantizations": o["quantizations"]},
        "usage": {"include": True},
    }
    req = urllib.request.Request(o["url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                                          "HTTP-Referer": "https://dielabs.eu", "X-Title": "Dielabs RAG"})
    with urllib.request.urlopen(req, timeout=o.get("timeout", 120)) as r:
        return json.load(r)


def generation_details(gen_id: str, cfg: dict, key: str) -> dict:
    """Dettagli della generazione da OpenRouter (provider, precisione, costo); vuoto se non disponibili."""
    req = urllib.request.Request(cfg["openrouter"]["url"].rstrip("/") + f"/generation?id={gen_id}",
                                 headers={"Authorization": f"Bearer {key}"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get("data", {})
        except Exception:
            time.sleep(1.5)
    return {}


def provider_quantization(provider: str, cfg: dict, key: str) -> str | None:
    """Precisione dichiarata dal provider per il modello, dalla lista degli endpoint di OpenRouter
    (la risposta e /generation non la riportano)."""
    o = cfg["openrouter"]
    req = urllib.request.Request(o["url"].rstrip("/") + f"/models/{o['model']}/endpoints",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            for e in json.load(r)["data"]["endpoints"]:
                if e.get("provider_name") == provider:
                    return e.get("quantization")
    except Exception:
        pass
    return None


def check_citations(text: str, n_sections: int) -> dict:
    cited = sorted({int(x) for x in re.findall(r"\[(\d+)\]", text)})
    return {"cited": [c for c in cited if 1 <= c <= n_sections],
            "invalid": [c for c in cited if not 1 <= c <= n_sections]}


def ask(query: str, software: str, version: str) -> dict:
    cfg = load_config()
    key = read_env().get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY mancante in .env")
    t0 = time.perf_counter()
    s_log = search(query, software, version)
    t1 = time.perf_counter()
    client = QdrantClient(url=cfg["qdrant"]["url"], timeout=cfg["qdrant"]["timeout"])
    sections = build_context(s_log["results"], client, cfg, software, version)
    prompt = make_prompt(query, sections)
    t2 = time.perf_counter()
    resp = call_openrouter(prompt, cfg, key)
    t3 = time.perf_counter()
    choice = resp["choices"][0]
    # il modello a volte restituisce content vuoto (None), es. se il ragionamento esaurisce max_tokens
    answer = choice["message"].get("content") or ""
    details = generation_details(resp.get("id", ""), cfg, key) if resp.get("id") else {}
    usage = resp.get("usage", {})
    log = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "query": query, "software": software, "version": version,
        "search_log": s_log["at"],
        "sections": [{k: v for k, v in s.items() if k != "text"} for s in sections],
        "context_chars": sum(s["chars"] for s in sections),
        "model": resp.get("model"), "provider": resp.get("provider"),
        "quantization": provider_quantization(resp.get("provider"), cfg, key),
        "provider_name": details.get("provider_name"), "finish_reason": details.get("finish_reason") or choice.get("finish_reason"),
        "tokens_reasoning": usage.get("completion_tokens_details", {}).get("reasoning_tokens"),
        "tokens_prompt": usage.get("prompt_tokens"), "tokens_completion": usage.get("completion_tokens"),
        "cost_usd": usage.get("cost", details.get("total_cost")),
        "seconds_search": round(t1 - t0, 3), "seconds_context": round(t2 - t1, 3),
        "seconds_generation": round(t3 - t2, 3),
        "citations": check_citations(answer, len(sections)),
        "answer": answer, "prompt": prompt, "generation_id": resp.get("id"),
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = log["at"].replace(":", "").replace("-", "").replace("+0000", "Z")
    (LOG_DIR / f"{stamp}_{software}_{version}.json").write_text(json.dumps(log, indent=2, ensure_ascii=False),
                                                               encoding="utf-8")
    return log, sections


def show(log: dict, sections: list[dict]) -> None:
    print(log["answer"].strip())
    print("\nFonti:")
    for s in sections:
        mark = "" if s["n"] in log["citations"]["cited"] else " (non citata)"
        print(f"  [{s['n']}] {s['software']} {s['version']} — {s['page']} > {s['section']}{mark}")
        print(f"      {s['url']}")
    if log["citations"]["invalid"]:
        print(f"\nAttenzione: citati numeri inesistenti: {log['citations']['invalid']}")
    print(f"\n{log['provider']} ({log['quantization'] or 'precisione n/d'}), "
          f"token {log['tokens_prompt']}+{log['tokens_completion']}, costo {log['cost_usd']} $, "
          f"ricerca {log['seconds_search']} s, generazione {log['seconds_generation']} s")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Risposta con citazioni su una KB")
    p.add_argument("software")
    p.add_argument("version")
    p.add_argument("query")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    log, sections = ask(a.query, a.software, a.version)
    print(json.dumps(log, indent=2, ensure_ascii=False)) if a.json else show(log, sections)
