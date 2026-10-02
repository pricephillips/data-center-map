#!/usr/bin/env python3
"""
master_diff.py

Writes data/master_diff_summary.md: what changed in the source of truth since
the previous commit (spec 004, US5). Layer E, sole writer, regenerated on every
pipeline run.

Two daff comparisons, both keyless (research.md D8):
  - master_opposition.csv at --base vs the working tree: added, removed and
    modified row counts, plus up to 200 highlighter rows. The raw file has no
    unique key (exact duplicate rows exist), so daff aligns rows by content.
  - master_opposition_clean.csv at --base vs the working tree: every modified
    cell in Community Outcome, Status or outcome_defensible, reported with
    both values. outcome_defensible only exists in the clean feed, and the
    clean feed is deduplicated, so outcome changes are read from it.

Usage
  python master_diff.py [--base auto|REV]
  python master_diff.py --selftest

Base revision (default "auto"):
  - the working tree's master_opposition.csv differs from HEAD's (this run
    edited it, e.g. status_resolution.py --apply): compare to HEAD;
  - otherwise: compare to the parent of the newest commit that changed
    master_opposition.csv, so the summary keeps describing the latest real
    change instead of turning into "No changes" whenever the previous commit
    was an auto-build that left the source of truth alone;
  - history too shallow to find that commit: HEAD~1, as before.
CI checks out with enough depth for the second rule. Without any prior
revision the summary says so and the module exits 0.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
from datetime import datetime, timezone

import daff

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = "master_opposition.csv"
CLEAN = "master_opposition_clean.csv"
OUT_MD = os.path.join(HERE, "data", "master_diff_summary.md")
OUTCOME_COLS = ("Community Outcome", "Status", "outcome_defensible")
DETAIL_CAP = 200


def read_rows(text: str) -> list[list[str]]:
    return [r for r in csv.reader(io.StringIO(text, newline="")) if r]


def at_rev(rev: str, path: str) -> str | None:
    # Bytes, decoded without newline translation, so the text matches a file
    # read with newline="" (text=True would turn CRLF into LF).
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=HERE,
                       capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def resolve_base(base: str) -> str:
    """Pick the comparison revision for --base auto (see the docstring)."""
    if base != "auto":
        return base
    head_raw = at_rev("HEAD", RAW)
    try:
        with open(os.path.join(HERE, RAW), encoding="utf-8", newline="") as fh:
            work_raw = fh.read()
    except OSError:
        work_raw = None
    if head_raw is not None and work_raw is not None and head_raw != work_raw:
        return "HEAD"
    r = subprocess.run(["git", "log", "-1", "--format=%H", "HEAD", "--", RAW],
                       cwd=HERE, capture_output=True, text=True)
    last = r.stdout.strip()
    if last and at_rev(f"{last}~1", RAW) is not None:
        return f"{last}~1"
    return "HEAD~1"


def short_sha(rev: str) -> str:
    r = subprocess.run(["git", "rev-parse", "--short", rev], cwd=HERE,
                       capture_output=True, text=True)
    return r.stdout.strip() or rev


def hilite(a: list[list[str]], b: list[list[str]]) -> list[list[str]]:
    align = daff.Coopy.compareTables(daff.PythonTableView(a),
                                     daff.PythonTableView(b)).align()
    out: list[list] = []
    daff.TableDiff(align, daff.CompareFlags()).hilite(daff.PythonTableView(out))
    return [["" if c is None else str(c) for c in row] for row in out]


def counts(diff: list[list[str]]) -> dict[str, int]:
    n = {"added": 0, "removed": 0, "modified": 0}
    for row in diff[1:]:
        tag = row[0] if row else ""
        if tag == "+++":
            n["added"] += 1
        elif tag == "---":
            n["removed"] += 1
        elif "->" in tag:
            n["modified"] += 1
    return n


def outcome_changes(diff: list[list[str]]) -> list[dict[str, str]]:
    """Modified cells in OUTCOME_COLS, from a clean-feed hilite table."""
    if not diff:
        return []
    header = diff[0]
    idx = {h: i for i, h in enumerate(header)}
    changes = []
    for row in diff[1:]:
        tag = row[0] if row else ""
        if "->" not in tag:
            continue
        for col in OUTCOME_COLS:
            i = idx.get(col)
            if i is None or i >= len(row):
                continue
            cell = row[i]
            marker = "->" if "->" in cell else None
            if tag == "-->" and "-->" in cell:
                marker = "-->"
            if not marker:
                continue
            before, _, after = cell.partition(marker)
            label = _label(row, idx)
            changes.append({"row": label, "column": col,
                            "before": before, "after": after})
    return changes


def _label(row: list[str], idx: dict[str, int]) -> str:
    def get(c: str) -> str:
        i = idx.get(c)
        v = row[i] if i is not None and i < len(row) else ""
        return v.split("->")[-1] if "->" in v else v
    inc, st, dt = get("Incident"), get("State"), get("Date")
    return f"{inc} ({st}, {dt})" if (st or dt) else inc


def esc(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render(base: str, head: str, raw_diff: list[list[str]] | None,
           outcome: list[dict[str, str]], now: str) -> str:
    lines = ["# Source-of-truth diff", "",
             f"Compared `{base}` to `{head}`, generated {now} UTC.", ""]
    if raw_diff is None:
        lines += ["No prior revision to compare against.", ""]
        return "\n".join(lines)
    n = counts(raw_diff)
    if not any(n.values()) and not outcome:
        lines += ["No changes.", ""]
        return "\n".join(lines)
    lines += ["## Row counts (master_opposition.csv)", "",
              "| Added | Removed | Modified |", "|---|---|---|",
              f"| {n['added']} | {n['removed']} | {n['modified']} |", "",
              "## Outcome field changes (clean feed)", ""]
    if outcome:
        lines += ["| Row | Column | Before | After |", "|---|---|---|---|"]
        lines += [f"| {esc(c['row'])} | {c['column']} | {esc(c['before'])} | "
                  f"{esc(c['after'])} |" for c in outcome]
    else:
        lines += ["No changes to Community Outcome, Status, or outcome_defensible."]
    lines += ["", "## Detail", ""]
    body = [r for r in raw_diff[1:] if r and r[0] not in ("", "...")]
    if body:
        hdr = raw_diff[0]
        lines += ["| " + " | ".join(esc(h) for h in hdr) + " |",
                  "|" + "---|" * len(hdr)]
        for r in body[:DETAIL_CAP]:
            lines.append("| " + " | ".join(esc(c) for c in r) + " |")
        if len(body) > DETAIL_CAP:
            lines += ["", f"{len(body) - DETAIL_CAP} further rows not shown."]
    else:
        lines += ["No row-level changes."]
    lines.append("")
    return "\n".join(lines)


def build(base: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    base = resolve_base(base)
    old_raw = at_rev(base, RAW)
    if old_raw is None:
        return render(base, "working tree", None, [], now)
    with open(os.path.join(HERE, RAW), encoding="utf-8", newline="") as fh:
        new_raw = fh.read()
    raw_diff = hilite(read_rows(old_raw), read_rows(new_raw))
    outcome: list[dict[str, str]] = []
    old_clean = at_rev(base, CLEAN)
    clean_path = os.path.join(HERE, CLEAN)
    if old_clean is not None and os.path.exists(clean_path):
        with open(clean_path, encoding="utf-8", newline="") as fh:
            outcome = outcome_changes(hilite(read_rows(old_clean),
                                             read_rows(fh.read())))
    return render(short_sha(base), "working tree", raw_diff, outcome, now)


def selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, bool(ok)))

    hdr = ["Incident", "State", "Date", "Status", "Community Outcome",
           "outcome_defensible"]
    a = [hdr, ["Loudoun rezoning", "VA", "2026-01-02", "pending", "", "no"],
         ["Pima hearing", "AZ", "2026-02-03", "pending", "", "no"]]
    b = [hdr, ["Loudoun rezoning", "VA", "2026-01-02", "pending", "", "no"],
         ["Pima hearing", "AZ", "2026-02-03", "pending", "restricted", "no"],
         ["New County vote", "IA", "2026-03-04", "pending", "", "no"]]
    d = hilite(a, b)
    n = counts(d)
    check("an appended row counts as added", n["added"] == 1)
    check("a changed cell counts as modified", n["modified"] == 1)
    oc = outcome_changes(d)
    check("the outcome change is found", len(oc) == 1
          and oc[0]["column"] == "Community Outcome"
          and oc[0]["before"] == "" and oc[0]["after"] == "restricted")
    md = render("abc1234", "working tree", d, oc, "2026-01-01 00:00")
    check("the summary names the changed row", "Pima hearing (AZ, 2026-02-03)" in md)
    check("the summary shows both values",
          "| Community Outcome |  | restricted |" in md)
    check("no em-dash in the summary", "\u2014" not in md)
    check("no CR in the summary", "\r" not in md)
    same = render("abc1234", "working tree", hilite(a, a), [], "t")
    check("identical revisions say no changes", "No changes." in same)
    check("an explicit base is used as given", resolve_base("abc1234") == "abc1234")
    none = render("HEAD~1", "working tree", None, [], "t")
    check("a missing base says so", "No prior revision" in none)
    check("pipes are escaped", esc("a|b\nc") == "a\\|b c")

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--base", default="auto")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    text = build(args.base)
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"master_diff: wrote {os.path.relpath(OUT_MD, HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
