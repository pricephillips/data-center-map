# Outcome Model — First Iteration (Phase 3)

Generated 2026-09-28 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **84** (52 advanced, 32 `blocked_confirmed`; base rate of blocked = 0.38)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 5 projects; capacity known for only 46 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.84** (10th–90th pct: 0.68–0.95). Chance = 0.50.
- Brier score across folds: **0.166** (10th–90th pct: 0.099–0.233). Predicting the base rate for everyone scores 0.236; lower is better.

The wide fold-to-fold range is the honest picture at n=84: each test fold holds ~16 projects and ~6 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 36 projects; mean predicted 0.14, observed blocked share 0.11
- Predicted 0.33-0.67: 22 projects; mean predicted 0.50, observed blocked share 0.41
- Predicted 0.67-1.00: 26 projects; mean predicted 0.83, observed blocked share 0.73

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `n_opposition_events`: +0.079 (coef -0.80, toward advanced)
- `days_to_first_opposition`: +0.064 (coef -0.78, toward advanced)
- `mech_public_comment`: +0.053 (coef -0.61, toward advanced)
- `capacity_missing`: +0.045 (coef +0.61, toward blocked)
- `opposition_span_days`: +0.038 (coef +0.59, toward blocked)
- `mech_zoning_restriction`: +0.028 (coef +0.42, toward blocked)
- `petition_signatures_log1p`: +0.021 (coef +0.44, toward blocked)
- `hyperscaler_involved`: +0.019 (coef -0.33, toward advanced)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=84 with 32 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
