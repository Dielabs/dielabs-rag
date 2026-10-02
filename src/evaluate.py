"""Valutazione con il set di domande di prova (Fase 1, Blocchi 7-8).

Per ogni domanda di eval/questions.yaml:
- ricerca: la pagina attesa sta nei primi K risultati? (conta anche "stesso testo anche in")
- con --answer: la risposta cita la pagina attesa? (chiama OpenRouter, costa)
Le domande del gruppo no_answer non hanno pagina attesa: si valutano solo con --answer,
stampando l'inizio della risposta da giudicare a mano.
Stampa una riga per domanda e il punteggio per gruppo; salva il dettaglio in data/logs/eval/.
"""
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone

import yaml

from answer import ask
from search import search
from sources import ROOT

QUESTIONS = ROOT / "eval" / "questions.yaml"
LOG_DIR = ROOT / "data" / "logs" / "eval"


def pages_of(result: dict) -> set[str]:
    return {result["path"], *result.get("also_in", [])}


def first_hit(results: list[dict], expected: list[str]) -> int | None:
    for r in results:
        if pages_of(r) & set(expected):
            return r["rank"]
    return None


def evaluate_answer(q: dict, s_log: dict) -> dict:
    a_log, sections = ask(q["question"], q["software"], q["version"])
    out = {"cost_usd": a_log["cost_usd"], "finish_reason": a_log["finish_reason"],
           "tokens_reasoning": a_log["tokens_reasoning"], "answer": a_log["answer"]}
    if not a_log["answer"].strip():
        return {**out, "answer_empty": True, "answer_cites_expected": False, "cited_pages": []}
    by_id = {r["section_id"]: r for r in s_log["results"]}
    cited_pages = set()
    for n in a_log["citations"]["cited"]:
        r = by_id.get(sections[n - 1]["section_id"])
        if r:
            cited_pages |= pages_of(r)
    return {**out, "answer_empty": False, "cited_pages": sorted(cited_pages),
            "answer_cites_expected": bool(cited_pages & set(q["expected"]))}


def run(k: int, with_answer: bool, only: str | None, group: str | None) -> dict:
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    for q in questions:
        q.setdefault("group", "base")
    if only:
        questions = [q for q in questions if q["software"] == only]
    if group:
        questions = [q for q in questions if q["group"] == group]
    if not with_answer:
        questions = [q for q in questions if q["expected"]]
    rows = []
    for q in questions:
        s_log = search(q["question"], q["software"], q["version"])
        row = {"id": q["id"], "group": q["group"], "question": q["question"], "expected": q["expected"],
               "top": [r["path"] for r in s_log["results"][:k]]}
        if q["expected"]:
            rank = first_hit(s_log["results"], q["expected"])
            row.update({"rank": rank, "hit": rank is not None and rank <= k})
        if with_answer:
            row.update(evaluate_answer(q, s_log))
        rows.append(row)
    groups = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    summary = {"k": k, "groups": {}}
    for g, rs in groups.items():
        gs = {"questions": len(rs)}
        scored = [r for r in rs if r["expected"]]
        if scored:
            gs["hits"] = sum(r["hit"] for r in scored)
            gs["mrr"] = round(sum(1 / r["rank"] for r in scored if r["rank"]) / len(scored), 3)
            if with_answer:
                gs["answers_citing_expected"] = sum(r["answer_cites_expected"] for r in scored)
        summary["groups"][g] = gs
    if with_answer:
        summary["cost_usd"] = round(sum(r["cost_usd"] or 0 for r in rows), 5)
        summary["empty_answers"] = sum(r["answer_empty"] for r in rows)
    log = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summary, "rows": rows}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = log["at"].replace(":", "").replace("-", "").replace("+0000", "Z")
    (LOG_DIR / f"{stamp}.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    return log


def show(log: dict) -> None:
    s = log["summary"]
    for r in log["rows"]:
        if not r["expected"]:
            print(f"??  {r['id']:<14} da giudicare a mano"
                  + (f", risposta VUOTA ({r['finish_reason']})" if r.get("answer_empty") else ":"))
            if not r.get("answer_empty"):
                print("      " + " ".join(r["answer"].split())[:300])
            continue
        mark = "OK " if r["hit"] else "NO "
        pos = f"pos {r['rank']}" if r["rank"] else "non nei primi 10"
        line = f"{mark} {r['id']:<14} {pos:<17}"
        if "answer_empty" in r:
            line += " risposta: " + ("VUOTA (" + str(r["finish_reason"]) + ")" if r["answer_empty"]
                                     else "cita la pagina" if r["answer_cites_expected"] else "NON cita la pagina")
        print(line)
        if not r["hit"]:
            print(f"      attesa {r['expected']}, primi: {r['top'][:3]}")
    print()
    for g, gs in s["groups"].items():
        if "hits" not in gs:
            print(f"{g:<10} {gs['questions']} domande da giudicare a mano")
            continue
        line = f"{g:<10} ricerca {gs['hits']}/{gs['questions']} nei primi {s['k']}, MRR {gs['mrr']}"
        if "answers_citing_expected" in gs:
            line += f"; risposta {gs['answers_citing_expected']}/{gs['questions']} cita la pagina attesa"
        print(line)
    if "cost_usd" in s:
        print(f"costo {s['cost_usd']} $, risposte vuote {s['empty_answers']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Valutazione con il set di domande di prova")
    p.add_argument("--k", type=int, default=5, help="quanti primi risultati contano (default 5)")
    p.add_argument("--answer", action="store_true", help="valuta anche la risposta (chiama OpenRouter, costa)")
    p.add_argument("--software", help="solo le domande di questo software")
    p.add_argument("--group", help="solo le domande di questo gruppo (base, vague, italian, no_answer)")
    a = p.parse_args()
    show(run(a.k, a.answer, a.software, a.group))
