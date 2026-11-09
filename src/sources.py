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


def version_key(v: str) -> tuple:
    """Ordine delle versioni: "0.4.13" < "0.5-dev" < "0.5.0" (una -dev viene prima della release con lo stesso numero)."""
    num, _, tag = v.partition("-")
    return tuple(int(x) for x in num.split(".")), tag == ""


def branches(source: dict) -> dict:
    """Versioni prese da un branch invece che da un tag (versions.branches nella scheda, ADR-0010)."""
    return source["versions"].get("branches") or {}


def branch_of(source: dict, version: str) -> str | None:
    b = branches(source).get(version)
    return b["branch"] if b else None


def remote_head(repo: str, branch: str) -> str:
    out = subprocess.run(
        ["git", "ls-remote", "--heads", repo, branch], check=True, capture_output=True, text=True
    ).stdout.split()
    if not out:
        raise SystemExit(f"Branch {branch} non trovato in {repo}")
    return out[0]


def resolve_versions(source: dict) -> list[str]:
    """Versioni da tenere, dalla più vecchia alla più recente, senza 'v': le release secondo la regola,
    più le eventuali versioni da branch (versions.branches)."""
    return sorted(release_versions(source) + list(branches(source)), key=version_key)


def release_versions(source: dict) -> list[str]:
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
        return [".".join(map(str, v)) for v in sorted(stable)[-keep:]]
    raise SystemExit(f"Regola di versione sconosciuta: {rule}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Mostra le versioni da tenere per un software")
    p.add_argument("software")
    args = p.parse_args()
    for v in resolve_versions(load(args.software)):
        print(v)


# ---- Infinity (embedding e reranker, GPU del notebook): errore chiaro quando non risponde ----
INFINITY_DOWN_MESSAGE = ("Non riesco a raggiungere la GPU del notebook (serve per cercare nella documentazione). "
                         "Accendila e riprova.")


class InfinityDown(Exception):
    """Infinity non raggiungibile: GPU spenta, notebook in sospensione o rete giù."""


def check_infinity(url: str, timeout: float = 3.0) -> None:
    """Controllo rapido della porta, per fallire in pochi secondi invece di aspettare il timeout lungo."""
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        socket.create_connection((u.hostname, u.port or 80), timeout=timeout).close()
    except OSError as e:
        raise InfinityDown(INFINITY_DOWN_MESSAGE) from e


def call_infinity(req, timeout: float):
    """urlopen verso Infinity: un errore di connessione diventa InfinityDown, una risposta di errore resta com'è."""
    import urllib.error
    import urllib.request
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise InfinityDown(INFINITY_DOWN_MESSAGE) from e
