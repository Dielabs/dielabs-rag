"""Ricerca su una KB (ADR-0002, Blocco 5).

Una KB per richiesta: software e versione. Ricerca ibrida in Qdrant (denso bge-m3 + BM25, fusione RRF),
deduplica dei testi identici dentro la KB, reranker sui testi distinti, nessuna soglia sul punteggio,
un risultato per sezione (le parti di una sezione lunga si uniscono).
Scrive un log verboso per richiesta in data/logs/search/ (ADR-0004).
"""
import argparse
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone

from qdrant_client import QdrantClient, models

from load import embed, kb_filter, load_config
from sources import ROOT, call_infinity, check_infinity, load as load_source

LOG_DIR = ROOT / "data" / "logs" / "search"


def rerank(query: str, texts: list[str], cfg: dict) -> list[float]:
    body = json.dumps({"model": cfg["model"], "query": query, "documents": texts}).encode()
    req = urllib.request.Request(cfg["url"].rstrip("/") + "/rerank", data=body,
                                 headers={"Content-Type": "application/json"})
    with call_infinity(req, cfg.get("timeout", 120)) as r:
        results = json.load(r)["results"]
    scores = [0.0] * len(texts)
    for x in results:
        scores[x["index"]] = x["relevance_score"]
    return scores


def hybrid(client: QdrantClient, query: str, software: str, version: str, cfg: dict) -> list:
    q, e, s = cfg["qdrant"], cfg["embedding"], cfg["search"]
    flt = kb_filter(software, version)
    dense = embed([query], e)[0]
    return client.query_points(
        q["collection"],
        prefetch=[
            models.Prefetch(query=dense, using="dense", limit=s["prefetch"], filter=flt),
            models.Prefetch(query=models.Document(text=query, model="qdrant/bm25"), using="bm25",
                            limit=s["prefetch"], filter=flt),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=s["candidates"], with_payload=True,
    ).points


def prefer_rank(path: str, prefer_paths: list[str]) -> int:
    """Posizione in prefer_paths del primo prefisso che combacia; oltre la lista se nessuno."""
    for i, p in enumerate(prefer_paths):
        if path.startswith(p):
            return i
    return len(prefer_paths)


def dedupe(points: list, prefer_paths: list[str]) -> list[dict]:
    """Un risultato per testo identico. Resta la copia preferita da prefer_paths (a parità, la prima
    nell'ordine ibrido); le altre pagine finiscono in also_in."""
    groups: dict[str, list] = {}
    for rank, p in enumerate(points):
        key = hashlib.sha1(p.payload["text"].encode("utf-8")).hexdigest()
        groups.setdefault(key, []).append((rank, p))
    out = []
    for members in groups.values():
        members.sort(key=lambda m: (prefer_rank(m[1].payload["path"], prefer_paths), m[0]))
        rank, p = members[0]
        out.append({
            "hybrid_rank": rank + 1, "hybrid_score": p.score, "payload": p.payload,
            "also_in": [m[1].payload["path"] for m in members[1:]],
        })
    out.sort(key=lambda r: r["hybrid_rank"])
    return out


def merge_parts(ranked: list[dict]) -> list[dict]:
    """Le parti di una stessa sezione (stesso section_id) diventano un risultato solo: resta la parte
    con il punteggio più alto, cioè la prima nell'ordine del reranker."""
    seen, out = set(), []
    for r in ranked:
        sid = r["payload"]["section_id"]
        if sid in seen:
            continue
        seen.add(sid)
        out.append(r)
    return out


def search(query: str, software: str, version: str) -> dict:
    cfg = load_config()
    for url in {cfg["embedding"]["url"], cfg["reranker"]["url"]}:
        check_infinity(url)          # GPU spenta: errore chiaro in pochi secondi
    s = cfg["search"]
    prefer_paths = load_source(software).get("search", {}).get("prefer_paths", [])
    t0 = time.perf_counter()
    client = QdrantClient(url=cfg["qdrant"]["url"], timeout=cfg["qdrant"]["timeout"])
    points = hybrid(client, query, software, version, cfg)
    t1 = time.perf_counter()
    distinct = dedupe(points, prefer_paths)
    scores = rerank(query, [r["payload"]["text"] for r in distinct], cfg["reranker"])
    t2 = time.perf_counter()
    for r, sc in zip(distinct, scores):
        r["rerank_score"] = sc
    ranked = merge_parts(sorted(distinct, key=lambda r: -r["rerank_score"]))
    results = []
    for i, r in enumerate(ranked[: s["top"]], 1):
        pl = r["payload"]
        results.append({
            "rank": i, "rerank_score": round(r["rerank_score"], 4), "hybrid_rank": r["hybrid_rank"],
            "software": pl["software"], "version": pl["version"], "path": pl["path"],
            "title": pl["title"], "headings": pl["headings"], "anchor": pl["anchor"],
            "section_url": pl["section_url"], "section_id": pl["section_id"],
            "section_part": pl["section_part"], "section_parts": pl["section_parts"],
            "also_in": r["also_in"], "text": pl["text"],
        })
    log = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "query": query, "software": software, "version": version,
        "candidates": len(points), "distinct": len(distinct), "sections": len(ranked), "returned": len(results),
        "seconds_hybrid": round(t1 - t0, 3), "seconds_rerank": round(t2 - t1, 3),
        "hybrid": [{"rank": i + 1, "score": p.score, "path": p.payload["path"], "anchor": p.payload["anchor"],
                    "section_id": p.payload["section_id"], "section_part": p.payload["section_part"]}
                   for i, p in enumerate(points)],
        "reranked": [{"rank": i + 1, "score": round(r["rerank_score"], 4), "hybrid_rank": r["hybrid_rank"],
                      "path": r["payload"]["path"], "anchor": r["payload"]["anchor"], "also_in": r["also_in"]}
                     for i, r in enumerate(ranked)],
        "results": results,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = log["at"].replace(":", "").replace("-", "").replace("+0000", "Z")
    (LOG_DIR / f"{stamp}_{software}_{version}.json").write_text(json.dumps(log, indent=2, ensure_ascii=False),
                                                               encoding="utf-8")
    return log


def show(log: dict) -> None:
    print(f"{log['software']} {log['version']} — {log['query']}")
    print(f"{log['candidates']} candidati, {log['distinct']} testi distinti, {log['sections']} sezioni, "
          f"ricerca {log['seconds_hybrid']} s, reranker {log['seconds_rerank']} s\n")
    for r in log["results"]:
        print(f"{r['rank']:>2}. [{r['rerank_score']:.2f}] {' > '.join(r['headings'])}")
        print(f"    {r['section_url']}")
        if r["also_in"]:
            print(f"    stesso testo anche in: {', '.join(r['also_in'])}")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Ricerca su una KB (ADR-0002)")
    p.add_argument("software")
    p.add_argument("version")
    p.add_argument("query")
    p.add_argument("--json", action="store_true", help="stampa il log completo in JSON invece dell'elenco")
    a = p.parse_args()
    log = search(a.query, a.software, a.version)
    print(json.dumps(log, indent=2, ensure_ascii=False)) if a.json else show(log)
