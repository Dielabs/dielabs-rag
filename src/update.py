"""Aggiornamento delle KB di un software (Fase 2, Blocco 10; ADR-0001, ADR-0010).

Guarda le versioni da tenere secondo la scheda (tag su GitHub), costruisce e carica quelle nuove,
poi toglie quelle uscite dalla lista. Prima carica, poi toglie: non c'è mai un momento senza
documentazione, e le domande restano possibili durante l'aggiornamento. Se un'aggiunta fallisce
non si toglie niente.

Il modo di prendere la documentazione dipende dal tipo di sorgente della scheda (`type`, oggi solo
`mkdocs`): per un software con un altro sistema (es. Fern, Sphinx) si aggiunge un costruttore
in CORPUS_BUILDERS, senza toccare il resto.

    python src/update.py vllm --plan     solo cosa farebbe
    python src/update.py vllm            aggiorna
"""
import argparse
import fcntl
import json
import shutil
import time
from datetime import datetime, timezone

from qdrant_client import QdrantClient, models

import build as build_mod
import chunk as chunk_mod
import extract as extract_mod
import load as load_mod
from sources import ROOT, load as load_source, resolve_versions

DATA = ROOT / "data"
LOCK = DATA / "update.lock"
LOG_DIR = DATA / "logs" / "update"


def corpus_mkdocs(software: str, version: str, step) -> None:
    step(f"Costruisco la documentazione {version}")
    build_mod.build(software, version)
    step(f"Estraggo le pagine di {version}")
    extract_mod.extract(software, version)


CORPUS_BUILDERS = {"mkdocs": corpus_mkdocs}


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def loaded_versions(software: str) -> list[str]:
    """Le versioni caricate in Qdrant: quelle con il caricamento completo (load.json)."""
    return sorted((d.parent.name for d in (DATA / "chunks" / software).glob("*/load.json")), key=vkey)


def plan(software: str) -> dict:
    source = load_source(software)
    kind = source.get("type", "mkdocs")
    if kind not in CORPUS_BUILDERS:
        raise SystemExit(f"Tipo di sorgente non gestito: {kind}")
    wanted = resolve_versions(source)
    if not wanted:
        raise SystemExit("Nessuna versione da tenere: non aggiorno.")
    present = loaded_versions(software)
    return {"software": software, "display_name": source.get("display_name", software), "type": kind,
            "wanted": wanted, "present": present,
            "add": sorted(set(wanted) - set(present), key=vkey),
            "remove": sorted(set(present) - set(wanted), key=vkey)}


def remove_version(software: str, version: str) -> dict:
    cfg = load_mod.load_config()["qdrant"]
    client = QdrantClient(url=cfg["url"], timeout=cfg.get("timeout", 120))
    flt = load_mod.kb_filter(software, version)
    before = client.count(cfg["collection"], count_filter=flt, exact=True).count
    client.delete(cfg["collection"], points_selector=models.FilterSelector(filter=flt), wait=True)
    removed_dirs = []
    for kind in ("chunks", "corpus", "build", "src"):
        d = DATA / kind / software / version
        if d.exists():
            shutil.rmtree(d)
            removed_dirs.append(str(d.relative_to(ROOT)))
    reports = []
    for r in (DATA / "reports" / software).glob("*.md"):
        if version in r.stem.split("_"):
            r.unlink()
            reports.append(r.name)
    return {"version": version, "points_removed": before, "dirs": removed_dirs, "reports": reports}


def run(software: str, on_event=print) -> dict:
    """Aggiorna un software. on_event riceve dict: plan, step, done, error."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("C'è già un aggiornamento in corso.")
    t0 = time.time()
    log = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "software": software, "steps": []}

    def step(text: str) -> None:
        log["steps"].append({"text": text, "seconds": round(time.time() - t0, 1)})
        on_event({"type": "step", "text": text})

    try:
        p = plan(software)
        log["plan"] = p
        on_event({"type": "plan", **p})
        builder = CORPUS_BUILDERS[p["type"]]
        for v in p["add"]:
            builder(software, v, step)
            step(f"Taglio in pezzi {v}")
            chunk_mod.run(software, v)
            step(f"Carico {v} in Qdrant")
            log.setdefault("loaded", []).append(load_mod.run(software, v))
        for v in p["remove"]:
            step(f"Tolgo {v}")
            log.setdefault("removed", []).append(remove_version(software, v))
        log["seconds"] = round(time.time() - t0, 1)
        on_event({"type": "done", "add": p["add"], "remove": p["remove"], "seconds": log["seconds"]})
        return log
    except BaseException as e:          # build ed estrazione segnalano gli errori con SystemExit
        log["error"] = f"{type(e).__name__}: {e}"
        on_event({"type": "error", "message": str(e) or type(e).__name__})
        raise
    finally:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = log["at"].replace(":", "").replace("-", "").replace("+0000", "Z")
        (LOG_DIR / f"{stamp}_{software}.json").write_text(json.dumps(log, indent=2, ensure_ascii=False))
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    a = argparse.ArgumentParser(description="Aggiorna le KB di un software")
    a.add_argument("software")
    a.add_argument("--plan", action="store_true", help="mostra solo cosa farebbe")
    args = a.parse_args()
    if args.plan:
        print(json.dumps(plan(args.software), indent=2))
    else:
        run(args.software, on_event=lambda e: print(json.dumps(e, ensure_ascii=False), flush=True))
