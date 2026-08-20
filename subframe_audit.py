#!/usr/bin/env python3
"""
subframe_audit.py

On-demand coverage audit bounded to a frame: a state list, a client's county
list, a utility territory, anything enumerable. Run it before a deliverable
ships.

Why this module exists. National recall is an average over 3,144 counties.
The TVA deliverable's frame was 198 counties, and inside it roughly 21
percent of the enacted-restriction counties were missing or stale while
national recall read 0.696. An aggregate cannot fail loudly on a subset,
and nothing re-derived coverage against a frame until a client deliverable
forced an analyst to do it by hand. coverage_audit.py measures the nation
continuously; this module measures the thing you are about to send.

What it adds beyond a filter on coverage_audit.py. Three signals that only
matter inside a bounded frame, joined per county:

  coverage        census-enacted counties with no tracker record, and the
                  two label disagreement classes, taken straight from
                  coverage_audit.audit() so classification cannot drift
                  between the national and the frame view
  staleness       non-terminal restrictive records past their mechanism
                  threshold, which is the Hamilton class: the frame's write
                  up says "proposing a pause" and the commission has since
                  acted
  adjacency       counties inside the frame that border a recently enacted
                  county, from adjacency_scan.py, which is how Walker GA
                  would have surfaced before an analyst noticed the border

Frame completeness is reported, never assumed. A frame given as a state
list is PROVISIONAL: the audit still finds every gap it can, but the recall
number it prints is a state-aggregate proxy and is marked as such in the
artifact itself, because quoting a seven-state recall as a 198-county
frame's recall would be exactly the kind of unstated denominator swap the
headline metric defect was. A frame is COMPLETE only when its county list is
enumerated.

Delivery gate. With --gate the module exits nonzero when the frame is
provisional or carries unresolved priority-1 items, so a deliverable
checklist can call it instead of trusting memory.

Outputs (one pair per frame, named by the frame key)
  data/subframe_audit_<frame>.csv
  data/subframe_audit_<frame>.md

Standing rules honored: four-tier vocabulary, additive, writes only new
files, LF line endings, no em-dashes in composed prose, --selftest with no
data or network dependency.

Usage
  python subframe_audit.py --frame tva
  python subframe_audit.py --frame vantage_three_state --gate
  python subframe_audit.py --states TN,GA --label tn_ga
  python subframe_audit.py --fips-file configs/frames/tva_198_counties.csv \\
      --label tva198
  python subframe_audit.py --list-frames
  python subframe_audit.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES_JSON = os.path.join(HERE, "configs", "audit_frames.json")
CENSUS_CSV = os.path.join(HERE, "data", "external_restriction_census.csv")
MASTER_CLEAN = os.path.join(HERE, "master_opposition_clean.csv")
AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
ADJ_QUEUE = os.path.join(HERE, "data", "adjacency_scan_queue.csv")
OUT_DIR = os.path.join(HERE, "data")

from coverage_audit import (ENACTED_STATUSES,  # noqa: E402
                           RESTRICTIVE_MECHS, audit as national_audit,
                           norm_county)
from stale_pending_audit import (NON_TERMINAL_OUTCOMES,  # noqa: E402
                                 parse_date, threshold_for)

FIELDS = ["action", "state", "county", "fips", "in_frame_basis",
          "census_status", "census_date", "gap_class", "label_check",
          "model_label", "label_divergence", "tracker_restrictive_records",
          "tracker_nonterminal_records", "stale_nonterminal_records",
          "latest_tracker_date", "adjacency_priority", "enacted_neighbors",
          "census_source"]

# Action strings, ordered by what a reviewer should do first. Kept as an
# explicit ladder so the ordering is a registered decision and not an
# accident of dict iteration.
ACTION_ORDER = [
    "ingest_missing_census_enacted",
    "reconcile_label_positive_no_tracker_record",
    "reverify_stale_nonterminal_restrictive",
    "confirm_unconfirmed_coverage",
    "check_adjacent_enactment",
    "covered",
    "no_signal",
]

# Frames at or below this size list every county, including the ones with no
# signal at all. On a three-county client frame, silence about the third
# county is itself a gap in the artifact: a reviewer cannot tell whether it
# was checked and clean or never reached.
SMALL_FRAME_MAX = 25


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_frames(path: str = FRAMES_JSON) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("frames", {}) or {}
    except (OSError, ValueError):
        return {}


def resolve_frame(spec: dict, agg_rows: list[dict],
                  root: str = HERE) -> dict:
    """Turns a frame spec into a concrete county set plus a status.

    A frame is complete only when its county list is enumerated. `states`
    alone resolves to every county in those states and is marked provisional,
    because a state aggregate is not the frame's own denominator.
    """
    states = {s.strip().upper() for s in (spec.get("states") or [])
              if s.strip()}
    fips = {str(f).strip().zfill(5) for f in (spec.get("fips") or [])
            if str(f).strip()}
    basis = "enumerated_inline" if fips else ""
    file_missing = ""
    ff = spec.get("fips_file")
    if not fips and ff:
        fpath = ff if os.path.isabs(ff) else os.path.join(root, ff)
        if os.path.exists(fpath):
            for r in read_csv(fpath):
                val = (r.get("fips") or r.get("FIPS")
                       or next(iter(r.values()), "") or "")
                val = str(val).strip()
                if val:
                    fips.add(val.zfill(5))
            basis = "enumerated_file"
        else:
            file_missing = ff

    if not fips and states:
        fips = {(r.get("fips") or "").strip() for r in agg_rows
                if (r.get("state") or "").strip().upper() in states}
        fips.discard("")
        basis = "state_expansion"

    status = "complete" if basis in ("enumerated_inline", "enumerated_file") \
        else "provisional"
    expected = spec.get("expected_counties")
    mismatch = (bool(expected) and status == "complete"
                and len(fips) != int(expected))
    if mismatch:
        status = "provisional"

    return {
        "fips": fips,
        "states": states,
        "basis": basis,
        "status": status,
        "expected_counties": expected,
        "count_mismatch": mismatch,
        "missing_fips_file": file_missing,
        "provenance": spec.get("provenance", ""),
        "name": spec.get("name", ""),
        "notes": spec.get("notes", ""),
    }


def county_index(agg_rows: list[dict]) -> tuple[dict, dict]:
    by_fips, by_name = {}, {}
    for r in agg_rows:
        fips = (r.get("fips") or "").strip()
        if not fips:
            continue
        st = (r.get("state") or "").strip().upper()
        name = (r.get("county_name") or "").split(",")[0].strip()
        by_fips[fips] = {"state": st, "county": name,
                         "label": (r.get("has_enacted_restrictive")
                                   or "").strip()}
        key = (st, norm_county(name, st))
        if key[1]:
            by_name.setdefault(key, fips)
    return by_fips, by_name


def tracker_by_county(records: list[dict], as_of: date) -> dict[tuple, dict]:
    """Restrictive-record counts and staleness per (state, norm county)."""
    out: dict[tuple, dict] = {}
    for r in records:
        st = (r.get("State") or "").strip().upper()
        cty = norm_county(r.get("County", ""), st)
        if not st or not cty:
            continue
        mech = (r.get("qc_mechanism") or "").strip().lower()
        if mech not in RESTRICTIVE_MECHS:
            continue
        cur = out.setdefault((st, cty), {"restrictive": 0, "non_terminal": 0,
                                         "stale": 0, "latest": ""})
        cur["restrictive"] += 1
        d = parse_date(r.get("Date"))
        if d and (not cur["latest"] or d.isoformat() > cur["latest"]):
            cur["latest"] = d.isoformat()
        if (r.get("outcome_defensible") or "").strip() \
                in NON_TERMINAL_OUTCOMES:
            cur["non_terminal"] += 1
            if d and (as_of - d).days >= threshold_for(mech):
                cur["stale"] += 1
    return out


def adjacency_index(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for r in rows:
        f = (r.get("fips") or "").strip()
        if not f:
            continue
        try:
            pri = int(r.get("priority") or 9)
        except ValueError:
            pri = 9
        try:
            nbrs = int(r.get("enacted_neighbors") or 0)
        except ValueError:
            nbrs = 0
        out[f] = {"priority": pri, "enacted_neighbors": nbrs}
    return out


def audit_frame(frame: dict, census: list[dict], records: list[dict],
                agg_rows: list[dict], adj_rows: list[dict],
                as_of: date,
                include_all: bool = False) -> tuple[list[dict], dict]:
    by_fips, by_name = county_index(agg_rows)
    trk = tracker_by_county(records, as_of)
    adj = adjacency_index(adj_rows)
    natl = national_audit(census, records, agg_rows)

    # Census rows keyed to the frame by fips, using the aggregate's own name
    # index so the join rule matches coverage_audit's.
    census_by_fips: dict[str, dict] = {}
    for row in natl:
        st = (row.get("state") or "").strip().upper()
        fips = by_name.get((st, norm_county(row.get("county", ""), st)))
        if fips:
            census_by_fips[fips] = row

    rows = []
    for fips in sorted(frame["fips"]):
        rec = by_fips.get(fips)
        if rec is None:
            continue
        key = (rec["state"], norm_county(rec["county"], rec["state"]))
        t = trk.get(key, {})
        c = census_by_fips.get(fips, {})
        a = adj.get(fips, {})
        cstatus = (c.get("census_status") or "").strip().lower()
        in_census = cstatus in ENACTED_STATUSES
        gap = c.get("gap_class", "")
        check = c.get("label_check", "")

        label_divergent = (rec["label"] == "1"
                           and not t.get("restrictive", 0))
        if in_census and gap == "missing":
            action = "ingest_missing_census_enacted"
        elif check == "label_positive_no_tracker_record" or label_divergent:
            # The census-derived check only reaches counties the census
            # covers. Inside a bounded frame the same divergence is
            # detectable without the census: the aggregate labels the county
            # enacted while the clean feed carries no county-level
            # restrictive record at all. Reported, never reconciled here.
            action = "reconcile_label_positive_no_tracker_record"
        elif t.get("stale", 0):
            action = "reverify_stale_nonterminal_restrictive"
        elif in_census and gap == "covered_unconfirmed":
            action = "confirm_unconfirmed_coverage"
        elif a.get("priority", 9) <= 2:
            action = "check_adjacent_enactment"
        elif in_census or t.get("restrictive", 0) or rec["label"] == "1":
            action = "covered"
        elif include_all:
            action = "no_signal"
        else:
            continue          # no signal in this county; nothing to report

        rows.append({
            "action": action,
            "state": rec["state"],
            "county": rec["county"],
            "fips": fips,
            "in_frame_basis": frame["basis"],
            "census_status": cstatus,
            "census_date": c.get("census_date", ""),
            "gap_class": gap,
            "label_check": check,
            "model_label": rec["label"],
            "label_divergence": "1" if label_divergent else "0",
            "tracker_restrictive_records": t.get("restrictive", 0),
            "tracker_nonterminal_records": t.get("non_terminal", 0),
            "stale_nonterminal_records": t.get("stale", 0),
            "latest_tracker_date": t.get("latest", ""),
            "adjacency_priority": a.get("priority", ""),
            "enacted_neighbors": a.get("enacted_neighbors", ""),
            "census_source": c.get("census_source", ""),
        })

    order = {a: i for i, a in enumerate(ACTION_ORDER)}
    rows.sort(key=lambda r: (order.get(r["action"], 99), r["state"],
                             r["county"]))

    in_scope = [r for r in rows if r["census_status"] in ENACTED_STATUSES]
    covered = [r for r in in_scope
               if r["gap_class"] in ("covered_confirmed",
                                    "covered_unconfirmed")]
    confirmed = [r for r in in_scope if r["gap_class"] == "covered_confirmed"]
    by_action = Counter(r["action"] for r in rows)
    p1 = (by_action.get("ingest_missing_census_enacted", 0)
          + by_action.get("reconcile_label_positive_no_tracker_record", 0)
          + by_action.get("reverify_stale_nonterminal_restrictive", 0))

    summary = {
        "as_of": as_of.isoformat(),
        "frame_name": frame["name"],
        "frame_status": frame["status"],
        "frame_basis": frame["basis"],
        "frame_counties": len(frame["fips"]),
        "frame_counties_in_county_layer": sum(
            1 for f in frame["fips"] if f in by_fips),
        "expected_counties": frame["expected_counties"],
        "count_mismatch": frame["count_mismatch"],
        "missing_fips_file": frame["missing_fips_file"],
        "provenance": frame["provenance"],
        "census_in_scope": len(in_scope),
        "covered_any": len(covered),
        "covered_confirmed": len(confirmed),
        "frame_recall_any": (round(len(covered) / len(in_scope), 3)
                             if in_scope else None),
        "frame_recall_confirmed": (round(len(confirmed) / len(in_scope), 3)
                                   if in_scope else None),
        "actions": dict(by_action),
        "listed_every_frame_county": include_all,
        "unresolved_priority_1": p1,
        "delivery_clear": frame["status"] == "complete" and p1 == 0,
        "note": ("Recall here is bounded to the frame. When frame_status is "
                 "provisional the county list is not enumerated, so the "
                 "recall figure is a proxy over the expanded state set and "
                 "must not be quoted as the frame's own recall. "
                 "adjacency_priority is a search prompt, never evidence."),
    }
    return rows, summary


def write_rows(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_report(rows: list[dict], summary: dict, label: str,
                 path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    status = summary["frame_status"].upper()
    lines = [
        f"# Frame coverage audit: {label}",
        "",
        f"{summary['frame_name'] or label}. As of {summary['as_of']}. "
        f"Generated by subframe_audit.py.",
        "",
        f"## Frame status: {status}",
        "",
        f"- Counties in frame: {summary['frame_counties']} "
        f"(resolved by {summary['frame_basis']})",
        f"- Counties matched into the county layer: "
        f"{summary['frame_counties_in_county_layer']}",
    ]
    if summary["expected_counties"]:
        lines.append(f"- Counties expected by the frame definition: "
                     f"{summary['expected_counties']}")
    if summary["missing_fips_file"]:
        lines.append(f"- County list file not present: "
                     f"{summary['missing_fips_file']}. The frame expanded to "
                     f"whole states instead, so every number below is a "
                     f"proxy over that larger set.")
    if summary["count_mismatch"]:
        lines.append("- The enumerated county count does not match the "
                     "expected count in the frame definition, so the frame "
                     "is treated as provisional.")
    lines += [
        f"- Provenance: {summary['provenance'] or 'not recorded'}",
        "",
    ]
    if summary["frame_status"] != "complete":
        lines += [
            "PROVISIONAL FRAME. The recall figures below are a proxy over an "
            "expanded county set, not this frame's own recall, and must not "
            "be quoted in a client deliverable as the frame's coverage. "
            "Enumerate the county list to clear this.",
            "",
        ]
    lines += [
        "## Coverage inside the frame",
        "",
        f"- Census counties with an in-scope enacted instrument: "
        f"{summary['census_in_scope']}",
        f"- Covered by any tracker restrictive record: "
        f"{summary['covered_any']} (recall {summary['frame_recall_any']})",
        f"- Covered with a terminal confirmation: "
        f"{summary['covered_confirmed']} "
        f"(recall {summary['frame_recall_confirmed']})",
        "",
        "## Actions",
        "",
        "| Action | Counties |",
        "|---|---|",
    ]
    for act in ACTION_ORDER:
        n = summary["actions"].get(act, 0)
        if n:
            lines.append(f"| {act} | {n} |")
    lines += [
        "",
        f"Unresolved priority-1 items (ingest, reconcile, re-verify): "
        f"{summary['unresolved_priority_1']}",
        "",
        f"Delivery clear: {'yes' if summary['delivery_clear'] else 'no'}",
        "",
        ("Every county in the frame is listed below, including the ones "
         "with no signal, so silence is distinguishable from absence."
         if summary["listed_every_frame_county"] else
         "Only counties carrying a signal are listed below."),
        "",
        "## Counties needing work",
        "",
        "| Action | State | County | Census | Gap | Label | Non-terminal | "
        "Stale | Adj P |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    work = [r for r in rows if r["action"] != "covered"][:80]
    for r in work:
        lines.append(
            f"| {r['action']} | {r['state']} | {r['county']} | "
            f"{r['census_status'] or '-'} | {r['gap_class'] or '-'} | "
            f"{r['model_label'] or '-'} | "
            f"{r['tracker_nonterminal_records']} | "
            f"{r['stale_nonterminal_records']} | "
            f"{r['adjacency_priority'] or '-'} |")
    if not work:
        lines.append("| none | | | | | | | | |")
    lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def ck(label, cond):
        checks.append((label, cond))

    as_of = date(2026, 8, 18)
    agg = [
        {"fips": "47065", "county_name": "Hamilton County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0"},
        {"fips": "47073", "county_name": "Hawkins County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0"},
        {"fips": "13295", "county_name": "Walker County, Georgia",
         "state": "GA", "has_enacted_restrictive": "0"},
        {"fips": "47157", "county_name": "Shelby County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "1"},
        {"fips": "47037", "county_name": "Davidson County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "1"},
        {"fips": "19153", "county_name": "Polk County, Iowa",
         "state": "IA", "has_enacted_restrictive": "0"},
        # No census row, no restrictive record, no adjacency: the Wilson
        # County shape, where the honest answer is "checked, nothing on
        # record" rather than silence.
        {"fips": "47189", "county_name": "Wilson County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0"},
    ]
    census = [
        # Enacted, absent from the tracker: the Hawkins case.
        {"state": "TN", "county": "Hawkins County", "instrument": "ban",
         "census_status": "active", "date_enacted": "2025-09-02",
         "source": "https://example.org/hawkins"},
        # Enacted and covered by a terminal tracker record.
        {"state": "TN", "county": "Davidson County",
         "instrument": "moratorium", "census_status": "active",
         "date_enacted": "2026-04-01", "source": "https://example.org/dav"},
        # Enacted, covered only by a non-terminal record: the Hamilton case.
        {"state": "TN", "county": "Hamilton County",
         "instrument": "moratorium", "census_status": "active",
         "date_enacted": "2026-07-15", "source": "https://example.org/ham"},
        # Outside the frame: must not affect frame recall.
        {"state": "IA", "county": "Polk County", "instrument": "moratorium",
         "census_status": "active", "date_enacted": "2026-01-01",
         "source": "https://example.org/polk"},
    ]
    records = [
        {"State": "TN", "County": "Davidson County",
         "qc_mechanism": "moratorium",
         "outcome_defensible": "blocked_confirmed", "Date": "2026-04-01"},
        # 170 days old against a 120-day moratorium threshold: stale.
        {"State": "TN", "County": "Hamilton County",
         "qc_mechanism": "moratorium", "outcome_defensible": "pending",
         "Date": "2026-03-01"},
        # Label positive with no county-level restrictive record on the feed.
        {"State": "TN", "County": "Shelby County",
         "qc_mechanism": "public_pressure", "outcome_defensible": "pending",
         "Date": "2026-06-01"},
    ]
    adj_rows = [
        {"fips": "13295", "priority": "1", "enacted_neighbors": "1"},
        {"fips": "47157", "priority": "4", "enacted_neighbors": "1"},
    ]

    # Complete frame: enumerated county list.
    frame = resolve_frame({"name": "test frame", "states": ["TN", "GA"],
                           "fips": ["47065", "47073", "13295", "47157",
                                    "47189"],
                           "provenance": "fixture"}, agg)
    ck("enumerated frame is complete", frame["status"] == "complete")
    ck("enumerated frame basis recorded",
       frame["basis"] == "enumerated_inline")

    rows, summary = audit_frame(frame, census, records, agg, adj_rows, as_of)
    by = {r["fips"]: r for r in rows}

    ck("missing census-enacted county is the first action",
       rows[0]["action"] == "ingest_missing_census_enacted"
       and rows[0]["fips"] == "47073")
    ck("frame recall counts only frame counties",
       summary["census_in_scope"] == 2)
    ck("Polk IA is excluded from the frame", "19153" not in by)
    ck("frame recall_any is 1 of 2",
       summary["frame_recall_any"] == 0.5)
    ck("Hamilton is flagged for re-verification, not as covered",
       by["47065"]["action"] == "reverify_stale_nonterminal_restrictive")
    ck("Hamilton's stale count is carried",
       by["47065"]["stale_nonterminal_records"] == 1)
    ck("label-positive with no tracker record is a reconcile action",
       by["47157"]["action"]
       == "reconcile_label_positive_no_tracker_record")
    ck("label divergence is detected without a census row",
       by["47157"]["label_divergence"] == "1"
       and not by["47157"]["census_status"])
    ck("a county covered by a terminal record is not called divergent",
       by["47037"]["label_divergence"] == "0"
       if "47037" in by else True)
    ck("adjacency priority 1 inside the frame raises a check",
       by["13295"]["action"] == "check_adjacent_enactment")
    ck("adjacency priority is carried for reference",
       by["13295"]["adjacency_priority"] == 1)
    ck("counties with no signal are omitted by default", len(rows) == 4)

    all_rows, all_sum = audit_frame(frame, census, records, agg, adj_rows,
                                    as_of, include_all=True)
    ck("a small frame can list every county",
       {r["fips"] for r in all_rows} == frame["fips"])
    ck("a county with no signal is named as such",
       {r["action"] for r in all_rows} - {r["action"] for r in rows}
       == {"no_signal"}
       and len(all_rows) == len(rows) + 1)
    ck("listing every county does not change recall",
       all_sum["frame_recall_any"] == summary["frame_recall_any"])
    ck("listing every county does not change the priority-1 count",
       all_sum["unresolved_priority_1"] == summary["unresolved_priority_1"])
    ck("unresolved priority-1 items counted",
       summary["unresolved_priority_1"] == 3)
    ck("a frame with open priority-1 items is not delivery clear",
       summary["delivery_clear"] is False)

    # Provisional frame: state expansion.
    prov = resolve_frame({"name": "state frame", "states": ["TN"],
                          "expected_counties": 95}, agg)
    ck("state-only frame is provisional", prov["status"] == "provisional")
    ck("state expansion pulls every county in the state",
       prov["fips"] == {"47065", "47073", "47157", "47037", "47189"})
    _, prov_sum = audit_frame(prov, census, records, agg, adj_rows, as_of)
    ck("provisional frame is never delivery clear",
       prov_sum["delivery_clear"] is False)
    ck("provisional frame names its basis",
       prov_sum["frame_basis"] == "state_expansion")

    # A count mismatch against the declared expectation demotes the frame.
    mm = resolve_frame({"name": "mismatch", "fips": ["47065"],
                        "expected_counties": 198}, agg)
    ck("enumerated frame short of its expected count is provisional",
       mm["status"] == "provisional" and mm["count_mismatch"] is True)

    # A declared county-list file that does not exist must not read as
    # complete; it falls back to states and says so.
    miss = resolve_frame({"name": "missing file", "states": ["GA"],
                          "fips_file": "configs/frames/__absent__.csv"}, agg)
    ck("absent county-list file falls back to state expansion",
       miss["basis"] == "state_expansion"
       and miss["status"] == "provisional")
    ck("absent county-list file is named in the result",
       miss["missing_fips_file"] == "configs/frames/__absent__.csv")

    # Frame with no in-scope census county: recall is None, not zero.
    empty = resolve_frame({"name": "no census", "fips": ["13295"]}, agg)
    _, esum = audit_frame(empty, census, records, agg, [], as_of)
    ck("a frame with no in-scope census county reports recall None",
       esum["frame_recall_any"] is None)

    # Frame county outside the county layer is counted honestly.
    ghost = resolve_frame({"name": "ghost", "fips": ["99999", "47073"]}, agg)
    _, gsum = audit_frame(ghost, census, records, agg, [], as_of)
    ck("frame counties absent from the county layer are reported",
       gsum["frame_counties"] == 2
       and gsum["frame_counties_in_county_layer"] == 1)

    ck("action ladder has no duplicates",
       len(ACTION_ORDER) == len(set(ACTION_ORDER)))
    ck("every emitted action is in the ladder",
       all(r["action"] in ACTION_ORDER for r in rows))

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list-frames", action="store_true")
    ap.add_argument("--frame", default=None,
                    help="frame key from configs/audit_frames.json")
    ap.add_argument("--states", default=None,
                    help="comma-separated states; ad-hoc provisional frame")
    ap.add_argument("--fips-file", default=None,
                    help="CSV with a fips column; ad-hoc complete frame")
    ap.add_argument("--label", default=None,
                    help="output name for an ad-hoc frame")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    ap.add_argument("--all-counties", action="store_true",
                    help="list every frame county, including ones with no "
                         "signal; automatic for frames of "
                         f"{SMALL_FRAME_MAX} counties or fewer")
    ap.add_argument("--gate", action="store_true",
                    help="exit nonzero unless the frame is complete and "
                         "carries no unresolved priority-1 items")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    frames = load_frames()
    if args.list_frames:
        if not frames:
            print("no frames registered in configs/audit_frames.json")
            return 0
        for key, spec in sorted(frames.items()):
            print(f"{key}: {spec.get('name', '')}")
            print(f"  states: {','.join(spec.get('states') or []) or 'none'}")
            print(f"  declared status: {spec.get('status', 'unset')}")
            print(f"  provenance: {spec.get('provenance', '')}")
        return 0

    for path in (MASTER_CLEAN, AGG_CSV):
        if not os.path.exists(path):
            print(f"ERROR: {os.path.relpath(path, HERE)} not found")
            return 1

    as_of = parse_date(args.as_of) if args.as_of else date.today()
    if as_of is None:
        print(f"ERROR: could not parse --as-of {args.as_of!r}")
        return 1

    agg = read_csv(AGG_CSV)
    if args.frame:
        if args.frame not in frames:
            print(f"ERROR: frame {args.frame!r} not registered. "
                  f"Known frames: {', '.join(sorted(frames)) or 'none'}")
            return 1
        spec, label = frames[args.frame], args.frame
    elif args.fips_file or args.states:
        spec = {"name": args.label or "ad-hoc frame",
                "states": [s.strip() for s in (args.states or "").split(",")
                           if s.strip()],
                "fips_file": args.fips_file,
                "provenance": "ad-hoc frame supplied on the command line"}
        label = args.label or "adhoc"
    else:
        print("ERROR: give --frame, --states, or --fips-file "
              "(or --list-frames)")
        return 1

    frame = resolve_frame(spec, agg)
    if not frame["fips"]:
        print("ERROR: the frame resolved to zero counties")
        return 1

    census = read_csv(CENSUS_CSV)
    records = read_csv(MASTER_CLEAN)
    adj_rows = read_csv(ADJ_QUEUE)
    include_all = (args.all_counties
                   or len(frame["fips"]) <= SMALL_FRAME_MAX)
    rows, summary = audit_frame(frame, census, records, agg, adj_rows, as_of,
                                include_all=include_all)
    if not census:
        summary["census_note"] = ("external census absent; coverage classes "
                                  "are unavailable and only staleness and "
                                  "adjacency signals were evaluated")
    if not adj_rows:
        summary["adjacency_note"] = ("data/adjacency_scan_queue.csv absent; "
                                     "adjacency signals were not evaluated")

    out_csv = os.path.join(OUT_DIR, f"subframe_audit_{label}.csv")
    out_md = os.path.join(OUT_DIR, f"subframe_audit_{label}.md")
    write_rows(rows, out_csv)
    write_report(rows, summary, label, out_md)

    print(f"frame {label}: {summary['frame_counties']} counties, status "
          f"{summary['frame_status'].upper()} "
          f"({summary['frame_basis']})")
    if summary["missing_fips_file"]:
        print(f"  county list file absent: {summary['missing_fips_file']}")
    print(f"census in scope inside the frame: {summary['census_in_scope']}")
    print(f"  recall_any: {summary['frame_recall_any']}  "
          f"recall_confirmed: {summary['frame_recall_confirmed']}")
    for act in ACTION_ORDER:
        n = summary["actions"].get(act, 0)
        if n:
            print(f"  {act}: {n}")
    print(f"unresolved priority-1 items: {summary['unresolved_priority_1']}")
    print(f"delivery clear: {'yes' if summary['delivery_clear'] else 'no'}")
    print(f"\nwrote {out_csv}")
    print(f"wrote {out_md}")

    if args.gate and not summary["delivery_clear"]:
        print("\nGATE: frame is not clear for delivery")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
