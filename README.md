# Seekcrew

**A crew of Claude Code agents that search jobs, catch duplicates and prepare tailored applications. Your data is stored locally on your computer, and nothing is sent or submitted without you.**

Seekcrew helps with a job search without ever applying for you. Its agents find current vacancies that match your profile, shortlist them, skip jobs you already have, prepare a folder with a tailored CV and cover letter for each one, and log it in a tracker. You review everything and apply yourself.

It runs on your own computer, in your own Claude Code, on your own files. There is no server and no shared account, so nothing about your search leaves your machine except what Claude Code itself sends.

## What it does

| Part | What it is |
|---|---|
| `job-scout` agent | Searches the boards and employer pages named in your profile, scores fit 1-5 against your evidence, checks every candidate for duplicates, writes a dated shortlist with a build queue and a table of which sources it could and could not read |
| `application-tailor` agent | For one job: duplicate check, numbered folder, requirements note, tailored CV and cover letter, tracker row |
| `gap-coach` agent | Says what matches, what is missing and what you can honestly do about it |
| `recruiter-reviewer` agent | Reads a finished pack like a busy recruiter, then fact-checks every claim against your evidence bank |
| `/job-sweep`, `/prepare-application` | Commands that run the agents in order |
| `tools/app_tools.py` | Plain Python (standard library only): duplicate check, folder numbering, tracker rows with backup and rollback |
| `dashboard/` | Optional local page that shows the agents at work |
| `agent/` | The screening step written as a standalone agent: a tool-use loop on the OpenAI API, OpenRouter or the Anthropic API with batching, memory, error handling, budgets, tracing, resume and a local HTTP service (standard library only). See [agent/README.md](agent/README.md) |
| `eval/` | Measures how well screening agrees with your own labels on a frozen sample of real adverts, against a keyword baseline: precision, recall, F1, fit agreement, cost, tool errors, grounding of evidence quotes and run-to-run stability. See [eval/README.md](eval/README.md) |

## Rules built into the agents

- Prepare only. They never send email, submit applications or contact anyone. The one change they may make to your tracker is to add a new row, through the tool, and they never touch existing rows or mark anything Applied.
- No invented experience. Every claim must trace to `EVIDENCE_BANK.md`, your master CV, or something you said. Gaps are stated plainly.
- No workarounds for blocked pages. Many job boards (LinkedIn, Indeed, HiringCafe and others) block automated reading or load results with JavaScript. The scout reports those boards as "not readable" and falls back to `site:` web searches plus the employer's own page. It does not use curl or scripts to get around a block, and a search snippet alone counts as unverified. Coverage is therefore partial, and every shortlist says so.

## Privacy

- **Stored locally.** Your profile, evidence bank, CVs, application folders, shortlists, tracker and backups are plain files in this folder on your computer.
- **Kept out of git.** `.gitignore` excludes those personal files, so they are not committed or pushed if you fork or share your copy. Run `git status` before every commit to check.
- **Your own API key for `agent/`.** The standalone agent calls the OpenAI API (`OPENAI_API_KEY`), OpenRouter (`OPENROUTER_API_KEY`) or the Anthropic API (`ANTHROPIC_API_KEY`) with your key; your profile, evidence bank and the adverts go to that provider under its data policy. With OpenRouter, the adverts, your profile and evidence bank go to OpenRouter and to the model provider it routes to, under their data policies; check the privacy settings in your OpenRouter account. Never put the key in a file in this folder. Run folders (`runs/`) and evaluation data (`eval/data/`) are git-ignored.
- **No server, account, database or analytics.** Seekcrew has none of these. The optional dashboard listens on `127.0.0.1` only, so other machines cannot open it.
- **What does leave your computer.** When the agents work, Claude Code sends the files they read (for example your CV and profile) and the job texts to Anthropic for processing, as in any Claude Code session, under your Claude plan's terms. The job-scout agent also runs web searches and opens job pages.
- **Nothing is sent for you.** The agents never submit applications, send emails or messages, or fill in forms. You review every document and apply yourself.

## Quick start

You need Python 3.10 or newer and Claude Code.

```bash
git clone https://github.com/<you>/seekcrew.git
cd seekcrew
python3 tools/app_tools.py init     # creates PROFILE.md, EVIDENCE_BANK.md, tracker.csv, config, folders
```

1. Edit `PROFILE.md` (who you are, target roles, locations, boards, what to flag).
2. Edit `EVIDENCE_BANK.md` (each thing you can claim, with its limit).
3. Put your master CV in `master/`.
4. Start Claude Code in this folder and run `/job-sweep`, or `/prepare-application <job link or pasted text>`.

Your personal files are in `.gitignore`, so a fork you push publicly does not include them. Check `git status` before every push.

## The duplicate check

`python3 tools/app_tools.py check --employer "Acme Ltd" --role "Applied Scientist" --url "https://..." --job-id 12345`

It compares the job with every folder in `applications/` and every row in `tracker.csv`, including closed and rejected ones, and returns a verdict:

- `NEW` (exit 0): nothing matches.
- `POSSIBLE` (exit 1): same employer and possibly the same role. A person decides.
- `DUPLICATE` (exit 2): same URL or job ID, or same employer and a similar role.

It normalises URLs, picks job IDs out of text, ignores generic words in employer and role names, and lets you add spelling aliases (for example "International Business Machines" and "IBM") in `seekcrew.config.json`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

`tests/test_agent.py` covers the agent loop with a scripted model (happy path, tool errors fed back, unknown tools, refused early finish, reminders, token and cost budgets, model errors, memory across batches, resume, the duplicate check), the API client's retries, the local service, the metrics and the advert collector. No key or network is needed.

The duplicate-check suite covers folder numbering, refusals, forcing, the tracker (new file, permanent IDs, existing rows unchanged, backup, rollback on a failed write, invalid values rejected) and 12 labelled duplicate cases. The labelled set is small, written by the author with fictional employers and roles, and not independent, so treat the 100% it prints as a regression check, not as a measured accuracy on real postings. Add your own cases to `tests/labelled_pairs.json` when the check gets one wrong.

## Evaluation results (October 2026)

The standalone agent (`agent/`) was evaluated on the author's own job search with the harness in `eval/`: 60 adverts sampled with a fixed seed from public Greenhouse and Ashby boards (about half chosen by titles in the author's field), labelled for relevance and fit (1-5). 7 of the 60 are labelled relevant. The adverts and labels are private, so these exact numbers cannot be reproduced; run the harness on your own search instead.

| Configuration | Runs | Precision | Recall | F1 (95% CI, run 1) | Fit within 1 | Fit weighted kappa | Run-to-run agreement | Grounded quotes | Cost per run |
|---|---|---|---|---|---|---|---|---|---|
| Keyword baseline (title keywords) | 1 | 0.67 | 0.86 | 0.75 (0.43 to 0.95) | 0.97 | 0.67 | n/a | n/a | free |
| Claude Haiku 5.5, agent 0.1.0, profile v1 | 1 | 1.00 | 0.57 | 0.73 (0.29 to 1.00) | 0.97 | 0.75 | n/a | 0.88 | $0.06 |
| Claude Haiku 5.5, agent 0.2.0, profile v1 | 1 | 1.00 | 0.57 | 0.73 (0.29 to 1.00) | 0.97 | 0.75 | n/a | 1.00 | $0.06 |
| GPT-6.1 Terra, agent 0.2.0, profile v1 | 3 | 1.00 | 0.29 | 0.44 (0.00 to 0.80) | 0.97 | 0.61 | 1.00 | 1.00 | $0.10 |
| Claude Haiku 5.5, agent 0.2.0, profile v2 | 3 | 1.00 | 0.57 | 0.73 (0.29 to 1.00) | 0.97 | 0.73 | 0.98 | 1.00 | $0.06 |
| **GPT-6.1 Terra, agent 0.2.0, profile v2** | 3 | **1.00** | **0.76 ± 0.16** | **0.86 ± 0.11** (0.67 to 1.00) | 0.97 | 0.70 | 0.98 | 1.00 | $0.10 |

Models were called through OpenRouter. Reports: [`eval/results/`](eval/results/) (the agent 0.1.0 run's files were lost; its figures come from the report as first read).

What the evaluation found and what changed:

- **Evidence copied from adverts.** Agent 0.1.0 put 2 of 17 "evidence" quotes from the advert's requirements instead of the evidence bank. Agent 0.2.0 tells the model that advert text is never evidence and refuses any quote not found in the evidence bank. Grounding went from 88% to 100%; no quote needed refusing in later runs, so the prompt change did the work and the check is a safety net.
- **An underspecified profile.** With profile v1, GPT-6.1 Terra rejected every Data Scientist role (recall 0.29) while the labels counted them, because the profile did not say whether they count. Profile v2 states that Data Scientist roles centred on applied ML, LLM applications or model evaluation count, and recall rose to 0.76. Most disagreement came from the instructions, not the model's reasoning.
- **Behaviour.** Both models were cautious: precision 1.00 in every run. About one decision in 60 changed between repeated runs. The harness completed all 11 runs with every advert decided; Claude Haiku sent malformed tool arguments 1-2 times per run and recovered each time.

Limits of these results: one person's job search; only 7 relevant adverts, so intervals are wide and the agent's F1 is not significantly better than the baseline's; the labels were drafted by an LLM and confirmed by the author, which can favour LLM agents; the sample over-represents titles in the author's field, which helps the keyword baseline; one labelled-relevant advert has ambiguous eligibility wording, and all 11 runs rejected it.

## Limits

- It does not guarantee a job, an interview or that a listing is still open. Results are a snapshot.
- Fit scores are the model's judgement against the evidence you wrote down. Read the requirements note yourself.
- Generated CVs and letters must be read and corrected by you before use. Confirm every fact and date.
- Check each website's terms before you point an automated tool at it. This project does not scrape boards.
- Tracker is a CSV. Excel and Google Sheets open it, but this tool does not edit spreadsheet files.
- Not legal or immigration advice, and it makes no eligibility decisions unless you ask it to flag something in `PROFILE.md`.

## Layout

```
.claude/agents/      the four agents
.claude/commands/    /job-sweep and /prepare-application
tools/app_tools.py   duplicate check, numbering, tracker (stdlib only)
tests/               unittest suite and labelled cases
dashboard/           optional local dashboard and hook logger
agent/               standalone screening agent: loop, tools, model client, CLI, local service
eval/                advert collector, labelling sheet, metrics, evaluation runner
*.example.*          templates; `init` copies them to your private files
```

## Licence

MIT. See `LICENSE`.
