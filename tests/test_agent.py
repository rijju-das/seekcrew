"""Tests for the agent loop, tools, model client and service. No API key or network needed:
the model is scripted (or the keyword dry-run stand-in) and HTTP calls go to a fake opener."""
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agent"))
sys.path.insert(0, str(REPO / "eval"))

from seekcrew_agent.loop import Limits, memory_digest, run  # noqa: E402
from seekcrew_agent.model import AnthropicModel, ModelError, ScriptedModel, text_reply, tool_use  # noqa: E402
from seekcrew_agent.dry import DryRunModel  # noqa: E402

JOBS = [{"id": "j1", "company": "Acme", "title": "Machine Learning Scientist", "location": "Dublin", "url": "https://acme.example/1", "text": "ML role"},
        {"id": "j2", "company": "Acme", "title": "Account Executive", "location": "Dublin", "url": "https://acme.example/2", "text": "Sales"},
        {"id": "j3", "company": "Beta", "title": "AI Research Engineer", "location": "Remote, Europe", "url": "https://beta.example/3", "text": "AI"}]


def decide(jid, rel=True, fit=4, evidence=None):
    return ("record_decision", {"job_id": jid, "relevant": rel, "fit": fit, "reason": "test", "evidence": evidence or []})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seekcrew_agent_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def go(self, model, jobs=JOBS, **kw):
        limits = kw.pop("limits", Limits(batch_size=kw.pop("batch_size", 8)))
        return run(jobs, "profile", "- Built a vision-language model", model, limits,
                   workspace=self.tmp, run_dir=self.tmp / "run", **kw)


class Loop(Base):
    def test_happy_path_records_every_job_and_writes_trace(self):
        m = ScriptedModel([tool_use("list_jobs", {}),
                           tool_use([decide("j1"), decide("j2", False, 1), decide("j3", True, 3)], None),
                           tool_use("finish", {"summary": "done"})])
        s = self.go(m)
        self.assertEqual(s["stop"], "completed")
        self.assertEqual(s["decided"], 3)
        self.assertEqual(s["relevant"], ["j1", "j3"])
        self.assertEqual(s["tool_error_rate"], 0)
        kinds = [json.loads(l)["kind"] for l in (self.tmp / "run" / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(kinds[0], "run_start")
        self.assertEqual(kinds[-1], "run_end")
        self.assertIn("tool_call", kinds)
        self.assertTrue((self.tmp / "run" / "state.json").exists())

    def test_invalid_tool_input_goes_back_as_error_and_the_model_can_fix_it(self):
        m = ScriptedModel([tool_use(*decide("j1", fit=7)),
                           tool_use([decide("j1"), decide("j2", False, 1), decide("j3")], None),
                           tool_use("finish", {"summary": "ok"})])
        s = self.go(m)
        self.assertEqual(s["stop"], "completed")
        self.assertEqual(s["tool_errors"], {"record_decision": 1})
        err = [b for b in m.calls[1]["messages"][-1]["content"] if b.get("is_error")]
        self.assertEqual(len(err), 1)
        self.assertIn("fit must be", err[0]["content"])

    def test_unknown_tool_and_bad_arguments_are_errors_not_crashes(self):
        m = ScriptedModel([tool_use([("delete_everything", {}), ("read_job", {"wrong": 1})], None),
                           tool_use([decide("j1"), decide("j2"), decide("j3")], None),
                           tool_use("finish", {"summary": "ok"})])
        s = self.go(m)
        self.assertEqual(s["stop"], "completed")
        self.assertEqual(sum(s["tool_errors"].values()), 2)

    def test_finish_with_undecided_jobs_is_refused(self):
        m = ScriptedModel([tool_use("finish", {"summary": "too early"}),
                           tool_use([decide("j1"), decide("j2"), decide("j3")], None),
                           tool_use("finish", {"summary": "ok"})])
        s = self.go(m)
        self.assertEqual(s["tool_errors"], {"finish": 1})
        self.assertEqual(s["decided"], 3)

    def test_text_without_tools_gets_a_reminder_then_continues(self):
        m = ScriptedModel([text_reply("Here is my analysis..."),
                           tool_use([decide("j1"), decide("j2"), decide("j3")], None),
                           tool_use("finish", {"summary": "ok"})])
        s = self.go(m)
        self.assertEqual(s["reminders"], 1)
        self.assertEqual(s["stop"], "completed")
        self.assertIn("still have no decision", m.calls[1]["messages"][-1]["content"])

    def test_gives_up_on_a_batch_after_two_reminders_and_reports_undecided(self):
        m = ScriptedModel([text_reply("a"), text_reply("b"), text_reply("c")])
        s = self.go(m)
        self.assertEqual(s["decided"], 0)
        self.assertEqual(sorted(s["undecided"]), ["j1", "j2", "j3"])

    def test_token_budget_stops_cleanly(self):
        m = ScriptedModel([tool_use(*decide("j1"), usage=(900, 200))])
        s = self.go(m, limits=Limits(batch_size=1, max_total_tokens=1000))
        self.assertEqual(s["stop"], "token_budget")
        self.assertEqual(s["undecided"], ["j2", "j3"])

    def test_cost_budget_uses_prices(self):
        m = ScriptedModel([tool_use(*decide("j1"), usage=(1_000_000, 0))])
        s = self.go(m, limits=Limits(batch_size=1, max_total_tokens=10**9, max_cost_usd=0.5, price_in_per_mtok=1.0, price_out_per_mtok=5.0))
        self.assertEqual(s["stop"], "cost_budget")
        self.assertAlmostEqual(s["cost_usd"], 1.0)

    def test_model_error_stops_the_run(self):
        m = ScriptedModel([ModelError("HTTP 401")])
        s = self.go(m)
        self.assertTrue(s["stop"].startswith("model_error"))

    def test_memory_carries_decisions_into_the_next_batch(self):
        m = ScriptedModel([tool_use(*decide("j1", True, 5)), tool_use("finish", {"summary": "1"}),
                           tool_use(*decide("j2", False, 1)), tool_use("finish", {"summary": "2"}),
                           tool_use(*decide("j3", True, 4)), tool_use("finish", {"summary": "3"})])
        s = self.go(m, batch_size=1)
        self.assertEqual(s["decided"], 3)
        first_of_batch2 = m.calls[2]["messages"][0]["content"]
        self.assertIn("Memory from earlier batches", first_of_batch2)
        self.assertIn("fit 5: Machine Learning Scientist at Acme", first_of_batch2)
        self.assertEqual(len(m.calls[2]["messages"]), 1)  # a fresh conversation per batch

    def test_resume_skips_decided_jobs(self):
        m1 = ScriptedModel([tool_use(*decide("j1"), usage=(900, 200))])
        s1 = self.go(m1, limits=Limits(batch_size=1, max_total_tokens=1000))
        self.assertEqual(s1["stop"], "token_budget")
        m2 = ScriptedModel([tool_use(*decide("j2", False, 1)), tool_use("finish", {"summary": "x"}),
                            tool_use(*decide("j3")), tool_use("finish", {"summary": "y"})])
        s2 = self.go(m2, limits=Limits(batch_size=1), resume=True)
        self.assertEqual(s2["stop"], "completed")
        self.assertEqual(s2["decided"], 3)
        self.assertEqual(s2["run_id"], s1["run_id"])
        self.assertNotIn("j1", m2.calls[0]["messages"][0]["content"])

    def test_duplicate_check_uses_the_workspace_tracker(self):
        (self.tmp / "tracker.csv").write_text("ID,Company,Role,Job link,Status\n1,Acme,Machine Learning Scientist,https://acme.example/1,Applied\n")
        m = ScriptedModel([tool_use("check_duplicate", {"employer": "Acme", "role": "Machine Learning Scientist", "url": "https://acme.example/1"}),
                           tool_use([decide("j1", False, 4), decide("j2", False, 1), decide("j3")], None),
                           tool_use("finish", {"summary": "ok"})])
        self.go(m)
        result = json.loads(m.calls[1]["messages"][-1]["content"][0]["content"])
        self.assertEqual(result["verdict"], "DUPLICATE")

    def test_dry_run_model_drives_the_real_loop(self):
        s = self.go(DryRunModel(["machine learning", "ai", "research", "scientist", "engineer"], threshold=2))
        self.assertEqual(s["stop"], "completed")
        self.assertEqual(s["relevant"], ["j1", "j3"])

    def test_memory_digest_is_empty_at_start(self):
        from seekcrew_agent.loop import RunState
        self.assertEqual(memory_digest(RunState("x"), JOBS), "")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    hdrs = {"retry-after": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://api.anthropic.com/v1/messages", code, "err", hdrs,
                                  io.BytesIO(b'{"error":{"message":"overloaded"}}'))


class Client(unittest.TestCase):
    def test_retries_overload_then_succeeds(self):
        seq = [http_error(529), http_error(429, "0"), FakeResp(json.dumps(text_reply("ok")).encode())]
        sleeps = []

        def opener(req, timeout):
            self.assertEqual(req.get_header("X-api-key"), "k")
            self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        m = AnthropicModel("some-model", api_key="k", opener=opener, sleep=sleeps.append)
        out = m.create("sys", [{"role": "user", "content": "hi"}], [])
        self.assertEqual(out["stop_reason"], "end_turn")
        self.assertEqual(m.retries, 2)
        self.assertEqual(sleeps[1], 0.0)  # honours retry-after

    def test_client_errors_are_not_retried(self):
        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise http_error(400)
        m = AnthropicModel("some-model", api_key="k", opener=opener, sleep=lambda s: None)
        with self.assertRaises(ModelError):
            m.create("s", [], [])
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_max_retries(self):
        def opener(req, timeout):
            raise urllib.error.URLError("down")
        m = AnthropicModel("some-model", api_key="k", opener=opener, sleep=lambda s: None, max_retries=2)
        with self.assertRaises(ModelError):
            m.create("s", [], [])
        self.assertEqual(m.retries, 2)

    def test_missing_model_or_key_fails_fast(self):
        with self.assertRaises(ModelError):
            AnthropicModel("", api_key="k")


class Service(Base):
    def test_post_a_run_and_poll_until_done(self):
        import serve
        svc = serve.Service(lambda: DryRunModel(["machine learning", "ai", "research", "scientist", "engineer"], 2),
                            Limits(), "p", "e", str(self.tmp), str(self.tmp / "runs"))
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(svc, {"model": "dry-run"}))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        try:
            health = json.loads(urllib.request.urlopen(base + "/health").read())
            self.assertTrue(health["ok"])
            req = urllib.request.Request(base + "/runs", data=json.dumps({"jobs": JOBS}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            rid = json.loads(urllib.request.urlopen(req).read())["run_id"]
            for _ in range(100):
                st = json.loads(urllib.request.urlopen(base + "/runs/" + rid).read())
                if st["status"] != "running":
                    break
                time.sleep(0.05)
            self.assertEqual(st["status"], "done")
            self.assertEqual(st["summary"]["relevant"], ["j1", "j3"])
            bad = urllib.request.Request(base + "/runs", data=b'{"jobs": [{"id": 1}]}', method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(bad)
            self.assertEqual(cm.exception.code, 400)
        finally:
            httpd.shutdown()


class Metrics(unittest.TestCase):
    def test_metrics_on_known_values(self):
        import metrics as M
        self.assertEqual(M.prf(*M.confusion({"a": True, "b": True}, {"a": True, "b": False, "c": True}))["f1"], 0.5)
        self.assertAlmostEqual(M.quadratic_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), 1.0)
        self.assertAlmostEqual(M.quadratic_kappa([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertAlmostEqual(M.spearman([1, 2, 3, 4], [1, 3, 2, 4]), 0.8)
        self.assertEqual(M.ranks([3, 1, 3, 2]), [3.5, 1.0, 3.5, 2.0])
        g = M.evidence_grounding({"x": {"evidence": ["Built a VLM!", "invented claim"]}}, "- Built a VLM and more")
        self.assertEqual(g, {"quotes": 2, "grounded": 0.5})
        self.assertEqual(M.pairwise_agreement([{"a": True, "b": False}, {"a": True, "b": True}]), 0.5)


class Collect(unittest.TestCase):
    def test_normalise_each_provider_and_location_filter(self):
        import collect as C
        gh = C.normalise("greenhouse", "Acme", {"id": 5, "title": "ML Scientist", "location": {"name": "Dublin, Ireland"},
                                                "absolute_url": "https://g/5", "content": "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;"})
        self.assertEqual(gh["text"], "Hello & welcome")
        lv = C.normalise("lever", "Beta", {"id": "ab", "text": "Data Engineer", "categories": {"location": "Cork"},
                                           "hostedUrl": "https://l/ab", "descriptionPlain": "Desc", "lists": [{"text": "You", "content": "<li>Python</li>"}]})
        self.assertIn("Python", lv["text"])
        ab = C.normalise("ashby", "Gamma", {"id": "x1", "title": "Researcher", "location": "London", "isRemote": True,
                                            "jobUrl": "https://a/x1", "descriptionPlain": "D"})
        self.assertEqual(ab["location"], "London, Remote")
        self.assertIsNone(C.normalise("ashby", "Gamma", {"id": "x2", "title": "Hidden", "jobUrl": "u", "isListed": False}))
        inc, exc = [r"\bireland\b", r"\bdublin\b"], ["northern ireland", "belfast"]
        self.assertTrue(C.location_ok("Dublin, Ireland", inc, exc))
        self.assertFalse(C.location_ok("Belfast, Northern Ireland", inc, exc))
        self.assertFalse(C.location_ok("London", inc, exc))

    def test_sample_is_seeded_and_mixed(self):
        import collect as C
        pool = [{"id": "t%d" % i, "title": "ML Engineer"} for i in range(40)] + [{"id": "o%d" % i, "title": "Sales Lead"} for i in range(40)]
        a = C.sample(pool, 20, 0.6, r"\b(ml|engineer)\b", 13)
        b = C.sample(pool, 20, 0.6, r"\b(ml|engineer)\b", 13)
        self.assertEqual([x["id"] for x in a], [x["id"] for x in b])
        self.assertEqual(sum(1 for x in a if x["id"].startswith("t")), 12)


if __name__ == "__main__":
    unittest.main()
