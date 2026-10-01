# Implementation Plan: Model Robustness and Governance

**Branch**: `008-model-robustness` (worked on `claude/epic-pascal-zdx2xy`) | **Date**: 2026-10-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-model-robustness/spec.md`, including its "Changes on main since this spec (2026-09-29)" section, and the owner's go-ahead for session 5 (2026-10-01).

## Summary

Five additions. All of them are diagnostics or challengers. None changes a
selection rule, a gate threshold, or what currently ships (Constitution V,
FR-001).

1. **Firth challenger (US1).** A new shared module, `estimator_candidates.py`,
   holds the Firth pipeline (firthmodels) and one comparison function.
   - `outcome_model.py` scores the registered estimator and the Firth
     estimator on the same `RepeatedStratifiedKFold` splits. It writes one row
     per estimator per repeat to `data/outcome_model_estimators.csv`, adds an
     `estimator_candidates` key to `data/outcome_model_metrics.json`, and adds
     a section to the report.
   - `landmark_model.py` does the same for the window its pre-registered rule
     selects, writing `data/landmark_model_estimators.csv`. While the
     landmark gate is closed (it is today), nothing is fit and the file is not
     written.
   - Neither module's registered rule has an estimator dimension. The window
     rule picks a window, and the outcome model has a single registered
     estimator. So the rule never picks Firth, and production is unchanged
     (Acceptance 2). Adopting Firth would need a new registration entry,
     which the report says in plain words (research D2).
2. **Calibration metrics (US2).** `calibration_gate.py` computes MCE, an
   equal-mass ECE and MCE, and the reliability bins in-house (research D3).
   - The new values are appended as columns to `data/calibration_history.csv`.
     An older header is widened once, in place, and existing columns and rows
     are unchanged (FR-002).
   - The gate criteria are unchanged. The report proposes an MCE ceiling for
     Price to adopt, and shows whether this run would meet it.
   - The report adds a section for the production county model, read from
     `data/county_policy_scores.csv` (cross-fitted `calibrated_score`). The
     section has ECE, MCE and a reliability table. It is report-only: no
     history row is added, so `operations_summary.py` and
     `county-profile.html` still read only outcome-model verdicts (SC-002).
3. **Spatial diagnostics (US3).** `county_policy_model.py` adds a diagnostic
   step after the selected specification is fit.
   - Moran's I is computed on the county residuals
     (`has_enacted_restrictive - calibrated_score`). It uses esda, with
     row-standardized contiguity weights built by libpysal from
     `data/county_adjacency.csv`, 999 permutations and a fixed seed.
   - Cross-validation of the selected specification and C is rerun with
     `StratifiedGroupKFold`, grouped by state, over the same number of
     repeats.
   - Both results go under a `spatial_diagnostics` key in
     `data/county_policy_metrics.json`, and into a report section beside the
     standard CV AUC (SC-003). They are never features, and they never change
     the selection.
   - If esda or libpysal is not importable, the key records
     `unavailable: <reason>`, and the module still exits 0.
4. **EBM challenger (US4).** `feature_search.py` fits an Explainable Boosting
   Machine (interpret-core) on the configured target (`restrict_profile`),
   using the same CV splits as the GBM.
   - It reports `held_out_auc.ebm_all_candidates`.
   - It refits once on the whole frame and writes per-variable shape tables to
     `data/feature_search_shapes.json`.
   - EBM output is never read by `choose_promotions()`. Promotions still come
     only from the L1 stability-selection rows, and the shapes file says so
     (SC-004).
   - The EBM runs inside `feature_search.py`, which only `feature-search.yml`
     runs (weekly). The nightly pipeline is untouched.
5. **Model cards (US5).** On a PROMOTE verdict, `calibration_gate.py` writes a
   skops card to `models/cards/outcome_model_<date>.md`.
   - The card holds the data window, frame size and positives, the
     specification, CV metrics with intervals, calibration metrics, the gate
     result, and the standing descriptive-not-causal statement.
   - It reads only model-level aggregates (Principle VI).
   - It is built with `Card(model=None, template=None)`, so no model object is
     serialized and nothing is pickled.
   - If skops is not importable, the card is skipped with a warning. The
     verdict and exit code are unchanged.

**Python 3.12 decision (spec edge case, and "Changes on main")**: stay on
Python 3.11 for every workflow (research D1).

- The spec's premise is out of date. esda 2.9.0 declares
  `Requires-Python >=3.11`, and every session 5 package resolves on 3.11
  with no change to an existing pin.
- The constraints file therefore keeps serving one interpreter.
- The Dependabot ignores for numpy >=2.5 and scipy >=1.18 stay in place. They
  come off in a separate, repo-wide 3.12 migration, which this spec does not
  need.

## Technical Context

**Language/Version**: Python 3.11 (CI 3.11.16; sandbox 3.11.15).

**Primary Dependencies**:

- New in `requirements/ci.in`, each resolved on 3.11 with no existing pin
  moved:
  - `firthmodels>=0.8,<1` (MIT), resolved at 0.8.2;
  - `esda>=2.9,<3` (BSD-3-Clause), resolved at 2.9.0;
  - `libpysal>=4.14,<5` (BSD-3-Clause), resolved at 4.14.1;
  - `interpret-core>=0.7,<0.8` (MIT), resolved at 0.7.8;
  - `skops>=0.16,<1` (MIT), resolved at 0.16.0.
- interpret-core is the glassbox subset of InterpretML. The full
  `interpret` meta-package adds dashboard and server dependencies that this
  repo does not use.
- netcal is **not** added. Its metrics module imports torch, and installing
  it pulls in torch, pyro-ppl, gpytorch and tensorboard (57 packages). The
  metrics it would supply take about 40 lines (research D3). The registry
  entry moves to `eliminated` with that reason.

**Storage**: CSV, JSON and Markdown in the repo. Model cards go to a new
`models/cards/` directory.

**Testing**:

- Every touched module extends its `--selftest`. `outcome_model.py` and
  `calibration_gate.py` gain their first selftest. The new module
  `estimator_candidates.py` ships with one.
- `tests/test_selftests.py` discovers all of them.
- The checks are synthetic and offline. Each one that needs a session 5
  package prints `SKIP` when the package is absent. `pipeline.yml`'s
  selftest step installs all five, so CI runs them for real.

**Target Platform**: GitHub Actions `ubuntu-latest`. The workflows involved
are `retrain.yml` (outcome model, gate, cards), `pipeline.yml` (county model)
and `feature-search.yml` (EBM, weekly).

**Project Type**: Script-based data pipeline: root-level modules and CI
workflows.

**Performance Goals** (measured in the sandbox):

- EBM on `restrict_profile` (3,144 counties, 42 candidates): about 2.6 s per
  fold with `interactions=0` and `outer_bags=8`. Across the registered 15
  folds plus one refit, that is under a minute.
- Moran's I with 999 permutations on 3,124 counties: under 0.1 s.
- State-grouped CV: 25 fits of one logistic specification, a few seconds.
- Firth on the outcome frame (80 x 20, 50 folds): a few seconds.

**Constraints**:

- No selection rule, gate threshold or production output changes value
  because of this spec.
- Existing `calibration_history.csv` columns keep their names and order.
- No pickle.
- Cards and shapes hold aggregates only.

**Scale/Scope**:

- One new module, `estimator_candidates.py`.
- Edits to five modules, three workflows, `configs/feature_search.json`,
  `configs/layers.json`, `configs/integrations.json`, `requirements/*`,
  `ARCHITECTURE.md`, `docs/tool_selection.md` and `.vale.ini`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| 1. `leak_audit.py --tier blocking` = 0 | **PASS (verify at end)** | New outputs are metrics, shapes and aggregate cards. Each writer runs the module's existing vocabulary check over its new files. |
| 2. `layer_audit.py` = 0 undeclared | **REQUIRES ACTION** | New outputs are declared in the table below. ARCHITECTURE.md gets writer lines. |
| 3. `--selftest` on touched and new modules | **REQUIRES ACTION** | New: `estimator_candidates.py`. First selftests: `outcome_model.py`, `calibration_gate.py`. Extended: `landmark_model.py`, `county_policy_model.py`, `feature_search.py`. |
| 4. `node --check` on touched JS | **N/A** | No JS is touched. |
| 5. No em-dashes, no CRLF | **PASS (verify at end)** | Writers use `lineterminator="\n"`. |
| 6. docx `--original` validation | **N/A** | No docx. |

New outputs and their layers:

| File | Writer | Layer |
|------|--------|-------|
| `data/outcome_model_estimators.csv` | `outcome_model.py` | E (covered by `data/outcome_model_*.csv`) |
| `data/landmark_model_estimators.csv` | `landmark_model.py` | E (covered by `data/landmark_*.csv`) |
| `data/feature_search_shapes.json` | `feature_search.py` | E (explicit entry) |
| `models/cards/*.md` | `calibration_gate.py` | E (explicit entry) |
| `data/calibration_history.csv` gains columns | `calibration_gate.py` | unchanged |
| `data/county_policy_metrics.json` gains `spatial_diagnostics` | `county_policy_model.py` | unchanged |

Principle checks:

- **I.** Every new number has its method and inputs in the report beside it.
- **II.** Generated prose is descriptive. Cards carry the standing statement,
  and `models/cards/*.md` is added to the Vale scope (FR-004).
- **V.** No new estimator, variable or EBM shape can reach production except
  through the registered rules and `calibration_gate.py`. Changes in headline
  metrics caused by the new diagnostics are expected, and are never a reason
  to revert.
- **VI.** Cards and the county calibration section read model-level
  aggregates only. No group-level outcome column is read.
- **VII.** Everything is additive: new files, new JSON keys and appended
  history columns.
- **VIII.** Each new file has exactly one writer.
- **IX.** Every touched module ships a selftest.

**Post-design re-check**: every gate resolves within scope, and there are no
unjustified violations.

**Implementation status (2026-10-01)**: T001-T021 are done.

Each touched module was run on the current data and compared byte for byte
against the pre-change module on the same inputs:

- `outcome_model_predictions.csv` is identical;
- `county_policy_scores.csv` is identical;
- every pre-existing key of `county_policy_metrics.json` is identical;
- `feature_search_ranking.csv` is identical;
- `feature_search_promotions.json` is identical apart from `inputs_hash`,
  which folds in the config hash.

Measured:

- **SC-001**: `outcome_model_estimators.csv` has 10 repeats for each of the
  two estimators.
  - Registered: AUC median 0.846 (p10 0.736, p90 0.935), 11 of 20
    coefficients sign-stable.
  - Firth: AUC 0.806 (0.675 to 0.896), 6 of 20 sign-stable.
  - The registered estimator ships. The landmark gate is closed, so its
    estimator file is not written.
- **SC-002**: outcome model ECE 0.059, MCE 0.154, equal-mass 0.059 and
  0.122. It would meet the proposed MCE ceiling of 0.30. The production
  county model: ECE 0.012 and MCE 0.173 (equal-width), 0.029 and 0.040
  (equal-mass).
- **SC-003**: Moran's I on county residuals is 0.118 (pseudo p 0.001,
  z 10.7, 20 islands). State-grouped AUC is 0.752 (p10 0.704, p90 0.799),
  beside a standard AUC of 0.758. The gap is 0.006, so the headline AUC is
  not carried by same-state information.
- **SC-004**: `feature_search_shapes.json` covers 42 variables for
  `restrict_profile`, with EBM held-out AUC 0.786 (0.750 to 0.820). The
  promotion list is unchanged (`projects_since_2024`).

The generated data files are left to the workflows. The first
`retrain.yml` run after merge widens the history header and writes the
first card. The next `pipeline.yml` and `feature-search.yml` runs write the
diagnostics and the shapes.

## Project Structure

### Documentation (this feature)

```text
specs/008-model-robustness/
├── spec.md
├── plan.md          # this file
├── research.md      # D1-D8 decisions
├── data-model.md    # new files and columns
├── quickstart.md    # how to run and verify each story
├── contracts/
│   ├── cli.md       # flags and exit codes
│   └── outputs.md   # file schemas and invariants
└── tasks.md         # /speckit-tasks output
```

### Source Code (repository root)

```text
estimator_candidates.py        NEW: Firth pipeline + per-repeat comparison
outcome_model.py               Firth challenger, first --selftest
landmark_model.py              Firth challenger on the selected window
calibration_gate.py            MCE, equal-mass ECE/MCE, bins, county section,
                               skops card, first --selftest
county_policy_model.py         Moran's I + state-grouped CV diagnostics
feature_search.py              EBM challenger + shapes file
configs/feature_search.json    "ebm" block (target, bags, interactions)
configs/layers.json            shapes file and model cards
configs/integrations.json      netcal eliminated; esda risk note updated;
                               interpret target names interpret-core
requirements/ci.in, ci.txt     five packages, in-place recompile
.github/workflows/retrain.yml  install firthmodels + skops; commit new files
.github/workflows/pipeline.yml install session 5 packages for selftests
                               and the county diagnostics
.github/workflows/feature-search.yml  install interpret-core; commit shapes
.vale.ini                      models/cards/*.md in scope
ARCHITECTURE.md, docs/tool_selection.md
```

## Complexity Tracking

No constitution violations to justify.
