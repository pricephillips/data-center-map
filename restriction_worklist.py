#!/usr/bin/env python3
"""
restriction_worklist.py

Turns the county coverage gap report into an actionable ingest and
reconciliation worklist, nationally or for one state.

Why this module exists. `coverage_audit.py` measures how far short the
county restriction layer falls against the external census. Measurement
is not collection. Every census county the tracker lacks is both a
missing record and, in most cases, a FALSE NEGATIVE in
`has_enacted_restrictive`, the target of the county policy model. This
module converts that measurement into rows a human can work, ranked by
how much each resolution moves the label, and it preserves work already
done across regenerations so CI cannot overwrite a human's research.

Registration 2026-08-05, initial. Ordering criterion is pre-registered
here before any row is worked:

  priority 1  reconcile_label     tracker already holds a restrictive
                                  record for the county but the label
                                  rule does not count it. Cheapest
                                  possible label repair: no collection,
                                  only reconciliation.
  priority 2  ingest_missing      no tracker record and the model
              (label-moving)      currently trains the county as a
                                  negative. Collection flips a label.
  priority 3  ingest_missing      no tracker record but the label is
              (label-agreeing)    already positive by another path.
                                  Worth collecting for the record and
                                  because the divergence itself needs
                                  explaining, but it does not move the
                                  training frame.
  priority 4  resolve_county_name census county does not join the
                                  national county frame at all. A census
                                  name defect, not a coverage result.

Within a priority, ordering is by census status currency (active,
extended, replaced, expired), then by state recall ascending so the
worst-covered states surface first, then state and county name.

What this module does NOT do. It does not write to
`master_opposition.csv`, does not infer an outcome, and does not treat
the external census as a source of record. The census is a pointer. Each
row still requires a primary source URL before it can be ingested, which
is what `ready_to_ingest` tracks. A row with no primary source is
research that is not finished.

Outputs
  data/national_restriction_worklist.csv    one row per open task
  data/restriction_ingest_template.csv      master_opposition-shaped
                                            skeleton rows for the ingest
                                            tasks, primary source blank
  data/restriction_worklist.md              per-state summary

  with --state XX, the worklist is written to
  data/{state_name}_coverage_worklist.csv instead, e.g.
  data/indiana_coverage_worklist.csv

The state filename carries its provenance on purpose. The 2026-07-30
Indiana worklist held hand-researched corrections that no census can
reproduce (a wrong enacting body, a date conflict between a plan
commission and a board of commissioners, a missing expired first
moratorium, a premature record). A census-derived file must never
occupy that filename and quietly replace research with a subset.

Standing rules honored: four-tier vocabulary only, additive, writes only
new files, no em-dashes, no scorekeeping vocabulary, LF line endings,
stdlib only, --selftest with no data or network dependency.

Usage
  python restriction_worklist.py
  python restriction_worklist.py --state IN
  python restriction_worklist.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict

try:
    from coverage_audit import norm_county
except Exception:  # pragma: no cover - selftest runs standalone
    import re

    def norm_county(name: str, state: str = "") -> str:
        n = str(name or "").lower().strip()
        n = re.sub(r"\([^)]*\)", " ", n)
        n = re.sub(r"\b(county|parish|borough)\b", "", n)
        n = re.sub(r"[^a-z ]", "", n)
        return re.sub(r"\s+", " ", n).strip()

HERE = os.path.dirname(os.path.abspath(__file__))
GAP_CSV = os.path.join(HERE, "data", "coverage_gap_report.csv")
GAP_JSON = os.path.join(HERE, "data", "coverage_gap_summary.json")
AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
OUT_CSV = os.path.join(HERE, "data", "national_restriction_worklist.csv")
OUT_TEMPLATE = os.path.join(HERE, "data", "restriction_ingest_template.csv")
OUT_MD = os.path.join(HERE, "data", "restriction_worklist.md")
MASTER_RAW = os.path.join(HERE, "master_opposition.csv")

STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut",
    "DE": "delaware", "DC": "district_of_columbia", "FL": "florida",
    "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky",
    "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new_hampshire",
    "NJ": "new_jersey", "NM": "new_mexico", "NY": "new_york",
    "NC": "north_carolina", "ND": "north_dakota", "OH": "ohio",
    "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode_island", "SC": "south_carolina", "SD": "south_dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west_virginia",
    "WI": "wisconsin", "WY": "wyoming",
}

# Census status ranked by currency. An active instrument is worth more
# to a client conversation than one that lapsed two years ago, so it is
# collected first inside a priority band.
STATUS_RANK = {"active": 0, "extended": 1, "replaced": 2, "expired": 3}

# Census status to the raw tracker Status vocabulary that
# county_aggregator.ENACTED_STATUSES recognizes. This mapping is the
# reason the template exists: ingesting a census "replaced" row with
# Status "replaced" would produce a record the label rule does not count,
# which would manufacture a new false negative through our own ingest.
# "replaced" maps to "passed" because the instrument did pass; it was
# later superseded, and the label asks whether the county ever enacted.
STATUS_MAP = {
    "active": "active",
    "extended": "extended",
    "expired": "expired",
    "replaced": "passed",
}

# Columns the reviewer fills. Preserved across regeneration.
RESOLUTION_FIELDS = [
    "resolution_status", "resolved_mechanism", "resolved_outcome",
    "resolved_enacting_body", "resolved_date", "source_url", "notes",
]

FIELDS = [
    "priority", "task_class", "state", "county", "frame_county_name",
    "fips", "census_instrument", "census_status", "census_episodes",
    "census_statuses", "census_date", "census_source",
    "tracker_mechanism", "tracker_outcome", "tracker_date",
    "model_label", "label_check", "gap_class", "state_recall_any",
    "action_required", "ready_to_ingest",
] + RESOLUTION_FIELDS

ACTIONS = {
    "reconcile_label": (
        "Tracker holds a restrictive record for this county but the "
        "county label rule does not count it. Compare the tracker row's "
        "Opposition Type and Status against county_aggregator "
        "RESTRICTIVE_TYPES and ENACTED_STATUSES, confirm the instrument "
        "against a primary source, and correct the record or register "
        "the vocabulary gap."
    ),
    "ingest_missing": (
        "No tracker record for this county. Find the primary source "
        "(county ordinance, resolution, meeting minutes, or local "
        "coverage naming the enacting body and date), then add a row to "
        "master_opposition.csv using the matching row in "
        "data/restriction_ingest_template.csv."
    ),
    "verify_tracker_record": (
        "The county aggregate labels this county enacted while the clean "
        "feed carries no county-level restrictive record. Identify which "
        "record produced the label and whether it is a county-level "
        "instrument, then either add the missing county record or "
        "correct the one driving the label."
    ),
    "resolve_county_name": (
        "This census county does not match the national county frame. "
        "Correct the county name in data/external_restriction_census.csv "
        "or add the government to CONSOLIDATED_ALIASES in "
        "coverage_audit.py."
    ),
}


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------
# core
# --------------------------------------------------------------------------

def classify(row: dict) -> tuple[str, int]:
    """Returns (task_class, priority) for one gap-report row."""
    gap = (row.get("gap_class") or "").strip()
    check = (row.get("label_check") or "").strip()

    if check == "out_of_scope":
        return ("", 0)
    if check == "unjoined_frame":
        return ("resolve_county_name", 4)
    if gap == "missing":
        if check == "label_false_negative":
            return ("ingest_missing", 2)
        if check == "label_positive_no_tracker_record":
            return ("verify_tracker_record", 3)
        return ("ingest_missing", 3)
    if check == "label_false_negative":
        return ("reconcile_label", 1)
    return ("", 0)


def build(gap_rows: list[dict], agg_rows: list[dict],
          summary: dict) -> list[dict]:
    frame = {}
    for r in agg_rows:
        st = (r.get("state") or "").strip()
        cty = norm_county((r.get("county_name") or "").split(",")[0], st)
        if st and cty:
            frame[(st, cty)] = {
                "fips": (r.get("fips") or "").strip(),
                "county_name": (r.get("county_name") or "").strip(),
            }

    states = (summary or {}).get("states", {})

    out = []
    for r in gap_rows:
        task, prio = classify(r)
        if not task:
            continue
        st = (r.get("state") or "").strip()
        cty = (r.get("county") or "").strip()
        hit = frame.get((st, norm_county(cty, st)), {})
        recall = (states.get(st) or {}).get("recall_any")
        out.append({
            "priority": prio,
            "task_class": task,
            "state": st,
            "county": cty,
            "frame_county_name": hit.get("county_name", ""),
            "fips": hit.get("fips", ""),
            "census_instrument": r.get("census_instrument", ""),
            "census_status": r.get("census_status", ""),
            "census_episodes": r.get("census_episodes", ""),
            "census_statuses": r.get("census_statuses", ""),
            "census_date": r.get("census_date", ""),
            "census_source": r.get("census_source", ""),
            "tracker_mechanism": r.get("tracker_mechanism", ""),
            "tracker_outcome": r.get("tracker_outcome", ""),
            "tracker_date": r.get("tracker_date", ""),
            "model_label": r.get("model_label", ""),
            "label_check": r.get("label_check", ""),
            "gap_class": r.get("gap_class", ""),
            "state_recall_any": "" if recall is None else recall,
            "action_required": ACTIONS.get(task, ""),
            "ready_to_ingest": "0",
            "resolution_status": "",
            "resolved_mechanism": "",
            "resolved_outcome": "",
            "resolved_enacting_body": "",
            "resolved_date": "",
            "source_url": "",
            "notes": "",
        })

    def sort_key(row: dict):
        rec = row["state_recall_any"]
        rec = 1.0 if rec == "" else float(rec)
        return (row["priority"],
                STATUS_RANK.get(row["census_status"], 9),
                rec, row["state"], row["county"])

    out.sort(key=sort_key)
    return out


def merge_prior(rows: list[dict], prior: list[dict]) -> list[dict]:
    """Carries forward reviewer-entered values across a regeneration.

    CI regenerates this file on every push. Without this merge, a run
    would silently discard a reviewer's sourcing work, which is a far
    worse failure than a stale row.
    """
    idx = {}
    for p in prior:
        key = (p.get("state", "").strip(),
               norm_county(p.get("county", ""), p.get("state", "")))
        idx[key] = p
    for r in rows:
        key = (r["state"], norm_county(r["county"], r["state"]))
        p = idx.get(key)
        if not p:
            continue
        for f in RESOLUTION_FIELDS:
            if str(p.get(f, "")).strip():
                r[f] = p[f]
        r["ready_to_ingest"] = "1" if str(r.get("source_url", "")).strip() \
            else "0"
    return rows


def template_rows(rows: list[dict], header: list[str]) -> list[dict]:
    """master_opposition-shaped skeletons for the collection tasks.

    Primary source is deliberately left blank: the census citation is a
    pointer to a dataset, not to the county's own record, and no external
    claim ships without a sourced URL. Community Outcome is left blank
    because the source coding is the reviewer's call, not this module's.
    """
    out = []
    for r in rows:
        if r["task_class"] not in ("ingest_missing", "verify_tracker_record"):
            continue
        county = r["frame_county_name"].split(",")[0] or r["county"]
        status = STATUS_MAP.get(r["census_status"], "")
        note = ""
        if r["census_status"] not in STATUS_MAP:
            note = ("census status has no tracker equivalent; code Status "
                    "by hand against the primary source")
        elif STATUS_MAP[r["census_status"]] != r["census_status"]:
            note = (f"census status {r['census_status']} mapped to Status "
                    f"{status} so the county label rule counts it")
        blank = {k: "" for k in header}
        blank.update({
            "Incident": f"{county} {r['census_instrument']}".strip(),
            "City": "",
            "Date": r["census_date"],
            "Entity": "",
            "Location": f"{county}, {r['state']}",
            "Opposition Type": r["census_instrument"],
            "Severity": "",
            "Source URL": "",
            "State": r["state"],
            "County": county,
            "Scope": "county",
            "Authority Level": "",
            "Status": status,
            "Community Outcome": "",
            "Summary": "",
            "Sources": r["census_source"],
            "data_source": "coverage_audit_worklist",
        })
        blank["_worklist_priority"] = str(r["priority"])
        blank["_worklist_note"] = note
        blank["_fips"] = r["fips"]
        out.append(blank)
    return out


def summarize_md(rows: list[dict], summary: dict) -> str:
    natl = (summary or {}).get("national", {})
    by_state = defaultdict(Counter)
    for r in rows:
        by_state[r["state"]][r["task_class"]] += 1
        by_state[r["state"]]["total"] += 1

    lines = []
    lines.append("# County restriction worklist")
    lines.append("")
    lines.append("Generated by `restriction_worklist.py` from "
                 "`data/coverage_gap_report.csv`. The external census is a "
                 "lower bound and a pointer, not a source of record: every "
                 "row still needs a primary source URL before ingest.")
    lines.append("")
    lines.append(f"- Census counties in scope: "
                 f"{natl.get('census_in_scope', '')}")
    lines.append(f"- National recall (any tracker restrictive record): "
                 f"{natl.get('recall_any', '')}")
    lines.append(f"- Counties the model trains as negatives despite a "
                 f"census enactment: {natl.get('label_false_negatives', '')}")
    lines.append(f"- Census counties unjoined to the national frame: "
                 f"{natl.get('unjoined_frame', '')}")
    lines.append(f"- Counties labeled enacted with no county-level tracker "
                 f"record: "
                 f"{natl.get('label_positive_no_tracker_record', '')}")
    lines.append(f"- Open worklist rows: {len(rows)}")
    lines.append("")

    lines.append("## Tasks by class")
    lines.append("")
    lines.append("| Priority | Task | Rows |")
    lines.append("|---|---|---|")
    cnt = Counter((r["priority"], r["task_class"]) for r in rows)
    for (p, t), n in sorted(cnt.items()):
        lines.append(f"| {p} | {t} | {n} |")
    lines.append("")

    lines.append("## Tasks by state")
    lines.append("")
    lines.append("| State | Rows | Recall | Worst class |")
    lines.append("|---|---|---|---|")
    states = (summary or {}).get("states", {})
    for st in sorted(by_state, key=lambda s: -by_state[s]["total"]):
        c = by_state[st]
        rec = (states.get(st) or {}).get("recall_any")
        worst = min((r["priority"] for r in rows if r["state"] == st),
                    default="")
        lines.append(f"| {st} | {c['total']} | "
                     f"{'' if rec is None else rec} | priority {worst} |")
    lines.append("")

    top = [r for r in rows if r["priority"] <= 2]
    lines.append("## Priority 1 and 2 detail")
    lines.append("")
    lines.append("| P | State | County | Instrument | Census status | "
                 "Census date | Tracker record |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in top:
        trk = r["tracker_mechanism"] or "none"
        lines.append(f"| {r['priority']} | {r['state']} | {r['county']} | "
                     f"{r['census_instrument']} | {r['census_status']} | "
                     f"{r['census_date']} | {trk} |")
    lines.append("")
    return "\n".join(lines)


def write_rows(rows: list[dict], path: str, fields: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    gap = [
        # missing + label 0: collection flips a training label
        {"state": "KS", "county": "Ford County", "census_instrument":
         "moratorium", "census_status": "active", "census_episodes": "1",
         "census_statuses": "active", "census_date": "2026-01-05",
         "census_source": "census:ks-ford", "tracker_mechanism": "",
         "tracker_outcome": "", "tracker_date": "", "model_label": "0",
         "join_status": "joined", "gap_class": "missing",
         "label_check": "label_false_negative"},
        # covered but label 0: cheapest repair, must outrank collection
        {"state": "PA", "county": "Montour County", "census_instrument":
         "moratorium", "census_status": "active", "census_episodes": "1",
         "census_statuses": "active", "census_date": "2025-11-18",
         "census_source": "census:pa-montour",
         "tracker_mechanism": "conditional_zoning",
         "tracker_outcome": "restricted_conditional",
         "tracker_date": "2026-02-10", "model_label": "0",
         "join_status": "joined", "gap_class": "covered_unconfirmed",
         "label_check": "label_false_negative"},
        # missing but label already 1: rule divergence, not a label move
        {"state": "CO", "county": "Larimer County", "census_instrument":
         "moratorium", "census_status": "extended", "census_episodes": "2",
         "census_statuses": "active;extended", "census_date": "2026-01-27",
         "census_source": "census:co-larimer", "tracker_mechanism": "",
         "tracker_outcome": "", "tracker_date": "", "model_label": "1",
         "join_status": "joined", "gap_class": "missing",
         "label_check": "label_positive_no_tracker_record"},
        # census name defect
        {"state": "IA", "county": "Nowhere County", "census_instrument":
         "ban", "census_status": "active", "census_episodes": "1",
         "census_statuses": "active", "census_date": "2026-02-02",
         "census_source": "census:ia-nowhere", "tracker_mechanism": "",
         "tracker_outcome": "", "tracker_date": "", "model_label": "",
         "join_status": "unjoined_frame", "gap_class": "missing",
         "label_check": "unjoined_frame"},
        # covered and agreeing: not a task
        {"state": "IN", "county": "Fulton County", "census_instrument":
         "moratorium", "census_status": "active", "census_episodes": "1",
         "census_statuses": "active", "census_date": "2026-03-03",
         "census_source": "census:in-fulton",
         "tracker_mechanism": "moratorium",
         "tracker_outcome": "blocked_confirmed",
         "tracker_date": "2026-03-03", "model_label": "1",
         "join_status": "joined", "gap_class": "covered_confirmed",
         "label_check": "agree"},
        # out of scope: not a task
        {"state": "OH", "county": "Stark County", "census_instrument":
         "moratorium", "census_status": "pending", "census_episodes": "1",
         "census_statuses": "pending", "census_date": "",
         "census_source": "census:oh-stark", "tracker_mechanism": "",
         "tracker_outcome": "", "tracker_date": "", "model_label": "0",
         "join_status": "joined",
         "gap_class": "census_pending_out_of_scope",
         "label_check": "out_of_scope"},
        # expired status must sort below active inside its band
        {"state": "GA", "county": "Brooks County", "census_instrument":
         "moratorium", "census_status": "expired", "census_episodes": "1",
         "census_statuses": "expired", "census_date": "2025-04-01",
         "census_source": "census:ga-brooks", "tracker_mechanism": "",
         "tracker_outcome": "", "tracker_date": "", "model_label": "0",
         "join_status": "joined", "gap_class": "missing",
         "label_check": "label_false_negative"},
        # replaced status: exercises the STATUS_MAP guard
        {"state": "ND", "county": "Williams County", "census_instrument":
         "moratorium", "census_status": "replaced", "census_episodes": "1",
         "census_statuses": "replaced", "census_date": "2023-06-20",
         "census_source": "census:nd-williams", "tracker_mechanism": "",
         "tracker_outcome": "", "tracker_date": "", "model_label": "0",
         "join_status": "joined", "gap_class": "missing",
         "label_check": "label_false_negative"},
    ]
    agg = [
        {"state": "KS", "fips": "20057", "county_name": "Ford County, Kansas",
         "has_enacted_restrictive": "0"},
        {"state": "PA", "fips": "42093",
         "county_name": "Montour County, Pennsylvania",
         "has_enacted_restrictive": "0"},
        {"state": "CO", "fips": "08069",
         "county_name": "Larimer County, Colorado",
         "has_enacted_restrictive": "1"},
        {"state": "GA", "fips": "13027",
         "county_name": "Brooks County, Georgia",
         "has_enacted_restrictive": "0"},
        {"state": "ND", "fips": "38105",
         "county_name": "Williams County, North Dakota",
         "has_enacted_restrictive": "0"},
        {"state": "IN", "fips": "18049",
         "county_name": "Fulton County, Indiana",
         "has_enacted_restrictive": "1"},
    ]
    summary = {"states": {"KS": {"recall_any": 0.375},
                          "PA": {"recall_any": 1.0},
                          "CO": {"recall_any": 0.5},
                          "GA": {"recall_any": 0.765},
                          "ND": {"recall_any": 0.4}},
               "national": {"census_in_scope": 7, "recall_any": 0.4,
                            "label_false_negatives": 4,
                            "unjoined_frame": 1,
                            "label_positive_no_tracker_record": 1}}

    rows = build(gap, agg, summary)
    by = {(r["state"], r["county"]): r for r in rows}
    checks = []

    def ck(label, cond):
        checks.append((label, cond))

    ck("agreeing covered county produces no task",
       ("IN", "Fulton County") not in by)
    ck("out-of-scope census row produces no task",
       ("OH", "Stark County") not in by)
    ck("open tasks equal the six actionable rows", len(rows) == 6)

    ck("reconcile outranks collection",
       by[("PA", "Montour County")]["priority"] == 1)
    ck("reconcile is classed as reconciliation, not ingest",
       by[("PA", "Montour County")]["task_class"] == "reconcile_label")
    ck("label-moving collection is priority 2",
       by[("KS", "Ford County")]["priority"] == 2)
    ck("label-agreeing gap is priority 3",
       by[("CO", "Larimer County")]["priority"] == 3)
    ck("directional disagreement is a verification task",
       by[("CO", "Larimer County")]["task_class"] == "verify_tracker_record")
    ck("unjoined census row is a name task at priority 4",
       by[("IA", "Nowhere County")]["priority"] == 4
       and by[("IA", "Nowhere County")]["task_class"]
       == "resolve_county_name")

    ck("priority 1 sorts first", rows[0]["priority"] == 1)
    ks = [i for i, r in enumerate(rows) if r["state"] == "KS"][0]
    ga = [i for i, r in enumerate(rows) if r["state"] == "GA"][0]
    ck("active census status sorts above expired inside a band", ks < ga)

    ck("fips joined from the national frame",
       by[("KS", "Ford County")]["fips"] == "20057")
    ck("frame county name carried",
       by[("KS", "Ford County")]["frame_county_name"]
       == "Ford County, Kansas")
    ck("state recall carried for context",
       by[("KS", "Ford County")]["state_recall_any"] == 0.375)
    ck("nothing is ready to ingest without a source",
       all(r["ready_to_ingest"] == "0" for r in rows))
    ck("resolution fields start empty",
       all(r[f] == "" for r in rows for f in RESOLUTION_FIELDS))

    # merge preserves reviewer work and flips readiness
    prior = [{"state": "KS", "county": "Ford County",
              "resolution_status": "done",
              "resolved_mechanism": "moratorium",
              "resolved_outcome": "blocked_confirmed",
              "resolved_enacting_body": "Board of County Commissioners",
              "resolved_date": "2026-01-05",
              "source_url": "https://example.org/ford-ordinance",
              "notes": "twelve month pause"}]
    merged = merge_prior(build(gap, agg, summary), prior)
    m = {(r["state"], r["county"]): r for r in merged}
    ck("merge carries the reviewer's source",
       m[("KS", "Ford County")]["source_url"]
       == "https://example.org/ford-ordinance")
    ck("merge carries the reviewer's outcome in four-tier vocabulary",
       m[("KS", "Ford County")]["resolved_outcome"] == "blocked_confirmed")
    ck("sourced row becomes ready to ingest",
       m[("KS", "Ford County")]["ready_to_ingest"] == "1")
    ck("unsourced row stays not ready",
       m[("GA", "Brooks County")]["ready_to_ingest"] == "0")
    ck("merge does not invent rows", len(merged) == len(rows))

    # template shape
    header = ["Incident", "City", "Date", "Entity", "Location",
              "Opposition Type", "Severity", "Source URL", "State",
              "County", "Scope", "Issue Category", "Objective",
              "Authority Level", "Status", "Community Outcome", "Summary",
              "Sources", "data_source"]
    tpl = template_rows(rows, header)
    tby = {(t["State"], t["County"]): t for t in tpl}
    ck("template covers collection tasks only",
       len(tpl) == 4
       and ("PA", "Montour County") not in tby
       and ("IA", "Nowhere County") not in tby)
    ck("template uses the frame county name",
       ("KS", "Ford County") in tby)
    ck("template maps active status through unchanged",
       tby[("KS", "Ford County")]["Status"] == "active")
    ck("template maps replaced to an enacted-counting status",
       tby[("ND", "Williams County")]["Status"] == "passed")
    ck("status remap is flagged for the reviewer",
       "mapped" in tby[("ND", "Williams County")]["_worklist_note"])
    ck("template leaves the primary source blank",
       tby[("KS", "Ford County")]["Source URL"] == "")
    ck("template carries the census citation in Sources",
       tby[("KS", "Ford County")]["Sources"] == "census:ks-ford")
    ck("template leaves source outcome coding to the reviewer",
       tby[("KS", "Ford County")]["Community Outcome"] == "")
    ck("template scopes rows to the county",
       tby[("KS", "Ford County")]["Scope"] == "county")
    ck("template carries fips", tby[("KS", "Ford County")]["_fips"] == "20057")

    md = summarize_md(rows, summary)
    ck("summary reports the open row count", "Open worklist rows: 6" in md)
    ck("summary lists priority 1 and 2 detail",
       "Priority 1 and 2 detail" in md and "Montour" in md)
    ck("no em-dash in generated summary", "\u2014" not in md)

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--state", default=None,
                    help="two-letter state code; writes a state worklist")
    ap.add_argument("--out", default=None, help="override the output path")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    for path in (GAP_CSV, AGG_CSV):
        if not os.path.exists(path):
            print(f"ERROR: {os.path.relpath(path, HERE)} not found; run "
                  f"coverage_audit.py first")
            return 1

    gap = read_csv(GAP_CSV)
    agg = read_csv(AGG_CSV)
    summary = {}
    if os.path.exists(GAP_JSON):
        with open(GAP_JSON, encoding="utf-8") as fh:
            summary = json.load(fh)

    rows = build(gap, agg, summary)

    state = (args.state or "").strip().upper()
    if state:
        rows = [r for r in rows if r["state"] == state]
        name = STATE_NAMES.get(state, state.lower())
        out_csv = args.out or os.path.join(
            HERE, "data", f"{name}_coverage_worklist.csv")
    else:
        out_csv = args.out or OUT_CSV

    if os.path.exists(out_csv):
        rows = merge_prior(rows, read_csv(out_csv))

    write_rows(rows, out_csv, FIELDS)

    # Ingest template and summary are national artifacts; a state run
    # leaves them alone rather than shrinking them to one state.
    if not state:
        header = []
        if os.path.exists(MASTER_RAW):
            with open(MASTER_RAW, newline="", encoding="utf-8-sig") as fh:
                header = next(csv.reader(fh))
        if header:
            tpl = template_rows(rows, header)
            write_rows(tpl, OUT_TEMPLATE,
                       header + ["_worklist_priority", "_worklist_note",
                                 "_fips"])
            print(f"wrote {OUT_TEMPLATE} ({len(tpl)} skeleton rows)")
        with open(OUT_MD, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(summarize_md(rows, summary))
        print(f"wrote {OUT_MD}")

    cnt = Counter((r["priority"], r["task_class"]) for r in rows)
    print(f"\nopen worklist rows: {len(rows)}")
    for (p, t), n in sorted(cnt.items()):
        print(f"  priority {p}  {t:24} {n}")
    ready = sum(1 for r in rows if r["ready_to_ingest"] == "1")
    print(f"  sourced and ready to ingest: {ready}")
    print(f"\nwrote {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
