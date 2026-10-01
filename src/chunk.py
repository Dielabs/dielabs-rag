"""Taglio delle pagine del corpus in pezzi (ADR-0007).

Input: data/corpus/<software>/<versione>/pages.jsonl.
Output: data/chunks/<software>/<versione>/chunks.jsonl, una riga per pezzo con i dati che andranno
in Qdrant più `embed_text` (prefisso + testo, l'input di embedding e BM25), e chunks.json con il riepilogo.
Parametri in config.yaml, sezione `chunking`.
"""
import argparse
import hashlib
import json
import re
import uuid
from collections import Counter

import yaml

from sources import ROOT

DATA = ROOT / "data"
HEADING = re.compile(r"^(#{1,6}) (.*)$")
SEP = "\n\n"


def is_fence(line: str) -> bool:
    return line.lstrip().startswith("```")


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["chunking"]


# --- pagina -> sezioni ---------------------------------------------------------------------------

def norm(s: str) -> str:
    """Solo lettere e cifre minuscole: per confrontare il testo di un titolo con il suo id HTML."""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # link Markdown: resta il testo
    s = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", s)  # link a riferimento [testo][rif]: resta il testo
    return re.sub(r"[^a-z0-9]", "", s.lower())


def text_matches(text: str, exp_id: str) -> bool:
    a, b = norm(text), norm(re.sub(r"_\d+$", "", exp_id))
    return a == b or (min(len(a), len(b)) >= 8 and (a.startswith(b) or b.startswith(a)))


def sections(page: dict, stats: Counter) -> list[dict]:
    """Divide la pagina per titoli. Ogni sezione: titoli dalla pagina a lei, ancora, righe (titolo compreso).

    I titoli veri sono quelli dell'HTML ([livello, id], in ordine). Una riga `#...` del Markdown è il
    prossimo titolo atteso se ha lo stesso livello e il testo corrisponde all'id. Se il testo non
    corrisponde (id scelto a mano nella documentazione) la si accetta solo se è fuori da un blocco di
    codice e più avanti nella pagina nessuna riga corrisponde meglio a quell'id. Così un commento
    `# ...` dentro un blocco di codice non diventa un titolo, anche con delimitatori ambigui."""
    expected = page.get("headings", [])
    lines = page["markdown"].split("\n")
    cand, infence = [], False  # (indice riga, livello, testo, dentro un blocco di codice)
    for i, line in enumerate(lines):
        if is_fence(line):
            infence = not infence
        m = HEADING.match(line)
        if m:
            cand.append((i, len(m.group(1)), m.group(2).strip(), infence))
    chosen, k = {}, 0
    for n, (i, level, text, inf) in enumerate(cand):
        if k >= len(expected) or level != expected[k][0]:
            continue
        exp_id = expected[k][1]
        ok = not exp_id or text_matches(text, exp_id)
        if not ok and not inf:
            ok = not any(l2 == level and text_matches(t2, exp_id) for _, l2, t2, _ in cand[n + 1:])
        if ok:
            chosen[i] = exp_id
            k += 1
    secs, stack = [], []
    cur = {"headings": [], "anchor": "", "lines": []}
    for i, line in enumerate(lines):
        if i in chosen:
            secs.append(cur)
            m = HEADING.match(line)
            level, text = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            cur = {"headings": [t for _, t in stack], "anchor": chosen[i], "lines": [line]}
        else:
            cur["lines"].append(line)
    secs.append(cur)
    if k < len(expected):
        stats["pages_headings_unmatched"] += 1
        stats["headings_unmatched"] += len(expected) - k
    return secs


# --- sezione -> blocchi --------------------------------------------------------------------------

def blocks(lines: list[str]) -> list[tuple[str, str]]:
    """Blocchi indivisibili di partenza: ("code"|"table"|"text", testo)."""
    out, buf, kind, infence = [], [], None, False

    def flush():
        nonlocal buf, kind
        if buf and any(l.strip() for l in buf):
            out.append((kind or "text", "\n".join(buf).strip("\n")))
        buf, kind = [], None

    for line in lines:
        if infence:
            buf.append(line)
            if is_fence(line):
                infence = False
                flush()
            continue
        if is_fence(line):
            flush()
            buf, kind, infence = [line], "code", True
            continue
        if line.lstrip().startswith("|"):
            if kind != "table":
                flush()
                kind = "table"
            buf.append(line)
            continue
        if kind == "table":
            flush()
        if not line.strip():
            flush()
        else:
            kind = "text"
            buf.append(line)
    flush()
    return out


def split_code(text: str, limit: int) -> list[str]:
    lines = text.split("\n")
    opener = lines[0]
    body = lines[1:-1] if len(lines) > 1 and is_fence(lines[-1]) else lines[1:]
    fence = opener.lstrip()[: len(opener.lstrip()) - len(opener.lstrip().lstrip("`"))]
    parts, cur = [], []
    for l in body:
        if cur and len(opener) + sum(len(x) + 1 for x in cur) + len(l) + len(fence) + 2 > limit:
            parts.append("\n".join([opener, *cur, fence]))
            cur = []
        cur.append(l)
    if cur:
        parts.append("\n".join([opener, *cur, fence]))
    return parts


def split_table(text: str, limit: int) -> list[str]:
    lines = text.split("\n")
    head, rows = lines[:2], lines[2:]
    parts, cur = [], []
    for r in rows:
        if cur and sum(len(x) + 1 for x in head + cur) + len(r) > limit:
            parts.append("\n".join(head + cur))
            cur = []
        cur.append(r)
    if cur or not parts:
        parts.append("\n".join(head + cur))
    return parts


def split_text(text: str, limit: int) -> list[str]:
    pieces = re.split(r"(?<=[.!?])\s+|\n", text)
    parts, cur = [], ""
    for p in pieces:
        while len(p) > limit:  # caso limite: una frase più lunga del tetto
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(p[:limit])
            p = p[limit:]
        if cur and len(cur) + 1 + len(p) > limit:
            parts.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}" if cur else p
    if cur:
        parts.append(cur)
    return parts


def fit_blocks(raw: list[tuple[str, str]], cfg: dict, stats: Counter) -> list[tuple[str, str]]:
    """Divide i blocchi troppo lunghi secondo le regole dell'ADR-0007."""
    out = []
    for kind, text in raw:
        if kind == "code" and len(text) > cfg["code_max_chars"]:
            stats["code_blocks_split"] += 1
            out += [("code", t) for t in split_code(text, cfg["code_max_chars"])]
        elif kind == "table" and len(text) > cfg["max_chars"]:
            stats["tables_split"] += 1
            out += [("table", t) for t in split_table(text, cfg["max_chars"])]
        elif kind == "text" and len(text) > cfg["max_chars"]:
            stats["paragraphs_split"] += 1
            out += [("text", t) for t in split_text(text, cfg["max_chars"])]
        else:
            out.append((kind, text))
    return out


def pack(bl: list[tuple[str, str]], cfg: dict) -> list[tuple[str, int]]:
    """Unisce i blocchi in pezzi fino al tetto. Restituisce (testo, caratteri ripetuti dal pezzo prima)."""
    chunks, cur, overlap = [], [], 0
    size = lambda items: len(SEP.join(t for _, t in items))
    for kind, text in bl:
        if cur and size(cur + [(kind, text)]) > cfg["max_chars"]:
            chunks.append((SEP.join(t for _, t in cur), overlap))
            last_kind, last = cur[-1]
            if (last_kind == "text" and len(last) <= cfg["overlap_max_chars"]
                    and len(last) + len(SEP) + len(text) <= cfg["max_chars"]):
                cur, overlap = [(last_kind, last)], len(last) + len(SEP)
            else:
                cur, overlap = [], 0
        cur.append((kind, text))
    if cur:
        chunks.append((SEP.join(t for _, t in cur), overlap))
    return chunks


# --- tutto insieme -------------------------------------------------------------------------------

def chunk_page(page: dict, cfg: dict, stats: Counter):
    sw, ver, path, title = page["software"], page["version"], page["path"], page["title"]
    position = 0
    for idx, sec in enumerate(sections(page, stats)):
        has_heading = bool(sec["headings"])
        body = blocks(sec["lines"][1:] if has_heading else sec["lines"])
        if not body:  # solo il titolo, nessun contenuto: lo porta già il prefisso dei figli
            if has_heading:
                stats["sections_empty"] += 1
            continue
        raw = ([("text", sec["lines"][0])] if has_heading else []) + body
        stats["sections"] += 1
        heads = sec["headings"]
        path_titles = heads[1:] if heads and heads[0] == title else heads
        prefix = " > ".join([title, *path_titles])
        anchor = sec["anchor"] or ""
        section_id = hashlib.sha1(f"{sw}|{ver}|{path}|{idx}|{anchor}".encode()).hexdigest()[:16]
        parts = pack(fit_blocks(raw, cfg, stats), cfg)
        for n, (text, overlap) in enumerate(parts):
            embed_text = prefix + SEP + text
            yield {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{sw}|{ver}|{path}|{idx}|{n}")),
                "software": sw, "version": ver, "path": path, "url": page["url"], "title": title,
                "headings": heads, "anchor": anchor,
                "section_url": page["url"] + (f"#{anchor}" if anchor else ""),
                "section_id": section_id, "section_part": n, "section_parts": len(parts),
                "overlap": overlap, "position": position, "text": text,
                "embed_hash": hashlib.sha256(embed_text.encode()).hexdigest()[:16],
                "embed_text": embed_text,
            }
            position += 1


def run(software: str, version: str) -> dict:
    cfg = load_config()
    src = DATA / "corpus" / software / version / "pages.jsonl"
    if not src.exists():
        raise SystemExit(f"Corpus non trovato: {src}. Lancia prima make corpus.")
    out_dir = DATA / "chunks" / software / version
    out_dir.mkdir(parents=True, exist_ok=True)
    stats, lengths, hashes = Counter(), [], set()
    with open(src, encoding="utf-8") as f, open(out_dir / "chunks.jsonl", "w", encoding="utf-8") as out:
        for line in f:
            page = json.loads(line)
            if "headings" not in page:
                raise SystemExit("pages.jsonl senza il campo headings: rilancia make extract.")
            stats["pages"] += 1
            for c in chunk_page(page, cfg, stats):
                out.write(json.dumps(c, ensure_ascii=False) + "\n")
                lengths.append(len(c["embed_text"]))
                hashes.add(c["embed_hash"])
    lengths.sort()
    summary = {
        "software": software, "version": version, **stats, "chunks": len(lengths),
        "unique_embed_texts": len(hashes),
        "chars_median": lengths[len(lengths) // 2] if lengths else 0,
        "chars_p95": lengths[int(len(lengths) * 0.95)] if lengths else 0,
        "chars_max": lengths[-1] if lengths else 0,
        "over_max_chars": sum(1 for l in lengths if l > cfg["max_chars"]),
        "config": cfg, "output": str(out_dir / "chunks.jsonl"),
    }
    (out_dir / "chunks.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Taglia le pagine del corpus in pezzi (ADR-0007)")
    p.add_argument("software")
    p.add_argument("version")
    a = p.parse_args()
    print(json.dumps(run(a.software, a.version), indent=2))
