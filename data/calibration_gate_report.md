# Calibration Gate — Latest Verdict

Run 2026-09-29T01:29:16Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.050 <= 0.15, Brier skill 0.396 >= 0.05, discrimination ok

## Metrics

- Sample: 77 projects, 31 blocked (base rate 0.40)
- Brier score: **0.145** (base-rate baseline 0.241)
- Brier skill score: **0.396** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.050** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.72 vs advanced 0.27)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 26 | 0.07 | 0.08 |
| 0.2-0.4 | 13 | 0.25 | 0.23 |
| 0.4-0.6 | 8 | 0.54 | 0.50 |
| 0.6-0.8 | 9 | 0.70 | 0.44 |
| 0.8-1.0 | 21 | 0.90 | 0.86 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-09-02 | 87 | 0.1103 | 0.227 | PROMOTE |
| 2026-09-07 | 88 | 0.1034 | 0.2416 | PROMOTE |
| 2026-09-14 | 88 | 0.1476 | 0.053 | PROMOTE |
| 2026-09-18 | 98 | 0.1582 | 0.0382 | HOLD |
| 2026-09-28 | 84 | 0.0672 | 0.3304 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
