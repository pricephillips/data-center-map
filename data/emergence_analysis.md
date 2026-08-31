# Emergence Analysis: Verified-Negative Audit

Generated 2026-08-31. Gate thresholds registered 2026-07-27. This is the analysis layer over negative_audit.py; the audit frame design itself is registered separately and is not modified here.

## Gate decision

**Emergence modeling: LOCKED**

| # | Criterion | Threshold | Observed | Met |
| :-- | :-- | :-- | :-- | :-- |
| G1 | random-stratum coverage | >= 0.60 | 0.0% | no |
| G2 | undeterminable share, random stratum | <= 0.35 | no coded rows | no |
| G3 | determinate codings, random stratum | >= 40 | 0 | no |
| G4 | worst-case bound width | <= 0.20 | not computable | no |

## Stratum separation

The worklist front-loads rows whose outcome is blocked_confirmed, because a project recorded as stopped with no recorded opposition is the most anomalous cell in the data. That ordering makes those rows a purposive cell rather than a random draw, so they are reported separately and never pooled into a frame-level rate.

| Stratum | Frame | Coded | Coverage |
| :-- | :-- | :-- | :-- |
| purposive (blocked_confirmed) | 12 | 9 | 75.0% |
| random (all other frame rows) | 158 | 0 | 0.0% |

Note the shape of the coded set: every coded row to date falls in the purposive cell and none in the random stratum. A combined coverage figure over the whole frame would therefore overstate progress toward an emergence estimate, which depends entirely on the random stratum. Random-stratum coverage is the number that matters and it is the one in the gate table above.

## Purposive cell: blocked with no recorded opposition

9 of 12 coded. Findings: 6 verified_opposition, 1 verified_none, 2 undeterminable.

This cell was a diagnostic question rather than an estimation target, and it has an answer. Where the cell is dominated by verified_opposition, the reading is that these projects did face opposition and the tracker did not carry it, which makes the cell a detection gap rather than a real population of quietly blocked projects. Consequence for existing statistics: opposition presence among blocked projects was understated, and the affected projects are listed in data/audit_discovered_opposition.csv for entry through the normal sourced-URL path.

The rate in this cell is not an emergence rate and must not be quoted as one. It is a census of the anomalous cell only.

## Emergence rate, random stratum

No determinate codings in the random stratum yet, so no emergence rate is computable. Nothing in this section can be filled in by analysis; it requires coded rows.

## What actually binds

At full coverage of the random stratum the worst-case bound width equals the undeterminable share. Observed share so far across all coded rows is 22.2%, which projects to a bound roughly that wide even after every row in the frame is coded.

| If undeterminable share is | Bound width at full coverage |
| :-- | :-- |
| 5.0% | 0.05 |
| 10.0% | 0.10 |
| 20.0% | 0.20 |
| 30.0% | 0.30 |
| 40.0% | 0.40 |

The operational consequence is the main finding of this pass. Coding more rows buys sampling precision, which the census will deliver anyway. It does not buy identification. The binding constraint is the share of rows the protocol cannot resolve, so protocol work is worth more per hour than volume work. Concretely: the current protocol is four news-style queries, and a row fails when no coverage of the approval process exists. Adding a municipal-records step (agenda or minutes search for the jurisdiction and date window) targets exactly the failure mode, and it is the same civic-scraper capability already sitting at Tier 2 of the tooling scan. That link is the argument for moving it up.

## Follow-on worklists

- data/audit_discovered_opposition.csv: 7 projects where the audit found sourced opposition. Each row carries the evidence URL and needs entry through the normal path; the audit does not write to the tracker.
- data/audit_data_flags.csv: 12 data-quality flags. Provenance is recorded per row. A flag marked detected_from_notes was recovered from prose and is an inference about intent; confirm it before acting. A flag marked declared came from a structured column and can be worked directly.

| Flag | n |
| :-- | :-- |
| duplicate_project | 1 |
| geography_error | 2 |
| mechanism_review | 1 |
| missing_opposition_events | 6 |
| outcome_review | 2 |

Batches after this one should populate a `flags` column in the codings file rather than relying on prose detection. The column is optional and its absence changes nothing, so adding it is backward compatible.

## Coded rows outside the frame

1 coded row(s) reference a universe_id not in the current frame. This is expected when a project was later suppressed as a duplicate; the coding is retained as an audit trail and excluded from every rate above.
- prj_76: verified_opposition

## Standing rules observed

Bounds rather than point estimates wherever identification is partial. Purposive and random strata never pooled. Undeterminable rows never dropped and never imputed. No scorekeeping vocabulary. No em-dashes. Nothing written to any source-of-truth file.

