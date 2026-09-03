#!/usr/bin/env python3
"""
fix_project_id_collision.py — one-shot repair + permanent guard for duplicate
project ids in data/proposals.csv.

WHAT WENT WRONG
---------------
project_id is "prj_" + the `id` column of data/proposals.csv, and every Layer B
artifact (project_links, project_lifecycles, baseline_universe, matched_controls,
outcome_model_features, county rollups) joins on it. Two writers feed that file:

  * the CMS export, which owns the contiguous id space from 1 upward, and
  * manual additions curated in-repo.

In July 2026 a manual-addition batch restarted its numbering at 321, inside the
CMS id space. Ids 321-326 were minted twice. Nothing errored. The joins simply
fanned out, and six Pennsylvania projects inherited another project's
coordinates, developer and opposition events -- e.g. "Nebius Butler Township
Data Center" (Schuylkill County, PA) was published at 32.507, -91.647, which is
Richland Parish, Louisiana, operated by "Meta", carrying Meta Hyperion's ten
opposition events. Two different projects also appeared under prj_322 in the
outcome model's training features.

WHAT THIS SCRIPT DOES
---------------------
  1. Renumbers the 12 manual-addition rows out of the CMS id space
     (321-332 -> 1001-1012), so the two writers can never collide again. This
     also heads off the guaranteed recurrence: the CMS mints 327 next, which
     would have collided with "Meta Project Everest".
  2. Rewrites the hand-curated references to those ids
     (project_links_manual.csv, project_decision_dates.csv,
     project_duplicates.csv). Generated artifacts are NOT touched -- the
     pipeline rebuilds them.
  3. Installs assert_unique_project_ids() in project_resolution.py so a future
     collision stops the pipeline instead of publishing a merged project.
  4. Rescopes the outcome-gate sentence on project-lifecycles.html, which cited
     a bare worklist count no reader could reconcile against the panel's own
     denominators.
  5. Records the id convention in ARCHITECTURE.md.

Every step is idempotent and independently skippable: re-running reports
"already applied" and changes nothing. Step 2 runs only when step 1 actually
had work to do, so a later, legitimate curated reference to prj_321-326 (which
now denote the Pennsylvania projects) can never be rewritten by a second run.

USAGE (from repo root)
----------------------
  python3 scripts/fix_project_id_collision.py --check     # report only, exit 1 if collisions
  python3 scripts/fix_project_id_collision.py --dry-run   # show what would change
  python3 scripts/fix_project_id_collision.py             # apply
  python3 scripts/fix_project_id_collision.py --regen     # apply, then rebuild derived artifacts

Stdlib only. No git operations are performed.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# The migration, stated explicitly rather than inferred.
#
# Keyed by project NAME, not by row position, so the script is safe to run
# against a proposals.csv that the CMS has since re-exported or reordered. A
# row is renumbered only if its current id is still in the old block, which is
# what makes the step idempotent.
# ---------------------------------------------------------------------------

OLD_BLOCK = [str(i) for i in range(321, 333)]        # ids the manual batch took
NEW_BASE = 1001                                       # manual additions live >= 1000
MANUAL_ID_FLOOR = 1000

RENUMBER = {
    "Harper Road Technology Park":                          "1001",
    "Province Group Perry Village Data Center Campus":      "1002",
    "Meta Hyperion":                                        "1003",
    "Prado AI Industrial Campus":                           "1004",
    "New Carlisle Chicago Trail Data Center (Third Site)":  "1005",
    "Project Maize / Google Michigan City Data Center":     "1006",
    "Meta Project Everest":                                 "1007",
    "Terra Nexus Custer Avenue Data Center":                "1008",
    "PRSM Group Cumberland County Data Center":             "1009",
    "Project Sail Data Center":                             "1010",
    "Howell Township Data Center (Project Splitrock)":      "1011",
    "Natelli Vance County Data Center":                     "1012",
}

# prj_321 .. prj_332 -> prj_1001 .. prj_1012
REF_MAP = {f"prj_{o}": f"prj_{NEW_BASE + i}" for i, o in enumerate(OLD_BLOCK)}
REF_RE = re.compile(r"prj_(3(?:2[1-9]|3[0-2]))\b")

PROPOSALS = "data/proposals.csv"
# Hand-curated files that reference project ids, either in an id column or in
# free-text notes. These are inputs, not pipeline outputs, so nothing regenerates
# them and the references have to be migrated here.
CURATED = [
    # The real home of the manual-addition block. data/proposals.csv is
    # regenerated nightly from this file, so renumbering only the generated
    # copy is undone by the next scrape -- which is exactly what happened on
    # 2026-09-03.
    "data/proposals_added.csv",
    "data/project_links_manual.csv",
    "data/project_decision_dates.csv",
    "data/project_duplicates.csv",
    "data/negative_audit_codings.csv",   # append-only, hand-filled; note text only
    "PHASE_STATUS.md",                   # standing document
]

# Deliberately NOT migrated: dated session records under data/ (the 2026-07-23
# landmark, dedup, negative-audit and announced-date write-ups, type_b_link_review.md,
# date_recovery_negative_spans.md). They describe the state of the repo on the day
# they were written, and rewriting them would falsify a record. Translate old ids
# through the table in ARCHITECTURE.md when reading them.

# Derived artifacts the pipeline rebuilds. Listed for the closing message only;
# this script never edits them.
REGEN = [
    ["python3", "project_resolution.py"],
    ["python3", "control_group.py"],
    ["python3", "control_comparison.py"],
    ["python3", "triage_accelerator.py"],
    ["python3", "county_aggregator.py"],
    ["python3", "landmark_model.py"],
    ["python3", "outcome_model.py"],
    ["python3", "cost_translation.py"],
    ["python3", "baseline_dated.py"],
    ["python3", "operations_summary.py"],
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

class Reporter:
    def __init__(self, dry: bool):
        self.dry = dry
        self.changed = 0
        self.skipped = 0

    def did(self, msg: str) -> None:
        print(("  WOULD  " if self.dry else "  DONE   ") + msg)
        self.changed += 1

    def skip(self, msg: str) -> None:
        print("  SKIP   " + msg)
        self.skipped += 1

    def warn(self, msg: str) -> None:
        print("  WARN   " + msg)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path: str, s: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(s)


def read_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def find_root() -> str:
    """Repo root: cwd if it looks right, else the script's parent directory."""
    for cand in (os.getcwd(), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        if os.path.isfile(os.path.join(cand, PROPOSALS)) and \
           os.path.isfile(os.path.join(cand, "project_resolution.py")):
            return cand
    sys.exit("fix_project_id_collision: run this from the data-center-map repo root "
             f"(expected {PROPOSALS} and project_resolution.py).")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def duplicate_ids(rows: list[dict]) -> list[tuple[str, list[str]]]:
    by_id: dict[str, list[str]] = {}
    for r in rows:
        by_id.setdefault((r.get("id") or "").strip(), []).append((r.get("name") or "").strip())
    return sorted(((pid, names) for pid, names in by_id.items() if len(names) > 1),
                  key=lambda t: (len(t[0]), t[0]))


def report_collisions(rows: list[dict]) -> int:
    dupes = duplicate_ids(rows)
    blank = sum(1 for r in rows if not (r.get("id") or "").strip())
    print(f"{PROPOSALS}: {len(rows)} rows, "
          f"{len({(r.get('id') or '').strip() for r in rows})} distinct ids")
    if blank:
        print(f"  BLANK  {blank} row(s) carry no id")
    for pid, names in dupes:
        print(f"  DUP    prj_{pid}: " + "  <->  ".join(names))
    if not dupes and not blank:
        print("  OK     no duplicate or missing ids")
    return len(dupes) + (1 if blank else 0)


# ---------------------------------------------------------------------------
# Step 1 — renumber the manual-addition block in proposals.csv
#
# Edits the id field of matched lines in place rather than round-tripping the
# file through csv.writer, so the diff is 12 lines and not 338 requoted ones.
# ---------------------------------------------------------------------------

def step_proposals(root: str, rep: Reporter) -> int:
    path = os.path.join(root, PROPOSALS)
    rows = read_rows(path)
    pending = [r for r in rows
               if (r.get("name") or "").strip() in RENUMBER
               and (r.get("id") or "").strip() in OLD_BLOCK]
    if not pending:
        rep.skip(f"{PROPOSALS}: manual-addition block already at "
                 f"{NEW_BASE}+ (nothing to renumber)")
        return 0

    want = {(r.get("name") or "").strip(): RENUMBER[(r.get("name") or "").strip()]
            for r in pending}
    taken = {(r.get("id") or "").strip() for r in rows} - {
        (r.get("id") or "").strip() for r in pending}
    clash = sorted(set(want.values()) & taken)
    if clash:
        sys.exit(f"fix_project_id_collision: target id(s) {clash} are already in use. "
                 "Resolve by hand; refusing to guess.")

    raw = read_text(path)
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)
    n = 0
    for i, line in enumerate(lines):
        m = re.match(r'^(3(?:2[1-9]|3[0-2])),("?)(.+?)\2,', line)
        if not m:
            continue
        name = m.group(3)
        new_id = want.get(name)
        if new_id and new_id != m.group(1):
            lines[i] = new_id + line[len(m.group(1)):]
            n += 1
            print(f"           prj_{m.group(1)} -> prj_{new_id}   {name}")
    if n != len(pending):
        sys.exit(f"fix_project_id_collision: matched {n} line(s) but expected "
                 f"{len(pending)}. proposals.csv formatting is not what this "
                 "script assumes; stopping before writing.")

    if not rep.dry:
        write_text(path, newline.join(lines))
        after = read_rows(path)
        dupes = duplicate_ids(after)
        if dupes:
            sys.exit("fix_project_id_collision: ids still duplicated after rewrite: "
                     + ", ".join(f"prj_{p}" for p, _ in dupes))
    rep.did(f"{PROPOSALS}: renumbered {n} manual-addition row(s) to {NEW_BASE}+")
    return n


# ---------------------------------------------------------------------------
# Step 2 — rewrite hand-curated references
#
# Runs ONLY when step 1 had work to do. After the migration, prj_321-326 denote
# the Pennsylvania projects, and a curated file may legitimately reference them;
# a second, unconditional pass would silently corrupt those rows.
# ---------------------------------------------------------------------------

def step_curated(root: str, rep: Reporter, migrated: bool) -> int:
    if not migrated:
        rep.skip("curated id references: migration already applied, not re-running "
                 "(prj_321-326 now denote the PA projects)")
        return 0
    total = 0
    for rel in CURATED:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            rep.warn(f"{rel}: not found, skipped")
            continue
        raw = read_text(path)
        hits = REF_RE.findall(raw)
        if not hits:
            rep.skip(f"{rel}: no references in the old id block")
            continue
        new = REF_RE.sub(lambda m: REF_MAP["prj_" + m.group(1)], raw)
        if not rep.dry:
            write_text(path, new)
        rep.did(f"{rel}: rewrote {len(hits)} reference(s)")
        total += len(hits)
    return total


# ---------------------------------------------------------------------------
# Step 3 — the permanent guard in project_resolution.py
# ---------------------------------------------------------------------------

GUARD_ANCHOR = """def prep_projects(rows: list[dict]) -> list[dict]:
    projects = []
    for r in rows:"""

GUARD_BLOCK = '''# Project ids are minted as "prj_" + proposals.csv `id`, so that column is a
# primary key: every downstream artifact (links, lifecycles, baseline universe,
# matched controls, model features) joins on it. proposals.csv is fed by two
# writers — the CMS export, which owns the contiguous low id space, and manual
# additions curated in-repo. A manual addition that reuses a CMS id does not
# error anywhere: the joins simply fan out, and one project silently inherits
# another's coordinates, developer, and opposition events. To keep the writers
# from colliding, manual additions are numbered from MANUAL_ID_FLOOR upward.
MANUAL_ID_FLOOR = %d


def assert_unique_project_ids(rows: list[dict]) -> None:
    """Fail loudly on duplicate proposals ids.

    A collision is unrecoverable at this layer: with two rows sharing an id
    there is no way to tell which project any downstream row refers to. Better
    to stop the pipeline than to publish a project pinned to another project's
    coordinates with another project's opposition attached.
    """
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for r in rows:
        pid = (r.get("id") or "").strip()
        name = (r.get("name") or "").strip()
        if not pid:
            dupes.append(f"  (blank id): {name}")
        elif pid in seen:
            dupes.append(f"  prj_{pid}: {seen[pid]}  <->  {name}")
        else:
            seen[pid] = name
    if dupes:
        raise SystemExit(
            "project_resolution: duplicate or missing ids in proposals.csv.\\n"
            + "\\n".join(dupes)
            + f"\\n\\nproject_id is the join key for every downstream artifact; a "
              f"collision silently merges two projects. Renumber the manual "
              f"addition to an unused id >= {MANUAL_ID_FLOOR} (the CMS export "
              f"owns the low id space) and re-run."
        )


def prep_projects(rows: list[dict]) -> list[dict]:
    assert_unique_project_ids(rows)
    projects = []
    for r in rows:''' % MANUAL_ID_FLOOR


def step_guard(root: str, rep: Reporter) -> int:
    path = os.path.join(root, "project_resolution.py")
    s = read_text(path)
    if "assert_unique_project_ids" in s:
        rep.skip("project_resolution.py: guard already installed")
        return 0
    if s.count(GUARD_ANCHOR) != 1:
        rep.warn("project_resolution.py: prep_projects() does not match the expected "
                 "shape; guard NOT installed. Add assert_unique_project_ids() by hand.")
        return 0
    if not rep.dry:
        write_text(path, s.replace(GUARD_ANCHOR, GUARD_BLOCK))
    rep.did("project_resolution.py: installed assert_unique_project_ids() guard")
    return 1


# ---------------------------------------------------------------------------
# Step 4 — rescope the outcome-gate sentence on the lifecycles page
# ---------------------------------------------------------------------------

GATE_OLD = """    (ops.worklists && ops.worklists.decision_dates
      ? 'Recovering the ' + ops.worklists.decision_dates.open +
        ' outstanding decision dates is what moves these counts; nothing else does.'
      : '');"""

GATE_NEW = """    (ops.worklists && ops.worklists.decision_dates
      ? 'The ' + ops.worklists.decision_dates.open + ' projects on the decision-date ' +
        'worklist \\u2014 decided and opposed and anchored, but undated \\u2014 are the only ' +
        'ones that can enter a frame once dated; nothing else moves these counts. ' +
        'That worklist is almost entirely advanced outcomes, which is the constraint that ' +
        'matters: a denial is reported on a dated public record far more reliably than an ' +
        'approval, so the dated subset runs heavily blocked against a much lower blocked ' +
        'share among all decided projects. Until the advanced arm is dated, the ' +
        'not-blocked floor cannot be met, and a frame drawn from what is dated today would ' +
        'not be representative of the decided set.'
      : '');"""


def step_gate_copy(root: str, rep: Reporter) -> int:
    path = os.path.join(root, "project-lifecycles.html")
    if not os.path.isfile(path):
        rep.warn("project-lifecycles.html: not found, skipped")
        return 0
    s = read_text(path)
    if "projects on the decision-date" in s:
        rep.skip("project-lifecycles.html: gate copy already rescoped")
        return 0
    if s.count(GATE_OLD) != 1:
        rep.warn("project-lifecycles.html: gate sentence does not match the expected "
                 "text; copy NOT changed.")
        return 0
    if not rep.dry:
        write_text(path, s.replace(GATE_OLD, GATE_NEW))
    rep.did("project-lifecycles.html: rescoped the outcome-gate sentence to name its pool")
    return 1


# ---------------------------------------------------------------------------
# Step 5 — document the convention
# ---------------------------------------------------------------------------

DOC_ANCHOR = """A permit that reaches built or operating status is the intended graduation path
from Layer B into Layer A."""

DOC_BLOCK = """`project_id` is `prj_` + the `id` column of `data/proposals.csv`, and every
Layer B artifact joins on it, so that column is a primary key. Two writers feed
the file: the CMS export, which owns the contiguous id space from 1 upward, and
manual additions curated in-repo. **Manual additions are numbered from 1000
upward** so the two writers cannot collide. They did collide once (2026-07,
ids 321-326 minted twice), and nothing errored: the joins fanned out and six
projects inherited another project's coordinates, developer and opposition
events. `project_resolution.assert_unique_project_ids` now stops the pipeline on
any duplicate rather than publishing a merged project.

The manual-addition block was renumbered on 2026-09-02. Dated session records
under `data/` still carry the pre-migration ids and are left as written; translate
them through this table.

| was | now | project |
|---|---|---|
| prj_321 | prj_1001 | Harper Road Technology Park (MO) |
| prj_322 | prj_1002 | Province Group Perry Village (OH) |
| prj_323 | prj_1003 | Meta Hyperion (LA) |
| prj_324 | prj_1004 | Prado AI Industrial Campus (MS) |
| prj_325 | prj_1005 | New Carlisle Chicago Trail, Third Site (IN) |
| prj_326 | prj_1006 | Project Maize / Google Michigan City (IN) |
| prj_327 | prj_1007 | Meta Project Everest (LA) |
| prj_328 | prj_1008 | Terra Nexus Custer Avenue (NC) |
| prj_329 | prj_1009 | PRSM Group Cumberland County (NC) |
| prj_330 | prj_1010 | Project Sail (GA) |
| prj_331 | prj_1011 | Howell Township / Project Splitrock (MI) |
| prj_332 | prj_1012 | Natelli Vance County (NC) |

After the migration `prj_321`-`prj_326` denote six Pennsylvania projects, which is
why the migration must never be run twice.

A permit that reaches built or operating status is the intended graduation path
from Layer B into Layer A."""


def step_docs(root: str, rep: Reporter) -> int:
    path = os.path.join(root, "ARCHITECTURE.md")
    if not os.path.isfile(path):
        rep.warn("ARCHITECTURE.md: not found, skipped")
        return 0
    s = read_text(path)
    if "Manual additions are numbered from 1000" in s:
        rep.skip("ARCHITECTURE.md: id convention already documented")
        return 0
    if s.count(DOC_ANCHOR) != 1:
        rep.warn("ARCHITECTURE.md: Layer B section does not match the expected text; "
                 "note NOT added.")
        return 0
    if not rep.dry:
        write_text(path, s.replace(DOC_ANCHOR, DOC_BLOCK))
    rep.did("ARCHITECTURE.md: recorded the manual-addition id convention")
    return 1


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(root: str) -> int:
    print("\nVerification")
    problems = 0

    rows = read_rows(os.path.join(root, PROPOSALS))
    dupes = duplicate_ids(rows)
    if dupes:
        print("  FAIL   proposals.csv still has duplicate ids: "
              + ", ".join(f"prj_{p}" for p, _ in dupes))
        problems += 1
    else:
        print(f"  PASS   proposals.csv: {len(rows)} rows, all ids distinct")

    lc = os.path.join(root, "data", "project_lifecycles.csv")
    if os.path.isfile(lc):
        lrows = read_rows(lc)
        ids = [r.get("project_id") for r in lrows]
        if len(ids) != len(set(ids)):
            print("  STALE  data/project_lifecycles.csv still has duplicate project_ids "
                  "(regenerate: python3 project_resolution.py)")
            problems += 1
        else:
            print(f"  PASS   data/project_lifecycles.csv: {len(lrows)} rows, all project_ids distinct")

    # Cross-check: a row's coordinates should sit in the state it claims.
    # Contamination showed up here first -- PA projects pinned in Louisiana.
    #
    # The boxes come from state_bounds.py, which covers all 50 states plus DC
    # and PR. This check used to carry its own nine-state table, listing only
    # the states the collision happened to touch, and that narrowness cost it:
    # it reported two bad rows when there were six. The other four named
    # Kentucky, Arizona and Texas, none of which were in the table, so they
    # were never examined. control_group.py now blocks on this at build time;
    # what runs here is the same check against the published artifact.
    bu = os.path.join(root, "data", "baseline_universe.csv")
    if os.path.isfile(bu):
        sys.path.insert(0, root)
        try:
            import state_bounds
        except ImportError:
            print("  SKIP   state_bounds.py not importable; state cross-check not run")
            return problems
        # control_group.KNOWN_BAD is the one debt list of rows that are wrong
        # and awaiting a source. Reporting them is useful; failing on them
        # every run is not, because this script cannot fix them -- they need a
        # citation or a CMS-side correction. Anything NOT on that list is new
        # and is a genuine failure.
        try:
            from control_group import KNOWN_BAD
        except Exception:
            KNOWN_BAD = {}
        rows_bu = read_rows(bu)
        bad = state_bounds.violations(rows_bu, "universe_id", exempt=set(KNOWN_BAD))
        known = state_bounds.violations(rows_bu, "universe_id")
        if bad:
            print(f"  FAIL   {len(bad)} NEW row(s) plotted outside the state they claim:")
            for rid, name, st, la, lo in bad[:10]:
                print(f"           {rid} {name[:40]} ({st} @ {la},{lo})")
            problems += 1
        else:
            print("  PASS   data/baseline_universe.csv: no new row plotted outside its state")
        carried = [b for b in known if b[0] in KNOWN_BAD]
        if carried:
            print(f"  NOTE   {len(carried)} known-bad row(s) still on file "
                  f"(control_group.KNOWN_BAD; each needs a source, not a guess):")
            for rid, name, st, la, lo in carried:
                print(f"           {rid} {name[:38]} ({st} @ {la},{lo})")

    return problems


def run_regen(root: str) -> int:
    print("\nRegenerating derived artifacts")
    failed = 0
    for cmd in REGEN:
        name = cmd[-1]
        if not os.path.isfile(os.path.join(root, name)):
            print(f"  SKIP   {name} (not present)")
            continue
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        tail = (p.stdout or p.stderr).strip().splitlines()
        last = tail[-1] if tail else ""
        if p.returncode == 0:
            print(f"  OK     {name}  {last[:88]}")
        else:
            failed += 1
            print(f"  FAIL   {name}  (exit {p.returncode})")
            for line in tail[-6:]:
                print("           " + line[:120])
    return failed


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="report collisions and exit non-zero if any exist; change nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="show every change without writing")
    ap.add_argument("--regen", action="store_true",
                    help="after applying, rebuild the derived artifacts")
    args = ap.parse_args()

    root = find_root()
    print(f"repo: {root}\n")

    rows = read_rows(os.path.join(root, PROPOSALS))
    n_bad = report_collisions(rows)

    if args.check:
        print("\n--check: nothing written.")
        return 1 if n_bad else 0

    print("\nApplying" + (" (dry run)" if args.dry_run else ""))
    rep = Reporter(args.dry_run)
    migrated = step_proposals(root, rep) > 0
    step_curated(root, rep, migrated)
    step_guard(root, rep)
    step_gate_copy(root, rep)
    step_docs(root, rep)
    print(f"\n{rep.changed} change(s), {rep.skipped} already in place")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    failed = run_regen(root) if args.regen else 0
    problems = verify(root)

    if not args.regen:
        print("\nDerived artifacts are stale until the pipeline runs. Either push and let "
              "Actions rebuild them, or re-run this with --regen.")
    if failed:
        print(f"\n{failed} regeneration step(s) failed -- see output above.")
    return 1 if (problems or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
