# Outcome Model — First Iteration (Phase 3)

Generated 2026-09-14 by `outcome_model.py`. All figures re-derived from current CSVs at generation time; the exact feature matrix is in `outcome_model_features.csv`.

**Internal diagnostic only — NOT client-facing.** This is a retrospective association analysis on a small, selection-affected sample. Feature importance is predictive association, not causation. Nothing here supports effect-size or cost claims.

## Sample

- Decided + opposed projects: **88** (62 advanced, 26 `blocked_confirmed`; base rate of blocked = 0.30)
- Labels are terminal dispositions only, per the decided-case rule.
- Features with missingness: county margin missing for 2 projects; capacity known for only 38 (median-imputed, with a missingness indicator retained as a feature).

> **Low-coverage features.** Observed on under 25% of the 88 projects, so they are mostly median-imputed and their coefficients carry little evidence. A feature that used to be well covered appearing here means its upstream source changed, not that the projects changed:
> - `days_to_first_opposition`: observed on 4/88 projects (5%)

## Model and validation

L2-regularized logistic regression (C=0.5, class-weighted), median imputation, standardized inputs. 5-fold stratified CV repeated 10× (50 evaluated folds).

- ROC-AUC across folds: **0.78** (10th–90th pct: 0.56–0.90). Chance = 0.50.
- Brier score across folds: **0.204** (10th–90th pct: 0.139–0.312). Predicting the base rate for everyone scores 0.208; lower is better.

The wide fold-to-fold range is the honest picture at n=88: each test fold holds ~17 projects and ~5 blocked cases.

## Coarse calibration (out-of-fold, first repeat)

- Predicted 0.00-0.33: 42 projects; mean predicted 0.11, observed blocked share 0.12
- Predicted 0.33-0.67: 20 projects; mean predicted 0.46, observed blocked share 0.35
- Predicted 0.67-1.00: 26 projects; mean predicted 0.82, observed blocked share 0.54

## Predictive associations (permutation importance, AUC drop, averaged over CV test folds)

AUC drop when permuted; sign = direction of the fold-averaged standardized coefficient (+ associates with blocked_confirmed, - with advanced_confirmed).

- `mech_public_comment`: +0.175 (coef -1.26, toward advanced)
- `hyperscaler_involved`: +0.068 (coef -0.78, toward advanced)
- `county_margin_2024`: +0.030 (coef +0.64, toward blocked)
- `mech_legislation`: -0.019 (coef -0.14, toward advanced)
- `log10_capacity_mw`: -0.014 (coef +0.08, toward blocked)
- `n_opposition_events`: +0.013 (coef -0.33, toward advanced)
- `mech_ordinance`: +0.011 (coef +0.39, toward blocked)
- `opposition_span_days`: -0.011 (coef +0.04, toward blocked)

Read these as "which features the model used," not "what causes blocks." In particular, opposition intensity features (events, span, groups) are partially contemporaneous with the outcome process — they describe how contested fights unfolded, and are not ex-ante predictors for a new project.

## Limitations (binding)

- n=88 with 26 blocked cases; estimates are unstable by nature. Growing the seed via link triage and date recovery is the highest-leverage improvement.
- Sample is opposed projects only; this model says nothing about unopposed baselines (the matched-control work addresses that separately).
- No delay/survival modeling yet: only projects with verified decision dates can enter that model (see date-recovery worklist).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate; until then this is run manually.
