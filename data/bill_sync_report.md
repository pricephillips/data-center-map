# Bill sync report

Generated 2026-08-17. Source of stage truth: Open States machine-classified action histories, mapped onto the qc/stage_ladder.csv discipline. Nothing here writes to master_opposition.csv; every row in data/bill_status_review.csv is a human decision.

- Legislative records on the worklist: 407, of which 154 carry a parseable bill identifier
- API calls this run: 122, cache hits: 117
- Lookups matched: 165, not found: 8, errors: 66
- Review rows: 96

| Severity | Flag | Count |
| :-- | :-- | :-- |
| HIGH | milestone_coded_as_enacted | 25 |
| HIGH | recorded_approved_but_terminal_blocked | 4 |
| HIGH | recorded_blocked_but_enacted | 7 |
| LOW | possible_sine_die_unconfirmed | 2 |
| LOW | recorded_status_unclassifiable | 3 |
| MEDIUM | recorded_terminal_but_bill_in_progress | 39 |
| MEDIUM | terminal_disposition_not_yet_recorded | 16 |

HIGH rows are the milestone-coded-as-enacted class and terminal reversals; fix these before any statistic that touches legislative outcomes ships. MEDIUM rows are dispositions the record has not caught up with. possible_sine_die rows are LOW and need a session-calendar check, because Open States emits no sine die action and the flag is inferred from staleness alone.

| Stage reached | Bills |
| :-- | :-- |
| Introduced | 62 |
| Signed into law | 41 |
| Passed one chamber | 27 |
| Passed committee only | 11 |
| Failed floor vote | 9 |
| Passed both chambers | 5 |
| Died in committee | 4 |
| Withdrawn | 3 |
| Vetoed | 3 |

