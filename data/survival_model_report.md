# Time-to-Decision Survival Model — First Iteration (Phase 3)

Generated 2026-07-15 by `survival_model.py`. Figures re-derived from current CSVs at generation time.

**Internal diagnostic only — NOT client-facing.** Small sample, retrospective, predictive-not-causal. Hazard ratios describe association with the RATE of reaching a decision, not causes of it.

## Sample and censoring

- Opposed projects in the model: **92** (21 reached a terminal decision = events; 71 still pending = right-censored).
- Of the 21 events: 4 `advanced_confirmed`, 17 `blocked_confirmed`.
- Time axis is announced→decision in days. Censored projects are observed to their last known activity date (last opposition event or status update).
- 53 opposed projects were EXCLUDED from the time axis because their announcement date is only year-precision (too coarse to floor without fabricating months).
- Month-precision announcement dates (floored to the 1st) carry up to ~30 days of error each.

## 1. Kaplan-Meier: time to a terminal decision

- Median not reached within observed follow-up (more than half of opposed projects remain pending at their last-observed time) — itself an informative result about how long opposition-linked projects stay unresolved.
- Median time to a `advanced` decision: not reached.
- Median time to a `blocked` decision: not reached.
- Log-rank test (blocked vs advanced timing, decided subset): p = 0.082. Suggestive of different timing.

Full KM table (time, survival, at-risk, events) is in `survival_km_curve.csv`.

## 2. Cox proportional-hazards model

WITHHELD: only 21 events (< 25 minimum). A Cox model on this few events would be unstable; KM above is the defensible summary until more decisions resolve.

## Limitations (binding)

- 21 events is a small basis for survival estimates; treat all numbers as provisional and interval-wide.
- Censored projects' eventual direction is unknown; by-direction KM curves estimate time-to-that-direction treating other outcomes as censored, which is standard but assumes non-informative censoring.
- Announced→decision spans are raw durations within the opposed sample, NOT opposition-attributable delay (that needs the matched controls at adequate n).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate.
