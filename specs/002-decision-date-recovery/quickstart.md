# Quickstart & Validation Guide: Decision-Date Recovery

**Feature**: `specs/002-decision-date-recovery/` | **Date**: 2026-09-28

---

## Prerequisites

```bash
# From repo root — all files must exist
ls data/decision_date_worklist.csv
ls master_opposition.csv
```

No pip installs required (stdlib only).

---

## Validation Scenarios

Run these in order to confirm the feature is working end-to-end.

### S1 — Selftest passes (no data files needed)

```bash
python date_recovery.py --selftest
```

**Expected**: All checks print `PASS`. Exit code 0.

**What this confirms**: offline pattern engine, no-match handling, year_only flag logic, `apply_recovery()` signature unchanged — all with inline fixtures, no file reads.

---

### S2 — --decision-dates pass produces a candidates file

```bash
python date_recovery.py --decision-dates
ls -lh data/decision_date_recovery_candidates.csv
```

**Expected**:
- File exists at `data/decision_date_recovery_candidates.csv`
- Exactly 48 rows (header + 48 data rows)

```bash
python -c "
import csv
rows = list(csv.DictReader(open('data/decision_date_recovery_candidates.csv')))
print(f'Total rows: {len(rows)}')
hits = [r for r in rows if r['recovered_date']]
print(f'Recovered: {len(hits)}')
year_only = [r for r in rows if r['year_only'] == 'true']
print(f'Year-only: {len(year_only)}')
no_url = [r for r in rows if r['method'] == 'no_source_url']
print(f'No source URL: {len(no_url)}')
"
```

**Expected output (approximate)**:
```
Total rows: 48
Recovered: 0–9  (depends on URL patterns in master_opposition)
Year-only: 0–9
No source URL: 39
```

---

### S3 — project_decision_dates.csv is not touched

```bash
git diff --name-only data/project_decision_dates.csv
python date_recovery.py --decision-dates
git diff --name-only data/project_decision_dates.csv
```

**Expected**: No output both times. The file is not modified.

---

### S4 — Candidates file columns are correct

```bash
python -c "
import csv
rows = list(csv.DictReader(open('data/decision_date_recovery_candidates.csv')))
expected = {'project_id','project_name','state','lifecycle_outcome',
            'recovered_date','method','source_url','year_only'}
actual = set(rows[0].keys())
print('OK' if actual == expected else f'MISMATCH: {actual ^ expected}')
"
```

**Expected**: `OK`

---

### S5 — Leak audit passes

```bash
python leak_audit.py --tier blocking
```

**Expected**: Exit code 0, no blocking hits reported.

---

### S6 — Layer audit passes (after configs/layers.json update)

```bash
python layer_audit.py
```

**Expected**: Exit code 0. `data/decision_date_recovery_candidates.csv` appears as a declared Layer E file (not flagged as undeclared).

---

### S7 — Pipeline selftest step runs clean

Add `python date_recovery.py --selftest` to `.github/workflows/pipeline.yml` and confirm the CI workflow run succeeds. For local verification:

```bash
python date_recovery.py --selftest && echo "CI step OK"
```

**Expected**: `CI step OK`

---

## Adoption (out of code scope — manual step)

After reviewing `data/decision_date_recovery_candidates.csv`:

1. Identify rows with `recovered_date != ""` and acceptable precision (check `year_only`)
2. Verify each `source_url` manually
3. Append accepted rows to `data/project_decision_dates.csv` with columns: `project_id, decision_date, decision_date_source, source_url, note`
4. Set `decision_date_source` to the `method` value (e.g., `iso_in_url`, `ymd_path`)

Do NOT use code to append to `project_decision_dates.csv`. It is a Layer B file; all writes are manual.
