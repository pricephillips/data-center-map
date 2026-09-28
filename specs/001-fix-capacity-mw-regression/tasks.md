# Tasks: Fix capacity_mw Regression

**Input**: Design documents from `specs/001-fix-capacity-mw-regression/`

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data model**: [data-model.md](data-model.md) | **Quickstart**: [quickstart.md](quickstart.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: User story this task belongs to (US1, US2, US3)

## Files touched

```
scripts/scrape-trackdatacenters-proposals.py     # US1 verify + US2 code changes
.github/workflows/scrape-trackdatacenters-proposals.yml  # US2 CI wiring
```

---

## Phase 1: Setup

**Purpose**: Confirm the baseline is green before making changes.

- [x] T001 Run `python3 scripts/scrape-trackdatacenters-proposals.py --selftest` and confirm all checks pass (exit 0); record the current check count as the baseline to verify the new checks are added in T006

**Checkpoint**: Baseline selftest count confirmed; safe to begin edits.

---

## Phase 2: User Story 1 — Correct capacity_mw parsing (Priority: P1) 🎯 MVP

**Goal**: Confirm `flatten()` reads `capacity_mw` from `capacityMw` (current API name) or the legacy `capacity_mw`, never imputes, and treats zero as a value.

**Independent Test**: Run `--selftest`; the checks `"capacity under the new name is read"`, `"the previous names still work if the source rolls back"`, and `"a non-date alias is not date-filtered"` all print `PASS`.

- [x] T002 [US1] In `scripts/scrape-trackdatacenters-proposals.py`, confirm `SOURCE_KEYS['capacity_mw']` is `('capacityMw', 'capacity_mw')` (current name first, legacy fallback); if absent or misordered, correct it
- [x] T003 [US1] In `scripts/scrape-trackdatacenters-proposals.py`, confirm `flatten()` calls `pick(record, *SOURCE_KEYS['capacity_mw'])` (not `pick_date()`); if it uses `pick_date()`, change to `pick()`
- [x] T004 [US1] In `scripts/scrape-trackdatacenters-proposals.py`, confirm the existing `selftest()` includes all three checks below; add any that are missing:
  - `"capacity under the new name is read"` — `flatten({"id":7,...,"capacityMw":250,...})["capacity_mw"] == 250`
  - `"the previous names still work if the source rolls back"` — `flatten({"id":8,...,"capacity_mw":10,...})["capacity_mw"] == 10`
  - `"a non-date alias is not date-filtered"` — `flatten({"id":13,...,"capacityMw":0})["capacity_mw"] == 0`

**Checkpoint**: `--selftest` passes; US1 acceptance scenarios 1–4 verified by the selftest.

---

## Phase 3: User Story 2 — 20% capacity-coverage guard + CI wiring (Priority: P1)

**Goal**: `--selftest` verifies the 20% capacity-coverage boundary (selftest-only, not a runtime abort per spec); the `CAPACITY_LOSS_RATIO` constant is added for use by the selftest; CI calls `--selftest` before every scrape.

**Independent Test**: `python3 scripts/scrape-trackdatacenters-proposals.py --selftest` exits 0 and includes three `PASS` lines for the capacity-coverage boundary checks added in T007.

- [x] T005 [US2] In `scripts/scrape-trackdatacenters-proposals.py`, add constant `CAPACITY_LOSS_RATIO = 0.2` immediately below `FIELD_LOSS_RATIO = 0.5` (comment: "tighter threshold for capacity_mw specifically; see 2026-09-10 regression")
- [x] T006 [US2] In `assert_field_population()` in `scripts/scrape-trackdatacenters-proposals.py`, confirm the function body is **unchanged** — do NOT add a second `population_violations()` call or a new `SystemExit` at the 20% threshold. The 20% check lives only in `selftest()` (T007); the spec Assumptions explicitly state "not a run-time abort." Only the constant `CAPACITY_LOSS_RATIO = 0.2` (added in T005) and the selftest assertions (T007) are the deliverables.
- [x] T007 [US2] In `selftest()` in `scripts/scrape-trackdatacenters-proposals.py`, add four named checks immediately after the existing `population_violations` boundary checks:
  - `"capacity coverage drop of 21% is flagged at 20% threshold"` — `population_violations([{"capacity_mw":"x"}]*100, [{"capacity_mw":"x"}]*79, ["capacity_mw"], ratio=0.2) != []`
  - `"capacity coverage drop of 19% is not flagged at 20% threshold"` — `population_violations([{"capacity_mw":"x"}]*100, [{"capacity_mw":"x"}]*81, ["capacity_mw"], ratio=0.2) == []`
  - `"capacity coverage drop at exactly 20% is not flagged (boundary)"` — `population_violations([{"capacity_mw":"x"}]*100, [{"capacity_mw":"x"}]*80, ["capacity_mw"], ratio=0.2) == []`
  - `"capacity coverage sparse skip applies at 20% threshold"` — `population_violations([{"capacity_mw":"x"}]*(FIELD_LOSS_MIN_PRIOR-1), [], ["capacity_mw"], ratio=0.2) == []`
- [x] T008 [P] [US2] In `.github/workflows/scrape-trackdatacenters-proposals.yml`, add a step immediately before the "Run scraper" step:
  ```yaml
  - name: Selftest
    run: python scripts/scrape-trackdatacenters-proposals.py --selftest
  ```

**Checkpoint**: `--selftest` exits 0 with 4 new PASS lines (3 boundary checks + 1 sparse-skip check); total check count is baseline + (new checks from T004 if any) + 4.

---

## Phase 4: User Story 3 — Backward-compatible CSV schema (Priority: P2)

**Goal**: Confirm `CSV_FIELDS` is unchanged end-to-end.

**Independent Test**: `git diff scripts/scrape-trackdatacenters-proposals.py` contains no lines touching `CSV_FIELDS`; the column `capacity_mw` is present at the same index as before.

- [x] T009 [US3] In `scripts/scrape-trackdatacenters-proposals.py`, verify `CSV_FIELDS` is unchanged (no columns added, removed, or reordered) by reviewing the diff; if any column was accidentally modified during T002–T007, restore it

**Checkpoint**: `CSV_FIELDS` diff is clean; US3 acceptance scenario 1 confirmed.

---

## Phase 5: Polish & Validation

**Purpose**: Run the full pre-commit validation suite from `quickstart.md`.

- [x] T010 Run all six validation scenarios from `specs/001-fix-capacity-mw-regression/quickstart.md` in order:
  1. `python3 scripts/scrape-trackdatacenters-proposals.py --selftest` → exit 0, baseline+N checks pass, four new capacity-coverage PASS lines visible
  2. `python3 -c "import csv; rows=list(csv.DictReader(open('data/proposals.csv'))); cap=[r for r in rows if r.get('capacity_mw','').strip()]; assert len(cap)>=123"` → passes
  3. `python3 cost_translation.py && python3 -c "import csv; assert len(list(csv.DictReader(open('data/cost_translation_demo.csv'))))>0"` → demo CSV non-empty
  4. `python3 leak_audit.py --tier blocking` → exit 0
  5. `python3 layer_audit.py` → exit 0
  6. `git diff HEAD scripts/scrape-trackdatacenters-proposals.py | grep -c CSV_FIELDS || true` → 0 (no CSV_FIELDS lines in diff)

---

## Dependencies & Execution Order

- **Phase 1** → must pass before any edits
- **Phase 2 (US1)** → T002, T003, T004 in order (each reads the same function; avoid conflicts)
- **Phase 3 (US2)** → T005, then T006 (needs CAPACITY_LOSS_RATIO), then T007 (needs population_violations); T008 [P] is independent (different file)
- **Phase 4 (US3)** → after T002–T008 are complete (verification pass)
- **Phase 5** → after all previous phases

### Parallel Opportunities

T008 (workflow YAML) is entirely independent of T005–T007 (scraper Python) — they touch different files. These can be worked simultaneously.

```bash
# Can run in parallel:
# Thread A: T005 → T006 → T007  (scraper constant + guard + selftest assertions)
# Thread B: T008                 (workflow YAML step)
```

---

## Implementation Strategy

### MVP (US1 + US2 — Priority P1 only)

1. T001 (baseline check)
2. T002 → T003 → T004 (verify and pin US1 parsing)
3. T005 → T006 → T007 + T008 (US2 guard and CI)
4. Run `--selftest` — must pass
5. Run `leak_audit.py --tier blocking` and `layer_audit.py` — must pass

US3 and Polish follow if the above passes.

### Single-file scope

All Python changes are in one function each: one constant added near the top of the guard section, one secondary call added to `assert_field_population()`, and three assertions added to `selftest()`. The workflow change is three lines of YAML. No new files, no imports, no schema changes.
