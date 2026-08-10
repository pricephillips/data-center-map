# Outcome Model — First Iteration (Phase 3)

Generated 2026-08-10 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **86** (59 advanced, 27 `blocked_confirmed`; base rate of blocked = 0.31)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 2 projects; capacity known for only 46 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.83** (10th–90th pct: 0.70–0.93). Chance = 0.50.
- Brier score across folds: **0.176** (10th–90th pct: 0.114–0.218). Predicting the base rate for everyone scores 0.215; lower is better.

The wide fold-to-fold range is the honest picture at n=86: each test fold holds ~17 projects and ~5 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 41 projects; mean predicted 0.11, observed blocked share 0.12
- Predicted 0.33-0.67: 22 projects; mean predicted 0.51, observed blocked share 0.27
- Predicted 0.67-1.00: 23 projects; mean predicted 0.83, observed blocked share 0.70

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.197 (coef -1.39, toward advanced)
- `hyperscaler_involved`: +0.030 (coef -0.57, toward advanced)
- `capacity_missing`: +0.019 (coef +0.52, toward blocked)
- `county_margin_2024`: +0.016 (coef +0.53, toward blocked)
- `log10_capacity_mw`: -0.015 (coef -0.02, toward advanced)
- `days_to_first_opposition`: +0.015 (coef -0.36, toward advanced)
- `mech_regulatory_action`: +0.010 (coef -0.37, toward advanced)
- `mech_legislation`: -0.008 (coef -0.07, toward advanced)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=86 with 27 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
