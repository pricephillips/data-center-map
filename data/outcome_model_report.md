# Outcome Model — First Iteration (Phase 3)

Generated 2026-10-01 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **93** (58 advanced, 35 `blocked_confirmed`; base rate of blocked = 0.38)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 4 projects; capacity known for only 54 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.82** (10th–90th pct: 0.70–0.92). Chance = 0.50.
- Brier score across folds: **0.178** (10th–90th pct: 0.125–0.250). Predicting the base rate for everyone scores 0.235; lower is better.

The wide fold-to-fold range is the honest picture at n=93: each test fold holds ~18 projects and ~7 blocked cases.

## Estimator candidates (spec 008)

| Estimator | Role | AUC median [p10-p90] | Brier median | Sign-stable coefficients | Largest coefficient | Ships |
|---|---|---|---|---|---|---|
| logistic_l2_registered | registered | 0.821 [0.701-0.917] | 0.178 | 8 of 20 | 1.49 | yes |
| firth_logistic | challenger | 0.766 [0.619-0.881] | 0.204 | 5 of 20 | 2.56 | no |

Per-repeat figures (10 repeats per estimator) are in the estimators CSV. Scored beside the registered estimator on identical folds. The registered rule has no estimator dimension, so the registered estimator ships; adopting a challenger requires a new dated registration entry and the calibration gate.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 44 projects; mean predicted 0.13, observed blocked share 0.14
- Predicted 0.33-0.67: 20 projects; mean predicted 0.51, observed blocked share 0.55
- Predicted 0.67-1.00: 29 projects; mean predicted 0.80, observed blocked share 0.62

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.165 (coef -1.16, toward advanced)
- `days_to_first_opposition`: +0.068 (coef -0.87, toward advanced)
- `n_opposition_events`: +0.019 (coef -0.48, toward advanced)
- `hyperscaler_involved`: +0.017 (coef -0.41, toward advanced)
- `mech_moratorium`: -0.013 (coef -0.06, toward advanced)
- `capacity_missing`: +0.013 (coef +0.32, toward blocked)
- `opposition_span_days`: -0.011 (coef +0.03, toward blocked)
- `mech_ordinance`: +0.011 (coef +0.35, toward blocked)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=93 with 35 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
