# Quickstart: Model Robustness and Governance

```bash
uv pip install --system -c requirements/ci.txt firthmodels esda libpysal interpret-core skops
```

1. **US1 Firth**: run `python estimator_candidates.py --selftest`, then
   `python outcome_model.py`. Check `data/outcome_model_estimators.csv` (10
   repeats x 2 estimators) and the "Estimator candidates" section of
   `data/outcome_model_report.md`. Then confirm that
   `git diff data/outcome_model_predictions.csv` is empty apart from new
   frame rows.
2. **US2 calibration**: run `python calibration_gate.py --selftest`, then
   `python calibration_gate.py`. The last history row has `mce` and
   `reliability_bins`. The report has the MCE proposal and the county
   section.
3. **US3 spatial**: run `python county_policy_model.py` (after
   `county_aggregator.py` and `county_features.py`). The report shows
   Moran's I and the state-grouped AUC beside the standard AUC.
4. **US4 EBM**: run `python feature_search.py --target restrict_profile`.
   This writes `data/feature_search_shapes.json`.
   `data/feature_search_promotions.json` is unaffected by the EBM.
5. **US5 cards**: after a PROMOTE, `models/cards/outcome_model_<today>.md`
   exists.

Gates: `pre-commit run --all-files`, `python -m pytest
tests/test_selftests.py`, `python leak_audit.py --tier blocking`,
`python layer_audit.py --strict --no-write` and
`python integration_audit.py`.
