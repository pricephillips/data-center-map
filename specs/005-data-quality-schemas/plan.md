# Implementation Plan: Data Quality Schemas and Regression Gates

**Branch**: `005-data-quality-schemas` (worked on `claude/great-einstein-7brlol`) | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-data-quality-schemas/spec.md`, including its "Changes on main since this spec (2026-09-29)" section.

## Summary

Four checks and one data source, all report or worklist outputs; none edits
`master_opposition.csv` or any label (FR-007).

1. **Schemas (US1).** `qc/schemas.py` is the only module that imports Pandera.
   It declares three schemas:
   - `master_opposition_clean.csv`: outcome grade, State, lat/lon, `is_*`
     tokens;
   - `data/county_policy_scores.csv`: FIPS against the county frame, scores in
     [0, 1];
   - `data/proposals.csv`, the Layer B schema the "Changes on main" note asks
     for: every column the widened scraper writes, and ids resolved through
     `data/project_key_map.csv`.

   Failures go to `qc/schema_report.md` with row ids. A run log,
   `data/schema_run_history.csv`, counts consecutive clean runs. One config
   flag in `configs/data_quality.json` sets report-only, window or blocking.
   In window mode, the default, the gate turns blocking after seven
   consecutive clean runs and records that date as the switch date.
2. **State normalizer (US2).** `normalize_state()` goes into
   `qc/schema_adapter.py`. It returns a two-letter code or nothing, and puts
   `US` and unknown values on a review list. The schema and the cleaner's
   headline backfill both use it. That backfill is the source of the nine
   full-name State values in the clean feed today, so they are fixed at source.
3. **Coverage delta (US3).** `qc/coverage_delta.py` profiles non-null counts
   per column. It stores one profile per file hash in
   `snapshots/coverage_profiles.csv`, keyed to `snapshots/manifest.csv` for the
   clean feed. It compares the current file against the most recent earlier
   profile and fails when a declared column drops past its threshold. It also
   flags robust z-scores above 6 on `Megawatts`, `Investment Million USD` and
   `Acreage`. Output: `data/coverage_delta_report.md`.
4. **Label disagreement (US4).** `label_disagreement_audit.py` reads the
   out-of-fold scores `county_policy_model.py` already writes to
   `data/county_policy_scores.csv`, so the model is not changed. It lists
   positives in the bottom score decile and negatives in the top 1 percent,
   each with the master rows behind the label. Output:
   `data/label_disagreement_worklist.csv`.
5. **Political source (US5).** `fetch_county_features.py` gains a `political`
   source that reads the MEDSL County Presidential Election Returns 2000-2024
   from Harvard Dataverse. It writes `data/features/political.csv` and a parity
   report against `data/county_votes.json`. It records the DOI, dataset
   version, file id, checksum and license terms in
   `data/features/features_manifest.json`. It is deliberately **not**
   registered in `configs/feature_plugins.json`, so no model or choropleth
   reads it until the parity report has been reviewed.

Workflow changes go in `docs/pending_data_quality.patch` with a matching `.md`,
per the constitution and spec 004 research D1:

- the Pandera install;
- new pipeline steps and their surface-failure steps;
- staging the new outputs.

Two findings change what the spec can promise. Both are written up in
research.md and neither is hidden:

- **SC-003 is not met by the specified rule.** The pre-2026-08-21 labels can
  be rebuilt from git history (`aaf6cb8` vs `57547fc`). The 28 counties
  removed on 2026-08-21 sat in **high** out-of-fold score deciles: 21 of 28
  are in deciles 9 or 10, and the lowest is in decile 2. Each was a county
  where a project was approved over recorded opposition. The model's
  predictors score that profile high, so the false labels looked typical to
  the model. The
  bottom-decile rule therefore surfaces 0 of 28, and even the bottom 100
  positives hold only 6, which is the base rate. The audit ships as specified,
  with the synthetic flipped-label test. SC-003 is reported as not met, and
  the claim that out-of-fold probabilities could have found this defect is
  scoped down (Principle I).
- **The clean feed has no `fips` column.** FIPS is resolved inside
  `county_aggregator.py`, and nothing about that is written to the feed. The
  FIPS check therefore runs where the column exists (`county_policy_scores.csv`,
  `political.csv`) and is declared "when present" for the clean feed.

## Technical Context

**Language/Version**: Python 3.11 (CI 3.11.16; sandbox 3.11.15).

**Primary Dependencies**:

- New: pandera `>=0.33,<0.34`, resolved at 0.33.1, per `configs/integrations.json`. Imported only in `qc/schemas.py` (FR-001).
- Existing, unchanged pins: pandas 3.0.6, numpy 2.4.6, scikit-learn 1.9.1. scikit-learn is used only by the disagreement selftest's synthetic cross-fit.
- Standard library for the normalizer, coverage delta and MEDSL parser.

**Storage**: CSV, JSON and Markdown files in the repo. No database.

**Testing**: A `--selftest` on every new or touched module, discovered by `tests/test_selftests.py` (spec 004). Real-data replays for SC-002 and SC-003 come from git history and are recorded as fixtures.

**Target Platform**: GitHub Actions `ubuntu-latest`, and local runs.

**Project Type**: Script-based data pipeline: root-level modules, `qc/`, CI workflows.

**Performance Goals**: Each new step runs in under 10 s on current data (1,896 clean rows, 3,144 counties, 396 proposals). The selftests add under 10 s to the discovery run.

**Constraints**:

- No commits under `.github/workflows/`; that work goes in the patch.
- `master_opposition.csv` bytes are unchanged (FR-007, Principle VII).
- No uniqueness check on `master_opposition.csv`. It carries 4,099 exact duplicate harvest rows, and removing them is Price's decision (Changes on main). The schema report shows the count as information only.
- Rows edited by `status_resolution.py --apply` must pass. The schema reads the clean feed, and constrains outcome grades and State, never the Status or Community Outcome text those rows change.
- Until the patch installs Pandera in CI, `qc/schemas.py --selftest` must not break the blocking discovery run. It reports `SKIP (pandera not installed)` and exits 0. Once Pandera is present, its checks are blocking. This is justified in Complexity Tracking.
- Harvard Dataverse is unreachable from the sandbox (proxy 403). The political source is built and tested offline. The first real pull happens in `fetch-features.yml`.

**Scale/Scope**:

- Four new modules: `qc/schemas.py`, `qc/coverage_delta.py`, `label_disagreement_audit.py`, and the political source inside `fetch_county_features.py`.
- Edits to `qc/schema_adapter.py`, `outcome_defensibility.py` (an additive `OUTCOME_GRADES` constant), `clean_opposition_data.py` (State backfill to codes), `leak_audit.py` (one file-plus-column exemption) and `configs/*`.
- The current feed shows 3 failure classes (research D6): 131 blank State rows, 55 `US` rows, 9 full-name State rows, and 0 coordinate failures. Implementation found 7 proposals at 0,0, which are declared by `pk`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| 1. `leak_audit.py --tier blocking` = 0 | **REQUIRES ACTION (resolved on `main` by `4c3eebb` before merge; this branch leaves `leak_audit.py` unchanged)** | `main` has 1 blocking hit today. `data/county_grid_territory.csv`, first committed at `c6180b9` on 2026-09-29, carries the utility name "Lost River Electric Coop Inc", an EIA proper noun. The fix is a file-plus-column exemption, the same class as the gazetteer place names. The file is Layer D reference data and never ships to a client. The new reports use grade names only, with no outcome prose. |
| 2. `layer_audit.py` = 0 undeclared | **REQUIRES ACTION** | New writers and files to declare: `qc/schema_report.md` (E), `data/schema_run_history.csv` (E), `data/coverage_delta_report.md` (E), `snapshots/coverage_profiles.csv` (already `not_a_layer: snapshots/*`), `data/label_disagreement_worklist.csv` (E), and `data/features/political*.csv` and `data/features/political_parity.md` (D). Add the ARCHITECTURE.md lines, then `layer_audit.py --write-gitattributes`. |
| 3. `--selftest` on touched/new modules | **REQUIRES ACTION** | New selftests: `qc/schemas.py`, `qc/coverage_delta.py`, `label_disagreement_audit.py`, `qc/schema_adapter.py` (first selftest there). Extended selftests: `fetch_county_features.py` (MEDSL fixture), `outcome_defensibility.py` (grade closure), `clean_opposition_data.py` (backfill emits codes). Discovery picks up every one of them (FR-006, Principle IX). |
| 4. `node --check` on touched JS | **N/A** | No JS touched. |
| 5. No em-dashes, no CRLF | **PASS (verify at end)** | Every writer uses `lineterminator="\n"`. The reports carry no em-dash, and the selftests assert it. |
| 6. docx `--original` validation | **N/A** | No docx. |

Principle checks:

- **I (defensibility).** The SC-003 shortfall is reported, not tuned away. The rule is not adjusted after the fact to catch the 28.
- **II.** Reports carry grade names, never outcome prose.
- **IV.** The political variables are described as observed vote shares.
- **V.** No model change. The audit reads existing out-of-fold scores.
- **VI.** Normalized State is available to every consumer through one function.
- **VII.** All outputs are additive. The only changed output value is the 9 full-name State cells, which become codes and so match the column's own convention.
- **VIII.** Every writer is declared.
- **IX.** Every module ships a selftest.

**Post-design re-check**: every gate resolves within scope. Three items need
Price's decision and are written up rather than acted on:

1. The blank-State harvest rows. The plan declares them as an allowed-exception
   rule, so the report-only window can close, and lists them in the report.
   The rule can be removed to make them blocking.
2. The 4,099 duplicate master rows (information line only).
3. Switching the choropleth and model from `county_votes.json` to
   `political.csv` after parity review.

## Project Structure

### Documentation (this feature)

```text
specs/005-data-quality-schemas/
├── plan.md              # This file
├── research.md          # Phase 0: decisions D1-D12, measured baselines, SC-003 finding
├── data-model.md        # Phase 1: schemas, config, profiles, worklist, political file
├── quickstart.md        # Phase 1: validation scenarios per user story and SC
├── contracts/
│   ├── cli.md                   # command lines, flags, exit codes for every new entry point
│   ├── data-quality-config.md   # configs/data_quality.json structure
│   └── outputs.md               # report, history, profile, worklist and political file formats
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
qc/schemas.py                       # NEW  Pandera schemas, report, run history, mode logic; --selftest
qc/coverage_delta.py                # NEW  column profiles, delta gate, robust z flags; --selftest
qc/schema_adapter.py                # EDIT add normalize_state(), STATE_VARIANTS; add --selftest
label_disagreement_audit.py         # NEW  OOF disagreement worklist with evidence rows; --selftest
fetch_county_features.py            # EDIT add political source, MEDSL parser, parity; selftest cases
outcome_defensibility.py            # EDIT add OUTCOME_GRADES constant (additive) + selftest closure check
clean_opposition_data.py            # EDIT headline State backfill writes two-letter codes
leak_audit.py                       # (no change: main 4c3eebb fixed the same hit first)
configs/data_quality.json           # NEW  mode flag, window, exceptions, thresholds, audit cutoffs
configs/feature_sources.json        # EDIT add political source settings
configs/layers.json                 # EDIT declare new outputs
configs/integrations.json           # EDIT pandera target list: qc/schemas.py only (FR-001)
ARCHITECTURE.md                     # EDIT writer lines for the new outputs
.gitattributes                      # REGEN via layer_audit.py --write-gitattributes
requirements/ci.in, ci.txt          # EDIT add pandera pin
snapshots/coverage_profiles.csv     # NEW  seeded with current profiles
tests/fixtures/medsl/countypres_fixture.csv          # NEW  five-county MEDSL long-format fixture
tests/fixtures/coverage_delta/proposals_2026-09-10.json  # NEW  real profile pair (SC-002 replay)
qc/schema_report.md, data/schema_run_history.csv, data/coverage_delta_report.md,
data/label_disagreement_worklist.csv  # NEW  first generated outputs on current data
docs/pending_data_quality.patch     # NEW  all .github/workflows/ changes
docs/pending_data_quality.md        # NEW  what the patch does and how to apply it
```

**Structure Decision**: Follow the existing layout. The QC-family checks live
in `qc/` next to the gate and adapter they extend. The county audit sits at the
root beside `link_disagreement_audit.py` and `county_policy_model.py`. The
political source is a builder in the existing feature fetcher, not a new
module, so it inherits the manifest and failure discipline.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `qc/schemas.py --selftest` skips (exit 0) when Pandera is absent | CI installs Pandera only after `docs/pending_data_quality.patch` is applied. Without the skip, the blocking discovery run would go red on the first push. | Keeping Pandera out until the patch lands would ship no schema at all. Writing the checks without Pandera would break FR-001, which names Pandera. The skip prints a visible `SKIP` line, and the patch's install step removes it in practice. |
| Allowed-exception rule for blank State on `signal_harvest_auto` rows | SC-001 needs each failure fixed at source or declared. These 131 rows need research to place, which FR-007 forbids a check from doing. | Leaving them failing keeps the window from ever closing, so the gate never turns blocking for anything. Listing them one by one would be 131 row ids that churn with every harvest. |
