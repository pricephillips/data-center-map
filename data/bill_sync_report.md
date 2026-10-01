# Bill sync report

Generated 2026-10-01. Source of stage truth: Open States machine-classified action histories, mapped onto the qc/stage_ladder.csv discipline. Nothing here writes to master_opposition.csv; every row in data/bill_status_review.csv is a human decision.

- Legislative records on the worklist: 460, of which 154 carry a parseable bill identifier
- API calls this run: 103, cache hits: 136
- Lookups matched: 156, not found: 10, errors: 73
- Review rows: 84

| Severity | Flag | Count |
| :-- | :-- | :-- |
| HIGH | milestone_coded_as_enacted | 13 |
| HIGH | recorded_approved_but_terminal_blocked | 4 |
| HIGH | recorded_blocked_but_enacted | 8 |
| LOW | possible_sine_die_unconfirmed | 4 |
| LOW | recorded_status_unclassifiable | 4 |
| MEDIUM | recorded_terminal_but_bill_in_progress | 27 |
| MEDIUM | terminal_disposition_not_yet_recorded | 24 |

HIGH rows are the milestone-coded-as-enacted class and terminal reversals; fix these before any statistic that touches legislative outcomes ships. MEDIUM rows are dispositions the record has not caught up with. possible_sine_die rows are LOW and need a session-calendar check, because Open States emits no sine die action and the flag is inferred from staleness alone.

| Stage reached | Bills |
| :-- | :-- |
| Signed into law | 50 |
| Introduced | 43 |
| Failed floor vote | 21 |
| Passed one chamber | 14 |
| Passed both chambers | 9 |
| Passed committee only | 7 |
| Died in committee | 5 |
| Withdrawn | 4 |
| Vetoed | 3 |

## Federal bills (Congress.gov)

- Federal legislative records: 43
- Matched: 15, not found: 0, no bill identifier: 34, skipped (no key): 0, errors: 0
- API calls: 15, cache hits: 0

| Stage reached | Bills |
| :-- | :-- |
| Introduced | 9 |
| Passed one chamber | 4 |
| Passed committee only | 2 |

Every federal record is in data/bill_sync_federal.csv, matched or with the reason it is not. Stages follow the same ladder as the state pass: passage in one chamber is Pending.
