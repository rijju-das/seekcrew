#!/usr/bin/env python3
"""Local server for the Agent Factory dashboard. Listens on 127.0.0.1 only.

Run from anywhere:  python3 dashboard/serve.py
Then open:          http://127.0.0.1:8765
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
EVENTS = HERE / "events.jsonl"
PORT = 8765


def read_events(after):
    if not EVENTS.exists():
        return [], 0
    lines = EVENTS.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    if after > total:
        after = 0
    out = []
    for line in lines[after : after + 400]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out, min(total, after + 400)


def append(rec):
    with open(EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def test_run():
    def ev(ev_name, tool="", sub="", info="", fp=""):
        rec = {"t": int(time.time() * 1000), "ev": ev_name, "sid": "test", "tool": tool, "info": info}
        if sub:
            rec["sub"] = sub
        if fp:
            rec["fp"] = fp
        append(rec)

    script = [
        lambda: ev("UserPromptSubmit", info="/job-sweep test run"),
        lambda: ev("PreToolUse", "Task", "job-scout", "Search current vacancies"),
        lambda: ev("PreToolUse", "WebSearch", info="applied scientist remote"),
        lambda: ev("PreToolUse", "WebFetch", info="https://example.com/jobs/123"),
        lambda: ev("PreToolUse", "Read", info="EVIDENCE_BANK.md", fp="EVIDENCE_BANK.md"),
        lambda: ev("PreToolUse", "Write", info="shortlist", fp="shortlists/test_shortlist.md"),
        lambda: ev("PostToolUse", "Task", "job-scout", "Search current vacancies"),
        lambda: ev("Stop"),
    ]
    for step in script:
        step()
        time.sleep(1.1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif u.path == "/events":
            q = parse_qs(u.query)
            try:
                after = int(q.get("after", ["0"])[0])
            except ValueError:
                after = 0
            evs, nxt = read_events(after)
            self._send(200, json.dumps({"events": evs, "next": nxt}))
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/test":
            threading.Thread(target=test_run, daemon=True).start()
            self._send(200, '{"ok":true}')
        elif u.path == "/clear":
            EVENTS.write_text("", encoding="utf-8")
            self._send(200, '{"ok":true}')
        else:
            self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    print(f"Agent Factory running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    print(f"Reading events from {EVENTS}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
