# Seekcrew agent

The screening step of Seekcrew written as a standalone agent: Python code that calls the Anthropic Messages API with tools, rather than prompts running inside Claude Code. It reads job adverts, decides which belong on your shortlist, gives each a fit score from 1 to 5 with evidence quoted from your evidence bank, and checks duplicates against your tracker. Standard library only.

## Run it

```bash
export ANTHROPIC_API_KEY=...          # from console.anthropic.com; set a spending limit there
export SEEKCREW_MODEL=<model id>      # a current model ID from docs.claude.com
python3 agent/cli.py --jobs eval/data/adverts.jsonl \
    --max-cost 2 --price-in <US$ per M input tokens> --price-out <US$ per M output tokens>

python3 agent/cli.py --jobs eval/data/adverts.jsonl --dry-run     # free: keyword stand-in, no key
python3 agent/cli.py --resume runs/<run folder>                   # continue after a stop
python3 agent/serve.py --port 8770                                # local HTTP service (127.0.0.1)
```

Each run writes `runs/<date>_<kind>/` with `state.json` (decisions so far), `trace.jsonl` (every model call and tool call with timings and tokens) and `summary.json`.

## Design

```
adverts ─► batches of 8 ─► per batch: fresh conversation ─► model ⇄ tools ─► decisions
                 ▲                                                        │
                 └──────────── memory digest of earlier decisions ◄───────┘
```

| Concern | How it is handled |
|---|---|
| Tools | `list_jobs`, `read_job`, `check_duplicate` (the same checker as the Claude Code agents), `record_decision`, `finish`, each with a JSON schema |
| Validation | Tool inputs are checked (job in batch, fit 1-5, types, sizes). A bad call returns an error result (`is_error`) so the model can correct itself; the failure is counted |
| Planning | The model decides per job whether the title is enough or the full advert must be read; `finish` is refused while any job in the batch is undecided |
| Memory | Each batch starts a fresh conversation to keep context small. A digest of earlier decisions and the fit scores used so far is passed in, so scoring stays consistent across batches |
| Errors | Rate limits, overload and network errors are retried with exponential backoff, honouring `retry-after`; other API errors stop the run cleanly. A model that stops without tools is reminded at most twice, then the batch is left undecided rather than guessed |
| Budgets | Steps per batch, total tokens and (when prices are given) estimated cost. Hitting a limit stops the run and reports which jobs are undecided |
| State | Saved after every tool call, so `--resume` continues an interrupted run without repeating decided jobs |
| Tracing | JSON lines per model call (latency, tokens, stop reason) and per tool call (arguments, success, error) |
| Service | `serve.py`: `POST /runs`, `GET /runs/<id>`, `GET /health`; one run at a time; binds to 127.0.0.1 only |
| Grounding | The prompt requires evidence to be quoted from the evidence bank; the evaluation measures how many quotes really are |

## Limits

- It screens adverts you give it; it does not search the web. Collecting adverts is `eval/collect.py` (public job-board APIs) or your own file.
- The prompt and tools are a first version. Use `eval/` to measure them before trusting the decisions, and compare against the keyword baseline.
- Cost estimates use the prices you pass in. Check real spend in the Anthropic console.
- The dry-run model is a keyword rule. Its results are a baseline, not an agent result.
