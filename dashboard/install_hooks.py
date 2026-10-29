#!/usr/bin/env python3
"""Add the Agent Factory logging hooks to this project's .claude/settings.json.

Run it yourself, once:   python3 dashboard/install_hooks.py
Safe to run again: it never duplicates the hooks and keeps a backup of your settings.
To remove: python3 dashboard/install_hooks.py --remove
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SETTINGS = PROJECT / ".claude" / "settings.json"
CMD = 'python3 "$CLAUDE_PROJECT_DIR/dashboard/log_event.py"'
EVENTS = {"UserPromptSubmit": None, "PreToolUse": "*", "PostToolUse": "*", "SubagentStop": None, "Stop": None}


def is_ours(entry):
    return any(h.get("command") == CMD for h in entry.get("hooks", []))


def main():
    remove = "--remove" in sys.argv
    data = {}
    if SETTINGS.exists():
        data = json.loads(SETTINGS.read_text(encoding="utf-8") or "{}")
        shutil.copy2(SETTINGS, SETTINGS.with_suffix(".json.bak"))
    hooks = data.setdefault("hooks", {})
    for ev, matcher in EVENTS.items():
        lst = [e for e in hooks.get(ev, []) if not is_ours(e)]
        if not remove:
            entry = {"hooks": [{"type": "command", "command": CMD}]}
            if matcher:
                entry["matcher"] = matcher
            lst.append(entry)
        if lst:
            hooks[ev] = lst
        else:
            hooks.pop(ev, None)
    if not hooks:
        data.pop("hooks", None)
    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(("Removed hooks from " if remove else "Hooks installed in ") + str(SETTINGS))
    if not remove:
        print("Restart Claude Code in this folder so it picks them up (check with /hooks).")


if __name__ == "__main__":
    main()
