# Landmark Outcome Model

Generated 2026-07-24. Landmark t0 = first opposition date; features from events in [t0, t0+W] only; training frame conditioned on being undecided at t0+W. Selection criterion pre-registered 2026-07-23 (see module docstring); candidate windows [30, 60, 90, 120, 180], floors n>=40, blocked>=12, not_blocked>=12.

## Frame coverage

- Decided + opposed projects with a dated first opposition: 83
- With a verified, day-precision decision date (eligible for any landmark frame): 31
- Missing a verified decision date (excluded; see decision_date_worklist.csv): 52, of which 1 blocked
- Pending projects with a dated first opposition (the scoring population once a window is selected): 101

## Per-window gate status

| W (days) | n | blocked | not blocked | gate |
|---|---|---|---|---|
| 30 | 7 | 5 | 2 | INFEASIBLE |
| 60 | 4 | 3 | 1 | INFEASIBLE |
| 90 | 2 | 2 | 0 | INFEASIBLE |
| 120 | 2 | 2 | 0 | INFEASIBLE |
| 180 | 2 | 2 | 0 | INFEASIBLE |

## Result: GATE CLOSED

No candidate window meets the pre-registered floors. The model was not fit. The binding constraint is verified decision-date coverage, not modeling: every decided project lacking a day-precision decision date in data/project_decision_dates.csv is excluded from every frame. The worklist (data/decision_date_worklist.csv) is ordered blocked arm first, then by opposition event count. Recovering decision dates is the gate-opening path; the selection criterion above stays locked and will be applied unchanged when the floors are met.

A second constraint will bind after coverage improves: survivor counts depend on the gap between first opposition and decision. In the current dated subset the median gap is near zero because many projects' only dated opposition event is the decision-adjacent record itself. Denser event dating (not just decision dating) raises survivor counts at every window. This is the same structural asymmetry recorded previously: blocked projects carry verified dates at higher rates, so coverage work must sample both arms to avoid steering the frame.
