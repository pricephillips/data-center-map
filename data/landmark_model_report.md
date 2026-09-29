# Landmark Outcome Model

Generated 2026-09-29. Landmark t0 = announced_date (anchor re-registered 2026-07-28; the original 2026-07-23 opposition anchor was infeasible, see module docstring and data/landmark_diagnostics.md). Features from events in [t0, t0+W] only; days_to_first_opposition right-censored at the window boundary; training frame conditioned on being undecided at t0+W. Selection criterion pre-registered 2026-07-23 and carried over unchanged; candidate windows [30, 60, 90, 120, 180], floors n>=40, blocked>=12, not_blocked>=12.

## Frame coverage

- Opposed projects with an announced_date (eligible to be anchored): 191
- Opposed projects missing an announced_date (excluded; see announce_date_worklist.csv): 17, of which 0 blocked
- Decided, anchored, with a verified day-precision decision date (eligible for a training frame): 31
- Decided and anchored but missing a verified decision date (excluded; see decision_date_worklist.csv): 46, of which 6 blocked
- Pending and anchored (the scoring population once a window is selected): 114

## Per-window gate status

| W (days) | n | blocked | not blocked | gate |
|---|---|---|---|---|
| 30 | 28 | 21 | 7 | INFEASIBLE |
| 60 | 23 | 16 | 7 | INFEASIBLE |
| 90 | 22 | 16 | 6 | INFEASIBLE |
| 120 | 17 | 13 | 4 | INFEASIBLE |
| 180 | 14 | 10 | 4 | INFEASIBLE |

## Result: GATE CLOSED

No candidate window meets the pre-registered floors, so the model was not fit. Under the announcement anchor the binding constraint is decision-date coverage rather than the anchor itself: the announcement-to-decision gap is positive by construction, so survivor conditioning no longer empties the frames the way the opposition anchor did. What limits the frame now is simply how many decided projects carry a verified day-precision decision date.

Two worklists open the gate. data/decision_date_worklist.csv (46 projects) is the primary one: recovering these dates moves decided projects into the training frame. data/announce_date_worklist.csv (17 projects) is secondary: these projects have no announcement date and cannot be anchored at all until one is sourced. The feasibility diagnosis in data/landmark_diagnostics.md identifies which recoveries actually change a frame at the shortest feasible window, and note the finding there that the advanced arm, not the blocked arm, is the binding constraint for feasibility under this anchor. The selection criterion stays locked and is applied unchanged when the floors are met.
