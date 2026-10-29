#!/usr/bin/env python3
"""Evaluate job screening against your own labels.

    python3 eval/run_eval.py --systems baseline,dry                       # free: checks the pipeline
    python3 eval/run_eval.py --systems baseline,agent --repeats 3 --max-cost 2   # needs OPENROUTER_API_KEY and SEEKCREW_MODEL
    (with --provider anthropic and ANTHROPIC_API_KEY, also pass --price-in and --price-out)
    python3 eval/run_eval.py --systems baseline --import claude_code=path/to/decisions.json

Systems
  baseline   keyword rule on the title (eval/baseline_keywords.json)
  dry        the same keyword rule driven through the real agent loop and tools (a pipeline check:
             it must score exactly like baseline)
  agent      the agent with a real model, run --repeats times to measure run-to-run variation
  NAME=FILE  imported decisions {job_id: {"relevant": bool, "fit": 1-5}} from any other system

Metrics
  relevance  precision, recall, F1 (with a 95% bootstrap interval), accuracy, coverage
  fit        MAE, exact and within-1 agreement, Spearman, quadratic weighted kappa (decided jobs with a fit label)
  process    model calls, tokens, estimated cost, seconds, tool-call error rate, full adverts read,
             reminders, revisions, retries, share of evidence quotes found verbatim in the evidence bank
  stability  agreement of relevant/not-relevant calls between repeats

Writes eval/results/<date>_<time>/report.md and results.json (metrics only, no advert text).
Per-job decisions stay in runs/ (git-ignored).
"""
import argparse
import csv
import datetime
import json
import os
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "agent"))

import metrics as M  # noqa: E402
from seekcrew_agent.dry import DryRunModel  # noqa: E402
from seekcrew_agent.loop import Limits, run  # noqa: E402
from seekcrew_agent.model import AnthropicModel, ModelError, make_model  # noqa: E402

YES = {"yes", "y", "true", "1", "relevant"}
NO = {"no", "n", "false", "0", "not relevant"}


def load_labels(path):
    rel, fit, skipped = {}, {}, 0
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            r = (row.get("relevant") or "").strip().lower()
            if r in YES:
                rel[row["id"]] = True
            elif r in NO:
                rel[row["id"]] = False
            else:
                skipped += 1
                continue
            v = (row.get("fit") or "").strip()
            if v:
                if not re.fullmatch(r"[1-5]", v):
                    raise SystemExit("Row %s: fit must be 1-5, got %r" % (row["id"], v))
                fit[row["id"]] = int(v)
    return rel, fit, skipped


def keyword_settings():
    p = HERE / "baseline_keywords.json"
    if not p.exists():
        p = HERE / "baseline_keywords.example.json"
    d = json.loads(p.read_text())
    return {"keywords": d["keywords"], "threshold": d.get("threshold", 2)}


def baseline(jobs):
    m = DryRunModel(**keyword_settings())
    out = {}
    for j in jobs:
        s = m._score(j["title"])
        out[j["id"]] = {"relevant": s >= m.threshold, "fit": max(1, min(5, 1 + s))}
    return out


def score(decisions, gold_rel, gold_fit, evidence_text=None):
    pred = {i: d["relevant"] for i, d in decisions.items() if i in gold_rel}
    tp, fp, fn, tn = M.confusion(pred, gold_rel)
    res = {"n": len(gold_rel), "coverage": sum(1 for i in gold_rel if i in decisions) / len(gold_rel),
           "tp": tp, "fp": fp, "fn": fn, "tn": tn}
    res.update(M.prf(tp, fp, fn, tn))
    lo, hi = M.bootstrap_f1(pred, gold_rel)
    res["f1_ci95"] = [lo, hi]
    res["fit"] = M.fit_agreement({i: d["fit"] for i, d in decisions.items()}, gold_fit)
    if evidence_text is not None:
        res["evidence"] = M.evidence_grounding(decisions, evidence_text)
    return res


def process_of(summary):
    keys = ["models_used", "usage", "cost_usd", "seconds", "tool_error_rate", "tool_calls", "tool_errors", "reads", "jobs_read",
            "reminders", "revisions", "model_retries", "stop", "undecided"]
    return {k: summary.get(k) for k in keys}


def fmt(x, pct=False, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "n/a"
    return ("%.0f%%" % (100 * x)) if pct else ("%.*f" % (nd, x))


def mean_sd(vals):
    vals = [v for v in vals if v is not None and v == v]
    if not vals:
        return "n/a"
    if len(vals) == 1:
        return fmt(vals[0])
    return "%s ± %s" % (fmt(statistics.fmean(vals)), fmt(statistics.stdev(vals)))


def report_md(meta, systems):
    L = ["# Seekcrew screening evaluation", "",
         "Date: %s. Adverts: %d labelled (%d unlabelled skipped), %d labelled relevant. Model: %s." % (
             meta["date"], meta["labelled"], meta["skipped"], meta["positives"], meta.get("model") or "n/a"),
         "Sources (adverts, of which labelled relevant): " + ", ".join("%s %d (%d)" % (k, v[0], v[1]) for k, v in sorted(meta.get("sources", {}).items())) + ".",
         "Labels are one person's judgement for their own profile, so results describe agreement with that person, not general accuracy.", "",
         "## Relevance (shortlist decision)", "",
         "| System | Runs | Precision | Recall | F1 (95% CI, run 1) | Coverage |", "|---|---|---|---|---|---|"]
    for name, s in systems.items():
        r = s["runs"]
        ci = r[0]["f1_ci95"]
        L.append("| %s | %d | %s | %s | %s (%s to %s) | %s |" % (
            name, len(r), mean_sd([x["precision"] for x in r]), mean_sd([x["recall"] for x in r]),
            mean_sd([x["f1"] for x in r]), fmt(ci[0]), fmt(ci[1]), mean_sd([x["coverage"] for x in r])))
    L += ["", "## Fit score (1-5) against your fit labels", "",
          "| System | n | MAE | Exact | Within 1 | Spearman | Weighted kappa |", "|---|---|---|---|---|---|---|"]
    for name, s in systems.items():
        f = [x["fit"] for x in s["runs"] if x["fit"].get("n")]
        if not f:
            L.append("| %s | 0 | n/a | n/a | n/a | n/a | n/a |" % name)
            continue
        L.append("| %s | %d | %s | %s | %s | %s | %s |" % (name, f[0]["n"], mean_sd([x["mae"] for x in f]),
                 mean_sd([x["exact"] for x in f]), mean_sd([x["within_1"] for x in f]),
                 mean_sd([x["spearman"] for x in f]), mean_sd([x["qwk"] for x in f])))
    proc = {n: s for n, s in systems.items() if s.get("process")}
    if proc:
        L += ["", "## Process (per run)", "",
              "| System | Model calls | Tokens in | Tokens out | Cost $ | Seconds | Tool error rate | Adverts read | Evidence quotes grounded | Stability |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for name, s in proc.items():
            p = s["process"]
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                name, mean_sd([x["usage"]["model_calls"] for x in p]), mean_sd([x["usage"]["input_tokens"] for x in p]),
                mean_sd([x["usage"]["output_tokens"] for x in p]), mean_sd([x["cost_usd"] for x in p]),
                mean_sd([x["seconds"] for x in p]), mean_sd([x["tool_error_rate"] for x in p]),
                mean_sd([x["jobs_read"] for x in p]),
                mean_sd([x.get("evidence", {}).get("grounded") for x in s["runs"]]),
                fmt(s.get("stability")) if s.get("stability") is not None else "n/a"))
        stops = {n: [x["stop"] for x in s["process"]] for n, s in proc.items()}
        used = {}
        for n, s in proc.items():
            for p in s["process"]:
                for mdl, c in (p.get("models_used") or {}).items():
                    used.setdefault(n, {}).setdefault(mdl, 0)
                    used[n][mdl] += c
        L += ["", "Models that answered: " + "; ".join("%s: %s" % (n, ", ".join("%s (%d calls)" % kv for kv in v.items())) for n, v in used.items())]
        L += ["", "Stop reasons: " + "; ".join("%s: %s" % (n, ", ".join(v)) for n, v in stops.items())]
    L += ["", "## Notes", "",
          "- Undecided jobs count as not relevant for precision and recall; coverage shows how many were decided.",
          "- The 95% interval resamples the labelled jobs (2,000 bootstrap samples) and reflects the small sample.",
          "- The board sample was drawn with a fixed seed and takes about half its adverts from titles in the user's field, which makes the keyword baseline look better than on an unfiltered feed. Manual adverts were chosen by the user and are likelier to be relevant.",
          "- Cost is the provider-reported cost when available (OpenRouter), otherwise an estimate from token counts and the prices passed in. Check it against your provider's dashboard."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluate job screening against your labels")
    ap.add_argument("--adverts", default=str(HERE / "data" / "adverts.jsonl"))
    ap.add_argument("--labels", default=str(HERE / "data" / "labels.csv"))
    ap.add_argument("--systems", default="baseline,dry")
    ap.add_argument("--import", dest="imports", action="append", default=[], help="NAME=decisions.json")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--provider", default=None, help="openai, openrouter or anthropic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-cost", type=float, default=None, help="cost cap per agent run, US$")
    ap.add_argument("--price-in", type=float, default=None)
    ap.add_argument("--price-out", type=float, default=None)
    ap.add_argument("--profile", default=str(REPO / "PROFILE.md"))
    ap.add_argument("--evidence", default=str(REPO / "EVIDENCE_BANK.md"))
    ap.add_argument("--workspace", default=None, help="folder for the duplicate check (default: an empty one, so earlier applications do not leak in)")
    ap.add_argument("--out", default=str(HERE / "results"))
    a = ap.parse_args(argv)

    jobs = [json.loads(l) for l in Path(a.adverts).read_text().splitlines() if l.strip()]
    gold_rel, gold_fit, skipped = load_labels(a.labels)
    jobs = [j for j in jobs if j["id"] in gold_rel]
    if len(jobs) < 10:
        raise SystemExit("Only %d labelled adverts match. Label at least 10 rows in %s." % (len(jobs), a.labels))
    gold_rel = {j["id"]: gold_rel[j["id"]] for j in jobs}
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = Path(a.out) / stamp
    runs_root = REPO / "runs" / ("eval_" + stamp)
    workspace = Path(a.workspace) if a.workspace else runs_root / "empty_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    names = [s.strip() for s in a.systems.split(",") if s.strip()]
    need_text = any(n in ("dry", "agent") for n in names)
    profile = evidence = None
    if need_text:
        for p in (a.profile, a.evidence):
            if not Path(p).exists():
                raise SystemExit("%s not found" % p)
        profile, evidence = Path(a.profile).read_text(), Path(a.evidence).read_text()

    systems, model_id = {}, None
    for name in names:
        if name == "baseline":
            systems[name] = {"runs": [score(baseline(jobs), gold_rel, gold_fit)]}
        elif name in ("dry", "agent"):
            runs, procs, rel_runs = [], [], []
            for k in range(1 if name == "dry" else a.repeats):
                if name == "dry":
                    model = DryRunModel(**keyword_settings())
                else:
                    try:
                        model = make_model(a.provider, a.model)
                    except ModelError as e:
                        raise SystemExit(str(e))
                    if not getattr(model, "reports_cost", False) and not isinstance(model, DryRunModel) and a.max_cost is not None and (a.price_in is None or a.price_out is None):
                        raise SystemExit("This provider does not report cost, so --max-cost needs --price-in and --price-out (US$ per million tokens)")
                    model_id = model.model
                limits = Limits(batch_size=a.batch_size, max_cost_usd=a.max_cost,
                                price_in_per_mtok=a.price_in, price_out_per_mtok=a.price_out)
                print("%s run %d: model requested %s" % (name, k + 1, model.model))
                s = run(jobs, profile, evidence, model, limits, workspace=workspace,
                        run_dir=runs_root / ("%s_%d" % (name, k + 1)), show=print if name == "agent" else None)
                print("%s run %d answered by: %s" % (name, k + 1, ", ".join("%s (%d calls)" % kv for kv in s["models_used"].items())))
                runs.append(score(s["decisions"], gold_rel, gold_fit, evidence))
                procs.append(process_of(s))
                rel_runs.append({i: d["relevant"] for i, d in s["decisions"].items()})
                print("%s run %d: F1 %.2f, coverage %.0f%%, stop=%s" % (name, k + 1, runs[-1]["f1"], 100 * runs[-1]["coverage"], s["stop"]))
            systems[name] = {"runs": runs, "process": procs, "stability": M.pairwise_agreement(rel_runs)}
        else:
            raise SystemExit("Unknown system %r" % name)
    for spec in a.imports:
        if "=" not in spec:
            raise SystemExit("--import needs NAME=FILE")
        n, f = spec.split("=", 1)
        dec = json.loads(Path(f).read_text())
        systems[n] = {"runs": [score(dec, gold_rel, gold_fit, evidence)]}

    if "dry" in systems and "baseline" in systems:
        same = abs(systems["dry"]["runs"][0]["f1"] - systems["baseline"]["runs"][0]["f1"]) < 1e-9
        print("Pipeline check (dry run scores exactly like the baseline): %s" % ("PASS" if same else "FAIL"))
    sources = {}
    for j in jobs:
        src = j.get("source", "unknown")
        sources.setdefault(src, [0, 0])
        sources[src][0] += 1
        sources[src][1] += int(gold_rel[j["id"]])
    meta = {"date": stamp, "labelled": len(gold_rel), "skipped": skipped, "positives": sum(gold_rel.values()),
            "sources": sources,
            "model": model_id, "repeats": a.repeats, "batch_size": a.batch_size}
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"meta": meta, "systems": systems}, indent=1))
    (out / "report.md").write_text(report_md(meta, systems))
    print("Report: %s" % (out / "report.md"))


if __name__ == "__main__":
    main()
