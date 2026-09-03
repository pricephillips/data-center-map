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
    """Map (state, identifier) -> {result, yes, no, vote_date, events}.

    A bill carries several roll-call events in one session (committee
    reports, amendments, then final passage), and they disagree: CO SB 24
    and DE HB 445 each record both a 'pass' and a 'fail' event. Summing
    every event's yeas and nays would invent a tally no chamber ever took,
    so pass/fail is read from the legislature's own reported `result` on the
    most recent event, and the reported margin is that same event's tally.

    Within an event the tally counts distinct legislators, not rows. The feed
    carries one row per (legislator x linked opposition incident), so a bill
    linked to three incidents repeats every legislator's vote three times.
    Counting rows made Maryland HB 1532's final passage read 315-81 in a
    141-seat chamber; it is 104-27 of 141 once each member is counted once.
    Identity is legislator_id, falling back to the name when the id is blank,
    and a row with neither is counted rather than dropped.
    """
    if not os.path.exists(path):
        return {}

    # (key, vote_date, chamber, motion_text) identifies one roll call. Chamber
    # belongs in the key: Oklahoma HB 2992 had a "Fourth Reading" in both
    # chambers on 2026-05-05, and without it the two merged into one event of
    # 147 legislators in a 101-seat House.
    events: dict[tuple, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = f"{(r.get('state') or '').strip().upper()}:{(r.get('identifier') or '').strip()}"
            ev_key = (key, (r.get("vote_date") or "").strip(),
                      (r.get("chamber") or "").strip().lower(),
                      (r.get("motion_text") or "").strip())
            ev = events.setdefault(ev_key, {
                "key": key,
                "vote_date": (r.get("vote_date") or "").strip(),
                "result": (r.get("result") or "").strip().lower(),
                "chamber": (r.get("chamber") or "").strip().lower(),
                "yes": 0, "no": 0, "seen": set(), "dupes": 0,
            })
            opt = (r.get("option") or "").strip().lower()
            if opt not in ("yes", "no"):
                continue
            who = ((r.get("legislator_id") or "").strip()
                   or (r.get("legislator_name") or "").strip())
            if who:
                if who in ev["seen"]:
                    ev["dupes"] += 1
                    continue
                ev["seen"].add(who)
            ev[opt] += 1

    by_bill: dict[str, list[dict]] = defaultdict(list)
    for ev in events.values():
        by_bill[ev["key"]].append(ev)

    tallies: dict[str, dict] = {}
    for key, evs in by_bill.items():
        # Latest recorded chamber action. Undated events sort first so a
        # dated event always wins.
        latest = sorted(evs, key=lambda e: (e["vote_date"], e["yes"] + e["no"]))[-1]
        tallies[key] = {
            "result": latest["result"],
            "yes": latest["yes"],
            "no": latest["no"],
            "vote_date": latest["vote_date"],
            "chamber": latest["chamber"],
            "events": len(evs),
            "duplicate_rows": latest["dupes"],
        }
    return tallies


def alignment_rows(feed: list[dict], state: str) -> list[dict]:
    return [r for r in feed
            if (r.get("State") or "").strip().upper() == state
            and truthy(r.get("is_statewide"))
            and (r.get("qc_leg_stance") or "").strip().lower() in
                ("supportive", "restrictive", "unclear")]


def bill_mentioned_in(identifier: str, descriptor: str) -> bool:
    """True when a bill identifier is named in a feed descriptor.

    Matched on word boundaries, not as a substring: a descriptor naming
    'HB 1030' must NOT match bill 'HB 1'. Internal spacing is flexible so
    'HB1030' and 'HB 1030' both match.

    The trailing guard rejects year-prefixed numbering. Colorado writes its
    bills 'SB26-102' (2026 session, bill 102), and a bare word boundary
    matches at the hyphen, so bill 'SB 26' matched the descriptor
    'HB26-1030 + SB26-102 both died in committee' and reported that bill as
    having passed 64-0 -- the opposite of what the descriptor says. A
    descriptor written that way now matches nothing rather than the wrong
    bill, which is the right trade for something published as evidence.
    """
    m = re.match(r"^\s*([A-Za-z]+)\s*0*(\d+)\s*$", identifier or "")
    if not m:
        return False
    prefix, number = m.group(1), m.group(2)
    pat = re.compile(rf"\b{re.escape(prefix)}\s*0*{re.escape(number)}\b(?!\s*-\s*\d)",
                     re.IGNORECASE)
    return bool(pat.search(descriptor or ""))


def row_verified_outcome(row: dict, margins: dict[str, dict]) -> tuple[str, str]:
    """(outcome, evidence). outcome is 'passed', 'failed', or '' (no verified
    vote). A verified vote overrides the feed's qc_leg_stance for direction
    of THIS record's actual chamber outcome.

    The clean feed carries no bill-level key, so a record is tied to its
    roll calls by the bill identifiers named in its project_descriptor. One
    descriptor can cover several bills ('SB 326, HB 233, HB 445 -- data
    center energy/ratepayer bills'), so every named bill's tallies are
    summed.
    """
    state = (row.get("State") or "").strip().upper()
    descriptor = (row.get("project_descriptor") or "").strip()
    if not state or not descriptor:
        return "", ""

    matched = []
    for key, m in margins.items():
        key_state, _, identifier = key.partition(":")
        if key_state != state:
            continue
        if not bill_mentioned_in(identifier, descriptor):
            continue
        if m.get("result") not in ("pass", "fail"):
            continue
        matched.append((identifier, m))

    if not matched:
        return "", ""

    results = {m["result"] for _, m in matched}
    if "pass" in results:
        # A descriptor covering several bills counts as advancing when any
        # one of them cleared its most recent recorded chamber action.
        outcome = "passed"
    elif results == {"fail"}:
        outcome = "failed"
    else:
        return "", ""

    parts = []
    for identifier, m in sorted(matched):
        parts.append(f"{identifier} {m['result']}ed "
                     f"{m['yes']} yes / {m['no']} no"
                     + (f" on {m['vote_date']}" if m["vote_date"] else ""))
    ev = "verified roll call, latest recorded action: " + "; ".join(parts)
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

    margins_pass = {"XX:HB 1": {"result": "pass", "yes": 80, "no": 5,
                                "vote_date": "2026-03-01", "events": 1}}
    checks.append(("verified restrictive bill passing -> 2",
                   score_state([row(stance="restrictive", ident="HB 1"),
                               row(stance="supportive", ident="HB 2")],
                              "XX", margins_pass)["score"] == 2))

    margins_fail = {"XX:HB 1": {"result": "fail", "yes": 5, "no": 80,
                                "vote_date": "2026-03-01", "events": 1}}
    checks.append(("verified restrictive bill failing -> 4",
                   score_state([row(stance="restrictive", ident="HB 1"),
                               row(stance="supportive", ident="HB 2")],
                              "XX", margins_fail)["score"] == 4))

    # The descriptor names the bill in prose rather than equalling it, which
    # is how the real feed reads.
    margins_prose = {"XX:SB 484": {"result": "pass", "yes": 70, "no": 3,
                                   "vote_date": "2026-03-01", "events": 1}}
    checks.append(("bill named inside a prose descriptor still joins",
                   score_state([row(stance="restrictive",
                                    ident="SB 484 data center water protections")],
                               "XX", margins_prose)["score"] == 2))

    checks.append(("HB 1 does not match a descriptor naming HB 1030",
                   not bill_mentioned_in("HB 1", "HB 1030 tax measure")))
    checks.append(("HB 1030 matches its own descriptor",
                   bill_mentioned_in("HB 1030", "HB 1030 tax measure")))
    checks.append(("spacing variant matches",
                   bill_mentioned_in("HB 1030", "statewide HB1030")))
    checks.append(("unrelated bill does not match",
                   not bill_mentioned_in("SB 22", "HB 1030 tax measure")))

    # One descriptor covering several bills sums every named bill's tallies.
    margins_multi = {"XX:SB 326": {"result": "fail", "yes": 1, "no": 40,
                                   "vote_date": "2026-02-01", "events": 2},
                     "XX:HB 233": {"result": "fail", "yes": 2, "no": 30,
                                   "vote_date": "2026-02-02", "events": 1}}
    outcome, ev = row_verified_outcome(
        {"State": "XX",
         "project_descriptor": "SB 326, HB 233 -- ratepayer bills"},
        margins_multi)
    checks.append(("multi-bill descriptor, all failing -> failed",
                   outcome == "failed" and "SB 326 failed" in ev
                   and "HB 233 failed" in ev))

    # One bill clearing its latest action means the package advanced.
    margins_mixed = dict(margins_multi)
    margins_mixed["XX:HB 233"] = {"result": "pass", "yes": 40, "no": 2,
                                  "vote_date": "2026-02-02", "events": 1}
    checks.append(("multi-bill descriptor, any passing -> passed",
                   row_verified_outcome(
                       {"State": "XX",
                        "project_descriptor": "SB 326, HB 233 -- ratepayer bills"},
                       margins_mixed)[0] == "passed"))

    # Latest recorded action decides, not a sum across the session.
    import io
    csv_text = (
        "state,identifier,chamber,vote_date,result,motion_text,option\r\n"
        "XX,HB 7,house,2026-01-10,pass,Committee report,yes\r\n"
        "XX,HB 7,house,2026-01-10,pass,Committee report,yes\r\n"
        "XX,HB 7,house,2026-03-20,fail,Third reading,no\r\n")
    tmp = "/tmp/_votes_selftest.csv"
    open(tmp, "w", newline="").write(csv_text)
    loaded = load_vote_margins(tmp)
    checks.append(("latest action decides, not a session sum",
                   loaded["XX:HB 7"]["result"] == "fail"
                   and loaded["XX:HB 7"]["events"] == 2))
    os.remove(tmp)

    # A tally counts legislators, not rows. The feed carries one row per
    # (legislator x linked opposition incident), so a bill linked to three
    # incidents repeats every member three times. Counting rows made Maryland
    # HB 1532 read 315-81 in a 141-seat chamber.
    csv_text = (
        "state,identifier,chamber,vote_date,result,motion_text,legislator_id,"
        "legislator_name,option\r\n"
        "XX,HB 8,house,2026-02-01,pass,Third reading,L1,Ann,yes\r\n"
        "XX,HB 8,house,2026-02-01,pass,Third reading,L1,Ann,yes\r\n"
        "XX,HB 8,house,2026-02-01,pass,Third reading,L1,Ann,yes\r\n"
        "XX,HB 8,house,2026-02-01,pass,Third reading,L2,Bob,no\r\n"
        "XX,HB 8,house,2026-02-01,pass,Third reading,L2,Bob,no\r\n"
        "XX,HB 9,house,2026-02-01,pass,Third reading,,Cy,yes\r\n"
        "XX,HB 9,house,2026-02-01,pass,Third reading,,Cy,yes\r\n")
    open(tmp, "w", newline="").write(csv_text)
    loaded = load_vote_margins(tmp)
    checks.append(("a legislator is counted once per roll call",
                   loaded["XX:HB 8"]["yes"] == 1 and loaded["XX:HB 8"]["no"] == 1
                   and loaded["XX:HB 8"]["duplicate_rows"] == 3))
    checks.append(("identity falls back to the name when the id is blank",
                   loaded["XX:HB 9"]["yes"] == 1))
    os.remove(tmp)

    # Chamber belongs in the event key. Oklahoma HB 2992 had a "Fourth
    # Reading" in both chambers on the same day; without chamber the two
    # merged into one event of 147 legislators in a 101-seat House.
    csv_text = (
        "state,identifier,chamber,vote_date,result,motion_text,legislator_id,option\r\n"
        "XX,HB 10,lower,2026-05-05,pass,Fourth Reading,L1,yes\r\n"
        "XX,HB 10,lower,2026-05-05,pass,Fourth Reading,L2,yes\r\n"
        "XX,HB 10,upper,2026-05-05,pass,Fourth Reading,S1,no\r\n")
    open(tmp, "w", newline="").write(csv_text)
    loaded = load_vote_margins(tmp)
    checks.append(("the two chambers are separate roll calls, not one",
                   loaded["XX:HB 10"]["events"] == 2
                   and loaded["XX:HB 10"]["yes"] + loaded["XX:HB 10"]["no"] <= 2))
    os.remove(tmp)

    checks.append(("year-prefixed numbering is not a match",
                   not bill_mentioned_in("SB 26",
                       "HB26-1030 + SB26-102 both died in committee")))
    checks.append(("a plain reference still matches",
                   bill_mentioned_in("SB 326", "SB 326, HB 233 -- ratepayer bills")))
    checks.append(("a trailing dash before prose still matches",
                   bill_mentioned_in("HB 445", "HB 445 -- data center energy")))
    checks.append(("no vote data leaves outcome unverified",
                   row_verified_outcome(
                       {"State": "XX", "project_descriptor": "HB 9"},
                       {})[0] == ""))

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
