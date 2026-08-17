# Calibration Gate — Latest Verdict

Run 2026-08-17T07:52:37Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.139 <= 0.15, Brier skill 0.165 >= 0.05, discrimination ok

## Metrics

- Sample: 88 projects, 27 blocked (base rate 0.31)
- Brier score: **0.178** (base-rate baseline 0.213)
- Brier skill score: **0.165** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.139** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.61 vs advanced 0.28)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 37 | 0.08 | 0.16 |
| 0.2-0.4 | 15 | 0.29 | 0.07 |
| 0.4-0.6 | 11 | 0.52 | 0.45 |
| 0.6-0.8 | 12 | 0.70 | 0.42 |
| 0.8-1.0 | 13 | 0.91 | 0.77 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-07-20 | 83 | 0.1265 | 0.1351 | PROMOTE |
| 2026-07-23 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-07-24 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-07-27 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-08-03 | 86 | 0.1155 | 0.1954 | PROMOTE |
| 2026-08-10 | 86 | 0.1155 | 0.1954 | PROMOTE |
| 2026-08-12 | 87 | 0.0853 | 0.2292 | PROMOTE |
| 2026-08-17 | 88 | 0.1389 | 0.1651 | PROMOTE |
