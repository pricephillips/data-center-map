# Calibration Gate — Latest Verdict

Run 2026-10-02T16:30:42Z on `outcome_model` out-of-fold predictions.

## Verdict: **PROMOTE**

PASSED: ECE 0.090 <= 0.15, Brier skill 0.232 >= 0.05, discrimination ok

## Metrics

- Sample: 93 projects, 35 blocked (base rate 0.38)
- Brier score: **0.180** (base-rate baseline 0.235)
- Brier skill score: **0.232** (>0 beats the baseline; floor 0.05)
- Expected calibration error (ECE): **0.090** (ceiling 0.15)
- Maximum calibration error (MCE): **0.199** (largest bin gap; proposed ceiling 0.3, not enforced)
- Equal-mass bins (5 bins of about 18 projects): ECE 0.070, MCE 0.132
- Discrimination (positives predicted higher than negatives): yes (mean pred: blocked 0.62 vs advanced 0.30)

## Reliability table (out-of-fold)

| Predicted bin | Projects | Mean predicted | Observed blocked |
|---|---|---|---|
| 0.0-0.2 | 31 | 0.08 | 0.03 |
| 0.2-0.4 | 16 | 0.28 | 0.38 |
| 0.4-0.6 | 13 | 0.51 | 0.54 |
| 0.6-0.8 | 18 | 0.71 | 0.61 |
| 0.8-1.0 | 15 | 0.87 | 0.67 |

Well-calibrated means mean-predicted and observed track each other down each row. Gaps are where the model is over- or under-confident.

## Proposed threshold (for adoption; not part of this verdict)

MCE <= 0.3 on the equal-width bins. This run: MCE 0.199, which would meet the proposed ceiling. The gate criteria above are unchanged until the ceiling is adopted and recorded in this module.

## Production county model (report only)

Read from data/county_policy_scores.csv (`calibrated_score`, cross-fitted: no county's recalibration was fit on itself). 3144 counties, 484 with an enacted restriction. No history row is written for this model, so the verdict history above stays the outcome model's.

- ECE 0.012, MCE 0.172 (equal-width, 5 bins)
- ECE 0.029, MCE 0.040 (equal-mass, 5 bins of about 628 counties)

| Predicted bin | Counties | Mean predicted | Observed share |
|---|---|---|---|
| 0.012-0.070 | 628 | 0.054 | 0.019 |
| 0.070-0.096 | 629 | 0.082 | 0.062 |
| 0.096-0.130 | 629 | 0.111 | 0.143 |
| 0.130-0.211 | 629 | 0.162 | 0.202 |
| 0.211-0.845 | 629 | 0.360 | 0.343 |

Equal-mass bins are shown because most county scores sit below 0.2, where equal-width bins would put nearly every county in one row.

## Promotion policy

A model is promoted only when ECE <= 0.15, Brier skill >= 0.05, discrimination holds, and the sample clears n >= 60 with >= 20 positives. A model that ranks well but is overconfident is held, consistent with the platform's rule to report calibrated ranges rather than unexplained point estimates. Thin data always holds; it never promotes.

## History (this model)

| Run | n | ECE | Brier skill | Verdict |
|---|---|---|---|---|
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 77 | 0.0501 | 0.3961 | PROMOTE |
| 2026-09-29 | 76 | 0.0759 | 0.4238 | PROMOTE |
| 2026-09-29 | 80 | 0.0592 | 0.4117 | PROMOTE |
| 2026-09-29 | 80 | 0.0592 | 0.4117 | PROMOTE |
| 2026-10-01 | 93 | 0.09 | 0.2324 | PROMOTE |
| 2026-10-02 | 93 | 0.09 | 0.2324 | PROMOTE |

Model card: `models/cards/outcome_model_2026-10-02.md`.
