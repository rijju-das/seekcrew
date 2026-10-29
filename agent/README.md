# Seekcrew agent

The screening step of Seekcrew written as a standalone agent: Python code that calls a model API with tools (OpenRouter or the Anthropic API), rather than prompts running inside Claude Code. It reads job adverts, decides which belong on your shortlist, gives each a fit score from 1 to 5 with evidence quoted from your evidence bank, and checks duplicates against your tracker. Standard library only.

## Run it

```bash
# OpenRouter: one key for many models; it reports the real cost of each call
export OPENROUTER_API_KEY=...         # from openrouter.ai/keys; set a credit limit on the key
export SEEKCREW_MODEL=<provider/model>  # an ID from openrouter.ai/models that supports tool calling
python3 agent/cli.py --jobs eval/data/adverts.jsonl --max-cost 2

# Or the Anthropic API directly (cost is estimated from the prices you pass)
export ANTHROPIC_API_KEY=... SEEKCREW_MODEL=<model id>
python3 agent/cli.py --provider anthropic --jobs eval/data/adverts.jsonl \
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
| Providers | The loop works with one internal message format. `AnthropicModel` speaks the Messages API; `OpenAICompatModel` translates to and from Chat Completions for OpenRouter (tool calls, tool results, errors, usage and reported cost) |
| Tools | `list_jobs`, `read_job`, `check_duplicate` (the same checker as the Claude Code agents), `record_decision`, `finish`, each with a JSON schema |
| Validation | Tool inputs are checked (job in batch, fit 1-5, types, sizes). A bad call returns an error result (`is_error`) so the model can correct itself; the failure is counted |
| Planning | The model decides per job whether the title is enough or the full advert must be read; `finish` is refused while any job in the batch is undecided |
| Memory | Each batch starts a fresh conversation to keep context small. A digest of earlier decisions and the fit scores used so far is passed in, so scoring stays consistent across batches |
| Errors | Rate limits, overload and network errors are retried with exponential backoff, honouring `retry-after`; other API errors stop the run cleanly. A model that stops without tools is reminded at most twice, then the batch is left undecided rather than guessed |
| Budgets | Steps per batch, total tokens and (when prices are given) estimated cost. Hitting a limit stops the run and reports which jobs are undecided |
| State | Saved after every tool call, so `--resume` continues an interrupted run without repeating decided jobs |
| Free models | Refused. Free OpenRouter endpoints may train on or publish prompts, and the agent sends your profile and evidence bank. A run also stops if OpenRouter answers with a free model |
| Tracing | Each model call prints which model answered (`Batch 1 step 2 answered by ...`) and the run ends with a count per model. JSON lines per model call (model that answered, latency, tokens, stop reason) and per tool call (arguments, success, error) |
| Service | `serve.py`: `POST /runs`, `GET /runs/<id>`, `GET /health`; one run at a time; binds to 127.0.0.1 only |
| Grounding | The prompt requires evidence to be quoted from the evidence bank; the evaluation measures how many quotes really are |

## Limits

- It screens adverts you give it; it does not search the web. Collecting adverts is `eval/collect.py` (public job-board APIs) or your own file.
- The prompt and tools are a first version. Use `eval/` to measure them before trusting the decisions, and compare against the keyword baseline.
- With OpenRouter the cost comes from its usage reports; with the Anthropic API it is estimated from the prices you pass. Check real spend in your provider's dashboard.
- Tool calling quality differs a lot between models. Pick a model that lists tool support on OpenRouter, and compare models with `eval/`.
- The dry-run model is a keyword rule. Its results are a baseline, not an agent result.
