# Cost-Translation Layer — Methodology (Phase 4, first iteration)

Generated 2026-07-20 by `cost_translation.py`. **Internal — NOT client-facing**
until (1) delay measurement reaches adequate n, and (2) anchors are
re-verified at publication time. Anchor values age; the registry records
as-of dates.

## What this layer does

It prices observables produced elsewhere in the platform. It does not
estimate opposition's effects. Inputs come from `project_lifecycles.csv`
(verified `days_announced_to_decision`) and, eventually, the outcome/delay
models. Every dollar figure is a range derived from the anchor registry
(`cost_anchors.csv`), where each anchor carries its source, date, and a
confidence flag (`published` vs `assumption`).

## Components

**1. Construction-cost escalation (destroyed value).**
`capex_per_mw x MW x escalation_rate x (delay_months / 12)`
A project delayed pre-construction faces higher build costs when it finally
starts. Anchors: JLL $10-12M/MW standard ($20-25M AI-optimized); T&T/JLL
escalation 5.5-6%/yr.

**2. Carrying cost on deployed capital (destroyed value).**
`deployed_capital x carrying_rate x (delay_months / 12)`
Financing/opportunity cost on capital already committed (land control,
engineering, deposits). `carrying_rate` (7-10%/yr) is an ASSUMPTION anchor —
reference floor is CBRE's Class-A cap rates at 10-yr Treasury +100-150 bps;
set per engagement. Requires a deployed-capital figure; never guessed.

**3. Deferred revenue exposure (upper bound, NOT destroyed value).**
`MW x 1000 kW x rent_per_kw_month x delay_months`
Gross wholesale revenue that arrives later. The economic cost is the time
value of the deferral, not this face amount; it is reported only as a
labeled exposure ceiling. Anchor: CBRE NA primary-market asking rents
$150-235/kW/mo.

**4. Block sunk cost (destroyed value, assumption-based).**
`capex x predevelopment_share`
Predevelopment capital at risk when a project is blocked. The 1-3% share is
an ASSUMPTION with no published benchmark identified; replace with project
actuals whenever available.

## Binding limitations

- Delay inputs currently come from 24 projects with verified decision
  dates, all with month-precision announced dates (up to ~30 days error
  each). No opposition-attributable delay exists yet — that requires the
  matched-control comparison at adequate n. Applying this layer to raw
  announced-to-decision spans prices the SPAN, not opposition's effect.
- Anchors are national/global averages; market-level variation is 25-40%
  (Turner & Townsend). Market-specific anchors should replace these for any
  engagement-grade estimate.
- Two anchors are assumptions (`carrying_rate`, `predevelopment_share`) and
  are flagged as such everywhere they are used.
- Standard vs AI-optimized capex differs >2x; the demo uses the standard
  anchor unless a project is known AI-optimized. Ranges do not capture
  tenant IT fit-out (up to $25M/MW additional, T&T).

## Worked example

A 100 MW standard project delayed 6 months, with $15M deployed:
- Escalation: $27.5M - $36.0M
- Carrying on deployed capital: $525,000 - $750,000 (assumption-based rate)
- Deferred revenue exposure (upper bound, labeled): $90.0M - $141.0M

## Anchor registry

See `data/cost_anchors.csv`. Re-verify all anchors before any external use.
