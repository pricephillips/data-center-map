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

Outputs
  data/coverage_gap_report.csv     one row per census county, matched or
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


def norm_county(name: str) -> str:
    n = str(name or "").lower().strip()
    n = re.sub(r"\b(county|parish|borough)\b", "", n)
    n = re.sub(r"[^a-z ]", "", n)
    return re.sub(r"\s+", " ", n).strip()


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
        cty = norm_county(r.get("County", ""))
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
        cty = norm_county((r.get("county_name") or "").split(",")[0])
        if st and cty:
            out[(st, cty)] = r.get("has_enacted_restrictive", "")
    return out


def audit(census: list[dict], records: list[dict],
          agg_rows: list[dict]) -> list[dict]:
    trk = tracker_counties(records)
    lbl = model_labels(agg_rows)

    rows = []
    for c in census:
        st = (c.get("state") or "").strip()
        cty_raw = (c.get("county") or "").strip()
        key = (st, norm_county(cty_raw))
        status = (c.get("census_status") or "").strip().lower()
        in_scope = status in ENACTED_STATUSES

        hit = trk.get(key)
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

        rows.append({
            "state": st,
            "county": cty_raw,
            "census_instrument": c.get("instrument", ""),
            "census_status": status,
            "census_date": c.get("date_enacted", ""),
            "census_source": c.get("source", ""),
            "tracker_mechanism": hit["mechanism"] if hit else "",
            "tracker_outcome": hit["outcome"] if hit else "",
            "tracker_date": hit["date"] if hit else "",
            "model_label": label,
            "gap_class": gap,
            "label_false_negative": "1" if label_false_negative else "0",
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    per = defaultdict(lambda: Counter())
    for r in rows:
        per[r["state"]][r["gap_class"]] += 1
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
        }
    tot_scope = sum(s["census_in_scope"] for s in states.values())
    tot_any = sum(s["covered_confirmed"] + s["covered_unconfirmed"]
                  for s in states.values())
    tot_fn = sum(s["label_false_negatives"] for s in states.values())
    return {
        "states": states,
        "national": {
            "census_in_scope": tot_scope,
            "recall_any": round(tot_any / tot_scope, 3) if tot_scope else None,
            "label_false_negatives": tot_fn,
        },
        "note": ("Census is a lower bound. recall_any is the share of "
                 "census counties with ANY tracker restrictive record; "
                 "recall_confirmed requires a terminal confirmation. "
                 "label_false_negatives are census-enacted counties the "
                 "county model currently trains on as negatives."),
    }


def write_rows(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["state", "county", "census_instrument", "census_status",
              "census_date", "census_source", "tracker_mechanism",
              "tracker_outcome", "tracker_date", "model_label",
              "gap_class", "label_false_negative"]
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
    ck("national recall matches", s["national"]["recall_any"]
       == round(2 / 3, 3))

    ck("norm_county strips suffix", norm_county("Cass County") == "cass")
    ck("norm_county handles parish",
       norm_county("Caddo Parish") == "caddo")
    ck("norm_county idempotent",
       norm_county(norm_county("St. Joseph County")) ==
       norm_county("St. Joseph County"))

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
