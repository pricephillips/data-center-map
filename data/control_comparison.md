# Opposed vs. Matched Controls — Descriptive Comparison

Generated 2026-10-02 by `control_comparison.py`. All figures re-derived from the current CSVs at generation time.

**This report is descriptive and diagnostic only.** Differences shown here are associations in an observational, selection-affected sample. Nothing in this document quantifies the effect or cost of opposition, and no figure here should appear in a client-facing deliverable.

## 1. Sample composition

- Opposed projects (treatment side): **224**, of which 93 decided / 131 pending
- Eligible control pool: **1391** — proposals_unopposed: 122, ai_centers: 16, atlas: 1253
- Excluded from control pool: **354** — county_shared_with_opposed_project: 279, within_15km_of_opposed_project: 70, no_coordinates: 5
- Matched: **224** opposed projects × k=3 → 672 match rows

## 2. Covariate balance (opposed vs. their matched controls)

Standardized mean differences across match rows. |SMD| < 0.10 = well balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.

**all tiers** (672 match rows)
- County 2024 margin: opposed mean -0.120, control mean -0.124, SMD 0.013 — well balanced (n pairs: 648)
- log10 capacity MW: opposed mean 2.580, control mean 2.270, SMD 0.582 — IMBALANCED — down-weight or re-match (n pairs: 68; capacity is sparse outside the proposals tier)

**proposals_unopposed** (545 match rows)
- County 2024 margin: opposed mean -0.155, control mean -0.156, SMD 0.006 — well balanced (n pairs: 521)
- log10 capacity MW: opposed mean 2.580, control mean 2.270, SMD 0.582 — IMBALANCED — down-weight or re-match (n pairs: 68; capacity is sparse outside the proposals tier)

**ai_centers** — no matches in this tier.

**atlas** (127 match rows)
- County 2024 margin: opposed mean 0.024, control mean 0.011, SMD 0.038 — well balanced (n pairs: 127)
- log10 capacity MW: opposed mean n/a, control mean n/a, SMD n/a — insufficient data (n pairs: 0; capacity is sparse outside the proposals tier)

## 3. Political geography (descriptive)

- Opposed projects sit in counties with mean 2024 margin -0.120 (n=216); the eligible control pool mean is 0.039 (n=1357).
- This is a raw compositional difference between two differently-constructed samples. It describes where tracked opposition occurs; it does not measure any political driver of opposition.

## 4. Outcomes among decided opposed projects

Of **93** decided + opposed projects:
- `advanced_confirmed`: 58 (62%)
- `blocked_confirmed`: 35 (38%)

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
- `national_fallback` matches (no in-state pool): **271**, covering 122 opposed projects. Growing the proposals_unopposed tier is the fix.
- Tier usage across all matches: proposals_unopposed: 545, ai_centers: 0, atlas: 127.

## 7. Limitations (binding)

- "Unopposed" = no opposition recorded in the tracker; absence of evidence, not verified absence.
- The atlas tier is survivorship-biased (built facilities) and lacks capacity data; sensitivity across tiers in §2 exists for exactly this reason.
- Matching balances only observed covariates (political margin, capacity). Unobserved differences (land use context, utility posture, media environment) remain.
- No causal, effect-size, or cost interpretation is supported. See `data/control_group_notes.md`.
