# Pending: data quality schemas and regression gates in CI (spec 005)

`docs/pending_data_quality.patch` holds every `.github/workflows/` change for
spec 005. It is written, passes actionlint, and applies cleanly to the branch
head it was built on. Per the constitution, CI changes that cannot be pushed
from a sandbox are staged here with this note, as `pending_ci_hygiene` was for
spec 004.

Apply from the repo root:

```bash
git apply --check docs/pending_data_quality.patch && git apply docs/pending_data_quality.patch
```

## What it changes

### `pipeline.yml`

1. **Installs `pandera`**, pinned at 0.33.1 through `requirements/ci.txt`.
   Until this lands, `qc/schemas.py --selftest` prints `SKIP (pandera not
   installed)` and exits 0, so the blocking discovery run stays green. After
   it lands, the schema selftest is blocking like every other selftest.
2. **Adds three steps before "Dataset barriers"**, after every generator, so
   they read this run's outputs:
   - **Label disagreement audit.** Runs only behind a green county layer.
     Writes `data/label_disagreement_worklist.csv`. A review list only.
   - **Data quality schemas** (`python qc/schemas.py`). Writes
     `qc/schema_report.md` and appends to `data/schema_run_history.csv`.
   - **Coverage delta** (`python qc/coverage_delta.py`). Writes
     `data/coverage_delta_report.md` and appends to
     `snapshots/coverage_profiles.csv`.

   Each step has `continue-on-error: true`, like the other fenced steps.
3. **Stages the outputs.** The schema report, run history, coverage report
   and profile store are staged on every run, failing runs included: the
   history is what counts consecutive clean runs. The worklist is staged only
   on a green audit step.
4. **Adds two surface steps at the end**, "Surface schema failure" and
   "Surface coverage-delta failure". They turn the run red after the feed has
   been committed, the same pattern as the county-layer and screener surface
   steps.

### `fetch-features.yml`

- Stages `data/features/*.md`, which carries the new
  `political_parity.md`, and adds MEDSL to the commit message.
- The existing `data/features/*.csv` glob already covers `political.csv` and
  `political_parity.csv`.
- `fetch_county_features.py` builds the `political` source on the next
  scheduled run with no workflow change needed. This patch only makes sure
  the parity report is committed with it.

## When the schema starts blocking

The flag is `configs/data_quality.json` `schema.mode`:

- `window`, the default: reports only until seven consecutive clean runs are
  in `data/schema_run_history.csv`, then blocks. The first blocking run's date
  is written to the history as the switch date and shown in the report.
- `report_only`: never blocks.
- `blocking`: blocks now. Set `switch_date` when flipping by hand.

Re-runs on identical inputs do not count toward the seven.

## What to check after applying

- **First pipeline run.** `qc/schema_report.md` should show 0 failures. The 9
  full-name State values in the committed feed are fixed at source by this
  branch (`clean_opposition_data.py` backfill plus `extract_state`), so the
  rebuilt feed carries codes or blanks. The report shows 3 declared exception
  groups:
  - 134 blank-State harvest rows (131 before, plus Washington County,
    Delaware County and Port Washington, which no longer get a guessed state);
  - 55 federal `US` rows;
  - 7 proposals the source publishes at 0,0.
- **`data/coverage_delta_report.md`** compares against the profiles seeded on
  this branch. Expect 0 failures.
- **Next `fetch-features.yml` run.** Read `data/features/political_parity.md`
  and the `political` entry in `data/features/features_manifest.json`. That
  entry holds the DOI, dataset version, md5 and the license name and terms
  Dataverse returned. The choropleth and the model keep reading
  `data/county_votes.json` until that review is done.
