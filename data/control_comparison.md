# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-09-30 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **208**, of which 88 decided / 120 pending
- Eligible control pool: **1380** — proposals_unopposed: 103, ai_centers: 16, atlas: 1261
- Excluded from control pool: **313** — county_shared_with_opposed_project: 242, within_15km_of_opposed_project: 66, no_coordinates: 5
- Matched: **208** opposed projects × k=3 → 624 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (624 match rows)
- County 2024 margin: opposed mean -0.122, control mean -0.126, SMD 0.015 — well balanced (n pairs: 600)
- log10 capacity MW: opposed mean 2.684, control mean 2.201, SMD 0.928 — IMBALANCED — down-weight or re-match (n pairs: 51; capacity is sparse outside the proposals tier)

**proposals_unopposed** (487 match rows)
- County 2024 margin: opposed mean -0.154, control mean -0.157, SMD 0.010 — well balanced (n pairs: 465)
- log10 capacity MW: opposed mean 2.683, control mean 2.190, SMD 0.941 — IMBALANCED — down-weight or re-match (n pairs: 50; capacity is sparse outside the proposals tier)

**ai_centers** (1 match rows)
- County 2024 margin: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0)
- log10 capacity MW: opposed mean 2.778, control mean 2.725, SMD n/a — insufficient data (n pairs: 1; capacity is sparse outside the proposals tier)

**atlas** (136 match rows)
- County 2024 margin: opposed mean -0.009, control mean -0.020, SMD 0.030 — well balanced (n pairs: 135)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.122 (n=200); the eligible control pool mean is 0.044 (n=1348).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **88** decided + opposed projects:
- `advanced_confirmed`: 58 (66%)
- `blocked_confirmed`: 30 (34%)

`restricted_conditional` is a terminal advance carrying binding conditions (conditional-use approval, negotiated concessions, reverting rezoning); it counts on the advanced side of any advanced-vs-blocked split but is tracked separately because the conditions can carry material cost or delay.

Decided means terminal dispositions only; pending and mixed cases are excluded, consistent with the platform's decided-case rule. These shares describe the tracked opposed sample only — they are not block rates for data center projects in general.

## 5. Delay observables (verified decision dates only)

- 25 decided+opposed projects have verified decision dates: announced-to-decision spans 12–492 days, median 99 days.
- Announced-date precision of these rows: month: 22, day: 3. Month-precision announced dates are floored to the 1st, so those delays carry up to ~30 days of error each.
- `advanced_confirmed` (n=6): 78–492 days, median 294.
- `blocked_confirmed` (n=19): 12–232 days, median 98.
- These are raw spans within the opposed sample: NOT opposition-attributable delay (that requires the matched-control comparison at adequate n) and not client-facing.

## 6. Match-quality flags

- `no_shared_covariates` matches (state/tier only): **18** — down-weight or manually review before any use.
- `national_fallback` matches (no in-state pool): **277**, covering 136 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 487, ai_centers: 1, atlas: 136.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
