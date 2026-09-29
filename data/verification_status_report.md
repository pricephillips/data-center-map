# Verification status

Source: `master_opposition.csv`
Rows: 4167

| status | rows | counted externally | reaches the feed |
| :-- | --: | :-- | :-- |
| `sourced` | 1965 | yes | yes |
| `sourced_no_url` | 0 | yes | yes |
| `headline_only` | 369 | no | no |
| `incomplete` | 1833 | no | no |

Countable rows: 1965
Held out of the feed build: 2202

Held rows are preserved in `master_opposition.csv` and listed in `data/verification_holdout.csv`. They are recoverable through `untagged_triage.py`, which builds the per-row review worklist. Recovery requires a publisher URL, not a redirect.
