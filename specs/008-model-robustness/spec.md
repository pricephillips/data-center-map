# Feature Specification: Model Robustness and Governance

**Feature Branch**: `008-model-robustness`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 5 of the tool integration plan: firthmodels, netcal, esda with libpysal, InterpretML (EBM), skops.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The rare blocked class gets a bias-reduced estimator candidate (Priority: P1)

`outcome_model.py` and `landmark_model.py` add Firth-penalized logistic regression (firthmodels) as a candidate in the existing registered specification set, evaluated under the same repeated stratified CV and sign-stability screen. It is promoted only if the pre-registered selection rule picks it and `calibration_gate.py` passes.

**Why this priority**: The decided-and-opposed frame has 26 blocked projects. Maximum-likelihood logistic regression on so few events is biased and can hit separation; Firth's penalty is the standard remedy and keeps coefficients interpretable.

**Independent Test**: On a synthetic separable fixture, standard logistic regression diverges and Firth returns finite coefficients.

**Acceptance Scenarios**:

1. **Given** the current frame, **When** the spec grid runs, **Then** Firth candidates appear in the selection table with the same metrics as the others.
2. **Given** the selection rule does not choose Firth, **When** the run completes, **Then** production is unchanged.

### User Story 2 - The calibration gate reports standard calibration metrics (Priority: P1)

`calibration_gate.py` adds expected and maximum calibration error (netcal) and reliability-diagram bins to `data/calibration_gate_report.md` and `data/calibration_history.csv`. Gate pass criteria are unchanged in this spec; any new threshold is proposed in the report for Price to adopt.

**Independent Test**: A perfectly calibrated synthetic set yields ECE near 0; a shifted set yields a larger ECE.

### User Story 3 - County scores are checked for spatial dependence (Priority: P2)

A diagnostic step computes Moran's I on county model residuals using contiguity weights built from `county_adjacency.csv`, and reruns cross-validation grouped by state. Both results go to the county metrics report. They are diagnostics, not features.

**Why this priority**: The feature-search handoff found that same-state diffusion drives most of the `restrict` target's signal. If residuals cluster spatially, standard CV overstates AUC; grouped CV gives the honest number.

**Independent Test**: A synthetic grid with planted spatial clustering yields a significant Moran's I; shuffled values do not.

### User Story 4 - A glassbox challenger produces client-readable variable shapes (Priority: P2)

`feature_search.py` adds an Explainable Boosting Machine (InterpretML) to the `restrict_profile` target beside the GBM. It reports held-out AUC and writes per-variable shape tables to `data/feature_search_shapes.json` for charting. EBM output never bypasses the promotion path.

**Independent Test**: On a synthetic target with one known monotone variable, the EBM shape for that variable is monotone.

### User Story 5 - Every promoted model ships a model card (Priority: P3)

On promotion, `calibration_gate.py` writes a skops model card to `models/cards/<model>_<date>.md`: data window, frame size and positives, specification, CV metrics with intervals, calibration metrics, gate result, and the standing descriptive-not-causal statement.

### Edge Cases

- esda needs Python 3.12: the plan either moves the county job to 3.12 or pins the newest release that supports 3.11, and records the choice.
- EBM is slower than GBM: it runs only in `feature-search.yml` (weekly), not in the nightly pipeline.
- A model card would expose group-level outcome columns: cards read only model-level aggregates (Principle VI).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: New estimators MUST enter through the existing registered specification mechanism; the pre-registered selection rule is not edited.
- **FR-002**: netcal metrics MUST be additive columns in `calibration_history.csv`; existing columns are unchanged.
- **FR-003**: Spatial diagnostics MUST write only to report files declared in `configs/layers.json`.
- **FR-004**: All client-facing language generated from these outputs MUST pass the spec 004 Vale rules (descriptive, not causal).
- **FR-005**: Every touched module MUST extend its `--selftest` to cover the new path with synthetic data.

## Success Criteria *(mandatory)*

- **SC-001**: The selection table includes Firth candidates with metrics for every CV repeat.
- **SC-002**: The calibration report shows ECE and MCE for the current production county model.
- **SC-003**: The county metrics report states Moran's I on residuals and the state-grouped CV AUC beside the standard CV AUC.
- **SC-004**: `feature_search_shapes.json` exists for `restrict_profile`, and no EBM-only variable is promoted without passing the gate.

## Assumptions

- Headline metric changes from new diagnostics are expected and never a reason to revert (Constitution V).
- Model artifacts, if persisted, use the skops format, not pickle.

## Changes on main since this spec (2026-09-29)

- **Python 3.12 now blocks dependency updates as well.** Dependabot's pip PR moved numpy to 2.5.3 and scipy to 1.18.1, which need 3.12. They were pinned back to 2.4.6 and 1.17.1, and `.github/dependabot.yml` ignores those ranges. The 3.12 decision here also decides when those ignores come off. Moving the county job alone leaves one constraints file serving two interpreters, so the plan must either split `requirements/ci.txt` by Python version or move every workflow together.
- **`acquire-geo-sources.yml` already runs 3.12.** It installs nothing from the constraints file today.
