# Calibration Gate — Latest Verdict

Run 2026-07-23T21:13:42Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.126 <= 0.15, Brier skill 0.118 >= 0.05, discrimination ok

## Metrics

- Sample: 85 projects, 26 blocked (base rate 0.31)
- Brier score: **0.187** (base-rate baseline 0.212)
- Brier skill score: **0.118** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.126** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.62 vs advanced 0.31)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 31 | 0.07 | 0.10 |
| 0.2-0.4 | 15 | 0.29 | 0.33 |
| 0.4-0.6 | 13 | 0.50 | 0.31 |
| 0.6-0.8 | 15 | 0.72 | 0.33 |
| 0.8-1.0 | 11 | 0.92 | 0.82 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-07-15 | 78 | 0.1348 | 0.1022 | PROMOTE |
| 2026-07-20 | 83 | 0.1265 | 0.1351 | PROMOTE |
| 2026-07-23 | 85 | 0.1262 | 0.1183 | PROMOTE |
