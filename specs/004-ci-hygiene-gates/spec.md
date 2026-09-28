# Feature Specification: CI Hygiene and Automated Constitution Gates

**Feature Branch**: `004-ci-hygiene-gates`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 1 of the tool integration plan (`docs/tool_selection.md`, `configs/integrations.json` session 1): uv, ruff, actionlint, zizmor, pre-commit, pytest, Vale, daff, Dependabot. Also wires `integration_audit.py`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Constitution gates run before every commit (Priority: P1)

Price commits from his machine. A pre-commit configuration runs the six Constitution Check gates that can run locally: LF endings on CSVs, em-dash scan on deliverables, `leak_audit.py --tier blocking`, `node --check` on touched JS, `layer_audit.py`, and `--selftest` on touched modules. A failing gate stops the commit with the file and line.

**Why this priority**: Every gate is currently enforced by Claude during a session or by CI after the push. A gate that fires before the commit keeps a bad file out of `main`, where auto-commit workflows would otherwise build on it.

**Independent Test**: Stage a CSV with CRLF endings and a markdown file with an em-dash, run `pre-commit run --all-files`, and confirm both fail with file names. Fix both and confirm a clean pass.

**Acceptance Scenarios**:

1. **Given** a staged CSV with CRLF endings, **When** the commit runs, **Then** the hook rewrites it to LF and the commit stops so the fix can be reviewed.
2. **Given** a staged file containing a scorekeeping term outside the `INTERNAL_QUOTES` exemption, **When** the commit runs, **Then** the leak audit hook fails.
3. **Given** no touched `.py` file, **When** the commit runs, **Then** the selftest hook is skipped.

### User Story 2 - Workflow files are linted and security-audited (Priority: P1)

A new `workflow-lint.yml` runs actionlint and zizmor on any change under `.github/workflows/`. zizmor starts report-only; the plan lists the findings and fixes the high-severity ones (token scope, template injection, unpinned third-party actions).

**Why this priority**: There are 19 workflows with auto-commit, dispatch, and secret use (`IOWA_DISPATCH_TOKEN`, `NASS_API_KEY`). Trigger and race-condition bugs have already cost pipeline runs.

**Independent Test**: Introduce an undefined `${{ inputs.x }}` reference in a copy of a workflow and confirm actionlint fails the job.

**Acceptance Scenarios**:

1. **Given** a workflow edit with a syntax or expression error, **When** pushed, **Then** `workflow-lint.yml` fails and names the file and line.
2. **Given** the current workflows, **When** zizmor first runs, **Then** its findings are written to the job summary and none block the job.

### User Story 3 - Selftest coverage becomes a reported number (Priority: P2)

`tests/test_selftests.py` discovers every module that exposes `--selftest`, runs each as a pytest case, and reports modules without one. `pipeline.yml`'s blocking selftest step calls pytest instead of a hand-maintained list, so a new module is covered once it ships a selftest (Principle IX).

**Why this priority**: PHASE_STATUS reports 19 of 53 modules with selftests; the list in `pipeline.yml` must be edited by hand for each new module.

**Independent Test**: `pytest tests/test_selftests.py -q` lists one case per selftested module and prints the untested-module count.

**Acceptance Scenarios**:

1. **Given** a module whose selftest exits 1, **When** pytest runs, **Then** that case fails with the module's output.
2. **Given** `integration_audit.py`, **When** pytest runs, **Then** it is discovered and passes.

### User Story 4 - Deliverable prose is linted (Priority: P2)

Vale runs with a `Hawthorn` style over deliverable markdown (`docs/*_report*.md`, `headline_metrics.md`, generated briefs). Rules: em-dash (error), scorekeeping blocklist (error, mirrors `leak_audit.py` terms), causal phrasing such as "caused by", "due to opposition", "attributable to" (warning), and an undefined-term list (Venn-Abers, calibrated score, decile, AUC, Brier) that warns unless the term is defined in the same file.

**Why this priority**: The no-causal-language and define-on-first-use rules are currently enforced by review only.

**Independent Test**: Run `vale` on a fixture containing "the delay was caused by opposition" and "AUC of 0.81" with no definition; confirm one warning each.

**Acceptance Scenarios**:

1. **Given** an em-dash in a deliverable, **When** Vale runs, **Then** it reports an error.
2. **Given** "decile" defined in a parenthetical on first use, **When** Vale runs, **Then** no undefined-term warning appears.

### User Story 5 - Changes to the source of truth are reviewable (Priority: P3)

After the clean feed is built, daff writes `data/master_diff_summary.md` with added, removed, and changed rows of `master_opposition.csv` against the previous commit, calling out any change to `Community Outcome`, `Status`, or `outcome_defensible`.

**Independent Test**: Run the diff step against two fixture CSVs that differ in one outcome cell and confirm the summary names the row and both values.

### Edge Cases

- uv resolves a different version than pip did: pin via a lock file per job family; the plan compares resolved versions before switching.
- ruff flags existing code: only E9, F63, F7, F82 are blocking; everything else is report-only.
- Vale false positives on quoted source text: the `INTERNAL_QUOTES` pattern from `leak_audit.py` is mirrored as a Vale exception.
- Dependabot opens PRs Price does not want: grouped weekly updates, Actions and pip only.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `.pre-commit-config.yaml` MUST run the local gates in User Story 1 using `language: system` hooks that call existing repo scripts.
- **FR-002**: `.github/workflows/workflow-lint.yml` MUST run actionlint (blocking) and zizmor (report-only at first) on changes under `.github/workflows/`.
- **FR-003**: All workflows MUST install Python deps with uv against pinned constraints; resolved versions MUST match the current pip-installed versions before the switch.
- **FR-004**: `tests/test_selftests.py` MUST discover selftests by scanning for a `--selftest` argument, run each with a timeout, and report untested modules.
- **FR-005**: Vale config and the `Hawthorn` style MUST live in `.vale.ini` and `styles/Hawthorn/`.
- **FR-006**: The daff step MUST write only `data/master_diff_summary.md`, declared in `configs/layers.json`.
- **FR-007**: `integration_audit.py --selftest` MUST be in the blocking selftest step, and `python3 integration_audit.py` MUST run in `gate-check.yml`.
- **FR-008**: `.github/dependabot.yml` MUST group weekly updates for `github-actions` and `pip`.

### Key Entities

- **Gate**: one Constitution Check item, run the same way locally and in CI.
- **Selftest case**: one module's `--selftest` invocation, surfaced as a pytest case.

## Success Criteria *(mandatory)*

- **SC-001**: `pre-commit run --all-files` passes on current `main` after fixes are applied.
- **SC-002**: `workflow-lint.yml` passes actionlint on all 19 workflows.
- **SC-003**: Median `pipeline.yml` install time drops versus the last ten runs (read from Actions timings).
- **SC-004**: pytest reports the selftest count and untested-module count; both appear in the pipeline job summary.
- **SC-005**: `python leak_audit.py --tier blocking` and `python layer_audit.py` stay at 0.

## Assumptions

- Price installs pre-commit locally once (`pipx install pre-commit && pre-commit install`).
- Pending workflow changes that cannot be pushed from a sandbox are staged as `docs/pending_*.patch` with a matching `.md`.
- The 2026-09-28 `fetch-features.yml` failures (grid_generation schema drift, drought coverage) are carryover fixes for this session, outside this spec's tool scope.
