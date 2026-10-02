# Time-to-Decision Survival Model — First Iteration (Phase 3)

Generated 2026-10-02 by `survival_model.py`. Figures re-derived from current CSVs at generation time.

**Internal diagnostic only — NOT client-facing.** Small sample, retrospective, predictive-not-causal. Hazard ratios describe association with the RATE of reaching a decision, not causes of it.

## Sample and censoring

- Opposed projects in the model: **111** (25 reached a terminal decision = events; 86 still pending = right-censored).
- Of the 25 events: 6 `advanced_confirmed`, 19 `blocked_confirmed`.
- Time axis is announced→decision in days. Censored projects are observed to their last known activity date (last opposition event or status update).
- 66 opposed projects were EXCLUDED from the time axis because their announcement date is only year-precision (too coarse to floor without fabricating months).
- Month-precision announcement dates (floored to the 1st) carry up to ~30 days of error each.

## 1. Kaplan-Meier: time to a terminal decision

- Median not reached within observed follow-up (more than half of opposed projects remain pending at their last-observed time) — itself an informative result about how long opposition-linked projects stay unresolved.
- Median time to a `advanced` decision: not reached.
- Median time to a `blocked` decision: not reached.
- Log-rank test (blocked vs advanced timing, decided subset): p = 0.031. Suggestive of different timing.

Full KM table (time, survival, at-risk, events) is in `survival_km_curve.csv`.

## 2. Cox proportional-hazards model

L2-penalized Cox (penalizer=0.5, deliberately strong for small n). Hazard ratio (HR) > 1 means the covariate is associated with reaching a decision FASTER; < 1, slower.

- `n_opposition_events`: HR 1.00 (95% CI 0.89–1.13, p=0.97)
- `has_lawsuit`: HR 1.08 (95% CI 0.59–1.96, p=0.81)
- `county_margin_2024`: HR 1.37 (95% CI 0.64–2.90, p=0.42)
- `hyperscaler_or_mechanism_moratorium`: HR 0.93 (95% CI 0.59–1.46, p=0.74)

- Cross-validated concordance index (discrimination): median **0.50** (range 0.50–0.63) across 5-fold CV. 0.50 = chance.

Wide CIs spanning 1.0 mean the direction is not established at this sample size. This is a scaffold that sharpens as decisions accrue.

**Interpretation — the pooled model understates a real signal.** This Cox pools two distinct exit types (blocked and advanced) into a single "reached a decision" event. But the cause-specific Kaplan-Meier medians above show blocked decisions arrive markedly faster than advanced ones. Pooling them means the covariates are asked to explain a mixture of two different timing processes, which depresses discrimination (the near-chance concordance is partly an artifact of this). A cause-specific or competing-risks Cox (separate hazards for block vs advance) is the correct next specification; it is deferred until the advanced-side event count is large enough to fit its own model without overfitting. Until then the cause-specific KM medians, not the pooled hazard ratios, are the defensible timing summary.

## Limitations (binding)

- 25 events is a small basis for survival estimates; treat all numbers as provisional and interval-wide.
- Censored projects' eventual direction is unknown; by-direction KM curves estimate time-to-that-direction treating other outcomes as censored, which is standard but assumes non-informative censoring.
- **Datable-outcome asymmetry (informative-censoring caution).** Among opposed projects that reached a terminal outcome, blocked outcomes are datable far more often than advanced ones: in the current data, 24/35 blocked vs 7/58 advanced carry a verified discrete decision date. This is structural, not a collection gap: a blocked project passes through a discrete denial or withdrawal that gets recorded, whereas an opposed project that advances often proceeds by-right (pre-zoned land, retrofits, incentive agreements) with no contested vote to date. The advanced side of any survival split is therefore both smaller and later-arriving than the true population, which depresses the advanced-cause hazard and is the main reason a cause-specific model is not yet fittable. Treat advanced-side timing as a lower bound on how fast advances actually occur.
- Announced→decision spans are raw durations within the opposed sample, NOT opposition-attributable delay (that needs the matched controls at adequate n).
- Not wired into CI. Automated retraining requires the Phase 5 calibration gate.
