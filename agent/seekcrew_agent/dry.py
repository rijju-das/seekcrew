"""A keyword-based stand-in for the model, for dry runs without an API key.

It drives the real loop and tools (list, check, record, finish) with simple keyword rules,
so you can test the whole pipeline, the trace and the evaluation for free. Its decisions are
a baseline, not an agent: do not report them as model results.
"""
import json
import re


def _words(text):
    return set(re.findall(r"[a-z][a-z0-9+#.-]{1,}", (text or "").lower()))


class DryRunModel:
    model = "dry-run (keywords)"

    def __init__(self, keywords, threshold=2):
        self.keywords = [k.lower() for k in keywords]
        self.threshold = threshold
        self.retries = 0
        self._n = 0

    def _score(self, title):
        low = " " + title.lower() + " "
        hits = sum(1 for k in self.keywords if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", low))
        return hits

    def create(self, system, messages, tools):
        self._n += 1
        last = messages[-1]["content"]
        usage = {"input_tokens": 0, "output_tokens": 0}
        if isinstance(last, list) and any("recorded" in (r.get("content") or "") for r in last):
            return {"content": [{"type": "tool_use", "id": "dry_f_%d" % self._n, "name": "finish",
                                 "input": {"summary": "dry run"}}], "stop_reason": "tool_use", "usage": usage}
        text = last if isinstance(last, str) else messages[0]["content"]
        rows = re.findall(r"^- ([^|]+?) \| ([^|]*?) \| ([^|]*?) \|", text, flags=re.M)
        calls = []
        for i, (jid, company, title) in enumerate(rows):
            s = self._score(title)
            relevant = s >= self.threshold
            fit = max(1, min(5, 1 + s))
            calls.append({"type": "tool_use", "id": "dry_%d_%d" % (self._n, i), "name": "record_decision",
                          "input": {"job_id": jid.strip(), "relevant": relevant, "fit": fit,
                                    "reason": "keyword score %d" % s, "evidence": [], "duplicate": "NOT_CHECKED"}})
        return {"content": calls, "stop_reason": "tool_use", "usage": usage}
