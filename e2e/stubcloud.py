"""A stand-in cloud for the fail-open test in fast mode. The real cloud cannot hand out a start time a few minutes away (its
tariff only flips on the hour), so this speaks the same /agent/v1/sync contract and hands out `now + delay_s`. Like the real
cloud it re-sends a decision until the agent reports it applied. Used only for checks whose names say "stand-in cloud"."""
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8101


class StubCloud:
    def __init__(self, key: str, delay_s: int = 150, port: int = PORT):
        self.key, self.delay_s, self.port = key, delay_s, port
        self.targets: set[str] = set()  # refs that get a decision
        self.decided: dict[str, int] = {}  # ref -> the start time (epoch) handed out, fixed at first sight
        self.applied: dict[str, bool] = {}  # ref -> whether the agent confirmed it
        self.bodies: list[dict] = []
        self._srv = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def handle(self, body: dict) -> dict:
        self.bodies.append(body)
        for a in body.get("applied", []):
            self.applied[a["ref"]] = bool(a["ok"])
        out = []
        for j in body.get("jobs", []):
            ref = j["ref"]
            if j["state"] != "PENDING" or ref not in self.targets or ref in self.applied:
                continue
            start = self.decided.setdefault(ref, int(time.time()) + self.delay_s)
            out.append({"ref": ref, "start_at": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return {"decisions": out, "release_all": False, "next_poll_s": 10}

    def start(self) -> None:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, data):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._send(200, {"ok": True})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))  # always read the body first, or the client sees a reset
                if self.headers.get("X-Site-Key") != stub.key:
                    return self._send(401, {"detail": "missing or invalid X-Site-Key"})
                self._send(200, stub.handle(json.loads(raw)))

            def log_message(self, *args):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._srv.server_port  # the real port when 0 was asked for
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
