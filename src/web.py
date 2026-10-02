"""Web GUI del Dielabs RAG (Fase 2, Blocco 9, ADR-0009).

Sola libreria standard, come l'interfaccia di NewsRAG: nessuna dipendenza nuova, nessun passo di build.
Un utente (Diego, rete di casa), quindi ThreadingHTTPServer basta.

    GET  /               la pagina
    GET  /static/<file>  CSS e JS della pagina (solo i file elencati)
    GET  /api/kbs        software e versioni caricate in Qdrant
    POST /api/ask        {"software", "version", "q"} -> eventi NDJSON (sources, thinking, token, done, error)

CLI:  python src/web.py --port 8095
"""
import argparse
import json
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from answer import ask_stream
from sources import ROOT, SOURCES_DIR, load as load_source

WEB = ROOT / "web"
STATIC = {"style.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8"}
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
MAX_QUESTION = 2000


def kbs() -> list[dict]:
    """Le KB davvero caricate: una versione conta se il suo caricamento in Qdrant è completo (load.json)."""
    out = []
    for path in sorted(SOURCES_DIR.glob("*.yaml")):
        src = load_source(path.stem)
        versions = []
        for d in (ROOT / "data" / "chunks" / path.stem).glob("*/load.json"):
            v = d.parent.name
            if VERSION.match(v):
                versions.append(v)
        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")))
        if versions:
            latest = json.loads((ROOT / "data" / "chunks" / path.stem / versions[-1] / "load.json").read_text())
            out.append({"name": path.stem, "display_name": src.get("display_name", path.stem),
                        "versions": versions[::-1], "points": latest.get("points", 0)})
    out.sort(key=lambda k: -k["points"])   # prima la documentazione più grande
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "DielabsRAG"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path.startswith("/static/") and path[8:] in STATIC:
            name = path[8:]
            return self._send(200, (WEB / name).read_bytes(), STATIC[name])
        if path == "/api/kbs":
            return self._json(200, kbs())
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/ask":
            return self._send(404, b"not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(length, 20000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "Richiesta non valida."})
        q = (req.get("q") or "").strip()
        sw, ver = req.get("software"), req.get("version")
        valid = {k["name"]: k["versions"] for k in kbs()}
        if not q or len(q) > MAX_QUESTION:
            return self._json(400, {"error": "Scrivi una domanda (al massimo 2000 caratteri)."})
        if sw not in valid or ver not in valid[sw]:
            return self._json(400, {"error": "Questa documentazione non è caricata."})

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event: dict) -> None:
            self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

        try:
            for event in ask_stream(q, sw, ver):
                emit(event)
        except (BrokenPipeError, ConnectionResetError):
            pass          # la pagina è stata chiusa durante la risposta
        except Exception as e:
            traceback.print_exc()
            try:
                emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            except (BrokenPipeError, ConnectionResetError):
                pass


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Web GUI del Dielabs RAG")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8095)
    a = p.parse_args()
    print(f"Dielabs RAG su http://{a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
