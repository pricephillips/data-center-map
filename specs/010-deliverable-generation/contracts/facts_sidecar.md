# Contract: facts sidecar and the re-derivation check

`outputs/location_reports/<stem>.facts.json` holds:

```json
{
  "fips": "13255",
  "generator": "scripts/render_location_report.py",
  "commit": "<HEAD sha, -dirty if an input is modified>",
  "as_of": "2026-10-02",
  "template": "templates/location_report.docx",
  "template_sha256": "...",
  "client": "",
  "plate": null,
  "inputs": [{"path": "data/county_policy_scores.csv", "sha256": "..."}],
  "facts": [
    {"name": "score.calibrated", "formatted": "0.17", "raw": "0.1717", "rule": "score2",
     "file": "data/county_policy_scores.csv", "sha256": "...",
     "key_column": "fips", "key": "13255", "column": "calibrated_score"},
    {"name": "cases.count", "formatted": "2", "raw": "2", "rule": "count",
     "file": "master_opposition_clean.csv", "sha256": "...",
     "key_column": "fips", "key": "13255", "column": "cases_in_county"}
  ]
}
```

`--verify` passes when, for every fact:

1. the file exists;
2. re-reading `column` at `key` (or re-running the named count filter) and
   re-applying `rule` gives `formatted`;
3. `formatted` appears in the DOCX text.

A changed `sha256` alone is reported as data growth, not a mismatch, as
Principle VI says. Only a value difference is a mismatch.
