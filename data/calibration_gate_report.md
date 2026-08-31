# Calibration Gate — Latest Verdict

Run 2026-08-31T14:52:14Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.073 <= 0.15, Brier skill 0.206 >= 0.05, discrimination ok

## Metrics

- Sample: 89 projects, 27 blocked (base rate 0.30)
- Brier score: **0.168** (base-rate baseline 0.211)
- Brier skill score: **0.206** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.073** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.62 vs advanced 0.26)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 37 | 0.08 | 0.08 |
| 0.2-0.4 | 15 | 0.27 | 0.27 |
| 0.4-0.6 | 12 | 0.49 | 0.33 |
| 0.6-0.8 | 12 | 0.71 | 0.50 |
| 0.8-1.0 | 13 | 0.91 | 0.77 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-07-24 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-07-27 | 85 | 0.1262 | 0.1183 | PROMOTE |
| 2026-08-03 | 86 | 0.1155 | 0.1954 | PROMOTE |
| 2026-08-10 | 86 | 0.1155 | 0.1954 | PROMOTE |
| 2026-08-12 | 87 | 0.0853 | 0.2292 | PROMOTE |
| 2026-08-17 | 88 | 0.1389 | 0.1651 | PROMOTE |
| 2026-08-24 | 89 | 0.0726 | 0.2068 | PROMOTE |
| 2026-08-31 | 89 | 0.0725 | 0.2063 | PROMOTE |
