"""Model clients.

AnthropicModel calls the Messages API over HTTPS with the standard library, retrying
rate limits, overload and network errors with exponential backoff. ScriptedModel replays
fixed responses so the loop can be tested without a key or any cost.
"""
import json
import os
import random
import time
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class ModelError(RuntimeError):
    """The model call failed and was not retried (or retries ran out)."""


class AnthropicModel:
    def __init__(self, model, api_key=None, max_tokens=4096, timeout=60, max_retries=4,
                 opener=None, sleep=time.sleep):
        if not model:
            raise ModelError("No model ID. Set SEEKCREW_MODEL or pass --model (see docs.claude.com for current model IDs).")
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ModelError("ANTHROPIC_API_KEY is not set.")
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self.retries = 0  # total retries made, reported in the run summary

    def create(self, system, messages, tools):
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens, "system": system,
                           "messages": messages, "tools": tools}).encode("utf-8")
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION,
                   "content-type": "application/json"}
        attempt = 0
        while True:
            req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            try:
                with self._open(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = _error_text(e)
                if e.code not in RETRY_STATUS or attempt >= self.max_retries:
                    raise ModelError("HTTP %s from the Messages API: %s" % (e.code, detail)) from None
                ra = _retry_after(e)
                wait = min(60.0, ra) if ra is not None else min(30.0, 2.0 ** attempt + random.random())
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt >= self.max_retries:
                    raise ModelError("Network error calling the Messages API: %s" % e) from None
                wait = min(30.0, 2.0 ** attempt + random.random())
            attempt += 1
            self.retries += 1
            self._sleep(wait)


def _error_text(e):
    try:
        return json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")[:300]
    except Exception:
        return str(e)
    finally:
        e.close()


def _retry_after(e):
    try:
        return float(e.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None


class ScriptedModel:
    """Returns pre-written responses in order. Each response is a dict shaped like a Messages API reply,
    or a callable taking (system, messages, tools) and returning one, or an Exception to raise."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.model = "scripted"
        self.retries = 0

    def create(self, system, messages, tools):
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages)), "tools": tools})
        if not self.responses:
            raise ModelError("ScriptedModel ran out of responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r(system, messages, tools) if callable(r) else r


def tool_use(name, args, id_=None, text=None, usage=(100, 20)):
    """Helper to build a scripted assistant reply that calls one or more tools."""
    calls = name if isinstance(name, list) else [(name, args)]
    content = [{"type": "text", "text": text}] if text else []
    for i, (n, a) in enumerate(calls):
        content.append({"type": "tool_use", "id": id_ or "tu_%s_%d_%d" % (n, i, random.randint(0, 10**9)), "name": n, "input": a})
    return {"content": content, "stop_reason": "tool_use", "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}}


def text_reply(text, usage=(50, 10)):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}}


OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENAI_URL = "https://api.openai.com/v1"
NO_RETRY_CODES = {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"}
OPENAI_RETRY_STATUS = {408, 429, 500, 502, 503, 504}
STOP_MAP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}


def is_free_model(model_id):
    """Free OpenRouter endpoints may train on or publish prompts, so they never get the user's profile."""
    m = (model_id or "").strip().lower()
    return m.endswith(":free") or m == "openrouter/free" or m.startswith("openrouter/free")


class OpenAICompatModel:
    """Chat Completions client for OpenRouter or the OpenAI API (provider="openrouter" or "openai").

    The loop speaks the Anthropic message shape internally; this class translates requests
    and replies both ways, so the loop, tools and tests are the same for every provider.
    OpenRouter returns the real cost of each call in usage.cost; it is passed on as `cost`.
    """

    def __init__(self, model, api_key=None, base_url=None, max_tokens=4096, timeout=90,
                 max_retries=4, opener=None, sleep=time.sleep, title="Seekcrew", provider="openrouter"):
        if provider not in ("openrouter", "openai"):
            raise ModelError("Unknown Chat Completions provider %r" % provider)
        self.provider = provider
        self.name = "OpenRouter" if provider == "openrouter" else "the OpenAI API"
        key_env = "OPENROUTER_API_KEY" if provider == "openrouter" else "OPENAI_API_KEY"
        base_url = base_url or (OPENROUTER_URL if provider == "openrouter" else OPENAI_URL)
        self.reports_cost = provider == "openrouter"
        if not model:
            raise ModelError("No model ID. Set SEEKCREW_MODEL or pass --model (%s)." % (
                "OpenRouter IDs look like provider/model, see openrouter.ai/models" if provider == "openrouter"
                else "an OpenAI model ID, see platform.openai.com/docs/models"))
        if provider == "openrouter" and is_free_model(model):
            raise ModelError("Free models are not allowed: their providers may train on or publish prompts, and Seekcrew sends "
                             "your profile and evidence bank. Choose a paid model ID (without ':free').")
        self.model = model
        self.api_key = api_key or os.environ.get(key_env)
        if not self.api_key:
            raise ModelError("%s is not set." % key_env)
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.max_tokens, self.timeout, self.max_retries = max_tokens, timeout, max_retries
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self.title = title
        self.retries = 0

    # ---------------------------------------------------------------- translation
    @staticmethod
    def to_openai(system, messages, tools):
        out = [{"role": "system", "content": system}]
        for m in messages:
            c = m["content"]
            if m["role"] == "user":
                if isinstance(c, str):
                    out.append({"role": "user", "content": c})
                    continue
                for b in c:
                    if b.get("type") == "tool_result":
                        text = b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content"))
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                    "content": ("ERROR: " + text) if b.get("is_error") else text})
                    elif b.get("type") == "text":
                        out.append({"role": "user", "content": b["text"]})
            else:
                blocks = c if isinstance(c, list) else [{"type": "text", "text": c}]
                text = "\n".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text"))
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                         for b in blocks if b.get("type") == "tool_use"]
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                elif not text:
                    msg["content"] = "(no output)"
                out.append(msg)
        oa_tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                      "parameters": t["input_schema"]}} for t in tools]
        return out, oa_tools

    @staticmethod
    def from_openai(resp):
        ch = (resp.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                args = {"_unparsable_arguments": raw[:500]}  # becomes a tool error the model can fix
            content.append({"type": "tool_use", "id": tc.get("id") or "call_%d" % len(content),
                            "name": fn.get("name"), "input": args if isinstance(args, dict) else {"_value": args}})
        u = resp.get("usage") or {}
        usage = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}
        if u.get("cost") is not None:
            usage["cost"] = float(u["cost"])
        fr = ch.get("finish_reason")
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else STOP_MAP.get(fr, fr or "end_turn")
        return {"content": content, "stop_reason": stop, "usage": usage, "model": resp.get("model")}

    # ---------------------------------------------------------------- call
    def create(self, system, messages, tools):
        oa_messages, oa_tools = self.to_openai(system, messages, tools)
        # OpenAI's newer models take max_completion_tokens; OpenRouter takes max_tokens
        limit_key = "max_tokens" if self.provider == "openrouter" else "max_completion_tokens"
        body = json.dumps({"model": self.model, limit_key: self.max_tokens, "messages": oa_messages,
                           "tools": oa_tools, "tool_choice": "auto"}).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        if self.provider == "openrouter":
            headers["X-Title"] = self.title
        attempt = 0
        while True:
            req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            wait = None
            try:
                with self._open(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                err = data.get("error") if isinstance(data, dict) else None
                if not err:
                    out = self.from_openai(data)
                    if self.provider == "openrouter" and is_free_model(out.get("model")):
                        raise ModelError("OpenRouter answered with a free model (%s). Stopped so your profile is not sent to free endpoints." % out["model"])
                    return out
                code = err.get("code") if isinstance(err.get("code"), int) else 502
                if code not in OPENAI_RETRY_STATUS or attempt >= self.max_retries:
                    raise ModelError("Provider error %s: %s" % (code, str(err.get("message", ""))[:300]))
            except urllib.error.HTTPError as e:
                detail, code = _openai_error(e)
                if e.code == 402 or code in NO_RETRY_CODES:
                    raise ModelError("HTTP %s from %s: no credit left or a spending limit was reached (%s). %s" % (
                        e.code, self.name, code or "credits", detail)) from None
                if e.code not in OPENAI_RETRY_STATUS or attempt >= self.max_retries:
                    raise ModelError("HTTP %s from %s: %s" % (e.code, self.name, detail)) from None
                wait = _retry_after(e)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt >= self.max_retries:
                    raise ModelError("Network error calling %s: %s" % (self.name, e)) from None
            if wait is None:
                wait = min(30.0, 2.0 ** attempt + random.random())
            attempt += 1
            self.retries += 1
            self._sleep(min(60.0, wait))


def _openai_error(e):
    """(message, code) from an OpenAI-style error body."""
    try:
        err = json.loads(e.read().decode("utf-8")).get("error", {}) or {}
        return str(err.get("message", ""))[:300], str(err.get("code") or err.get("type") or "")
    except Exception:
        return str(e), ""
    finally:
        e.close()


def default_provider():
    for env, name in (("OPENROUTER_API_KEY", "openrouter"), ("OPENAI_API_KEY", "openai"), ("ANTHROPIC_API_KEY", "anthropic")):
        if os.environ.get(env):
            return name
    return "anthropic"


def make_model(provider=None, model=None):
    """Pick the provider: explicit, else SEEKCREW_PROVIDER, else by which key is set
    (OPENROUTER_API_KEY, then OPENAI_API_KEY, then ANTHROPIC_API_KEY)."""
    provider = (provider or os.environ.get("SEEKCREW_PROVIDER") or default_provider()).lower()
    model = model or os.environ.get("SEEKCREW_MODEL")
    if provider in ("openrouter", "openai"):
        return OpenAICompatModel(model, provider=provider)
    if provider == "anthropic":
        return AnthropicModel(model)
    raise ModelError("Unknown provider %r (use openai, openrouter or anthropic)." % provider)
