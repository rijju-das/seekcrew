#!/usr/bin/env python3
"""Append one short line per Claude Code hook event to dashboard/events.jsonl.

Used only by the local Agent Factory dashboard. Never blocks Claude Code: any error is swallowed
and the script always exits 0 without printing anything.
"""
import json
import sys
import time
from pathlib import Path


def short(value, n=140):
    s = str(value if value is not None else "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "~"


def main():
    d = json.load(sys.stdin)
    ev = d.get("hook_event_name", "")
    tool = d.get("tool_name", "") or ""
    ti = d.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    rec = {"t": int(time.time() * 1000), "ev": ev, "sid": str(d.get("session_id", ""))[:8], "tool": tool}
    sub = ti.get("subagent_type") or d.get("agent_type") or d.get("subagent_type") or ""
    if sub:
        rec["sub"] = short(sub, 60)
    fp = ti.get("file_path") or ti.get("path") or ""
    if fp:
        rec["fp"] = short(fp, 200)
    if ev == "UserPromptSubmit":
        info = d.get("prompt", "")
    elif tool in ("Task", "Agent"):
        info = ti.get("description") or ti.get("prompt") or ""
    elif tool == "Bash":
        info = ti.get("command", "")
    elif tool in ("WebSearch",):
        info = ti.get("query", "")
    elif tool in ("WebFetch",):
        info = ti.get("url", "")
    elif tool in ("Grep", "Glob"):
        info = ti.get("pattern", "")
    else:
        info = fp
    rec["info"] = short(info, 160)
    out = Path(__file__).resolve().parent / "events.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
