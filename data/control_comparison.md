# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-10-01 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **216**, of which 90 decided / 126 pending
- Eligible control pool: **1400** — proposals_unopposed: 129, ai_centers: 16, atlas: 1255
- Excluded from control pool: **353** — county_shared_with_opposed_project: 277, within_15km_of_opposed_project: 71, no_coordinates: 5
- Matched: **216** opposed projects × k=3 → 648 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (648 match rows)
- County 2024 margin: opposed mean -0.116, control mean -0.120, SMD 0.011 — well balanced (n pairs: 624)
- log10 capacity MW: opposed mean 2.595, control mean 2.307, SMD 0.532 — IMBALANCED — down-weight or re-match (n pairs: 69; capacity is sparse outside the proposals tier)

**proposals_unopposed** (530 match rows)
- County 2024 margin: opposed mean -0.154, control mean -0.156, SMD 0.004 — well balanced (n pairs: 506)
- log10 capacity MW: opposed mean 2.595, control mean 2.307, SMD 0.532 — IMBALANCED — down-weight or re-match (n pairs: 69; capacity is sparse outside the proposals tier)

**ai_centers** — no matches in this tier.

**atlas** (118 match rows)
- County 2024 margin: opposed mean 0.046, control mean 0.034, SMD 0.037 — well balanced (n pairs: 118)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.116 (n=208); the eligible control pool mean is 0.037 (n=1366).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **90** decided + opposed projects:
- `advanced_confirmed`: 57 (63%)
- `blocked_confirmed`: 33 (37%)

`restricted_conditional` is a terminal advance carrying binding conditions (conditional-use approval, negotiated concessions, reverting rezoning); it counts on the advanced side of any advanced-vs-blocked split but is tracked separately because the conditions can carry material cost or delay.

Decided means terminal dispositions only; pending and mixed cases are excluded, consistent with the platform's decided-case rule. These shares describe the tracked opposed sample only — they are not block rates for data center projects in general.

## 5. Delay observables (verified decision dates only)

- 25 decided+opposed projects have verified decision dates: announced-to-decision spans 12–492 days, median 99 days.
- Announced-date precision of these rows: month: 22, day: 3. Month-precision announced dates are floored to the 1st, so those delays carry up to ~30 days of error each.
- `advanced_confirmed` (n=6): 78–492 days, median 294.
- `blocked_confirmed` (n=19): 12–232 days, median 98.
- These are raw spans within the opposed sample: NOT opposition-attributable delay (that requires the matched-control comparison at adequate n) and not client-facing.

## 6. Match-quality flags

- `no_shared_covariates` matches (state/tier only): **16** — down-weight or manually review before any use.
- `national_fallback` matches (no in-state pool): **256**, covering 114 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 530, ai_centers: 0, atlas: 118.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
