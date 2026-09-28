# Contract: pre-commit hooks

File: `.pre-commit-config.yaml`. Every hook is `repo: local` and
`language: system` (FR-001). Install once with
`pipx install pre-commit && pre-commit install`.

| Hook id | Entry | Files | pass_filenames | Exit 0 when | Exit 1 when |
|---|---|---|---|---|---|
| `csv-lf` | `python scripts/precommit_gates.py crlf --fix` | `\.csv$`; `master_opposition.csv` is skipped inside the script | true | no staged CSV introduces CR bytes relative to HEAD (a new CSV has no baseline, so it must be LF) | any file was rewritten; prints `fixed CRLF -> LF: <path>` per file |
| `deliverable-emdash` | `python scripts/precommit_gates.py emdash` | `^(headline_metrics\.md\|docs/.*_report.*\.md\|deliverables/.*\.md)$` | true | no U+2014 | prints `<path>:<line>: em-dash` per hit |
| `leak-audit` | `python leak_audit.py --tier blocking` | `\.(py\|md\|html\|csv\|json\|ya?ml)$` (trigger only) | false | 0 blocking hits | any blocking hit, in leak_audit's own format |
| `node-check` | `python scripts/precommit_gates.py nodecheck` | `\.(js\|html)$` | true | `node --check` passes on each file and each inline script block | prints the file and node's error |
| `layer-audit` | `python layer_audit.py --strict --no-write` | `\.py$\|^configs/layers\.json$` (trigger only) | false | 0 undeclared findings | any undeclared finding |
| `touched-selftest` | `python scripts/precommit_gates.py selftest` | `\.py$` | true | every staged module that has a selftest passes | prints the module and the tail of its output |

Hooks run in the order listed. `csv-lf` runs first so a rewritten CSV does not
also fail later hooks on the same run.

`touched-selftest` skips staged files that have no selftest entry point and
says so in one line. When no `.py` file is staged, pre-commit skips the hook,
as acceptance scenario 3 requires.
