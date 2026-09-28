# Quickstart: validating spec 004

Run everything from the repo root. Prerequisites:

```
uv pip install --system -c requirements/ci.txt pandas numpy scikit-learn scipy mapie pytest daff vale ruff
pipx install pre-commit    # or: uv tool install pre-commit
uv tool install actionlint-py shellcheck-py zizmor    # for workflow checks only
```

## US1: gates before commit

1. `pre-commit install`
2. Write a CSV with CRLF endings (`printf 'a,b\r\n1,2\r\n' > tmp_crlf.csv`) and
   a deliverable containing an em-dash
   (`printf 'x \xe2\x80\x94 y\n' > docs/tmp_report.md`), then stage both.
3. `pre-commit run --all-files`. **Expect**: `csv-lf` fails and rewrites the
   CSV; `deliverable-emdash` fails with `docs/tmp_report.md:1`.
4. Run it again. **Expect**: `csv-lf` passes (already fixed) and em-dash still
   fails.
5. Remove the em-dash and run again. **Expect**: all hooks pass. Delete both
   temp files.
6. Stage only a `.md` file and run `pre-commit run`. **Expect**:
   `touched-selftest` shows "Skipped".
7. SC-001: `pre-commit run --all-files` on a clean tree passes.

## US2: workflow lint

Apply the patch first with `git apply docs/pending_ci_hygiene.patch`.

1. `actionlint`. **Expect**: exit 0 with shellcheck installed (SC-002, 19
   workflows plus `workflow-lint.yml`).
2. `zizmor --offline .github/workflows`. **Expect**: no `template-injection` or
   `dangerous-triggers` at high severity, and no `unpinned-uses`.
3. Copy a workflow, add `run: echo ${{ inputs.nope }}`, and run `actionlint` on
   it. **Expect**: it fails and names the file and line.

## US3: selftests via pytest

1. `pytest tests/test_selftests.py -q`. **Expect**: 69 passed (67 on `main`
   today plus `scripts/precommit_gates.py` and `master_diff.py`), and the summary
   line `selftests: 69 modules; untested: 28 of 97`.
2. `pytest tests/test_selftests.py -q -k integration_audit`. **Expect**: 1
   passed.
3. Temporarily make a module's selftest `sys.exit(1)`. **Expect**: that case
   fails and shows the module's output. Revert afterwards.

## US4: Vale

1. `vale tests/fixtures/vale/causal_and_undefined.md`. **Expect**: exactly one
   `Hawthorn.Causal` warning and one `Hawthorn.Undefined` warning (for AUC).
2. `vale tests/fixtures/vale/defined_decile.md`. **Expect**: no
   `Hawthorn.Undefined`.
3. `vale tests/fixtures/vale/emdash.md`. **Expect**: one `Hawthorn.EmDash`
   error.
4. `python scripts/precommit_gates.py vale-sync`. **Expect**: exit 0.
5. `vale headline_metrics.md`. **Expect**: 0 errors.

## US5: source-of-truth diff

1. `python master_diff.py --selftest`. **Expect**: pass. The fixture's changed
   `Community Outcome` row and both values appear in the summary.
2. `python master_diff.py`. **Expect**: `data/master_diff_summary.md` is
   written, with counts against `HEAD~1`, and nothing else in `git status`.

## Constitution gates (SC-005)

```
python leak_audit.py --tier blocking     # 0 blocking
python layer_audit.py --strict --no-write  # 0 undeclared, tree unchanged
python layer_audit.py --check-gitattributes
python integration_audit.py              # clean
ruff check --select E9,F63,F7,F82 .      # 0
git status --short                       # only intended files
```

## Patch application check

On a fresh clone of `main`, `git apply --check docs/pending_ci_hygiene.patch`
exits 0.
