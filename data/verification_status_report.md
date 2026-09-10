# Verification status

Source: `master_opposition.csv`
Rows: 6533

| status | rows | counted externally | reaches the feed |
| :-- | --: | :-- | :-- |
| `sourced` | 1919 | yes | yes |
| `sourced_no_url` | 0 | yes | yes |
| `headline_only` | 369 | no | no |
| `incomplete` | 4245 | no | no |

Countable rows: 1919
Held out of the feed build: 4614

Held rows are preserved in `master_opposition.csv` and listed in `data/verification_holdout.csv`. They are recoverable through `untagged_triage.py`, which builds the per-row review worklist. Recovery requires a publisher URL, not a redirect.
