# Implementation Plan: Negative Audit Random Stratum Coding Tooling

**Branch**: `003-negative-audit-stratum` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-negative-audit-stratum/spec.md`

## Summary

Add `--next N`, a coverage gate, and `--selftest` to `negative_audit.py`. `--next N` prints the next N uncoded rows from the random stratum (audit_order > last blocked_confirmed row) in ascending order. The report suppresses the emergence rate below an 80% random-stratum coverage threshold (named constant `COVERAGE_THRESHOLD = 0.80`). `--selftest` exercises coding validation, queue logic, and the coverage gate with inline fixtures. CI is wired via a new `--selftest` step in `pipeline.yml`.

## Technical Context

**Language/Version**: Python 3.12 (stdlib only — `csv`, `re`, `argparse`, `sys`, `random`, `collections`)

**Primary Dependencies**: None (stdlib); all logic lives in `negative_audit.py`

**Storage**: CSV/Markdown. Reads: `data/negative_audit_worklist.csv`, `data/negative_audit_codings.csv`. Writes: `data/negative_audit_worklist.csv`, `data/negative_audit_report.md` (existing outputs). `--next` and `--selftest` write nothing.

**Testing**: `--selftest` pattern (no external test runner); inline fixtures only, no file I/O

**Target Platform**: Linux (GitHub Actions), macOS (local dev)

**Project Type**: CLI pipeline script (`negative_audit.py` at repo root)

**Performance Goals**: N/A — offline, ≤191 rows

**Constraints**:
- `data/negative_audit_worklist.csv` and `data/negative_audit_codings.csv` schemas MUST remain unchanged (FR-009)
- `COVERAGE_THRESHOLD = 0.80` MUST be a named module-level constant, not an inline literal (FR-004)
- `--next` MUST NOT include blocked_confirmed rows (FR-003)
- `--selftest` MUST NOT read any data file (FR-007)
- Current `main()` default behavior (regenerate worklist + report) MUST be preserved

**Scale/Scope**: 191-row worklist; 22 blocked_confirmed (purposive cell); 169 random stratum; 10 coded as of 2026-09-28

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| `python leak_audit.py --tier blocking` returns 0 | **REQUIRES ATTENTION** | The report currently passes (`print("leak audit: clean")`). New text in `--next` output (project names, state abbreviations) is unlikely to trigger scorekeeping vocabulary. Confirm after implementation. |
| `python layer_audit.py` returns 0 undeclared findings | **PASS (no change)** | No new output files. `negative_audit_*.csv` and `negative_audit_*.md` already declared in Layer E. |
| `--selftest` passes on every touched module | **REQUIRES ACTION** | `negative_audit.py` has no `--selftest`. Must add and wire into `pipeline.yml`. |
| `node --check` on touched JS | **N/A** | No JS touched |
| No em-dashes, no CRLF | **N/A** | Python source and Markdown only |
| `project_decision_dates.csv` not auto-written | **N/A** | Out of scope |

All gates are resolvable within scope. No Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/003-negative-audit-stratum/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
negative_audit.py                     # add argparse entrypoint, --next N, coverage gate,
                                      # COVERAGE_THRESHOLD constant, selftest()
.github/workflows/pipeline.yml        # add --selftest step to selftest block
```

**Structure Decision**: All changes in-place on `negative_audit.py`. No new files in the repo root. No schema changes to any data file. `pipeline.yml` gains one line.

## Complexity Tracking

> No violations. No new output files. No new data schemas. All changes are additive to `negative_audit.py`.
