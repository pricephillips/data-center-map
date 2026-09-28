# Feature Specification: Decision-Date Recovery for Landmark Retrain Gate

**Feature Branch**: `002-decision-date-recovery`

**Created**: 2026-09-28

**Status**: Draft

**Input**: User description: "Decision-date recovery: 46 dates are missing, and they're what keeps the landmark retrain gate closed."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Offline URL-pattern recovery populates as many decision dates as possible without fetching (Priority: P1)

A maintainer runs `python3 date_recovery.py --decision-dates` (or equivalent entry point) and the script reads `data/decision_date_worklist.csv`, finds source URLs from `master_opposition.csv` for the 48 listed projects, and parses any date-containing URLs using the existing offline pattern engine. Recovered dates are written to `data/decision_date_recovery_candidates.csv` for review before adoption — never auto-applied to `data/project_decision_dates.csv`.

**Why this priority**: The offline pass costs nothing and may recover a subset of the 48 dates without any manual work. Even recovering 5–10 converts previously excluded decided projects into landmark frame members and grows n toward the 40-project floor. The pattern engine already works for opposition-event dates; the same patterns apply to project-level source URLs.

**Independent Test**: Run `python3 date_recovery.py --selftest` and confirm the module exits 0 with all checks passing. Inspect `data/decision_date_recovery_candidates.csv` after a run to verify every candidate has a non-empty `source_url` and a parseable date.

**Acceptance Scenarios**:

1. **Given** a project in the worklist has at least one opposition event in `master_opposition.csv` whose `Source URL` matches a URL date pattern, **When** offline recovery runs, **Then** the candidate file contains a row for that project with `recovered_date`, `method`, and `source_url` populated.
2. **Given** a project's source URLs contain no parseable date pattern, **When** offline recovery runs, **Then** the project appears in the candidates file with `recovered_date` blank, `method="no_pattern_match"`, and no date imputed — the row is present so the reviewer has a complete 48-row tally.
3. **Given** the candidate file already contains a project's entry, **When** a maintainer reviews and approves it, **Then** the date is appended to `project_decision_dates.csv` with `decision_date`, `decision_date_source`, `source_url`, and `note` filled in — the manual step is explicit and reversible.
4. **Given** a candidate date is year-precision only (e.g., "2025"), **When** a maintainer reviews it, **Then** the candidate is flagged as year-only and excluded from `project_decision_dates.csv` (constitution rule: year-precision dates are excluded from the time axis rather than floored).

---

### User Story 2 - Landmark retrain gate opens at a feasible window after dates are recovered (Priority: P1)

After the recovered dates are entered into `project_decision_dates.csv` and the pipeline is run, `data/landmark_feasibility.csv` shows at least one window where `gate = FEASIBLE` (n ≥ 40, n_blocked ≥ 12, n_not_blocked ≥ 12). Until then, the landmark diagnostics report states how many more decided-project dates are needed per window.

**Why this priority**: The gate is currently INFEASIBLE at all five windows (best: n=24/18/6 at W=30). The critical constraint is `n_not_blocked ≥ 12` — currently 6. At least 6 more not-blocked decided projects need sourced decision dates to unlock the gate. Every date recovered from the worklist is a direct contribution to opening it.

**Independent Test**: After entering recovered dates, run `python3 landmark_model.py` and read `data/landmark_feasibility.csv`. Verify that n, n_blocked, and n_not_blocked have increased, and that at least one window shows FEASIBLE once the target count is reached.

**Acceptance Scenarios**:

1. **Given** 6 or more not-blocked decided projects gain sourced decision dates, **When** the landmark model runs, **Then** at least one window's `n_not_blocked` reaches 12 and `gate` shows FEASIBLE (assuming n and n_blocked also meet their floors).
2. **Given** a date is entered in `project_decision_dates.csv` without a `source_url`, **When** the pipeline runs**, Then** that date is not used in the landmark frame (constitution: decision dates require source and URL).
3. **Given** a date is entered with year-precision only, **When** the landmark model runs, **Then** that project is excluded from every window frame (constitution: year-precision dates excluded from time axis).

---

### User Story 3 - The date_recovery module ships a --selftest entry point (Priority: P2)

`python3 date_recovery.py --selftest` runs without network access, without reading any project file, and exits 0 when all checks pass and non-zero when any fails. Constitution Principle IX requires every pipeline module to carry this entry point before its outputs are relied on.

**Why this priority**: `date_recovery.py` currently has no `--selftest`. Adding one is a constitution requirement for any touched module, and this feature touches the module.

**Independent Test**: Run `python3 date_recovery.py --selftest` in an environment with no network and no data files. Verify exit code 0 and that all named checks are listed as PASS.

**Acceptance Scenarios**:

1. **Given** the selftest is run, **When** `recover_from_url()` is exercised with inline fixtures, **Then** each URL-pattern type produces the correct date and method label.
2. **Given** a URL with no date pattern, **When** `recover_from_url()` is called, **Then** the selftest confirms it returns `("", "")`.
3. **Given** the offline pattern for year-only matches a URL, **When** `recover_from_url()` is called, **Then** the returned method label is `year_only_midyear` and the candidate is flagged accordingly.
4. **Given** all selftest checks pass, **When** the selftest exits, **Then** exit code is 0; if any fail, exit code is 1.

---

### Edge Cases

- What if the same project appears in the worklist multiple times (duplicate project_id)? — The offline recovery deduplicates by project_id and keeps the best (most precise) candidate date.
- What if a source URL that contains a parseable date belongs to an opposition event, not the project's decision record? — The candidate file labels the pattern method (e.g., `iso_in_url`, `ymd_path`), and a maintainer review step is required before adoption; the method label flags that the date may not be the decision date.
- What if the worklist contains a project whose decision outcome is `blocked_confirmed` but the decision date from a URL is ambiguous (applies to a procedural vote, not the terminal decision)? — The maintainer review step is where this is resolved; the script never auto-applies.
- What happens if `project_decision_dates.csv` already has a date for a project that also appears in a recovery candidate? — The existing entry takes precedence; the candidate is skipped during adoption.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `date_recovery.py` MUST accept a `--decision-dates` flag (or equivalent CLI argument) that reads `data/decision_date_worklist.csv` and cross-references `master_opposition.csv` to collect source URLs for the listed projects.
- **FR-002**: For each project, the module MUST apply the existing offline URL-pattern engine (`recover_from_url()`) to all matching source URLs and select the most day-precise result.
- **FR-003**: The module MUST write candidates to `data/decision_date_recovery_candidates.csv` with columns: `project_id`, `project_name`, `state`, `lifecycle_outcome`, `recovered_date`, `method`, `source_url`, `year_only`. It MUST NOT write to or modify `project_decision_dates.csv` automatically.
- **FR-004**: Year-precision candidates (`method` ends in `_midyear`) MUST be flagged in the candidates file with a `year_only: true` marker so a reviewer can exclude them before adoption.
- **FR-005**: The module MUST ship a `--selftest` entry point (no network, no file I/O) that exercises `recover_from_url()` with inline fixtures covering every pattern type, and exits 0 when all pass and 1 when any fail.
- **FR-006**: `project_decision_dates.csv` MUST only be written to by the maintainer's manual adopt step, never by the automated recovery run. The adopt step validates that each row has non-empty `decision_date`, `decision_date_source`, and `source_url` before appending.
- **FR-007**: The module MUST NOT impute, infer, or estimate a decision date when no URL pattern matches; affected projects are listed in the candidates file as `no_pattern_match` with empty `recovered_date`.
- **FR-008**: The module MUST respect the constitution rule: decision dates entered into `project_decision_dates.csv` MUST have a source URL; year-precision dates are excluded from the landmark time axis.
- **FR-009**: `date_recovery.py` changes MUST be backward-compatible: the existing `apply_recovery()` function and its caller interface for opposition-event date recovery must remain unchanged.

### Key Entities

- **`decision_date_worklist.csv`**: The 48 decided-but-undated projects that are excluded from every landmark window. Produced by `landmark_model.py`. Input to the recovery pass.
- **`decision_date_recovery_candidates.csv`**: New output of the `--decision-dates` pass. Staging area for human review before adoption. Never auto-applied.
- **`project_decision_dates.csv`**: Authoritative registry of sourced decision dates. Only written by human maintainer after reviewing candidates. Currently 31 entries.
- **`landmark_feasibility.csv`**: Gate status file. Currently all-INFEASIBLE. Opens when decided projects with verified decision dates reach n≥40, n_blocked≥12, n_not_blocked≥12 at some window.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `python3 date_recovery.py --selftest` exits 0 with all named checks passing after the module is updated.
- **SC-002**: The offline recovery pass produces `data/decision_date_recovery_candidates.csv` with at least 1 candidate entry (confirming the pipeline runs end-to-end), and no year-precision candidate is flagged without a `year_only` marker.
- **SC-003**: After human adoption of reviewed candidates, `data/project_decision_dates.csv` grows from its current 31 entries, and every new entry has non-empty `decision_date_source` and `source_url`.
- **SC-004**: After the adopted dates are incorporated and `python3 landmark_model.py` is run, `data/landmark_feasibility.csv` shows at least one window closer to the feasibility floor than before — the n, n_blocked, and n_not_blocked columns increase.
- **SC-005**: The long-run target: `data/landmark_feasibility.csv` shows at least one FEASIBLE window, unblocking the landmark model retrain. (This may require multiple recovery + sourcing cycles if offline patterns cover fewer than the needed 16+ projects.)
- **SC-006**: `python3 leak_audit.py --tier blocking` returns 0 blocking hits after any code changes.

## Assumptions

- The primary bottleneck to the landmark gate is `n_not_blocked ≥ 12`: currently 6 not-blocked projects have decision dates; 6 more are needed at minimum. The worklist (48 projects) is assumed to contain enough not-blocked decided cases to close this gap once dates are recovered.
- The existing `recover_from_url()` pattern engine will match only a fraction of the 48 worklist URLs; the remainder require manual web sourcing. The spec covers the offline pass only; manual sourcing is the maintainer's next step after reviewing candidates.
- `master_opposition.csv` contains at least one row per project in the worklist (each decided-opposed project has at least one opposition event with a source URL). If a project has no rows in `master_opposition.csv`, it produces no URL candidates and is listed as `no_pattern_match`.
- No changes to `landmark_model.py` or `project_decision_dates.csv` schema are in scope. The adopt step is a maintainer action, not a script.
- `date_recovery.py` is not currently wired into CI. Adding the `--selftest` wires it in per Principle IX; the CI pipeline change is in scope.
