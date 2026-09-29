# Date hint agreement (SC-003)

Sample: first 30 rows of data/project_decision_dates.csv by project_id.
Rule: a hint agrees when it is within 1 day(s) of the verified decision date (fixed in specs/006-source-durability-dedupe/research.md D10).
Target: at least 80 percent of hints and 24 of 30 rows.

| Measure | Rows |
|---|---|
| Sampled | 30 |
| Fetched | 30 |
| With a hint | 24 |
| Exact day | 7 |
| Within 1 day(s) | 15 |
| Within 3 days | 17 |
| No hint | 6 |

SC-003: not met (15 of 24 hints agree; 15 of 30 rows; needs 24)
