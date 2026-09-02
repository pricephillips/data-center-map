# Landmark Outcome Model

Generated 2026-09-02. Landmark t0 = announced_date (anchor re-registered 2026-07-28; the original 2026-07-23 opposition anchor was infeasible, see module docstring and data/landmark_diagnostics.md). Features from events in [t0, t0+W] only; days_to_first_opposition right-censored at the window boundary; training frame conditioned on being undecided at t0+W. Selection criterion pre-registered 2026-07-23 and carried over unchanged; candidate windows [30, 60, 90, 120, 180], floors n>=40, blocked>=12, not_blocked>=12.

## Frame coverage

- Opposed projects with an announced_date (eligible to be anchored): 173
- Opposed projects missing an announced_date (excluded; see announce_date_worklist.csv): 15, of which 3 blocked
- Decided, anchored, with a verified day-precision decision date (eligible for a training frame): 29
- Decided and anchored but missing a verified decision date (excluded; see decision_date_worklist.csv): 48, of which 1 blocked
- Pending and anchored (the scoring population once a window is selected): 96

## Per-window gate status

| W (days) | n | blocked | not blocked | gate |
|---|---|---|---|---|
| 30 | 24 | 18 | 6 | INFEASIBLE |
| 60 | 19 | 13 | 6 | INFEASIBLE |
| 90 | 18 | 13 | 5 | INFEASIBLE |
| 120 | 15 | 11 | 4 | INFEASIBLE |
| 180 | 12 | 8 | 4 | INFEASIBLE |

## Result: GATE CLOSED

No candidate window meets the pre-registered floors, so the model was not fit. Under the announcement anchor the binding constraint is decision-date coverage rather than the anchor itself: the announcement-to-decision gap is positive by construction, so survivor conditioning no longer empties the frames the way the opposition anchor did. What limits the frame now is simply how many decided projects carry a verified day-precision decision date.

Two worklists open the gate. data/decision_date_worklist.csv (48 projects) is the primary one: recovering these dates moves decided projects into the training frame. data/announce_date_worklist.csv (15 projects) is secondary: these projects have no announcement date and cannot be anchored at all until one is sourced. The feasibility diagnosis in data/landmark_diagnostics.md identifies which recoveries actually change a frame at the shortest feasible window, and note the finding there that the advanced arm, not the blocked arm, is the binding constraint for feasibility under this anchor. The selection criterion stays locked and is applied unchanged when the floors are met.
