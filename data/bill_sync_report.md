# Bill sync report

Generated 2026-08-24. Source of stage truth: Open States machine-classified action histories, mapped onto the qc/stage_ladder.csv discipline. Nothing here writes to master_opposition.csv; every row in data/bill_status_review.csv is a human decision.

- Legislative records on the worklist: 414, of which 154 carry a parseable bill identifier
- API calls this run: 114, cache hits: 125
- Lookups matched: 168, not found: 9, errors: 62
- Review rows: 97

| Severity | Flag | Count |
| :-- | :-- | :-- |
| HIGH | milestone_coded_as_enacted | 18 |
| HIGH | recorded_approved_but_terminal_blocked | 4 |
| HIGH | recorded_blocked_but_enacted | 8 |
| LOW | possible_sine_die_unconfirmed | 4 |
| LOW | recorded_status_unclassifiable | 3 |
| MEDIUM | recorded_terminal_but_bill_in_progress | 39 |
| MEDIUM | terminal_disposition_not_yet_recorded | 21 |

HIGH rows are the milestone-coded-as-enacted class and terminal reversals; fix these before any statistic that touches legislative outcomes ships. MEDIUM rows are dispositions the record has not caught up with. possible_sine_die rows are LOW and need a session-calendar check, because Open States emits no sine die action and the flag is inferred from staleness alone.

| Stage reached | Bills |
| :-- | :-- |
| Introduced | 57 |
| Signed into law | 47 |
| Passed one chamber | 25 |
| Failed floor vote | 17 |
| Passed committee only | 8 |
| Passed both chambers | 4 |
| Died in committee | 4 |
| Withdrawn | 3 |
| Vetoed | 3 |

