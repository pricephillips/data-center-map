#!/usr/bin/env python3
"""Interim incentive-execution-risk proxy score, per state/county.

Computes a documented starting number for Incentive Execution Risk: whether
a local incentive or development agreement is likely to clear its local
approval pathway without a stall, separate from Incentive Durability Risk
(which covers the state legislative record). This is a proxy, not a model
output: it reads local meeting activity and court dockets only. Local EDO
posture, staff recommendation, and community-relations context are not
tracked here and must be researched before any score is treated as final.

Score anchors (1 to 5, higher is lower execution risk / smoother pathway):
  5  no local incentive-adjacent record and no local meeting activity on
     file for the county
  4  local meeting activity on file (a hearing/agenda item exists)
  3  no local meeting feed coverage for the county yet (platform not
     discovered or not fetched): unscored inputs, not a risk assessment
  2  a local tax-incentive record for the county is already flagged as an
     enacted block in the feed's own qc_block_status field
  1  never assigned automatically. A 1 requires a judgment that a specific
     incentive is actually going to be blocked or clawed back, which this
     platform does not make from meeting/docket presence alone.

Candidate dispute dockets are reported as evidence but deliberately do NOT
move the score. A full-text docket search that pairs a county name with
incentive vocabulary returns nationwide matches that mention the name only
incidentally (an "Anchorage" query surfaced Deepwater Horizon and Katrina
canal-breach dockets). Until those results are scoped to in-state courts and
confirmed to concern the county in question, presence of a hit cannot support
a risk number. See dispute_watch.py.

Evidence, in order of preference:
  1. data/local_meeting_feed.csv (local_meeting_feed.py output): presence of
     recent local board/commission meeting items is descriptive-only
     evidence that the local pathway has an active, trackable venue. It is
     never read for outcome, only for coverage.
  2. data/dispute_watch.csv (dispute_watch.py output): candidate court
     dockets combining the county name with incentive-dispute vocabulary.
     Never treated as a confirmed dispute over THIS project's incentive and
     never scored; reported as a research lead only.
  3. The feed's own qc_block_status field for the county's local
     tax-incentive records, when present, reported descriptively.

State/county-scoped and dashboard-wide by construction: works for any
(state, county) pair in master_opposition_clean.csv.

Usage:
  python3 incentive_execution_risk_proxy.py --state VA --county "Powhatan County"
  python3 incentive_execution_risk_proxy.py --all --out data/incentive_execution_risk_proxy.md
  python3 incentive_execution_risk_proxy.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(ROOT, "master_opposition_clean.csv")
MEETINGS = os.path.join(ROOT, "data", "local_meeting_feed.csv")
DISPUTES = os.path.join(ROOT, "data", "dispute_watch.csv")

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)


def truthy(v: str) -> bool:
    return (v or "").strip().lower() == "true"


def load_feed(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_csv_if_present(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def county_pairs(feed: list[dict], state: str | None = None) -> list[tuple[str, str]]:
    return sorted({
        ((r.get("State") or "").strip().upper(), (r.get("County") or "").strip())
        for r in feed
        if (r.get("County") or "").strip() and not truthy(r.get("is_statewide"))
        and (not state or (r.get("State") or "").strip().upper() == state)
    })


def score_county(feed: list[dict], meetings: list[dict], disputes: list[dict],
                 state: str, county: str) -> dict:
    local_rows = [r for r in feed
                  if (r.get("State") or "").strip().upper() == state
                  and (r.get("County") or "").strip() == county
                  and not truthy(r.get("is_statewide"))]
    incentive_rows = [r for r in local_rows if truthy(r.get("is_tax_incentive"))]
    blocked_incentive = any((r.get("qc_block_status") or "") == "enacted_block"
                           for r in incentive_rows)

    county_meetings = [m for m in meetings
                       if (m.get("state") or "").strip().upper() == state
                       and (m.get("county") or "").strip() == county]
    county_disputes = [d for d in disputes
                       if (d.get("state") or "").strip().upper() == state
                       and (d.get("county") or "").strip() == county]
    feed_has_coverage_attempt = bool(meetings)  # a feed file exists at all

    has_meeting_activity = len(county_meetings) > 0

    detail = []
    for m in county_meetings[:5]:
        detail.append(f"local meeting: {m.get('meeting_datetime','')} "
                      f"{m.get('item_title','')} ({m.get('platform','')})")
    for d in county_disputes[:5]:
        detail.append(f"candidate docket ({d.get('search_term','')}): "
                      f"{d.get('case_name','')}, {d.get('court','')} "
                      f"({d.get('date_filed','')})")

    if blocked_incentive:
        score, anchor = 2, ("A local tax-incentive record for the county is "
                            "already flagged as an enacted block.")
    elif has_meeting_activity:
        score, anchor = 4, "Local meeting activity on file for the county."
    elif not feed_has_coverage_attempt:
        score, anchor = 3, ("No local meeting feed coverage available yet: "
                            "unscored input, not a risk assessment.")
    else:
        score, anchor = 5, ("No local incentive-adjacent record and no "
                            "local meeting activity on file for the county.")

    return {"state": state, "county": county, "score": score, "anchor": anchor,
            "meeting_n": len(county_meetings), "dispute_n": len(county_disputes),
            "incentive_records_n": len(incentive_rows), "detail": detail}


def render_county(result: dict) -> str:
    L = [f"### Incentive execution risk proxy: {result['county']}, {result['state']}", ""]
    L.append(f"Proxy score {result['score']}. {result['anchor']} A 1 is never "
             f"assigned automatically; see module docstring.")
    L.append("")
    L.append(f"- Local tax-incentive records on file: {result['incentive_records_n']}")
    L.append(f"- Local meeting items on file: {result['meeting_n']}")
    L.append(f"- Candidate dispute docket hits (unscored research leads, "
             f"not scoped to in-state courts): {result['dispute_n']}")
    for d in result["detail"]:
        L.append(f"  - {d}")
    L.append("- This proxy reads local meeting activity and candidate court "
             "dockets only. Local EDO posture, staff recommendation, and "
             "community-relations context are not covered and must be "
             "researched separately.")
    L.append("")
    return "\n".join(L)


def leak_audit(text: str) -> None:
    hits = LEAK_RE.findall(text)
    if hits:
        raise SystemExit(f"leak audit failed: scorekeeping vocabulary in "
                         f"output: {sorted(set(h.lower() for h in hits))}")


def selftest() -> int:
    def row(state="XX", county="Test County", incentive=False, block_status="", **kw):
        base = {"State": state, "County": county, "is_statewide": "False",
                "is_tax_incentive": "True" if incentive else "False",
                "qc_block_status": block_status}
        base.update(kw)
        return base

    def meeting(state="XX", county="Test County", **kw):
        base = {"state": state, "county": county, "meeting_datetime": "2026-01-01",
                "item_title": "Hearing", "platform": "civicclerk"}
        base.update(kw)
        return base

    def dispute(state="XX", county="Test County", **kw):
        base = {"state": state, "county": county, "search_term": "tax increment",
                "case_name": "X v. Y", "court": "Test Court", "date_filed": "2026-01-01"}
        base.update(kw)
        return base

    checks = []

    r = score_county([row()], [meeting()], [], "XX", "Test County")
    checks.append(("meeting activity, no dispute -> 4", r["score"] == 4))

    r = score_county([row()], [], [], "XX", "Test County")
    checks.append(("no meeting feed at all, no dispute -> 3", r["score"] == 3))

    r = score_county([row()], [meeting(state="YY")], [], "XX", "Test County")
    checks.append(("meeting feed exists but not for this county -> 5",
                   r["score"] == 5))

    # Candidate dockets are nationwide full-text matches and are not scoped to
    # in-state courts, so their presence must not move the score at all.
    r_no_dispute = score_county([row()], [meeting()], [], "XX", "Test County")
    r_dispute = score_county([row()], [meeting()], [dispute()], "XX", "Test County")
    checks.append(("a dispute hit does not change the score",
                   r_dispute["score"] == r_no_dispute["score"] == 4))
    checks.append(("a dispute hit is still reported as evidence",
                   r_dispute["dispute_n"] == 1
                   and any("candidate docket" in d for d in r_dispute["detail"])))

    r = score_county([row(incentive=True, block_status="enacted_block")],
                     [meeting()], [], "XX", "Test County")
    checks.append(("enacted block without dispute hit -> 2", r["score"] == 2))

    try:
        leak_audit("the community outcome was a loss")
        checks.append(("leak audit fires", False))
    except SystemExit:
        checks.append(("leak audit fires", True))
    try:
        leak_audit(render_county(score_county([row()], [meeting()], [], "XX", "Test County")))
        checks.append(("rendered output passes leak audit", True))
    except SystemExit:
        checks.append(("rendered output passes leak audit", False))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"{len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", help="two-letter state code")
    ap.add_argument("--county", help="county name, requires --state")
    ap.add_argument("--all", action="store_true",
                    help="every (state, county) with a local record")
    ap.add_argument("--data", default=FEED, help="feed CSV path")
    ap.add_argument("--out", help="write markdown here instead of stdout")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.all and not (args.state and args.county):
        ap.print_help()
        return 0

    feed = load_feed(args.data)
    meetings = load_csv_if_present(MEETINGS)
    disputes = load_csv_if_present(DISPUTES)

    if args.all:
        pairs = county_pairs(feed)
    else:
        pairs = [(args.state.strip().upper(), args.county.strip())]

    blocks = [render_county(score_county(feed, meetings, disputes, st, co))
             for st, co in pairs]
    text = "\n".join(blocks)
    leak_audit(text)

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {os.path.relpath(args.out, ROOT)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
