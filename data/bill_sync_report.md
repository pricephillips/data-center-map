# Bill sync report

Generated 2026-08-12. Source of stage truth: Open States machine-classified action histories, mapped onto the qc/stage_ladder.csv discipline. Nothing here writes to master_opposition.csv; every row in data/bill_status_review.csv is a human decision.

- Legislative records on the worklist: 397, of which 154 carry a parseable bill identifier
- API calls this run: 210, cache hits: 29
- Lookups matched: 87, not found: 4, errors: 148
- Review rows: 50

| Severity | Flag | Count |
| :-- | :-- | :-- |
| HIGH | milestone_coded_as_enacted | 13 |
| HIGH | recorded_blocked_but_enacted | 3 |
| LOW | possible_sine_die_unconfirmed | 1 |
| LOW | recorded_status_unclassifiable | 1 |
| MEDIUM | recorded_terminal_but_bill_in_progress | 23 |
| MEDIUM | terminal_disposition_not_yet_recorded | 9 |

HIGH rows are the milestone-coded-as-enacted class and terminal reversals; fix these before any statistic that touches legislative outcomes ships. MEDIUM rows are dispositions the record has not caught up with. possible_sine_die rows are LOW and need a session-calendar check, because Open States emits no sine die action and the flag is inferred from staleness alone.

| Stage reached | Bills |
| :-- | :-- |
| Introduced | 35 |
| Signed into law | 24 |
| Passed one chamber | 14 |
| Passed committee only | 6 |
| Failed floor vote | 5 |
| Passed both chambers | 2 |
| Died in committee | 1 |

