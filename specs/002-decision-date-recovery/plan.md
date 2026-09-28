# Implementation Plan: Decision-Date Recovery for Landmark Retrain Gate

**Branch**: `002-decision-date-recovery` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-decision-date-recovery/spec.md`

## Summary

Add `--decision-dates` and `--selftest` modes to `date_recovery.py`. The `--decision-dates` pass reads `data/decision_date_worklist.csv`, joins to `master_opposition.csv` by project name to collect source URLs, applies the existing offline `recover_from_url()` engine, and writes candidates to `data/decision_date_recovery_candidates.csv` for human review. The new output file is declared in Layer E of `configs/layers.json`. `project_decision_dates.csv` (Layer B) is never touched by code.

## Technical Context

**Language/Version**: Python 3.12 (stdlib only — `csv`, `re`, `argparse`, `sys`)

**Primary Dependencies**: None (stdlib); `recover_from_url()` already exists in `date_recovery.py` and is reused as-is

**Storage**: CSV files. Reads: `data/decision_date_worklist.csv`, `master_opposition.csv`. Writes: `data/decision_date_recovery_candidates.csv` (new). Never writes: `data/project_decision_dates.csv`.

**Testing**: `--selftest` pattern (no external test runner); inline fixtures only, no network, no file I/O

**Target Platform**: Linux (GitHub Actions), macOS (local dev)

**Project Type**: CLI pipeline script (`date_recovery.py` at repo root)

**Performance Goals**: N/A — offline, processes 48 worklist rows

**Constraints**:
- `apply_recovery()` signature and behavior MUST remain unchanged (FR-009)
- `data/project_decision_dates.csv` MUST NOT be written by code under any code path
- New output file must be declared in `configs/layers.json` Layer E before `layer_audit.py` is run
- `python leak_audit.py --tier blocking` must return 0 after changes

**Scale/Scope**: 48 worklist projects; 9/48 have source URLs via project-name join to `master_opposition.csv`; 39 will be listed as `no_pattern_match`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| `python leak_audit.py --tier blocking` returns 0 | **PASS (expected)** | Candidates file contains project metadata only — no prose subject to leak audit. Confirm after implementation. |
| `python layer_audit.py` returns 0 undeclared findings | **REQUIRES ACTION** | `data/decision_date_recovery_candidates.csv` is new. Must be declared in `configs/layers.json` Layer E before first run. |
| `--selftest` passes on every touched module | **REQUIRES ACTION** | `date_recovery.py` currently has no `--selftest`. Must add and wire into `pipeline.yml` selftest block. |
| `node --check` on touched JS | **N/A** | No JS touched |
| No em-dashes, no CRLF in touched deliverables | **N/A** | Python source and CSV only |
| `project_decision_dates.csv` not auto-written | **CRITICAL GATE** | Layer B file. The implementation MUST NOT write to this file under any code path. Guardrail per user instruction and FR-006. |

All gates are resolvable within scope. No Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/002-decision-date-recovery/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
date_recovery.py                     # add --decision-dates and --selftest modes;
                                     # main() gains argparse; apply_recovery() unchanged
configs/layers.json                  # add data/decision_date_recovery_candidates.csv
                                     # to Layer E files list; add date_recovery.py
                                     # as its writer
.github/workflows/pipeline.yml       # add --selftest step in the selftest block (~line 119)
```

**Structure Decision**: All changes are in-place on existing files. No new files in the repo root. The candidates CSV is a net-new data file under `data/`, declared in Layer E.

## Complexity Tracking

> No violations. CRITICAL gate (no auto-write to `project_decision_dates.csv`) is enforced by code structure, not by a runtime check — the path to that file simply does not exist anywhere in the new code.
