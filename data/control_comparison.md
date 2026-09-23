# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-09-23 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **212**, of which 72 decided / 140 pending
- Eligible control pool: **1356** — proposals_unopposed: 89, ai_centers: 17, atlas: 1250
- Excluded from control pool: **307** — county_shared_with_opposed_project: 239, within_15km_of_opposed_project: 66, no_coordinates: 2
- Matched: **212** opposed projects × k=3 → 636 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (636 match rows)
- County 2024 margin: opposed mean -0.122, control mean -0.130, SMD 0.024 — well balanced (n pairs: 603)
- log10 capacity MW: opposed mean 2.686, control mean 2.484, SMD 0.317 — IMBALANCED — down-weight or re-match (n pairs: 37; capacity is sparse outside the proposals tier)

**proposals_unopposed** (536 match rows)
- County 2024 margin: opposed mean -0.150, control mean -0.157, SMD 0.022 — well balanced (n pairs: 505)
- log10 capacity MW: opposed mean 2.684, control mean 2.477, SMD 0.320 — IMBALANCED — down-weight or re-match (n pairs: 36; capacity is sparse outside the proposals tier)

**ai_centers** (1 match rows)
- County 2024 margin: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0)
- log10 capacity MW: opposed mean 2.778, control mean 2.725, SMD n/a — insufficient data (n pairs: 1; capacity is sparse outside the proposals tier)

**atlas** (99 match rows)
- County 2024 margin: opposed mean 0.021, control mean 0.009, SMD 0.035 — well balanced (n pairs: 98)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.122 (n=201); the eligible control pool mean is 0.035 (n=1325).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **72** decided + opposed projects:
- `advanced_confirmed`: 44 (61%)
- `blocked_confirmed`: 28 (39%)

`restricted_conditional` is a terminal advance carrying binding conditions (conditional-use approval, negotiated concessions, reverting rezoning); it counts on the advanced side of any advanced-vs-blocked split but is tracked separately because the conditions can carry material cost or delay.

Decided means terminal dispositions only; pending and mixed cases are excluded, consistent with the platform's decided-case rule. These shares describe the tracked opposed sample only — they are not block rates for data center projects in general.

## 5. Delay observables (verified decision dates only)

- 21 decided+opposed projects have verified decision dates: announced-to-decision spans -176–562 days, median 98 days.
- Announced-date precision of these rows: month: 21. Month-precision announced dates are floored to the 1st, so those delays carry up to ~30 days of error each.
- `advanced_confirmed` (n=5): -121–492 days, median 111.
- `blocked_confirmed` (n=3): 51–192 days, median 98.
- `pending` (n=13): -176–562 days, median 79.
- These are raw spans within the opposed sample: NOT opposition-attributable delay (that requires the matched-control comparison at adequate n) and not client-facing.

## 6. Match-quality flags

- `no_shared_covariates` matches (state/tier only): **21** — down-weight or manually review before any use.
- `national_fallback` matches (no in-state pool): **319**, covering 145 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 536, ai_centers: 1, atlas: 99.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
