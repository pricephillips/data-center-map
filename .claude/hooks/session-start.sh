#!/bin/bash
# SessionStart hook: tools every session on this repo expects.
#
# Everywhere (cloud and local): ast-grep and the DuckDB CLI (session 0 of the
# tool plan, configs/integrations.json). Installed only when missing, through
# uv, and never fatal: a machine without uv gets a notice, not a failed start.
#
# Cloud sessions only: the pipeline's Python packages at the versions pinned in
# requirements/ci.txt, plus the spec 004 checkers, so pre-commit, pytest
# selftests, ruff, Vale, actionlint and zizmor run the same way CI does. Local
# machines are left alone; see .pre-commit-config.yaml for local setup.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0
export PATH="$HOME/.local/bin:$PATH"

have() { command -v "$1" >/dev/null 2>&1; }

tool() {  # tool <command> <uv package>
  have "$1" && return 0
  if have uv; then
    uv tool install -q "$2" >/dev/null 2>&1 || echo "session-start: could not install $2" >&2
  else
    echo "session-start: $1 missing and uv not found; install $2 by hand" >&2
  fi
}

tool ast-grep ast-grep-cli
tool duckdb duckdb-cli

if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && have uv; then
  # lifelines is left out: its autograd-gamma wheel does not build here, and no
  # selftest needs it. retrain.yml installs it on the runner.
  uv pip install --system -q -c requirements/ci.txt \
    pandas numpy scipy "scikit-learn>=1.2" mapie pyarrow openpyxl requests \
    pytest ruff daff vale >/dev/null 2>&1 \
    || echo "session-start: python package install failed" >&2
  tool pre-commit pre-commit
  tool actionlint actionlint-py
  tool shellcheck shellcheck-py
  tool zizmor zizmor
fi

exit 0
