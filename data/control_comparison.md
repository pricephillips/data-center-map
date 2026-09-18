# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-09-18 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **208**, of which 98 decided / 110 pending
- Eligible control pool: **1303** — proposals_unopposed: 68, ai_centers: 16, atlas: 1219
- Excluded from control pool: **347** — county_shared_with_opposed_project: 261, within_15km_of_opposed_project: 82, no_coordinates: 4
- Matched: **208** opposed projects × k=3 → 624 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (624 match rows)
- County 2024 margin: opposed mean -0.125, control mean -0.129, SMD 0.013 — well balanced (n pairs: 594)
- log10 capacity MW: opposed mean 2.697, control mean 2.320, SMD 0.608 — IMBALANCED — down-weight or re-match (n pairs: 46; capacity is sparse outside the proposals tier)

**proposals_unopposed** (492 match rows)
- County 2024 margin: opposed mean -0.149, control mean -0.152, SMD 0.010 — well balanced (n pairs: 467)
- log10 capacity MW: opposed mean 2.695, control mean 2.306, SMD 0.607 — IMBALANCED — down-weight or re-match (n pairs: 42; capacity is sparse outside the proposals tier)

**ai_centers** (4 match rows)
- County 2024 margin: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0)
- log10 capacity MW: opposed mean 2.719, control mean 2.629, SMD 0.897 — IMBALANCED — down-weight or re-match (n pairs: 4; capacity is sparse outside the proposals tier)

**atlas** (128 match rows)
- County 2024 margin: opposed mean -0.036, control mean -0.044, SMD 0.024 — well balanced (n pairs: 127)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.125 (n=198); the eligible control pool mean is 0.050 (n=1274).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **98** decided + opposed projects:
- `advanced_confirmed`: 68 (69%)
- `blocked_confirmed`: 30 (31%)

`restricted_conditional` is a terminal advance carrying binding conditions (conditional-use approval, negotiated concessions, reverting rezoning); it counts on the advanced side of any advanced-vs-blocked split but is tracked separately because the conditions can carry material cost or delay.

Decided means terminal dispositions only; pending and mixed cases are excluded, consistent with the platform's decided-case rule. These shares describe the tracked opposed sample only — they are not block rates for data center projects in general.

## 5. Delay observables (verified decision dates only)

- 20 decided+opposed projects have verified decision dates: announced-to-decision spans -75–2073 days, median 203 days.
- Announced-date precision of these rows: month: 17, day: 3. Month-precision announced dates are floored to the 1st, so those delays carry up to ~30 days of error each.
- `advanced_confirmed` (n=7): -75–972 days, median 423.
- `blocked_confirmed` (n=2): 21–98 days, median 98.
- `pending` (n=11): -11–2073 days, median 131.
- These are raw spans within the opposed sample: NOT opposition-attributable delay (that requires the matched-control comparison at adequate n) and not client-facing.

## 6. Match-quality flags

- `no_shared_covariates` matches (state/tier only): **20** — down-weight or manually review before any use.
- `national_fallback` matches (no in-state pool): **276**, covering 124 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 492, ai_centers: 4, atlas: 128.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
