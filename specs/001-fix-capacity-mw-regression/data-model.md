# Data Model: Fix capacity_mw Regression

## Entities

### 1. `data/proposals.csv` — Primary scraper output

Written by `scripts/scrape-trackdatacenters-proposals.py`. Schema is frozen at `CSV_FIELDS`. No column changes in this feature.

| Column | Type in CSV | Source | Notes |
|--------|------------|--------|-------|
| `id` | int (as str) | API `id` | Primary key |
| `name` | str | API `name` | |
| `type` | str | API `type` | |
| `phase` | str | API `phase` | |
| `status` | str | API `status` | |
| `state` | str | API `state` | |
| `towns` | str | API `municipality.towns` (joined) | |
| `counties` | str | API `municipality.counties` (joined) | |
| `address` | str | API `address` | |
| `lat` | str | API `lat` | |
| `lon` | str | API `lon` | |
| `size_acres` | str | `SOURCE_KEYS['size_acres']` alias | Tries `sizeAcres` then `size_acres` |
| **`capacity_mw`** | str | `SOURCE_KEYS['capacity_mw']` alias | **Tries `capacityMw` then `capacity_mw`** — the fixed column |
| `scale` | str | API `scale` | |
| `date` | str | `SOURCE_KEYS['date']` alias | Tries `dateAnnounced` then `date`; date-filtered |
| `lastUpdated` | str | `SOURCE_KEYS['lastUpdated']` alias | Tries `dateUpdated` then `lastUpdated`; date-filtered |
| `yearOpened` | str | API `yearOpened` | |
| `jobsConstruction` | str | API `jobsConstruction` | |
| `jobsLongTerm` | str | API `jobsLongTerm` | |
| `jobsTotal` | str | API `jobsTotal` | |
| `companies` | str | API `companies` (joined) | |
| `zoningAllowance` | str | API `zoningAllowance` | |
| `landSold` | str | API `landSold` | |
| `bringingOwnEnergy` | str | API `bringingOwnEnergy` | |
| `approx` | str | API `approx` (retired) | Empty when source doesn't send it; not derived |
| `locationTbd` | str | API `locationTbd` (retired) | Empty when source doesn't send it; not derived |
| `locationConfidence` | str | API `locationConfidence` | Replaced `approx`/`locationTbd` |
| `moratoriumExempt` | str | API `moratoriumExempt` | |
| `info` | str | API `info` (newlines stripped) | |
| `createdAt` | str | API `createdAt` | |
| `updatedAt` | str | API `updatedAt` | |

Manual-addition columns appended by `data/proposals_added.csv` (e.g., `outcome_detail`) are preserved but not in `CSV_FIELDS`.

---

### 2. Guard constants (scraper module-level)

| Constant | Current value | Scope |
|----------|-------------|-------|
| `FIELD_LOSS_MIN_PRIOR` | 20 | All fields: skip guard when prior run had fewer than 20 populated rows |
| `FIELD_LOSS_RATIO` | 0.5 | All fields: raise when > 50% of prior population is gone |
| **`CAPACITY_LOSS_RATIO`** | **0.2** | **`capacity_mw` only: raise when > 20% of prior capacity population is gone** |

`CAPACITY_LOSS_RATIO` is the only new symbol introduced by this feature.

---

### 3. SOURCE_KEYS mapping (existing, no change)

```python
SOURCE_KEYS = {
    'size_acres':  ('sizeAcres', 'size_acres'),
    'capacity_mw': ('capacityMw', 'capacity_mw'),   # current name first, legacy fallback
    'date':        ('dateAnnounced', 'date'),
    'lastUpdated': ('dateUpdated', 'lastUpdated'),
}
```

`pick()` returns the first key the record carries with a non-None, non-blank value. `pick_date()` additionally rejects non-datelike values. `capacity_mw` uses `pick()` (not `pick_date()`), so `capacityMw: 0` is kept as `0`.

---

### 4. `assert_field_population()` — extended guard logic

Current flow:
1. Load previous scraped rows from `data/proposals.csv` (manual-added rows filtered out by id)
2. Call `population_violations(prev, new, watched, ratio=FIELD_LOSS_RATIO)` — all watched fields at 50%
3. If hits → raise `SystemExit` (or print warning if `--allow-field-loss`)

Extended flow (this feature):
1. Same as current step 1–3
2. **NEW**: Call `population_violations(prev, new, ['capacity_mw'], min_prior=FIELD_LOSS_MIN_PRIOR, ratio=CAPACITY_LOSS_RATIO)` — capacity only at 20%
3. **NEW**: If capacity hits → raise `SystemExit` with message naming the 20% threshold explicitly (so the operator knows which guard fired and why)

The two checks are independent. The 50% check on `capacity_mw` via `FIELD_LOSS_RATIO` remains in the first call; the second call adds the 20% early-warning guard.

---

### 5. Selftest coverage additions

New named checks in `selftest()`:

| Check label | What it asserts |
|-------------|----------------|
| `"capacity coverage drop of 21% is flagged at 20% threshold"` | `population_violations([{"capacity_mw":"x"}]*100, [{"capacity_mw":"x"}]*79, ["capacity_mw"], ratio=0.2) != []` |
| `"capacity coverage drop of 19% is not flagged at 20% threshold"` | `population_violations([{"capacity_mw":"x"}]*100, [{"capacity_mw":"x"}]*81, ["capacity_mw"], ratio=0.2) == []` |
| `"capacity coverage drop at exactly 20% is not flagged (boundary)"` | `population_violations([{"capacity_mw":"x"}]*100, [{"capacity_mw":"x"}]*80, ["capacity_mw"], ratio=0.2) == []` |

The boundary check (exactly 20%) should NOT flag — `population_violations` fires when `new <= prev * (1.0 - ratio)`, so at exactly 80% remaining (20% gone) it is at the boundary and passes.

---

## Invariants

- `CSV_FIELDS` is immutable; no column additions, removals, or reordering.
- `capacity_mw` is always a string in the CSV (the API may return int `300` or float; `pick()` returns the native type from the API response, and `csv.DictWriter` coerces to string).
- Manual rows in `data/proposals_added.csv` bypass `flatten()` entirely; they are appended verbatim and excluded from the population guard comparison.
- `CAPACITY_LOSS_RATIO` must always be ≤ `FIELD_LOSS_RATIO`; if the tighter threshold fires, the looser one would also fire — but the capacity check runs first, so the error message names the correct reason.
