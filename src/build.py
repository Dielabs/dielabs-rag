"""Build MkDocs di una versione di un software, dentro Docker (ADR-0005).

Input: scheda in sources/, versione. Output: data/build/<software>/<versione>/site
più build.log e build.json (tempi ed esito).
"""
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from sources import ROOT, load

DATA = ROOT / "data"


def clone(source: dict, version: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--depth", "1", "--branch", f"v{version}",
         source["repo"], str(dest)],
        check=True,
    )


def build(software: str, version: str) -> dict:
    source = load(software)
    b = source["build"]
    src = DATA / "src" / software / version
    out = DATA / "build" / software / version
    pip_cache = DATA / "cache" / "pip"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    pip_cache.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    clone(source, version, src)
    t_clone = time.time() - t0

    public_url = source["extract"]["public_url"].format(version=version)
    env = {**b.get("env", {}), "READTHEDOCS_CANONICAL_URL": public_url,
           "PYTHONDONTWRITEBYTECODE": "1"}
    env_args = [a for k, v in env.items() for a in ("-e", f"{k}={v}")]
    uid, gid = os.getuid(), os.getgid()
    script = (
        "git config --global --add safe.directory /src "
        "&& chown -R 0:0 /root/.cache/pip "
        f"&& pip install --quiet --root-user-action=ignore -r {b['requirements']} "
        f"&& mkdocs build -f {b['config']} -d /out/site; "
        f"rc=$?; chown -R {uid}:{gid} /src /out /root/.cache/pip; exit $rc"
    )
    cmd = ["docker", "run", "--rm",
           "-v", f"{src}:/src", "-v", f"{out}:/out", "-v", f"{pip_cache}:/root/.cache/pip",
           "-w", "/src", *env_args, b["image"], "bash", "-c", script]

    t1 = time.time()
    with open(out / "build.log", "w") as log:
        rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    t_build = time.time() - t1

    pages = len(list((out / "site").rglob("*.html"))) if rc == 0 else 0
    result = {"software": software, "version": version, "returncode": rc,
              "clone_seconds": round(t_clone, 1), "build_seconds": round(t_build, 1),
              "html_files": pages}
    (out / "build.json").write_text(json.dumps(result, indent=2))
    if rc != 0:
        raise SystemExit(f"Build fallita (rc={rc}), vedi {out / 'build.log'}")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build MkDocs di una versione, in Docker")
    p.add_argument("software")
    p.add_argument("version", help="es. 0.30.0")
    args = p.parse_args()
    print(json.dumps(build(args.software, args.version), indent=2))
