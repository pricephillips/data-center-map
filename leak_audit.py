#!/usr/bin/env python3
"""
leak_audit.py

Repo-wide scorekeeping-vocabulary audit with severity tiers.

Six modules currently each carry their own copy of

    LEAK_RE = re.compile(r"\\b(win|wins|loss|losses|lost)\\b", re.IGNORECASE)

and each applies it to its own generated prose. Nothing applies it to the
repo as a whole, so three classes of exposure have never been checked:

  1. Source modules and docs. A stale docstring in group_registry.py listed
     output columns using pre-four-tier vocabulary for the life of the
     module. Generated-output audits cannot see a docstring.

  2. Data columns as opposed to report prose. master_opposition_clean.csv
     carries the raw `Community Outcome` column, 482 rows valued "win" and
     292 "loss". Downstream consumers and the client-side JavaScript in the
     embedded dashboards read that column directly.

  3. Client-side identifiers. opposition-tracker.html and
     opposition-dashboard.html render correct labels (Blocked, Approved,
     Pending, Contested) over CSS classes and data keys named badge-win,
     oc-loss, s-win. The rendered text is clean; the DOM is not.

Tiers

  BLOCKING  Generated prose and generated data. These are artifacts the
            pipeline produces and can therefore fix. Exit code 1.

  ADVISORY  Source-of-truth input columns, client-side identifiers, and
            source modules. Reported, never blocking, because changing them
            is a migration with downstream coordination, not a lint fix.

  EXEMPT    Lines that define or document the rule itself.

Usage
  python leak_audit.py
  python leak_audit.py --tier blocking
  python leak_audit.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import os
import re
import sys

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

BLOCKING = "blocking"
ADVISORY = "advisory"
EXEMPT = "exempt"

# Artifacts the pipeline generates. A hit here is fixable in-pipeline.
GENERATED = [
    "*_report.md", "headline_metrics.md", "qc_report.md",
    "data_quality_report.md", "untagged_triage.md",
    "verification_status_report.md", "fips_resolution_report.md",
    "docs/*.md", "data/*_report.md", "data/*_intervals.md",
    "snapshots/*",
]

# Source-of-truth inputs. The raw vocabulary is the recorded value and is
# not ours to rewrite; the clean feed is where the mapping belongs.
SOURCE_DATA = [
    "master_opposition.csv", "master_opposition_raw.csv",
    "atlas.csv", "ai_centers.csv", "change_log.csv",
]

CLIENT_SIDE = ["*.html"]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".github/workflows/cache"}
SCAN_EXT = {".py", ".md", ".html", ".csv", ".json", ".yml", ".yaml"}

# Internal triage artifacts, ruled 2026-07-30: internal-only files may
# quote the raw scorekeeping vocabulary, because the quoted value is the
# thing the triage note is pointing the reviewer at. The fence is that
# these files never ship to a client. Pairs are (file glob, column).
INTERNAL_QUOTES = {
    ("data/bill_sync_worklist.csv", "recorded_outcome"),
    ("master_opposition_clean.csv", "outcome_conflict_reason"),
    ("review_conflicts.csv", "outcome_conflict_reason"),
    # Registered 2026-08-18. The blocking tier was RED on main and both
    # pipeline.yml and gate-check.yml run this module as a hard gate with no
    # continue-on-error, so every nightly build aborted at the audit step,
    # before project resolution, the county layer, the coverage audit, the
    # screener, and the commit. It is also why data/decision_date_worklist.csv
    # sits at 46 rows while a live landmark run produces 47: gate-check.yml
    # never reached its commit step either. None of the five hits was our
    # vocabulary:
    #
    #   motion_text  Open States machine-coded legislative journal text
    #                ("Conrad AM2794 lost" is the journal's own wording for a
    #                failed amendment). 392 values, transported not composed,
    #                and the exact wording is what makes a vote row auditable.
    #   party        CourtListener docket party names. "WIN J. WRIGHT" and
    #                "Win Maung" are people.
    #
    # Same class as Community Outcome: inherited source values in
    # internal-only artifacts, exempted as exact file-plus-column pairs
    # rather than by field name, so a composed column called motion_text
    # somewhere else would still block.
    ("data/bill_sync_votes.csv", "motion_text"),
    ("data/bill_sync_cache.json", "motion_text"),
    ("data/dispute_watch_cache.json", "party"),
    ("data/dispute_watch.csv", "party"),
    # Registered 2026-08-26 with manual_records.py. new_value is the verbatim
    # replacement value for a named master column (Summary, Community
    # Outcome, ...), i.e. transported record content whose destination
    # columns are all in INHERITED_FIELDS ("loss of farmland" in a Summary
    # correction was the first hit). detail quotes gate reasons and recorded
    # values for the audit trail. Both files are internal intake/ledger
    # artifacts and never ship to a client.
    ("data/manual_corrections.csv", "new_value"),
    ("data/manual_records_report.csv", "detail"),
}

# Columns and keys copied verbatim from the source of record. The pipeline
# transports these, it does not compose them, so a hit is inherited rather
# than introduced. Rewriting them is a source-data migration.
INHERITED_FIELDS = {
    "community outcome", "summary", "sources", "source url", "objective",
    "incident", "entity", "project name", "opposition groups", "notes",
    "what it means", "correct outcome", "message", "all_issues",
    "info", "title", "headline", "description", "text",
}

# A URL is not prose. "shreveport-wins-moratorium" in a citation is the
# publisher's headline slug, not our vocabulary.
URL_RE = re.compile(r"https?://\S+")

# A line that defines, documents, or tests the rule is not a violation.
EXEMPT_LINE = re.compile(
    r"LEAK_RE|leak_audit|leak audit|scorekeeping|no_scorekeeping|"
    r"four-tier|_LEAK|SCOREKEEPING", re.IGNORECASE)


def _match_any(relpath: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relpath, p) or fnmatch.fnmatch(
        os.path.basename(relpath), p) for p in patterns)


def classify(relpath: str, line: str, field: str = "") -> str:
    """Assigns a tier to one hit."""
    if EXEMPT_LINE.search(line):
        return EXEMPT
    # Strip URLs, then re-test: if nothing scorekeeping survives, the only
    # hit was inside a link.
    if not LEAK_RE.search(URL_RE.sub(" ", line)):
        return EXEMPT
    if field:
        fld = field.strip().strip("$.[]")
        for fpat, col in INTERNAL_QUOTES:
            if col == fld and (fnmatch.fnmatch(relpath, fpat)
                               or os.path.basename(relpath)
                               == os.path.basename(fpat)):
                return ADVISORY
        if fld.lower() in INHERITED_FIELDS:
            return ADVISORY
    if _match_any(relpath, SOURCE_DATA):
        return ADVISORY
    if _match_any(relpath, CLIENT_SIDE):
        return ADVISORY
    if _match_any(relpath, GENERATED):
        return BLOCKING
    if relpath.endswith(".csv") or relpath.endswith(".json"):
        # Generated data. The clean feed lands here.
        return BLOCKING
    if relpath.endswith(".py"):
        return ADVISORY
    return ADVISORY


def scan_csv_columns(path: str, relpath: str) -> list[dict]:
    """For CSVs, reports the offending column rather than every row, so one
    scorekeeping column does not produce hundreds of identical hits."""
    hits = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rdr = csv.DictReader(fh)
            if not rdr.fieldnames:
                return []
            counts: dict[str, int] = {}
            example: dict[str, str] = {}
            for row in rdr:
                for col, val in row.items():
                    if val and LEAK_RE.search(str(val)):
                        counts[col] = counts.get(col, 0) + 1
                        example.setdefault(col, str(val))
            for col, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                hits.append({
                    "path": relpath, "line": 0,
                    "tier": classify(relpath, example[col], field=col),
                    "text": f"column {col!r}: {n} row(s), e.g. {example[col]!r}",
                })
    except (OSError, csv.Error, UnicodeDecodeError):
        pass
    return hits


def scan_json(path: str, relpath: str) -> list[dict]:
    """Reports the offending JSON key rather than every occurrence, so a
    record dump does not produce one hit per record."""
    counts: dict[str, int] = {}
    example: dict[str, str] = {}

    def walk(node, keypath="$"):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{keypath}.{k}")
        elif isinstance(node, list):
            for v in node:
                walk(v, f"{keypath}[]")
        elif isinstance(node, str) and LEAK_RE.search(node):
            counts[keypath] = counts.get(keypath, 0) + 1
            example.setdefault(keypath, node[:80])

    try:
        with open(path, encoding="utf-8") as fh:
            walk(json.load(fh))
    except (OSError, ValueError, RecursionError):
        return scan_text(path, relpath)

    return [{"path": relpath, "line": 0,
             "tier": classify(relpath, example[k], field=k.rsplit(".", 1)[-1]),
             "text": f"key {k}: {n} value(s), e.g. {example[k]!r}"}
            for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]


def scan_text(path: str, relpath: str) -> list[dict]:
    hits = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if LEAK_RE.search(line):
                    tier = classify(relpath, line)
                    if tier == EXEMPT:
                        continue
                    hits.append({"path": relpath, "line": i, "tier": tier,
                                 "text": line.strip()[:140]})
    except OSError:
        pass
    return hits


def scan_repo(root: str) -> list[dict]:
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SCAN_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if ext == ".csv":
                hits.extend(scan_csv_columns(full, rel))
            elif ext == ".json":
                hits.extend(scan_json(full, rel))
            else:
                hits.extend(scan_text(full, rel))
    return hits


def report(hits: list[dict], tier_filter: str | None) -> int:
    order = {BLOCKING: 0, ADVISORY: 1}
    sel = [h for h in hits if not tier_filter or h["tier"] == tier_filter]
    sel.sort(key=lambda h: (order.get(h["tier"], 2), h["path"], h["line"]))

    n_block = sum(1 for h in hits if h["tier"] == BLOCKING)
    n_adv = sum(1 for h in hits if h["tier"] == ADVISORY)

    current = None
    for h in sel:
        if h["tier"] != current:
            current = h["tier"]
            print(f"\n[{current.upper()}]")
        loc = f"{h['path']}:{h['line']}" if h["line"] else h["path"]
        print(f"  {loc}\n      {h['text']}")

    print(f"\nblocking: {n_block}   advisory: {n_adv}")
    if n_block:
        print("LEAK AUDIT FAILED: scorekeeping vocabulary in generated "
              "artifacts.")
        return 1
    print("leak audit: clean (generated artifacts)")
    return 0


# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def eq(label, got, want):
        checks.append((f"{label}: {got!r}", got == want))

    eq("generated report is blocking",
       classify("data/outcome_model_report.md", "a clear win for opponents"),
       BLOCKING)
    eq("docs are blocking",
       classify("docs/fact_pack.md", "the loss column"), BLOCKING)
    eq("generated csv is blocking",
       classify("master_opposition_clean.csv", "a clear win",
                field="model_note"), BLOCKING)
    eq("source csv is advisory",
       classify("master_opposition.csv", "a clear win",
                field="model_note"), ADVISORY)
    eq("client-side is advisory",
       classify("opposition-tracker.html", ".badge-win { }"), ADVISORY)
    eq("source module is advisory",
       classify("group_registry.py", "decided, wins, win_rate"), ADVISORY)
    eq("inherited column is advisory",
       classify("master_opposition_clean.csv", "win",
                field="Community Outcome"), ADVISORY)
    eq("composed column stays blocking",
       classify("data/x.csv", "a clear win", field="model_note"), BLOCKING)
    eq("internal triage quote is advisory",
       classify("data/bill_sync_worklist.csv", "loss",
                field="recorded_outcome"), ADVISORY)
    eq("conflict-reason quote is advisory",
       classify("review_conflicts.csv", "recorded outcome 'win' without",
                field="outcome_conflict_reason"), ADVISORY)
    # --- 2026-08-18 registrations (the five hits that had main's gate red) ---
    eq("open states motion text is advisory",
       classify("data/bill_sync_votes.csv", "Conrad AM2794 lost",
                field="motion_text"), ADVISORY)
    eq("open states motion text in the cache is advisory",
       classify("data/bill_sync_cache.json", "Conrad AM2794 lost",
                field="motion_text"), ADVISORY)
    eq("docket party name is advisory",
       classify("data/dispute_watch_cache.json", "Win Maung",
                field="party"), ADVISORY)
    eq("docket party name in the csv is advisory",
       classify("data/dispute_watch.csv", "WIN J. WRIGHT",
                field="party"), ADVISORY)
    eq("a composed motion_text elsewhere still blocks",
       classify("data/county_policy_report.csv", "a clear win",
                field="motion_text"), BLOCKING)
    eq("a composed party column elsewhere still blocks",
       classify("data/outcome_summary.csv", "a clear win",
                field="party"), BLOCKING)
    eq("prose in a bill sync report still blocks",
       classify("data/bill_sync_report.md", "a win for opponents"), BLOCKING)
    # --- 2026-08-26 registrations (manual corrections/uploads intake) ---
    eq("correction ledger new_value quote is advisory",
       classify("data/manual_corrections.csv", "loss of farmland",
                field="new_value"), ADVISORY)
    eq("manual records report detail quote is advisory",
       classify("data/manual_records_report.csv", "recorded 'win' without",
                field="detail"), ADVISORY)
    eq("a composed new_value elsewhere still blocks",
       classify("data/other_intake.csv", "a clear win",
                field="new_value"), BLOCKING)

    eq("same column elsewhere stays blocking",
       classify("data/some_report.csv", "a win",
                field="recorded_outcome"), BLOCKING)
    eq("url-only hit is exempt",
       classify("data/x.csv", "https://x.org/shreveport-wins-moratorium"),
       EXEMPT)
    eq("prose beside a url still counts",
       classify("data/r_report.md", "a win, see https://x.org/a"), BLOCKING)
    eq("rule definition is exempt",
       classify("site_screener.py",
                'LEAK_RE = re.compile(r"\\b(win|loss)\\b")'), EXEMPT)
    eq("rule prose is exempt",
       classify("docs/notes.md", "run the leak audit on all outputs"), EXEMPT)
    eq("scorekeeping mention is exempt",
       classify("docs/notes.md", "no scorekeeping vocabulary here"), EXEMPT)

    # regex behaviour: whole words only, no substring firing
    checks.append(("matches bare win", bool(LEAK_RE.search("a win"))))
    checks.append(("matches Lost cased", bool(LEAK_RE.search("Lost ground"))))
    checks.append(("ignores window", not LEAK_RE.search("window")))
    checks.append(("ignores winner", not LEAK_RE.search("winner")))
    checks.append(("ignores glossary", not LEAK_RE.search("glossary")))
    checks.append(("ignores lossless", not LEAK_RE.search("lossless")))

    # tier ordering: a blocking hit fails, advisory alone does not
    checks.append(("advisory alone passes", report(
        [{"path": "x.py", "line": 1, "tier": ADVISORY, "text": "t"}],
        None) == 0))
    checks.append(("blocking fails", report(
        [{"path": "d/r_report.md", "line": 1, "tier": BLOCKING, "text": "t"}],
        None) == 1))

    failed = [n for n, ok in checks if not ok]
    print()
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--tier", choices=[BLOCKING, ADVISORY], default=None)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return report(scan_repo(args.root), args.tier)


if __name__ == "__main__":
    sys.exit(main())
