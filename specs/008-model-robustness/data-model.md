# Data Model: Model Robustness and Governance

## 1. Estimator comparison (`data/outcome_model_estimators.csv`, `data/landmark_model_estimators.csv`)

One row per estimator per CV repeat.

| Column | Notes |
|--------|-------|
| estimator | `logistic_l2_registered` or `firth_logistic`. |
| role | `registered` or `challenger`. |
| window_days | Landmark only: the selected window. Blank in the outcome file. |
| repeat | 0-based repeat index. |
| folds_scored | Folds with both classes present (AUC defined). |
| auc_mean | Mean fold AUC within the repeat. |
| brier_mean | Mean fold Brier within the repeat. |
| max_abs_coef | Largest absolute standardized coefficient across the repeat's folds. |

The summary goes in the module's metrics JSON (outcome model:
`estimator_candidates`). Per estimator, it holds:

- `auc` {p10, p50, p90} over all folds;
- `brier_p50`;
- `n_sign_stable` (coefficients whose sign holds on every fold) and `n_coef`;
- `selected` (`true` only for the registered estimator);
- `note`, which says that adopting a challenger needs a new registration.

## 2. Calibration history (`data/calibration_history.csv`)

The existing 12 columns are unchanged in name, order and meaning. Appended:

| Column | Notes |
|--------|-------|
| mce | Largest gap over non-empty equal-width bins (same 5 bins as `ece`). |
| ece_equal_mass | ECE over 5 equal-count bins. |
| mce_equal_mass | MCE over the same equal-count bins. |
| reliability_bins | JSON list of `[lo, hi, n, mean_pred, observed]`, equal-width, non-empty bins only, 4 decimals. |

Migration: when the file's header is the old 12-column one, it is rewritten
once with the widened header. Old rows get blanks in the new columns, and no
existing value changes.

## 3. County spatial diagnostics (`data/county_policy_metrics.json` key `spatial_diagnostics`)

```json
{
  "residual": "has_enacted_restrictive - calibrated_score",
  "morans_i": {"I": 0.118, "expected_I": -0.0003, "p_sim": 0.001,
               "z_sim": 10.6, "permutations": 999, "n": 3124,
               "islands": 20, "weights": "contiguity, row-standardized,
               data/county_adjacency.csv"},
  "state_grouped_cv": {"auc": {"p10": 0.0, "p50": 0.0, "p90": 0.0},
                       "folds": 5, "repeats": 5, "groups": "state FIPS",
                       "n_states": 0},
  "standard_cv_auc_p50": 0.0,
  "state_grouped_gap": 0.0,
  "note": "diagnostics only; never features, never used for selection"
}
```

When esda or libpysal is missing, `morans_i` is
`{"unavailable": "<reason>"}`. State-grouped CV needs only scikit-learn.

## 4. EBM shapes (`data/feature_search_shapes.json`)

```json
{
  "generated": "YYYY-MM-DD",
  "target": "restrict_profile",
  "role": "challenger; never read by choose_promotions()",
  "config": {"target": "restrict_profile", "interactions": 0, "outer_bags": 8, "max_bins": 32, "seed": 7},
  "n_frame": 3144, "n_positive": 0,
  "held_out_auc": {"p10": 0.0, "p50": 0.0, "p90": 0.0},
  "shapes": {
    "<feature>": {"label": "...", "type": "continuous|nominal",
                  "importance": 0.0,
                  "edges": [], "scores": [], "lower": [], "upper": []}
  }
}
```

For continuous variables, `len(edges) == len(scores) + 1`. For nominal
variables, `edges` holds the category names and `len(edges) == len(scores)`.
Scores are on the log-odds scale.

## 5. Model card (`models/cards/outcome_model_<date>.md`)

Sections, in order:

1. Model description.
2. Data window: frame generated date, decided cases only.
3. Frame: n, positives, base rate.
4. Specification: estimator, features.
5. Cross-validated metrics: AUC and Brier, p10/p50/p90.
6. Calibration: ECE, MCE, equal-mass variants, Brier skill, reliability
   table.
7. Gate result: verdict, reason, thresholds.
8. Limitations: the standing statement that associations are descriptive,
   not causal, and support no cost claim.

Only aggregates appear on the card.
