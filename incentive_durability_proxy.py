#!/usr/bin/env python3
"""Interim incentive-durability proxy score, per state.

Computes a documented starting number for Incentive Durability Risk from
evidence the pipeline already holds, until data/incentive_agreement_registry.csv
has enough rows to fit a real model. This is a proxy, not a model output:
it reads the public legislative record only. Property tax abatements, grants,
utility incentives, local programs and state-local alignment are not tracked
here and must be researched before any score is treated as final.

Score anchors (1 to 5, higher is more durable):
  5  no incentive-adjacent statewide record on file
  4  incentive-adjacent records exist, all reached terminal dispositions
     without enactment
  3  live incentive-adjacent activity without repeal or sunset language
  2  live proposal touching the incentive stack with repeal or sunset
     language on the record
  1  never assigned automatically. A 1 requires repeal or severe impairment
     to be likely, which is a judgment about a bill's prospects that this
     platform does not make.

Stage evidence, in order of preference:
  1. data/bill_sync_matches.csv, when present: verified worst-case stage per
     record from the Open States action history (bill_sync.py output).
  2. The feed's status_clean and action_complete fields otherwise.

Two additional durability inputs are reported descriptively but never folded
into the number, because no confirmed adjustment rule exists for them yet:
granting authority tier and program age, read from the optional analyst file
data/incentive_programs.csv (columns: state, program_name, granting_authority,
enacted_year, source_url; granting_authority one of state_legislature,
governor, local_edo).

Usage:
  python3 incentive_durability_proxy.py --state VA
  python3 incentive_durability_proxy.py --all --out data/incentive_durability_proxy.md
  python3 incentive_durability_proxy.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

from bill_sync import STAGES, opp_event_id

ROOT = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(ROOT, "master_opposition_clean.csv")
MATCHES = os.path.join(ROOT, "data", "bill_sync_matches.csv")
PROGRAMS = os.path.join(ROOT, "data", "incentive_programs.csv")

STAGE_TERMINAL = {s: t for s, _, t in STAGES}

REPEAL_LANGUAGE_RE = re.compile(
    r"\b(repeal|sunset|phase[- ]?out|roll(?:ing)? back|clawback)\b",
    re.IGNORECASE)

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

AUTHORITY_TIERS = ("state_legislature", "governor", "local_edo")


def truthy(v: str) -> bool:
    return (v or "").strip().lower() == "true"


def load_feed(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_matches(path: str) -> dict[str, dict]:
    """Map opp_id -> verified match row, when bill_sync output exists."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if r.get("lookup_status") == "matched" and r.get("stage")]
    return {r["opp_id"]: r for r in rows}


def load_programs(path: str) -> dict[str, list[dict]]:
    """Map state -> analyst-recorded incentive programs, when the file exists."""
    if not os.path.exists(path):
        return {}
    out: dict[str, list[dict]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault((r.get("state") or "").strip().upper(), []).append(r)
    return out


def incentive_rows(feed: list[dict], state: str) -> list[dict]:
    return [r for r in feed
            if (r.get("State") or "").strip().upper() == state
            and truthy(r.get("is_tax_incentive"))
            and truthy(r.get("is_statewide"))]


def row_is_live(row: dict, matches: dict[str, dict]) -> tuple[bool, str]:
    """(live, evidence). Verified bill_sync stage wins over feed status."""
    m = matches.get(opp_event_id(row))
    if m:
        stage = m["stage"]
        live = not STAGE_TERMINAL.get(stage, False)
        return live, f"verified stage: {stage} ({m.get('stage_date') or 'no date'})"
    live = ((row.get("status_clean") or "").strip().lower() == "active"
            or (row.get("action_complete") or "").strip() == "False")
    return live, f"feed status: {row.get('status_clean') or 'unknown'}"


def has_repeal_language(row: dict) -> bool:
    text = f"{row.get('Summary') or ''} {row.get('Objective') or ''}"
    return bool(REPEAL_LANGUAGE_RE.search(text))


def score_state(feed: list[dict], state: str,
                matches: dict[str, dict]) -> dict:
    rows = incentive_rows(feed, state)
    detail = []
    live_n = 0
    live_repeal = False
    for r in rows:
        live, ev = row_is_live(r, matches)
        repeal = has_repeal_language(r)
        if live:
            live_n += 1
            live_repeal = live_repeal or repeal
        detail.append({
            "date": r.get("Date") or "undated",
            "live": live,
            "evidence": ev,
            "repeal_language": repeal,
            "source_url": r.get("Source URL") or "",
        })
    if not rows:
        score, anchor = 5, "No incentive-adjacent statewide record on file."
    elif live_n == 0:
        score, anchor = 4, ("Incentive-adjacent measures on the record all "
                            "reached terminal dispositions without enactment.")
    elif live_repeal:
        score, anchor = 2, ("Live proposal touching the incentive stack with "
                            "repeal or sunset language on the record.")
    else:
        score, anchor = 3, ("Live incentive-adjacent activity without repeal "
                            "or sunset language: monitoring required.")
    return {"state": state, "score": score, "anchor": anchor,
            "records_n": len(rows), "live_n": live_n, "detail": detail}


def render_state(result: dict, programs: dict[str, list[dict]],
                 matches_present: bool) -> str:
    L = [f"### Incentive durability proxy: {result['state']}", ""]
    L.append(f"Proxy score {result['score']}. {result['anchor']} A 1 is never "
             f"assigned automatically; see module docstring.")
    L.append("")
    L.append(f"- Incentive-adjacent statewide records: {result['records_n']} "
             f"({result['live_n']} live)")
    src = ("verified bill_sync stages where matched, feed status otherwise"
           if matches_present else
           "feed status only; run bill_sync.py resolve for verified stages")
    L.append(f"- Stage evidence: {src}")
    for d in result["detail"]:
        state_txt = "live" if d["live"] else "terminal"
        rep = ", repeal or sunset language present" if d["repeal_language"] else ""
        L.append(f"  - {d['date']}: {state_txt} ({d['evidence']}){rep}")
    progs = programs.get(result["state"], [])
    if progs:
        L.append("- Analyst-recorded incentive programs (descriptive context, "
                 "not folded into the score):")
        for p in progs:
            auth = (p.get("granting_authority") or "").strip()
            auth_txt = auth if auth in AUTHORITY_TIERS else f"{auth} (unrecognized tier)"
            L.append(f"  - {p.get('program_name') or 'unnamed program'}: "
                     f"granted by {auth_txt}, enacted "
                     f"{p.get('enacted_year') or 'year not recorded'}")
    else:
        L.append("- Granting authority and program age: not on file. Record "
                 "them in data/incentive_programs.csv for descriptive context.")
    L.append("- This proxy reads the public legislative record only. Property "
             "tax abatements, grants, utility incentives, local programs and "
             "state-local alignment must be researched before this score is "
             "treated as final.")
    L.append("")
    return "\n".join(L)


def leak_audit(text: str) -> None:
    hits = LEAK_RE.findall(text)
    if hits:
        raise SystemExit(f"leak audit failed: scorekeeping vocabulary in "
                         f"output: {sorted(set(h.lower() for h in hits))}")


def selftest() -> int:
    def row(state="XX", status="active", complete="False", summary="",
            **kw):
        base = {"State": state, "is_tax_incentive": "True",
                "is_statewide": "True", "status_clean": status,
                "action_complete": complete, "Summary": summary,
                "Objective": "", "Date": "2026-01-01", "Source URL": "",
                "Incident": "t", "City": ""}
        base.update(kw)
        return base

    checks = []
    # 5: nothing on record
    checks.append(("no records -> 5",
                   score_state([], "XX", {})["score"] == 5))
    # 4: all terminal
    r4 = score_state([row(status="failed", complete="True")], "XX", {})
    checks.append(("all terminal -> 4", r4["score"] == 4))
    # 3: live, no repeal language
    r3 = score_state([row(summary="study committee on incentives")], "XX", {})
    checks.append(("live without repeal language -> 3", r3["score"] == 3))
    # 2: live with repeal language
    r2 = score_state([row(summary="would sunset the exemption")], "XX", {})
    checks.append(("live with repeal language -> 2", r2["score"] == 2))
    # matches join overrides feed status: feed says active, verified terminal
    live_row = row(summary="would repeal the exemption")
    oid = opp_event_id(live_row)
    verified = {oid: {"opp_id": oid, "stage": "Died in committee",
                      "stage_date": "2026-03-01", "lookup_status": "matched"}}
    rj = score_state([live_row], "XX", verified)
    checks.append(("verified terminal stage overrides live feed status -> 4",
                   rj["score"] == 4))
    # leak audit fires on scorekeeping vocabulary
    try:
        leak_audit("the community outcome was a loss")
        checks.append(("leak audit fires", False))
    except SystemExit:
        checks.append(("leak audit fires", True))
    # leak audit passes on rendered output
    try:
        leak_audit(render_state(r2, {}, False))
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
                    help="every state with incentive-adjacent records")
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
    matches = load_matches(MATCHES)
    programs = load_programs(PROGRAMS)

    if args.all:
        states = sorted({(r.get("State") or "").strip().upper()
                         for r in feed
                         if truthy(r.get("is_tax_incentive"))
                         and truthy(r.get("is_statewide"))})
    else:
        states = [args.state.strip().upper()]

    blocks = [render_state(score_state(feed, st, matches), programs,
                           bool(matches))
              for st in states]
    text = "\n".join(blocks)
    leak_audit(text)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
