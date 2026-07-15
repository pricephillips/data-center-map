# Calibration Gate — Latest Verdict

Run 2026-07-15T16:44:29Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.135 <= 0.15, Brier skill 0.102 >= 0.05, discrimination ok

## Metrics

- Sample: 78 projects, 24 blocked (base rate 0.31)
- Brier score: **0.191** (base-rate baseline 0.213)
- Brier skill score: **0.102** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.135** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.57 vs advanced 0.30)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 30 | 0.09 | 0.17 |
| 0.2-0.4 | 14 | 0.29 | 0.14 |
| 0.4-0.6 | 11 | 0.50 | 0.36 |
| 0.6-0.8 | 11 | 0.66 | 0.36 |
| 0.8-1.0 | 12 | 0.87 | 0.75 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.
