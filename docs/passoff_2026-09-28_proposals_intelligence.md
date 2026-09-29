# Session pass-off: Proposals intelligence + source-id repair (2026-09-28)

This pass-off is for merging this session with the other active sessions. It covers the file claims, the overlap risks and the merge order this branch depends on.

## 1. Status

| | |
|---|---|
| Branch | `claude/pipeline-intel-upgrade` |
| Base | `origin/main` at `f7c7c85` (merged in; includes spec 004 CI hygiene and PR #49) |
| Head | `70d4b7a` (6 commits) |
| Pushed | **No.** The sandbox proxy denied the repo. The branch exists only as `pipeline-intel-upgrade.bundle` and `pipeline-intel-upgrade.patch` in the chat outputs. |
| Land it | `git fetch pipeline-intel-upgrade.bundle claude/pipeline-intel-upgrade:claude/pipeline-intel-upgrade && git push -u origin claude/pipeline-intel-upgrade`, then open the PR with `PR_DESCRIPTION.md` as the body. |
| Scope | Layer B (Proposed Centers): new data inputs and deeper analytics, plus the repair for source-id renumbering |
| Project doc | `claude/handoff-2026-09-28-proposals-intelligence-and-id-rekey.md` (same content as the PR description) |

## 2. Headline finding: other sessions must know this before touching Layer B

TrackDataCenters **renumbered its project ids** on 2026-09-17 and 2026-09-22. It also partially renumbered on 05-01, 05-21 and 07-02. `project_id = prj_<source id>` is therefore **not a stable key**. Any row written before 2026-09-22 and keyed on `prj_N` may point at a different project today.

- This branch re-keyed 190 rows:
  - `proposals_manual_overlay.csv`: 10
  - `project_decision_dates.csv`: 28
  - `project_links_manual.csv`: 141
  - `project_duplicates.csv`: 1
  - `negative_audit_codings.csv`: 10
- The full list is in `data/pipeline_intel_rekey_report.md`.
- 3 orphans remain: rows for EdgeConneX Sheridan, which left the source.
- **Rule for every other session:** any work keyed on `prj_N` with ids below 1000 that was written before 2026-09-22 must be passed through `project_id_rekey.py` after this branch lands. That includes worklists, coding files, client reports that cite `prj_` ids, and the Vantage, TVA and county reports. The ids in those reports may name the wrong project.
- On 2026-09-22 the source also folded 32 `approved` projects into `proposed`. This is recorded as `source_reclassified`. It lowers the decided count for the landmark and outcome models, so **any session reporting decided-case statistics after 2026-09-22 is affected.** It needs a ruling.

## 3. File claims

### Files this session created (sole owner; no other session should create these)

`proposal_history.py`, `project_id_rekey.py`, `proposal_enrichment.py`, `proposal_discovery.py`, `fetch_air_permits.py`, `fetch_planned_generation.py`, `fetch_grid_territory.py`, `configs/grid_iso_crosswalk.json`, `.github/workflows/proposals-intel.yml`, `data/pipeline_intel_*` (all), `data/proposal_candidates_news*.csv`, `data/proposal_candidates_airpermits*.csv`, `data/proposals_detail.json`, `data/county_grid_territory*`, `data/grid_planned_generation*`.

### Existing files this session modified: overlap risk

| File | What changed | Overlap risk | Merge rule |
|---|---|---|---|
| `scripts/scrape-trackdatacenters-proposals.py` | +22 appended columns, `proposals_detail.json` sidecar, id-stability check, `IGNORED_KEYS`, 4 updated selftest fixtures, one em dash fixed | HIGH if another session edits the scraper | Keep the new columns appended after `updatedAt`, never interleaved. The selftest must stay at 106/106. |
| `.github/workflows/scrape-trackdatacenters-proposals.yml` | `fetch-depth: 0`; runs `proposal_history.py` and the re-key report after the commit; opens an issue on stale keys | MEDIUM | Keep the history step after the first commit and before the push. |
| `.github/workflows/pipeline.yml` | 8 selftest lines added to the blocking gate | HIGH (spec 004's `docs/pending_ci_hygiene.patch` also edits `pipeline.yml`) | Append-only. Re-add the 8 lines after that patch is applied. |
| `configs/layers.json` | B: `proposal_candidates_*`, `proposals_detail.json`; D: grid files; E: `pipeline_intel_*`; `declared_crossings.project_id_rekey.py`; `exempt_multi_writer.data/project_links_manual.csv` | HIGH (every session declares files here) | Union all entries. Already merged once with main's `status_resolution.py` and `master_diff_summary.md` entries. |
| `configs/surfaces.json` | 13 `dispositions` entries for the new files | MEDIUM | Union. Keep 2-space indent. |
| `ARCHITECTURE.md` | Two new subsections at the end of Layer B ("The source id is not a stable key", "Layer B intelligence inputs"); one em dash fixed at line ~309 | MEDIUM | Keep both subsections just before `### Layer C`. |
| `PHASE_STATUS.md` | One append-only log entry dated 2026-09-28 | LOW (append-only) | Keep every session's entries in date order. |
| `DATA_NOTICES.md` | Appended section for EPA ECHO, EIA-860M and EIA-861/PUDL | LOW | Append. |
| `trackdatacenters-proposals.html` | Fully rebuilt into the analytics page | HIGH if another session edits this page | Take this version whole. It falls back to `proposals.csv` if the enriched file is missing. |
| `proposals-map.html` | Popups now read `pipeline_intel_enriched.csv`; em dashes replaced | LOW to MEDIUM | Additive `enrichedRows()` block. Keep it. |
| Hand files: `proposals_manual_overlay.csv`, `project_decision_dates.csv`, `project_links_manual.csv`, `project_duplicates.csv`, `negative_audit_codings.csv` | Only the id column changed, on 190 rows | **CRITICAL** | See section 4. Never resolve by "take theirs". |
| Generated: `project_lifecycles.csv`, `project_links.csv`, `project_link_review.csv`, `layer_audit_summary.json`, `docs/visibility_matrix.md` | Regenerated locally | LOW | Take main's copy (`scripts/resolve_generated_conflicts.py`). CI rebuilds them from the corrected hand files. |

## 4. Critical merge procedure for the hand-maintained id files

If any other session added or edited rows in the five hand files above:

1. Merge that session's rows in first. Keep this branch's re-keyed ids for the rows it changed.
2. Run `python proposal_history.py`. It needs the full git history.
3. Run `python project_id_rekey.py`, which only writes a report. Every new row written before 2026-09-22 with an id below 1000 will show as `rekey` if its id is stale.
4. Review `data/pipeline_intel_rekey_report.md`, then run `python project_id_rekey.py --apply`.
5. Re-run `python project_resolution.py`.

Constitution VII: CI runs the re-key in report mode only, and a person applies it.

## 5. Dependencies and ordering against other sessions

- **Spec 004 / `docs/pending_ci_hygiene.patch`:** apply before this branch, or re-add the 8 selftest lines to `pipeline.yml` afterward. This branch already passes spec 004's precommit gates (crlf, emdash, nodecheck) on every file it touched.
- **The pipeline commit-conflict fix handoff (`claude/handoff-2026-09-28-pipeline-commit-conflict-fix.md`):** that change touches the `pipeline.yml` commit step. This branch touches only the selftest step, so they should merge cleanly, but check it.
- **Adaptive feature search and plugin features:** this branch **reads** `data/features/*.csv` (drought, water_use, retail_price, farmland, grid_generation) and never writes them. If that session renames columns, update the `f("drought", "drought_d1_mean_pct")` calls in `proposal_enrichment.py`.
- **County label provenance / status resolution:** no file overlap. `layers.json` is already merged.
- **State-level promotion gate:** no known overlap. If it touches `project_links_manual.csv`, follow section 4.
- **Any session that edits `site_screener.py` or `county_policy_model.py` outputs:** enrichment joins `site_screen.csv`, `county_policy_scores.csv` and `county_aggregate.csv` by `project_id` or `fips`, with a name check. Renaming columns there breaks those joins silently. They go blank, not wrong.
- **Vantage and TVA deliverables:** re-verify any `prj_` ids they cite (section 2).

## 6. Verification state at hand-off

- Selftests pass:

  | Module | Checks |
  |---|---|
  | scraper | 106/106 |
  | history | 31/31 |
  | rekey | 15/15 |
  | discovery | 19/19 |
  | air | 10/10 |
  | generation | 9/9 |
  | grid | 9/9 |
  | enrichment | 25/25 |
  | layer_audit | 28/28 |
  | facility_registry | 38/38 |
  | signal_harvest | all pass |
  | site_screener | all pass |

- `leak_audit.py --tier blocking`: 0.
- `layer_audit.py --strict`: this branch adds no undeclared findings. The one remaining finding, `data/research_routine_log.csv`, came from main.
- Precommit gates on touched files: crlf, emdash and nodecheck pass.
- The dashboard was rendered and screenshotted at desktop and 390px with no horizontal scroll.
- **Not yet run live:** ECHO, EIA-860M, EIA-861 and news discovery. The sandbox blocks those hosts. Their columns stay blank until the first `proposals-intel.yml` run. The first run may surface schema mismatches; each fetcher aborts without writing and prints the columns it saw.

## 7. Open decisions (need Price)

1. Treat the 2026-09-22 `approved` to `proposed` relabel as a vocabulary change and restore `approved`, or accept it. This affects the decided n for the landmark and outcome models.
2. Move Layer B to a stable key: an id minted in-repo, or the source `slug`, which is now captured. Until then the renumbering repair is reactive.
3. `facility_registry.graduation_candidates` reads `yearOpened`. The source stopped sending it, so graduation by opening year is silently inactive.
4. The 3 orphan manual-link rows for EdgeConneX Sheridan: re-home them to a manual addition in the 1000+ range, or drop them.

## 8. Do-not-redo list for the consolidated session

- Do not re-scrape or re-map the 22 captured fields.
- Do not rebuild the history miner. `proposal_history.py` already handles the id migration, degraded snapshots, renumberings and reclassifications.
- Do not re-key again blindly. The re-key is idempotent, and a second `--apply` on already-fixed rows changes nothing.
- Do not add a grid-region source other than `fetch_grid_territory.py` and `configs/grid_iso_crosswalk.json`.
- Do not create a second enriched project table. Extend `proposal_enrichment.py` and its `FIELDS` list.
