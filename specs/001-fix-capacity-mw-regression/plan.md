# Implementation Plan: Fix capacity_mw Regression

**Branch**: `001-fix-capacity-mw-regression` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-fix-capacity-mw-regression/spec.md`

## Summary

Restore reliable `capacity_mw` parsing in the TrackDatacenters proposals scraper after a 2026-09-10 field rename (`capacity_mw` → `capacityMw`) caused the column to go from 123 to 3 populated rows silently. The field aliasing fix (`SOURCE_KEYS`) is already in place and correct; the two remaining gaps are (1) a tighter per-field runtime guard for `capacity_mw` at a 20% loss threshold (vs. the global 50% threshold), and (2) selftest coverage for that threshold plus a wiring step in the CI workflow.

## Technical Context

**Language/Version**: Python 3.12 (stdlib only — `csv`, `re`, `subprocess`, `tempfile`, `difflib`)

**Primary Dependencies**: None (stdlib); `curl` subprocess for live scrape (not touched by this fix)

**Storage**: CSV files at `data/proposals.csv`, `data/proposals_added.csv`, `data/proposals_manual_overlay.csv`. Schema frozen at `CSV_FIELDS` in the scraper.

**Testing**: `--selftest` pattern (no external test runner); inline fixtures, no network, no file I/O. Exit 0 = all pass, 1 = any fail.

**Target Platform**: Linux (GitHub Actions `ubuntu-latest`), macOS (local dev)

**Project Type**: CLI pipeline script (`scripts/scrape-trackdatacenters-proposals.py`)

**Performance Goals**: N/A — batch run, completes in seconds on ~400 rows

**Constraints**: Zero new columns in `CSV_FIELDS`; no imports beyond current stdlib set; backward-compatible CSV schema. `python leak_audit.py --tier blocking` must return 0.

**Scale/Scope**: ~400 scraped rows; 33 CSV fields in `CSV_FIELDS`; 4 aliased fields in `SOURCE_KEYS`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| `python leak_audit.py --tier blocking` returns 0 | **PASS** | No new output text or fields; change is to guard constants and selftest assertions only |
| `python layer_audit.py` returns 0 undeclared findings | **PASS** | `data/proposals.csv` writer is already declared in `configs/layers.json` for `scripts/scrape-trackdatacenters-proposals.py`; no new writers or cross-layer writes |
| `--selftest` passes on every touched module | **PASS (to be confirmed)** | Scraper already has `--selftest`; new assertions for the 20% threshold and SOURCE_KEYS coverage will be added. CI workflow step to be added (see Project Structure). |
| `node --check` on touched JS | **N/A** | No JS touched |
| No em-dashes, no CRLF in touched deliverables | **N/A** | Python source only; no CSVs or deliverables modified |
| Docx validation | **N/A** | No docx produced |

All gates pass. No Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/001-fix-capacity-mw-regression/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
scripts/
└── scrape-trackdatacenters-proposals.py   # add CAPACITY_LOSS_RATIO constant,
                                           # per-field capacity check in
                                           # assert_field_population(), and
                                           # selftest assertions for 20% threshold

.github/workflows/
└── scrape-trackdatacenters-proposals.yml  # add --selftest step before the scrape run
```

**Structure Decision**: Single-file change to the existing scraper module. No new files in `scripts/`. The workflow change is a one-step addition; it follows the identical pattern used in `fetch-permits.yml` and all other scraper workflows that already call `--selftest` before their main run step.

## Complexity Tracking

> No violations. All gates pass without exemption.
