# Calibration Gate — Latest Verdict

Run 2026-09-18T11:49:39Z on `outcome_model` out-of-fold predictions.

## Verdict: **HOLD**

MISCALIBRATED: ECE 0.158 > 0.15 ceiling

## Metrics

- Sample: 98 projects, 30 blocked (base rate 0.31)
- Brier score: **0.204** (base-rate baseline 0.212)
- Brier skill score: **0.038** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.158** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.61 vs advanced 0.34)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 38 | 0.10 | 0.08 |
| 0.2-0.4 | 12 | 0.31 | 0.50 |
| 0.4-0.6 | 15 | 0.48 | 0.27 |
| 0.6-0.8 | 19 | 0.73 | 0.42 |
| 0.8-1.0 | 14 | 0.88 | 0.64 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-08-17 | 88 | 0.1389 | 0.1651 | PROMOTE |
| 2026-08-24 | 89 | 0.0726 | 0.2068 | PROMOTE |
| 2026-08-31 | 89 | 0.0725 | 0.2063 | PROMOTE |
| 2026-09-02 | 87 | 0.1103 | 0.227 | PROMOTE |
| 2026-09-02 | 87 | 0.1103 | 0.227 | PROMOTE |
| 2026-09-07 | 88 | 0.1034 | 0.2416 | PROMOTE |
| 2026-09-14 | 88 | 0.1476 | 0.053 | PROMOTE |
| 2026-09-18 | 98 | 0.1582 | 0.0382 | HOLD |
