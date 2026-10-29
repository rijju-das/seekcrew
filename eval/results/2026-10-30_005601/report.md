# Seekcrew screening evaluation

Date: 2026-10-30_005601. Adverts: 60 labelled (0 unlabelled skipped), 7 labelled relevant. Model: openai/gpt-6.1-terra.
Sources (adverts, of which labelled relevant): ashby 13 (1), greenhouse 47 (6).
Labels are one person's judgement for their own profile, so results describe agreement with that person, not general accuracy.

## Relevance (shortlist decision)

| System | Runs | Precision | Recall | F1 (95% CI, run 1) | Coverage |
|---|---|---|---|---|---|
| baseline | 1 | 0.67 | 0.86 | 0.75 (0.43 to 0.95) | 1.00 |
| agent | 3 | 1.00 ± 0.00 | 0.29 ± 0.00 | 0.44 ± 0.00 (0.00 to 0.80) | 1.00 ± 0.00 |

## Fit score (1-5) against your fit labels

| System | n | MAE | Exact | Within 1 | Spearman | Weighted kappa |
|---|---|---|---|---|---|---|
| baseline | 60 | 0.37 | 0.67 | 0.97 | 0.68 | 0.67 |
| agent | 60 | 0.27 ± 0.03 | 0.78 ± 0.01 | 0.97 ± 0.02 | 0.70 ± 0.06 | 0.61 ± 0.06 |

## Process (per run)

| System | Model calls | Tokens in | Tokens out | Cost $ | Seconds | Tool error rate | Adverts read | Evidence quotes grounded | Stability |
|---|---|---|---|---|---|---|---|---|---|
| agent | 28.33 ± 0.58 | 196763.67 ± 5354.58 | 7661.33 ± 42.25 | 0.10 ± 0.01 | 150.11 ± 2.25 | 0.00 ± 0.00 | 25.00 ± 1.00 | 1.00 ± 0.00 | 1.00 |

Agent version: 0.2.0. Evidence quotes rejected during the run for not being in the evidence bank: agent: 0, 0, 0

Models that answered: agent: openai/gpt-6.1-terra (85 calls)

Stop reasons: agent: completed, completed, completed

## Notes

- Undecided jobs count as not relevant for precision and recall; coverage shows how many were decided.
- The 95% interval resamples the labelled jobs (2,000 bootstrap samples) and reflects the small sample.
- The board sample was drawn with a fixed seed and takes about half its adverts from titles in the user's field, which makes the keyword baseline look better than on an unfiltered feed. Manual adverts were chosen by the user and are likelier to be relevant.
- Cost is the provider-reported cost when available (OpenRouter), otherwise an estimate from token counts and the prices passed in. Check it against your provider's dashboard.
