# Dashboard (optional)

A local, read-only view of what the agents are doing, drawn as a small airport: the job-scout, application-tailor, gap-coach and recruiter-reviewer are staff at counters, and each job is a suitcase moving along.

```bash
python3 dashboard/serve.py        # then open http://127.0.0.1:8765
```

- **Demo** mode plays a scripted run and needs no setup.
- **Live** mode reads `dashboard/events.jsonl`, which Claude Code hooks write while you run `/job-sweep` or `/prepare-application`.
  Run `python3 dashboard/install_hooks.py` once, restart Claude Code in this folder, and check with `/hooks`.
  Remove the hooks with `python3 dashboard/install_hooks.py --remove`.

The server listens on 127.0.0.1 only. The event log holds short snippets of your prompts, file paths and commands (cut to about 160 characters). It is git-ignored; delete it any time and do not share it.
