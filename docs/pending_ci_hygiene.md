# Pending: CI hygiene and automated constitution gates (spec 004)

`docs/pending_ci_hygiene.patch` holds every `.github/workflows/` change for
spec 004. It is written, linted and verified to apply cleanly to `main`.

Everything else in spec 004 is already committed:

- the pre-commit gates;
- `scripts/precommit_gates.py`;
- `tests/test_selftests.py`;
- the Vale style;
- `master_diff.py`;
- `requirements/ci.txt`, `ruff.toml` and Dependabot.

This patch is the CI half.

## Why it is a patch

Agent sessions cannot commit under `.github/workflows/`; see
`docs/pending_ci_wiring.md` for the standing reason. A person applying it is
the intended shape.

## Apply

```
git apply docs/pending_ci_hygiene.patch
git add .github/workflows && git commit -m "Apply spec 004 CI hygiene patch"
```

Before applying, `git apply --check docs/pending_ci_hygiene.patch` should print
nothing. If a workflow changed on `main` since this patch was built, re-run the
export in `specs/004-ci-hygiene-gates/tasks.md` (T031) instead of editing hunks
by hand.

## What it changes (20 files)

| File | Change |
|---|---|
| `workflow-lint.yml` (new) | actionlint, blocking (the runner's shellcheck included), and zizmor, report-only, into the job summary. Runs on any change under `.github/workflows/`. |
| `pipeline.yml` | Checkout `fetch-depth: 2`, so `master_diff.py` can compare to `HEAD~1`. Installs via uv with the pinned constraints, plus pytest, ruff, daff and vale. The hand-kept `--selftest` list is replaced by `ruff check .` and `python -m pytest tests/test_selftests.py -q`; the Node selftests and the inline-JS sweep are unchanged. A report-only ruff summary step. `python master_diff.py` and a Vale step after the feed build. `data/master_diff_summary.md` is staged in the commit step. Trigger paths gain `tests/**`, `requirements/ci.txt`, `ruff.toml`, `.vale.ini` and `styles/**`. |
| `gate-check.yml` | `python integration_audit.py` runs as a gate before the leak audit; `configs/integrations.json` and `integration_audit.py` join the trigger paths (FR-007). Summary redirects are quoted (shellcheck SC2086). |
| `fetch-permits.yml` | The `inputs.config` and `steps.cfg.outputs.configs` expansions move into `env:` (template injection, zizmor high). |
| `stakeholder-refresh.yml` | `inputs.cache_days` moves into `env:` (template injection, zizmor high). |
| `update-opposition-csv.yml` | An inline `zizmor: ignore[dangerous-triggers]` with its reason: a same-repo `workflow_run` that checks out the default branch and uses no artifacts from the triggering run. |
| `bill-sync.yml`, `local-signals.yml`, `fetch-pudl.yml` | Scoped `# shellcheck disable=SC2086` on `git add $EXISTING` and `python fetch_pudl.py $ARGS`. Those variables are deliberately word-split lists, so quoting them would break the step. |
| `scrape-trackdatacenters-proposals.yml` | The unused retry loop variable is renamed to `_` (SC2034). |
| All 19 existing workflows | Every `uses:` is pinned to a full commit SHA with its version comment (actions/checkout v4.4.0, setup-python v5.6.0, setup-node v4.4.0, github-script v7.1.0, upload-artifact v4.6.2; astral-sh/setup-uv v10.2.0 where uv is used). Dependabot keeps the SHAs current. |
| The 7 workflows that install packages | `pip install X` becomes `uv pip install --system -c requirements/ci.txt X`, after a `Set up uv` step. Each workflow still installs only its own packages; the constraints file only fixes versions. |

## Verified before committing

Everything below was run on the patched tree:

- `actionlint` exits 0 with shellcheck installed.
- `zizmor --offline` reports 0 high findings, down from 47. The 19 remaining are
  `artipacked` (medium), left report-only because most of those workflows push
  and would need a credential rewrite.
- A workflow with an undefined `inputs.nope` fails actionlint with its file and
  line.
- `pytest tests/test_selftests.py` passes 71 of 71 modules, and reports 28 of 99
  modules without a selftest.
- The 39 modules the old hand list ran are all found by discovery.
  `qc/qc_pipeline.py` is included; the list named it through a `cd qc`
  fallback.
- `requirements/ci.txt` pins pandas 3.0.6, numpy 2.4.6, scikit-learn 1.9.1,
  scipy 1.17.1 and mapie 1.5.0, exactly the versions the last CI run resolved.

## What to check after the first run

1. **Workflow Lint**: both jobs appear. actionlint is green; the zizmor summary
   lists the `artipacked` findings.
2. **Build Clean Feed**:
   - The job summary has a "Selftest coverage" section (71 modules; 28
     untested), a ruff report and a Vale section.
   - `data/master_diff_summary.md` is committed, and names the rows that
     changed since the previous commit.
3. **Gate Check**: the integration registry audit step is green.

## Job time (SC-003 and a finding)

- **Install step, before this patch**: 15 s, 15 s and 17 s on the three most
  recent successful `pipeline.yml` runs (36486798609, 36489097950,
  36489879464). The median is 15 s. Compare it with the same step after the
  patch.
- **Time the patch adds**: pytest adds about 35 s over the old gate. Most of
  that is `dispute_watch.py`, whose selftest sleeps through its 1.5 s throttle
  against stubbed calls. ruff, Vale and `master_diff.py` add about 5 s
  together.
- **Finding, independent of this patch**: the whole `build` job already takes 6
  to 7.5 minutes against `timeout-minutes: 10`. The "Verification status +
  untagged review worklist" step alone takes 4.5 to 5.5 minutes. The headroom
  is about 2.5 minutes before this patch and about 2 after. That step is the
  one to look at if the job ever times out.

## Proposal for a decision: CSV line endings

61 tracked CSVs contain CR bytes. 57 are CRLF on every line because Python's
`csv.writer` defaults to `\r\n`, and 4 mix both endings. `master_opposition.csv`
is CRLF by its writers' choice.

The pre-commit `csv-lf` hook therefore only blocks a CSV that *introduces* CR
bytes relative to its committed version, so it never churns files the pipeline
rewrites.

The repo-wide fix is one line emitted by `layer_audit.py`'s
`GITATTRIBUTES_HEADER` (`*.csv text eol=lf`), plus a one-time
`git add --renormalize '*.csv'`. It changes the stored bytes of
`master_opposition.csv`, which the Iowa tracker consumes. No reader in this
repo depends on CRLF: pandas and the `csv` module read both. It is not applied
here; it is yours to decide (research.md D3).
