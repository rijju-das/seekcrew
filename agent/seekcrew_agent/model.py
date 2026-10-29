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
