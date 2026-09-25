# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-09-25 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **231**, of which 84 decided / 147 pending
- Eligible control pool: **1309** — proposals_unopposed: 82, ai_centers: 16, atlas: 1211
- Excluded from control pool: **361** — county_shared_with_opposed_project: 289, within_15km_of_opposed_project: 70, no_coordinates: 2
- Matched: **231** opposed projects × k=3 → 693 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (693 match rows)
- County 2024 margin: opposed mean -0.120, control mean -0.126, SMD 0.020 — well balanced (n pairs: 663)
- log10 capacity MW: opposed mean 2.584, control mean 2.313, SMD 0.465 — IMBALANCED — down-weight or re-match (n pairs: 48; capacity is sparse outside the proposals tier)

**proposals_unopposed** (575 match rows)
- County 2024 margin: opposed mean -0.141, control mean -0.146, SMD 0.018 — well balanced (n pairs: 547)
- log10 capacity MW: opposed mean 2.580, control mean 2.304, SMD 0.470 — IMBALANCED — down-weight or re-match (n pairs: 47; capacity is sparse outside the proposals tier)

**ai_centers** (1 match rows)
- County 2024 margin: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0)
- log10 capacity MW: opposed mean 2.778, control mean 2.725, SMD n/a — insufficient data (n pairs: 1; capacity is sparse outside the proposals tier)

**atlas** (117 match rows)
- County 2024 margin: opposed mean -0.019, control mean -0.029, SMD 0.031 — well balanced (n pairs: 116)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.120 (n=221); the eligible control pool mean is 0.046 (n=1279).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **84** decided + opposed projects:
- `advanced_confirmed`: 52 (62%)
- `blocked_confirmed`: 32 (38%)

`restricted_conditional` is a terminal advance carrying binding conditions (conditional-use approval, negotiated concessions, reverting rezoning); it counts on the advanced side of any advanced-vs-blocked split but is tracked separately because the conditions can carry material cost or delay.

Decided means terminal dispositions only; pending and mixed cases are excluded, consistent with the platform's decided-case rule. These shares describe the tracked opposed sample only — they are not block rates for data center projects in general.

## 5. Delay observables (verified decision dates only)

- 21 decided+opposed projects have verified decision dates: announced-to-decision spans -176–779 days, median 111 days.
- Announced-date precision of these rows: month: 21. Month-precision announced dates are floored to the 1st, so those delays carry up to ~30 days of error each.
- `advanced_confirmed` (n=6): -121–779 days, median 206.
- `blocked_confirmed` (n=3): 51–192 days, median 98.
- `pending` (n=12): -176–562 days, median 115.
- These are raw spans within the opposed sample: NOT opposition-attributable delay (that requires the matched-control comparison at adequate n) and not client-facing.

## 6. Match-quality flags

- `no_shared_covariates` matches (state/tier only): **18** — down-weight or manually review before any use.
- `national_fallback` matches (no in-state pool): **373**, covering 178 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 575, ai_centers: 1, atlas: 117.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
