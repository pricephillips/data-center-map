# Tasks: Data Quality Schemas and Regression Gates

**Status (2026-09-29)**: all tasks are done. The workflow half ships as
`docs/pending_data_quality.patch` for Price to apply. Where implementation
differed from the plan, or found something the plan did not know:

- **T009, State backfill**: 3 of the 9 full-name State values were wrong
  states, not just the wrong form. "Washington County" (OH), "Delaware County"
  (PA) and "Port Washington" (WI) had matched a state name inside a place
  name. `extract_state()` now skips those matches, so the 3 rows go blank and
  join harvest triage, and the other 6 become codes. A scratch rebuild changes
  exactly those 9 feed rows, and the QC gate's quarantine is unchanged
  (research D6).
- **T013/T018, proposals schema**: found 7 North Carolina proposals the source
  publishes at 0,0 with `locationConfidence = exact`. They are declared by
  permanent `pk`, not id, because the source renumbers. Exception rules now
  accept a list of checks and a list of values.
- **T020, coverage delta**: a declared column that disappears **fails**
  instead of warning. The real 2026-09-10 replay fails on 7 columns, not only
  `capacity_mw`: `size_acres` 224 to 7, `date` 316 to 10, `lastUpdated` 331 to
  12, `yearOpened` 11 to 0, `bringingOwnEnergy` and `moratoriumExempt` 326 to 0.
- **T027, SC-003**: replay recall is **0 of 28**, so SC-003 is not met
  (research D11). The rule was not retuned to the 28.
- **T005**: `outcome_defensibility.py` had no selftest. It now has one: grade
  closure in both directions.
- **T003**: superseded. While this branch was open, `main` fixed the same
  blocking leak-audit hit (EIA utility "Lost River Electric Coop Inc") in
  `4c3eebb` by adding `utilities` to `INHERITED_FIELDS`. On rebase, this
  branch's file-plus-column exemption was dropped as redundant, and
  `leak_audit.py` is unchanged from `main`.
- **Counts**: 86 selftests discovered (81 before).

**Input**: Design documents from `specs/005-data-quality-schemas/`

**Prerequisites**: plan.md, spec.md (including "Changes on main"), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Each new or touched module ships a `--selftest` (Principle IX, FR-006). `tests/test_selftests.py` discovers them. The selftest cases are part of each implementation task, not separate tasks.

**Organization**: Tasks are grouped by user story. Every change under `.github/workflows/` is made in a scratch copy and exported to `docs/pending_data_quality.patch` (research D1). No task commits a workflow file.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 to US5 from spec.md

---

## Phase 1: Setup

**Purpose**: pin the dependency and fix the pre-existing gate failure.

- [X] T001 Add `pandera==0.33.1` to `requirements/ci.in`, and add the pin with its resolved dependencies to `requirements/ci.txt`. Use `uv pip compile requirements/ci.in --python-version 3.11 -o requirements/ci.txt`, keeping the existing pins unchanged (research D2).
- [X] T002 [P] Narrow the `pandera` entry's `target` in `configs/integrations.json` to `["qc/schemas.py"]` (FR-001).
- [X] T003 [P] Add the file-plus-column exemption `("data/county_grid_territory.csv", "utilities")` to `leak_audit.py`, with a comment that these are EIA utility proper nouns ("Lost River Electric Coop Inc") in a Layer D reference file. Confirm that `python leak_audit.py --tier blocking` returns 0 (Constitution gate 1).

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the shared config and the single-source constants that several stories read.

- [X] T004 Create `configs/data_quality.json` per `contracts/data-quality-config.md`:
  - `schema.mode` is `"window"`, `window_clean_runs` is 7, and `switch_date` is null.
  - `proposals_columns` equals the current 32-column header of `data/proposals.csv`.
  - Two `allowed_exceptions` rules: US + federal, and blank State + `signal_harvest_auto`.
  - A `coverage_delta` block. The default is 0.20, with `master_opposition_clean.csv {"*": 0.20}` and `data/proposals.csv {"*": 0.20, "capacity_mw": 0.20}`. `robust_z` has threshold 6 on `Megawatts`, `Investment Million USD` and `Acreage`.
  - A `label_disagreement` block with 0.10, 0.99 and 5.
- [X] T005 [P] Add `OUTCOME_GRADES` to `outcome_defensibility.py`: `advanced_confirmed`, `restricted_conditional`, `blocked_confirmed`, `pending`, `blocked_unverified`, `advanced_unverified`, `mixed`. This is additive. Add a selftest check that every grade literal the module assigns is in `OUTCOME_GRADES`. Check how the module currently runs selftests, and add a `--selftest` entry if none exists (research D4).
- [X] T006 Declare the new outputs in `configs/layers.json`:
  - Layer E: `qc/schema_report.md`, `data/schema_run_history.csv`, `data/coverage_delta_report.md`, `data/label_disagreement_worklist.csv`.
  - Layer D: `data/features/*.md`.

  Add the writer lines to the Layer D and E rows of `ARCHITECTURE.md`. Regenerate `.gitattributes` with `python layer_audit.py --write-gitattributes` after the writers exist (see T030).

**Checkpoint**: the config and constants exist, and the stories can proceed.

---

## Phase 3: User Story 2, State normalizer (P1)

US2 goes first because US1's schema depends on it.

**Goal**: one `normalize_state()` at the adapter. It never guesses, and it routes `US` and unknown values to review.

**Independent Test**: `python qc/schema_adapter.py --selftest`.

- [X] T007 [US2] Add `STATE_VARIANTS`, `NOT_A_STATE` and `normalize_state(value, review=None) -> str` to `qc/schema_adapter.py`, per data-model.md section 2 and research D5:
  - Accept two-letter codes in any case, with a trailing period allowed.
  - Accept full names, and the prefixes `Commonwealth of`, `State of` and `District of Columbia`.
  - Accept AP abbreviations.
  - Return `""` for `NOT_A_STATE` (review reason `not_a_state`) and for unknown values (reason `unknown`).

  Leave `normalize_record()` unchanged.
- [X] T008 [US2] Add a `--selftest` entry point to `qc/schema_adapter.py`, keeping the demo behavior when a CSV path is given. Cases:
  - "Virginia", "VA", "va.", "Commonwealth of Virginia" and "Va." map to VA.
  - "US" maps to "" with a review entry `not_a_state`. "Freedonia" maps to "" with `unknown`. A blank value maps to "" with no review entry.
  - "District of Columbia" maps to DC.
  - "Kentucky" maps to KY, and `state_bounds.in_state(34.0537, -93.1059, "KY") is False` (prj_61, now prj_78). Normalization never overrides the geographic gate.
  - `normalize_record` still expands "WA" to "Washington".
- [X] T009 [US2] In `clean_opposition_data.py` step 6b (geography backfill), pass the `_A.extract_state(src)` result through `_A.normalize_state()`, so the feed gets two-letter codes. Fall back to the old behavior if the adapter lacks the function. Add a selftest case asserting that a blank-State row with "... - Texas" in the headline backfills to `TX` (research D6, the fix at source for the 9 full-name rows).

**Checkpoint**: the normalizer is usable by US1 and by every consumer.

---

## Phase 4: User Story 1, Schemas (P1) (MVP)

**Goal**: declared, enforced schemas with a report-only window and then blocking.

**Independent Test**: `python qc/schemas.py --selftest`. The fixture with `State = "US"`, a four-digit FIPS and an out-of-ladder outcome gives exactly three named failures.

- [X] T010 [US1] Create `qc/schemas.py` as the only Pandera import (FR-001). Guard the import so `--selftest` prints `SKIP (pandera not installed)` and exits 0 when Pandera is absent (plan, Complexity Tracking). Load `configs/data_quality.json` with built-in defaults. Import `OUTCOME_GRADES` and `STATE_ABBREV`/`normalize_state`, never copying them (FR-002).
- [X] T011 [US1] In `qc/schemas.py`, build the `clean_feed` schema per data-model.md 1a:
  - `outcome_grade`: in `OUTCOME_GRADES`, not null.
  - `state_normalized`: in the `STATE_ABBREV` keys, not null.
  - `lat_bounds`: "null, or 17 <= lat <= 72".
  - `lon_bounds`: "null, or -180 <= lon <= -64, or 172 <= lon <= 180".
  - `not_null_island`, `coords_paired`.
  - `bool_token`: lower-cased value in `{true, false, 1, 0}` for every `is_*` column.
  - `fips_frame`: only when a `fips` column exists.

  Row id is `r<line>` plus `row_key` = sha1(`Source URL|Incident|Date|State`)[:10], plus `project_id`.
- [X] T012 [US1] In `qc/schemas.py`, build the `county_scores` schema per data-model.md 1b:
  - `fips_format` `^\d{5}$`, `fips_frame` against `data/county_census_features.csv`, `fips_unique`.
  - `score_unit`: `calibrated_score` and `raw_oof_score` in [0, 1], not null.
  - `decile_range` 1..10, `label_binary` {0, 1}.

  Row id is fips.
- [X] T013 [US1] In `qc/schemas.py`, build the `proposals` Layer B schema per data-model.md 1c:
  - `columns_present` from config `proposals_columns`.
  - `id_unique`.
  - `id_keyed`: resolves to an `active` row of `data/project_key_map.csv` by `current_id`.
  - `state_normalizable`.
  - The coordinate checks.
  - `capacity_nonneg`.

  Row id is the `pk` from the key map, with `id` alongside ("Changes on main").
- [X] T014 [US1] In `qc/schemas.py`, implement validation with `lazy=True`. Map Pandera `failure_cases` (index) to failure records `{schema, check, column, row_id, row_key, value, allowed}`, and apply `allowed_exceptions` rules (every `when` pair matches exactly).
- [X] T015 [US1] In `qc/schemas.py`, implement the run history and mode logic per data-model.md "Run history row" and "Mode state transitions":
  - Columns: `run_utc, input_sha256, mode_config, mode_effective, failures, allowed, clean, consecutive_clean, switch_date`.
  - Do not append when `input_sha256` equals the last row's.
  - Window mode turns blocking when the previous `consecutive_clean >= window_clean_runs`, and a sticky `switch_date` keeps it blocking.
  - Exit codes per contracts/cli.md: 0, 1 when effective blocking with failures, 2 for missing input.
- [X] T016 [US1] In `qc/schemas.py`, write `qc/schema_report.md` per contracts/outputs.md: summary table, failures (first 50 per check, full count), allowed exceptions, and an Information line with the exact duplicate count of `master_opposition.csv`. That line carries no check (it is a decision for Price). Use LF only and no em-dash. Support `--no-write` and `--only`.
- [X] T017 [US1] Add a `--selftest` to `qc/schemas.py`:
  - A 3-row fixture with `State="US"` (no federal Scope), a four-digit fips and outcome `"won"` gives exactly `state_normalized`, `fips_format` and `outcome_grade`.
  - A federal `US` row is allowed.
  - Window: 7 clean runs, then a failing run, is effective blocking with exit 1. Report-only never blocks.
  - An identical sha does not append.
  - The report has no em-dash and no CR.
  - All files live in a temp dir.
- [X] T018 [US1] Run `python qc/schemas.py` on current data. Record the failure list in `qc/schema_report.md` and the first history row in `data/schema_run_history.csv` (SC-001). Confirm that only the 9 full-name State rows fail until the feed is rebuilt.

**Checkpoint**: the schema MVP works on its own.

---

## Phase 5: User Story 3, Coverage delta (P1)

**Goal**: a column's coverage cannot silently collapse.

**Independent Test**: `python qc/coverage_delta.py --selftest` replays 123 to 3 on `capacity_mw` and fails with both counts.

- [X] T019 [P] [US3] Create `tests/fixtures/coverage_delta/proposals_2026-09-10.json` holding the real before/after profiles of `data/proposals.csv`:
  - Before: `git show e71c8d9~1:data/proposals.csv`, 338 rows, `capacity_mw` 123.
  - After: `git show e71c8d9:data/proposals.csv`, 338 rows, `capacity_mw` 3.

  Record every column's non-null count, the sha256 and the commit ids (research D9).
- [X] T020 [US3] Create `qc/coverage_delta.py`:
  - `profile(path)`, and a store read/append for `snapshots/coverage_profiles.csv` with columns `date, sha256, file, rows, column, non_null`.
  - Baseline selection per research D8: manifest-anchored for `master_opposition_clean.csv`, the most recent profile with a different sha for other files, and the fallback recorded.
  - A delta rule: `fail` when `before > 0 and (before - after)/before > threshold`, `warn` for a missing declared column.
  - Thresholds from `configs/data_quality.json` (FR-004).
- [X] T021 [US3] In `qc/coverage_delta.py`, add robust-z outlier flags per research D10: `0.6745*(x-median)/MAD`, with the mean-absolute-deviation fallback, a threshold of 6, and review only. Parse values with commas stripped and a trailing unit tolerated.
- [X] T022 [US3] In `qc/coverage_delta.py`, write `data/coverage_delta_report.md` per contracts/outputs.md. Add the CLI flags `--no-write`, `--before/--after`, `--before-profile/--after-profile` and `--file`, with exit codes per contracts/cli.md.
- [X] T023 [US3] Add a `--selftest` to `qc/coverage_delta.py`:
  - The fixture replay fails naming `capacity_mw`, 123 and 3.
  - A 15 percent drop passes.
  - A missing baseline is recorded as none.
  - Manifest-anchored selection skips the current sha.
  - The z-score flags a planted 10,000 MW value among values of about 100.
  - MAD = 0 does not crash.
  - The report has no em-dash and no CR.
- [X] T024 [US3] Seed `snapshots/coverage_profiles.csv` by running `python qc/coverage_delta.py` on current data. Verify the SC-002 live replay from quickstart exits 1.

---

## Phase 6: User Story 4, Label disagreement (P2)

**Goal**: surface county labels that strongly contradict the out-of-fold scores, as a review worklist.

**Independent Test**: `python label_disagreement_audit.py --selftest`. The flipped label ranks first.

- [X] T025 [US4] Create `label_disagreement_audit.py`:
  - Read `data/county_policy_scores.csv` (or `--scores`). Skip with exit 0 when it is missing or lacks the columns.
  - Rules from config: positive with `calibrated_score` at or below the 0.10 quantile (`positive_bottom_decile`); negative at or above the 0.99 quantile (`negative_top_1pct`).
  - Ranking key: `|label - calibrated_score|` descending.
  - Evidence rows via `county_aggregator`'s `load_frame`, `norm_county`, `norm_state`, `_type_tokens`, `RESTRICTIVE_TYPES`, `ENACTED_STATUSES` and `DIRECTION_AMBIGUOUS_STATUSES`, imported and not copied, applying the same countable filter and the approved-needs-win guard.
  - Join `label_provenance` from `data/county_aggregate.csv`.
  - Write `data/label_disagreement_worklist.csv` with the columns of data-model.md section 4.
  - Flags `--out`, `--no-evidence`, `--no-write` and `--check-recall`.
- [X] T026 [US4] Add a `--selftest` to `label_disagreement_audit.py`:
  - Build a synthetic frame (400 counties, one informative feature, labels following it), and compute 5-fold cross-fit logistic probabilities with scikit-learn.
  - Flip one strongly negative county to label 1, and assert that it ranks first.
  - Missing scores give a SKIP and exit 0.
  - The evidence filter skips an `approved` row whose outcome is not `win`.
  - Output has LF only.
- [X] T027 [US4] Run the SC-003 replay from quickstart (`aaf6cb8` vs `57547fc`) and the audit on current data. Record the measured recall in tasks.md status notes and in the PR. The expected result is 0 of 28, not met (research D11).

---

## Phase 7: User Story 5, Political source (P2)

**Goal**: MEDSL-based political variables with parity against `county_votes.json`, not yet wired into any consumer.

**Independent Test**: `python fetch_county_features.py --selftest` passes the five-county MEDSL fixture cases.

- [X] T028 [P] [US5] Create `tests/fixtures/medsl/countypres_fixture.csv` in MEDSL long format. Columns: `year,state,state_po,county_name,county_fips,office,candidate,party,candidatevotes,totalvotes,version,mode`. Cover these five counties:
  - IA 19001 with TOTAL rows.
  - IL 17001 with modes split and no TOTAL.
  - MO 29095 Jackson, plus the Kansas City row `2938000`.
  - CT with retired county 09001 for 2016/2020 and planning regions 09110/09120 for 2024.
  - AK districts `2001`/`2002`.

  Cover 2016, 2020 and 2024.
- [X] T029 [US5] Add the `political` source to `fetch_county_features.py` per research D12:
  - Dataverse API metadata (DOI `doi:10.7910/DVN/VOQCHQ`), file selection, and the original-format download.
  - The pure `parse_medsl(rows, frame, years, fallback_states)`: TOTAL-or-sum modes, the KC/46113/51515 recodes, and the per-state-year geo basis.
  - Columns per data-model.md section 5, with `votes_per_pop_{Y}` using population from `data/county_aggregate.csv`, added to `load_frame` as an additive `population` key.
  - Parity against `data/county_votes.json`, written to `data/features/political_parity.csv` and `.md`.
  - Manifest `info` with `doi`, `dataset_version`, `file_id`, `file_name`, `md5`, `license_name`, `license_uri` and `terms_of_use` (FR-008).

  Register the source in `BUILDERS` and in `configs/feature_sources.json` (years `[2016, 2020, 2024]`, `state_fallback ["AK","CT"]`). Do not register it in `configs/feature_plugins.json`.
- [X] T030 [US5] Extend `selftest()` in `fetch_county_features.py` with the fixture cases:
  - IA two-party share exact.
  - IL mode sum.
  - Jackson includes KC.
  - CT 2016 `geo_basis=state` with the statewide share for both planning regions, and CT 2024 `geo_basis=county`.
  - AK statewide for both boroughs.
  - Change columns computed.
  - Parity flag at 0.02.
  - Manifest info keys present.
  - `political` not in the plugin config.

  Then run `python layer_audit.py --write-gitattributes`.

---

## Phase 8: Polish and cross-cutting

- [X] T031 Write `docs/pending_data_quality.patch` and `docs/pending_data_quality.md`. `pipeline.yml` changes:
  - Add `pandera` to the install line.
  - After the feed build, add the step `Data quality schemas` (`id: schemas`, `continue-on-error: true`, `python qc/schemas.py`) and the step `Coverage delta` (`id: coverage_delta`, `continue-on-error: true`, `python qc/coverage_delta.py`).
  - After the county layer, add `Label disagreement audit` (`id: label_disagreement`, gated on `steps.county_layer.outcome == 'success'`, `continue-on-error: true`).
  - Stage `qc/schema_report.md`, `data/schema_run_history.csv`, `data/coverage_delta_report.md`, `snapshots/coverage_profiles.csv` and the worklist, the last on its step outcome.
  - Add surface-failure steps for the schema and coverage delta.

  `fetch-features.yml` changes: stage `data/features/*.md`. Verify with `git apply --check` against HEAD.
- [X] T032 [P] Run `python leak_audit.py --tier blocking` (0), `python layer_audit.py --strict --no-write` (0 undeclared), and a grep for em-dashes and CR in touched files (SC-004, Constitution gates 1, 2 and 5).
- [X] T033 Run `pre-commit run --all-files` and `python -m pytest tests/test_selftests.py -q`. All must pass, and the new selftests must appear in discovery.
- [X] T034 Update the Status notes at the top of `specs/005-data-quality-schemas/tasks.md` and mark the tasks done.

---

## Dependencies and execution order

- Phase 1 comes first. T002 and T003 are parallel with T001.
- Phase 2: T004 before T010, T020 and T025. T005 before T011.
- US2 (Phase 3) before US1 (Phase 4), because the schema imports `normalize_state`.
- US3, US4 and US5 depend only on Phase 2 and can run in parallel with US1.
- T006 declarations before T032. T030's gitattributes regen comes after all writers exist.
- T031 comes after every step it wires exists.

## Parallel examples

- After Phase 2: T019 (fixture), T028 (MEDSL fixture) and T007 (normalizer) touch different files.
- US3, US4 and US5 are fully independent modules.

## Implementation strategy

1. MVP: Phases 1 to 4. The schema runs report-only, with the normalizer.
2. Add US3, the coverage gate, which would have caught the 2026-09-10 regression.
3. Add US4 and US5 (review worklist and new source), neither of which blocks.
4. Polish: patch, gates, status notes.
