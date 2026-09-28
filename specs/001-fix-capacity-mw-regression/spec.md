# Feature Specification: Fix capacity_mw Regression in TrackDatacenters Proposals Scraper

**Feature Branch**: `001-fix-capacity-mw-regression`

**Created**: 2026-09-28

**Status**: Draft

**Input**: User description: "Fix the capacity_mw regression in the trackdatacenters proposals scraper. On 2026-09-10 rows with capacity_mw fell from 123 to 3, which empties cost_translation_demo.csv. Restore capacity parsing without imputing values, add a selftest that fails if capacity coverage drops more than 20 percent run over run, and keep proposals.csv columns backward-compatible."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Scraper produces correct capacity_mw values on every run (Priority: P1)

A maintainer runs the nightly scrape and capacity_mw is populated for all projects the source carries it on, whether the source API uses the field name `capacityMw` or the legacy name `capacity_mw`. The produced `data/proposals.csv` contains the same or higher number of capacity-populated rows as the previous run (within the 20% tolerance), so `cost_translation_demo.csv` is never emptied by a silent field rename.

**Why this priority**: The original incident (2026-09-10) caused capacity_mw to fall from 123 to 3, which emptied cost_translation_demo.csv — the dollar-range output that depends on known MW. The scraper's `record.get(name, '')` pattern made the rename invisible to the scrape logic and reported success, so the damage was silent and persisted for four days. Restoring reliable parsing is the foundation for all downstream cost outputs.

**Independent Test**: Run `python scripts/scrape-trackdatacenters-proposals.py --selftest` and confirm that the test exercises both the current API field name (`capacityMw`) and the legacy name (`capacity_mw`), and that both produce correct values in `flatten()` output.

**Acceptance Scenarios**:

1. **Given** the source API returns `capacityMw: 300` for a project, **When** `flatten()` processes the record, **Then** the resulting row has `capacity_mw` set to `300` (not empty).
2. **Given** the source API returns the legacy `capacity_mw: 300`, **When** `flatten()` processes the record, **Then** the resulting row has `capacity_mw` set to `300` (backward-compatible).
3. **Given** the source API returns neither name for a project, **When** `flatten()` processes the record, **Then** `capacity_mw` is empty (no imputation).
4. **Given** the source returns `capacityMw: 0` for a project, **When** `flatten()` processes the record, **Then** `capacity_mw` is `0` (zero is a value, not an absence).

---

### User Story 2 - Selftest catches a run-over-run capacity coverage drop of more than 20% (Priority: P1)

A developer adds a `--selftest` check that compares capacity coverage (rows with a non-empty `capacity_mw`) from the previous run to the current run, and exits non-zero if coverage falls by more than 20 percentage points. This selftest runs in CI without network access and without touching any file on disk.

**Why this priority**: The existing field-population guard fires at 50% loss on any watched field. Capacity has a known sensitivity — 20% loss is the threshold at which cost_translation_demo.csv starts dropping projects — and the tighter guard should be expressed as a named selftest rather than a global threshold change, so it is visible, documented, and independently runnable. Constitution Principle IX requires every pipeline module to ship a `--selftest` entry point wired into the blocking CI step.

**Independent Test**: Run `python scripts/scrape-trackdatacenters-proposals.py --selftest` and verify that a new named check passes when coverage holds and fails when capacity-populated rows drop by more than 20% relative to the prior run.

**Acceptance Scenarios**:

1. **Given** previous run had 100 capacity-populated rows out of 100 total, **When** current run has 81 or more capacity-populated rows, **Then** the selftest check passes (≤19% drop).
2. **Given** previous run had 100 capacity-populated rows out of 100 total, **When** current run has 79 or fewer capacity-populated rows, **Then** the selftest check fails (>20% drop).
3. **Given** previous run had fewer than 20 capacity-populated rows (sparse), **When** the selftest runs, **Then** the check is skipped (too sparse to be meaningful), consistent with existing `FIELD_LOSS_MIN_PRIOR` logic.
4. **Given** no previous run file exists, **When** the selftest runs, **Then** the capacity coverage check is skipped (no baseline to compare against).
5. **Given** the selftest is run, **When** all checks pass, **Then** exit code is 0; when any check fails, exit code is 1.

---

### User Story 3 - proposals.csv column layout stays backward-compatible (Priority: P2)

Any consumer reading `data/proposals.csv` (dashboards, downstream scripts, `cost_translation.py`) continues to see `capacity_mw` at its existing column position with no column renames, removals, or type changes. The fix adds no new columns and does not reorder existing ones.

**Why this priority**: Constitution Principle VII prohibits breaking existing consumers. The `capacity_mw` column is read directly by `cost_translation.py` and by any dashboard query joining on that name. A rename or removal would silently break the cost layer even if parsing were restored.

**Independent Test**: Confirm `CSV_FIELDS` in the scraper is unchanged and that `data/proposals.csv` written after the fix has `capacity_mw` at the same column index as before the incident.

**Acceptance Scenarios**:

1. **Given** a consumer reads `data/proposals.csv` using the column name `capacity_mw`, **When** the scraper has run with the fix applied, **Then** the column is present, non-empty for all rows the source carries capacity on, and carries no new columns inserted before it.
2. **Given** the scraper is run, **When** `data/proposals_added.csv` contains manual rows with `capacity_mw` values, **Then** those manual values are preserved unchanged in the output.

---

### Edge Cases

- What happens when the source sends `capacityMw: null`? — The field should be treated as absent (no imputation). The `pick()` helper must treat `None` as absent.
- What happens when the source sends `capacityMw: "300"` (string) vs `capacityMw: 300` (integer)? — Both are valid; `pick()` must accept either without coercing.
- What if a future source rename changes `capacityMw` to yet another name? — The existing field-population guard will stop the scrape when coverage collapses, and the field audit will surface the new unmapped key. The 20% selftest is a development-time check; the run-time guard is the enforcement path.
- What if cost_translation_demo.csv is empty because projects lack verified decision dates, not because of missing capacity? — The fix must not impute capacity to inflate the demo output; an empty demo is the correct result when no eligible projects carry both a decision date and a known MW.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `flatten()` MUST read `capacity_mw` by trying `capacityMw` first, then `capacity_mw` (the legacy name), using the existing `SOURCE_KEYS` aliasing pattern already applied to `size_acres`, `date`, and `lastUpdated`.
- **FR-002**: `flatten()` MUST NOT impute, estimate, or derive a `capacity_mw` value when neither API key is present; the cell is left empty.
- **FR-003**: The scraper MUST treat `capacityMw: 0` (or `capacity_mw: 0`) as a populated value (zero is a value, not an absence), consistent with the `_present()` semantics already in the codebase.
- **FR-004**: The `--selftest` entry point MUST include a named check that fails when capacity coverage (fraction of rows with non-empty `capacity_mw`) drops more than 20 percentage points run over run.
- **FR-005**: The capacity coverage selftest MUST skip the check when the prior run had fewer than `FIELD_LOSS_MIN_PRIOR` (20) capacity-populated rows, consistent with the existing sparsity rule.
- **FR-006**: The capacity coverage selftest MUST skip the check when no prior run file exists (no baseline).
- **FR-007**: `CSV_FIELDS` in the scraper MUST remain unchanged; no columns may be added, removed, or reordered.
- **FR-008**: The fix MUST leave `data/proposals_added.csv` manual rows untouched in the output.
- **FR-009**: The scraper's `--selftest` exit code MUST be 0 when all checks pass and 1 (or non-zero) when any check fails.

### Key Entities

- **`proposals.csv`**: The scraper's primary output. Unit of analysis is one data-center proposal. Key field `capacity_mw` (string in CSV, numeric in API response) drives the cost-translation layer.
- **`cost_translation_demo.csv`**: Secondary output of `cost_translation.py`. Populated only for projects that carry both a verified decision date and a known `capacity_mw`. Goes empty when `capacity_mw` disappears from `proposals.csv`.
- **`SOURCE_KEYS`**: The dict in the scraper mapping each output column name to an ordered tuple of API key names to try. Already covers `size_acres`, `date`, and `lastUpdated`; `capacity_mw` must be confirmed present.
- **`FIELD_LOSS_MIN_PRIOR` / `FIELD_LOSS_RATIO`**: Global guard thresholds (20 rows minimum, 50% drop). The new 20%-capacity selftest is an additional, named check — it does not change these constants.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After the fix, `python scripts/scrape-trackdatacenters-proposals.py --selftest` exits 0 with all checks passing, including the new capacity-coverage check.
- **SC-002**: `data/proposals.csv` produced by the fixed scraper contains at least as many capacity-populated rows as the pre-regression run (≥123), assuming the source still carries those values.
- **SC-003**: `data/cost_translation_demo.csv` is non-empty after a fresh run of `cost_translation.py` against the fixed `proposals.csv`, provided projects with both decision dates and known MW exist.
- **SC-004**: A simulated run-over-run drop of exactly 20% in capacity coverage causes the new selftest check to pass; a drop of 20.01% causes it to fail — the boundary is precise.
- **SC-005**: `CSV_FIELDS` in the scraper is byte-for-byte identical before and after the fix (verified by diff).
- **SC-006**: `python leak_audit.py --tier blocking` returns 0 blocking hits after the change.

## Assumptions

- The source API at trackdatacenters.com currently sends `capacityMw` (camelCase) as the field name for capacity; the legacy snake_case name `capacity_mw` may return in future rollbacks and must continue to work.
- `cost_translation.py` reads `capacity_mw` directly from `data/proposals.csv` by column name and does not alias or coerce it; no change to `cost_translation.py` is in scope unless the spec is extended.
- The 20% threshold for the capacity-coverage selftest is a development-time safety net, not a run-time abort; the runtime enforcement path remains the existing `assert_field_population()` guard at the 50% threshold.
- `data/proposals_manual_overlay.csv` and `data/proposals_added.csv` are not in scope; their rows pass through unchanged.
- No new columns will be added to `proposals.csv` as part of this fix; the column set and order defined in `CSV_FIELDS` is the contract.
- The `--selftest` check for capacity coverage does NOT require network access and does NOT read from disk; it exercises the logic with inline fixtures, consistent with the existing selftest pattern.
