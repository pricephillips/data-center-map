# Implementation Plan: CI Hygiene and Automated Constitution Gates

**Branch**: `004-ci-hygiene-gates` (worked on `claude/trusting-feynman-twgsr6`) | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-ci-hygiene-gates/spec.md`

## Summary

The six local Constitution Check gates get a `.pre-commit-config.yaml` built
only from `language: system` hooks. The gate logic that no existing script
covers goes in one new module, `scripts/precommit_gates.py`. `layer_audit.py`
gains a `--no-write` flag so it can run as a hook.

The hand-kept selftest list in `pipeline.yml` becomes pytest discovery
(`tests/test_selftests.py`). That takes the blocking gate from about 40 modules
to all 67 that have a selftest, and `integration_audit.py` is picked up
automatically.

The plan also adds:

- a Vale `Hawthorn` style for deliverable prose;
- `master_diff.py`, which writes `data/master_diff_summary.md` with daff after
  each feed build;
- ruff in the pipeline (blocking on syntax-class rules only);
- Dependabot;
- uv installs against a constraints file pinned to the versions CI resolves
  today.

Every change under `.github/workflows/` ships as `docs/pending_ci_hygiene.patch`
with a matching `.md`, per the constitution. That patch covers the new
`workflow-lint.yml`, the pipeline and gate-check edits, the uv switch, 3
template-injection fixes, SHA pins for 43 action references, and fixes for the
9 shellcheck findings the runners would report.

## Technical Context

**Language/Version**: Python 3.11 (CI resolves CPython 3.11.16). Node 20 in CI for the existing JS selftests.

**Primary Dependencies**:

- New CI and dev tools: pytest >=8.4, ruff >=0.13, pre-commit >=4.0 (local only), Vale >=3.12 (PyPI wrapper), daff >=1.4 (Python library), uv >=0.8, actionlint >=1.7, zizmor >=1.0.
- Existing runtime, pinned unchanged: pandas 3.0.6, numpy 2.4.6, scikit-learn 1.9.1, scipy 1.17.1, mapie 1.5.0.
- Also pinned: lifelines, pyarrow and requests, at the versions in their workflows' latest logs.

**Storage**: Files only.

- New writes: `data/master_diff_summary.md` (Layer E).
- New tracked config: `.pre-commit-config.yaml`, `.vale.ini`, `styles/Hawthorn/*.yml`, `ruff.toml`, `requirements/ci.in`, `requirements/ci.txt`, `.github/dependabot.yml`.
- No schema changes to any data file.

**Testing**:

- The `--selftest` pattern on each new or touched module (`scripts/precommit_gates.py`, `master_diff.py`, `layer_audit.py`).
- `tests/test_selftests.py`, the new pytest wrapper over all selftests.
- Vale fixtures in `tests/fixtures/vale/`.
- actionlint and zizmor run against the patched tree.

**Target Platform**: GitHub Actions `ubuntu-latest`, and Price's macOS machine for pre-commit.

**Project Type**: Script-based data pipeline, with root-level modules and CI workflows.

**Performance Goals**:

- The pre-commit run on a typical commit stays under about 10 s (leak audit about 3.8 s, layer audit under 1 s, touched selftests).
- The pipeline selftest step stays inside the job's 10-minute timeout. Serial total is about 60 s, most of it `dispute_watch.py` at 24 s.

**Constraints**:

- No commits under `.github/workflows/`; that work goes in the patch (research D1).
- `master_opposition.csv` bytes unchanged (Principle VII; research D3).
- Constraint pins MUST equal the CI-resolved versions (FR-003).
- zizmor stays report-only at first (FR-002).
- ruff blocks only on `E9,F63,F7,F82`.

**Scale/Scope**:

- 95 Python modules, 67 with selftests, 19 workflows.
- 123 existing ruff default-set findings, left report-only.
- 61 CR-bearing CSVs, of which 57 are generated and are excluded from the hook.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| 1. `leak_audit.py --tier blocking` = 0 | **PASS (verify at end)** | Returns 0 on `main` today. `leak_audit.classify` puts a `tests/fixtures/vale/*.md` hit in the ADVISORY tier, because it matches no GENERATED glob. That holds only if no fixture filename ends in `_report.md`, which would make it BLOCKING, so the Scorekeeping positive fixture is safe. `docs/pending_ci_hygiene.md` falls under the `docs/*.md` BLOCKING glob and must contain no listed term. |
| 2. `layer_audit.py` = 0 undeclared | **REQUIRES ACTION** | `master_diff.py` is a new writer. Declare `data/master_diff_summary.md` under Layer E in `configs/layers.json`, add the ARCHITECTURE.md line, then run `layer_audit.py --write-gitattributes`. |
| 3. `--selftest` on touched/new modules | **REQUIRES ACTION** | New selftests in `scripts/precommit_gates.py` and `master_diff.py`, and a `--no-write` case in `layer_audit.py`. All three are discovered by `tests/test_selftests.py`, which satisfies Principle IX with no hand list. |
| 4. `node --check` on touched JS | **N/A** | No JS touched. The `node-check` hook wraps the existing checker. |
| 5. No em-dashes, no CRLF | **PASS (verify at end)** | New files are LF. `csv-lf` covers hand-edited CSVs; generated CSVs and `master_opposition.csv` are out of scope, and research D3 gives the approved-later path. |
| 6. docx `--original` validation | **N/A** | No docx. |

**Post-design re-check**: all gates stay resolvable within scope. Two items need
Price's decision and are written up rather than acted on:

- the repo-wide CSV `eol=lf` normalization (D3);
- the growing exact-duplicate rows in `master_opposition.csv` (D8 finding).

Neither blocks this spec.

## Project Structure

### Documentation (this feature)

```text
specs/004-ci-hygiene-gates/
├── plan.md              # This file
├── research.md          # Phase 0: decisions D1-D10, measured baselines, findings
├── data-model.md        # Phase 1: gates, hooks, selftest cases, diff summary
├── quickstart.md        # Phase 1: validation scenarios per user story
├── contracts/
│   ├── precommit-hooks.md      # hook ids, commands, file scopes, exit codes
│   ├── cli.md                  # precommit_gates.py, master_diff.py, layer_audit --no-write
│   └── master-diff-summary.md  # output format of data/master_diff_summary.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
.pre-commit-config.yaml            # NEW  local system hooks (US1)
scripts/precommit_gates.py         # NEW  crlf | emdash | nodecheck | selftest | vale-sync; --selftest
layer_audit.py                     # EDIT add --no-write (additive)
tests/test_selftests.py            # NEW  discovery + parametrized selftest cases (US3)
tests/fixtures/vale/*.md           # NEW  Vale rule fixtures (US4)
.vale.ini                          # NEW  (US4)
styles/Hawthorn/EmDash.yml         # NEW
styles/Hawthorn/Scorekeeping.yml   # NEW  mirrors leak_audit.LEAK_RE
styles/Hawthorn/Causal.yml         # NEW
styles/Hawthorn/Undefined.yml      # NEW
master_diff.py                     # NEW  daff summary writer (US5); --selftest
configs/layers.json                # EDIT declare data/master_diff_summary.md (Layer E)
ARCHITECTURE.md                    # EDIT one line for the new writer
.gitattributes                     # REGEN via layer_audit.py --write-gitattributes
ruff.toml                          # NEW  blocking select E9,F63,F7,F82
requirements/ci.in                 # NEW  union of workflow deps + pytest, daff, vale, ruff
requirements/ci.txt                # NEW  uv pip compile output, pins == CI-resolved
.github/dependabot.yml             # NEW  github-actions + pip, weekly, grouped (FR-008)
docs/pending_ci_hygiene.patch      # NEW  all .github/workflows/ changes (see below)
docs/pending_ci_hygiene.md         # NEW  what the patch does, how to apply, what to check,
                                   #      SC-003 baseline, eol=lf proposal
PHASE_STATUS.md                    # EDIT selftest count 19/53 -> 67/95
```

The patch touches these workflow files:

```text
.github/workflows/workflow-lint.yml     # NEW  actionlint (blocking) + zizmor (report-only)
.github/workflows/pipeline.yml          # uv; pytest selftests; ruff; Vale; master_diff
.github/workflows/gate-check.yml        # uv; integration_audit.py step; paths; SC2086
.github/workflows/fetch-permits.yml     # template-injection x2 -> env
.github/workflows/stakeholder-refresh.yml  # template-injection x1 -> env
.github/workflows/update-opposition-csv.yml  # zizmor ignore with reason
.github/workflows/{bill-sync,fetch-pudl,local-signals,scrape-trackdatacenters-proposals}.yml  # shellcheck
.github/workflows/*.yml                 # SHA-pin uses:, uv install (14 files)
```

**Structure Decision**: Everything lives at the repo root, following existing
practice: modules at the root and helpers in `scripts/`. `tests/` is new and
holds only the pytest wrapper and fixtures. It is not a second test framework,
because every test is still a module's own `--selftest`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New top-level `tests/` directory | pytest needs a collection root for the discovery wrapper (FR-004) | Putting the wrapper in `scripts/` mixes a test harness with pipeline helpers, and pytest's default discovery would scan pipeline scripts |
| Selftest gate widened from about 40 to 67 modules | FR-004: coverage follows from a module shipping a selftest, not from a hand edit | Keeping the hand list is the defect US3 removes. All 67 pass today with the pinned dependencies |
