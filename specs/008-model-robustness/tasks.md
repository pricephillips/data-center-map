# Tasks: Model Robustness and Governance

**Input**: plan.md, research.md, data-model.md and contracts/ in this
directory.

`[P]` marks a task that can run in parallel with the others in its phase.

## Phase 1: Setup

- [x] T001 Add firthmodels, esda, libpysal, interpret-core and skops to `requirements/ci.in`, and recompile `requirements/ci.txt` in place. Verify that no existing pin moved.
- [x] T002 [P] `configs/integrations.json`:
  - netcal moves to `eliminated` (cost-infra, make-versus-take);
  - the esda risk note is updated (3.11 supported);
  - the interpret entry names interpret-core.
  Run `integration_audit.py`.
- [x] T003 [P] `configs/layers.json`: declare `data/feature_search_shapes.json` and `models/cards/*.md` in Layer E. Add writer lines to ARCHITECTURE.md.

## Phase 2: Foundational

- [x] T004 Create `estimator_candidates.py`, containing:
  - `firth_available()`, `firth_pipeline()`, `registered_pipeline(C, class_weight)`;
  - `compare_estimators(X, y, cv, n_folds, estimators)`, which returns per-repeat rows and a summary;
  - `write_rows(path, rows)`;
  - `--selftest`: separable fixture, plus a row count per repeat.

## Phase 3: US1 Firth challenger (P1)

- [x] T005 `outcome_model.py`:
  - call `compare_estimators` on the same seed;
  - write `data/outcome_model_estimators.csv`;
  - add `estimator_candidates` to the metrics;
  - add a report section;
  - add the first `--selftest`, which runs the challenger path on synthetic data.
- [x] T006 `landmark_model.py`: run the challenger on the selected window and write `data/landmark_model_estimators.csv`. Extend `selftest()`.
- [x] T007 `retrain.yml`: install firthmodels and skops, and commit the estimator files.

## Phase 4: US2 Calibration metrics (P1)

- [x] T008 `calibration_gate.py`:
  - add `width_bins`, `mass_bins`, `ece_of`, `mce_of`, `max_calibration_error` and `bins_json`;
  - widen the history header and append the new columns;
  - add the MCE proposal and the county report section.
- [x] T009 `calibration_gate.py --selftest`: calibrated vs shifted ECE, MCE >= ECE, equal-mass counts, header widening on a temp file.

## Phase 5: US3 Spatial diagnostics (P2)

- [x] T010 `county_policy_model.py`:
  - add `read_adjacency()`, `neighbor_map()`, `morans_i()` and `state_grouped_cv()`;
  - add the `spatial_diagnostics` key and report section.
- [x] T011 Extend `county_policy_model.py` `selftest()`: planted grid vs shuffled, plus state-grouped CV on synthetic data.
- [x] T012 `pipeline.yml` selftest install: add the five packages.

## Phase 6: US4 EBM challenger (P2)

- [x] T013 `configs/feature_search.json`: add an `ebm` block.
- [x] T014 `feature_search.py`:
  - add the EBM in `search_target` for the configured target;
  - write `data/feature_search_shapes.json`;
  - add the shapes file to the skip check;
  - add a report line.
- [x] T015 `feature_search.py` selftest: monotone shape check.
- [x] T016 `feature-search.yml`: install interpret-core, and commit the shapes file.

## Phase 7: US5 Model cards (P3)

- [x] T017 `calibration_gate.py`: on PROMOTE, write the skops card to `models/cards/outcome_model_<date>.md`. Run the leak check on it, and cover it in the selftest (temp dir).
- [x] T018 `.vale.ini`: add `models/cards/*.md`. `retrain.yml`: commit `models/cards/`.

## Phase 8: Polish

- [x] T019 Run each module on real data and record SC-001 to SC-004 in plan.md.
- [x] T020 Update `docs/tool_selection.md` and PHASE_STATUS.md.
- [x] T021 Merge `origin/main`. Then run `pre-commit run --all-files`, `pytest tests/test_selftests.py`, `leak_audit.py --tier blocking`, `layer_audit.py --strict --no-write` and `integration_audit.py`. Push, and open the PR.

## Dependencies

- T004 comes before T005 and T006.
- T008 comes before T017.
- T001 comes before every workflow edit.
- The US2 to US4 phases are independent of each other.
