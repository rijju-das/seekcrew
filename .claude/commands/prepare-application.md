---
description: Prepare a full application pack for one job (duplicate check, folder, CV, cover letter), then get gap advice and a recruiter review
---

Prepare an application for this job: $ARGUMENTS

0. Run the duplicate check (`python3 tools/app_tools.py check ...` with the employer, role, URL and any job ID). If it says DUPLICATE or POSSIBLE, stop and tell the user which folder or tracker row matches. Carry on only if the user says it is a different job.
1. Use the gap-coach agent first. If it recommends skip, or a mandatory condition is unresolved, stop and report instead of building documents.
2. Otherwise use the application-tailor agent to create the folder, requirements note, CV and cover letter, and add the tracker row.
3. Use the recruiter-reviewer agent on the finished files and apply any must-fix items that do not need the user's confirmation.
4. Report the folder, files, what the user must confirm, the deadline and any eligibility risk. Do not send or submit anything, and do not change any existing tracker row.
