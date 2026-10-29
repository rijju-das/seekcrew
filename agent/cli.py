#!/usr/bin/env python3
"""Run the Seekcrew agent on a file of job adverts.

    export ANTHROPIC_API_KEY=...            # never put the key in a file in this repo
    export SEEKCREW_MODEL=<model id>        # a current model ID from docs.claude.com
    python3 agent/cli.py --jobs eval/data/adverts.jsonl --max-cost 2 --price-in <$/MTok> --price-out <$/MTok>

    python3 agent/cli.py --jobs eval/data/adverts.jsonl --dry-run     # no key, no cost: keyword stand-in
    python3 agent/cli.py --resume runs/<run folder>                   # continue an interrupted run

Reads PROFILE.md and EVIDENCE_BANK.md from the repository root (or --profile / --evidence).
Writes runs/<date>_<id>/ with state.json, trace.jsonl and summary.json (git-ignored).
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parent

from seekcrew_agent.loop import Limits, run  # noqa: E402
from seekcrew_agent.model import AnthropicModel, ModelError  # noqa: E402
from seekcrew_agent.dry import DryRunModel  # noqa: E402


def load_jobs(path):
    jobs = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    for j in jobs:
        for k in ("id", "company", "title"):
            if not j.get(k):
                raise SystemExit("Every job needs id, company and title (missing %s in %s)" % (k, path))
    return jobs


def dry_settings():
    cfg = REPO / "eval" / "baseline_keywords.json"
    if not cfg.exists():
        cfg = REPO / "eval" / "baseline_keywords.example.json"
    data = json.loads(cfg.read_text())
    return {"keywords": data["keywords"], "threshold": data.get("threshold", 2)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the Seekcrew agent on job adverts.")
    ap.add_argument("--jobs", help="JSON lines file of adverts (id, company, title, location, url, text)")
    ap.add_argument("--profile", default=str(REPO / "PROFILE.md"))
    ap.add_argument("--evidence", default=str(REPO / "EVIDENCE_BANK.md"))
    ap.add_argument("--workspace", default=str(REPO), help="folder whose applications/ and tracker.csv the duplicate check uses")
    ap.add_argument("--model", default=os.environ.get("SEEKCREW_MODEL"))
    ap.add_argument("--out", default=str(REPO / "runs"))
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-tokens-total", type=int, default=400_000)
    ap.add_argument("--max-cost", type=float, default=None, help="stop when the estimated cost reaches this many US dollars")
    ap.add_argument("--price-in", type=float, default=None, help="input price, US$ per million tokens (from the pricing page)")
    ap.add_argument("--price-out", type=float, default=None, help="output price, US$ per million tokens")
    ap.add_argument("--dry-run", action="store_true", help="use the keyword stand-in instead of a model")
    ap.add_argument("--resume", help="run folder to continue")
    a = ap.parse_args(argv)

    if a.max_cost is not None and (a.price_in is None or a.price_out is None):
        raise SystemExit("--max-cost needs --price-in and --price-out so the cost can be estimated.")
    run_dir = Path(a.resume) if a.resume else None
    if run_dir:
        meta = json.loads((run_dir / "run.json").read_text())
        a.jobs = a.jobs or meta["jobs"]
    if not a.jobs:
        raise SystemExit("--jobs is required")
    jobs = load_jobs(a.jobs)
    for p in (a.profile, a.evidence):
        if not Path(p).exists():
            raise SystemExit("%s not found. Run python3 tools/app_tools.py init and fill it in." % p)
    profile, evidence = Path(a.profile).read_text(), Path(a.evidence).read_text()

    try:
        model = DryRunModel(**dry_settings()) if a.dry_run else AnthropicModel(a.model)
    except ModelError as e:
        raise SystemExit(str(e))
    if not run_dir:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_dir = Path(a.out) / ("%s_%s" % (stamp, "dry" if a.dry_run else "agent"))
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({"jobs": str(Path(a.jobs).resolve()), "model": model.model,
                                                       "batch_size": a.batch_size, "dry_run": a.dry_run}, indent=1))
    limits = Limits(batch_size=a.batch_size, max_total_tokens=a.max_tokens_total, max_cost_usd=a.max_cost,
                    price_in_per_mtok=a.price_in, price_out_per_mtok=a.price_out)
    s = run(jobs, profile, evidence, model, limits, workspace=a.workspace, run_dir=run_dir, resume=bool(a.resume))
    cost = "" if s["cost_usd"] is None else ", est. cost $%.3f" % s["cost_usd"]
    print("Run %s (%s): %d/%d jobs decided, %d relevant, stop=%s" % (s["run_id"], s["model"], s["decided"], s["jobs"], len(s["relevant"]), s["stop"]))
    print("Model calls %d, tokens in %d / out %d%s, tool errors %d of %d calls, %.1fs" % (
        s["usage"]["model_calls"], s["usage"]["input_tokens"], s["usage"]["output_tokens"], cost,
        sum(s["tool_errors"].values()), sum(s["tool_calls"].values()), s["seconds"]))
    print("Saved to %s" % run_dir)
    return 0 if s["stop"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
