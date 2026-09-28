# Research: CI Hygiene and Automated Constitution Gates

**Feature**: `specs/004-ci-hygiene-gates/` | **Date**: 2026-09-28

Every number below was measured on `main` at `1f8d348` in this session unless it
says otherwise. Tools were run at these versions: actionlint 1.7.12 (with
shellcheck), zizmor 1.30.1, ruff (repo-local), Vale 3.22.0, pre-commit 4.x,
daff (PyPI), uv (repo-local), CPython 3.11.15.

## Decision 1: How workflow changes reach `main`

**Decision**: Every change under `.github/workflows/` ships as
`docs/pending_ci_hygiene.patch` with a matching `docs/pending_ci_hygiene.md`.
This covers the new `workflow-lint.yml`, the `pipeline.yml` and `gate-check.yml`
edits, the uv switch, the zizmor fixes and the shellcheck fixes. The patch is
verified with `git apply --check` on a clean checkout of `main` and with
actionlint and zizmor run against the patched tree. Files outside
`.github/workflows/` are committed normally. That includes
`.github/dependabot.yml`, `.pre-commit-config.yaml`, `.vale.ini`, `styles/`,
`tests/`, `ruff.toml`, the constraints file and the module changes.

**Rationale**: The constitution's quality-gates section and the spec's
Assumptions both require it. `docs/pending_ci_wiring.md` records that agent
sessions are blocked from committing under `.github/workflows/`, and that the
block is intended rather than a bug to route around. Applying the patch is one
command for Price.

**Alternatives considered**: Pushing workflow files directly was rejected
because it bypasses the control. Splitting the work into one patch per story
was rejected because the stories edit the same files (`pipeline.yml` is touched
by US3, US5 and the uv switch), so separate patches would conflict with each
other.

---

## Decision 2: Local gate implementation (US1, FR-001)

**Decision**: `.pre-commit-config.yaml` uses only `repo: local`,
`language: system` hooks. The gate logic that no existing script covers goes in
one new module, `scripts/precommit_gates.py`, which has subcommands and its own
`--selftest`:

| Hook id | Command | Files | Behavior |
|---|---|---|---|
| `csv-lf` | `scripts/precommit_gates.py crlf --fix` | staged `*.csv` minus exclusions (Decision 3) | rewrites CRLF to LF, exits 1 if any file changed so the fix is reviewed |
| `deliverable-emdash` | `scripts/precommit_gates.py emdash` | deliverable globs (Decision 7) | exits 1 with `path:line` |
| `leak-audit` | `python leak_audit.py --tier blocking` | always (`pass_filenames: false`) | read-only, about 3.8 s |
| `node-check` | `scripts/precommit_gates.py nodecheck` | staged `*.js`, `*.html` | wraps `scripts/check_inline_js.py` logic on the touched files only |
| `layer-audit` | `python layer_audit.py --strict --no-write` | always | see below |
| `touched-selftest` | `scripts/precommit_gates.py selftest` | staged `*.py` | runs `--selftest` on each touched module that has one. Skipped when no `.py` is staged, which is acceptance scenario 3 |

`layer_audit.py` needs one additive flag, `--no-write`. Today the default run
rewrites `data/layer_audit.csv` and `data/layer_audit_summary.json`, as this
session saw when it dirtied the tree after a local run. It also exits 0 on
undeclared findings unless `--strict` is passed. pre-commit fails any hook that
modifies files, so a hook that writes cannot be used. `--no-write` suppresses
both writes and changes nothing else.

**Rationale**: FR-001 requires system hooks that call repo scripts. Gathering the
small checks into one module keeps a single `--selftest` surface (Principle IX)
instead of four tiny scripts.

**Alternatives considered**: `pre-commit/pre-commit-hooks` (`mixed-line-ending`,
`end-of-file-fixer`) was rejected because it is a remote hook repo, which FR-001
excludes, and because it would add an unpinned network fetch to every fresh
clone.

---

## Decision 3: CRLF finding and scope of the LF hook

**Finding**: 61 tracked CSVs contain CR bytes. 57 are CRLF on every line and 4
mix CRLF and LF. `master_opposition.csv` is CRLF on all 8,266 lines, and its
append writers (`census_gap_candidates.py:437` and `:461`) pass
`lineterminator="\r\n"` explicitly to match. The other generated files are CRLF
because Python's `csv.writer` defaults to `\r\n`. Bots commit them, so no
pre-commit hook ever sees them.

The four mixed files are a real defect: two writers with different terminators
append to one file.

| File | Lines | CRLF | Generated |
|---|---|---|---|
| `data/permit_candidates_loudoun_lola.csv` | 1,122 | 533 | no |
| `data/project_links_manual.csv` | 234 | 136 | yes |
| `data/signal_harvest_log.csv` | 141 | 71 | yes |
| `data/signal_promotion_report.csv` | 4,438 | 2,490 | yes |

**Revised at implementation (2026-09-28)**: the exclusion below could not be
built reliably. `layer_audit.py`'s write map misses 16 of the 19 non-generated
CR-bearing CSVs, among them `master_opposition_clean.csv`, `data/project_links.csv`
and `data/proposals.csv`, which are written through CLI-supplied paths. A first
`pre-commit run --all-files` rewrote them, and those rewrites were reverted. The
shipped rule needs no list: `csv-lf` fails only when a CSV *introduces* CR bytes
relative to its committed (HEAD) version, or is a new CSV that has CR bytes.
`master_opposition.csv` is always skipped. The original decision follows for the
record.

**Original decision**: The `csv-lf` hook covers hand-edited CSVs only. It excludes:

- every path `layer_audit.py --list-generated` prints, which is the same
  AST-derived write map that generates `.gitattributes`;
- `master_opposition.csv`.

Any remaining non-generated CRLF CSV is normalized in this feature's commit. That
brings SC-001 to a pass without touching the source of truth.

The repo-wide fix, a `*.csv text eol=lf` line emitted by `layer_audit.py`'s
`GITATTRIBUTES_HEADER` followed by a one-time `git add --renormalize`, is
written up as a proposal in the patch's `.md` and is **not** applied. It changes
the stored bytes of `master_opposition.csv`, the file the Iowa tracker consumes
over `csv-updated`. Principle VII makes that a decision for Price, not a
side effect of a hygiene pass.

**Evidence the proposal is safe when approved**: no reader in the repo depends
on CRLF. The only `\r\n` literals in HTML are CSV *export* joins
(`county-profile.html:2600`, `positions-dashboard.html:711`), and pandas and the
`csv` module read both line endings.

**Alternatives considered**: Changing every writer to `lineterminator="\n"` was
rejected: it touches about 30 modules and still leaves the files already on
`main` as they are. A hook that checks all CSVs was rejected because it fails on
57 files that no human commits.

---

## Decision 4: Selftest discovery (US3, FR-004)

**Finding**: 95 Python modules sit at the repo root and under `qc/` and
`scripts/`. 67 of them expose a real `--selftest` entry point
(`add_argument("--selftest"` or a `sys.argv` test), and all 67 were confirmed by
running them. `pipeline.yml`'s blocking step lists about 40 of them by hand.
PHASE_STATUS's "19 of 53" is out of date.

All 67 pass offline once the pipeline's dependencies are installed (pandas,
numpy, scikit-learn, mapie, scipy). Without them, three fail on import:
`clean_opposition_data.py`, `county_policy_intervals.py` and
`landmark_model.py`. The slowest is `dispute_watch.py` at 24.1 s wall and 0.1 s
CPU. That is its `THROTTLE_S = 1.5` sleep against stubbed calls, not network.
Everything else finishes in under 5 s.

**Decision**: `tests/test_selftests.py` discovers modules by static scan using
the same two patterns. It parametrizes one pytest case per module, id'd by path,
running `python <module> --selftest` from the repo root with a 120 s timeout. On
failure it asserts with the module's combined output. A session-scoped fixture
writes the counts (`selftested`, `untested`, and the untested list) to stdout
and, when `$GITHUB_STEP_SUMMARY` is set, to the job summary (SC-004).

`pipeline.yml`'s blocking step replaces the hand list with
`pytest tests/test_selftests.py -q`. The five Node selftests and
`scripts/check_inline_js.py` (the syntax sweep, not its selftest) stay as
explicit lines.

`integration_audit.py` is found by the scan with no special case (acceptance
scenario 2, FR-007 part one).

**Risk carried into tasks**: widening the blocking gate from about 40 to 67
modules means 27 selftests become blocking that never were. All pass today.
Their dependencies (for example `survival_model.py` and lifelines) must be in
the pipeline's install line. lifelines does not build in this sandbox (its
autograd-gamma wheel fails), so it is confirmed from the CI log, not locally.

**Alternatives considered**: An import-and-call-`selftest()` discovery was
rejected because modules name the function differently, and some run work at
import time. `pytest-timeout` was rejected in favor of `subprocess.run(...,
timeout=120)`, which avoids a plugin dependency.

---

## Decision 5: uv and pinned constraints (FR-003, SC-003)

**Finding**: 14 workflows use `setup-python` with 3.11. The six distinct install
lines combine to pandas, numpy, scipy, scikit-learn>=1.2, mapie, lifelines,
pyarrow and requests. No lockfile or requirements file exists. The PR #44 build
job (run 36485905804) resolved, on CPython 3.11.16:

```
cloudpickle-3.1.2 joblib-1.6.0 mapie-1.5.0 narwhals-2.26.0 numpy-2.4.6
pandas-3.0.6 python-dateutil-2.9.0.post0 scikit-learn-1.9.1 scipy-1.17.1
six-1.17.0 threadpoolctl-3.7.0
```

This session resolved the identical set.

**Decision**: Add `requirements/ci.in` (the union above plus pytest and daff)
and `requirements/ci.txt` compiled by `uv pip compile --python-version 3.11`.
Pins for the packages above must equal the CI-resolved versions, and the task
fails if they differ (FR-003). The remaining pins (lifelines, pyarrow, requests
and their trees) are read from their workflows' latest successful job logs
before the switch.

Workflows install with `astral-sh/setup-uv` (hash-pinned) and then
`uv pip install --system -c requirements/ci.txt <that workflow's packages>`.
Each workflow keeps its own package list, so it installs no more than it does
today. The single constraints file is what enforces "pinned".

SC-003's baseline is the median duration of the install step over the last ten
`pipeline.yml` runs, read from Actions job step timings before the patch and
recorded in the patch's `.md`.

**Alternatives considered**: One lock per job family, as the spec's edge case
suggests, was rejected. With six small, overlapping sets, one constraints file
gives the same pinning with a single source to review, and Dependabot's `pip`
ecosystem can update it. `uv sync` with a `pyproject.toml` was rejected because
the repo is a set of scripts, not a package, and adding one would be a
structural change beyond this spec.

---

## Decision 6: Linters and their day-one baselines (US2, FR-002, SC-002)

| Tool | Result on `main` | Decision |
|---|---|---|
| ruff, blocking set `E9,F63,F7,F82` | 0 | blocking in `pipeline.yml`; `ruff.toml` pins the select |
| ruff, default set | 123 (E741 32, F401 23, E731 16, F541 16, E402 11, E702 10, F841 7, other 8) | report-only step, `--exit-zero`, written to the summary |
| actionlint, no shellcheck | 0 | |
| actionlint with shellcheck, as ubuntu runners have it | 9: SC2086 (info) x8 in bill-sync, fetch-pudl, gate-check x5 and local-signals; SC2034 (warning) x1 in scrape-trackdatacenters-proposals | fix all 9 in the patch so the blocking job is green on day one |
| zizmor | 47 high, 19 medium, 1 info (94 suppressed) | report-only job summary; the patch fixes the high ones below |

The zizmor high findings the patch fixes:

- **template-injection x3**: `fetch-permits.yml:73` and `:74`
  (`github.event.inputs.config`) and `stakeholder-refresh.yml:55`
  (`inputs.cache_days`). Each moves into `env:` and is referenced as `"$VAR"`.
- **unpinned-uses x43**: every `uses:` is first-party (`actions/checkout@v4`
  x19+2, `setup-python@v5` x18, `github-script@v7` x4, `setup-node@v4`,
  `upload-artifact@v4`). All are pinned to a full commit SHA with a `# vX.Y.Z`
  comment. Dependabot's `github-actions` ecosystem keeps the SHAs current
  (FR-008).
- **dangerous-triggers x1**: `update-opposition-csv.yml` uses `workflow_run` on
  a same-repo workflow. It does not check out a PR head or use its artifacts,
  which is the unsafe pattern. It gets an inline `# zizmor: ignore[dangerous-triggers]`
  with that reason, not a trigger rewrite, because the trigger is how the
  scrape-then-update chain works today.

`artipacked` (medium, x19) stays report-only. Most of those workflows push, so
`persist-credentials: false` would need a per-workflow credential rewrite, which
is outside a hygiene pass.

---

## Decision 7: Vale scope and the Hawthorn style (US4, FR-005)

**Finding**: `docs/*_report*.md` matches nothing today, `headline_metrics.md`
has 0 em-dashes, and no generated brief exists yet. The 5 generated
`data/*_report.md` files that do contain em-dashes are internal pipeline
reports, not client deliverables.

**Decision**: `.vale.ini` targets `headline_metrics.md`, `docs/*_report*.md` and
`deliverables/**/*.md` (the future brief location that spec 010 will write to),
with `MinAlertLevel = warning`. `styles/Hawthorn/` holds:

- `EmDash.yml`: `existence`, error, token U+2014 (written as `\x{2014}` in the rule).
- `Scorekeeping.yml`: `existence`, error. Tokens mirror `leak_audit.LEAK_RE`
  (`win|wins|loss|losses|lost`, word-bounded, case-insensitive). A selftest in
  `precommit_gates.py` asserts the token list equals the regex alternation, so
  the two cannot drift.
- `Causal.yml`: `existence`, warning. Tokens are `caused by`,
  `due to (the )?opposition` and `attributable to`.
- `Undefined.yml`: `conditional`, warning. `first` matches a listed term
  (Venn-Abers, calibrated score, decile, AUC, Brier). `second` matches the same
  term followed by a parenthetical definition, so a term defined anywhere in the
  file clears its warnings (acceptance scenario 2). The fixture test proves the
  regex pair before the rule ships.

The `INTERNAL_QUOTES` exemption is file-plus-column and has no analogue in
markdown prose. It is mirrored by leaving the internal files out of Vale's
globs, and by honoring Vale's `<!-- vale off -->` for a quoted source passage.
Fixtures live in `tests/fixtures/vale/`. CI runs Vale via the `vale` PyPI
wrapper, pinned in `requirements/ci.txt`, in `pipeline.yml` after the clean
feed, blocking on errors only.

**Alternatives considered**: Pointing Vale at `data/*_report.md` was rejected
for now. It would turn 5 internal reports red and require writer changes in 5
modules. That belongs with spec 010, which owns deliverable generation.

---

## Decision 8: daff diff of the source of truth (US5, FR-006)

**Decision**: A new module, `master_diff.py`, with `--selftest`. It reads
`master_opposition.csv` at `HEAD~1` (via `git show`) and at the working tree,
diffs them with the `daff` Python library, and writes
`data/master_diff_summary.md` with three sections:

- counts of added, removed and changed rows;
- a table of every change to `Community Outcome`, `Status` or
  `outcome_defensible`, showing the row key and both values;
- the full daff highlighter output, capped at 200 rows with a pointer to the
  count.

It writes nothing else. `data/master_diff_summary.md` is declared under Layer E
in `configs/layers.json`, with an ARCHITECTURE.md line (Principle VIII).
`layer_audit.py --write-gitattributes` then picks it up automatically.
`pipeline.yml` runs it after the clean-feed build and stages it with the other
artifacts.

**Row key (resolved)**: `master_opposition.csv` has no key. `Incident` has 3,994
distinct values across 8,265 rows. No composite of Incident, Date, State,
County, Source URL and data_source is unique either, because **4,099 rows are
exact duplicates of another row on all 34 columns** (see the finding below). The
module therefore runs daff without a key, using its content-based row
alignment, and reports rows by `Incident | State | Date` for display only.

`outcome_defensible` exists only in `master_opposition_clean.csv`, which is
deduplicated (1,905 rows, 0 duplicates on the raw columns). The module runs a
second, smaller daff over the clean feed at the two revisions, keyed on
`project_id` + `Incident`. The outcome-change table is built from that run, so
`Community Outcome`, `Status` and `outcome_defensible` are all reported from a
deduplicated frame.

**Finding for Price (outside this spec)**: the exact duplicates in
`master_opposition.csv` all have `data_source = signal_harvest_auto`, and they
grow with every harvest run:

| Commit | Date | Rows | Exact duplicates |
|---|---|---|---|
| e91d833 | 2026-09-24 | 7,479 | 3,611 |
| 43c6703 | 2026-09-26 | 7,829 | 3,832 |
| 4aacebd | 2026-09-28 | 8,265 | 4,099 |

The cleaner removes them, so no client surface is affected. The raw source of
truth still carries about 50% duplicate rows, and the promote or append path is
still adding them. Spec 006 covers near-duplicate syndication, not this. It is
reported to Price as a separate defect. It is not fixed here, because it
rewrites the source of truth (Principle VII).

**Alternatives considered**: A daff CLI step inline in the workflow was rejected.
It would be a writer that no module owns, which `layer_audit.py` cannot see and
which Principle IX cannot selftest.

---

## Decision 9: `integration_audit.py` in CI (FR-007)

**Decision**: Part one needs no edit, because it is covered by the pytest
discovery (Decision 4). Part two: `gate-check.yml` gets a
`python integration_audit.py` step before the leak audit, and its `push.paths`
gain `configs/integrations.json` and `integration_audit.py`. Both changes are in
the patch.

---

## Decision 10: Dependabot (FR-008)

**Decision**: `.github/dependabot.yml` with two ecosystems:

- `github-actions` at `/`;
- `pip` at `/requirements`, which reads `ci.txt`.

Both run weekly (Monday) and each uses one `groups:` entry (`patterns: ["*"]`),
so each ecosystem opens at most one PR a week.

---

## Carryover check (spec Assumptions)

The two 2026-09-28 `fetch-features.yml` failures are already fixed on `main`.
Commit `3a9a441` (github-actions, 21:33 UTC) wrote `data/features/drought.csv`
and `data/features/grid_generation.csv` with 3,144 county rows each, and updated
`features_manifest.json`. No session 1 carryover work remains. The tasks list
records a one-line confirmation, not a fix.
