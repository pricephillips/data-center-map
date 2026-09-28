#!/usr/bin/env python3
"""
precommit_gates.py

The local Constitution Check gates that no existing script covers, gathered
in one module so they share one --selftest (Principle IX). Called by the
system hooks in .pre-commit-config.yaml (spec 004, US1). Each subcommand takes
the staged file list from pre-commit and exits 1 on any finding.

  crlf [--fix] FILE...   A CSV may not introduce CR bytes. A file whose
                         committed (HEAD) version already has CR is left
                         alone: 61 tracked CSVs are CRLF because Python's
                         csv.writer defaults to it, pipeline modules rewrite
                         them every run, and master_opposition.csv is CRLF by
                         its writers' choice. Normalizing those is a separate
                         decision (research.md D3). New CSVs and LF CSVs stay
                         LF. --fix rewrites the offenders and still exits 1.
  emdash FILE...         U+2014 in deliverables, reported as path:line.
  nodecheck FILE...      node --check on .js files and on inline <script>
                         blocks of .html files (scripts/check_inline_js.py).
  selftest FILE...       --selftest on each touched module that has one.
  vale-sync              styles/Hawthorn/Scorekeeping.yml tokens must equal
                         the alternation in leak_audit.LEAK_RE.

Usage
  python scripts/precommit_gates.py <subcommand> [args]
  python scripts/precommit_gates.py --selftest
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

EMDASH = "\u2014"
MASTER = "master_opposition.csv"
SCOREKEEPING_YML = os.path.join(ROOT, "styles", "Hawthorn", "Scorekeeping.yml")

# Same two patterns tests/test_selftests.py uses to discover selftests.
SELFTEST_RE = re.compile(
    r"""add_argument\(\s*["']--selftest|["']--selftest["']\s*(?:in|==)""")


def _rel(path: str) -> str:
    return os.path.relpath(os.path.abspath(path), ROOT).replace(os.sep, "/")


def head_has_cr(rel: str) -> bool:
    """True if the committed version of rel contains CR. A file absent from
    HEAD (new) has no baseline, so it must be LF."""
    r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT,
                       capture_output=True)
    return r.returncode == 0 and b"\r" in r.stdout


def crlf(files: list[str], fix: bool, baseline=head_has_cr) -> int:
    bad = 0
    for f in files:
        rel = _rel(f)
        if not rel.endswith(".csv") or rel == MASTER:
            continue
        with open(f, "rb") as fh:
            data = fh.read()
        if b"\r" not in data or baseline(rel):
            continue
        bad += 1
        if fix:
            with open(f, "wb") as fh:
                fh.write(data.replace(b"\r\n", b"\n"))
            print(f"fixed CRLF -> LF: {rel}")
        else:
            print(f"{rel}: CRLF line endings")
    return 1 if bad else 0


def emdash(files: list[str]) -> int:
    bad = 0
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if EMDASH in line:
                    print(f"{_rel(f)}:{i}: em-dash")
                    bad += 1
    return 1 if bad else 0


def nodecheck(files: list[str]) -> int:
    import check_inline_js as cij
    bad = 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            src = fh.read()
        if f.endswith(".html"):
            blocks = cij.inline_blocks(src)
        elif f.endswith(".js"):
            blocks = [(1, src)]
        else:
            continue
        for line, block in blocks:
            ok, err = cij.node_check(block)
            if not ok:
                print(f"{_rel(f)}:{line}: {err.strip().splitlines()[-1] if err.strip() else 'syntax error'}")
                bad += 1
    return 1 if bad else 0


def has_selftest(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as fh:
            return bool(SELFTEST_RE.search(fh.read()))
    except OSError:
        return False


def selftest_files(files: list[str]) -> int:
    bad = 0
    for f in files:
        if not f.endswith(".py"):
            continue
        if not has_selftest(f):
            print(f"skip (no selftest): {_rel(f)}")
            continue
        r = subprocess.run([sys.executable, f, "--selftest"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            bad += 1
            tail = (r.stdout + r.stderr).strip().splitlines()[-20:]
            print(f"FAIL selftest: {_rel(f)}\n  " + "\n  ".join(tail))
    return 1 if bad else 0


def leak_tokens() -> set[str]:
    import leak_audit
    m = re.search(r"\(([^()]*)\)", leak_audit.LEAK_RE.pattern)
    return set(m.group(1).split("|")) if m else set()


def yml_tokens(path: str) -> set[str]:
    toks, in_tokens = set(), False
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if re.match(r"^tokens:\s*$", line):
                in_tokens = True
                continue
            if in_tokens:
                m = re.match(r"^\s+-\s*['\"]?([^'\"\n]+?)['\"]?\s*$", line)
                if m:
                    toks.add(m.group(1))
                elif line.strip() and not line.startswith(" "):
                    in_tokens = False
    return toks


def vale_sync(path: str = SCOREKEEPING_YML) -> int:
    want, have = leak_tokens(), yml_tokens(path)
    if want == have and want:
        print(f"vale-sync: {len(want)} tokens match leak_audit.LEAK_RE")
        return 0
    print(f"vale-sync: MISMATCH. missing from yml: {sorted(want - have)}; "
          f"extra in yml: {sorted(have - want)}")
    return 1


def selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, bool(ok)))

    import contextlib
    import io
    td = tempfile.mkdtemp()

    def w(name: str, data: bytes) -> str:
        p = os.path.join(td, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    quiet = contextlib.redirect_stdout(io.StringIO())
    crlf_csv = w("hand.csv", b"a,b\r\n1,2\r\n")
    lf_csv = w("clean.csv", b"a,b\n1,2\n")
    gen_csv = w("gen.csv", b"a,b\r\n")
    lf_base = lambda rel: False  # noqa: E731
    cr_base = lambda rel: True  # noqa: E731
    with quiet:
        check("a CSV introducing CRLF is reported", crlf([crlf_csv], False, lf_base) == 1)
        check("LF csv is clean", crlf([lf_csv], False, lf_base) == 0)
        check("a CSV already CRLF in HEAD is left alone", crlf([gen_csv], False, cr_base) == 0)
        check("--fix rewrites and still exits 1", crlf([crlf_csv], True, lf_base) == 1)
    with open(crlf_csv, "rb") as fh:
        check("--fix leaves LF only", fh.read() == b"a,b\n1,2\n")
    master_like = os.path.join(ROOT, MASTER)
    with quiet:
        check("master_opposition.csv is always excluded",
              crlf([master_like], False, lf_base) == 0)

    md = w("deliv.md", ("ok\nhas " + EMDASH + " here\n").encode())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = emdash([md])
    check("em-dash exits 1 with its line number", rc == 1 and ":2: em-dash" in buf.getvalue())
    with quiet:
        check("clean markdown passes emdash", emdash([w("ok.md", b"plain, text\n")]) == 0)

    nost = w("plain.py", b"print('no selftest here')\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = selftest_files([nost])
    check("a module without a selftest is skipped", rc == 0 and "skip" in buf.getvalue())
    good = w("good.py", b"import sys\nif '--selftest' in sys.argv: sys.exit(0)\n")
    badp = w("bad.py", b"import sys\nif '--selftest' in sys.argv: sys.exit(1)\n")
    with quiet:
        check("a passing selftest passes", selftest_files([good]) == 0)
        check("a failing selftest fails", selftest_files([badp]) == 1)

    toks = leak_tokens()
    check("leak_audit tokens parse", {"win", "lost"} <= toks)
    match = w("match.yml", ("extends: existence\ntokens:\n"
                            + "".join(f"  - {t}\n" for t in sorted(toks))).encode())
    miss = w("miss.yml", b"extends: existence\ntokens:\n  - win\n")
    with quiet:
        check("vale-sync passes on matching tokens", vale_sync(match) == 0)
        check("vale-sync fails on a mismatch", vale_sync(miss) == 1)
        if os.path.exists(SCOREKEEPING_YML):
            check("repo Scorekeeping.yml is in sync", vale_sync() == 0)

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_crlf = sub.add_parser("crlf")
    p_crlf.add_argument("--fix", action="store_true")
    p_crlf.add_argument("files", nargs="*")
    for name in ("emdash", "nodecheck", "selftest"):
        sub.add_parser(name).add_argument("files", nargs="*")
    sub.add_parser("vale-sync")
    args = ap.parse_args(argv)
    if args.cmd == "crlf":
        return crlf(args.files, args.fix)
    if args.cmd == "emdash":
        return emdash(args.files)
    if args.cmd == "nodecheck":
        return nodecheck(args.files)
    if args.cmd == "selftest":
        return selftest_files(args.files)
    return vale_sync()


if __name__ == "__main__":
    sys.exit(main())
