#!/usr/bin/env python3
"""
coverage_audit.py

Measures the tracker's recall of county-level restrictive actions against
an external census, state by state, and quantifies the label-noise this
implies for the county enacted-restriction model.

Why this module exists. The Indiana verification found the tracker held 9
restricted counties while Indiana University's Environmental Resilience
Institute counted roughly 30, including one of only two enacted county
BANS in the state (Cass) that the tracker missed entirely. The tracker
had no way to know it was short, because nothing measured recall. Missing
restricted counties are not just missing rows: they are FALSE NEGATIVES in
`has_enacted_restrictive`, the target variable of the county policy model.
Undercounted positives depress the base rate, bias the calibration layer,
and contaminate the negative class that the model trains against.

External census. `data/external_restriction_census.csv`, seeded from the
Moratorium Nation dataset (mjbommar/moratorium-data-2026, CC-BY-4.0,
222 moratoria coded from ~4,400 primary documents) filtered to
data-center-sector county-level rows, plus manually sourced rows for
censuses that publish counts without machine-readable lists (e.g. IU ERI
for Indiana). The census is a LOWER BOUND on true restriction activity,
which is exactly what a recall check needs: every census county absent
from the tracker is a confirmed gap, while the reverse is not evidence of
census error.

Registration 2026-08-05. Three defects found while building the national
ingest worklist off the first run, all of which understate the gap:

  (a) COUNTY NAME JOIN. `norm_county()` did not strip parentheticals or
      governing-body suffixes, so seven census rows never matched the
      national frame: two Kentucky rows carrying "Fiscal Court", two
      North Carolina rows carrying an explanatory parenthetical, two
      North Dakota "Oliver County (Phase N)" rows, and Georgia's
      consolidated Athens-Clarke government. A row that fails to join
      reads back an empty `model_label`, and the false-negative test was
      `label == "0"`, so an unjoined row could never be counted as label
      noise. The join failure silenced the very signal the module exists
      to raise. Parentheticals and body suffixes are now stripped, a
      small explicit alias table handles consolidated city-county
      governments, and a `join_status` column makes any residual failure
      visible instead of silent.

  (b) UNIT OF ANALYSIS. The audit emitted one row per census episode, so
      North Dakota's Oliver County counted three times (a rescinded
      phase, an expired phase, and an active phase) and inflated both the
      national denominator and North Dakota's missing count. Recall is a
      property of counties, not of instruments. Episodes now collapse to
      one row per county, with `census_episodes` and `census_statuses`
      preserving the history.

  (c) DIRECTIONAL DISAGREEMENT WAS INVISIBLE. Four counties carried
      `has_enacted_restrictive == 1` while this module saw no tracker
      restrictive record at all. That is not a coverage gap, it is a
      divergence between two rules reading the same tracker:
      `county_aggregator.py` labels off raw `Opposition Type` plus
      `Status`, this module reads `qc_mechanism` plus
      `outcome_defensible` on the clean feed. Both views are reported
      side by side and the disagreement is named in `label_check`
      (`label_positive_no_tracker_record`) rather than reconciled here.
      Unifying the two rules is a separate decision with a live effect on
      the label, and it does not belong in a measurement module.

Outputs
  data/coverage_gap_report.csv     one row per census COUNTY, matched or
                                   missing, with tracker cross-reference
  data/coverage_gap_summary.json   per-state recall and label-noise stats

Standing rules honored: four-tier vocabulary only, additive, writes only
new files, no em-dashes, leak audit clean, --selftest with no data or
network dependency.

Usage
  python coverage_audit.py
  python coverage_audit.py --state IN
  python coverage_audit.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS_CSV = os.path.join(HERE, "data", "external_restriction_census.csv")
MASTER_CLEAN = os.path.join(HERE, "master_opposition_clean.csv")
AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
OUT_CSV = os.path.join(HERE, "data", "coverage_gap_report.csv")
OUT_JSON = os.path.join(HERE, "data", "coverage_gap_summary.json")

# Tracker mechanisms that constitute a county restrictive action for
# recall purposes. Mirrors the RESTRICTIVE_TYPES notion in
# county_aggregator.py but works on the clean feed's qc_mechanism.
RESTRICTIVE_MECHS = {"moratorium", "ban", "conditional_zoning"}

# Census statuses that count as ever-enacted. `pending` census rows are
# excluded from recall (the tracker is not wrong to lack them);
# `rescinded` likewise.
ENACTED_STATUSES = {"active", "extended", "replaced", "expired"}


# Governing bodies appear in census county fields because the source
# documents name the enacting body ("Meade County Fiscal Court"). The body
# is not part of the county name and must not survive into the join key.
BODY_RE = re.compile(
    r"\b(fiscal court|quorum court|board of (county )?(commissioners|"
    r"supervisors)|county (commission|council|court)|area plan commission|"
    r"plan commission|board of zoning appeals|city council)\b",
    re.IGNORECASE)

# Parentheticals in the census carry annotations, not name parts:
# "(Phase 2)", "(cryptocurrency mining; data-center-adjacent)".
PAREN_RE = re.compile(r"\([^)]*\)")


def display_county(name: str) -> str:
    """Human-readable county name with census annotations removed.

    Keeps the original casing and the County/Parish suffix, so the
    worklist reads as a place name rather than a join key.
    """
    n = PAREN_RE.sub(" ", str(name or ""))
    n = BODY_RE.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip(" ,;-")
    return n

# Consolidated city-county governments publish under the joined name while
# the national frame carries the county name. Explicit and small on
# purpose: a general hyphen rule would break Miami-Dade, Matanuska-Susitna
# and the Alaska census areas.
CONSOLIDATED_ALIASES = {
    ("GA", "athensclarke"): "clarke",
    ("GA", "augustarichmond"): "richmond",
    ("GA", "columbusmuscogee"): "muscogee",
    ("GA", "maconbibb"): "bibb",
    ("IN", "indianapolismarion"): "marion",
    ("KY", "louisvillejefferson"): "jefferson",
    ("KY", "lexingtonfayette"): "fayette",
    ("TN", "nashvilledavidson"): "davidson",
    ("KS", "kansascitywyandotte"): "wyandotte",
    ("MT", "buttesilverbow"): "silver bow",
    ("MT", "anacondadeerlodge"): "deer lodge",
}


def norm_county(name: str, state: str = "") -> str:
    n = str(name or "").lower().strip()
    n = PAREN_RE.sub(" ", n)
    n = BODY_RE.sub(" ", n)
    n = re.sub(r"\b(county|parish|borough)\b", "", n)
    n = re.sub(r"[^a-z ]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    alias_key = (str(state or "").strip().upper(), n.replace(" ", ""))
    if alias_key in CONSOLIDATED_ALIASES:
        return CONSOLIDATED_ALIASES[alias_key]
    return n


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------
# core
# --------------------------------------------------------------------------

def tracker_counties(records: list[dict]) -> dict[tuple, dict]:
    """Maps (state, norm_county) -> best tracker evidence at county level."""
    out: dict[tuple, dict] = {}
    for r in records:
        st = (r.get("State") or "").strip()
        cty = norm_county(r.get("County", ""), st)
        if not st or not cty:
            continue
        mech = (r.get("qc_mechanism") or "").strip()
        if mech not in RESTRICTIVE_MECHS:
            continue
        key = (st, cty)
        cur = out.get(key)
        rank = {"blocked_confirmed": 3, "restricted_conditional": 2,
                "pending": 1}.get(r.get("outcome_defensible", ""), 0)
        if cur is None or rank > cur["rank"]:
            out[key] = {"rank": rank, "mechanism": mech,
                        "outcome": r.get("outcome_defensible", ""),
                        "date": r.get("Date", "")}
    return out


def model_labels(agg_rows: list[dict]) -> dict[tuple, str]:
    """Maps (state, norm_county) -> has_enacted_restrictive from the county
    aggregate, i.e. the model's actual training label."""
    out = {}
    for r in agg_rows:
        st = (r.get("state") or "").strip()
        cty = norm_county((r.get("county_name") or "").split(",")[0], st)
        if st and cty:
            out[(st, cty)] = r.get("has_enacted_restrictive", "")
    return out


def collapse_census(census: list[dict]) -> list[dict]:
    """Collapses census episodes to one record per county.

    Recall is a property of counties. Oliver County ND carries three
    moratorium phases in the census; counting it three times inflated both
    the national denominator and North Dakota's missing count. The episode
    history is preserved in `census_episodes` and `census_statuses`.
    """
    by_county: dict[tuple, dict] = {}
    order: list[tuple] = []
    for c in census:
        st = (c.get("state") or "").strip()
        raw = (c.get("county") or "").strip()
        key = (st, norm_county(raw, st))
        if not key[0] or not key[1]:
            continue
        status = (c.get("census_status") or "").strip().lower()
        date = (c.get("date_enacted") or "").strip()
        src = (c.get("source") or "").strip()
        inst = (c.get("instrument") or "").strip()
        cur = by_county.get(key)
        if cur is None:
            order.append(key)
            by_county[key] = {
                "state": st,
                # Annotations are stripped for display; the shortest
                # remaining form wins across episodes.
                "county": display_county(raw),
                "instrument": inst,
                "statuses": [status],
                "dates": [d for d in [date] if d],
                "sources": [s for s in [src] if s],
            }
            continue
        disp = display_county(raw)
        if disp and len(disp) < len(cur["county"]):
            cur["county"] = disp
        cur["statuses"].append(status)
        if date:
            cur["dates"].append(date)
        if src and src not in cur["sources"]:
            cur["sources"].append(src)
        # A ban outranks a moratorium for display.
        if inst == "ban":
            cur["instrument"] = "ban"

    out = []
    for key in order:
        v = by_county[key]
        in_scope = [s for s in v["statuses"] if s in ENACTED_STATUSES]
        # Report the strongest in-scope status; fall back to the first
        # recorded status when nothing is in scope.
        rank = {"active": 4, "extended": 3, "replaced": 2, "expired": 1}
        status = (max(in_scope, key=lambda s: rank.get(s, 0))
                  if in_scope else v["statuses"][0])
        out.append({
            "state": v["state"],
            "county": v["county"],
            "instrument": v["instrument"],
            "census_status": status,
            "date_enacted": min(v["dates"]) if v["dates"] else "",
            "source": " | ".join(v["sources"]),
            "census_episodes": len(v["statuses"]),
            "census_statuses": ";".join(v["statuses"]),
        })
    return out


def audit(census: list[dict], records: list[dict],
          agg_rows: list[dict]) -> list[dict]:
    trk = tracker_counties(records)
    lbl = model_labels(agg_rows)

    rows = []
    for c in collapse_census(census):
        st = (c.get("state") or "").strip()
        cty_raw = (c.get("county") or "").strip()
        key = (st, norm_county(cty_raw, st))
        status = (c.get("census_status") or "").strip().lower()
        in_scope = status in ENACTED_STATUSES

        hit = trk.get(key)
        joined = key in lbl
        label = lbl.get(key, "")

        if not in_scope:
            gap = "census_pending_out_of_scope"
        elif hit and hit["outcome"] == "blocked_confirmed":
            gap = "covered_confirmed"
        elif hit:
            gap = "covered_unconfirmed"
        else:
            gap = "missing"

        # label-noise call: census says enacted, model label says 0
        label_false_negative = (in_scope and label == "0")

        # Named label states. `unjoined_frame` used to read as an empty
        # label and therefore never registered as noise, which is the
        # defect that hid seven counties.
        if not in_scope:
            check = "out_of_scope"
        elif not joined:
            check = "unjoined_frame"
        elif label == "0":
            check = "label_false_negative"
        elif label == "1" and gap == "missing":
            # The aggregate labels this county enacted while this module
            # sees no county-level restrictive record on the clean feed.
            # Two rules reading the same tracker, reported not reconciled.
            check = "label_positive_no_tracker_record"
        else:
            check = "agree"

        rows.append({
            "state": st,
            "county": cty_raw,
            "census_instrument": c.get("instrument", ""),
            "census_status": status,
            "census_episodes": c.get("census_episodes", 1),
            "census_statuses": c.get("census_statuses", status),
            "census_date": c.get("date_enacted", ""),
            "census_source": c.get("source", ""),
            "tracker_mechanism": hit["mechanism"] if hit else "",
            "tracker_outcome": hit["outcome"] if hit else "",
            "tracker_date": hit["date"] if hit else "",
            "model_label": label,
            "join_status": "joined" if joined else "unjoined_frame",
            "gap_class": gap,
            "label_check": check,
            "label_false_negative": "1" if label_false_negative else "0",
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    per = defaultdict(lambda: Counter())
    for r in rows:
        per[r["state"]][r["gap_class"]] += 1
        per[r["state"]]["check:" + r.get("label_check", "")] += 1
        if r["label_false_negative"] == "1":
            per[r["state"]]["label_false_negative"] += 1

    states = {}
    for st, c in sorted(per.items()):
        scope = (c["covered_confirmed"] + c["covered_unconfirmed"]
                 + c["missing"])
        states[st] = {
            "census_in_scope": scope,
            "covered_confirmed": c["covered_confirmed"],
            "covered_unconfirmed": c["covered_unconfirmed"],
            "missing": c["missing"],
            "recall_confirmed": (round(c["covered_confirmed"] / scope, 3)
                                 if scope else None),
            "recall_any": (round((c["covered_confirmed"]
                                  + c["covered_unconfirmed"]) / scope, 3)
                           if scope else None),
            "label_false_negatives": c["label_false_negative"],
            "unjoined_frame": c["check:unjoined_frame"],
            "label_positive_no_tracker_record":
                c["check:label_positive_no_tracker_record"],
        }
    tot_scope = sum(s["census_in_scope"] for s in states.values())
    tot_any = sum(s["covered_confirmed"] + s["covered_unconfirmed"]
                  for s in states.values())
    tot_fn = sum(s["label_false_negatives"] for s in states.values())
    tot_unj = sum(s["unjoined_frame"] for s in states.values())
    tot_dis = sum(s["label_positive_no_tracker_record"]
                  for s in states.values())
    return {
        "states": states,
        "national": {
            "census_in_scope": tot_scope,
            "recall_any": round(tot_any / tot_scope, 3) if tot_scope else None,
            "label_false_negatives": tot_fn,
            "unjoined_frame": tot_unj,
            "label_positive_no_tracker_record": tot_dis,
        },
        "note": ("Census is a lower bound and the unit is the county, not "
                 "the instrument episode. recall_any is the share of "
                 "census counties with ANY tracker restrictive record; "
                 "recall_confirmed requires a terminal confirmation. "
                 "label_false_negatives are census-enacted counties the "
                 "county model currently trains on as negatives. "
                 "unjoined_frame counts census counties that do not match "
                 "the national county frame at all, which is a census "
                 "name defect, not a coverage result. "
                 "label_positive_no_tracker_record counts the reverse "
                 "direction: the aggregate labels the county enacted "
                 "while the clean feed carries no county-level "
                 "restrictive record, which is a divergence between "
                 "county_aggregator.py's label rule and this module's "
                 "mechanism view, reported here and not reconciled here."),
    }


def write_rows(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["state", "county", "census_instrument", "census_status",
              "census_episodes", "census_statuses", "census_date",
              "census_source", "tracker_mechanism",
              "tracker_outcome", "tracker_date", "model_label",
              "join_status", "gap_class", "label_check",
              "label_false_negative"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    census = [
        {"state": "IN", "county": "Cass County", "instrument": "ban",
         "census_status": "active", "date_enacted": "2026-05-01",
         "source": "https://example.org/cass"},
        {"state": "IN", "county": "Fulton County", "instrument": "moratorium",
         "census_status": "active", "date_enacted": "2026-03-03",
         "source": "https://example.org/fulton"},
        {"state": "IN", "county": "DeKalb County", "instrument": "moratorium",
         "census_status": "active", "date_enacted": "2026-04-13",
         "source": "https://example.org/dekalb"},
        {"state": "OH", "county": "Stark County", "instrument": "moratorium",
         "census_status": "pending", "date_enacted": "",
         "source": "https://example.org/stark"},
        # Governing-body suffix: must join to Meade County.
        {"state": "KY", "county": "Meade County Fiscal Court",
         "instrument": "moratorium", "census_status": "active",
         "date_enacted": "2026-01-05", "source": "https://example.org/meade"},
        # Three episodes, one county: must collapse to a single row.
        {"state": "ND", "county": "Oliver County (Phase 1)",
         "instrument": "moratorium", "census_status": "rescinded",
         "date_enacted": "2024-12-12", "source": "https://example.org/ol1"},
        {"state": "ND", "county": "Oliver County (Phase 2)",
         "instrument": "moratorium", "census_status": "expired",
         "date_enacted": "2025-05-06", "source": "https://example.org/ol2"},
        {"state": "ND", "county": "Oliver County (Phase 3)",
         "instrument": "moratorium", "census_status": "active",
         "date_enacted": "2026-03-13", "source": "https://example.org/ol3"},
        # Consolidated city-county government.
        {"state": "GA", "county": "Athens-Clarke County",
         "instrument": "moratorium", "census_status": "replaced",
         "date_enacted": "2025-12-02", "source": "https://example.org/acc"},
        # Aggregate says enacted, clean feed carries no restrictive record.
        {"state": "IA", "county": "Story County", "instrument": "moratorium",
         "census_status": "active", "date_enacted": "2026-02-02",
         "source": "https://example.org/story"},
        # Census county absent from the national frame entirely.
        {"state": "IA", "county": "Nowhere County", "instrument": "ban",
         "census_status": "active", "date_enacted": "2026-02-02",
         "source": "https://example.org/nowhere"},
    ]
    records = [
        {"State": "IN", "County": "Fulton County",
         "qc_mechanism": "moratorium",
         "outcome_defensible": "blocked_confirmed", "Date": "2026-03-03"},
        {"State": "IN", "County": "DeKalb County",
         "qc_mechanism": "moratorium",
         "outcome_defensible": "pending", "Date": "2026-04-14"},
        {"State": "IN", "County": "Cass County",
         "qc_mechanism": "public_pressure",
         "outcome_defensible": "pending", "Date": "2026-02-01"},
    ]
    agg = [
        {"state": "IN", "county_name": "Cass County, Indiana",
         "has_enacted_restrictive": "0"},
        {"state": "IN", "county_name": "Fulton County, Indiana",
         "has_enacted_restrictive": "1"},
        {"state": "IN", "county_name": "DeKalb County, Indiana",
         "has_enacted_restrictive": "0"},
        {"state": "KY", "county_name": "Meade County, Kentucky",
         "has_enacted_restrictive": "0"},
        {"state": "ND", "county_name": "Oliver County, North Dakota",
         "has_enacted_restrictive": "0"},
        {"state": "GA", "county_name": "Clarke County, Georgia",
         "has_enacted_restrictive": "0"},
        {"state": "IA", "county_name": "Story County, Iowa",
         "has_enacted_restrictive": "1"},
    ]

    rows = audit(census, records, agg)
    by = {(r["state"], r["county"]): r for r in rows}
    checks = []

    def ck(label, cond):
        checks.append((label, cond))

    cass = by[("IN", "Cass County")]
    ck("cass is missing (non-restrictive record does not count)",
       cass["gap_class"] == "missing")
    ck("cass is a model label false negative",
       cass["label_false_negative"] == "1")
    ck("fulton covered_confirmed",
       by[("IN", "Fulton County")]["gap_class"] == "covered_confirmed")
    ck("fulton not a false negative",
       by[("IN", "Fulton County")]["label_false_negative"] == "0")
    dek = by[("IN", "DeKalb County")]
    ck("dekalb covered_unconfirmed (pending outcome)",
       dek["gap_class"] == "covered_unconfirmed")
    ck("dekalb IS a false negative (census enacted, label 0)",
       dek["label_false_negative"] == "1")
    ck("pending census row out of scope",
       by[("OH", "Stark County")]["gap_class"]
       == "census_pending_out_of_scope")
    ck("pending census row not a false negative",
       by[("OH", "Stark County")]["label_false_negative"] == "0")

    s = summarize(rows)
    ck("IN in-scope = 3", s["states"]["IN"]["census_in_scope"] == 3)
    ck("IN recall_any = 2/3",
       s["states"]["IN"]["recall_any"] == round(2 / 3, 3))
    ck("IN recall_confirmed = 1/3",
       s["states"]["IN"]["recall_confirmed"] == round(1 / 3, 3))
    ck("IN label false negatives = 2",
       s["states"]["IN"]["label_false_negatives"] == 2)
    ck("OH has zero in scope", s["states"]["OH"]["census_in_scope"] == 0)
    ck("national recall counts every state in the fixture",
       s["national"]["census_in_scope"] == 8)

    ck("norm_county strips suffix", norm_county("Cass County") == "cass")
    ck("norm_county handles parish",
       norm_county("Caddo Parish") == "caddo")
    ck("norm_county idempotent",
       norm_county(norm_county("St. Joseph County")) ==
       norm_county("St. Joseph County"))

    # --- 2026-08-05 fixes ---
    ck("norm_county strips governing body",
       norm_county("Meade County Fiscal Court") == "meade")
    ck("display name strips governing body",
       display_county("Meade County Fiscal Court") == "Meade County")
    ck("display name strips parenthetical",
       display_county("Clay County (permanent restriction)") == "Clay County")
    ck("norm_county strips parenthetical annotation",
       norm_county("Clay County (permanent restriction)") == "clay")
    ck("norm_county strips phase annotation",
       norm_county("Oliver County (Phase 2)") == "oliver")
    ck("norm_county resolves consolidated government",
       norm_county("Athens-Clarke County", "GA") == "clarke")
    ck("consolidated alias is state-scoped",
       norm_county("Athens-Clarke County", "OH") == "athensclarke")
    ck("norm_county leaves Miami-Dade intact",
       norm_county("Miami-Dade County", "FL") == "miamidade")

    ck("meade joins the frame after body strip",
       by[("KY", "Meade County")]["join_status"] == "joined")
    ck("meade now registers as label noise",
       by[("KY", "Meade County")]["label_check"]
       == "label_false_negative")

    ol = [r for r in rows if r["state"] == "ND"]
    ck("oliver collapses to one row", len(ol) == 1)
    ck("oliver keeps the episode count", ol[0]["census_episodes"] == 3)
    ck("oliver reports the strongest in-scope status",
       ol[0]["census_status"] == "active")
    ck("oliver keeps the earliest in-scope date",
       ol[0]["census_date"] == "2024-12-12")
    ck("oliver display name drops the annotation",
       "(" not in ol[0]["county"])

    acc = [r for r in rows if r["state"] == "GA"][0]
    ck("athens-clarke joins the frame", acc["join_status"] == "joined")

    story = by[("IA", "Story County")]
    ck("directional disagreement is named, not counted as coverage",
       story["label_check"] == "label_positive_no_tracker_record")
    ck("directional disagreement is not a label false negative",
       story["label_false_negative"] == "0")

    nowhere = by[("IA", "Nowhere County")]
    ck("census county absent from the frame is flagged unjoined",
       nowhere["join_status"] == "unjoined_frame")
    ck("unjoined row does not silently read as agreement",
       nowhere["label_check"] == "unjoined_frame")

    ck("summary carries unjoined count",
       s["national"]["unjoined_frame"] == 1)
    ck("summary carries directional disagreement count",
       s["national"]["label_positive_no_tracker_record"] == 1)

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--state", default=None)
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    for path in (CENSUS_CSV, MASTER_CLEAN, AGG_CSV):
        if not os.path.exists(path):
            print(f"ERROR: {os.path.relpath(path, HERE)} not found; "
                  f"coverage audit needs it")
            return 1

    census = read_csv(CENSUS_CSV)
    records = read_csv(MASTER_CLEAN)
    agg = read_csv(AGG_CSV)

    rows = audit(census, records, agg)
    write_rows(rows, OUT_CSV)
    summary = summarize(rows)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    natl = summary["national"]
    print(f"census counties in scope: {natl['census_in_scope']}")
    print(f"national recall_any: {natl['recall_any']}")
    print(f"model label false negatives: {natl['label_false_negatives']}")
    print(f"census rows unjoined to the county frame: "
          f"{natl['unjoined_frame']}")
    print(f"label-positive with no tracker record: "
          f"{natl['label_positive_no_tracker_record']}")
    print()
    sel = summary["states"]
    if args.state:
        sel = {args.state: sel[args.state]} if args.state in sel else {}
    for st, s in sorted(sel.items(),
                        key=lambda kv: -(kv[1]["missing"] or 0)):
        print(f"  {st}: in_scope {s['census_in_scope']:3}  "
              f"missing {s['missing']:3}  recall_any {s['recall_any']}  "
              f"label_FN {s['label_false_negatives']}")
    print(f"\nwrote {OUT_CSV}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
