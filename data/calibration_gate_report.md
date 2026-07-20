# Calibration Gate — Latest Verdict

Run 2026-07-20T10:10:20Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.127 <= 0.15, Brier skill 0.135 >= 0.05, discrimination ok

## Metrics

- Sample: 83 projects, 26 blocked (base rate 0.31)
- Brier score: **0.186** (base-rate baseline 0.215)
- Brier skill score: **0.135** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.127** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.61 vs advanced 0.32)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 28 | 0.09 | 0.07 |
| 0.2-0.4 | 17 | 0.29 | 0.35 |
| 0.4-0.6 | 12 | 0.53 | 0.42 |
| 0.6-0.8 | 14 | 0.68 | 0.14 |
| 0.8-1.0 | 12 | 0.90 | 0.92 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-07-15 | 78 | 0.1348 | 0.1022 | PROMOTE |
| 2026-07-20 | 83 | 0.1265 | 0.1351 | PROMOTE |
