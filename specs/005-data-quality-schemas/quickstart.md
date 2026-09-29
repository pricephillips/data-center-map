# Quickstart: Validating Spec 005

Run from the repo root with the CI requirements installed:
`pip install -r requirements/ci.txt`, which includes pandera 0.33.1. The
formats are in [contracts/](contracts/), and the decisions and measured
baselines are in [research.md](research.md).

## US1: Schema

```bash
python qc/schemas.py --selftest
# The fixture run has three named failures: state_normalized (US),
# fips_format (4-digit), outcome_grade (out of ladder).
python qc/schemas.py --no-write
# On current main: 0 failures and 186 allowed (131 blank-State harvest rows,
# 55 federal US rows). This assumes the 9 full-name rows were fixed at source
# by a rebuilt feed (see US2). Before the feed is rebuilt, the 9 rows show as
# state_normalized failures.
python qc/schemas.py
# Writes qc/schema_report.md and appends to data/schema_run_history.csv.
# Exit 0 while the effective mode is report_only.
```

Window check, acceptance 2. The selftest drives a temporary history through 7
clean runs followed by one failing run, and asserts that the effective mode is
`blocking` with exit code 1.

## US2: State normalizer

```bash
python qc/schema_adapter.py --selftest
```

The selftest checks the following, among other cases:

- "Virginia", "VA", "va." and "Commonwealth of Virginia" all map to VA.
- "US" maps to "" and lands on the review list with reason `not_a_state`.
- "Kentucky" maps to KY, while `state_bounds.in_state(34.0537, -93.1059,
  "KY")` stays False. That is the prj_61 row, now prj_78.

## US3: Coverage delta

```bash
python qc/coverage_delta.py --selftest
# Replays tests/fixtures/coverage_delta/proposals_2026-09-10.json and must
# report: FAIL data/proposals.csv capacity_mw 123 -> 3.
git show e71c8d9~1:data/proposals.csv > /tmp/before.csv
git show e71c8d9:data/proposals.csv   > /tmp/after.csv
python qc/coverage_delta.py --before /tmp/before.csv --after /tmp/after.csv --file data/proposals.csv
# SC-002 live replay: exit 1, and the line names capacity_mw, 123 and 3.
# This needs history back to 2026-09-03: git fetch --shallow-since=2026-08-15 origin main
python qc/coverage_delta.py --no-write
# On current main: exit 0 against the stored profiles.
```

## US4: Label disagreement

```bash
python label_disagreement_audit.py --selftest
# The synthetic frame with one flipped label ranks that county first.
python label_disagreement_audit.py
# Writes data/label_disagreement_worklist.csv. A review list only.
```

SC-003 replay. This is expected **not** to meet the criterion; see research
D11.

```bash
git show aaf6cb8:data/county_policy_scores.csv > /tmp/pre.csv
git show 57547fc:data/county_policy_scores.csv > /tmp/post.csv
python - <<'EOF'
import csv
pre = {r["fips"]: r for r in csv.DictReader(open("/tmp/pre.csv"))}
post = {r["fips"]: r for r in csv.DictReader(open("/tmp/post.csv"))}
rem = [f for f in pre if pre[f]["has_enacted_restrictive"] == "1"
       and post.get(f, {}).get("has_enacted_restrictive") == "0"]
open("/tmp/removed28.txt", "w").write("\n".join(rem) + "\n")
print(len(rem))
EOF
python label_disagreement_audit.py --scores /tmp/pre.csv --no-evidence --no-write --check-recall /tmp/removed28.txt
# Measured: recall 0 of 28. SC-003 needs 14.
```

Missing scores: `python label_disagreement_audit.py --scores /nonexistent`
prints `SKIP` and exits 0.

## US5: Political source

```bash
python fetch_county_features.py --selftest
# Covers the MEDSL fixture: TOTAL-mode and summed-mode counties, the Kansas
# City fold-in, CT retired-county fallback, CT 2024 planning regions on
# county basis, and AK districts on statewide basis.
python fetch_county_features.py --only political
# In CI only (fetch-features.yml). The sandbox proxy blocks Dataverse. Writes
# data/features/political.csv and the parity files. The manifest records the
# DOI, version, file id, md5 and license.
```

The choropleth and model keep reading `data/county_votes.json`. `political.csv`
is not in `configs/feature_plugins.json`.

## Constitution gates (SC-004)

```bash
python leak_audit.py --tier blocking     # 0 blocking
python layer_audit.py --strict --no-write   # 0 undeclared
python -m pytest tests/test_selftests.py -q  # all pass; the 4 new modules are discovered
pre-commit run --all-files
```
