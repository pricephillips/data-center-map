# Outcome Model — First Iteration (Phase 3)

Generated 2026-08-17 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **88** (61 advanced, 27 `blocked_confirmed`; base rate of blocked = 0.31)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 2 projects; capacity known for only 47 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.82** (10th–90th pct: 0.72–0.94). Chance = 0.50.
- Brier score across folds: **0.173** (10th–90th pct: 0.104–0.226). Predicting the base rate for everyone scores 0.213; lower is better.

The wide fold-to-fold range is the honest picture at n=88: each test fold holds ~17 projects and ~5 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 48 projects; mean predicted 0.12, observed blocked share 0.15
- Predicted 0.33-0.67: 19 projects; mean predicted 0.51, observed blocked share 0.37
- Predicted 0.67-1.00: 21 projects; mean predicted 0.85, observed blocked share 0.62

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.194 (coef -1.39, toward advanced)
- `hyperscaler_involved`: +0.031 (coef -0.57, toward advanced)
- `capacity_missing`: +0.023 (coef +0.51, toward blocked)
- `county_margin_2024`: +0.022 (coef +0.52, toward blocked)
- `log10_capacity_mw`: -0.019 (coef -0.02, toward advanced)
- `days_to_first_opposition`: +0.015 (coef -0.41, toward advanced)
- `n_opposition_groups`: -0.012 (coef -0.08, toward advanced)
- `n_opposition_events`: +0.011 (coef -0.33, toward advanced)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=88 with 27 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
