# Tasks: Negative Audit Random Stratum Coding Tooling

**Input**: Design documents from `specs/003-negative-audit-stratum/`

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data model**: [data-model.md](data-model.md) | **Quickstart**: [quickstart.md](quickstart.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: User story this task belongs to (US1, US2, US3)

## Files touched

```
negative_audit.py                             # argparse entrypoint, COVERAGE_THRESHOLD,
                                              # next_batch(), coverage gate in report,
                                              # selftest()
.github/workflows/pipeline.yml               # add --selftest step to selftest block
```

---

## Phase 1: Setup

**Purpose**: Confirm baseline is green before any edits.

- [x] T001 Run `python3 negative_audit.py` to confirm the module runs cleanly (worklist + report regenerate, "leak audit: clean" printed, exit 0); record the current exit code as baseline

**Checkpoint**: Module runs cleanly; `data/negative_audit_worklist.csv` and `data/negative_audit_report.md` are intact.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Infrastructure all user stories share.

- [x] T002 In `negative_audit.py`, add `import argparse` to the imports block (after `from collections import Counter`); add module-level constant `COVERAGE_THRESHOLD = 0.80` immediately after `RANDOM_STATE = 20260723`

- [x] T003 In `negative_audit.py`, replace `if __name__ == "__main__": sys.exit(main())` with an argparse block: `parser = argparse.ArgumentParser(...)`, `parser.add_argument("--next", type=int, metavar="N")`, `parser.add_argument("--selftest", action="store_true")`; the default path (no flags) calls `sys.exit(main())` exactly as before; the `--next N` path calls `sys.exit(next_batch(args.next))`; the `--selftest` path calls `raise SystemExit(selftest())`

**Checkpoint**: `python3 negative_audit.py` still runs identically (exit 0, same output); `python3 negative_audit.py --help` lists `--next` and `--selftest`.

---

## Phase 3: User Story 1 — --next N queue (Priority: P1) 🎯 MVP

**Goal**: `python3 negative_audit.py --next N` prints the next N uncoded random-stratum rows in ascending `audit_order`, with name, state, county, lifecycle_outcome, and search_protocol. Never includes `blocked_confirmed` rows. Exits 0 if rows found, 0 if stratum complete (with message).

**Independent Test**: Run `python3 negative_audit.py --next 5` and verify: exactly 5 rows printed, no `blocked_confirmed` row in output, first row has `audit_order` matching the first uncoded random-stratum row (≥23 with current codings).

- [x] T004 [US1] In `negative_audit.py`, implement `next_batch(n: int) -> int` — reads `data/negative_audit_worklist.csv` with `csv.DictReader` (NOT calling `build_frame()`); separates random stratum (`lifecycle_outcome != "blocked_confirmed"`); reads `data/negative_audit_codings.csv` if it exists (empty list if missing); calls `validate_codings(codings_raw, frame_ids)` to get accepted codings; builds `coded_ids = {c["universe_id"] for c in accepted}`; filters random stratum to uncoded rows; takes first `n` (by file order = ascending `audit_order`); if `n <= 0` or `n` not supplied: prints usage error and returns 1; if stratum fully coded: prints "Random stratum complete ({total} rows coded). {n_blocked_uncoded} blocked_confirmed rows remain uncoded." and returns 0; otherwise prints each row as:
  ```
  [{audit_order}] {name} ({state}, {county}) [{lifecycle_outcome}]
  Search: {search_protocol}
  ```
  one blank line between rows; returns 0

- [x] T005 [US1] In `negative_audit.py`, wire `next_batch()` into the argparse `--next` path in the `__main__` block (T003): when `args.next` is not None and `args.next > 0`, call `sys.exit(next_batch(args.next))`; when `args.next` is 0 or negative, print `"--next requires a positive integer N"` and exit 1

**Checkpoint**: `python3 negative_audit.py --next 5` prints exactly 5 rows, all from random stratum (lifecycle_outcome != blocked_confirmed), in ascending audit_order; `python3 negative_audit.py --next 0` exits 1 with usage error.

---

## Phase 4: User Story 2 — Coverage gate in report (Priority: P1)

**Goal**: `python3 negative_audit.py` (default mode) generates a report that labels the coding-mix table as "interim descriptives" and suppresses the emergence rate when less than 80% of random-stratum rows are coded. At ≥80%, prints emergence rate with undeterminable count alongside.

**Independent Test**: Run `python3 negative_audit.py` and `grep -i "interim" data/negative_audit_report.md` — should return a line at current 10/169 coverage (~6%). `grep -i "emergence rate:" data/negative_audit_report.md` should return no output.

- [x] T006 [US2] In `negative_audit.py`, in `main()`, after building the `codings` dict, add:
  - `random_stratum_ids = {r["universe_id"] for r in frame if r.get("lifecycle_outcome") != "blocked_confirmed"}`
  - `n_random_total = len(random_stratum_ids)`
  - `n_random_coded = sum(1 for uid in codings if uid in random_stratum_ids)`
  - `random_coverage = n_random_coded / n_random_total if n_random_total > 0 else 0.0`
  - `below_threshold = random_coverage < COVERAGE_THRESHOLD`

- [x] T007 [US2] In `negative_audit.py`, in `main()`, update the report section that writes the coding-mix table: when `below_threshold` is True, replace the heading `"## Coding mix (coded rows)"` with `"## Coding mix (interim descriptives — {pct}% of random stratum coded; {n_remaining} rows remaining)"` (where `pct = int(random_coverage * 100)` and `n_remaining = n_random_total - n_random_coded`); add a line after the table: `"Emergence rate withheld: random-stratum coverage at {pct}% (threshold: {int(COVERAGE_THRESHOLD * 100)}%). Rate will appear when coding reaches {int(COVERAGE_THRESHOLD * 100)}% of {n_random_total} random-stratum rows."` (FR-005); when `below_threshold` is False, add the emergence rate after the coding-mix table in the form: `"Emergence rate: {vo} / ({vo} + {vn}) = {rate:.1%} ({und} undeterminable rows excluded from denominator, not missing at random)."` where `vo = mix.get("verified_opposition", 0)`, `vn = mix.get("verified_none", 0)`, `und = mix.get("undeterminable", 0)` (FR-006)

**Checkpoint**: `python3 negative_audit.py` exits 0; `data/negative_audit_report.md` contains "interim descriptives" with current 10/169 codings; report does not contain the pattern `Emergence rate: \d`.

---

## Phase 5: User Story 3 — --selftest entry point (Priority: P2)

**Goal**: `python3 negative_audit.py --selftest` runs with no file I/O, exits 0 when all checks pass, 1 on any failure. Exercises: coding validation, `--next` queue logic, coverage gate threshold.

**Independent Test**: Run `python3 negative_audit.py --selftest` with no data files present. Verify exit code 0 and all named checks print `PASS`.

- [x] T008 [US3] In `negative_audit.py`, implement `selftest() -> int` function: add `checks = []` and local `check(label, ok)` helper (same pattern as `date_recovery.py` — `checks.append((label, ok))`, `print(f"{'PASS' if ok else 'FAIL'}  {label}")`); then add these inline fixture checks:
  - `"validate_codings: row without coded_by is rejected"` — construct a fake coding dict missing `coded_by`; call `validate_codings([row], {"prj_test"})`; check that `ok == []`
  - `"validate_codings: verified_none without detectability_url is rejected"` — construct a fake `verified_none` coding with `coded_by` but empty `detectability_url`; call `validate_codings([row], {"prj_test"})`; check that `ok == []`
  - `"validate_codings: valid verified_opposition row is accepted"` — construct a valid `verified_opposition` row with `evidence_url`, `coded_by`, `coded_date`; call `validate_codings([row], {"prj_test"})`; check that `len(ok) == 1`
  - `"next_batch queue: only random-stratum rows returned"` — build an inline worklist of 3 blocked_confirmed rows + 5 random rows; simulate `next_batch` logic (filter by lifecycle_outcome != blocked_confirmed); check that result has 5 rows and none has `blocked_confirmed`
  - `"next_batch queue: coded rows excluded"` — from the 5 random rows above, mark 2 as coded; check that result has 3 uncoded rows
  - `"coverage gate: 79% is below threshold"` — `check(...)` that `79/100 < COVERAGE_THRESHOLD` is True (rate suppressed)
  - `"coverage gate: 80% meets threshold"` — `check(...)` that `80/100 >= COVERAGE_THRESHOLD` is True (rate permitted)
  - `"coverage gate: 100% meets threshold"` — `check(...)` that `100/100 >= COVERAGE_THRESHOLD` is True
  - `"COVERAGE_THRESHOLD is 0.80"` — `check(...)` that `COVERAGE_THRESHOLD == 0.80`
  - `"--next output passes leak-audit regex"` — build a sample output string `"[23] Sample Project (VA, fairfax) [advanced_confirmed]\nSearch: foo bar baz"` and assert `re.compile(r"\b(win|wins|loss|losses|lost)\b", re.I).search(sample)` is None (FR-008)

- [x] T009 [US3] Wire `selftest()` into the `--selftest` argparse path in the `__main__` block (already stubbed in T003): `raise SystemExit(selftest())` when `args.selftest` is True

**Checkpoint**: `python3 negative_audit.py --selftest` exits 0; all 9 named checks print `PASS`; no files read or written.

---

## Phase 6: Polish & Validation

**Purpose**: CI wiring and full pre-commit gate.

- [x] T010 [P] In `.github/workflows/pipeline.yml`, add the following line to the selftest block immediately after `python date_recovery.py --selftest` (added in 002):
  ```
  python negative_audit.py --selftest
  ```

- [x] T011 Run all validation scenarios from `specs/003-negative-audit-stratum/quickstart.md` in order:
  1. S1: `python3 negative_audit.py --selftest` → exit 0, all checks PASS
  2. S2: `python3 negative_audit.py --next 5` → exactly 5 rows, none blocked_confirmed
  3. S3: two runs of `--next 5` produce identical output
  4. S4: `python3 negative_audit.py` → `data/negative_audit_report.md` contains "interim", no `Emergence rate:` line
  5. S5: `python3 negative_audit.py --next 20 | grep "blocked_confirmed"` → no output
  6. S6: `python3 negative_audit.py` → exit 0, "leak audit: clean"
  7. S7: `python3 leak_audit.py --tier blocking` → exit 0

---

## Dependencies & Execution Order

- **Phase 1** → baseline before any edits
- **Phase 2** → T002 then T003 (sequential in same file)
- **Phase 3 (US1)** → T004 → T005 (T004 implements function; T005 wires it to argparse)
- **Phase 4 (US2)** → T006 → T007 (sequential; T007 reads variables set by T006)
- **Phase 5 (US3)** → T008 → T009 (sequential; T009 wires what T008 implements)
- **Phase 6** → T010 [P] can run alongside T011-S1; T011 must complete last

### Parallel Opportunities

T010 (`pipeline.yml`) can run in parallel with T011's early validation steps — different file.

---

## Implementation Strategy

### MVP (US1 + US2 — both P1)

1. T001 (baseline)
2. T002 → T003 (argparse + COVERAGE_THRESHOLD)
3. T004 → T005 (--next queue)
4. T006 → T007 (coverage gate)
5. Run S2, S4, S6 from quickstart.md — must all pass before continuing

### Full Delivery (US3 + CI)

6. T008 → T009 (--selftest)
7. T010 (pipeline.yml)
8. T011 (full suite)

### Single-file scope

All Python changes are in `negative_audit.py`: one import + one constant (T002), new argparse block (T003), `next_batch()` function (T004), argparse wiring (T005), two coverage gate additions to `main()` (T006–T007), `selftest()` function (T008), selftest wiring (T009). `pipeline.yml` gains one line (T010).
