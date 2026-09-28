# Feature Specification: Data Quality Schemas and Regression Gates

**Feature Branch**: `005-data-quality-schemas`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 2 of the tool integration plan: Pandera, plus three in-house checks chosen over external packages (state normalizer, column coverage delta, out-of-fold label disagreement), and the MIT Election Data and Science Lab county returns as the replacement source for the political variables.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The clean feed has a declared, enforced schema (Priority: P1)

`qc/schemas.py` declares Pandera schemas for `master_opposition_clean.csv` and `county_policy_scores.csv`. Checks include: `outcome_defensible` in the ladder; `State` in the normalized set; `fips` five digits and present in the county frame; `lat`/`lon` inside U.S. bounds and not 0,0; every `is_*` column coercible by the standing rule `str(v).lower() in ('true','1')`; `calibrated_score` in [0,1]. The QC gate runs it report-only for seven consecutive clean runs, then as blocking.

**Why this priority**: The known defects (mixed `State` values, a stray `US`, boolean coercion, retired Connecticut FIPS) were each found by hand after they reached a deliverable.

**Independent Test**: Run the schema against a fixture with `State = "US"`, a four-digit FIPS, and an out-of-ladder outcome; confirm three named failures.

**Acceptance Scenarios**:

1. **Given** the current clean feed, **When** the schema runs, **Then** every failure is written to `qc/schema_report.md` with row ids, and the run does not block during the report-only window.
2. **Given** the report-only window has passed with zero failures, **When** a failure appears, **Then** the QC gate fails.

### User Story 2 - State values are normalized once, at the adapter (Priority: P1)

An in-house `normalize_state()` in `schema_adapter.py` maps full names, abbreviations, and common variants to two-letter codes, and routes `US` and unknown values to a review list rather than guessing.

**Why this priority**: Principle VI requires a normalized `State` field for any state-count statistic; today each consumer normalizes separately or not at all.

**Independent Test**: `schema_adapter.py --selftest` covers "Virginia", "VA", "va.", "Commonwealth of Virginia", and "US".

**Acceptance Scenarios**:

1. **Given** "Kentucky" on a row whose coordinates fall in Arkansas (prj_61), **When** normalization runs, **Then** the value normalizes to KY and the existing geographic gate still flags the mismatch; normalization never overrides it.

### User Story 3 - A column's coverage cannot silently collapse (Priority: P1)

`qc/coverage_delta.py` compares non-null counts per column against the last snapshot in `snapshots/manifest.csv` and fails when a declared column drops by more than its threshold (default 20 percent). It also flags values beyond a robust z-score of 6 on `Megawatts`, `Investment Million USD`, and `Acreage` for review.

**Why this priority**: The 2026-09-10 scraper regression took `capacity_mw` from 123 rows to 3 and emptied the cost-translation demo without failing any check.

**Independent Test**: Feed a fixture pair where one column goes from 123 non-null to 3; confirm failure naming the column and both counts.

### User Story 4 - County labels that strongly disagree with the model are surfaced (Priority: P2)

`label_disagreement_audit.py` reads the cross-fit out-of-fold probabilities already produced by `county_policy_model.py` and lists counties labeled positive with a probability in the bottom decile, and counties labeled negative in the top 1 percent, each with the evidence rows that produced the label. The output is a review worklist only.

**Why this priority**: The label polarity defect (28 false-positive counties) was a labeling error the model's own probabilities could have pointed to. cleanlab does this, but it is AGPL; the in-house version is about 100 lines at this scale.

**Independent Test**: The selftest injects a flipped label into a synthetic frame and confirms it ranks first.

### User Story 5 - Political variables come from an authoritative source (Priority: P2)

`fetch_county_features.py` gains a `political` source that reads the MIT Election Data and Science Lab County Presidential Election Returns 2000-2024 (Harvard Dataverse) and writes `data/features/political.csv` (two-party share and turnout for 2016, 2020 and 2024, plus the change between cycles). A parity report compares it with `county_votes.json` county by county. The choropleth and model switch sources only after parity is reviewed; `county_votes.json` stays in place until then.

**Why this priority**: The 2026-09-28 notices audit found `county_votes.json` was scraped from Townhall.com (2016) and Fox News (2024) and is described by its compiler as not authoritative. Political alignment appears in client site reports, so its source must hold up.

**Independent Test**: The parser's selftest reads a five-county fixture in the MEDSL long format and produces the expected shares, including the Connecticut planning-region and Alaska district handling.

### Edge Cases

- A schema check fails on a legitimate new category: categories are declared in `configs/`, not hard-coded, so adding one is a config edit.
- A snapshot is missing: coverage delta compares against the most recent available snapshot and records which one.
- Out-of-fold probabilities are missing after a retrain: the audit skips with a message and does not fail CI.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Pandera MUST be pinned per `configs/integrations.json` and imported only in `qc/schemas.py`.
- **FR-002**: The schema MUST read the outcome ladder and state set from existing sources (README ladder, `schema_adapter.py`), not duplicate them.
- **FR-003**: Report-only versus blocking mode MUST be a single config flag with the switch date recorded.
- **FR-004**: `coverage_delta.py` thresholds MUST be declared per column in a config file.
- **FR-005**: New output files (`qc/schema_report.md`, `data/coverage_delta_report.md`, `data/label_disagreement_worklist.csv`) MUST be declared in `configs/layers.json`.
- **FR-006**: Every new module MUST ship `--selftest` and be picked up by the spec 004 pytest runner.
- **FR-007**: No check may edit `master_opposition.csv` or any label; all outputs are reports or worklists (Principle VII).
- **FR-008**: The political source MUST record the Dataverse DOI, file version and license terms in `data/features/features_manifest.json`.

## Success Criteria *(mandatory)*

- **SC-001**: The schema run against current `main` produces a finite, reviewed failure list, and each failure is either fixed at source or declared as an allowed exception.
- **SC-002**: A replay of the 2026-09-10 snapshot pair through `coverage_delta.py` fails on `capacity_mw`.
- **SC-003**: A replay of the pre-2026-08-21 county labels through the disagreement audit ranks at least half of the 28 later-removed counties in its worklist.
- **SC-004**: Leak audit and layer audit stay at 0.

## Assumptions

- Out-of-fold probabilities from `county_policy_model.py` are available or can be written as a new declared artifact without changing the model.
- Seven clean runs is the default report-only window; Price can change it.
- SC-003 depends on reconstructing the pre-2026-08-21 label set from git history; if it cannot be rebuilt, SC-003 becomes a synthetic flipped-label test only.
