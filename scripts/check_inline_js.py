#!/usr/bin/env python3
"""
check_inline_js.py

Syntax gate for the front end. Runs `node --check` over every standalone .js
module in the repository and over every inline <script> block in every .html
page, so a typo in a page's loader cannot reach a client-visible surface.

Inline blocks are checked as modules-in-isolation, which catches the failure
that matters (a syntax error taking the whole page's script down) without
pretending to resolve cross-block references.

Usage
  python scripts/check_inline_js.py
  python scripts/check_inline_js.py --selftest
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
SRC_RE = re.compile(r"\bsrc\s*=", re.I)
TYPE_RE = re.compile(r"""\btype\s*=\s*['"]([^'"]+)['"]""", re.I)
JS_TYPES = {"", "text/javascript", "application/javascript", "module"}


def inline_blocks(html: str) -> list[tuple[int, str]]:
    """(1-indexed line where the block starts, block source) for real JS."""
    out = []
    for m in SCRIPT_RE.finditer(html):
        attrs, body = m.group(1), m.group(2)
        if SRC_RE.search(attrs):
            continue                      # external file, checked separately
        t = TYPE_RE.search(attrs)
        if t and t.group(1).strip().lower() not in JS_TYPES:
            continue                      # JSON-LD, templates, and the like
        if not body.strip():
            continue
        out.append((html[:m.start()].count("\n") + 1, body))
    return out


def node_check(source: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(source)
        tmp = fh.name
    try:
        r = subprocess.run(["node", "--check", tmp],
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0, (r.stderr or "").strip()
    finally:
        os.unlink(tmp)


def run(root: str = HERE) -> int:
    failures = []
    checked = 0
    for path in sorted(glob.glob(os.path.join(root, "*.js"))):
        ok, err = node_check(open(path, encoding="utf-8").read())
        checked += 1
        if not ok:
            failures.append((os.path.basename(path), 0, err))
    for path in sorted(glob.glob(os.path.join(root, "*.html"))):
        html = open(path, encoding="utf-8", errors="ignore").read()
        for line, body in inline_blocks(html):
            ok, err = node_check(body)
            checked += 1
            if not ok:
                failures.append((os.path.basename(path), line, err))
    print(f"checked {checked} script bodies")
    for name, line, err in failures:
        where = f"{name}:{line}" if line else name
        print(f"  FAIL  {where}\n        {err.splitlines()[0] if err else ''}")
    if failures:
        print(f"{len(failures)} syntax failure(s)")
        return 1
    print("front-end syntax: clean")
    return 0


def selftest() -> int:
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    html = ('<script src="./x.js"></script>\n'
            '<script>var a = 1;</script>\n'
            '<script type="application/ld+json">{"a": 1}</script>\n'
            '<script type="module">export const b = 2;</script>\n'
            '<script>   </script>\n')
    blocks = inline_blocks(html)
    bodies = [b for _, b in blocks]
    check("external script skipped", all("x.js" not in b for b in bodies))
    check("json-ld skipped", all('"a": 1' not in b for b in bodies))
    check("module kept", any("export const b" in b for b in bodies))
    check("empty block skipped", len(blocks) == 2)
    check("line numbers reported", blocks[0][0] == 2)

    ok, _ = node_check("var a = 1;")
    check("valid js passes", ok)
    bad, err = node_check("function ( {")
    check("invalid js fails", not bad and bool(err))

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    return selftest() if args.selftest else run()


if __name__ == "__main__":
    sys.exit(main())
