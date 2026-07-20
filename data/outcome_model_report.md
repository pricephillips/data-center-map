# Outcome Model — First Iteration (Phase 3)

Generated 2026-07-20 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **83** (57 advanced, 26 `blocked_confirmed`; base rate of blocked = 0.31)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 2 projects; capacity known for only 44 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.82** (10th–90th pct: 0.71–0.93). Chance = 0.50.
- Brier score across folds: **0.177** (10th–90th pct: 0.127–0.239). Predicting the base rate for everyone scores 0.215; lower is better.

The wide fold-to-fold range is the honest picture at n=83: each test fold holds ~16 projects and ~5 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 40 projects; mean predicted 0.14, observed blocked share 0.15
- Predicted 0.33-0.67: 23 projects; mean predicted 0.52, observed blocked share 0.30
- Predicted 0.67-1.00: 20 projects; mean predicted 0.82, observed blocked share 0.65

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.197 (coef -1.35, toward advanced)
- `capacity_missing`: +0.059 (coef +0.71, toward blocked)
- `hyperscaler_involved`: +0.047 (coef -0.63, toward advanced)
- `county_margin_2024`: +0.036 (coef +0.59, toward blocked)
- `mech_legislation`: -0.013 (coef -0.14, toward advanced)
- `log10_capacity_mw`: -0.009 (coef -0.02, toward advanced)
- `has_lawsuit`: -0.008 (coef -0.15, toward advanced)
- `mech_lawsuit`: -0.008 (coef -0.15, toward advanced)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=83 with 26 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
