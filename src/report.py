"""Report delle differenze tra due versioni dello stesso software (ADR-0008, Blocco 4).

Input: data/chunks/<software>/<A|B>/chunks.jsonl. Output: data/reports/<software>/<A>_<B>.md.
Nessun LLM: le sezioni si ricostruiscono dai pezzi e si confrontano riga per riga. Una sezione si
riconosce da pagina, ancora e ordine nella pagina (l'ancora può ripetersi nella stessa pagina).
"""
import argparse
import difflib
import json
import re
from collections import defaultdict
from datetime import date

from sources import ROOT, load as load_source

DATA = ROOT / "data"
SIMILAR = 0.8  # soglia per confermare una sezione spostata o rinominata


def load(software: str, version: str) -> dict:
    """Sezioni della versione: chiave (path, anchor, occorrenza) -> dati e testo ricostruito dai pezzi."""
    parts = defaultdict(list)
    for line in open(DATA / "chunks" / software / version / "chunks.jsonl", encoding="utf-8"):
        c = json.loads(line)
        parts[c["section_id"]].append(c)
    seen, out = defaultdict(int), {}
    for ps in sorted(parts.values(), key=lambda ps: (ps[0]["path"], ps[0]["position"])):
        ps.sort(key=lambda c: c["section_part"])
        first = ps[0]
        text = first["text"]
        for c in ps[1:]:
            text += "\n\n" + c["text"][c["overlap"]:]  # la sovrapposizione non va contata due volte
        k = (first["path"], first["anchor"])
        n = seen[k]
        seen[k] += 1
        out[k + (n,)] = {
            "path": first["path"], "page_title": first["title"], "url": first["url"],
            "title": first["headings"][-1] if first["headings"] else first["title"],
            "section_url": first["section_url"], "position": first["position"], "text": text,
        }
    return out


def diff_lines(a: str, b: str) -> list[str]:
    return [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def strip_version(text: str, version: str) -> str:
    minor = ".".join(version.split(".")[:2])
    text = re.sub(r"v?" + re.escape(version), "<V>", text)
    return re.sub(r"v?" + re.escape(minor) + r"(?!\d)", "<V>", text)


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def match_moved(gone: list, new: list, A: dict, B: dict) -> list[tuple]:
    """Coppie (sparita, nuova) con stessa ancora o stesso titolo, confermate dal testo (uguale o simile)."""
    pairs, used = [], set()
    for g in gone:
        best = None
        for n in new:
            if n in used:
                continue
            if g[1] != n[1] and norm_title(A[g]["title"]) != norm_title(B[n]["title"]):
                continue
            ratio = 1.0 if A[g]["text"] == B[n]["text"] else difflib.SequenceMatcher(
                None, A[g]["text"], B[n]["text"]).ratio()
            if ratio >= SIMILAR and (best is None or ratio > best[1]):
                best = (n, ratio)
        if best:
            pairs.append((g, best[0], best[1]))
            used.add(best[0])
    return pairs


def compare(software: str, a: str, b: str) -> dict:
    A, B = load(software, a), load(software, b)
    same = [k for k in A if k in B and A[k]["text"] == B[k]["text"]]
    changed = [k for k in A if k in B and A[k]["text"] != B[k]["text"]]
    gone = [k for k in A if k not in B]
    new = [k for k in B if k not in A]
    moved = match_moved(gone, new, A, B)
    gone = [k for k in gone if k not in {g for g, _, _ in moved}]
    new = [k for k in new if k not in {n for _, n, _ in moved}]
    diffs = {k: diff_lines(A[k]["text"], B[k]["text"]) for k in changed}
    noise = [k for k in changed if strip_version(A[k]["text"], a) == strip_version(B[k]["text"], b)]
    # modifiche comuni: stessa ancora e stesse righe cambiate, su più pagine
    groups = defaultdict(list)
    for k in changed:
        groups[(k[1], "\n".join(diffs[k]))].append(k)
    common = [ks for ks in groups.values() if len(ks) > 1]
    common.sort(key=lambda ks: (-len(ks), ks[0]))
    common_of = {k: i for i, ks in enumerate(common, 1) for k in ks}
    pages_a, pages_b = {k[0] for k in A}, {k[0] for k in B}
    return {
        "A": A, "B": B, "same": same, "changed": changed, "new": new, "gone": gone, "moved": moved,
        "diffs": diffs, "noise": noise, "common": common, "common_of": common_of,
        "pages_new": sorted(pages_b - pages_a), "pages_gone": sorted(pages_a - pages_b),
    }


def link(s: dict, url_key: str = "section_url") -> str:
    return f"[{s['title']}]({s[url_key]})"


def fence(lines: list[str]) -> list[str]:
    return ["~~~~diff", *lines, "~~~~"]


def render(software: str, a: str, b: str, r: dict) -> str:
    A, B = r["A"], r["B"]
    name = load_source(software).get("display_name", software)
    out = [f"# {name}: differenze {a} → {b}", ""]
    n_common_secs = sum(len(ks) for ks in r["common"])
    out.append(f"Calcolato il {date.today().isoformat()} dai pezzi in `data/chunks/`, senza LLM. "
               f"Sezioni: {len(r['same'])} uguali, {len(r['changed'])} cambiate"
               f" (di cui {n_common_secs} in {len(r['common'])} modifiche comuni), "
               f"{len(r['new'])} nuove, {len(r['gone'])} sparite, {len(r['moved'])} spostate o rinominate. "
               f"Pagine: {len(r['pages_new'])} nuove, {len(r['pages_gone'])} sparite. "
               f"Differenze dovute solo al numero di versione: {len(r['noise'])}.")
    page_title = {}
    for S in (A, B):
        for s in S.values():
            page_title.setdefault(s["path"], (s["page_title"], s["url"]))

    out += ["", f"## Pagine nuove ({len(r['pages_new'])})", ""]
    for p in r["pages_new"]:
        out.append(f"- [{page_title[p][0]}]({page_title[p][1]}) — `{p}`, {sum(1 for k in r['new'] if k[0] == p)} sezioni")
    out += ["", f"## Pagine sparite ({len(r['pages_gone'])})", ""]
    for p in r["pages_gone"]:
        out.append(f"- [{page_title[p][0]}]({page_title[p][1]}) — `{p}`, {sum(1 for k in r['gone'] if k[0] == p)} sezioni")

    out += ["", "## Per pagina", ""]
    by_page = defaultdict(list)
    for k in r["changed"]:
        by_page[k[0]].append(("Cambiata", k, A[k]["position"]))
    for k in r["new"]:
        if k[0] not in r["pages_new"]:
            by_page[k[0]].append(("Nuova", k, B[k]["position"]))
    for k in r["gone"]:
        if k[0] not in r["pages_gone"]:
            by_page[k[0]].append(("Sparita", k, A[k]["position"]))
    for p in sorted(by_page):
        out += [f"### `{p}` — {page_title[p][0]}", ""]
        for kind, k, _ in sorted(by_page[p], key=lambda x: x[2]):
            if kind == "Nuova":
                out.append(f"- **Nuova**: {link(B[k])}")
            elif kind == "Sparita":
                out.append(f"- **Sparita**: {link(A[k])}")
            elif k in r["common_of"]:
                out.append(f"- **Cambiata**: {link(B[k])} → vedi modifica comune n. {r['common_of'][k]}")
            else:
                out += [f"- **Cambiata**: {link(B[k])}", ""] + fence(r["diffs"][k]) + [""]
        out.append("")

    out += [f"## Modifiche comuni ({len(r['common'])})", "",
            "La stessa modifica in sezioni con la stessa ancora su più pagine, mostrata una volta sola.", ""]
    for i, ks in enumerate(r["common"], 1):
        out += [f"### n. {i} — {B[ks[0]]['title']} ({len(ks)} pagine)", "",
                "Pagine: " + ", ".join(f"[`{k[0]}`]({B[k]['section_url']})" for k in ks), ""]
        out += fence(r["diffs"][ks[0]]) + [""]

    out += [f"## Spostate o rinominate ({len(r['moved'])})", ""]
    for g, n, ratio in r["moved"]:
        out.append(f"- {link(A[g])} (`{g[0]}`) → {link(B[n])} (`{n[0]}`)"
                   + (" — testo uguale" if ratio == 1.0 else f" — testo simile al {ratio:.0%}"))
        if ratio < 1.0:
            out += [""] + fence(diff_lines(A[g]["text"], B[n]["text"])) + [""]
    return "\n".join(out) + "\n"


def run(software: str, a: str, b: str) -> dict:
    r = compare(software, a, b)
    out_dir = DATA / "reports" / software
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{a}_{b}.md"
    path.write_text(render(software, a, b, r), encoding="utf-8")
    return {
        "software": software, "from": a, "to": b,
        "same": len(r["same"]), "changed": len(r["changed"]), "new": len(r["new"]), "gone": len(r["gone"]),
        "moved": len(r["moved"]), "common_changes": len(r["common"]),
        "common_sections": sum(len(ks) for ks in r["common"]),
        "pages_new": len(r["pages_new"]), "pages_gone": len(r["pages_gone"]),
        "version_only_noise": len(r["noise"]), "output": str(path),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Report delle differenze tra due versioni (ADR-0008)")
    p.add_argument("software")
    p.add_argument("a", help="versione di partenza")
    p.add_argument("b", help="versione di arrivo")
    args = p.parse_args()
    print(json.dumps(run(args.software, args.a, args.b), indent=2))
