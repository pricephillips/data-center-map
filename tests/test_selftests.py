"""
Every module's --selftest as one pytest case (spec 004, US3; Principle IX).

Discovery replaces the hand-kept list in pipeline.yml: a module is covered the
moment it ships a selftest. The scan uses the same patterns as
scripts/precommit_gates.py. Run from anywhere:

    pytest tests/test_selftests.py -q
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELFTEST_RE = re.compile(
    r"""add_argument\(\s*["']--selftest|["']--selftest["']\s*(?:in|==)""")
TIMEOUT_S = 120


def _scan() -> tuple[list[str], list[str]]:
    paths = sorted(set(glob.glob(os.path.join(ROOT, "*.py"))
                       + glob.glob(os.path.join(ROOT, "qc", "*.py"))
                       + glob.glob(os.path.join(ROOT, "scripts", "*.py"))))
    tested, untested = [], []
    for p in paths:
        rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
        with open(p, encoding="utf-8", errors="replace") as fh:
            (tested if SELFTEST_RE.search(fh.read()) else untested).append(rel)
    return tested, untested


TESTED, UNTESTED = _scan()


@pytest.fixture(scope="session", autouse=True)
def coverage_summary():
    yield
    total = len(TESTED) + len(UNTESTED)
    line = f"selftests: {len(TESTED)} modules; untested: {len(UNTESTED)} of {total}"
    print("\n" + line)
    for rel in UNTESTED:
        print(f"  untested: {rel}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## Selftest coverage\n\n{line}\n\n")
            fh.write("".join(f"- `{rel}`\n" for rel in UNTESTED) + "\n")


@pytest.mark.parametrize("module", TESTED, ids=TESTED)
def test_selftest(module: str) -> None:
    try:
        r = subprocess.run([sys.executable, module, "--selftest"], cwd=ROOT,
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") + (exc.stderr or "")) if isinstance(exc.stdout, str) else ""
        pytest.fail(f"{module} --selftest timed out after {TIMEOUT_S}s\n"
                    + "\n".join(out.splitlines()[-60:]))
    if r.returncode != 0:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-60:])
        pytest.fail(f"{module} --selftest exited {r.returncode}\n{tail}")
