# Calibration Gate — Latest Verdict

Run 2026-08-10T08:26:37Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.115 <= 0.15, Brier skill 0.195 >= 0.05, discrimination ok

## Metrics

- Sample: 86 projects, 27 blocked (base rate 0.31)
- Brier score: **0.173** (base-rate baseline 0.215)
- Brier skill score: **0.195** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.115** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.64 vs advanced 0.29)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 33 | 0.08 | 0.06 |
| 0.2-0.4 | 13 | 0.30 | 0.38 |
| 0.4-0.6 | 11 | 0.50 | 0.27 |
| 0.6-0.8 | 14 | 0.69 | 0.29 |
| 0.8-1.0 | 15 | 0.88 | 0.87 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-07-15 | 78 | 0.1348 | 0.1022 | PROMOTE |
| 2026-07-20 | 83 | 0.1265 | 0.1351 | PROMOTE |
| 2026-07-23 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-07-24 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-07-27 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-08-03 | 86 | 0.1155 | 0.1954 | PROMOTE |
| 2026-08-10 | 86 | 0.1155 | 0.1954 | PROMOTE |
