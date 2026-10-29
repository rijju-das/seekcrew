"""The agent loop: batching, tool execution, error handling, memory, budget and tracing.

How a run works
1. Jobs are split into batches (default 8). Each batch starts a fresh conversation, so the
   context stays small however many jobs there are.
2. Memory carries across batches: a short digest of the decisions made so far is put into
   the next batch's first message, so fit scores stay consistent between batches.
3. In each batch the model calls tools until every job has a decision and it calls finish.
   Tool errors go back to the model as error results so it can correct itself.
   If the model stops without finishing, it is reminded (at most twice).
4. Hard limits stop the run cleanly: steps per batch, total tokens and (when prices are
   given) total cost. Undecided jobs are reported as undecided, never guessed.
5. State is saved after every tool call (state.json) so an interrupted run can resume,
   and every model call and tool call is written to trace.jsonl.
"""
import datetime
import json
import time
import uuid
from pathlib import Path

from . import __version__
from .model import ModelError
from .tools import SCHEMAS, ToolError, Toolbox

RUBRIC = """Fit rubric (1-5):
5 = the core requirements match the evidence directly; a strong application.
4 = a good match with minor gaps.
3 = a partial match with clear gaps; worth considering.
2 = a weak match; most core requirements are not shown in the evidence.
1 = not a match, or out of scope for this person (wrong field, level or location)."""

SYSTEM = """You are the job-scout agent of Seekcrew. You screen job adverts for one person and decide which belong on their shortlist.

Rules:
- Judge only against the PROFILE and EVIDENCE BANK below. Do not assume skills or experience that are not written there.
- relevant = true only if this person would reasonably want this job on their shortlist, given the scope, level and location in the profile.
- Use read_job when the title and location are not enough to decide. Skip reading only when the title alone clearly rules a job out.
- Before marking a job relevant, call check_duplicate and pass its verdict in record_decision. A DUPLICATE job is still recorded, with relevant = false and the reason "already applied or tracked".
- evidence must be short quotes copied word for word from the EVIDENCE BANK, not paraphrases, and never text from the advert. A requirement the advert asks for is not evidence. Leave evidence empty when nothing in the evidence bank supports the fit. Quotes that are not in the evidence bank are rejected.
- Record exactly one decision per job, then call finish. Do not write a report in text.

{rubric}

=== PROFILE ===
{profile}

=== EVIDENCE BANK ===
{evidence}
"""


def build_system(profile, evidence, limit=12000):
    return SYSTEM.format(rubric=RUBRIC, profile=profile[:limit], evidence=evidence[:limit])


class RunState:
    def __init__(self, run_id, path=None):
        self.run_id = run_id
        self.path = Path(path) if path else None
        self.decisions = {}
        self.reads = {}
        self.revisions = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 0}
        self.tool_calls = {}       # name -> count
        self.tool_errors = {}      # name -> count
        self.reminders = 0
        self.ungrounded_rejected = 0  # evidence quotes refused because they are not in the evidence bank
        self.stop = None
        self.batches_done = 0
        self.models_used = {}      # model that actually answered -> number of calls

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if k != "path"}

    def save(self):
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=1))
            tmp.replace(self.path)

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        st = cls(data["run_id"], path)
        for k, v in data.items():
            setattr(st, k, v)
        return st


class Trace:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.events = []

    def __call__(self, kind, **data):
        ev = dict(t=datetime.datetime.now().isoformat(timespec="milliseconds"), kind=kind, **data)
        self.events.append(ev)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(ev) + "\n")


def memory_digest(state, jobs, limit=12):
    """Short summary of decisions so far, used to keep scoring consistent across batches."""
    if not state.decisions:
        return ""
    by_id = {j["id"]: j for j in jobs}
    rel = [(i, d) for i, d in state.decisions.items() if d["relevant"]]
    rel.sort(key=lambda x: -x[1]["fit"])
    lines = ["Decisions so far: %d jobs, %d relevant." % (len(state.decisions), len(rel))]
    for i, d in rel[:limit]:
        j = by_id.get(i, {})
        lines.append("- fit %d: %s at %s" % (d["fit"], j.get("title", i), j.get("company", "")))
    fits = [d["fit"] for d in state.decisions.values()]
    lines.append("Fit scores used so far: " + ", ".join("%d x%d" % (f, fits.count(f)) for f in sorted(set(fits), reverse=True)))
    lines.append("Keep using the rubric the same way for this batch.")
    return "\n".join(lines)


def batch_message(batch_jobs, digest):
    rows = "\n".join("- %s | %s | %s | %s" % (j["id"], j["company"], j["title"], j.get("location", "")) for j in batch_jobs)
    parts = ["Screen these %d jobs. Record one decision for each, then call finish.\n\nid | company | title | location\n%s" % (len(batch_jobs), rows)]
    if digest:
        parts.append("Memory from earlier batches:\n" + digest)
    return "\n\n".join(parts)


class Limits:
    def __init__(self, batch_size=8, max_steps_per_batch=None, max_total_tokens=3_000_000,
                 max_cost_usd=None, price_in_per_mtok=None, price_out_per_mtok=None, max_reminders=2):
        self.batch_size = batch_size
        self.max_steps_per_batch = max_steps_per_batch
        self.max_total_tokens = max_total_tokens
        self.max_cost_usd = max_cost_usd
        self.price_in = price_in_per_mtok
        self.price_out = price_out_per_mtok
        self.max_reminders = max_reminders

    def cost(self, usage):
        if usage.get("cost_reported") is not None:
            return usage["cost_reported"]
        if self.price_in is None or self.price_out is None:
            return None
        return usage["input_tokens"] / 1e6 * self.price_in + usage["output_tokens"] / 1e6 * self.price_out


def run(jobs, profile, evidence, model, limits=None, workspace=None, run_dir=None, resume=False, clock=time.monotonic,
        show=None):
    """Screen `jobs` and return a summary dict. Writes state.json and trace.jsonl into run_dir if given.
    show: optional function called with one line of progress per model call (for example print)."""
    limits = limits or Limits()
    run_dir = Path(run_dir) if run_dir else None
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json" if run_dir else None
    if resume and state_path and state_path.exists():
        state = RunState.load(state_path)
        state.stop = None
    else:
        state = RunState(uuid.uuid4().hex[:12], state_path)
    trace = Trace(run_dir / "trace.jsonl" if run_dir else None)
    tb = Toolbox(jobs, state, evidence_text=evidence, **({"workspace": workspace} if workspace else {}))
    system = build_system(profile, evidence)
    started = clock()
    trace("run_start", run_id=state.run_id, agent_version=__version__, model=getattr(model, "model", "?"), jobs=len(jobs), resume=resume)

    todo = [j for j in jobs if j["id"] not in state.decisions]
    batches = [todo[i:i + limits.batch_size] for i in range(0, len(todo), limits.batch_size)]
    for b in batches:
        if state.stop:
            break
        tb.set_batch([j["id"] for j in b])
        messages = [{"role": "user", "content": batch_message(b, memory_digest(state, jobs))}]
        max_steps = limits.max_steps_per_batch or (3 * len(b) + 6)
        reminders = 0
        for step in range(max_steps):
            t0 = clock()
            try:
                resp = model.create(system, messages, SCHEMAS)
            except ModelError as e:
                state.stop = "model_error: %s" % e
                trace("model_error", error=str(e))
                break
            u = resp.get("usage", {})
            answered = resp.get("model") or getattr(model, "model", "?")
            state.models_used[answered] = state.models_used.get(answered, 0) + 1
            state.usage["input_tokens"] += int(u.get("input_tokens", 0))
            state.usage["output_tokens"] += int(u.get("output_tokens", 0))
            state.usage["model_calls"] += 1
            if u.get("cost") is not None:  # real cost reported by the provider (OpenRouter)
                state.usage["cost_reported"] = state.usage.get("cost_reported", 0.0) + float(u["cost"])
            ms = round((clock() - t0) * 1000)
            trace("model_call", step=step, ms=ms, stop_reason=resp.get("stop_reason"), answered_by=answered,
                  input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"))
            if show:
                n_tools = sum(1 for c in resp.get("content", []) if c.get("type") == "tool_use")
                show("Batch %d step %d answered by %s (%d tool call%s, %d ms)" % (
                    state.batches_done + 1, step + 1, answered, n_tools, "" if n_tools == 1 else "s", ms))
            content = resp.get("content") or [{"type": "text", "text": "(no output)"}]
            messages.append({"role": "assistant", "content": content})
            calls = [c for c in content if c.get("type") == "tool_use"]
            if calls:
                results = []
                for c in calls:
                    name = c.get("name")
                    state.tool_calls[name] = state.tool_calls.get(name, 0) + 1
                    t1 = clock()
                    try:
                        out, err = tb.call(name, c.get("input") or {}), False
                    except ToolError as e:
                        out, err = {"error": str(e)}, True
                    except TypeError as e:  # wrong or missing arguments
                        out, err = {"error": "Bad arguments for %s: %s" % (name, e)}, True
                    if err:
                        state.tool_errors[name] = state.tool_errors.get(name, 0) + 1
                    trace("tool_call", name=name, ok=not err, ms=round((clock() - t1) * 1000),
                          args={k: (v if len(str(v)) < 120 else str(v)[:120] + "...") for k, v in (c.get("input") or {}).items()},
                          error=out.get("error") if err else None)
                    r = {"type": "tool_result", "tool_use_id": c.get("id"), "content": json.dumps(out)}
                    if err:
                        r["is_error"] = True
                    results.append(r)
                messages.append({"role": "user", "content": results})
                state.save()
            total = state.usage["input_tokens"] + state.usage["output_tokens"]
            cost = limits.cost(state.usage)
            if total >= limits.max_total_tokens:
                state.stop = "token_budget"
            elif limits.max_cost_usd is not None and cost is not None and cost >= limits.max_cost_usd:
                state.stop = "cost_budget"
            if state.stop or tb.finished:
                break
            if not calls:  # the model stopped talking without finishing the batch
                left = [j["id"] for j in b if j["id"] not in state.decisions]
                if reminders >= limits.max_reminders:
                    trace("batch_abandoned", undecided=left)
                    break
                reminders += 1
                state.reminders += 1
                messages.append({"role": "user", "content": "Use the tools only. %d jobs still have no decision: %s. Record a decision for each, then call finish." % (len(left), ", ".join(left))})
        else:
            trace("max_steps", batch=[j["id"] for j in b])
        state.batches_done += 1
        state.save()

    undecided = [j["id"] for j in jobs if j["id"] not in state.decisions]
    summary = {
        "run_id": state.run_id, "agent_version": __version__, "model": getattr(model, "model", "?"),
        "jobs": len(jobs), "decided": len(jobs) - len(undecided), "undecided": undecided,
        "relevant": sorted(i for i, d in state.decisions.items() if d["relevant"]),
        "stop": state.stop or "completed", "models_used": dict(state.models_used),
        "usage": dict(state.usage), "cost_usd": limits.cost(state.usage),
        "tool_calls": dict(state.tool_calls), "tool_errors": dict(state.tool_errors),
        "tool_error_rate": round(sum(state.tool_errors.values()) / max(1, sum(state.tool_calls.values())), 4),
        "reads": sum(state.reads.values()), "jobs_read": len(state.reads), "revisions": state.revisions,
        "reminders": state.reminders, "model_retries": getattr(model, "retries", 0),
        "ungrounded_rejected": getattr(state, "ungrounded_rejected", 0),
        "seconds": round(clock() - started, 2), "decisions": state.decisions,
    }
    trace("run_end", **{k: v for k, v in summary.items() if k != "decisions"})
    if run_dir:
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary
