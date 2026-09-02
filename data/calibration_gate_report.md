# Calibration Gate — Latest Verdict

Run 2026-09-02T21:48:19Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.110 <= 0.15, Brier skill 0.227 >= 0.05, discrimination ok

## Metrics

- Sample: 87 projects, 26 blocked (base rate 0.30)
- Brier score: **0.162** (base-rate baseline 0.210)
- Brier skill score: **0.227** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.110** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.63 vs advanced 0.26)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 33 | 0.07 | 0.12 |
| 0.2-0.4 | 23 | 0.29 | 0.17 |
| 0.4-0.6 | 6 | 0.51 | 0.17 |
| 0.6-0.8 | 11 | 0.69 | 0.64 |
| 0.8-1.0 | 14 | 0.91 | 0.71 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-08-03 | 86 | 0.1155 | 0.1954 | PROMOTE |
| 2026-08-10 | 86 | 0.1155 | 0.1954 | PROMOTE |
| 2026-08-12 | 87 | 0.0853 | 0.2292 | PROMOTE |
| 2026-08-17 | 88 | 0.1389 | 0.1651 | PROMOTE |
| 2026-08-24 | 89 | 0.0726 | 0.2068 | PROMOTE |
| 2026-08-31 | 89 | 0.0725 | 0.2063 | PROMOTE |
| 2026-09-02 | 87 | 0.1103 | 0.227 | PROMOTE |
| 2026-09-02 | 87 | 0.1103 | 0.227 | PROMOTE |
