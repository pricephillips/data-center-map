# Bill sync report

Generated 2026-08-31. Source of stage truth: Open States machine-classified action histories, mapped onto the qc/stage_ladder.csv discipline. Nothing here writes to master_opposition.csv; every row in data/bill_status_review.csv is a human decision.

- Legislative records on the worklist: 428, of which 154 carry a parseable bill identifier
- API calls this run: 91, cache hits: 148
- Lookups matched: 171, not found: 12, errors: 56
- Review rows: 95

| Severity | Flag | Count |
| :-- | :-- | :-- |
| HIGH | milestone_coded_as_enacted | 18 |
| HIGH | recorded_approved_but_terminal_blocked | 4 |
| HIGH | recorded_blocked_but_enacted | 8 |
| LOW | possible_sine_die_unconfirmed | 5 |
| LOW | recorded_status_unclassifiable | 3 |
| MEDIUM | recorded_terminal_but_bill_in_progress | 37 |
| MEDIUM | terminal_disposition_not_yet_recorded | 20 |

HIGH rows are the milestone-coded-as-enacted class and terminal reversals; fix these before any statistic that touches legislative outcomes ships. MEDIUM rows are dispositions the record has not caught up with. possible_sine_die rows are LOW and need a session-calendar check, because Open States emits no sine die action and the flag is inferred from staleness alone.

| Stage reached | Bills |
| :-- | :-- |
| Introduced | 58 |
| Signed into law | 48 |
| Passed one chamber | 23 |
| Failed floor vote | 20 |
| Passed committee only | 7 |
| Died in committee | 5 |
| Passed both chambers | 4 |
| Withdrawn | 3 |
| Vetoed | 3 |

