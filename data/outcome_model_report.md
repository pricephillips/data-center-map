# Outcome Model — First Iteration (Phase 3)

Generated 2026-09-29 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **80** (50 advanced, 30 `blocked_confirmed`; base rate of blocked = 0.38)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 4 projects; capacity known for only 44 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.85** (10th–90th pct: 0.75–0.97). Chance = 0.50.
- Brier score across folds: **0.165** (10th–90th pct: 0.095–0.226). Predicting the base rate for everyone scores 0.234; lower is better.

The wide fold-to-fold range is the honest picture at n=80: each test fold holds ~16 projects and ~6 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 39 projects; mean predicted 0.12, observed blocked share 0.08
- Predicted 0.33-0.67: 14 projects; mean predicted 0.54, observed blocked share 0.50
- Predicted 0.67-1.00: 27 projects; mean predicted 0.83, observed blocked share 0.74

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.145 (coef -1.22, toward advanced)
- `days_to_first_opposition`: +0.042 (coef -0.69, toward advanced)
- `hyperscaler_involved`: +0.031 (coef -0.51, toward advanced)
- `capacity_missing`: +0.021 (coef +0.53, toward blocked)
- `log1p_prior_state_opposition`: +0.019 (coef +0.41, toward blocked)
- `n_opposition_events`: +0.015 (coef -0.43, toward advanced)
- `petition_signatures_log1p`: +0.013 (coef +0.40, toward blocked)
- `opposition_span_days`: -0.009 (coef +0.14, toward blocked)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=80 with 30 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
