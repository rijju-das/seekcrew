@AGENTS.md

# Claude Code notes for this folder

Read these before any job-search task: `PROFILE.md`, `EVIDENCE_BANK.md`, `WRITING_STYLE_GUIDE.md`.

## Hard rules for every agent here

- Prepare only. Never send an email, submit an application, or message a referee or recruiter. The tracker (`tracker.csv`) may be changed only by adding a row for a newly prepared pack with `python3 tools/app_tools.py tracker-add`. Never edit existing rows or mark anything Applied; the user does that.
- Never invent experience, tools, metrics, courses, collaborations or outcomes. Every claim in a CV or letter must trace to `EVIDENCE_BANK.md`, the master CV, a paper or something the user said.
- Facts that must stay consistent across documents (degree status, dates, titles) are in `PROFILE.md`.
- If a web page cannot be fetched with the search and fetch tools, say so. Do not work around it with curl or scripts.
- Application folders are created only with `python3 tools/app_tools.py new-folder ...`, which runs the duplicate check, picks the next number and refuses on DUPLICATE or POSSIBLE. `check` runs the check alone (exit 0 NEW, 1 POSSIBLE, 2 DUPLICATE).

## Agents (in .claude/agents)

- `job-scout`: finds and scores vacancies, checks each for duplicates, writes a dated shortlist with a Build queue. Creates no folders itself.
- `application-tailor`: for each job in the queue (or one chosen job), re-checks for duplicates, creates the folder, requirements note, tailored CV and cover letter, and adds the tracker row.
- `gap-coach`: says what matches, what is missing and what the user can honestly do about it.
- `recruiter-reviewer`: reviews a finished CV and cover letter as a busy recruiter and fact-checks them.

## Commands (in .claude/commands)

- `/job-sweep [focus | shortlist only]`
- `/prepare-application <job URL or pasted description>`
