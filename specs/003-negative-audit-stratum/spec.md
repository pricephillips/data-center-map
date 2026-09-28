# Feature Specification: Coding the Random Stratum of the Verified-Negative Audit

**Feature Branch**: `003-negative-audit-stratum`

**Created**: 2026-09-28

**Status**: Draft

**Input**: User description: "Coding the random stratum of the verified-negative audit."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Researcher works through the next uncoded batch using a printed coding queue (Priority: P1)

A researcher runs `python3 negative_audit.py --next N` and receives a formatted list of the next N uncoded rows from the random stratum (the seeded-shuffle portion after the 22 blocked_confirmed rows), each printed with its pre-generated search protocol. The researcher works top-down, enters codings into `data/negative_audit_codings.csv`, then re-runs to see progress and the next batch. No row is shown twice once coded.

**Why this priority**: The worklist has 181 uncoded rows (10 coded of 191 total). The random stratum is the worklist rows after the 22 blocked_confirmed rows — 169 rows. Working top-down through the seeded-shuffle preserves the statistical property that any partial batch is a random subset. Without a `--next` command, a researcher must manually scan the worklist to find uncoded rows, which risks skipping rows or coding out of order. Ordering matters: blocking mixes batches that include blocked_confirmed rows; the random stratum must be worked separately.

**Independent Test**: Run `python3 negative_audit.py --next 5` with the current codings in place. Verify that exactly 5 uncoded rows from the random stratum are printed, in worklist order, each with a `search_protocol` column value. Re-run; same 5 rows appear until one is coded.

**Acceptance Scenarios**:

1. **Given** 10 rows are already coded, **When** `--next 10` is run, **Then** the output lists the next 10 uncoded rows from the random stratum (not from the blocked_confirmed section) in ascending `audit_order`.
2. **Given** a row is coded and `negative_audit_codings.csv` is updated, **When** `--next 10` is run again, **Then** the coded row no longer appears; the next uncoded row takes its place.
3. **Given** the random stratum is fully coded, **When** `--next N` is run, **Then** the output says the random stratum is complete and prints the count of uncoded blocked_confirmed rows remaining.
4. **Given** `--next` is run with N=0 or without N, **Then** a usage error is printed and exit code is 1.

---

### User Story 2 - A coverage gate blocks emergence-rate publication until the random stratum reaches a configurable completion threshold (Priority: P1)

`negative_audit.py` tracks the fraction of random-stratum rows coded and refuses to print an emergence rate (verified_opposition / (verified_opposition + verified_none)) in the report until that fraction exceeds a stated threshold. Below threshold, the report states the interim descriptive count and the rows remaining. This prevents a partial-batch rate from being cited as a frame-level rate before coding is complete.

**Why this priority**: The audit docstring warns: "No emergence model trains until coverage of the frame is complete; partial-coverage rates are interim descriptives only." With 10/191 coded (5%), publishing any rate now would be misleading. The gate makes the prohibition machine-enforceable rather than documentation-only.

**Independent Test**: Run `python3 negative_audit.py` with the current 10 codings and verify that `data/negative_audit_report.md` contains the word "interim" near the coding-mix table, and does not contain a line matching the pattern `emergence rate:` or similar.

**Acceptance Scenarios**:

1. **Given** fewer than 80% of random-stratum rows are coded, **When** the report is generated, **Then** the report prints the coding mix as "interim descriptives" and explicitly omits the emergence rate.
2. **Given** exactly 80% or more of the random stratum is coded, **When** the report is generated, **Then** the report includes the emergence rate with its n and the undeterminable count alongside it.
3. **Given** the blocked_confirmed rows are all coded but the random stratum is below threshold, **When** the report is generated, **Then** the emergence rate is still withheld — the rate uses the full frame, not the purposive cell alone.

---

### User Story 3 - negative_audit.py ships a --selftest entry point (Priority: P2)

`python3 negative_audit.py --selftest` runs without network access, without reading any project data file, and exits 0 when all checks pass. Constitution Principle IX requires every pipeline module to carry this entry point. The selftest exercises coding validation, the `--next` queue logic, and the coverage-gate logic with inline fixtures.

**Why this priority**: `negative_audit.py` currently has no `--selftest`. Adding one is required before any new outputs are relied on.

**Independent Test**: Run `python3 negative_audit.py --selftest` in an environment with no data files. Verify exit code 0 and all named checks listed as PASS.

**Acceptance Scenarios**:

1. **Given** a coding row without `coded_by`, **When** `validate_codings()` is called in the selftest, **Then** the check confirms the row is rejected.
2. **Given** a `verified_none` coding without `detectability_url`, **When** `validate_codings()` is called, **Then** the check confirms the row is rejected.
3. **Given** a worklist fixture with 22 blocked_confirmed rows and 10 random-stratum rows, **When** `--next 3` logic runs, **Then** only random-stratum rows are returned in order.
4. **Given** 79% of random-stratum rows are coded, **When** the coverage-gate logic runs, **Then** the check confirms the emergence rate is suppressed; at 80% it is permitted.

---

### Edge Cases

- What if a researcher codes a blocked_confirmed row out of order (mixed into a random batch)? — The `--next` flag never includes blocked_confirmed rows; coding them separately is valid but they are always excluded from emergence-rate calculations.
- What if `negative_audit_codings.csv` contains a duplicate universe_id (researcher re-coded a row)? — The existing logic (most recent row per id takes precedence) handles this; the selftest confirms it.
- What if the random stratum is fully coded but the report still says "interim"? — This would be a bug; the selftest's coverage-gate check at 100% must confirm the emergence rate is printed.
- What if a `verified_opposition` coding appears for a row that is in the random stratum (expected to be opposed-but-unrecorded)? — This is a valid result; the coding mix and emergence rate account for it. No row is excluded post-hoc based on outcome.
- What if the leak-audit regex fires on the report output? — The current implementation already runs an inline leak audit; adding `--next` output must also pass it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `negative_audit.py` MUST accept a `--next N` CLI argument that prints the next N uncoded rows from the random stratum (rows with `audit_order` greater than the last blocked_confirmed row) in ascending `audit_order`, with each row's `name`, `state`, `county`, `lifecycle_outcome`, and `search_protocol` displayed.
- **FR-002**: `--next` MUST read `data/negative_audit_codings.csv` to determine which rows are already coded and exclude them from the output. If the codings file does not exist, all random-stratum rows are uncoded.
- **FR-003**: `--next` MUST NOT print blocked_confirmed rows; those remain a purposive cell worked separately.
- **FR-004**: The report generator MUST compute the fraction of random-stratum rows coded and suppress the emergence rate when that fraction is below a declared threshold (80% by default, labeled in the report).
- **FR-005**: When the emergence rate is suppressed, the report MUST explicitly label the coding-mix table as "interim descriptives" and state the number of random-stratum rows remaining.
- **FR-006**: When the emergence rate is printed (threshold met), it MUST appear alongside the undeterminable count in the same sentence, per the existing docstring rule.
- **FR-007**: `negative_audit.py` MUST ship a `--selftest` entry point (no network, no file I/O) that exercises: coding validation, `--next` queue logic, coverage-gate threshold, and leak-audit pass. Exit 0 on all pass, 1 on any fail.
- **FR-008**: The `--next` output MUST pass the existing leak-audit regex (no scorekeeping vocabulary).
- **FR-009**: `data/negative_audit_worklist.csv` and `data/negative_audit_codings.csv` schemas MUST remain unchanged; the feature adds only behavior, not new columns.

### Key Entities

- **Random stratum**: The set of worklist rows with `audit_order` greater than the last `blocked_confirmed` row. These are in seeded-shuffle order (RANDOM_STATE=20260723); any top-down batch is statistically a random subset of the remaining frame.
- **Purposive cell**: The 22 `blocked_confirmed` rows sorted to the top of the worklist. Their coding results must not be extrapolated to the frame; they are always reported separately.
- **Emergence rate**: `verified_opposition / (verified_opposition + verified_none)`. Only published when the random stratum is ≥80% coded. Requires the undeterminable count alongside it in every statement.
- **Coverage gate threshold**: 80% of random-stratum rows coded. Declared as a named constant in the module; not hardcoded inline.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `python3 negative_audit.py --selftest` exits 0 with all named checks passing.
- **SC-002**: `python3 negative_audit.py --next 10` prints exactly 10 uncoded random-stratum rows with name, state, and search protocol, in worklist order, with exit code 0.
- **SC-003**: After the 10 currently coded rows, the next `--next 10` call returns the rows starting at `audit_order` 33 (the first uncoded row after the 22 blocked_confirmed rows + 10 already coded) — or the correct position given the current coding state.
- **SC-004**: `data/negative_audit_report.md` generated with current codings (10/191) does not contain an emergence rate and explicitly labels the coding-mix table as interim.
- **SC-005**: `python3 leak_audit.py --tier blocking` returns 0 blocking hits after the changes.
- **SC-006**: Long-run: when ≥80% of the 169 random-stratum rows are coded (≥136 rows), the report begins printing the emergence rate with the undeterminable count alongside it.

## Assumptions

- The 22 blocked_confirmed rows are all in the first 22 positions of the worklist (already verified by the seeded sort); the random stratum starts at `audit_order` 23.
- The current 10 codings may include blocked_confirmed rows; `--next` logic correctly identifies stratum membership by `lifecycle_outcome` from the worklist, not from the codings file.
- The 80% coverage threshold is a design choice matching the docstring's intent ("coverage of the frame is complete before emergence model trains"). This can be adjusted via a named constant without changing the spec.
- No new columns are added to `negative_audit_worklist.csv` or `negative_audit_codings.csv`; the worklist already includes `lifecycle_outcome` and `search_protocol`.
- This spec does not cover the actual coding work (researching and entering results for 169 rows). The tooling enables the work; the research is a separate maintainer task.
- `negative_audit.py` is not currently wired into CI. Adding `--selftest` wires it in per Principle IX; the CI pipeline change is in scope.
