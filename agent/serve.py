#!/usr/bin/env python3
"""Run the Seekcrew agent as a small local HTTP service (127.0.0.1 only, standard library).

    export ANTHROPIC_API_KEY=... SEEKCREW_MODEL=<model id>
    python3 agent/serve.py --port 8770 [--dry-run] [--max-cost 2 --price-in X --price-out Y]

    GET  /health            service status
    POST /runs              body {"jobs": [...]} or {"jobs_file": "eval/data/adverts.jsonl"}; returns 202 and a run id
    GET  /runs              recent runs
    GET  /runs/<id>         status and, when done, the summary with every decision

One run at a time (a second POST while busy returns 409). Runs are written to runs/ as with the CLI.
"""
import argparse
import datetime
import json
import os
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parent

from cli import dry_settings, load_jobs  # noqa: E402
from seekcrew_agent.dry import DryRunModel  # noqa: E402
from seekcrew_agent.loop import Limits, run  # noqa: E402
from seekcrew_agent.model import AnthropicModel, ModelError  # noqa: E402

MAX_BODY = 5_000_000


class Service:
    def __init__(self, make_model, limits, profile, evidence, workspace, out):
        self.make_model, self.limits = make_model, limits
        self.profile, self.evidence, self.workspace, self.out = profile, evidence, workspace, Path(out)
        self.runs = {}
        self.lock = threading.Lock()
        self.busy = False

    def start(self, jobs):
        with self.lock:
            if self.busy:
                return None
            self.busy = True
            rid = uuid.uuid4().hex[:12]
            self.runs[rid] = {"id": rid, "status": "running", "jobs": len(jobs),
                              "started": datetime.datetime.now().isoformat(timespec="seconds")}
        threading.Thread(target=self._work, args=(rid, jobs), daemon=True).start()
        return rid

    def _work(self, rid, jobs):
        try:
            run_dir = self.out / ("%s_service_%s" % (datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"), rid))
            s = run(jobs, self.profile, self.evidence, self.make_model(), self.limits,
                    workspace=self.workspace, run_dir=run_dir)
            self.runs[rid].update(status="done", summary=s, folder=str(run_dir))
        except Exception as e:  # report, never crash the server
            self.runs[rid].update(status="failed", error="%s: %s" % (type(e).__name__, e))
            traceback.print_exc()
        finally:
            with self.lock:
                self.busy = False


def make_handler(svc, info):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj, indent=1).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            sys.stderr.write("[serve] " + fmt % args + "\n")

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, dict(info, ok=True, busy=svc.busy))
            if self.path == "/runs":
                return self._send(200, [{k: v for k, v in r.items() if k != "summary"} for r in svc.runs.values()])
            if self.path.startswith("/runs/"):
                r = svc.runs.get(self.path.split("/", 2)[2])
                return self._send(200, r) if r else self._send(404, {"error": "unknown run id"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/runs":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(400, {"error": "send a JSON body under 5 MB"})
            try:
                body = json.loads(self.rfile.read(n))
                if "jobs_file" in body:
                    path = (REPO / body["jobs_file"]).resolve()
                    if REPO.resolve() not in path.parents:
                        return self._send(400, {"error": "jobs_file must be inside the repository"})
                    jobs = load_jobs(path)
                else:
                    jobs = body["jobs"]
                    if not isinstance(jobs, list) or not all(isinstance(j, dict) and j.get("id") and j.get("title") and j.get("company") for j in jobs):
                        raise ValueError("jobs must be a list of objects with id, company and title")
            except (ValueError, KeyError, SystemExit, OSError) as e:
                return self._send(400, {"error": str(e)})
            if not jobs:
                return self._send(400, {"error": "no jobs"})
            rid = svc.start(jobs)
            if rid is None:
                return self._send(409, {"error": "a run is already in progress"})
            self._send(202, {"run_id": rid, "status_url": "/runs/" + rid})
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Seekcrew agent as a local HTTP service")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--model", default=os.environ.get("SEEKCREW_MODEL"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-cost", type=float, default=None)
    ap.add_argument("--price-in", type=float, default=None)
    ap.add_argument("--price-out", type=float, default=None)
    ap.add_argument("--profile", default=str(REPO / "PROFILE.md"))
    ap.add_argument("--evidence", default=str(REPO / "EVIDENCE_BANK.md"))
    ap.add_argument("--workspace", default=str(REPO))
    ap.add_argument("--out", default=str(REPO / "runs"))
    a = ap.parse_args(argv)
    if a.max_cost is not None and (a.price_in is None or a.price_out is None):
        raise SystemExit("--max-cost needs --price-in and --price-out")
    if a.dry_run:
        make_model = lambda: DryRunModel(**dry_settings())  # noqa: E731
    else:
        try:
            AnthropicModel(a.model)  # fail fast on a missing key or model
        except ModelError as e:
            raise SystemExit(str(e))
        make_model = lambda: AnthropicModel(a.model)  # noqa: E731
    for p in (a.profile, a.evidence):
        if not Path(p).exists():
            raise SystemExit("%s not found" % p)
    limits = Limits(batch_size=a.batch_size, max_cost_usd=a.max_cost, price_in_per_mtok=a.price_in, price_out_per_mtok=a.price_out)
    svc = Service(make_model, limits, Path(a.profile).read_text(), Path(a.evidence).read_text(), a.workspace, a.out)
    info = {"model": "dry-run" if a.dry_run else a.model, "max_cost_usd": a.max_cost}
    print("Seekcrew agent service on http://127.0.0.1:%d  (Ctrl+C to stop)" % a.port)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(svc, info)).serve_forever()


if __name__ == "__main__":
    main()
