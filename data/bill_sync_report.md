# Bill sync report

Generated 2026-09-07. Source of stage truth: Open States machine-classified action histories, mapped onto the qc/stage_ladder.csv discipline. Nothing here writes to master_opposition.csv; every row in data/bill_status_review.csv is a human decision.

- Legislative records on the worklist: 449, of which 154 carry a parseable bill identifier
- API calls this run: 111, cache hits: 128
- Lookups matched: 152, not found: 6, errors: 81
- Review rows: 91

| Severity | Flag | Count |
| :-- | :-- | :-- |
| HIGH | milestone_coded_as_enacted | 15 |
| HIGH | recorded_approved_but_terminal_blocked | 4 |
| HIGH | recorded_blocked_but_enacted | 8 |
| LOW | possible_sine_die_unconfirmed | 6 |
| LOW | recorded_status_unclassifiable | 3 |
| MEDIUM | recorded_terminal_but_bill_in_progress | 36 |
| MEDIUM | terminal_disposition_not_yet_recorded | 19 |

HIGH rows are the milestone-coded-as-enacted class and terminal reversals; fix these before any statistic that touches legislative outcomes ships. MEDIUM rows are dispositions the record has not caught up with. possible_sine_die rows are LOW and need a session-calendar check, because Open States emits no sine die action and the flag is inferred from staleness alone.

| Stage reached | Bills |
| :-- | :-- |
| Signed into law | 48 |
| Introduced | 44 |
| Passed one chamber | 20 |
| Failed floor vote | 20 |
| Passed committee only | 7 |
| Died in committee | 5 |
| Withdrawn | 3 |
| Vetoed | 3 |
| Passed both chambers | 2 |

