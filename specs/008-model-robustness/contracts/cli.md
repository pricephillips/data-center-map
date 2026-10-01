# CLI contract

| Command | Behavior | Exit |
|---------|----------|------|
| `python estimator_candidates.py --selftest` | Separable fixture: unpenalized logistic regression diverges and Firth stays finite. Also checks that the comparison returns one row per estimator per repeat. Prints SKIP for the Firth checks without firthmodels. | 0 pass, 1 fail |
| `python outcome_model.py` | As before, plus the Firth challenger rows and summary. Production predictions come from the registered estimator only. | unchanged |
| `python outcome_model.py --selftest` | First selftest. Runs the challenger path on a synthetic frame. | 0 or 1 |
| `python landmark_model.py` | As before. When a window is selected, it also writes the challenger file. | unchanged |
| `python landmark_model.py --selftest` | Existing checks, plus the challenger path on a synthetic frame. | 0 or 1 |
| `python calibration_gate.py` | As before, plus new columns, the county section, and a card on PROMOTE. | 0 PROMOTE, 10 HOLD, 1 error (unchanged) |
| `python calibration_gate.py --selftest` | First selftest. Calibrated vs shifted ECE, MCE >= ECE, equal-mass bins, header widening on a temp file, card written to a temp dir. | 0 or 1 |
| `python county_policy_model.py` | As before, plus `spatial_diagnostics`. | unchanged |
| `python county_policy_model.py --selftest` | Existing checks, plus a planted-cluster grid (significant I) vs shuffled (not significant), and state-grouped CV on synthetic data. | 0 or 1 |
| `python feature_search.py` | As before, plus EBM AUC on the configured target and the shapes file. | unchanged |
| `python feature_search.py --selftest` | Existing checks, plus the EBM monotone-shape check. | 0 or 1 |
