# Proposed data center pipeline: intelligence report

Generated 2026-09-29 by `proposal_enrichment.py`. Descriptive shares with their denominators. Decided means a terminal lifecycle outcome (blocked_confirmed, restricted_conditional, advanced_confirmed); block rates are observed among those, not effects. Estimated MW is an acreage-based range for projects reporting no capacity and is never added into reported totals.

## Totals

- 396 projects in 28 states. By source phase: 242 pending, 101 advancing, 53 withdrawn or rejected. By verified lifecycle outcome: 153 decided, 52 blocked_confirmed.
- Reported capacity 99,153 MW across 163 projects. Another 151 report acreage but no MW; at the observed MW-per-acre range they would add 58,293 to 229,486 MW (estimate, not included above).
- 208 projects carry recorded opposition.
- 32 projects were relabeled "proposed" when the source folded its "approved" phase into "proposed" on 2026-09-22 (listed in `source_reclassified_from`). Under Ruling 1, 12 keep "approved" on in-repo evidence of a final approval (a sourced row in `data/project_decision_dates.csv`, or a linked opposition record of the approving vote); the rest count as pending, are listed as APPROVAL_EVIDENCE rows in `data/project_link_review.csv`, and are restored on the run after such evidence is committed. This is a data correction: decided counts reported before the relabel included all of them.

## Outcomes among decided projects (descriptive)

| slice | blocked | decided | share |
|---|---|---|---|
| all decided | 52 | 153 | 34% |
| with opposition | 30 | 88 | 34% |
| without recorded opposition | 22 | 65 | 34% |
| with lawsuit | 4 | 15 | 27% |
| nda reported | 0 | 0 |  |
| top3 county deciles | 50 | 144 | 35% |
| bottom7 county deciles | 2 | 9 | 22% |
| colocated generation | 1 | 4 | 25% |

## By grid region

| region | projects | reported MW | blocked_confirmed | share of decided |
|---|---|---|---|---|
| PJM | 182 | 63,094 | 28 | 45% |
| Southeast (non-RTO) | 83 | 10,355 | 11 | 25% |
| MISO | 80 | 14,142 | 8 | 24% |
| NYISO | 20 | 3,751 | 1 | 50% |
| SPP | 16 | 4,371 | 1 | 14% |
| ISO-NE | 13 | 320 | 3 | 100% |
| None (islanded) | 2 | 3,120 | 0 | 0% |

## Coverage

| column | share of projects |
|---|---|
| capacity_mw | 41% |
| size_acres | 70% |
| announced_date | 84% |
| project_cost_usd | 16% |
| cooling_source | 6% |
| btm_power | 97% |
| date_online | 11% |
| fips | 100% |
| grid_region | 100% |
| screen_tier | 100% |
| county_restriction_score | 100% |
| first_seen | 98% |

## Source health

The source renumbered its ids 5 times between 2026-04-23 and 2026-09-29; see `project_id_rekey.py`.
