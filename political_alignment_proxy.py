#!/usr/bin/env python3
"""Interim political alignment proxy score, per state.

Computes a documented starting number for Political Alignment from evidence
the pipeline already holds, the same interim-proxy approach
incentive_durability_proxy.py takes for Incentive Durability Risk. This is a
proxy, not a model output: it reads the public legislative record only.
Governor's position, local board/EDO stance, and utility/regulator posture
are not covered here and must be researched before any score is treated as
final (see political_alignment_worklist.py for that checklist).

Score anchors (1 to 5, higher is more aligned toward allowing/expediting a
data center project):
  5  no statewide legislative record touching data centers on file, or every
     tracked record's stance is supportive with no restrictive record on file
  4  supportive and restrictive records both exist, but verified chamber
     votes (bill_sync_votes.csv) show restrictive measures failing and/or
     supportive measures passing by a clear margin
  3  mixed record, no verified vote evidence tips the balance either way:
     monitoring required
  2  restrictive records outnumber supportive, or a verified chamber vote
     shows a restrictive measure passing / a supportive measure failing
  1  never assigned automatically. A 1 requires a judgment that the
     legislature is actively hostile, which is a stance call this platform
     does not make from vote counts alone.

Evidence, in order of preference:
  1. data/bill_sync_votes.csv, when present: verified per-legislator roll
     call (bill_sync.py --resolve output) gives a chamber-level pass/fail
     margin for the specific bill.
  2. The feed's own qc_leg_stance field (supportive/restrictive/unclear),
     already computed by the QC layer for every statewide legislative
     record, otherwise.

State-scoped and dashboard-wide by construction: works for any state code
present in master_opposition_clean.csv, not a fixed list of sites.

Usage:
  python3 political_alignment_proxy.py --state VA
  python3 political_alignment_proxy.py --all --out data/political_alignment_proxy.md
  python3 political_alignment_proxy.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(ROOT, "master_opposition_clean.csv")
VOTES = os.path.join(ROOT, "data", "bill_sync_votes.csv")

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)


def truthy(v: str) -> bool:
    return (v or "").strip().lower() == "true"


def load_feed(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_vote_margins(path: str) -> dict[str, dict]:
    """Map (state, identifier) -> {yes, no, chambers: {chamber: (yes, no)}},
    when bill_sync.py --resolve has produced verified roll-call rows."""
    if not os.path.exists(path):
        return {}
    tallies: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = f"{(r.get('state') or '').strip().upper()}:{(r.get('identifier') or '').strip()}"
            opt = (r.get("option") or "").strip().lower()
            chamber = (r.get("chamber") or "").strip().lower()
            entry = tallies.setdefault(key, {"yes": 0, "no": 0,
                                              "chambers": defaultdict(lambda: [0, 0])})
            if opt == "yes":
                entry["yes"] += 1
                entry["chambers"][chamber][0] += 1
            elif opt == "no":
                entry["no"] += 1
                entry["chambers"][chamber][1] += 1
    return tallies


def alignment_rows(feed: list[dict], state: str) -> list[dict]:
    return [r for r in feed
            if (r.get("State") or "").strip().upper() == state
            and truthy(r.get("is_statewide"))
            and (r.get("qc_leg_stance") or "").strip().lower() in
                ("supportive", "restrictive", "unclear")]


def row_verified_outcome(row: dict, margins: dict[str, dict]) -> tuple[str, str]:
    """(outcome, evidence). outcome is 'passed', 'failed', or '' (no verified
    vote). A verified vote overrides the feed's qc_leg_stance for direction
    of THIS record's actual chamber outcome."""
    state = (row.get("State") or "").strip().upper()
    ident = (row.get("project_descriptor") or "").strip()
    key = f"{state}:{ident}"
    m = margins.get(key)
    if not m:
        return "", ""
    total_yes, total_no = m["yes"], m["no"]
    if total_yes == 0 and total_no == 0:
        return "", ""
    outcome = "passed" if total_yes > total_no else "failed"
    ev = f"verified roll call: {total_yes} yes / {total_no} no"
    return outcome, ev


def score_state(feed: list[dict], state: str, margins: dict[str, dict]) -> dict:
    rows = alignment_rows(feed, state)
    detail = []
    supportive_n = restrictive_n = 0
    verified_restrictive_passed = verified_supportive_failed = False
    verified_restrictive_failed = verified_supportive_passed = False

    for r in rows:
        stance = (r.get("qc_leg_stance") or "").strip().lower()
        outcome, ev = row_verified_outcome(r, margins)
        if stance == "supportive":
            supportive_n += 1
            if outcome == "passed":
                verified_supportive_passed = True
            elif outcome == "failed":
                verified_supportive_failed = True
        elif stance == "restrictive":
            restrictive_n += 1
            if outcome == "passed":
                verified_restrictive_passed = True
            elif outcome == "failed":
                verified_restrictive_failed = True
        detail.append({
            "date": r.get("Date") or "undated",
            "identifier": r.get("project_descriptor") or "unidentified",
            "stance": stance,
            "verified_outcome": outcome,
            "evidence": ev or f"feed qc_leg_stance: {stance}",
            "source_url": r.get("Source URL") or "",
        })

    if not rows:
        score, anchor = 5, "No statewide legislative record touching data centers on file."
    elif restrictive_n == 0:
        score, anchor = 5, ("Every tracked statewide record's stance is "
                            "supportive; no restrictive record on file.")
    elif verified_restrictive_passed or verified_supportive_failed:
        score, anchor = 2, ("A verified chamber roll call shows a "
                            "restrictive measure passing or a supportive "
                            "measure failing.")
    elif verified_restrictive_failed or verified_supportive_passed:
        score, anchor = 4, ("Verified chamber roll calls show restrictive "
                            "measures failing and/or supportive measures "
                            "passing by a clear margin.")
    elif restrictive_n > supportive_n:
        score, anchor = 2, "Restrictive statewide records outnumber supportive ones."
    else:
        score, anchor = 3, ("Mixed statewide record with no verified vote "
                            "evidence tipping the balance: monitoring required.")

    return {"state": state, "score": score, "anchor": anchor,
            "records_n": len(rows), "supportive_n": supportive_n,
            "restrictive_n": restrictive_n, "detail": detail}


def render_state(result: dict, votes_present: bool) -> str:
    L = [f"### Political alignment proxy: {result['state']}", ""]
    L.append(f"Proxy score {result['score']}. {result['anchor']} A 1 is never "
             f"assigned automatically; see module docstring.")
    L.append("")
    L.append(f"- Statewide legislative records on data centers: "
             f"{result['records_n']} ({result['supportive_n']} supportive, "
             f"{result['restrictive_n']} restrictive)")
    src = ("verified roll-call votes where available (bill_sync_votes.csv), "
           "feed qc_leg_stance otherwise" if votes_present else
           "feed qc_leg_stance only; run bill_sync.py --resolve for "
           "verified roll-call votes")
    L.append(f"- Stance evidence: {src}")
    for d in result["detail"]:
        L.append(f"  - {d['date']} ({d['identifier']}): {d['stance']} "
                 f"({d['evidence']})")
    L.append("- This proxy reads the public statewide legislative record "
             "only. Governor's position, local board and EDO stance, and "
             "utility/regulator posture are not covered and must be "
             "researched separately; see political_alignment_worklist.py.")
    L.append("")
    return "\n".join(L)


def leak_audit(text: str) -> None:
    hits = LEAK_RE.findall(text)
    if hits:
        raise SystemExit(f"leak audit failed: scorekeeping vocabulary in "
                         f"output: {sorted(set(h.lower() for h in hits))}")


def selftest() -> int:
    def row(state="XX", stance="unclear", ident="HB 1", **kw):
        base = {"State": state, "is_statewide": "True", "qc_leg_stance": stance,
                "project_descriptor": ident, "Date": "2026-01-01",
                "Source URL": ""}
        base.update(kw)
        return base

    checks = []
    checks.append(("no records -> 5", score_state([], "XX", {})["score"] == 5))
    checks.append(("all supportive -> 5",
                   score_state([row(stance="supportive")], "XX", {})["score"] == 5))
    checks.append(("restrictive outnumber supportive -> 2",
                   score_state([row(stance="restrictive", ident="HB 1"),
                               row(stance="restrictive", ident="HB 2"),
                               row(stance="supportive", ident="HB 3")],
                              "XX", {})["score"] == 2))
    checks.append(("balanced mixed with no vote evidence -> 3",
                   score_state([row(stance="restrictive", ident="HB 1"),
                               row(stance="supportive", ident="HB 2")],
                              "XX", {})["score"] == 3))

    margins_pass = {"XX:HB 1": {"yes": 80, "no": 5, "chambers": {}}}
    checks.append(("verified restrictive bill passing -> 2",
                   score_state([row(stance="restrictive", ident="HB 1"),
                               row(stance="supportive", ident="HB 2")],
                              "XX", margins_pass)["score"] == 2))

    margins_fail = {"XX:HB 1": {"yes": 5, "no": 80, "chambers": {}}}
    checks.append(("verified restrictive bill failing -> 4",
                   score_state([row(stance="restrictive", ident="HB 1"),
                               row(stance="supportive", ident="HB 2")],
                              "XX", margins_fail)["score"] == 4))

    try:
        leak_audit("the community outcome was a loss")
        checks.append(("leak audit fires", False))
    except SystemExit:
        checks.append(("leak audit fires", True))
    try:
        leak_audit(render_state(score_state([row()], "XX", {}), False))
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
    ap.add_argument("--all", action="store_true",
                    help="every state with a statewide legislative record")
    ap.add_argument("--data", default=FEED, help="feed CSV path")
    ap.add_argument("--out", help="write markdown here instead of stdout")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.state and not args.all:
        ap.print_help()
        return 0

    feed = load_feed(args.data)
    margins = load_vote_margins(VOTES)

    if args.all:
        states = sorted({(r.get("State") or "").strip().upper()
                         for r in feed if truthy(r.get("is_statewide"))})
    else:
        states = [args.state.strip().upper()]

    blocks = [render_state(score_state(feed, st, margins), bool(margins))
             for st in states]
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
