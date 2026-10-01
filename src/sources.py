"""Lettura delle schede in sources/ e risoluzione delle versioni da tenere."""
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def load(name: str) -> dict:
    path = SOURCES_DIR / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"Scheda non trovata: {path}")
    return yaml.safe_load(path.read_text())


def remote_tags(repo: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-remote", "--tags", repo], check=True, capture_output=True, text=True
    ).stdout
    tags = []
    for line in out.splitlines():
        ref = line.split("\t")[1]
        if ref.endswith("^{}"):
            continue
        tags.append(ref.removeprefix("refs/tags/"))
    return tags


def resolve_versions(source: dict) -> list[str]:
    """Versioni da tenere, dalla più vecchia alla più recente, senza 'v'."""
    rule = source["versions"]["rule"]
    keep = source["versions"]["keep"]
    stable = []
    for tag in remote_tags(source["repo"]):
        m = STABLE_TAG.match(tag)
        if m:
            stable.append(tuple(int(x) for x in m.groups()))
    if not stable:
        raise SystemExit(f"Nessun tag stabile trovato in {source['repo']}")
    if rule == "latest_minors":
        minors = sorted({(a, b) for a, b, _ in stable})[-keep:]
        return [f"{a}.{b}.0" for a, b in minors if (a, b, 0) in stable]
    if rule == "latest_release":
        return [".".join(map(str, sorted(stable)[-1]))]
    raise SystemExit(f"Regola di versione sconosciuta: {rule}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Mostra le versioni da tenere per un software")
    p.add_argument("software")
    args = p.parse_args()
    for v in resolve_versions(load(args.software)):
        print(v)
