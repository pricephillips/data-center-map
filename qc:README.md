# Master Opposition QC Gate

A single validity filter for the whole opposition database. Every downstream
consumer (the Iowa tracker is one of several) should read the gate's clean
output, not the raw database. Records that fail strict, context-aware tests are
quarantined so they cannot reach any downstream product.

These files live in `data-center-map/qc/`. The workflow file is the one
exception and goes at `.github/workflows/qc.yml`.

## Files (all in data-center-map/qc/)

- `qc_pipeline.py` - the gate. Routes each record to a brain, runs the checks, blocks anything serious, writes the outputs.
- `legislative_outcome.py` - the legislative brain's engine (bill lifecycle, the "passed committee is not Approved" logic).
- `stage_ladder.csv` - the bill stage table the legislative engine reads. Edit this to change how a stage maps to an outcome; no code change needed.
- `fetch_notion.py` - optional. Pulls the Notion database into `records.json` in the shape the gate expects.
- `qc.yml` - optional GitHub Actions workflow. Copy it to `.github/workflows/qc.yml`.

`qc_pipeline.py`, `legislative_outcome.py`, and `stage_ladder.csv` must stay
together in this folder. The pipeline imports the engine by name and the engine
finds the CSV next to itself, so they resolve each other automatically.

## Quick start

Run from inside the qc folder:

```bash
cd data-center-map/qc
python3 qc_pipeline.py --selftest                          # confirm the gate is healthy
python3 qc_pipeline.py                                     # run the built-in demo
python3 qc_pipeline.py --records records.json --out qc_out # run against your data
```

(You can also run from the repo root with `python3 qc/qc_pipeline.py ...`; the
imports still work because the script's own folder is on the path.)

Outputs land in `qc_out/`:

- `clean_export.json` - the only file downstream products should read.
- `quarantine.json` - blocked records, each with the reasons.
- `qc_report.json` / `qc_report.md` - full report and a human summary.

The process exits non-zero when anything is blocked, so CI turns red.

## First run from your CSV (no Notion needed)

```bash
cd data-center-map/qc
python3 -c "import csv,json; json.dump(list(csv.DictReader(open('../master_opposition.csv'))), open('records.json','w'), indent=2)"
python3 qc_pipeline.py --records records.json --out qc_out
```

Adjust the path to `master_opposition.csv` to wherever it lives in the repo.

## Wiring downstream

Point the Iowa tracker and every other consumer at `qc_out/clean_export.json`
(or at records whose QC status is Pass), not at the raw database. That is what
makes a bad record incapable of reaching a memo or a map.

## The brains

Each record is routed by what kind of source it is, then tested with checks
built for that kind, on top of a universal baseline (valid source, sane dates,
valid vocabulary, coordinate sanity, import-artifact detection).

- `legislative` - bill lifecycle, statewide records must not carry local geography, a claim about a bill's fate needs a primary or news source.
- `moratorium` - local-government outcome logic: a moratorium that was adopted is Blocked, one voted down lets development proceed, a county plus city split reads as Mixed.
- `project` - operator, location, and capacity sanity.
- `public_comment` - locality required.
- `study` - studies, reports, polls, and datasets are evidence, not events. They are judged on source quality and attribution, and are not forced to carry an Approved/Blocked outcome.
- `generic` - fallback baseline.

## Severity and blocking

Issues are LOW, MEDIUM, HIGH, or CRITICAL. A record is blocked if any issue is
HIGH or CRITICAL. This threshold is `BLOCK_AT` at the top of `qc_pipeline.py`;
change it in one place to tune strictness.

## Tuning

- Add or change a bill stage: edit `stage_ladder.csv`.
- Recognize more news outlets so legitimate sources are not flagged unverified: extend `NEWS_DOMAINS` in `qc_pipeline.py`.
- Add a check: write a function that takes a record and returns a list of `Issue`, then add it to the relevant brain's check list (or `BASELINE_CHECKS` to run it on everything).
- Make a partial-result mismatch block too: the "recorded Blocked but actually Mixed" case is currently MEDIUM (non-blocking); raise it in `_mor_severity`.

## The one manual piece

`fetch_notion.py` is the only file not exercised in the build. Set
`NOTION_TOKEN` and `NOTION_DATABASE_ID`, run it, and confirm the flattened
property names match what the gate reads (Outcome, Status, County, City, State,
Date, Source URL, Latitude, Longitude, Notes, Opposition Type). Rename
properties in Notion or adjust the flattening if they differ. To run in CI, put
`qc.yml` at `.github/workflows/qc.yml` and add the two secrets.
