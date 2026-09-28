# Tasks: Decision-Date Recovery for Landmark Retrain Gate

**Input**: Design documents from `specs/002-decision-date-recovery/`

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data model**: [data-model.md](data-model.md) | **Quickstart**: [quickstart.md](quickstart.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: User story this task belongs to (US1, US3; US2 is a long-run human-adoption outcome, not a code task)

## Files touched

```
date_recovery.py                              # argparse entrypoint + --decision-dates + --selftest
configs/layers.json                           # declare data/decision_date_recovery_candidates.csv in Layer E
.github/workflows/pipeline.yml               # add --selftest step to selftest block
```

---

## Phase 1: Setup

**Purpose**: Confirm baseline is green before any edits.

- [x] T001 Run `python date_recovery.py master_opposition_clean.csv 2>&1 | head -5` to confirm `apply_recovery()` is reachable and the module imports cleanly; record any import errors before touching the file

**Checkpoint**: Module imports cleanly; `apply_recovery()` signature confirmed unchanged before edits.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Infrastructure that all user stories depend on.

- [x] T002 In `date_recovery.py`, replace the `if __name__ == "__main__"` block (lines 139–142) with an `argparse`-based entrypoint: add `parser = argparse.ArgumentParser()`, add `--input FILE` (default `master_opposition_clean.csv`), add `--decision-dates` (store_true), add `--selftest` (store_true); the default path (no flags) must still call `apply_recovery(rows)` exactly as before to preserve backward compatibility (FR-009)

- [x] T003 [P] In `configs/layers.json`, add `"data/decision_date_recovery_candidates.csv"` to the Layer E `"files"` list, immediately after `"data/decision_date_worklist.csv"` (line 143)

**Checkpoint**: `python date_recovery.py master_opposition_clean.csv` still runs identically; `python layer_audit.py` no longer flags `decision_date_recovery_candidates.csv` as undeclared.

---

## Phase 3: User Story 1 — --decision-dates offline recovery pass (Priority: P1) 🎯 MVP

**Goal**: `python date_recovery.py --decision-dates` reads worklist, joins master_opposition by project name, runs offline URL-pattern engine, writes `data/decision_date_recovery_candidates.csv` (48 rows, all projects). Never writes `project_decision_dates.csv`.

**Independent Test**: Run `python date_recovery.py --decision-dates` and verify (a) `data/decision_date_recovery_candidates.csv` has exactly 48 data rows, (b) `project_decision_dates.csv` is untouched, (c) every row has `project_id`, `project_name`, `state`, `lifecycle_outcome`, `recovered_date`, `method`, `source_url`, `year_only`, (d) rows with no URL have `method="no_source_url"`, (e) rows with URL but no pattern match have `method="no_pattern_match"`, (f) rows where `method` ends in `_midyear` have `year_only="true"`.

- [x] T004 [US1] In `date_recovery.py`, implement `_load_decision_worklist(path: str) -> list[dict]` — reads `data/decision_date_worklist.csv` with `csv.DictReader`; returns list of dicts; columns: `project_id, project_name, state, county, phase, lifecycle_outcome, n_opposition_events, first_opposition_date, what_to_recover`

- [x] T005 [US1] In `date_recovery.py`, implement `_build_url_index(opposition_path: str) -> dict[str, list[str]]` — reads `master_opposition.csv`, builds dict from `project_name.lower().strip()` → **list of all non-blank `Source URL` values** for that project name (one entry per matching opposition row, preserving order); used to try all URLs per project in T006 (FR-002: "all matching source URLs")

- [x] T006 [US1] In `date_recovery.py`, implement `run_decision_dates(worklist_path: str, opposition_path: str, out_path: str) -> dict` — for each worklist row: (a) look up URL list via `_build_url_index` (empty list if no match), (b) if no URLs, set `method="no_source_url"`, `recovered_date=""`, `source_url=""`; (c) if URLs present, call `recover_from_url(url)` for **each URL in the list**, keep the result with the most day-precision (ymd > ym_midmonth > year_only_midyear > no match) — precision order: full-date methods > midmonth > midyear > no match; use the `source_url` of the winning hit; (d) if all URLs return no match, set `method="no_pattern_match"`, `recovered_date=""`; (e) set `year_only="true"` if winning method ends with `"_midyear"` else `"false"`; (f) write all 48 rows to `out_path` with fieldnames `project_id, project_name, state, lifecycle_outcome, recovered_date, method, source_url, year_only`; return summary dict `{"total": 48, "recovered": n, "no_source_url": n, "no_pattern_match": n, "year_only": n}`; MUST NOT read or write `data/project_decision_dates.csv` at any point

- [x] T007 [US1] In `date_recovery.py`, wire `run_decision_dates()` into the `--decision-dates` flag in the argparse block: call `run_decision_dates("data/decision_date_worklist.csv", "master_opposition.csv", "data/decision_date_recovery_candidates.csv")`; print the returned summary dict; exit 0

**Checkpoint**: `python date_recovery.py --decision-dates` runs to completion; `data/decision_date_recovery_candidates.csv` has 48 rows; `project_decision_dates.csv` diff is clean.

---

## Phase 4: User Story 3 — --selftest entry point (Priority: P2)

**Goal**: `python date_recovery.py --selftest` runs with no network, no file I/O, exits 0 when all checks pass, 1 on any failure. Exercises every URL pattern, no-match case, and year-only flagging logic with inline fixtures.

**Independent Test**: Run `python date_recovery.py --selftest` in an environment with no data files present. Verify exit code 0 and all checks print `PASS`.

- [x] T008 [US3] In `date_recovery.py`, implement `selftest()` function: first add a local helper `def check(label, ok): global failed; ...` and a `failed = 0` counter at the top of `selftest()` (same pattern as in `scripts/scrape-trackdatacenters-proposals.py` — copy the helper from there); then add inline fixture checks for every pattern type in `_PATTERNS`:
  - `"ymd_path: /2024/03/15/ → 2024-03-15"` — `recover_from_url("https://example.com/news/2024/03/15/story") == ("2024-03-15", "ymd_path")`
  - `"iso_in_url: 2023-07-04 in body → 2023-07-04"` — `recover_from_url("https://city.gov/docs/decision-2023-07-04.pdf") == ("2023-07-04", "iso_in_url")`
  - `"compact: 20221105 in segment → 2022-11-05"` — `recover_from_url("https://example.com/release_20221105_final") == ("2022-11-05", "compact")`
  - `"ym_path_midmonth: /2025/06/ → 2025-06-15"` — `recover_from_url("https://example.com/2025/06/") == ("2025-06-15", "ym_path_midmonth")`
  - `"monthname: march 15 2023 → 2023-03-15"` — `recover_from_url("https://example.com/march-15-2023-hearing") == ("2023-03-15", "monthname")`
  - `"dmonthname: 15 march 2023 → 2023-03-15"` — `recover_from_url("https://example.com/15-march-2023-vote") == ("2023-03-15", "dmonthname")`
  - `"year_only_midyear: /2021/ → 2021-07-01 with method year_only_midyear"` — `recover_from_url("https://example.com/projects/2021/final-approval") == ("2021-07-01", "year_only_midyear")`
  - `"no match: plain URL → empty"` — `recover_from_url("https://example.com/meeting-notes") == ("", "")`
  - `"empty url → empty"` — `recover_from_url("") == ("", "")`
  - `"year_only flag is true when method ends in _midyear"` — construct a minimal candidates row where method is `"year_only_midyear"` and verify the flag logic sets `year_only="true"`
  - `"year_only flag is false for ymd_path method"` — same check for a non-midyear method

- [x] T009 [US3] In `date_recovery.py`, wire `selftest()` into the `--selftest` flag in the argparse block: call `selftest()`; print passed/failed counts; exit 0 if all pass, exit 1 if any fail

**Checkpoint**: `python date_recovery.py --selftest` exits 0; all 11 named checks print `PASS`; no file I/O performed during the run.

---

## Phase 5: Polish & Validation

**Purpose**: CI wiring and full pre-commit gate.

- [x] T010 [P] In `.github/workflows/pipeline.yml`, add the following step in the selftest block (where other `--selftest` entries are grouped, around line 119):
  ```yaml
  - name: Selftest date_recovery
    run: python date_recovery.py --selftest
  ```

- [x] T011 Run all validation scenarios from `specs/002-decision-date-recovery/quickstart.md` in order:
  1. S1: `python date_recovery.py --selftest` → exit 0, all checks PASS
  2. S2: `python date_recovery.py --decision-dates` → `data/decision_date_recovery_candidates.csv` exists with 48 rows
  3. S3: `git diff --name-only data/project_decision_dates.csv` → no output (file untouched)
  4. S4: candidates columns match `{project_id, project_name, state, lifecycle_outcome, recovered_date, method, source_url, year_only}`
  5. S5: `python leak_audit.py --tier blocking` → exit 0
  6. S6: `python layer_audit.py` → exit 0 (candidates file declared)

---

## Dependencies & Execution Order

- **Phase 1** → must pass before any edits
- **Phase 2** → T002 then T003 [P] (different files; T002 and T003 can run simultaneously)
- **Phase 3 (US1)** → T004 → T005 → T006 → T007 (each step builds on the previous within `date_recovery.py`)
- **Phase 4 (US3)** → T008 → T009 (sequential, same function)
- **Phase 5** → T010 [P] can run alongside T011's first three validation scenarios; T011 must complete last

### Parallel Opportunities

T003 (`configs/layers.json`) can run in parallel with T002 (`date_recovery.py`) — different files.
T010 (`pipeline.yml`) can run in parallel with T011's setup steps — different file.

```bash
# Can run in parallel:
# Thread A: T002 (argparse entrypoint in date_recovery.py)
# Thread B: T003 (Layer E declaration in configs/layers.json)
```

---

## Implementation Strategy

### MVP (US1 — get candidates file working)

1. T001 (baseline check)
2. T002 (argparse entrypoint) + T003 [P] (layer declaration)
3. T004 → T005 → T006 → T007 (--decision-dates logic)
4. Run S2–S4 from quickstart.md — candidates file must exist with correct schema
5. Run `python layer_audit.py` — must pass

### Full Delivery (US3 + CI)

6. T008 → T009 (--selftest)
7. T010 (pipeline.yml selftest step)
8. T011 (full validation suite)

### Single-file scope

All Python changes are in `date_recovery.py`: the `__main__` block (T002), three new private functions (T004, T005, T006), wiring in the argparse block (T007, T009), and the `selftest()` function (T008). `configs/layers.json` is a one-line JSON array append (T003). `.github/workflows/pipeline.yml` is a two-line YAML addition (T010).
