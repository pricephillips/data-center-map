# Quickstart & Validation Guide: Negative Audit Stratum Tooling

**Feature**: `specs/003-negative-audit-stratum/` | **Date**: 2026-09-28

---

## Prerequisites

```bash
# From repo root — worklist must exist (run default mode once if not)
ls data/negative_audit_worklist.csv
ls data/negative_audit_codings.csv   # may not exist; --next handles that gracefully
```

---

## Validation Scenarios

Run these in order to confirm the feature is working end-to-end.

### S1 — Selftest passes (no data files needed)

```bash
python3 negative_audit.py --selftest
```

**Expected**: All checks print `PASS`. Exit code 0.

**What this confirms**: coding validation rules (coded_by required, detectability_url required for verified_none), `--next` queue logic (random stratum only, ascending order), coverage gate (79% suppresses rate, 80% permits it), leak audit inline check.

---

### S2 — --next returns exactly N uncoded random-stratum rows

```bash
python3 negative_audit.py --next 5
```

**Expected**: Exactly 5 rows printed, each with name, state, county, lifecycle_outcome, and search_protocol. No blocked_confirmed rows included.

```bash
# Verify count:
python3 negative_audit.py --next 5 | grep -c "^\["
```

**Expected output**: `5`

---

### S3 — --next re-run returns same rows until one is coded

```bash
python3 negative_audit.py --next 5 > /tmp/batch1.txt
python3 negative_audit.py --next 5 > /tmp/batch2.txt
diff /tmp/batch1.txt /tmp/batch2.txt
```

**Expected**: No diff — same 5 uncoded rows returned.

---

### S4 — Coverage gate: current state says "interim descriptives"

```bash
python3 negative_audit.py
grep -i "interim" data/negative_audit_report.md
grep -i "emergence rate:" data/negative_audit_report.md
```

**Expected**: `grep -i "interim"` returns a line; `grep -i "emergence rate:"` returns no output (rate is suppressed at current 10/169 coverage = ~6%).

---

### S5 — No blocked_confirmed rows in --next output

```bash
python3 negative_audit.py --next 20 | grep "blocked_confirmed"
```

**Expected**: No output. `--next` never includes purposive-cell rows.

---

### S6 — Default mode still works (backward compat)

```bash
python3 negative_audit.py
echo "exit: $?"
```

**Expected**: Prints audit frame summary, coding mix, "leak audit: clean". Exit code 0.

---

### S7 — Leak audit passes

```bash
python3 leak_audit.py --tier blocking
```

**Expected**: Exit code 0, `blocking: 0`.

---

### S8 — Pipeline selftest step runs clean

```bash
python3 negative_audit.py --selftest && echo "CI step OK"
```

**Expected**: `CI step OK`

---

## Usage for Researchers

To work through the next batch of 10 uncoded rows:

```bash
python3 negative_audit.py --next 10
```

Print the output, work top-down, enter results into `data/negative_audit_codings.csv`:

```csv
universe_id,coding,evidence_url,detectability_url,queries_run,notes,coded_by,coded_date
prj_XXX,verified_none,,https://example.com/hearing-record,3,"",your_name,2026-09-28
```

Re-run `--next 10` to see progress — coded rows drop off automatically.

Run `python3 negative_audit.py` to regenerate the report and see updated coverage.
