# Tasks: CI Hygiene and Automated Constitution Gates

**Input**: Design documents from `specs/004-ci-hygiene-gates/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Each new or touched module ships a `--selftest` (Principle IX). No separate test tasks are needed, because the selftest cases are part of each implementation task. The Vale fixtures in US4 are the spec's own independent test.

**Organization**: Tasks are grouped by user story. Every change under `.github/workflows/` is made in a scratch copy and exported to `docs/pending_ci_hygiene.patch` (research D1). No task commits a workflow file directly.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 to US5 from spec.md

## Path Conventions

Modules sit at the repo root, helpers in `scripts/`, the pytest wrapper and fixtures in `tests/`, and config at the root (plan.md, Project Structure).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: pinned dependencies and the scratch area for workflow edits.

- [ ] T001 Create `requirements/ci.in` listing the union of every workflow `pip install` line (pandas, numpy, scipy, "scikit-learn>=1.2", mapie, lifelines, pyarrow, requests) plus pytest>=8.4, ruff>=0.13, daff>=1.4, vale>=3.12, one per line, LF endings.
- [ ] T002 Compile `requirements/ci.txt` with `uv pip compile requirements/ci.in --python-version 3.11 -o requirements/ci.txt`. Then check that pandas==3.0.6, numpy==2.4.6, scikit-learn==1.9.1, scipy==1.17.1, mapie==1.5.0, joblib==1.6.0 and threadpoolctl==3.7.0 are pinned exactly (the CI-resolved set in research D5). If any differs, edit `requirements/ci.in` with `==` pins and recompile. If lifelines metadata fails to resolve in the sandbox, pin it to the version in the latest `retrain.yml` job log (fetch it via the GitHub MCP `get_job_logs`) and record the source in a comment at the top of `requirements/ci.in`.
- [ ] T003 [P] Create `ruff.toml` at the repo root with `[lint] select = ["E9", "F63", "F7", "F82"]` and `extend-exclude = [".specify", "node_modules"]`. Verify `ruff check .` exits 0.
- [ ] T004 Create the scratch workflow tree: copy `.github/workflows/` to `$SCRATCH/wf/.github/workflows/` (the session scratchpad), where every later `[WF]` task edits. T031 diffs it against the repo to produce the patch.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the read-only layer audit and the shared gate module that US1, US3 and US4 depend on.

- [ ] T005 Add an additive `--no-write` flag to `layer_audit.py`. When it is set, skip writing `data/layer_audit.csv` and `data/layer_audit_summary.json`; findings, stdout and exit codes (including `--strict`) stay identical. Add a selftest case that runs the audit with `--no-write` against a temp root and asserts both output paths do not exist afterwards. Verify that `python layer_audit.py --strict --no-write` exits 0 on `main` and leaves `git status` clean.
- [ ] T006 Create `scripts/precommit_gates.py` with argparse subcommands `crlf [--fix]`, `emdash`, `nodecheck`, `selftest` and `vale-sync`, plus `--selftest`, per `contracts/cli.md`. Rules:
  - `crlf` excludes every path printed by `python layer_audit.py --list-generated`, and `master_opposition.csv`.
  - `emdash` reports `path:line: em-dash` for U+2014.
  - `nodecheck` runs `node --check` on each `.js` file and on inline `<script>` blocks, reusing the extraction logic in `scripts/check_inline_js.py` (import it; do not copy it).
  - `selftest` runs `python <file> --selftest` for each given file that has a selftest entry point (same patterns as T013) and skips the rest with one line.
  - `vale-sync` compares the alternation in `leak_audit.LEAK_RE` with the tokens in `styles/Hawthorn/Scorekeeping.yml`.
  - Exit 0 when clean and 1 on any finding.
  - The selftest covers: CRLF detect and fix on a temp file; the generated-path and master exclusion; em-dash line numbers; a module without a selftest being skipped; vale-sync on a matching and a mismatched temp YAML.
  - Write no em-dashes in the source; use ASCII punctuation.

**Checkpoint**: `python scripts/precommit_gates.py --selftest` and `python layer_audit.py --selftest` pass.

---

## Phase 3: User Story 1 - Constitution gates run before every commit (Priority: P1) MVP

**Goal**: A failing gate stops a local commit and names the file and line.

**Independent Test**: quickstart.md, US1 steps 1 to 7.

- [ ] T007 [US1] Create `.pre-commit-config.yaml` with one `repo: local` block and the six hooks in `contracts/precommit-hooks.md`, in that order: `csv-lf`, `deliverable-emdash`, `leak-audit`, `node-check`, `layer-audit`, `touched-selftest`. Every hook has `language: system`. The two whole-repo audits have `pass_filenames: false`. Copy the `files` regexes verbatim from the contract.
- [ ] T008 [US1] Run `pre-commit run --all-files`. Any hand-edited CSV the `csv-lf` hook rewrites stays rewritten, provided it is outside the generated set and is not `master_opposition.csv`. Record the list of rewritten files in the commit message. Fix any em-dash in `headline_metrics.md` (0 expected). Re-run until every hook passes (SC-001).
- [ ] T009 [US1] Run quickstart US1 steps 2 to 6 with temp files (a CRLF CSV, and `docs/tmp_report.md` with an em-dash). Confirm the expected failures, pass states and the `touched-selftest` skip, then delete the temp files.

**Checkpoint**: US1 is independently usable once Price runs `pre-commit install`.

---

## Phase 4: User Story 2 - Workflow files are linted and security-audited (Priority: P1)

**Goal**: actionlint blocks broken workflows; zizmor reports without blocking, and its high findings are fixed.

**Independent Test**: quickstart.md US2, run against the scratch tree.

- [ ] T010 [P] [US2] [WF] Create `$SCRATCH/wf/.github/workflows/workflow-lint.yml`:
  - Triggers: `on: push` and `pull_request` with `paths: ['.github/workflows/**']`, plus `workflow_dispatch`.
  - `permissions: contents: read`.
  - Job `actionlint`, blocking: install with `uv tool install actionlint-py`, then run `actionlint`.
  - Job `zizmor`, `continue-on-error: true`: install with `uv tool install zizmor`, run `zizmor --format plain .github/workflows | tee zizmor.txt`, then append `zizmor.txt` to `$GITHUB_STEP_SUMMARY` inside a fenced block.
  - Use hash-pinned `actions/checkout` and `astral-sh/setup-uv` with `# vX.Y.Z` comments, and set `persist-credentials: false` on checkout.
- [ ] T011 [US2] [WF] Fix the three template injections in the scratch tree. `fetch-permits.yml:73-74` gets `env: CONFIG_INPUT: ${{ github.event.inputs.config }}`, and the script uses `"$CONFIG_INPUT"`. `stakeholder-refresh.yml:55` gets `env: CACHE_DAYS: ${{ github.event.inputs.cache_days || '14' }}` and `"$CACHE_DAYS"`. Also route `steps.cfg.outputs.configs` at `fetch-permits.yml:100` through `env`.
- [ ] T012 [US2] [WF] In the scratch tree:
  - Pin every `uses:` to a full commit SHA with a trailing `# vX.Y.Z` comment: actions/checkout v4, setup-python v5, setup-node v4, github-script v7, upload-artifact v4. Resolve each SHA with `git ls-remote https://github.com/actions/<name> refs/tags/<latest v4/v5/v7 tag>`.
  - Add `# zizmor: ignore[dangerous-triggers]` with a one-line reason to the `workflow_run` trigger in `update-opposition-csv.yml`. The reason: it is a same-repo workflow, and the job checks out no PR head and uses no artifacts.
  - Fix the 9 shellcheck findings: quote `$GITHUB_STEP_SUMMARY` and the other variables SC2086 flags in `bill-sync.yml:58`, `fetch-pudl.yml:58`, `gate-check.yml:154` (5 hits) and `local-signals.yml:74`, and rename the unused loop var `i` to `_` in `scrape-trackdatacenters-proposals.yml:42`.
  - Verify from the scratch root: `actionlint` exits 0 with shellcheck installed, and `zizmor --offline` reports 0 `template-injection`, 0 `unpinned-uses` and 0 unsuppressed `dangerous-triggers` (SC-002).
- [ ] T013 [US2] Confirm the negative case. Copy a scratch workflow, add `run: echo ${{ inputs.nope }}`, and run `actionlint` on the copy. It must fail and name the file and line. Delete the copy afterwards.

**Checkpoint**: the scratch tree lints clean. US2 lands when Price applies the patch.

---

## Phase 5: User Story 3 - Selftest coverage becomes a reported number (Priority: P2)

**Goal**: pytest discovers and runs every selftest and reports the untested count, replacing the hand-kept list.

**Independent Test**: `pytest tests/test_selftests.py -q` prints 67 cases and `untested: 28 of 95` (quickstart US3).

- [ ] T014 [US3] Create `tests/test_selftests.py`:
  - Scan `*.py` at the root, `qc/*.py` and `scripts/*.py`, excluding `tests/` and `.github/`. A module has a selftest when it matches `add_argument\(["']--selftest` or `["']--selftest["']\s*(in|==)`.
  - `pytest.mark.parametrize` one case per module, with `ids=` the repo-relative path.
  - Each case runs `subprocess.run([sys.executable, path, "--selftest"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)`. On a non-zero exit or `TimeoutExpired`, fail with the last 60 lines of combined output.
  - A session-scoped autouse fixture prints `selftests: <n> modules; untested: <m> of <total>` plus the untested list, and, when `GITHUB_STEP_SUMMARY` is set, appends a `## Selftest coverage` section to it.
  - Add a `tests/conftest.py` only if needed for the fixture. Do not add a pytest plugin dependency.
- [ ] T015 [US3] Run `pytest tests/test_selftests.py -q` with the `requirements/ci.txt` packages installed. Expect 67 passed, plus 2 more for the new selftest-bearing modules added by this spec (T006, T026), so 69 in total. Confirm `-k integration_audit` selects and passes 1 case. Temporarily make one module's selftest return 1 and confirm the case fails with its output, then revert.
- [ ] T016 [US3] [WF] In the scratch `pipeline.yml`, replace the hand-listed python `--selftest` lines in the step whose name starts with "Gate" and ends with "run self-tests" (its name contains an em-dash; leave the name unchanged) with `pytest tests/test_selftests.py -q`. Keep `python scripts/check_inline_js.py` (the syntax sweep) and the five `node *_selftest.js` lines. Add `pytest` to that workflow's install. Add a `ruff check .` step before the gate (blocking via `ruff.toml`), and a report-only `ruff check --select E,F --exit-zero --statistics . >> $GITHUB_STEP_SUMMARY` step.
- [ ] T017 [P] [US3] Update `PHASE_STATUS.md`: change the selftest coverage figure from "19 of 53" to the pytest-reported count (69 of 97 after this spec), and note that coverage is now discovered, not hand-listed.

**Checkpoint**: pytest reports the counts locally, and the pipeline change is in the scratch tree.

---

## Phase 6: User Story 4 - Deliverable prose is linted (Priority: P2)

**Goal**: Vale enforces the em-dash, scorekeeping, causal-language and define-on-first-use rules on deliverables.

**Independent Test**: quickstart.md US4 steps 1 to 5.

- [ ] T018 [P] [US4] Create `.vale.ini`: `StylesPath = styles`, `MinAlertLevel = warning`, and `[{headline_metrics.md,docs/*_report*.md,deliverables/**/*.md,tests/fixtures/vale/*.md}]` with `BasedOnStyles = Hawthorn`.
- [ ] T019 [P] [US4] Create `styles/Hawthorn/EmDash.yml`: `extends: existence`, `level: error`, `message: "Em-dash: use a comma, colon or parentheses."`, `nonword: true`, `tokens: ['\x{2014}']`.
- [ ] T020 [P] [US4] Create `styles/Hawthorn/Scorekeeping.yml`: `extends: existence`, `level: error`, `ignorecase: true`, and `tokens` equal to the alternation in `leak_audit.LEAK_RE` (win, wins, loss, losses, lost), with a message pointing to the README outcome ladder.
- [ ] T021 [P] [US4] Create `styles/Hawthorn/Causal.yml`: `extends: existence`, `level: warning`, `ignorecase: true`, `tokens: ['caused by', 'due to (?:the )?opposition', 'attributable to']`, with a message citing IDENTIFIABILITY.md.
- [ ] T022 [P] [US4] Create `styles/Hawthorn/Undefined.yml`: `extends: conditional`, `level: warning`, `ignorecase: false`, `first: '\b(Venn-Abers|calibrated score|decile|AUC|Brier)\b'`, `second: '\b(Venn-Abers|calibrated score|decile|AUC|Brier)\s*\([^)]{6,}\)'`, with the message "'%s' is used without a definition on first use." Verify the conditional rule captures group 1 on both patterns. If Vale 3.x needs `exceptions` instead, adjust and record the change in research.md D7.
- [ ] T023 [US4] Create the fixtures in `tests/fixtures/vale/`. No filename may end in `_report.md` (plan, gate 1).
  - `causal_and_undefined.md`: the text "The delay was caused by local review." and "The model reached an AUC of 0.81."
  - `defined_decile.md`: "Counties in the top decile (one of ten equal-sized groups) ..." and a later bare "decile".
  - `emdash.md`: one em-dash.
  - `scorekeeping.md`: one listed term.
  - Run `vale` on each and confirm exactly the alerts in quickstart US4. Then confirm `python scripts/precommit_gates.py vale-sync` exits 0 and `vale headline_metrics.md` reports 0 errors.
- [ ] T024 [US4] [WF] In the scratch `pipeline.yml`, after the clean-feed build, add a step `vale --minAlertLevel=error headline_metrics.md docs deliverables` (blocking on errors only; skip missing paths with `--glob`), plus `vale --output=line ... >> $GITHUB_STEP_SUMMARY || true` for warnings. Install `vale` from `requirements/ci.txt`, and run `vale sync` only if `.vale.ini` gains `Packages` (it does not today).

**Checkpoint**: the Vale rules are proven on fixtures, and the CI step is in the scratch tree.

---

## Phase 7: User Story 5 - Changes to the source of truth are reviewable (Priority: P3)

**Goal**: every pipeline run writes a readable diff of `master_opposition.csv` and of the outcome fields.

**Independent Test**: `python master_diff.py --selftest`, and a real run against `HEAD~1` (quickstart US5).

- [ ] T025 [US5] Declare `data/master_diff_summary.md` in the `files` list of Layer E in `configs/layers.json`. Add one line to ARCHITECTURE.md's Layer E section naming `master_diff.py` as its sole writer.
- [ ] T026 [US5] Create `master_diff.py` per `contracts/cli.md` and `contracts/master-diff-summary.md`:
  - `--base` defaults to `HEAD~1`, read via `git show <rev>:<path>`. `--out` defaults to `data/master_diff_summary.md`.
  - A keyless `daff` comparison of `master_opposition.csv` produces the added, removed and modified counts plus up to 200 highlighter rows.
  - A second `daff` comparison of `master_opposition_clean.csv`, keyed on `project_id` and `Incident`, fills the outcome-change table for `Community Outcome`, `Status` and `outcome_defensible`. Rows are labeled `Incident (State, Date)`.
  - Handle the "no prior revision" and "no changes" states with exit 0.
  - Escape `|` and newlines in cells. Write LF endings, ASCII punctuation, and no em-dashes.
  - The `--selftest` builds two in-memory frames that differ in one `Community Outcome` cell and asserts that the rendered summary contains that row label and both values. It touches no repo file.
- [ ] T027 [US5] Run `python master_diff.py`. Confirm that `data/master_diff_summary.md` is written, that `git status` shows only that file, and that `python leak_audit.py --tier blocking` stays at 0. Then run `python layer_audit.py --write-gitattributes` and `python layer_audit.py --strict --no-write`: 0 undeclared, and `.gitattributes` gains the new path.
- [ ] T028 [US5] [WF] In the scratch `pipeline.yml`:
  - Set `fetch-depth: 2` on the checkout.
  - Add `python master_diff.py` after the clean-feed build.
  - Stage `data/master_diff_summary.md` in the existing commit step.
  - Add `daff` to the install.

**Checkpoint**: the summary is generated locally, and the CI wiring is in the scratch tree.

---

## Phase 8: Polish and Cross-Cutting Concerns

- [ ] T029 [P] Create `.github/dependabot.yml` (version 2):
  - `package-ecosystem: github-actions`, `directory: /`.
  - `package-ecosystem: pip`, `directory: /requirements`.
  - Both use `schedule.interval: weekly` with `day: monday`, plus `groups: {all: {patterns: ["*"]}}` (FR-008).
- [ ] T030 [WF] Apply the uv switch in all 14 scratch workflows that use `setup-python`:
  - Add a hash-pinned `astral-sh/setup-uv` step.
  - Replace each `pip install <pkgs>` with `uv pip install --system -c requirements/ci.txt <same pkgs>`, adding pytest, daff and vale only where T016, T024 and T028 need them.
  - Add `python integration_audit.py` to scratch `gate-check.yml` before "Leak audit on everything written (GATE)", and add `configs/integrations.json` and `integration_audit.py` to its `push.paths` (FR-007).
  - Re-run actionlint and zizmor on the scratch tree.
- [ ] T031 Export the patch. From the repo root, run `diff -ruN .github/workflows $SCRATCH/wf/.github/workflows`, rewritten into `git apply` form, or better, apply the scratch files onto a temporary `git worktree` of HEAD and run `git diff` there. Write the result to `docs/pending_ci_hygiene.patch`. Verify with `git apply --check docs/pending_ci_hygiene.patch` on a clean checkout, then apply it to a temporary worktree and run actionlint and zizmor there.
- [ ] T032 Write `docs/pending_ci_hygiene.md`, modeled on `docs/pending_ci_wiring.md`. Cover:
  - what the patch changes, per file;
  - why it is a patch (research D1);
  - the one-line apply command;
  - what to check after the first run: the workflow-lint result, the selftest coverage section, the vale step, and `data/master_diff_summary.md`;
  - the SC-003 baseline: the median install-step duration over the last 10 `pipeline.yml` runs, from the GitHub MCP `actions_list` and job steps;
  - the CSV `eol=lf` proposal from research D3, stated as a decision for Price.

  This file is in the leak_audit BLOCKING scope, so it must contain none of the listed terms.
- [ ] T033 Final gates (plan, Constitution Check):
  - `python leak_audit.py --tier blocking` returns 0;
  - `python layer_audit.py --strict --no-write` returns 0;
  - `python layer_audit.py --check-gitattributes` returns 0;
  - `python integration_audit.py` is clean;
  - `pytest tests/test_selftests.py -q` passes in full;
  - `ruff check .` returns 0;
  - `pre-commit run --all-files` passes;
  - no em-dash or CRLF in any new file (`grep -rlP '\x{2014}|\r'` over the new paths).
- [ ] T034 Confirm the session 1 carryover in the commit message (research, "Carryover check"): `data/features/drought.csv` and `grid_generation.csv` hold 3,144 counties on `main` (commit `3a9a441`), so there is nothing to fix.

---

## Dependencies & Execution Order

- **Setup (T001 to T004)** comes first. T002 depends on T001.
- **Foundational (T005, T006)** depends on Setup and blocks US1, US3 (T015 counts T006's selftest) and US4 (T023 runs vale-sync).
- **US1 (T007 to T009)** depends on T005 and T006.
- **US2 (T010 to T013)** depends only on T004 and can run in parallel with US1.
- **US3 (T014 to T017)** depends on T002 (deps) and T006. T016 depends on T004.
- **US4 (T018 to T024)** depends on T006 for vale-sync. T018 to T022 can run in parallel.
- **US5 (T025 to T028)** depends on T002 (daff) and T005 (the `--no-write` check in T027).
- **Polish**: T030 follows T010 to T012, T016, T024 and T028, because they edit the same scratch files. T031 follows T030, T032 follows T031, and T033 is last.

### User story independence

- US1, US2 and US5 each stand alone after Foundational.
- US3 and US4 share only `scripts/precommit_gates.py`.
- All `[WF]` tasks touch the scratch tree, so they serialize on `pipeline.yml` (T016, T024, T028, T030).

## Parallel Example

```text
After T006:
  US1: T007 -> T008 -> T009
  US2: T010, then T011 + T012 (different workflow files) -> T013
  US4: T018, T019, T020, T021, T022 together -> T023
```

## Implementation Strategy

1. **MVP**: Setup, Foundational and US1. The pre-commit gates run on Price's machine with no workflow change.
2. Add US3 and US4 locally (pytest and Vale work without CI), and US5 (the module and layer declaration).
3. Build the scratch workflow tree (US2, and the CI halves of US3, US4 and US5), then T030.
4. Export and verify the single patch (T031, T032). CI changes take effect when Price applies it.
5. Run the final gates (T033) and commit everything outside `.github/workflows/`.
