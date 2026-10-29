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
from seekcrew_agent.model import AnthropicModel, ModelError, OpenAICompatModel, ScriptedModel, make_model, text_reply, tool_use  # noqa: E402
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


class OpenRouter(Base):
    def oa_reply(self, tool_calls=None, content=None, finish="tool_calls", cost=0.001):
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = [{"id": "call_%d" % i, "type": "function",
                                  "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                                 for i, (n, a) in enumerate(tool_calls)]
        return {"choices": [{"message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": cost}}

    def model(self, replies, sent=None, sleeps=None):
        seq = list(replies)

        def opener(req, timeout):
            self.assertEqual(req.get_header("Authorization"), "Bearer k")
            if sent is not None:
                sent.append(json.loads(req.data))
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return FakeResp(json.dumps(r).encode())
        return OpenAICompatModel("vendor/model", api_key="k", opener=opener,
                                 sleep=(sleeps.append if sleeps is not None else (lambda s: None)))

    def test_full_run_through_translation_with_reported_cost(self):
        sent = []
        m = self.model([self.oa_reply([("record_decision", {"job_id": "j1", "relevant": True, "fit": 4, "reason": "r"}),
                                       ("record_decision", {"job_id": "j2", "relevant": False, "fit": 1, "reason": "r"}),
                                       ("record_decision", "{not json")]),
                        self.oa_reply([("record_decision", {"job_id": "j3", "relevant": True, "fit": 3, "reason": "r"})]),
                        self.oa_reply([("finish", {"summary": "ok"})])], sent=sent)
        s = self.go(m)
        self.assertEqual(s["stop"], "completed")
        self.assertEqual(s["relevant"], ["j1", "j3"])
        self.assertEqual(s["tool_errors"], {"record_decision": 1})  # the unparsable call
        self.assertAlmostEqual(s["cost_usd"], 0.003)
        second = sent[1]["messages"]
        self.assertEqual(second[0]["role"], "system")
        self.assertEqual(second[2]["role"], "assistant")
        self.assertEqual(len(second[2]["tool_calls"]), 3)
        tool_msgs = [x for x in second if x["role"] == "tool"]
        self.assertEqual([x["tool_call_id"] for x in tool_msgs], ["call_0", "call_1", "call_2"])
        self.assertTrue(tool_msgs[2]["content"].startswith("ERROR: "))
        self.assertEqual(sent[0]["tools"][0]["type"], "function")
        self.assertIn("parameters", sent[0]["tools"][0]["function"])

    def test_reported_cost_enforces_the_cap_without_prices(self):
        m = self.model([self.oa_reply([("record_decision", {"job_id": "j1", "relevant": True, "fit": 4, "reason": "r"})], cost=0.6)])
        s = self.go(m, limits=Limits(batch_size=1, max_cost_usd=0.5))
        self.assertEqual(s["stop"], "cost_budget")

    def test_rate_limit_retried_credits_error_not(self):
        sleeps = []
        m = self.model([http_error(429, "1"), {"error": {"code": 502, "message": "provider down"}}, self.oa_reply(content="hi", finish="stop")], sleeps=sleeps)
        out = m.create("s", [{"role": "user", "content": "x"}], [])
        self.assertEqual(out["stop_reason"], "end_turn")
        self.assertEqual(m.retries, 2)
        self.assertEqual(sleeps[0], 1.0)
        m2 = self.model([http_error(402)])
        with self.assertRaises(ModelError) as cm:
            m2.create("s", [{"role": "user", "content": "x"}], [])
        self.assertIn("402", str(cm.exception))
        self.assertEqual(m2.retries, 0)

    def test_free_models_are_refused(self):
        for mid in ("openrouter/free", "vendor/some-model:free", "OpenRouter/Free"):
            with self.assertRaises(ModelError) as cm:
                OpenAICompatModel(mid, api_key="k")
            self.assertIn("Free models are not allowed", str(cm.exception))
        reply = self.oa_reply(content="hi", finish="stop")
        reply["model"] = "vendor/other:free"
        m = self.model([reply])
        with self.assertRaises(ModelError) as cm:
            m.create("s", [{"role": "user", "content": "x"}], [])
        self.assertIn("free model", str(cm.exception))

    def test_answering_model_is_recorded_and_shown(self):
        r1 = self.oa_reply([("record_decision", {"job_id": "j1", "relevant": True, "fit": 4, "reason": "r"}),
                            ("record_decision", {"job_id": "j2", "relevant": False, "fit": 1, "reason": "r"}),
                            ("record_decision", {"job_id": "j3", "relevant": True, "fit": 3, "reason": "r"})])
        r1["model"] = "vendor/model-a"
        r2 = self.oa_reply([("finish", {"summary": "ok"})])
        r2["model"] = "vendor/model-a"
        lines = []
        s = self.go(self.model([r1, r2]), show=lines.append)
        self.assertEqual(s["models_used"], {"vendor/model-a": 2})
        self.assertEqual(len(lines), 2)
        self.assertIn("answered by vendor/model-a (3 tool calls", lines[0])
        events = [json.loads(l) for l in (self.tmp / "run" / "trace.jsonl").read_text().splitlines()]
        self.assertEqual([e["answered_by"] for e in events if e["kind"] == "model_call"], ["vendor/model-a"] * 2)

    def test_openai_provider_request_shape_and_quota_error(self):
        sent, headers = [], []
        replies = [self.oa_reply(content="hi", finish="stop")]

        def opener(req, timeout):
            sent.append(json.loads(req.data))
            headers.append(dict(req.header_items()))
            self.assertTrue(req.full_url.startswith("https://api.openai.com/v1/"))
            return FakeResp(json.dumps(replies.pop(0)).encode())
        m = OpenAICompatModel("gpt-x", api_key="k", provider="openai", opener=opener, sleep=lambda s: None)
        m.create("s", [{"role": "user", "content": "x"}], [])
        self.assertIn("max_completion_tokens", sent[0])
        self.assertNotIn("max_tokens", sent[0])
        self.assertNotIn("X-title", headers[0])
        self.assertFalse(m.reports_cost)
        quota = urllib.error.HTTPError("u", 429, "err", {}, io.BytesIO(b'{"error":{"message":"You exceeded your current quota","code":"insufficient_quota"}}'))

        def opener2(req, timeout):
            raise quota
        m2 = OpenAICompatModel("gpt-x", api_key="k", provider="openai", opener=opener2, sleep=lambda s: None)
        with self.assertRaises(ModelError) as cm:
            m2.create("s", [{"role": "user", "content": "x"}], [])
        self.assertIn("insufficient_quota", str(cm.exception))
        self.assertEqual(m2.retries, 0)

    def test_provider_choice(self):
        import os
        old = {k: os.environ.pop(k, None) for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SEEKCREW_PROVIDER")}
        try:
            os.environ["OPENAI_API_KEY"] = "k"
            m = make_model(None, "gpt-x")
            self.assertEqual(m.provider, "openai")
            os.environ["OPENROUTER_API_KEY"] = "k"
            self.assertIsInstance(make_model(None, "a/b"), OpenAICompatModel)
            os.environ["ANTHROPIC_API_KEY"] = "k"
            self.assertIsInstance(make_model("anthropic", "m"), AnthropicModel)
            with self.assertRaises(ModelError):
                make_model("other", "m")
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


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

    def test_manual_adverts_parse_and_append_without_touching_labels(self):
        import collect as C
        tmp = Path(tempfile.mkdtemp(prefix="seekcrew_manual_"))
        try:
            man = tmp / "manual"
            man.mkdir()
            body = "Responsibilities and requirements. " * 10
            (man / "a.txt").write_text("Company: Acme\nTitle: ML Scientist\nLocation: Dublin\nURL: https://x.example/1\n\n" + body)
            (man / "bad.txt").write_text("Title: No company\n\n" + body)
            ads, problems = C.manual_adverts(man)
            self.assertEqual(len(ads), 1)
            self.assertEqual(ads[0]["source"], "manual")
            self.assertTrue(ads[0]["id"].startswith("manual-acme-"))
            self.assertIn("missing company", problems[0])
            old = C.DATA
            C.DATA = tmp / "data"
            C.DATA.mkdir()
            try:
                board = {"id": "greenhouse-x-1", "company": "X", "title": "T", "location": "Dublin", "url": "https://g/1", "text": "t"}
                C.write_sample(C.DATA / "adverts.jsonl", C.DATA / "labels.csv", [board])
                labels = (C.DATA / "labels.csv").read_text().replace("greenhouse-x-1,X,T,Dublin,https://g/1,,,", "greenhouse-x-1,X,T,Dublin,https://g/1,no,1,")
                (C.DATA / "labels.csv").write_text(labels)
                C.add_manual_only(man)
                C.add_manual_only(man)  # second time adds nothing
                rows = (C.DATA / "labels.csv").read_text().splitlines()
                self.assertEqual(len(rows), 3)
                self.assertIn(",no,1,", rows[1])
                self.assertEqual(len((C.DATA / "adverts.jsonl").read_text().splitlines()), 2)
            finally:
                C.DATA = old
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_labelling_page_saves_one_row_and_keeps_the_rest(self):
        import label as LB
        tmp = Path(tempfile.mkdtemp(prefix="seekcrew_label_"))
        try:
            ads = tmp / "a.jsonl"
            ads.write_text(json.dumps({"id": "j1", "company": "A", "title": "T1", "location": "D", "url": "u1", "text": "x1", "source": "greenhouse"}) + "\n"
                           + json.dumps({"id": "j2", "company": "B", "title": "T2", "location": "D", "url": "u2", "text": "x2", "source": "manual"}) + "\n")
            lab = tmp / "labels.csv"
            lab.write_text("id,company,title,location,url,relevant,fit,notes\nj1,A,T1,D,u1,no,1,keep me\nj2,B,T2,D,u2,,,\n")
            out = LB.save_label(lab, "j2", relevant="yes", fit="4", notes="good\nmatch")
            self.assertEqual(out, {"saved": "j2", "done": 2, "total": 2})
            items = LB.load_items(ads, lab)
            self.assertEqual([(x["relevant"], x["fit"], x["notes"]) for x in items], [("no", "1", "keep me"), ("yes", "4", "good match")])
            self.assertEqual(items[1]["text"], "x2")
            with self.assertRaises(ValueError):
                LB.save_label(lab, "j1", fit="9")
            with self.assertRaises(KeyError):
                LB.save_label(lab, "nope", relevant="yes")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sample_is_seeded_and_mixed(self):
        import collect as C
        pool = [{"id": "t%d" % i, "title": "ML Engineer"} for i in range(40)] + [{"id": "o%d" % i, "title": "Sales Lead"} for i in range(40)]
        a = C.sample(pool, 20, 0.6, r"\b(ml|engineer)\b", 13)
        b = C.sample(pool, 20, 0.6, r"\b(ml|engineer)\b", 13)
        self.assertEqual([x["id"] for x in a], [x["id"] for x in b])
        self.assertEqual(sum(1 for x in a if x["id"].startswith("t")), 12)


if __name__ == "__main__":
    unittest.main()
