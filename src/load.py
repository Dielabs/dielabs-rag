"""Caricamento dei pezzi di una KB in Qdrant (ADR-0002, ADR-0006, ADR-0007).

Input: data/chunks/<software>/<versione>/chunks.jsonl.
Per ogni pezzo: vettore denso da bge-m3 su Infinity, vettore sparso BM25 calcolato da Qdrant,
payload con i campi dell'ADR-0007. Prima di caricare toglie i punti già presenti di quella KB,
così un pezzo sparito dalla documentazione non resta nell'indice (gli indici sono cache).
Indirizzi e parametri in config.yaml.
"""
import argparse
import json
import time
import urllib.request

import yaml
from qdrant_client import QdrantClient, models

from sources import ROOT, call_infinity

DATA = ROOT / "data"
PAYLOAD_SKIP = {"id", "embed_text"}
INDEXED = ("software", "version", "section_id")


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def embed(texts: list[str], cfg: dict) -> list[list[float]]:
    body = json.dumps({"model": cfg["model"], "input": texts}).encode()
    req = urllib.request.Request(cfg["url"].rstrip("/") + "/embeddings", data=body,
                                 headers={"Content-Type": "application/json"})
    with call_infinity(req, cfg.get("timeout", 300)) as r:
        data = json.load(r)["data"]
    return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    if not client.collection_exists(name):
        client.create_collection(
            name,
            vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
    for field in INDEXED:
        client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)


def kb_filter(software: str, version: str) -> models.Filter:
    return models.Filter(must=[
        models.FieldCondition(key="software", match=models.MatchValue(value=software)),
        models.FieldCondition(key="version", match=models.MatchValue(value=version)),
    ])


def run(software: str, version: str) -> dict:
    cfg = load_config()
    q, e = cfg["qdrant"], cfg["embedding"]
    src = DATA / "chunks" / software / version / "chunks.jsonl"
    if not src.exists():
        raise SystemExit(f"Pezzi non trovati: {src}. Lancia prima make chunks.")
    chunks = [json.loads(l) for l in open(src, encoding="utf-8")]
    client = QdrantClient(url=q["url"], timeout=q.get("timeout", 120))
    t0 = time.time()
    dim = len(embed(["dimension probe"], e)[0])
    ensure_collection(client, q["collection"], dim)
    before = client.count(q["collection"], count_filter=kb_filter(software, version), exact=True).count
    client.delete(q["collection"], points_selector=models.FilterSelector(filter=kb_filter(software, version)), wait=True)
    print(f"{software} {version}: tolti {before} punti vecchi, carico {len(chunks)} pezzi", flush=True)
    bs, t_embed, t_upsert = e["batch_size"], 0.0, 0.0
    for i in range(0, len(chunks), bs):
        batch = chunks[i:i + bs]
        t = time.time()
        vecs = embed([c["embed_text"] for c in batch], e)
        t_embed += time.time() - t
        points = [models.PointStruct(
            id=c["id"],
            vector={"dense": v, "bm25": models.Document(text=c["embed_text"], model="qdrant/bm25")},
            payload={k: val for k, val in c.items() if k not in PAYLOAD_SKIP},
        ) for c, v in zip(batch, vecs)]
        t = time.time()
        client.upsert(q["collection"], points=points, wait=True)
        t_upsert += time.time() - t
        done = i + len(batch)
        if done % (bs * 20) == 0 or done == len(chunks):
            print(f"  {done}/{len(chunks)} ({time.time() - t0:.0f} s)", flush=True)
    count = client.count(q["collection"], count_filter=kb_filter(software, version), exact=True).count
    summary = {"software": software, "version": version, "chunks": len(chunks), "points": count,
               "removed_before": before, "seconds_total": round(time.time() - t0, 1),
               "seconds_embedding": round(t_embed, 1), "seconds_upsert": round(t_upsert, 1),
               "collection": q["collection"], "dense_dim": dim}
    (DATA / "chunks" / software / version / "load.json").write_text(json.dumps(summary, indent=2))
    if count != len(chunks):
        raise SystemExit(f"Punti in Qdrant ({count}) diversi dai pezzi ({len(chunks)})")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Carica i pezzi di una KB in Qdrant")
    p.add_argument("software")
    p.add_argument("version")
    a = p.parse_args()
    print(json.dumps(run(a.software, a.version), indent=2))
