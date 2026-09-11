# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-09-11 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **189**, of which 88 decided / 101 pending
- Eligible control pool: **1382** — proposals_unopposed: 79, ai_centers: 18, atlas: 1285
- Excluded from control pool: **273** — county_shared_with_opposed_project: 209, within_15km_of_opposed_project: 59, no_coordinates: 5
- Matched: **189** opposed projects × k=3 → 567 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (567 match rows)
- County 2024 margin: opposed mean -0.121, control mean -0.125, SMD 0.013 — well balanced (n pairs: 549)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

**proposals_unopposed** (454 match rows)
- County 2024 margin: opposed mean -0.141, control mean -0.143, SMD 0.006 — well balanced (n pairs: 437)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

**ai_centers** — no matches in this tier.

**atlas** (113 match rows)
- County 2024 margin: opposed mean -0.044, control mean -0.057, SMD 0.039 — well balanced (n pairs: 112)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.121 (n=183); the eligible control pool mean is 0.045 (n=1351).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **88** decided + opposed projects:
- `advanced_confirmed`: 62 (70%)
- `blocked_confirmed`: 26 (30%)

`restricted_conditional` is a terminal advance carrying binding conditions (conditional-use approval, negotiated concessions, reverting rezoning); it counts on the advanced side of any advanced-vs-blocked split but is tracked separately because the conditions can carry material cost or delay.

Decided means terminal dispositions only; pending and mixed cases are excluded, consistent with the platform's decided-case rule. These shares describe the tracked opposed sample only — they are not block rates for data center projects in general.

## 5. Delay observables (verified decision dates only)

Only 3 projects have verified decision dates with computable delay; distributional statistics are withheld below n=5. Grow via the date-recovery worklist.

## 6. Match-quality flags

- `no_shared_covariates` matches (state/tier only): **18** — down-weight or manually review before any use.
- `national_fallback` matches (no in-state pool): **248**, covering 122 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 454, ai_centers: 0, atlas: 113.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
