# Quickstart Validation Guide: Fix capacity_mw Regression

## Prerequisites

- Python 3.12+ in PATH
- Repo root as working directory
- `data/proposals.csv` present (the current file, which already has 163 capacity-populated rows)

No network access needed for validation. The scrape step is optional and requires network.

---

## Scenario 1: Selftest passes with all new assertions

**Validates**: FR-005 (selftest), FR-004 (20% threshold logic), SC-001

```bash
python3 scripts/scrape-trackdatacenters-proposals.py --selftest
```

**Expected output** (last line):
```
N/N checks passed
```
where N is the total check count (currently 71; will increase by 3 new checks).

Exit code must be 0. If any line prints `FAIL`, the implementation is incomplete.

**Key new checks to look for in the output:**
```
PASS  capacity coverage drop of 21% is flagged at 20% threshold
PASS  capacity coverage drop of 19% is not flagged at 20% threshold
PASS  capacity coverage drop at exactly 20% is not flagged (boundary)
```

---

## Scenario 2: SOURCE_KEYS alias reads capacityMw correctly

**Validates**: FR-001, FR-003 (zero is a value), existing behavior confirmed unbroken

Already covered in the existing selftest, but can be spot-checked:

```python
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from scrape_trackdatacenters_proposals import flatten, SOURCE_KEYS
# New name
r = {'id': 1, 'name': 'X', 'capacityMw': 300}
assert flatten(r)['capacity_mw'] == 300, 'capacityMw not read'
# Legacy name
r2 = {'id': 2, 'name': 'X', 'capacity_mw': 150}
assert flatten(r2)['capacity_mw'] == 150, 'legacy name not read'
# Zero is a value
r3 = {'id': 3, 'name': 'X', 'capacityMw': 0}
assert flatten(r3)['capacity_mw'] == 0, 'zero treated as absent'
# Neither name → empty
r4 = {'id': 4, 'name': 'X'}
assert flatten(r4)['capacity_mw'] == '', 'missing should be empty'
print('All SOURCE_KEYS checks pass')
"
```

Note: the import path above assumes the module can be imported as `scrape_trackdatacenters_proposals` (hyphens become underscores in Python imports). If the direct import fails, run the assertions inside `selftest()` instead.

---

## Scenario 3: capacity_mw coverage in the current proposals.csv

**Validates**: SC-002 (≥123 capacity-populated rows after the fix is applied)

```bash
python3 -c "
import csv
rows = list(csv.DictReader(open('data/proposals.csv')))
cap = [r for r in rows if r.get('capacity_mw','').strip()]
print(f'Total rows: {len(rows)}')
print(f'Rows with capacity_mw: {len(cap)}')
assert len(cap) >= 123, f'Expected >=123, got {len(cap)}'
print('Coverage check passes')
"
```

**Expected**: `Rows with capacity_mw:` is 163 or higher (the current file already has 163; this confirms the fix did not regress it).

---

## Scenario 4: cost_translation_demo.csv is non-empty

**Validates**: SC-003

```bash
python3 cost_translation.py
python3 -c "
import csv
rows = list(csv.DictReader(open('data/cost_translation_demo.csv')))
print(f'cost_translation_demo.csv rows: {len(rows)}')
assert len(rows) > 0, 'demo CSV is empty'
print('Demo CSV populated')
"
```

**Expected**: At least 1 row. Currently 21 rows with the existing proposals.csv — should remain at 21 or more after the fix.

---

## Scenario 5: leak_audit passes

**Validates**: SC-006, Constitution Gate 1

```bash
python3 leak_audit.py --tier blocking
```

**Expected**: Exit 0 with output `0 blocking hits` (or equivalent clean output).

---

## Scenario 6: layer_audit passes

**Validates**: Constitution Gate 2

```bash
python3 layer_audit.py
```

**Expected**: Exit 0 with `0 undeclared findings`.

---

## Scenario 7: CSV_FIELDS unchanged

**Validates**: SC-005, FR-007

```bash
python3 -c "
import subprocess, sys
# Confirm CSV_FIELDS in the modified file matches the committed version
result = subprocess.run(['git', 'diff', 'HEAD', 'scripts/scrape-trackdatacenters-proposals.py'],
                       capture_output=True, text=True)
diff = result.stdout
if 'CSV_FIELDS' in diff:
    lines = [l for l in diff.splitlines() if 'CSV_FIELDS' in l]
    print('CSV_FIELDS lines in diff:')
    for l in lines: print(' ', l)
    sys.exit(1)
print('CSV_FIELDS unchanged in diff')
"
```

**Expected**: `CSV_FIELDS unchanged in diff` — no lines touching `CSV_FIELDS` in the diff.

---

## Full pre-commit checklist

Run these in order before staging the commit:

```bash
python3 scripts/scrape-trackdatacenters-proposals.py --selftest   # must exit 0
python3 leak_audit.py --tier blocking                              # must exit 0
python3 layer_audit.py                                             # must exit 0
```
