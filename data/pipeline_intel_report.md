# Proposed data center pipeline: intelligence report

Generated 2026-09-28 by `proposal_enrichment.py`. Descriptive shares with their denominators. Decided means a terminal lifecycle outcome (blocked_confirmed, restricted_conditional, advanced_confirmed); block rates are observed among those, not effects. Estimated MW is an acreage-based range for projects reporting no capacity and is never added into reported totals.

## Totals

- 396 projects in 28 states. By source phase: 253 pending, 89 advancing, 54 withdrawn or rejected. By verified lifecycle outcome: 142 decided, 53 blocked_confirmed.
- Reported capacity 99,153 MW across 163 projects. Another 154 report acreage but no MW; at the observed MW-per-acre range they would add 59,735 to 235,164 MW (estimate, not included above).
- 208 projects carry recorded opposition.
- 31 projects read "proposed" only because the source folded its "approved" phase into "proposed" on 2026-09-22; they are listed in `source_reclassified_from` and counted as pending until a person decides.

## Outcomes among decided projects (descriptive)

| slice | blocked | decided | share |
|---|---|---|---|
| all decided | 53 | 142 | 37% |
| with opposition | 31 | 77 | 40% |
| without recorded opposition | 22 | 65 | 34% |
| with lawsuit | 4 | 14 | 29% |
| nda reported | 0 | 0 |  |
| top3 county deciles | 52 | 139 | 37% |
| bottom7 county deciles | 1 | 3 | 33% |
| colocated generation | 0 | 0 |  |

## By grid region

| region | projects | reported MW | blocked_confirmed | share of decided |
|---|---|---|---|---|
| PJM | 187 | 68,189 | 28 | 47% |
| MISO | 96 | 14,787 | 12 | 34% |
| Southeast (non-RTO) | 61 | 6,497 | 7 | 21% |
| NYISO | 20 | 3,751 | 1 | 50% |
| ISO-NE | 16 | 433 | 4 | 100% |
| SPP | 11 | 2,376 | 1 | 25% |
| None (islanded) | 3 | 3,120 | 0 | 0% |
| Non-RTO (LG&E-KU) | 2 | 0 | 0 | 0% |

## Coverage

| column | share of projects |
|---|---|
| capacity_mw | 41% |
| size_acres | 70% |
| announced_date | 84% |
| project_cost_usd | 0% |
| cooling_source | 0% |
| btm_power | 0% |
| date_online | 0% |
| fips | 100% |
| grid_region | 100% |
| screen_tier | 100% |
| county_restriction_score | 100% |
| first_seen | 98% |

## Source health

The source renumbered its ids 5 times between 2026-04-23 and 2026-09-25; see `project_id_rekey.py`.
