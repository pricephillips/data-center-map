# Calibration Gate — Latest Verdict

Run 2026-09-29T16:09:49Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.059 <= 0.15, Brier skill 0.412 >= 0.05, discrimination ok

## Metrics

- Sample: 80 projects, 30 blocked (base rate 0.38)
- Brier score: **0.138** (base-rate baseline 0.234)
- Brier skill score: **0.412** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.059** (ceiling 0.15)
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.72 vs advanced 0.27)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 31 | 0.09 | 0.03 |
| 0.2-0.4 | 8 | 0.26 | 0.25 |
| 0.4-0.6 | 11 | 0.51 | 0.45 |
| 0.6-0.8 | 13 | 0.69 | 0.54 |
| 0.8-1.0 | 17 | 0.90 | 0.88 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-09-14 | 88 | 0.1476 | 0.053 | PROMOTE |
| 2026-09-18 | 98 | 0.1582 | 0.0382 | HOLD |
| 2026-09-28 | 84 | 0.0672 | 0.3304 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 76 | 0.0759 | 0.4238 | PROMOTE |
| 2026-09-29 | 80 | 0.0592 | 0.4117 | PROMOTE |
