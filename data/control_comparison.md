# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-07-12 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **176**, of which 79 decided / 97 pending
- Eligible control pool: **1460** — proposals_unopposed: 95, ai_centers: 20, atlas: 1345
- Excluded from control pool: **199** — county_shared_with_opposed_project: 158, within_15km_of_opposed_project: 37, no_coordinates: 4
- Matched: **176** opposed projects × k=3 → 528 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (528 match rows)
- County 2024 margin: opposed mean -0.125, control mean -0.128, SMD 0.013 — well balanced (n pairs: 510)
- log10 capacity MW: opposed mean 2.884, control mean 2.641, SMD 0.491 — IMBALANCED — down-weight or re-match (n pairs: 29; capacity is sparse outside the proposals tier)

**proposals_unopposed** (436 match rows)
- County 2024 margin: opposed mean -0.147, control mean -0.149, SMD 0.007 — well balanced (n pairs: 420)
- log10 capacity MW: opposed mean 2.888, control mean 2.638, SMD 0.496 — IMBALANCED — down-weight or re-match (n pairs: 28; capacity is sparse outside the proposals tier)

**ai_centers** (1 match rows)
- County 2024 margin: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0)
- log10 capacity MW: opposed mean 2.778, control mean 2.725, SMD n/a — insufficient data (n pairs: 1; capacity is sparse outside the proposals tier)

**atlas** (91 match rows)
- County 2024 margin: opposed mean -0.021, control mean -0.033, SMD 0.037 — well balanced (n pairs: 90)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.125 (n=170); the eligible control pool mean is 0.035 (n=1427).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **79** decided + opposed projects:
- `advanced_confirmed`: 54 (68%)
- `blocked_confirmed`: 25 (32%)

Decided means terminal dispositions only; pending and mixed cases are excluded, consistent with the platform's decided-case rule. These shares describe the tracked opposed sample only — they are not block rates for data center projects in general.

## 5. Delay observables (verified decision dates only)

- 10 decided+opposed projects have verified decision dates: announced-to-decision spans 12–294 days, median 156 days.
- Announced-date precision of these rows: month: 10. Month-precision announced dates are floored to the 1st, so those delays carry up to ~30 days of error each.
- `advanced_confirmed` (n=3): 75–294 days, median 119.
- `blocked_confirmed` (n=7): 12–205 days, median 156.
- These are raw spans within the opposed sample: NOT opposition-attributable delay (that requires the matched-control comparison at adequate n) and not client-facing.

## 6. Match-quality flags

- `no_shared_covariates` matches (state/tier only): **15** — down-weight or manually review before any use.
- `national_fallback` matches (no in-state pool): **222**, covering 109 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 436, ai_centers: 1, atlas: 91.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
