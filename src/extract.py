"""Estrazione del contenuto delle pagine dal sito costruito (ADR-0005).

Input: data/build/<software>/<versione>/site. Output: data/corpus/<software>/<versione>/pages.jsonl,
una riga per pagina con software, versione, percorso, indirizzo pubblico, titolo e testo in Markdown.
"""
import argparse
import json
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify

from sources import ROOT, load

DATA = ROOT / "data"


def pages(site: Path, exclude: list[str]):
    for html in sorted(site.rglob("index.html")):
        rel = html.parent.relative_to(site).as_posix()
        rel = "" if rel == "." else rel + "/"
        if any(rel.startswith(x) for x in exclude):
            continue
        yield rel, html


def unfold_tabs(content, soup) -> None:
    """Schede a linguette del tema Material: ogni linguetta diventa etichetta in grassetto + contenuto.
    Si parte dalle più interne, così le linguette annidate restano ordinate."""
    for tabset in reversed(content.select("div.tabbed-set")):
        labels = [l.get_text(" ", strip=True) for l in tabset.select(":scope > div.tabbed-labels > label")]
        blocks = tabset.select(":scope > div.tabbed-content > div.tabbed-block")
        wrapper = soup.new_tag("div")
        for label, block in zip(labels, blocks):
            p = soup.new_tag("p")
            strong = soup.new_tag("strong")
            strong.string = label
            p.append(strong)
            wrapper.append(p)
            for child in list(block.contents):
                wrapper.append(child.extract())
        tabset.replace_with(wrapper)


def extract_page(html: Path, cfg: dict) -> tuple[str, str] | None:
    soup = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
    content = soup.select_one(cfg["content_selector"])
    if content is None:
        return None
    for sel in cfg.get("remove_selectors", []):
        for el in content.select(sel):
            el.decompose()
    unfold_tabs(content, soup)
    h1 = content.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")
    md = markdownify(str(content), heading_style="ATX", escape_underscores=False, escape_asterisks=False).strip()
    return title, md


def extract(software: str, version: str) -> dict:
    cfg = load(software)["extract"]
    site = DATA / "build" / software / version / "site"
    if not site.exists():
        raise SystemExit(f"Sito non trovato: {site}. Lancia prima la build.")
    out_dir = DATA / "corpus" / software / version
    out_dir.mkdir(parents=True, exist_ok=True)
    base = cfg["public_url"].format(version=version)
    written, skipped = 0, []
    with open(out_dir / "pages.jsonl", "w", encoding="utf-8") as f:
        for rel, html in pages(site, cfg.get("exclude_paths", [])):
            res = extract_page(html, cfg)
            if res is None or not res[1]:
                skipped.append(rel)
                continue
            title, md = res
            rec = {"software": software, "version": version, "path": rel,
                   "url": base + rel, "title": title, "markdown": md}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
    if written == 0:
        raise SystemExit("Nessuna pagina estratta: controlla content_selector nella scheda.")
    summary = {"software": software, "version": version, "pages": written,
               "skipped": skipped, "output": str(out_dir / "pages.jsonl")}
    (out_dir / "extract.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Estrae le pagine dal sito costruito")
    p.add_argument("software")
    p.add_argument("version")
    args = p.parse_args()
    s = extract(args.software, args.version)
    print(json.dumps({k: (v if k != "skipped" else len(v)) for k, v in s.items()}, indent=2))
    if s["skipped"]:
        print("Saltate:", *s["skipped"], sep="\n  ")
