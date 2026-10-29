---
description: Find new jobs, skip duplicates, create a folder for each shortlisted job, prepare its CV and cover letter, and add it to the tracker
---

Run a full job sweep. Focus (optional): $ARGUMENTS

1. Use the job-scout agent to search for current vacancies that match `PROFILE.md`, across the job boards named in its instructions as well as employer pages. It checks every candidate for duplicates against `applications/` and `tracker.csv` (any status) and writes `shortlists/` with a Build queue. It must report which boards were readable.
2. If the arguments say "shortlist only" or "no packs", stop here and summarise the top five.
3. Otherwise take the Build queue (at most 5 jobs; list the rest as queued for the next run). For each job, strictly one at a time and in queue order, use the application-tailor agent. It re-checks for duplicates, creates the next numbered folder in `applications/`, writes `Job_Requirements.md`, builds a tailored CV and cover letter from the master CV, and adds one row to `tracker.csv` (status Preparing, or Saved when only the requirements note was possible).
4. After each pack, use the recruiter-reviewer agent and apply every must-fix item that does not need the user's confirmation.
5. Finish with one table: folder, employer and role, deadline, fit, status (pack built / requirements note only / skipped as duplicate), and what the user must confirm. Do not send, submit or email anything, and do not change any existing tracker row.
