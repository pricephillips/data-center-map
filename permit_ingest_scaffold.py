"""
permit_ingest_scaffold.py — propose an ingest column map from a fetched source.

`fetch-permits.yml` is built so that adding a jurisdiction is a config drop
rather than a workflow edit, and for the fetch half that is already true: drop
`configs/<source>.json`, and discovery plus fetch pick it up on the next run.
The promote half is not. A source only reaches the dated baseline once someone
hand-writes `configs/<source>_ingest.json`, and of the nine registered sources
exactly one has that file. A resolved layer that nobody has written a column map
for produces candidates and stops there.

This module does the mechanical part of that write-up. It reads the rows the
fetch already produced and proposes the column map, the terminal-status
vocabulary and the source URL, so the human step is confirming a draft rather
than reading an unfamiliar schema from scratch.

What it will not do
-------------------
It writes `data/ingest_scaffold_<source>.json`, never
`configs/<source>_ingest.json`. Nothing here promotes a row, and a scaffold
does not become a live config until a person moves it. That is the same
review gate the harvester and the gap-closure loop already run behind, and it
matters more here than in either: a wrong column map does not fail, it
silently dates or names every row of a jurisdiction wrongly.

Two fields are deliberately left for the person:

  state       Not inferable. `loudoun_lola` is Virginia and nothing in the
              fetched rows or the fetch config says so. Emitted empty with a
              note rather than guessed from a source name.
  as_of       Emitted as today's date, which is when the scaffold was built,
              not when the upstream data was true. Confirm it against the
              source's own vintage before promoting anything.

Every proposal carries the evidence it was made from: the header it chose, the
headers it rejected, and the observed values behind the status vocabulary. A
proposal a reviewer cannot check is worse than no proposal.

Usage:
  python permit_ingest_scaffold.py --source loudoun_lola
  python permit_ingest_scaffold.py --all
  python permit_ingest_scaffold.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

CONFIGS = P("configs")
DATA = P("data")

# Ordered most-specific first: the first pattern that matches a header wins for
# that field, so "PlanApplicationDate" beats a bare "Date" for announced_date.
# No \b anchors. Portal schemas are overwhelmingly CamelCase, and "PlanStatus"
# has no word boundary between "n" and "S", so r"\bstatus\b" matches nothing
# in exactly the sources this is for. The selftest that reproduces the Loudoun
# map is what caught it.
NAME_PATTERNS = [r"project[_ ]?name", r"plan[_ ]?name", r"name", r"title",
                 r"description"]
DATE_PATTERNS = [r"applicat", r"filed", r"received", r"submit", r"announc",
                 r"created", r"date"]
STATUS_PATTERNS = [r"status", r"disposition", r"decision", r"outcome"]

# permit_ingest.py's own default, kept identical so a scaffold that proposes
# nothing still describes what the ingest would do.
DEFAULT_TERMINAL = ["approved", "denied", "withdrawn", "issued", "final"]


def _match(headers, patterns):
    """First header matching the most specific pattern. Returns (choice, why)."""
    for pat in patterns:
        for h in headers:
            if re.search(pat, h, re.IGNORECASE):
                return h, pat
    return None, None


def propose_columns(headers):
    """Column map plus the evidence for each choice."""
    out, why = {}, {}
    for field, pats in (("name", NAME_PATTERNS),
                        ("announced_date", DATE_PATTERNS),
                        ("status", STATUS_PATTERNS)):
        choice, pat = _match(headers, pats)
        if choice:
            out[field] = choice
            why[field] = {"chose": choice, "matched_pattern": pat,
                          "other_headers": [h for h in headers if h != choice]}
        else:
            why[field] = {"chose": None, "matched_pattern": None,
                          "other_headers": list(headers)}
    return out, why


def propose_terminal_statuses(values, default_terminal=DEFAULT_TERMINAL):
    """Observed status values that are terminal under the default vocabulary.

    Returns (proposed, observed_counts). Proposing only what was actually
    observed keeps a jurisdiction's config honest about its own vocabulary:
    a source that never says "withdrawn" should not carry it.
    """
    counts = {}
    for v in values:
        v = (v or "").strip()
        if v:
            counts[v] = counts.get(v, 0) + 1
    proposed = sorted({v.lower() for v in counts if v.lower() in default_terminal})
    return proposed, counts


def scaffold(source, candidates_csv, fetch_cfg):
    with open(candidates_csv, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    columns, why = propose_columns(headers)
    status_col = columns.get("status")
    terminal, observed = propose_terminal_statuses(
        (r.get(status_col) for r in rows) if status_col else [])

    unresolved = [f for f in ("name", "announced_date", "status")
                  if f not in columns]

    return {
        "_scaffold": {
            "generated": date.today().isoformat(),
            "generated_by": "permit_ingest_scaffold.py",
            "source_rows": len(rows),
            "status": "DRAFT — confirm, then move to "
                      f"configs/{source}_ingest.json to make it live",
            "unresolved_fields": unresolved,
            "column_evidence": why,
            "observed_status_values": observed,
            "state_note": "Not inferable from the fetched rows or the fetch "
                          "config. Fill this in before promoting.",
            "as_of_note": "This is the scaffold's build date, not the "
                          "upstream vintage. Confirm against the source.",
        },
        "source": source,
        "state": "",
        "as_of": date.today().isoformat(),
        "default_source_url": (fetch_cfg or {}).get("url") or "",
        "columns": columns,
        "terminal_statuses": terminal or list(DEFAULT_TERMINAL),
    }


def sources_with_candidates():
    out = []
    for path in sorted(glob.glob(os.path.join(DATA, "permit_candidates_*.csv"))):
        name = os.path.basename(path)[len("permit_candidates_"):-len(".csv")]
        out.append(name)
    return out


def build(source):
    cand = os.path.join(DATA, f"permit_candidates_{source}.csv")
    if not os.path.exists(cand):
        print(f"  {source}: no fetched candidates yet, nothing to scaffold")
        return None
    cfg_path = os.path.join(CONFIGS, f"{source}.json")
    fetch_cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as fh:
            fetch_cfg = json.load(fh)

    live = os.path.join(CONFIGS, f"{source}_ingest.json")
    out = scaffold(source, cand, fetch_cfg)
    out_path = os.path.join(DATA, f"ingest_scaffold_{source}.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")

    state = "already live" if os.path.exists(live) else "NOT yet live"
    unresolved = out["_scaffold"]["unresolved_fields"]
    note = f"; unresolved: {', '.join(unresolved)}" if unresolved else ""
    print(f"  {source}: {out['_scaffold']['source_rows']} rows, "
          f"{len(out['columns'])}/3 columns proposed, "
          f"{len(out['terminal_statuses'])} terminal status(es){note} "
          f"[{state}] -> {os.path.relpath(out_path, ROOT)}")
    return out_path


def main_build(which):
    names = sources_with_candidates() if which == "__all__" else [which]
    if not names:
        print("no fetched permit candidates found; nothing to scaffold")
        return 0
    print(f"permit ingest scaffold: {len(names)} source(s) with fetched rows")
    for n in names:
        build(n)
    return 0


def selftest():
    checks = []

    def check(label, ok):
        checks.append((label, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    # The one real example in the tree: reproduce the hand-written Loudoun map
    # from the headers its fetch actually produced.
    loudoun = ["PlanApplicationDate", "PlanDescription", "PlanName",
               "PlanNumber", "PlanStatus", "PlanType"]
    cols, why = propose_columns(loudoun)
    check("reproduces the hand-written Loudoun column map",
          cols == {"name": "PlanName", "announced_date": "PlanApplicationDate",
                   "status": "PlanStatus"})
    check("prefers an application date over a bare date column",
          propose_columns(["Date", "ApplicationDate"])[0]["announced_date"]
          == "ApplicationDate")
    check("prefers a project name over a description",
          propose_columns(["Description", "ProjectName"])[0]["name"]
          == "ProjectName")
    check("records why a header was chosen",
          why["name"]["chose"] == "PlanName"
          and why["name"]["matched_pattern"] is not None)
    check("records the headers it did not choose",
          "PlanType" in why["name"]["other_headers"])

    missing, _ = propose_columns(["Foo", "Bar"])
    check("proposes nothing rather than guessing when no header matches",
          missing == {})

    # Loudoun's real status distribution.
    vals = (["Approved"] * 371 + ["In Review"] * 135
            + ["Submitted - Online"] * 9 + ["Submitted"] * 7 + ["Denied"])
    terminal, observed = propose_terminal_statuses(vals)
    check("reproduces the hand-written Loudoun terminal statuses",
          terminal == ["approved", "denied"])
    check("counts every observed status value",
          observed["Approved"] == 371 and observed["Denied"] == 1)
    check("does not propose a terminal status the source never uses",
          "withdrawn" not in terminal)
    check("ignores blank status values",
          propose_terminal_statuses(["", "  ", "Approved"])[1] == {"Approved": 1})
    check("proposes nothing terminal when nothing observed is terminal",
          propose_terminal_statuses(["In Review", "Pending"])[0] == [])

    n_ok = sum(1 for _, ok in checks if ok)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--source", help="one source name, e.g. loudoun_lola")
    ap.add_argument("--all", action="store_true",
                    help="every source with fetched candidates")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.all:
        return main_build("__all__")
    if args.source:
        return main_build(args.source)
    ap.error("give --source NAME, --all, or --selftest")


if __name__ == "__main__":
    raise SystemExit(main())
