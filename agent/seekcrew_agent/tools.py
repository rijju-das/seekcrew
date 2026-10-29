"""Tools the agent can call, with their JSON schemas, input validation and error reporting.

Every tool returns a JSON-serialisable result or raises ToolError. The loop sends a ToolError
back to the model as an error tool result (is_error) so the model can correct itself, and
counts it as a tool-call failure in the run summary.
"""
import importlib.util
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class ToolError(Exception):
    pass


SCHEMAS = [
    {"name": "list_jobs",
     "description": "List the jobs in the current batch with id, company, title, location and whether you have already decided on them.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "read_job",
     "description": "Read the full advert text for one job in the current batch. Use it when the title and location are not enough to decide.",
     "input_schema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"], "additionalProperties": False}},
    {"name": "check_duplicate",
     "description": "Check whether a job is already in the user's applications or tracker. Returns NEW, POSSIBLE or DUPLICATE with reasons. Call it before marking a job relevant.",
     "input_schema": {"type": "object", "properties": {
         "employer": {"type": "string"}, "role": {"type": "string"},
         "url": {"type": "string"}, "job_id": {"type": "string"}},
         "required": ["employer", "role"], "additionalProperties": False}},
    {"name": "record_decision",
     "description": "Record your decision for one job. relevant: true if it should go on the user's shortlist. fit: 1-5 using the rubric in the instructions. reason: one or two sentences. evidence: short quotes from the evidence bank that support the fit (empty if none). duplicate: the check_duplicate verdict if you ran it.",
     "input_schema": {"type": "object", "properties": {
         "job_id": {"type": "string"}, "relevant": {"type": "boolean"},
         "fit": {"type": "integer", "minimum": 1, "maximum": 5},
         "reason": {"type": "string"},
         "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
         "duplicate": {"type": "string", "enum": ["NEW", "POSSIBLE", "DUPLICATE", "NOT_CHECKED"]}},
         "required": ["job_id", "relevant", "fit", "reason"], "additionalProperties": False}},
    {"name": "finish",
     "description": "Call once every job in the batch has a decision. summary: one or two sentences.",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}},
]


def _load_app_tools(root):
    """Load tools/app_tools.py with SEEKCREW_ROOT pointing at the workspace to check against."""
    old = os.environ.get("SEEKCREW_ROOT")
    os.environ["SEEKCREW_ROOT"] = str(root)
    try:
        spec = importlib.util.spec_from_file_location("seekcrew_app_tools_%d" % id(root), REPO / "tools" / "app_tools.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if old is None:
            os.environ.pop("SEEKCREW_ROOT", None)
        else:
            os.environ["SEEKCREW_ROOT"] = old


class Toolbox:
    def __init__(self, jobs, state, workspace=REPO, max_chars=6000):
        self.jobs = {j["id"]: j for j in jobs}
        self.state = state
        self.max_chars = max_chars
        self.batch = list(self.jobs)
        self._app_tools = _load_app_tools(Path(workspace))
        self.finished = False

    def set_batch(self, ids):
        self.batch = list(ids)
        self.finished = False

    def _job(self, job_id):
        if job_id not in self.jobs:
            raise ToolError("Unknown job_id %r. Call list_jobs for valid ids." % job_id)
        if job_id not in self.batch:
            raise ToolError("Job %r is not in the current batch." % job_id)
        return self.jobs[job_id]

    def call(self, name, args):
        fn = getattr(self, "t_" + name, None)
        if fn is None:
            raise ToolError("Unknown tool %r." % name)
        if not isinstance(args, dict):
            raise ToolError("Tool input must be an object.")
        return fn(**args) if args else fn()

    # ------------------------------------------------------------------ tools
    def t_list_jobs(self):
        return [{"id": i, "company": self.jobs[i]["company"], "title": self.jobs[i]["title"],
                 "location": self.jobs[i].get("location", ""), "decided": i in self.state.decisions}
                for i in self.batch]

    def t_read_job(self, job_id):
        job = self._job(job_id)
        self.state.reads[job_id] = self.state.reads.get(job_id, 0) + 1
        text = job.get("text", "")
        cut = len(text) > self.max_chars
        return {"id": job_id, "company": job["company"], "title": job["title"], "location": job.get("location", ""),
                "url": job.get("url", ""), "text": text[: self.max_chars] + (" [truncated]" if cut else "")}

    def t_check_duplicate(self, employer, role, url="", job_id=""):
        if not employer.strip() or not role.strip():
            raise ToolError("employer and role must not be empty.")
        verdict, matches = self._app_tools.check(employer, role, url or "", job_id or "")
        return {"verdict": verdict, "matches": [{"where": rec["where"], "reasons": reasons}
                                                for _, rec, reasons in matches[:3]]}

    def t_record_decision(self, job_id, relevant, fit, reason, evidence=None, duplicate="NOT_CHECKED"):
        self._job(job_id)
        if not isinstance(relevant, bool):
            raise ToolError("relevant must be true or false.")
        if isinstance(fit, bool) or not isinstance(fit, int) or not 1 <= fit <= 5:
            raise ToolError("fit must be a whole number from 1 to 5.")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolError("reason must be a non-empty sentence.")
        evidence = evidence or []
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence) or len(evidence) > 5:
            raise ToolError("evidence must be a list of at most 5 strings.")
        if duplicate not in ("NEW", "POSSIBLE", "DUPLICATE", "NOT_CHECKED"):
            raise ToolError("duplicate must be NEW, POSSIBLE, DUPLICATE or NOT_CHECKED.")
        revised = job_id in self.state.decisions
        self.state.decisions[job_id] = {"relevant": relevant, "fit": fit, "reason": reason.strip()[:500],
                                        "evidence": [e[:300] for e in evidence], "duplicate": duplicate}
        if revised:
            self.state.revisions += 1
        left = [i for i in self.batch if i not in self.state.decisions]
        return {"recorded": job_id, "revised": revised, "undecided_in_batch": len(left)}

    def t_finish(self, summary):
        left = [i for i in self.batch if i not in self.state.decisions]
        if left:
            raise ToolError("%d jobs in this batch have no decision yet: %s" % (len(left), ", ".join(left[:10])))
        self.finished = True
        return {"finished": True}
