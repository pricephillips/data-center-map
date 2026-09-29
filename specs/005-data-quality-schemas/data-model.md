# Data Model: Data Quality Schemas and Regression Gates

Phase 1 for [plan.md](plan.md). The formats are fixed in
[contracts/outputs.md](contracts/outputs.md) and
[contracts/data-quality-config.md](contracts/data-quality-config.md).

## 1. Schemas (`qc/schemas.py`)

Each schema is a named Pandera `DataFrameSchema` with a table spec. The table
spec gives a path, a row-id rule and a Layer. Files are read as `str` with
`keep_default_na=False`, and numeric columns are parsed before validation, so
a blank is a null and never a string `"nan"`.

### 1a. `clean_feed`: `master_opposition_clean.csv` (Layer C)

| Check id | Column(s) | Rule |
|----------|-----------|------|
| `outcome_grade` | `outcome_defensible` | in `outcome_defensibility.OUTCOME_GRADES`, not null |
| `state_normalized` | `State` | in `schema_adapter.STATE_ABBREV` keys (two-letter), not null |
| `lat_bounds` | `lat` | null, or 17 <= lat <= 72 |
| `lon_bounds` | `lon` | null, or -180 <= lon <= -64, or 172 <= lon <= 180 (Aleutians) |
| `not_null_island` | `lat`, `lon` | not both within 0.001 of 0 |
| `coords_paired` | `lat`, `lon` | both present or both null |
| `bool_token` | every `is_*` column | lower-cased value in `{true, false, 1, 0}` |
| `fips_frame` | `fips` (when present) | five digits and in the county frame |

**Row id**: `r<line>` for the 1-based data line, plus `row_key`, the first 10
hex characters of sha1(`Source URL|Incident|Date|State`). `project_id` is
included when present. The line number locates a row, and `row_key` survives
reordering.

### 1b. `county_scores`: `data/county_policy_scores.csv` (Layer D)

| Check id | Column(s) | Rule |
|----------|-----------|------|
| `fips_format` | `fips` | matches `^\d{5}$` |
| `fips_frame` | `fips` | in `data/county_census_features.csv` fips (the model frame) |
| `fips_unique` | `fips` | unique |
| `score_unit` | `calibrated_score`, `raw_oof_score` | 0 <= x <= 1, not null |
| `decile_range` | `score_decile` | integer 1..10 |
| `label_binary` | `has_enacted_restrictive` | in {0, 1} |

**Row id**: `fips`.

The county frame is `data/county_census_features.csv`. That is the frame
`county_aggregator.load_frame()` reads, and it has no retired Connecticut
FIPS. A retired code (09001-09015) therefore fails `fips_frame` by
construction.

### 1c. `proposals`: `data/proposals.csv` (Layer B)

| Check id | Column(s) | Rule |
|----------|-----------|------|
| `columns_present` | (frame) | every column in `schema.proposals_columns` (config) exists |
| `id_unique` | `id` | unique, not null |
| `id_keyed` | `id` | resolves to an `active` row of `data/project_key_map.csv` (by `current_id`) |
| `state_normalizable` | `state` | `normalize_state(state)` is not empty |
| `lat_bounds`, `lon_bounds`, `coords_paired`, `not_null_island` | `lat`, `lon` | as 1a |
| `capacity_nonneg` | `capacity_mw` | null, or numeric >= 0 |

**Row id**: the `pk` from `data/project_key_map.csv`, with `id` alongside.
The ids are read through the key map, per the "Changes on main" note.

### Failure record

`{schema, check, column, row_id, row_key, value, allowed}`. Here `allowed` is
the reason string of the matching exception rule, or empty.

### Exception rules

These are declared in `configs/data_quality.json` under
`schema.allowed_exceptions`. Each rule is `{schema, check, when: {col:
value, ...}, reason}`. A failure is *allowed* when every `when` pair matches
its row exactly. Allowed failures are listed in the report and never count
toward `failures`.

### Run history row (`data/schema_run_history.csv`)

`run_utc, input_sha256, mode_config, mode_effective, failures, allowed,
clean, consecutive_clean, switch_date`

- `clean` is 1 when `failures == 0`.
- `consecutive_clean` counts back from and including this row.
- `switch_date` is set on the first row whose `mode_effective` is `blocking`,
  and carried forward after that.
- An identical `input_sha256` on consecutive runs does not append a new row.
  This follows the manifest's dedupe rule, so re-running on unchanged data
  cannot count toward the window.

### Mode state transitions

```text
config.mode = report_only  -> effective report_only (never blocks)
config.mode = blocking     -> effective blocking
config.mode = window:
    consecutive_clean(before this run) >= window_clean_runs  -> effective blocking
    else                                                     -> effective report_only
```

Once a window-mode run is effective blocking, it stays blocking. The history's
`switch_date` is sticky, so later failures fail the gate (acceptance 2) rather
than reopening the window.

## 2. State normalizer (`qc/schema_adapter.py`)

- `STATE_ABBREV`: code to full name (existing, unchanged).
- `STATE_VARIANTS`: extra lower-cased aliases to codes, in AP style (`ala.`,
  `ariz.`, `calif.`, `penn.`, `va.`, `wash.`, `d.c.`, ...).
- `NOT_A_STATE`: `{"us", "usa", "u.s.", "u.s.a.", "united states", "national",
  "federal", "nationwide"}`.
- `normalize_state(value, review=None) -> str`. The review reasons are
  `not_a_state` and `unknown`.

Nothing in `normalize_record()` changes: it still expands codes to full names
for the gate's internal checks. The new function is additive.

## 3. Coverage profiles (`qc/coverage_delta.py`)

- **Profile**: `{file, sha256, date, rows, columns: {name: non_null}}`.
- **Store row** (`snapshots/coverage_profiles.csv`): `date, sha256, file,
  rows, column, non_null`, one row per column.
- **Delta finding**: `{file, column, before, after, drop_share, threshold,
  status}`. Here `status` is `fail` when `before > 0` and `(before - after) /
  before > threshold`. It is `warn` when a declared column is missing from
  the current file, and `ok` otherwise.
- **Outlier finding**: `{file, column, row_id, value, robust_z}`, review
  only.

Thresholds are declared per file and column, with `*` as the per-file
default. A column not declared under a file that has no `*` is profiled but
not gated.

## 4. Label disagreement worklist (`label_disagreement_audit.py`)

One row per flagged county:

`rank, fips, county_name, state, label, calibrated_score, raw_oof_score,
score_decile, rule, disagreement, label_provenance, n_evidence_rows,
evidence`

- `rule` is `positive_bottom_decile` or `negative_top_1pct`.
- `disagreement` is `|label - calibrated_score|`.
- `evidence` holds up to 5 items as `Date | Opposition Type | Status | Source
  URL`, separated by ` ;; `. Negatives say `no enacted restrictive row`.

## 5. Political features (`data/features/political.csv`, Layer D)

This file covers the county frame of `fetch_county_features.load_frame()`:
fips, not Puerto Rico. Columns, for Y in the configured years:

`fips, dem_share_2p_{Y}, margin_dr_{Y}, total_votes_{Y}, votes_per_pop_{Y},
geo_basis_{Y}, dem_share_2p_chg_2016_2020, dem_share_2p_chg_2020_2024,
total_votes_pct_chg_2016_2020, total_votes_pct_chg_2020_2024, source`

Parity (`data/features/political_parity.csv`):

`fips, year, medsl_margin_dr, county_votes_margin, abs_diff, flag, geo_basis`

Manifest entry `sources.political.info`:

`{doi, dataset_version, file_id, file_name, md5, license_name, license_uri,
terms_of_use, years, parity: {year: {n, median_abs_diff, p95_abs_diff,
n_flagged}}}`
