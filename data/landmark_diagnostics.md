# Landmark Diagnostics: Anchor Feasibility

Generated 2026-07-28. Diagnostic only. landmark_model.py is unmodified; its formulation remains registered as of 2026-07-23 with the landmark anchored at first opposition date. Survivor conditioning, floors (n >= 40, blocked >= 12, not_blocked >= 12), and the registered window grid [30, 60, 90, 120, 180] are imported from that module rather than restated.

## Finding

The gate closure has been attributed to decision-date coverage. That is not the binding constraint. Under the registered opposition anchor the gate cannot be opened by recovering decision dates at all, and the reason is a property of the event data rather than a coverage gap. An announcement-anchored landmark does not have the problem, and against that anchor the existing 54-project recovery worklist is exactly what unlocks the model.

## 1. Opposition anchor: the ceiling is below the floor

Frame inputs: 31 decided projects with a verified decision date, 54 decided projects missing one, 99 pending.

The ceiling column is the frame that would exist if every missing decision date were recovered. It is an upper bound: a project with an unknown decision date can only survive window W if its anchor is more than W days before today, since the decision must have already happened.

| W | now n | now blocked | ceiling n | ceiling blocked | blocked floor met at ceiling |
| :-- | :-- | :-- | :-- | :-- | :-- |
| 30 | 7 | 5 | 60 | 7 | no |
| 60 | 4 | 3 | 55 | 5 | no |
| 90 | 2 | 2 | 51 | 4 | no |
| 120 | 2 | 2 | 47 | 4 | no |
| 180 | 2 | 2 | 37 | 3 | no |

The blocked arm ceiling peaks at 7 against a floor of 12. Recovering all 54 dates does not close that gap, because the worklist is 52 advanced and only 2 blocked. Blocked projects already carry verified decision dates at a far higher rate, which is a known structural asymmetry in this dataset, so the arm that binds is the arm recovery cannot help.

## 2. Why the opposition anchor collapses

Anchor-to-decision gaps across the 31 decided projects with dates: median 0 days, range -394 to 232. 20 of 31 (0.645) are non-positive, and 13 are exactly zero.

A non-positive gap means the first recorded opposition event is dated at or after the terminal decision, so no window can contain pre-decision information and survivor conditioning removes the project from every frame. The cause is visible in the event counts: 24 of 31 decided projects have exactly one linked opposition event. Coverage is triggered by the decision, one story is recorded, and the opposition and the outcome share a date.

This is a measurement property, not a claim that opposition began on the day of the decision. It is the same detection limit the verified-negative audit ran into from the other direction.

### The outcome-typed-event test

The registered frame rules exclude project_withdrawal and permit_denial from features at every window because they encode the label. Extending that rule to the anchor is a reasonable reading, so it was tested: recomputing t0 from non-outcome-typed events only. Result at W = 30, the most favorable window: n falls from 7 to 4, and 5 decided projects lose their anchor entirely because every event linked to them is outcome-typed.

So the extension makes the frame smaller, not cleaner, and the zero-gap pattern is not mostly an artifact of denial events being coded as opposition. Recommend leaving the registered rule as written. Recording the negative result matters more than the result itself: it closes off the cheap explanation.

## 3. Announcement anchor

Setting t0 = announced_date. Available for 29 of 31 decided projects with decision dates, 46 of 54 on the worklist, and 94 of 99 pending.

Announcement-to-decision gaps: median 156 days, range 6 to 492, with 0 non-positive. The anchor precedes the decision by construction, which is the property the opposition anchor lacks.

| W | now n | now blocked | now not_blocked | ceiling n | ceiling blocked | ceiling not_blocked | all floors met at ceiling | pending scoreable |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| 30 | 24 | 18 | 6 | 70 | 19 | 51 | yes | 93 |
| 60 | 19 | 13 | 6 | 65 | 14 | 51 | yes | 91 |
| 90 | 18 | 13 | 5 | 64 | 14 | 50 | yes | 87 |
| 120 | 15 | 11 | 4 | 61 | 12 | 49 | yes | 83 |
| 180 | 12 | 8 | 4 | 57 | 9 | 48 | no | 66 |
| 270 (exploratory) | 7 | 4 | 3 | 41 | 4 | 37 | no | 43 |
| 365 (exploratory) | 3 | 2 | 1 | 32 | 2 | 30 | no | 32 |

Windows whose ceiling clears all three floors: 30, 60, 90, 120. Applying the registered tie-breaking preference for the shortest window would select W = 30, but note that the registered criterion selects on cross-validated AUC among feasible windows, which cannot be evaluated until the frame actually exists. The window named here is the shortest FEASIBLE one, not a selected model.

At W = 30 the ceiling is n = 70 with 19 blocked and 51 not blocked, and 93 of 99 pending projects would be scoreable. Making pending projects scoreable was the purpose of the landmark formulation, so that last number is the one to weigh against the recovery cost.

What binds, precisely. With zero recovery the announcement anchor at W = 30 already has 18 blocked survivors against a floor of 12, so the blocked arm is not the problem. The binding constraints are total n (24 now, floor 40) and the not_blocked arm (6 now, floor 12), and both are filled by the advanced-arm recoveries. Of the 54 worklist projects, 46 can enter the W = 30 frame once dated, 45 of them advanced. This inverts the usual recovery priority: here the advanced arm is the arm that opens the gate, so advanced-arm dates are worth as much as blocked ones for feasibility, even though blocked rows remain first for outcome-statistic integrity elsewhere.

## Recommendation

Two steps, in order.

**Re-register the landmark anchor.** The opposition anchor is not reachable with the current event data and no amount of decision-date recovery changes that. Proposed registration text, to be dated when adopted:

> Landmark anchor: t0 = announced_date. Rationale: the opposition-anchored formulation registered 2026-07-23 is infeasible because the first recorded opposition event is dated at or after the terminal decision for a majority of decided projects, a detection property of news-triggered coverage rather than a coverage gap, so survivor conditioning empties every candidate frame and the blocked-arm ceiling under complete decision-date recovery sits below floor. The announcement anchor precedes the terminal decision by construction. Candidate windows, floors, survivor conditioning, selection criterion, and the no-auto-promotion rule are unchanged. Windows beyond the registered grid are not adopted without a further registration entry.

**Then work the decision-date worklist.** Against the announcement anchor it is no longer a housekeeping task; it is the single input that moves the gate. data/landmark_recovery_priority.csv ranks the 54 projects by whether recovering each one actually changes a frame, with blocked-arm rows first.

What this pass does not claim: that the announcement-anchored model will be any good. Feasibility is a counting result. Discrimination, calibration, and the Phase 5 promotion gate are all downstream and none of them are prejudged here. A feasible frame is permission to fit, not evidence of fit.

## Standing rules observed

No decision date invented anywhere; the ceiling is an upper bound derived from elapsed time only. Registered specifications diagnosed, not edited. Decided means terminal dispositions only. No scorekeeping vocabulary. No em-dashes. Nothing written to any source-of-truth file.

