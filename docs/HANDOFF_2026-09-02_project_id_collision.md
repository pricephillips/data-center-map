# Handoff — duplicate `project_id` repair

**Date:** 2026-09-02 · **Entry point:** a coverage panel on `project-lifecycles.html` that looked wrong · **Status:** repair written and verified, not yet pushed

---

## What was wrong

`project_id` is `prj_` + the `id` column of `data/proposals.csv`. Every Layer B artifact joins on it — `project_links`, `project_lifecycles`, `baseline_universe`, `matched_controls`, `outcome_model_features`, the county rollups. Nothing validated it.

Two writers feed that file: the CMS export, which owns the contiguous id space from 1 upward, and manual additions curated in-repo. A July 2026 manual batch restarted its numbering at 321, inside the CMS space. **Ids 321–326 were minted twice.**

No error was raised anywhere. The joins fanned out, and six Pennsylvania projects inherited a different project's attributes:

| id | CMS project | inherited from |
|---|---|---|
| prj_321 | PNK Valley View (PA) | Harper Road Technology Park (MO) |
| prj_322 | Ransom Township Mudiita (PA) | Province Group Perry Village (OH) |
| prj_323 | Nebius Butler Township (PA) | Meta Hyperion (LA) |
| prj_324 | Alpha Compute (PA) | Prado AI Industrial Campus (MS) |
| prj_325 | Swedeland Discovery Center (PA) | New Carlisle Chicago Trail (IN) |
| prj_326 | Stonebridge Lawrence (PA) | Project Maize / Google Michigan City (IN) |

Concretely: `baseline_universe.csv` published Nebius Butler Township, Schuylkill County PA, at `32.507, -91.647` — Richland Parish, Louisiana — with operator `Meta`, carrying Meta Hyperion's ten opposition events. Six pins in the wrong state under the wrong developer. Opposition events from LA, OH, MO, MS and IN attributed to Pennsylvania projects. `outcome_model_features.csv` carried two different projects under `prj_322`, both in the training set. Project Maize was counted as decided-and-opposed on opposition that belonged entirely to Stonebridge.

This is a Principle 1 failure and it was client-visible on the map.

## What the repair does

All of it is in **`scripts/fix_project_id_collision.py`**, idempotent, stdlib only, no git operations.

1. Renumbers the 12 manual-addition rows **321–332 → 1001–1012**, moving that writer permanently out of the CMS id space. This also heads off the certain recurrence: the CMS mints 327 next, which would have collided with Meta Project Everest.
2. Migrates hand-curated references — `project_links_manual.csv` (13), `project_decision_dates.csv` (3), `project_duplicates.csv` (2), `negative_audit_codings.csv` (3, note text), `PHASE_STATUS.md` (3).
3. Installs `project_resolution.assert_unique_project_ids()`. A duplicate or blank id now halts the pipeline naming both projects.
4. Rescopes the outcome-gate sentence on `project-lifecycles.html`.
5. Records the convention and the old→new translation table in `ARCHITECTURE.md`.

Generated artifacts are never edited; the pipeline rebuilds them. `--regen` runs that chain locally.

```bash
python3 scripts/fix_project_id_collision.py --check     # report only, exit 1 if collisions
python3 scripts/fix_project_id_collision.py --dry-run   # show every change, write nothing
python3 scripts/fix_project_id_collision.py             # apply
python3 scripts/fix_project_id_collision.py --regen     # apply, then rebuild derived artifacts
```

**Step 2 runs only when step 1 had work to do.** After migration, `prj_321`–`prj_326` denote the six Pennsylvania projects, so an unconditional second pass would corrupt any legitimate future reference to them. Do not remove that coupling.

Dated session records under `data/` (the 2026-07-23 landmark, dedup, negative-audit and announced-date write-ups; `type_b_link_review.md`; `date_recovery_negative_spans.md`) keep the pre-migration ids on purpose — they describe the repo as it was on the day they were written. Translate through the table in `ARCHITECTURE.md`.

## Numbers, before and after

Re-derived from `data/project_lifecycles.csv` after regeneration, not carried over from a snapshot.

| | before | after |
|---|---|---|
| Projects tracked | 337 | 337 |
| Advanced / blocked / pending | 113 / 40 / 184 | unchanged |
| With linked opposition | 193 | **188** |
| Decided + opposed | 89 | **87** |
| Decided with a verified decision date | 31 | 31 |
| Anchored + dated frame | 29 | 29 |
| Decision-date worklist | 46 | **48** |
| Outcome model | n=89 | **n=87**, AUC 0.85 [0.65–0.93], Brier 0.167 vs 0.210 base |
| Landmark frames | W=30 n=24 (18 blocked) | unchanged |

The five phantom-opposition projects came out of the opposed count; the two that were decided came out of the decided-and-opposed frame. Landmark frames are unchanged because none of the contaminated projects carried a verified decision date.

## Verification performed

Applied to a pristine clone, `--regen` green across all ten modules, then compared byte-for-byte against an independent hand-made fix — all source files identical. Second run reports `0 change(s), 5 already in place`. Reintroducing a collision makes `project_resolution.py` exit 1 before writing anything. `leak_audit` clean (blocking 0); self-tests green: `operations_summary` 21/21, `subframe_audit` 31/31, `coverage_audit` 39/39, `leak_audit` 34/34.

The script's verifier also checks that every project's coordinates fall inside the state it claims — that cross-check is what would have caught this on day one.

## Open items this leaves

**1. Two facility rows plotted outside their own state.** Pre-existing, unrelated to the collision, present at HEAD, live on the map now. Addresses are right; coordinates aren't.

| row | address of record | plotted at | actually |
|---|---|---|---|
| `aic_0003` Meta Prometheus | 1 Community Cir, New Albany, OH 43054 | 33.948, -84.5499 | Smyrna, Georgia |
| `aic_0020` Meta Hyperion | Holly Ridge, LA 71269 | 45.5051, -122.9752 | Beaverton, Oregon |

Left alone deliberately: coordinates need a source, not a guess. Fix by geocoding both addresses with a citation, or by nulling them pending one.

**2. Promote the state-bounds check into the QC gate.** It currently lives in the repair script's verifier and reports only. Moving it into `qc/qc_pipeline.py` would make geographic contradiction blocking. That is the durable version of the lesson.

**3. Decision-date recovery is selecting for blocked outcomes.** Not caused by the collision; confirmed against the data while investigating. Blocked is 40 of 153 decided projects (26%), but 24 of the 31 that carry a verified decision date (77%). A denial produces a dated, sourced public record; an approval quietly proceeds. Consequence for Phase 3: `not_blocked >= 12` is the binding floor, not `n >= 40` — `n_not_blocked` never exceeds 6 at any observation window. The worklist is already correctly targeted (47 of 48 rows are `advanced_confirmed`), so the work is to run it, not to re-scope it. Until the advanced arm is dated, a frame drawn from what is dated today would not represent the decided set, and the gate should stay closed.

**4. `PHASE_STATUS.md` headline paragraph.** The append-only log now carries a 2026-09-02 entry with corrected figures. The "Last updated: 2026-08-25" paragraph at the top still cites 338 projects / 89 decided+opposed / 46 outstanding dates and should be refreshed on the next standing-state pass.

## Files changed

Source (push these; Actions rebuilds the rest):

```
scripts/fix_project_id_collision.py   new
data/proposals.csv                    12 ids
data/project_links_manual.csv         13 refs
data/project_decision_dates.csv        3 refs
data/project_duplicates.csv            2 refs
data/negative_audit_codings.csv        3 refs (note text)
project_resolution.py                 + assert_unique_project_ids()
project-lifecycles.html               gate copy
ARCHITECTURE.md                       convention + translation table
PHASE_STATUS.md                        3 refs + update-log entry
docs/HANDOFF_2026-09-02_project_id_collision.md   this file
```
