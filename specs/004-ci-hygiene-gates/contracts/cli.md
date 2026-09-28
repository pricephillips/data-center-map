# Contract: command-line interfaces

## `scripts/precommit_gates.py` (new)

```
python scripts/precommit_gates.py crlf [--fix] FILE...
python scripts/precommit_gates.py emdash FILE...
python scripts/precommit_gates.py nodecheck FILE...
python scripts/precommit_gates.py selftest FILE...
python scripts/precommit_gates.py vale-sync
python scripts/precommit_gates.py --selftest
```

| Subcommand | Reads | Writes | Exit |
|---|---|---|---|
| `crlf` | the given files, and `layer_audit.py --list-generated` for exclusions | only with `--fix`, the given in-scope files, in place | 0 if clean; 1 if any CR found (with or without `--fix`) |
| `emdash` | the given files | nothing | 0 if clean; 1 with `path:line` per hit |
| `nodecheck` | the given files; inline `<script>` blocks are extracted the way `scripts/check_inline_js.py` does | temp files only | 0 if clean; 1 on any syntax error |
| `selftest` | the given files | nothing | 0 if every file that has a selftest passes; 1 otherwise |
| `vale-sync` | `leak_audit.LEAK_RE`, `styles/Hawthorn/Scorekeeping.yml` | nothing | 0 if the token sets are equal; 1 if they differ, with the difference shown |
| `--selftest` | nothing on disk; inline fixtures and a temp dir | temp dir only | 0 or 1 |

The selftest must cover:

- CRLF detection and rewrite;
- the generated-path and `master_opposition.csv` exclusion;
- em-dash line numbers;
- a module with no selftest being skipped;
- vale-sync equality against the real repo files.

## `master_diff.py` (new)

```
python master_diff.py [--base REV] [--out data/master_diff_summary.md]
python master_diff.py --selftest
```

- `--base` defaults to `HEAD~1`.
- It reads `master_opposition.csv` and `master_opposition_clean.csv` at `--base`
  via `git show`, and from the working tree.
- It writes only `--out` (FR-006).
- Exit 0 on success, including the "no prior revision" and "no changes" states.
  Exit 1 only when a CSV cannot be parsed.
- The selftest uses two inline fixture frames that differ in one
  `Community Outcome` cell. It asserts that the summary names the row and both
  values, which is the spec's independent test for US5.

**CI requirement**: the pipeline checkout needs `fetch-depth: 2`. The default
depth of 1 has no `HEAD~1`, and without it every run would write "no prior
revision".

## `layer_audit.py --no-write` (new flag, additive)

This flag suppresses writing `data/layer_audit.csv` and
`data/layer_audit_summary.json`. Findings, stdout and exit codes are otherwise
identical, and `--strict` still exits non-zero on any undeclared finding.
Default behavior without the flag is unchanged (Principle VII).

A new selftest case asserts that both output paths are untouched after a
`--no-write` run on a temp root.

## `tests/test_selftests.py` (new)

```
pytest tests/test_selftests.py -q
```

- One case per discovered module, with the case id equal to the module path.
- It prints `selftests: <n> modules; untested: <m> of <total>` and the untested
  list.
- When `$GITHUB_STEP_SUMMARY` is set, it appends a `## Selftest coverage`
  section there.
