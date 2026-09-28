# Data Model: Decision-Date Recovery

**Feature**: `specs/002-decision-date-recovery/` | **Date**: 2026-09-28

---

## Inputs (read-only)

### `data/decision_date_worklist.csv` — Layer E

48 rows, one per project awaiting a decision date.

| Column | Type | Notes |
|--------|------|-------|
| `project_id` | string | `prj_XXX` format |
| `project_name` | string | Join key to `master_opposition.csv` |
| `state` | string | 2-letter state code |
| `county` | string | County name |
| `phase` | string | Project phase label |
| `lifecycle_outcome` | string | e.g., `advanced_confirmed`, `blocked_confirmed` |
| `n_opposition_events` | integer | Count of opposition rows |
| `first_opposition_date` | date string | ISO format |
| `what_to_recover` | string | Describes which date type is missing |

### `master_opposition.csv` — Layer C

Source of record for opposition events. The join target.

| Column | Notes |
|--------|-------|
| `Project Name` | Join key (case-insensitive match to `project_name` in worklist) |
| `Source URL` | URL of the article or document. May be blank. |
| other columns | Not read by this feature |

**Join coverage**: 9 of 48 worklist projects have at least one matching row with a non-blank `Source URL`. 39 have no match or blank URL.

---

## Output (new file, Layer E)

### `data/decision_date_recovery_candidates.csv`

One row per worklist project. All 48 rows present, including non-matches.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `project_id` | string | No | From worklist |
| `project_name` | string | No | From worklist |
| `state` | string | No | From worklist |
| `lifecycle_outcome` | string | No | From worklist |
| `recovered_date` | string | Yes | ISO date (YYYY-MM-DD) or blank if no recovery |
| `method` | string | No | Pattern method name, `no_source_url`, or `no_pattern_match` |
| `source_url` | string | Yes | The URL that was processed; blank if no match in master_opposition |
| `year_only` | string | No | `"true"` or `"false"` — true when method is `year_only_midyear` |

**Method values**:

| Value | Meaning |
|-------|---------|
| `ymd_path` | Full date from URL path segment `/YYYY/MM/DD/` |
| `iso_in_url` | ISO-format date in URL body |
| `compact` | Compact date `YYYYMMDD` in URL |
| `ym_path_midmonth` | Year-month path `/YYYY/MM/`; day imputed to 15 |
| `monthname` | Month name + day + year in URL |
| `dmonthname` | Day + month name + year in URL |
| `year_only_midyear` | Year only in URL; day imputed to July 1 |
| `no_source_url` | Worklist project had no URL in master_opposition join |
| `no_pattern_match` | URL found but no pattern matched |

**`year_only` flag**: `"true"` only when `method == "year_only_midyear"`. Used by reviewer to identify precision-limited candidates that need verification before adoption.

---

## File that MUST NOT be written

### `data/project_decision_dates.csv` — Layer B

Human-maintained source of record for confirmed decision dates. The adopt step (appending a reviewed candidate) is a manual maintainer action, not a code operation. No code in this feature reads or writes this file.

Schema (for reviewer reference only):

| Column | Notes |
|--------|-------|
| `project_id` | Matches `prj_XXX` in worklist |
| `decision_date` | ISO date |
| `decision_date_source` | Label for source type |
| `source_url` | URL backing the date |
| `note` | Free text |

---

## State Transitions

```
decision_date_worklist.csv (48 rows)
          |
          | --decision-dates pass
          v
   master_opposition.csv
   (join by project_name)
          |
          | 9/48 match → source_url collected
          | 39/48 no match → source_url = ""
          v
   recover_from_url(source_url)
          |
          | pattern hit → recovered_date + method
          | no hit → recovered_date = "", method = "no_pattern_match"
          | no url → method = "no_source_url"
          v
decision_date_recovery_candidates.csv (48 rows, all projects)
          |
          | [HUMAN REVIEW — out of code scope]
          v
project_decision_dates.csv (append by maintainer)
```
