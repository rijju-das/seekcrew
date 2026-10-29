# Seekcrew screening evaluation

Date: 2026-10-30_011434. Adverts: 60 labelled (0 unlabelled skipped), 7 labelled relevant. Model: anthropic/claude-haiku-5.5.
Inputs (first 10 hex of SHA-256): profile 8700e869cd, evidence bank f4c06aed35, labels baea00c086. Runs with the same three values used identical inputs.
Sources (adverts, of which labelled relevant): ashby 13 (1), greenhouse 47 (6).
Labels are one person's judgement for their own profile, so results describe agreement with that person, not general accuracy.

## Relevance (shortlist decision)

| System | Runs | Precision | Recall | F1 (95% CI, run 1) | Coverage |
|---|---|---|---|---|---|
| baseline | 1 | 0.67 | 0.86 | 0.75 (0.43 to 0.95) | 1.00 |
| agent | 3 | 1.00 ± 0.00 | 0.57 ± 0.00 | 0.73 ± 0.00 (0.29 to 1.00) | 1.00 ± 0.00 |

## Fit score (1-5) against your fit labels

| System | n | MAE | Exact | Within 1 | Spearman | Weighted kappa |
|---|---|---|---|---|---|---|
| baseline | 60 | 0.37 | 0.67 | 0.97 | 0.68 | 0.67 |
| agent | 60 | 0.23 ± 0.00 | 0.80 ± 0.00 | 0.97 ± 0.00 | 0.77 ± 0.02 | 0.73 ± 0.01 |

## Process (per run)

| System | Model calls | Tokens in | Tokens out | Cost $ | Seconds | Tool error rate | Adverts read | Evidence quotes grounded | Stability |
|---|---|---|---|---|---|---|---|---|---|
| agent | 29.33 ± 1.53 | 387515.67 ± 16661.48 | 40035.00 ± 496.34 | 0.06 ± 0.00 | 198.35 ± 8.23 | 0.02 ± 0.01 | 23.67 ± 0.58 | 1.00 ± 0.00 | 0.98 |

Agent version: 0.2.0. Evidence quotes rejected during the run for not being in the evidence bank: agent: 0, 0, 0

Models that answered: agent: anthropic/claude-haiku-5.5 (88 calls)

Stop reasons: agent: completed, completed, completed

## Notes

- Undecided jobs count as not relevant for precision and recall; coverage shows how many were decided.
- The 95% interval resamples the labelled jobs (2,000 bootstrap samples) and reflects the small sample.
- The board sample was drawn with a fixed seed and takes about half its adverts from titles in the user's field, which makes the keyword baseline look better than on an unfiltered feed. Manual adverts were chosen by the user and are likelier to be relevant.
- Cost is the provider-reported cost when available (OpenRouter), otherwise an estimate from token counts and the prices passed in. Check it against your provider's dashboard.
