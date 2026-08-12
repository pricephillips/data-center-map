# Outcome Model — First Iteration (Phase 3)

Generated 2026-08-12 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **87** (60 advanced, 27 `blocked_confirmed`; base rate of blocked = 0.31)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 2 projects; capacity known for only 47 (median-imputed, with a missingness indicator retained as a feature).

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.85** (10th–90th pct: 0.68–0.93). Chance = 0.50.
- Brier score across folds: **0.172** (10th–90th pct: 0.107–0.220). Predicting the base rate for everyone scores 0.214; lower is better.

The wide fold-to-fold range is the honest picture at n=87: each test fold holds ~17 projects and ~5 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 45 projects; mean predicted 0.11, observed blocked share 0.13
- Predicted 0.33-0.67: 20 projects; mean predicted 0.49, observed blocked share 0.30
- Predicted 0.67-1.00: 22 projects; mean predicted 0.84, observed blocked share 0.68

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.196 (coef -1.42, toward advanced)
- `hyperscaler_involved`: +0.027 (coef -0.61, toward advanced)
- `capacity_missing`: +0.018 (coef +0.55, toward blocked)
- `days_to_first_opposition`: +0.013 (coef -0.34, toward advanced)
- `county_margin_2024`: +0.012 (coef +0.54, toward blocked)
- `n_opposition_events`: +0.011 (coef -0.33, toward advanced)
- `mech_regulatory_action`: +0.009 (coef -0.34, toward advanced)
- `mech_moratorium`: +0.009 (coef -0.32, toward advanced)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=87 with 27 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
