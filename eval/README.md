# Evaluating screening

How often does a screening system agree with your own judgement about which jobs belong on your shortlist, and at what cost? This folder measures that on a frozen sample of real adverts.

## Steps

1. **Collect a sample** (on your own computer; it needs normal internet access):

   ```bash
   cp eval/boards.example.json eval/boards.json          # edit the boards and location patterns
   python3 eval/collect.py                               # writes eval/data/adverts.jsonl and labels.csv
   ```

   It reads public Greenhouse, Lever and Ashby job-board APIs, keeps adverts in your locations and draws a seeded sample (default 60) that mixes technical and non-technical titles. The sample is frozen: rerunning the evaluation uses the same adverts. `eval/data/` is git-ignored because advert text belongs to the employers.

2. **Label it yourself** in `eval/data/labels.csv` (Excel, Numbers or any editor; save as CSV). Label before you look at any system's output, so the labels are not influenced by it.

   - `relevant`: `yes` if you would want this job on your shortlist (field, level and location all acceptable), otherwise `no`.
   - `fit`: 1 to 5 for every row, using the rubric below, judged on what the advert asks for against what you can show.
   - `notes`: optional, one line (for example "needs a security clearance").

   | Fit | Meaning |
   |---|---|
   | 5 | The core requirements match your evidence directly; a strong application |
   | 4 | A good match with minor gaps |
   | 3 | A partial match with clear gaps; worth considering |
   | 2 | A weak match; most core requirements are not shown in your evidence |
   | 1 | Not a match, or out of scope (wrong field, level or location) |

3. **Check the pipeline for free**:

   ```bash
   python3 eval/run_eval.py --systems baseline,dry
   ```

   `dry` drives the real agent loop and tools with the keyword rule; it must score exactly like `baseline` (the run prints PASS).

4. **Evaluate the agent** (needs `OPENROUTER_API_KEY` and `SEEKCREW_MODEL`; costs money):

   ```bash
   python3 eval/run_eval.py --systems baseline,agent --repeats 3 --max-cost 2
   ```

   `--max-cost` caps each run, using the cost OpenRouter reports. Start with `--repeats 1` to see the cost of one run. To compare models, run it once per model (change `SEEKCREW_MODEL`). With the OpenAI API (`OPENAI_API_KEY`) or the Anthropic API instead, add `--provider openai` or `--provider anthropic`, plus `--price-in <US$/M> --price-out <US$/M>` so the cost cap can estimate spend.

5. **Compare other systems** by exporting their decisions as `{"job_id": {"relevant": true, "fit": 4}}` and adding `--import name=file.json` (for example the Claude Code job-scout's decisions on the same adverts).

## What is measured

| Group | Metrics |
|---|---|
| Relevance | Precision, recall, F1 with a 95% bootstrap interval, accuracy, coverage (share of jobs decided) |
| Fit | Mean absolute error, exact and within-1 agreement, Spearman correlation, quadratic weighted kappa |
| Process | Model calls, input and output tokens, estimated cost, seconds, tool-call error rate, adverts read in full, reminders, revisions, retries |
| Grounding | Share of evidence quotes that appear verbatim (after normalising case and punctuation) in your evidence bank |
| Stability | Agreement of relevant/not-relevant calls between repeated runs |

Each agent run prints which model answered every call, and the report lists the models that answered, so you can confirm one model produced the results. Free models are refused.

Results go to `eval/results/<date>/report.md` and `results.json` (metrics only, no advert text). Per-job decisions stay in `runs/`.

## Reading the results honestly

- The labels are one person's judgement for one profile. A high score means the system agrees with that person, not that it is accurate in general.
- 60 adverts give wide intervals; the report shows them. Differences inside the interval are not evidence that one system is better.
- The sample mixes technical and non-technical titles, which makes keyword matching look better than it would on an unfiltered feed. Say so when you quote the baseline.
- The sample is a snapshot of the boards you chose on the day you collected it.
