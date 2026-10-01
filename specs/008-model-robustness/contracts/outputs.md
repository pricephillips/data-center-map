# Output contract

Invariants that the selftests and the gates enforce:

1. `calibration_history.csv`: the first 12 header names equal the
   pre-feature header, in order. Every pre-existing row keeps its values.
2. `outcome_model_predictions.csv` is produced by the registered estimator.
   Its values do not depend on whether firthmodels is installed.
3. `*_estimators.csv` holds, for each estimator, exactly one row per CV
   repeat.
4. `county_policy_metrics.json` keeps every pre-feature key and value.
   `spatial_diagnostics` is the only new key. `selected`, `roc_auc` and
   `coefficients` are computed before the diagnostics and are not read by
   them.
5. `feature_search_promotions.json` is computed by `choose_promotions()` from
   the L1 rows only. The shapes file is written after it and is never read by
   it.
6. `models/cards/*.md` exist only for PROMOTE runs. They hold no row-level
   values and pass the leak vocabulary check and the Vale rules.
7. Every new file is declared in `configs/layers.json` with one writer.
