# Proposed data center pipeline: intelligence report

Generated 2026-10-01 by `proposal_enrichment.py`. Descriptive shares with their denominators. Decided means a terminal lifecycle outcome (blocked_confirmed, restricted_conditional, advanced_confirmed); block rates are observed among those, not effects. Estimated MW is an acreage-based range for projects reporting no capacity and is never added into reported totals.

## Totals

- 464 projects in 28 states. By source phase: 284 pending, 111 advancing, 69 withdrawn or rejected. By verified lifecycle outcome: 179 decided, 68 blocked_confirmed.
- Reported capacity 115,026 MW across 201 projects. Another 171 report acreage but no MW; at the observed MW-per-acre range they would add 59,392 to 231,330 MW (estimate, not included above).
- 224 projects carry recorded opposition.
- 32 projects were relabeled "proposed" when the source folded its "approved" phase into "proposed" on 2026-09-22 (listed in `source_reclassified_from`). Under Ruling 1, 12 keep "approved" on in-repo evidence of a final approval (a sourced row in `data/project_decision_dates.csv`, or a linked opposition record of the approving vote); the rest count as pending, are listed as APPROVAL_EVIDENCE rows in `data/project_link_review.csv`, and are restored on the run after such evidence is committed. This is a data correction: decided counts reported before the relabel included all of them.

## Outcomes among decided projects (descriptive)

| slice | blocked | decided | share |
|---|---|---|---|
| all decided | 68 | 179 | 38% |
| with opposition | 35 | 93 | 38% |
| without recorded opposition | 33 | 86 | 38% |
| with lawsuit | 5 | 16 | 31% |
| nda reported | 0 | 1 | 0% |
| top3 county deciles | 62 | 166 | 37% |
| bottom7 county deciles | 6 | 13 | 46% |
| colocated generation | 1 | 4 | 25% |

## By grid region

| region | projects | reported MW | blocked_confirmed | share of decided |
|---|---|---|---|---|
| PJM | 205 | 70,041 | 32 | 48% |
| MISO | 107 | 20,758 | 14 | 30% |
| Southeast (non-RTO) | 93 | 11,635 | 14 | 29% |
| SPP | 22 | 4,901 | 3 | 33% |
| NYISO | 21 | 4,251 | 1 | 33% |
| ISO-NE | 14 | 320 | 4 | 100% |
| None (islanded) | 2 | 3,120 | 0 | 0% |

## Coverage

| column | share of projects |
|---|---|
| capacity_mw | 43% |
| size_acres | 70% |
| announced_date | 72% |
| project_cost_usd | 24% |
| cooling_source | 6% |
| btm_power | 97% |
| date_online | 12% |
| fips | 100% |
| grid_region | 100% |
| screen_tier | 100% |
| county_restriction_score | 100% |
| first_seen | 99% |

## Source health

The source renumbered its ids 5 times between 2026-04-23 and 2026-10-01; see `project_id_rekey.py`.
