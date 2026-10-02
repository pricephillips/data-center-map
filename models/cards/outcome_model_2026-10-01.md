
# Model description

Outcome model: L2-regularized logistic regression (C = 0.5, class-weighted) on decided and opposed data center projects, estimating the probability that a decided project's terminal disposition is blocked rather than advanced.

## Data window

Training frame built 2026-10-01 from the project lifecycle layer. Decided cases only (terminal dispositions); pending projects are never labels.

## Frame

93 projects, 35 blocked (base rate 0.38).

## Specification

Median imputation, standardized inputs, 20 features: `n_opposition_events`, `n_opposition_groups`, `has_lawsuit`, `opposition_span_days`, `county_margin_2024`, `log10_capacity_mw`, `capacity_missing`, `petition_signatures_log1p`, `hyperscaler_involved`, `log1p_existing_dc_in_county`, `days_to_first_opposition`, `log1p_prior_state_opposition`, `mech_moratorium`, `mech_zoning_restriction`, `mech_lawsuit`, `mech_public_comment`, `mech_legislation`, `mech_ordinance`, `mech_regulatory_action`, `mech_petition`. Estimator registered in outcome_model.py; challengers are reported in data/outcome_model_estimators.csv and do not ship.

# Evaluation

Repeated stratified cross-validation, 5 folds x 10 repeats.

## Cross-validated metrics

AUC (area under the ROC curve: the chance a random blocked project scores above a random advanced one; 0.5 is chance) median 0.8214, p10 0.699, p90 0.9167. Brier (mean squared gap between predicted probability and outcome; lower is better) median 0.1783, p10 0.125, p90 0.2498; predicting the base rate scores 0.2347.

## Calibration

On out-of-fold predictions: ECE 0.09, MCE 0.1993, equal-mass ECE 0.0699, equal-mass MCE 0.1324, Brier skill 0.2324.

| Predicted bin | Projects | Mean predicted | Observed share |
|---|---|---|---|
| 0.0-0.2 | 31 | 0.08 | 0.03 |
| 0.2-0.4 | 16 | 0.28 | 0.38 |
| 0.4-0.6 | 13 | 0.51 | 0.54 |
| 0.6-0.8 | 18 | 0.71 | 0.61 |
| 0.8-1.0 | 15 | 0.87 | 0.67 |

# Gate result

PROMOTE on 2026-10-01T21:39:37Z: PASSED: ECE 0.090 <= 0.15, Brier skill 0.232 >= 0.05, discrimination ok. Thresholds: ECE <= 0.15, Brier skill >= 0.05, n >= 60, positives >= 20.

# Limitations

Associations in this model are predictive and descriptive, not causal. Scores describe how closely a decided project's recorded profile resembles blocked projects in the training frame; they are not a forecast for any single project and support no cost or effect-size claim.
