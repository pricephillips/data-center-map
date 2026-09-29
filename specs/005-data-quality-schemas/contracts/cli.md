# Contract: Command Lines

Every command runs from the repo root. Every module accepts `--selftest`,
which touches no committed file and needs no network.

## `qc/schemas.py`

```text
python qc/schemas.py                 validate all three tables, write report + history
python qc/schemas.py --no-write      validate and print; write nothing
python qc/schemas.py --only clean_feed[,county_scores,proposals]
python qc/schemas.py --selftest
```

Exit codes:

- `0`: no failures, or the effective mode is `report_only`.
- `1`: effective mode is `blocking` and there is at least one non-allowed
  failure.
- `2`: an input file is missing or unreadable. The step is broken, not the
  data.

`--selftest` exits 0 with a `SKIP` line when Pandera is not importable (plan,
Complexity Tracking).

## `qc/coverage_delta.py`

```text
python qc/coverage_delta.py                      profile + compare every declared file, write report, append profiles
python qc/coverage_delta.py --no-write           compare only
python qc/coverage_delta.py --before A.csv --after B.csv [--file data/proposals.csv]
                                                 compare two explicit files under the named file's thresholds
python qc/coverage_delta.py --before-profile P.json --after-profile Q.json [--file ...]
python qc/coverage_delta.py --selftest
```

Exit codes:

- `0`: no declared column dropped past its threshold. Outlier flags never
  fail the run.
- `1`: at least one `fail` finding, including a declared column that
  disappeared. The failure lines name the file, the column, and both counts.
- `2`: an input file is missing.

## `label_disagreement_audit.py`

```text
python label_disagreement_audit.py
python label_disagreement_audit.py --scores PATH [--out PATH] [--no-evidence]
python label_disagreement_audit.py --scores PATH --no-write --check-recall FIPS.txt
python label_disagreement_audit.py --selftest
```

Exit codes: always `0` outside `--selftest`, because the output is a
worklist. When the scores are missing or lack the columns, it prints
`SKIP: ...` and writes nothing. `--check-recall` prints `recall: k of n`,
counting the listed FIPS that appear in the worklist.

## `fetch_county_features.py`

```text
python fetch_county_features.py --only political
```

Needs network access to `dataverse.harvard.edu`. On failure it writes nothing
for this source, records the error in the manifest, and exits nonzero only
when every enabled source failed. This is unchanged behavior.

## `qc/schema_adapter.py`

```text
python qc/schema_adapter.py [CSV]    unchanged demo
python qc/schema_adapter.py --selftest
```
