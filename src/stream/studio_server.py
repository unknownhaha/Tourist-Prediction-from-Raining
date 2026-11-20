"""Local HTTP API for the ML Drift Training Studio. Run from the inner repository."""
import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.common.paths import ROOT
from src.stream.studio import DEFAULT_DIR, SNAPSHOT, Studio
from src.stream.validate import StreamHalt


def serve(studio, page, port):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, payload, mime="application/json", download=False):
            if mime == "application/json":
                payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="tourism-replay-2025.json"')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self.send(200, page.read_bytes(), "text/html")
            elif path == "/presentation":
                self.send(200, (ROOT / "site" / "drift-lab.html").read_bytes(), "text/html")
            elif path == "/api/progress":
                self.send(200, studio.progress)
            elif path in ("/api/state", "/api/load", "/api/export"):
                self.send(200, studio.state(), download=path == "/api/export")
            else:
                self.send(404, {"error": "Unknown route"})

        def do_POST(self):
            host = self.headers.get("Host", "")
            allowed = {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin != "http://" + host):
                return self.send(403, {"error": "Use the local studio origin"})
            action = self.path.removeprefix("/api/")
            if self.path != "/api/" + action or action not in {"train", "step", "promote", "rollback"}:
                return self.send(404, {"error": "Unknown action"})
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 <= size <= 4096 or self.headers.get_content_type() != "application/json":
                    return self.send(400, {"error": "Expected a small JSON object"})
                body = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                result = studio.apply(action, body)
            except (ValueError, StreamHalt) as exc:
                if action == "step" and isinstance(exc, StreamHalt):
                    studio.stage("ERROR", str(exc))
                return self.send(409, {"error": str(exc), "checks": getattr(exc, "checks", [])})
            except Exception:
                if action == "step":
                    studio.stage("ERROR", "See server log")
                logging.exception("Replay operation failed")
                return self.send(500, {"error": "Replay failed. Inspect the server log before retrying."})
            self.send(200, result)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ML Drift Training Studio http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--raw-2025", action="store_true", help="Read each 2025 batch from MOTS workbooks and HII CSV")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--page", type=Path, default=ROOT / "site" / "studio.html")
    args = parser.parse_args()
    if args.output_dir is None:
        args.output_dir = DEFAULT_DIR.with_name("studio_2025_raw_rain_lag_2m") if args.raw_2025 else DEFAULT_DIR
    baseline = ROOT / "reports/baseline_test_2024.json" if args.output_dir.resolve() == DEFAULT_DIR.resolve() else None
    serve(Studio(args.output_dir, args.snapshot, baseline, raw=args.raw_2025, triggered=args.raw_2025), args.page, args.port)
