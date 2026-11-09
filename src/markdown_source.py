"""Corpus da file Markdown del repo, letti al tag (tipo di sorgente `markdown`, ADR-0010).

Per i software che non hanno un sito di documentazione da costruire: si clona il repo al tag e si
leggono i file indicati nella scheda (`markdown.include`, percorsi o glob relativi al repo).
Output: data/corpus/<software>/<versione>/pages.jsonl, stesso formato dell'estrazione MkDocs
(software, versione, percorso, indirizzo pubblico, titolo, testo, titoli come [livello, id]).
Gli id dei titoli seguono la regola di GitHub, così i link alle sezioni funzionano.

Una versione presa da un branch (versions.branches nella scheda) può avere un suo blocco `markdown`.
Con `url_style: site` i Markdown sono il sorgente di un sito (MkDocs/Zensical, docs/ come cartella):
docs/a/b.md diventa <public_url>a/b/ e gli id dei titoli seguono la regola del sito (Python-Markdown),
così le citazioni puntano alle pagine pubblicate (lm-eval 0.5-dev su readthedocs, 2026-11-09).

    python src/markdown_source.py lmeval 0.4.13
"""
import argparse
import json
import re
import subprocess
import unicodedata

from build import clone
from sources import ROOT, branches, load

DATA = ROOT / "data"
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def is_fence(line: str) -> bool:
    return line.lstrip().startswith(("```", "~~~"))


def slug(text: str) -> str:
    """Id di un titolo come lo calcola GitHub: minuscole, via la punteggiatura, spazi in trattini."""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # link: resta il testo
    t = re.sub(r"<[^>]+>", "", t)                          # tag HTML
    t = t.replace("`", "").lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.replace(" ", "-")


def slug_site(text: str) -> str:
    """Id di un titolo come lo calcola un sito MkDocs/Zensical (slugify di Python-Markdown)."""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    t = re.sub(r"<[^>]+>", "", t).replace("`", "")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^\w\s-]", "", t).strip().lower()
    return re.sub(r"[-\s]+", "-", t)


def headings(md: str, site: bool = False) -> list[list]:
    """Titoli ATX fuori dai blocchi di codice, in ordine, con id unici: come su GitHub (-1, -2...)
    oppure, con site=True, come su un sito MkDocs/Zensical (_1, _2...)."""
    out, seen, infence = [], {}, False
    for line in md.split("\n"):
        if is_fence(line):
            infence = not infence
            continue
        m = None if infence else HEADING.match(line)
        if not m:
            continue
        base = slug_site(m.group(2)) if site else slug(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append([len(m.group(1)), base if n == 0 else f"{base}{'_' if site else '-'}{n}"])
    return out


def title_of(md: str, path: str) -> str:
    infence = False
    for line in md.split("\n"):
        if is_fence(line):
            infence = not infence
        m = None if infence else HEADING.match(line)
        if m and len(m.group(1)) == 1:
            return m.group(2).strip()
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def page_url(base: str, rel: str, site: bool) -> str:
    """Indirizzo pubblico di un file: sul repo (rel così com'è) o sul sito (docs/a/b.md -> a/b/)."""
    if not site:
        return base + rel
    p = rel.removeprefix("docs/").removesuffix(".md")
    p = "" if p == "index" else p.removesuffix("/index") + "/"
    return base + p


def corpus(software: str, version: str) -> dict:
    source = load(software)
    cfg = (branches(source).get(version) or {}).get("markdown") or source["markdown"]
    site = cfg.get("url_style") == "site"
    src = DATA / "src" / software / version
    clone(source, version, src)
    commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()
    files = sorted({p for pat in cfg["include"] for p in src.glob(pat) if p.is_file()})
    exclude = cfg.get("exclude", [])
    out_dir = DATA / "corpus" / software / version
    out_dir.mkdir(parents=True, exist_ok=True)
    base = cfg["public_url"].format(version=version)
    written, skipped = 0, []
    with open(out_dir / "pages.jsonl", "w", encoding="utf-8") as f:
        for p in files:
            rel = p.relative_to(src).as_posix()
            if any(rel.startswith(x) for x in exclude):
                continue
            md = p.read_text(encoding="utf-8").strip()
            if not md:
                skipped.append(rel)
                continue
            rec = {"software": software, "version": version, "path": rel, "url": page_url(base, rel, site),
                   "title": title_of(md, rel), "markdown": md, "headings": headings(md, site)}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
    if written == 0:
        raise SystemExit("Nessun file Markdown trovato: controlla markdown.include nella scheda.")
    summary = {"software": software, "version": version, "commit": commit, "pages": written,
               "skipped": skipped, "output": str(out_dir / "pages.jsonl")}
    (out_dir / "extract.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    a = argparse.ArgumentParser(description="Corpus da file Markdown del repo, al tag")
    a.add_argument("software")
    a.add_argument("version")
    args = a.parse_args()
    print(json.dumps(corpus(args.software, args.version), indent=2))
