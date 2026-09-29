#!/usr/bin/env python3
"""
project_id_rekey.py

Finds hand-maintained rows whose project id now points at a different project,
and re-keys them to the project they were written about.

The defect
----------
project_id is "prj_" + the TrackDataCenters id, and that id is not stable.
The source renumbered its whole id space on 2026-09-17 and again on
2026-09-22 (and partially on 2026-05-01, 05-21 and 07-02; proposal_history.py
detects and records each one). Every hand-verified row written before a
renumbering kept its old number, and the old number now belongs to someone
else. On 2026-09-28:

  data/proposals_manual_overlay.csv  id 141 "approval VOIDED ... must not show
      as approved" was written for Project Delta (NC) and was forcing
      Armory Innovation Data Center (St. Louis, MO) to phase "proposed".
  data/project_decision_dates.csv    prj_74 carried Saline Township (MI)'s
      decision onto Applied Digital Adair (IA); prj_158 carried the Apex (NC)
      Natelli withdrawal onto Kenwood Commons (Albany, NY).

Nothing errored. The joins still matched, which is the whole problem.

How a row is resolved
---------------------
1. When was the row written? `git blame` gives the commit time of each line.
2. Which project held that id at that time? data/pipeline_intel_id_timeline.csv
   (written by proposal_history.py) gives every project's source-id spans
   between snapshot commits. The span in force at the blame time identifies
   the project the author was looking at.
3. What does it carry now? That project's current id.

Blame is evidence, not proof: a bulk rewrite of a file (line-ending changes,
a migration script) moves every line's blame date forward. So a second,
independent test is applied wherever the row has prose to test against: the
row's own text (reason, source, note, URL) is scored against the names of the
two candidate projects, the one blame points to and the one the raw id points
to today. A row is re-keyed only when blame and text agree, or when blame
points elsewhere and the text cannot tell. When the text names the CURRENT
occupant, the row is kept: it was written in today's id space.

Verdicts: ok, rekey, keep_text_confirms_current, orphan (the project blame
points to is no longer in the source), unresolved (no span holds the id at the
blame time).

Outputs
-------
  data/pipeline_intel_rekey_report.csv   one row per keyed row examined
  data/pipeline_intel_rekey_report.md    summary and every non-ok verdict

Usage
-----
  python project_id_rekey.py              report only
  python project_id_rekey.py --apply      rewrite ids for verdict == rekey
  python project_id_rekey.py --selftest

Ids of 1000 and above are manual additions (data/proposals_added.csv) that
never pass through the source's id space, and are left alone.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TIMELINE = os.path.join(HERE, "data", "pipeline_intel_id_timeline.csv")
OUT_CSV = os.path.join(HERE, "data", "pipeline_intel_rekey_report.csv")
OUT_MD = os.path.join(HERE, "data", "pipeline_intel_rekey_report.md")
MANUAL_FLOOR = 1000

# (path, id columns, prefixed?) for every hand-maintained file keyed on a
# source id. Generated files are not listed: they are rebuilt from these.
KEYED_FILES = [
    ("data/proposals_manual_overlay.csv", ("id",), False),
    ("data/project_decision_dates.csv", ("project_id",), True),
    ("data/project_links_manual.csv", ("project_id",), True),
    ("data/project_duplicates.csv", ("keep_id", "drop_id"), True),
    ("data/negative_audit_codings.csv", ("universe_id",), True),
]

REPORT_FIELDS = ["file", "line", "column", "raw_id", "written_at", "verdict",
                 "new_id", "written_about", "occupant_now", "text_score_about",
                 "text_score_now", "detail"]

_GENERIC = {"data", "center", "centre", "campus", "project", "the", "of", "and",
            "technology", "tech", "park", "digital", "dc", "ai", "site", "llc",
            "inc", "county", "township", "city", "town", "new", "north", "south",
            "east", "west"}


def _tokens(s):
    return {t for t in re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split()
            if len(t) > 2 and t not in _GENERIC}


def text_score(text, name, state=""):
    """Share of a project's distinctive name tokens that appear in the text."""
    nt = _tokens(name)
    if not nt:
        return 0.0
    tt = _tokens(text)
    return len(nt & tt) / len(nt)


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def _utc(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_timeline(path=TIMELINE):
    by_sid = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            r["_t0"], r["_t1"] = _utc(r["first_snapshot"]), _utc(r["last_snapshot"])
            by_sid[r["source_id"]].append(r)
    for spans in by_sid.values():
        spans.sort(key=lambda r: r["_t0"])
    return by_sid


def current_occupant(by_sid, sid):
    for r in by_sid.get(sid, []):
        if r["present_now"] == "yes" and r["current_id"] == sid:
            return r
    return None


def span_at(by_sid, sid, when, panel_start=None):
    """The span holding `sid` at time `when`.

    A row written between two snapshots belongs to the id space of the last
    snapshot before it, so the span is the latest one starting at or before
    `when`, provided that project still held the id until at least the next
    snapshot after its start. Before the first snapshot, the earliest span.
    """
    spans = by_sid.get(sid, [])
    before = [r for r in spans if r["_t0"] <= when]
    if before:
        return max(before, key=lambda r: r["_t0"])
    # Written before this id existed. Only a row older than the whole panel
    # may borrow the earliest span; otherwise the id meant something the
    # panel never saw, and guessing would be the defect this module repairs.
    if spans and panel_start is not None and spans[0]["_t0"] <= panel_start:
        return spans[0]
    return None


def blame_times(path, cwd=HERE):
    """{1-based line number: commit time (UTC)} via git blame --line-porcelain."""
    out = subprocess.run(["git", "blame", "--line-porcelain", "--", path],
                         cwd=cwd, check=True, capture_output=True, text=True).stdout
    times, line, t = {}, None, None
    for ln in out.splitlines():
        m = re.match(r"^[0-9a-f]{40} \d+ (\d+)", ln)
        if m:
            line = int(m.group(1))
        elif ln.startswith("committer-time "):
            t = datetime.fromtimestamp(int(ln.split()[1]), tz=timezone.utc)
        elif ln.startswith("\t") and line is not None:
            times[line] = t
    return times


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

# The 2026-09-02 manual-addition renumbering (ARCHITECTURE.md). Those ids were
# never source ids, so the timeline cannot resolve them; a row written before
# the migration that carries one is translated by the published table.
MANUAL_MIGRATION = {str(o): str(n) for o, n in zip(range(321, 333), range(1001, 1013))}
MIGRATION_AT = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def panel_start_of(by_sid):
    starts = [r["_t0"] for spans in by_sid.values() for r in spans]
    return min(starts) if starts else None


def resolve(raw_sid, when, row_text, by_sid, panel_start=None):
    """Return (verdict, new_sid, about_row, occupant_row, s_about, s_now, detail)."""
    occ = current_occupant(by_sid, raw_sid)
    if when and when < MIGRATION_AT and raw_sid in MANUAL_MIGRATION:
        new = MANUAL_MIGRATION[raw_sid]
        about = current_occupant(by_sid, new)
        sa = text_score(row_text, about["name"]) if about else ""
        sn = text_score(row_text, occ["name"]) if occ else 0.0
        return ("rekey", new, about, occ, sa, sn,
                "manual addition written before the 2026-09-02 renumbering")
    about = span_at(by_sid, raw_sid, when, panel_start) if when else None
    if about is None:
        return ("unresolved", "", None, occ, "", "",
                "no project held this id at the time the row was written")
    s_about = text_score(row_text, about["name"])
    s_now = text_score(row_text, occ["name"]) if occ else 0.0
    if about["present_now"] != "yes":
        return ("orphan", "", about, occ, s_about, s_now,
                "the project this row was written about has left the source")
    new = about["current_id"]
    if new == raw_sid:
        return ("ok", raw_sid, about, occ, s_about, s_now, "")
    if occ is not None and s_now > s_about and s_now >= 0.5:
        return ("keep_text_confirms_current", raw_sid, about, occ, s_about, s_now,
                "blame points to an older id space but the row's text names the "
                "project that holds the id today")
    return ("rekey", new, about, occ, s_about, s_now,
            "text agrees with blame" if s_about > s_now else
            "blame decides; the text names neither project")


def examine(files=KEYED_FILES, by_sid=None, cwd=HERE, blame=blame_times):
    by_sid = by_sid if by_sid is not None else load_timeline()
    p0 = panel_start_of(by_sid)
    report = []
    for rel, cols, prefixed in files:
        path = os.path.join(cwd, rel)
        if not os.path.exists(path):
            continue
        times = blame(rel, cwd=cwd)
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for i, r in enumerate(rows):
            line = i + 2                           # header is line 1
            text = " ".join(v for k, v in r.items() if k not in cols and v)
            for col in cols:
                raw = str(r.get(col) or "").strip()
                sid = raw[4:] if prefixed and raw.startswith("prj_") else raw
                if not sid.isdigit() or int(sid) >= MANUAL_FLOOR:
                    continue
                when = times.get(line)
                v, new, about, occ, sa, sn, det = resolve(sid, when, text, by_sid, p0)
                report.append({
                    "file": rel, "line": line, "column": col, "raw_id": raw,
                    "written_at": when.isoformat().replace("+00:00", "Z") if when else "",
                    "verdict": v,
                    "new_id": (("prj_" if prefixed else "") + new) if new else "",
                    "written_about": f"{about['name']} ({about['state']})" if about else "",
                    "occupant_now": f"{occ['name']} ({occ['state']})" if occ else "",
                    "text_score_about": f"{sa:.2f}" if sa != "" else "",
                    "text_score_now": f"{sn:.2f}" if sn != "" else "",
                    "detail": det})
    return report


def apply(report, cwd=HERE):
    """Rewrite ids for verdict == rekey. Preserves column order and line endings."""
    by_file = defaultdict(dict)
    for r in report:
        if r["verdict"] == "rekey":
            by_file[r["file"]][(r["line"], r["column"])] = r["new_id"]
    changed = Counter()
    for rel, edits in by_file.items():
        path = os.path.join(cwd, rel)
        raw = open(path, "rb").read()
        crlf = b"\r\n" in raw
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            fields, rows = reader.fieldnames, list(reader)
        for (line, col), new in edits.items():
            rows[line - 2][col] = new
            changed[rel] += 1
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields,
                               lineterminator="\r\n" if crlf else "\n")
            w.writeheader()
            w.writerows(rows)
    return changed


def write_report(report, csv_path=OUT_CSV, md_path=OUT_MD):
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REPORT_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(report)
    counts = Counter(r["verdict"] for r in report)
    lines = ["# Project id re-key report", "",
             "Generated by `project_id_rekey.py`. Every hand-maintained row keyed on "
             "a TrackDataCenters id, resolved to the project it was written about "
             "using git blame and `data/pipeline_intel_id_timeline.csv`.", "",
             "| verdict | rows |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "## Every row that is not ok", "",
              "| file | line | id | verdict | new id | written about | holds the id now | detail |",
              "|---|---|---|---|---|---|---|---|"]
    for r in report:
        if r["verdict"] != "ok":
            lines.append(f"| {r['file']} | {r['line']} | {r['raw_id']} | {r['verdict']} | "
                         f"{r['new_id']} | {r['written_about']} | {r['occupant_now']} | "
                         f"{r['detail']} |")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest():
    checks = []

    def check(label, ok):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    T = lambda s: _utc(s)
    # id 141: Project Delta (NC) until the 17th, then Millville, then Armory.
    # Project Delta now carries id 150.
    tl = defaultdict(list)
    for sid, t0, t1, cur, now, name, st in [
        ("141", "2026-07-01T11:00:00Z", "2026-09-14T11:00:00Z", "150", "yes", "Project Delta", "North Carolina"),
        ("145", "2026-09-17T11:00:00Z", "2026-09-17T11:00:00Z", "150", "yes", "Project Delta", "North Carolina"),
        ("150", "2026-09-22T11:00:00Z", "2026-09-25T11:00:00Z", "150", "yes", "Project Delta", "North Carolina"),
        ("141", "2026-09-22T11:00:00Z", "2026-09-25T11:00:00Z", "141", "yes", "Armory Innovation Data Center", "Missouri"),
        ("77", "2026-07-01T11:00:00Z", "2026-08-01T11:00:00Z", "77", "no", "Gone Project", "Ohio"),
    ]:
        tl[sid].append({"source_id": sid, "_t0": T(t0), "_t1": T(t1), "current_id": cur,
                        "present_now": now, "name": name, "state": st})
    for spans in tl.values():
        spans.sort(key=lambda r: r["_t0"])

    v = resolve("141", T("2026-07-23T15:00:00Z"),
                "Approval VOIDED; must not show as approved", tl)
    check("a July row keyed 141 resolves to the project it was written about",
          v[0] == "rekey" and v[1] == "150" and v[2]["name"] == "Project Delta")
    v = resolve("141", T("2026-07-23T15:00:00Z"), "Project Delta withdrew", tl)
    check("text naming the original project strengthens the re-key",
          v[0] == "rekey" and v[6] == "text agrees with blame")
    v = resolve("141", T("2026-09-28T15:00:00Z"), "Armory Innovation Market Street", tl)
    check("a row written after the renumbering is already in today's space",
          v[0] == "ok")
    v = resolve("141", T("2026-08-01T15:00:00Z"), "Armory Innovation, St. Louis", tl)
    check("an old blame date is overruled when the text names today's occupant",
          v[0] == "keep_text_confirms_current")
    check("a project that left the source is an orphan, not re-keyed",
          resolve("77", T("2026-07-15T00:00:00Z"), "", tl)[0] == "orphan")
    check("an id nobody held is unresolved",
          resolve("999", T("2026-07-15T00:00:00Z"), "", tl)[0] == "unresolved")
    check("a row with no blame time is unresolved, never guessed",
          resolve("141", None, "", tl)[0] == "unresolved")
    check("an id first seen after the row was written is unresolved, not borrowed",
          resolve("150", T("2026-07-15T00:00:00Z"), "", tl, T("2026-07-01T11:00:00Z"))[0]
          == "unresolved")
    check("a pre-panel row may borrow the earliest span",
          resolve("141", T("2026-06-01T00:00:00Z"), "", tl, T("2026-07-01T11:00:00Z"))[1]
          == "150")
    check("a pre-migration manual id follows the published renumbering",
          resolve("322", T("2026-07-23T00:00:00Z"), "Province Group", tl)[:2]
          == ("rekey", "1002"))
    check("a post-migration 322 is a source id like any other",
          resolve("322", T("2026-09-10T00:00:00Z"), "", tl)[0] == "unresolved")
    check("text scoring ignores generic words",
          text_score("the data center project", "Project Data Center") == 0.0)

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "data"))
        p = os.path.join(d, "data", "o.csv")
        with open(p, "w", newline="", encoding="utf-8") as fh:
            fh.write("id,field,value,reason\r\n141,phase,proposed,VOIDED\r\n1001,x,y,z\r\n")
        files = [("data/o.csv", ("id",), False)]
        fake_blame = lambda rel, cwd: {2: T("2026-07-23T15:00:00Z"),
                                       3: T("2026-07-23T15:00:00Z")}
        rep = examine(files, tl, cwd=d, blame=fake_blame)
        check("manual-addition ids are never examined", len(rep) == 1)
        apply(rep, cwd=d)
        body = open(p, "rb").read()
        check("apply rewrites only the id", b"150,phase,proposed,VOIDED" in body)
        check("apply preserves CRLF line endings", body.count(b"\r\n") == 3)

    n = sum(checks)
    print(f"\n{n}/{len(checks)} checks passed")
    return 0 if n == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="Re-key hand-maintained rows after source id renumbering")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    rep = examine()
    write_report(rep)
    c = Counter(r["verdict"] for r in rep)
    print(f"project_id_rekey: {len(rep)} keyed rows examined {dict(c)} -> {OUT_MD}")
    if a.apply:
        ch = apply(rep)
        print(f"project_id_rekey: applied {sum(ch.values())} re-key(s) {dict(ch)}")


if __name__ == "__main__":
    main()
