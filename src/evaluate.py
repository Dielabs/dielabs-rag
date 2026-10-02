"""Valutazione con il set di domande di prova (Fase 1, Blocco 7).

Per ogni domanda di eval/questions.yaml:
- ricerca: la pagina attesa sta nei primi K risultati? (conta anche "stesso testo anche in")
- con --answer: la risposta cita la pagina attesa? (chiama OpenRouter, costa)
Stampa una riga per domanda e il punteggio; salva il dettaglio in data/logs/eval/.
"""
import argparse
import json
from datetime import datetime, timezone

import yaml

from answer import ask
from sources import ROOT
from search import search

QUESTIONS = ROOT / "eval" / "questions.yaml"
LOG_DIR = ROOT / "data" / "logs" / "eval"


def pages_of(result: dict) -> set[str]:
    return {result["path"], *result.get("also_in", [])}


def first_hit(results: list[dict], expected: list[str]) -> int | None:
    for r in results:
        if pages_of(r) & set(expected):
            return r["rank"]
    return None


def run(k: int, with_answer: bool, only: str | None) -> dict:
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    if only:
        questions = [q for q in questions if q["software"] == only]
    rows = []
    for q in questions:
        s_log = search(q["question"], q["software"], q["version"])
        rank = first_hit(s_log["results"], q["expected"])
        row = {"id": q["id"], "question": q["question"], "expected": q["expected"],
               "rank": rank, "hit": rank is not None and rank <= k,
               "top": [r["path"] for r in s_log["results"][:k]]}
        if with_answer:
            by_id = {r["section_id"]: r for r in s_log["results"]}
            a_log, sections = ask(q["question"], q["software"], q["version"])
            if not a_log["answer"].strip():
                row.update({"answer_cites_expected": False, "cited_pages": [], "cost_usd": a_log["cost_usd"],
                            "answer_empty": True, "finish_reason": a_log["finish_reason"],
                            "tokens_reasoning": a_log["tokens_reasoning"]})
                rows.append(row)
                continue
            cited = [sections[n - 1] for n in a_log["citations"]["cited"]]
            cited_pages = set()
            for s in cited:
                r = by_id.get(s["section_id"])
                if r:
                    cited_pages |= pages_of(r)
            row.update({"answer_cites_expected": bool(cited_pages & set(q["expected"])),
                        "cited_pages": sorted(cited_pages), "cost_usd": a_log["cost_usd"]})
        rows.append(row)
    n = len(rows)
    summary = {"questions": n, "k": k, "hits": sum(r["hit"] for r in rows),
               "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3) if n else 0}
    if with_answer:
        summary["answers_citing_expected"] = sum(r["answer_cites_expected"] for r in rows)
        summary["cost_usd"] = round(sum(r["cost_usd"] or 0 for r in rows), 5)
    log = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summary, "rows": rows}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = log["at"].replace(":", "").replace("-", "").replace("+0000", "Z")
    (LOG_DIR / f"{stamp}.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    return log


def show(log: dict) -> None:
    s = log["summary"]
    for r in log["rows"]:
        mark = "OK " if r["hit"] else "NO "
        pos = f"pos {r['rank']}" if r["rank"] else "non nei primi 10"
        line = f"{mark} {r['id']:<12} {pos:<17}"
        if "answer_cites_expected" in r:
            line += " risposta: " + ("VUOTA (" + str(r["finish_reason"]) + ")" if r.get("answer_empty")
                                     else "cita la pagina" if r["answer_cites_expected"] else "NON cita la pagina")
        print(line)
        if not r["hit"]:
            print(f"      attesa {r['expected']}, primi: {r['top'][:3]}")
    print(f"\nRicerca: {s['hits']}/{s['questions']} con la pagina attesa nei primi {s['k']}, MRR {s['mrr']}")
    if "answers_citing_expected" in s:
        print(f"Risposta: {s['answers_citing_expected']}/{s['questions']} citano la pagina attesa, "
              f"costo {s['cost_usd']} $")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Valutazione con il set di domande di prova")
    p.add_argument("--k", type=int, default=5, help="quanti primi risultati contano (default 5)")
    p.add_argument("--answer", action="store_true", help="valuta anche la risposta (chiama OpenRouter, costa)")
    p.add_argument("--software", help="solo le domande di questo software")
    a = p.parse_args()
    show(run(a.k, a.answer, a.software))
