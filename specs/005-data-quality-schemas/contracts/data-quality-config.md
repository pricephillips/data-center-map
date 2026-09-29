# Contract: `configs/data_quality.json`

The file is hand-edited, and no process writes it. `qc/schemas.py`,
`qc/coverage_delta.py` and `label_disagreement_audit.py` read it. If the file
or a key is missing, each module falls back to its built-in default and says
so in its report.

```json
{
  "_comment": ["..."],
  "schema": {
    "mode": "window",
    "window_clean_runs": 7,
    "switch_date": null,
    "proposals_columns": ["id", "name", "...every column the scraper writes..."],
    "allowed_exceptions": [
      {"schema": "clean_feed", "check": "state_normalized",
       "when": {"State": "US", "Scope": "federal"},
       "reason": "national records; no single state"},
      {"schema": "clean_feed", "check": "state_normalized",
       "when": {"State": "", "data_source": "signal_harvest_auto"},
       "reason": "harvested headline did not name a state; awaiting triage"}
    ]
  },
  "coverage_delta": {
    "default_threshold": 0.20,
    "files": {
      "master_opposition_clean.csv": {"*": 0.20},
      "data/proposals.csv": {"*": 0.20, "capacity_mw": 0.20}
    },
    "robust_z": {
      "threshold": 6.0,
      "columns": {
        "master_opposition_clean.csv": ["Megawatts", "Investment Million USD", "Acreage"]
      }
    }
  },
  "label_disagreement": {
    "positive_quantile": 0.10,
    "negative_quantile": 0.99,
    "max_evidence": 5
  }
}
```

Rules:

- `schema.mode` is one of `report_only`, `window`, `blocking`. Any other
  value is treated as `report_only`, and the report says so.
- `switch_date` is `YYYY-MM-DD` or null. Set it when flipping to `blocking`
  by hand.
- Adding a category is a config edit, per the spec's edge case. For a new
  exception, add a rule. The outcome grades and the state set are not
  declared here (FR-002). They come from `outcome_defensibility.OUTCOME_GRADES`
  and `schema_adapter.STATE_ABBREV`.
- Thresholds are shares in [0, 1]. A column-level key overrides `*`.
