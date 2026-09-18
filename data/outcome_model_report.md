# Outcome Model — First Iteration (Phase 3)

Generated 2026-09-18 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **98** (68 advanced, 30 `blocked_confirmed`; base rate of blocked = 0.31)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 4 projects; capacity known for only 54 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.79** (10th–90th pct: 0.67–0.89). Chance = 0.50.
- Brier score across folds: **0.178** (10th–90th pct: 0.132–0.242). Predicting the base rate for everyone scores 0.212; lower is better.

The wide fold-to-fold range is the honest picture at n=98: each test fold holds ~19 projects and ~6 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 45 projects; mean predicted 0.12, observed blocked share 0.16
- Predicted 0.33-0.67: 22 projects; mean predicted 0.48, observed blocked share 0.32
- Predicted 0.67-1.00: 31 projects; mean predicted 0.80, observed blocked share 0.52

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.186 (coef -1.35, toward advanced)
- `days_to_first_opposition`: +0.030 (coef -0.52, toward advanced)
- `log1p_prior_state_opposition`: +0.025 (coef +0.43, toward blocked)
- `county_margin_2024`: +0.022 (coef +0.43, toward blocked)
- `n_opposition_events`: +0.016 (coef -0.41, toward advanced)
- `n_opposition_groups`: -0.016 (coef +0.05, toward blocked)
- `capacity_missing`: +0.012 (coef +0.31, toward blocked)
- `hyperscaler_involved`: +0.010 (coef -0.37, toward advanced)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=98 with 30 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
