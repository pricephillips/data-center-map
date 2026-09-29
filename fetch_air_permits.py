#!/usr/bin/env python3
"""
fetch_air_permits.py -- EPA ECHO air-program facilities that are data centers.

Why air permits
---------------
A hyperscale campus needs backup generation, and backup generation needs an
air permit: hundreds of diesel or gas gensets (one Alabama project in
data/proposals.csv lists 516) put a site over major-source thresholds. That
permit is filed with the state and flows into EPA's ICIS-Air, and from there
into ECHO, usually well before the building exists. It is also the one
federal record that names the facility, geocodes it, and carries the NAICS
code for data processing and hosting (518210). No other free national source
dates a data center that early.

What it does
------------
Queries ECHO's air facility search state by state, twice: by NAICS 518210 and
by facility name containing "DATA CENTER". De-duplicates on the FRS registry
id, then classifies each facility against what the repository already knows:

  proposal_match   within MATCH_KM of a Layer B project in the same state:
                   independent federal corroboration of that project
  facility_match   within FACILITY_KM of a Layer A registry facility:
                   an existing operating site, context only
  unmatched        neither: a permitted data center the repository has never
                   seen. The review queue.

`first_seen` is preserved per registry id across runs, so a facility that
appears in ECHO for the first time is visible as new. Nothing here is a
source of record; every row is a candidate for a human to verify.

Review decisions live in data/airpermit_review.csv, a hand-maintained file
keyed on the FRS registry id (verdict, priority, basis). This module only
reads it: each run copies the verdict onto the matching queue row as
review_verdict / review_priority / review_note, and sorts rows nobody has
reviewed yet to the top of each match group, so a permit that is new to ECHO
stands out instead of hiding among the ones already triaged.

Schema tolerance
----------------
ECHO's column names are resolved through candidate lists and checked at
runtime, the same discipline as fetch_pudl.py. If name, state or coordinates
cannot be resolved the run writes nothing and prints the keys it received.

Output
  data/proposal_candidates_airpermits.csv
  data/proposal_candidates_airpermits_log.csv   append-only run log
Reads
  data/airpermit_review.csv                     hand-kept review verdicts

Usage
  python fetch_air_permits.py --fetch [--states VA,OH]
  python fetch_air_permits.py --fixture path.json   offline replay
  python fetch_air_permits.py --selftest

Public domain (EPA). Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(ROOT, "data", "proposal_candidates_airpermits.csv")
LOG_CSV = os.path.join(ROOT, "data", "proposal_candidates_airpermits_log.csv")
PROPOSALS = os.path.join(ROOT, "data", "proposals.csv")
FACILITIES = os.path.join(ROOT, "data", "facility_registry.csv")
REVIEW = os.path.join(ROOT, "data", "airpermit_review.csv")

ECHO = "https://echodata.epa.gov/echo/air_rest_services"
USER_AGENT = "hawthorn-dc-tracker/1.0 (data center permit monitoring; contact repo owner)"
NAICS = "518210"
NAME_QUERY = "DATA CENTER"
MATCH_KM = 3.0
FACILITY_KM = 1.0
PAUSE_S = 1.0

STATES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
          "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY").split()
STATE_NAME = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
              "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
              "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
              "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
              "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
              "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
              "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
              "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
              "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
              "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
              "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
              "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}
NAME_TO_ABBR = {v.lower(): k for k, v in STATE_NAME.items()}

CONCEPTS = {
    "name": ["AIRName", "FacName", "Name"],
    "street": ["AIRStreet", "FacStreet", "Street"],
    "city": ["AIRCity", "FacCity", "City"],
    "state": ["AIRState", "FacState", "State"],
    "zip": ["AIRZip", "FacZip", "Zip"],
    "county": ["AIRCounty", "FacCounty", "County"],
    "lat": ["FacLat", "AIRLat", "Lat", "Latitude"],
    "lon": ["FacLong", "AIRLong", "Lon", "Long", "Longitude"],
    "registry_id": ["RegistryID", "FRSID", "RegistryId"],
    "source_id": ["SourceID", "AIRIDs", "SourceId"],
    "naics": ["AIRNAICS", "FacNAICSCodes", "NAICSCodes", "AIRNaics"],
    "programs": ["AIRPrograms", "AIRProgramCodes", "Programs"],
    "status": ["AIROpStatus", "AIRStatus", "OperatingStatus"],
    "classification": ["AIRUniverse", "AIRClassification", "AIRMacts"],
}
REQUIRED = ("name", "state", "lat", "lon")

FIELDS = ["registry_id", "source_id", "name", "street", "city", "county", "state",
          "zip", "lat", "lon", "naics", "programs", "status", "classification",
          "found_by", "match", "match_project_id", "match_project_name", "match_km",
          "match_facility_id", "first_seen", "last_seen", "echo_url",
          "review_verdict", "review_priority", "review_note"]


def resolve(keys) -> tuple[dict, list]:
    lower = {k.lower(): k for k in keys}
    m = {}
    for c, cands in CONCEPTS.items():
        for k in cands:
            if k.lower() in lower:
                m[c] = lower[k.lower()]
                break
    return m, [c for c in REQUIRED if c not in m]


# ---------------------------------------------------------------------------
# network
# ---------------------------------------------------------------------------

def _get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def query_state(state, by):
    """All ECHO air facilities in `state` matching NAICS or name. List of dicts."""
    params = {"output": "JSON", "p_st": state, "responseset": "5000"}
    if by == "naics":
        params["p_ncs"] = NAICS
    else:
        params["p_fn"] = NAME_QUERY
    head = _get(f"{ECHO}.get_facilities?{urllib.parse.urlencode(params)}")
    res = head.get("Results", {})
    qid = res.get("QueryID")
    if not qid or str(res.get("QueryRows", "0")) in ("0", ""):
        return []
    out, page = [], 1
    while True:
        body = _get(f"{ECHO}.get_qid?{urllib.parse.urlencode({'output': 'JSON', 'qid': qid, 'pageno': page})}")
        facs = body.get("Results", {}).get("Facilities") or []
        out.extend(facs)
        if len(facs) == 0 or len(out) >= int(res.get("QueryRows") or 0):
            break
        page += 1
        time.sleep(PAUSE_S)
    return out


def fetch(states):
    raw = []
    for st in states:
        for by in ("naics", "name"):
            try:
                facs = query_state(st, by)
            except Exception as e:                      # one state never kills the run
                print(f"  {st} {by}: {e}", file=sys.stderr)
                facs = []
            for f in facs:
                f["_found_by"] = by
            raw.extend(facs)
            time.sleep(PAUSE_S)
    return raw


# ---------------------------------------------------------------------------
# normalize + match
# ---------------------------------------------------------------------------

def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def haversine_km(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def normalize(raw):
    if not raw:
        return []
    keys = set()
    for r in raw[:50]:
        keys |= set(r)
    m, missing = resolve(keys)
    if missing:
        raise SystemExit("fetch_air_permits: ECHO schema did not resolve "
                         f"({', '.join(missing)}). Keys received: {sorted(keys)}")
    by_reg = {}
    for r in raw:
        row = {c: str(r.get(col, "") or "").strip() for c, col in m.items()}
        row["found_by"] = r.get("_found_by", "")
        key = row.get("registry_id") or f"{row['name']}|{row['state']}|{row['lat']}"
        if key in by_reg:
            prev = by_reg[key]
            if row["found_by"] not in prev["found_by"]:
                prev["found_by"] = "; ".join(sorted({prev["found_by"], row["found_by"]}))
            continue
        by_reg[key] = row
    return list(by_reg.values())


def _state_abbr(s):
    s = (s or "").strip()
    return s.upper() if len(s) == 2 else NAME_TO_ABBR.get(s.lower(), s)


def load_points(path, id_col, name_col, state_col):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            lat, lon = _f(r.get("lat")), _f(r.get("lon"))
            if lat is None or lon is None:
                continue
            out.append((str(r.get(id_col, "")), r.get(name_col, ""),
                        _state_abbr(r.get(state_col, "")), lat, lon))
    return out


def classify(rows, projects, facilities):
    for row in rows:
        lat, lon = _f(row.get("lat")), _f(row.get("lon"))
        st = _state_abbr(row.get("state"))
        row.update(match="unmatched", match_project_id="", match_project_name="",
                   match_km="", match_facility_id="")
        if lat is None or lon is None:
            row["match"] = "no_coordinates"
            continue
        best = None
        for pid, name, pst, plat, plon in projects:
            if pst and st and pst != st:
                continue
            d = haversine_km(lat, lon, plat, plon)
            if d <= MATCH_KM and (best is None or d < best[0]):
                best = (d, pid, name)
        if best:
            row.update(match="proposal_match", match_project_id=f"prj_{best[1]}",
                       match_project_name=best[2], match_km=f"{best[0]:.2f}")
            continue
        for fid, name, fst, flat, flon in facilities:
            if haversine_km(lat, lon, flat, flon) <= FACILITY_KM:
                row.update(match="facility_match", match_facility_id=fid)
                break
    return rows


def merge_history(rows, prior_path=OUT_CSV, today=None):
    """Carry first_seen forward per registry id; stamp last_seen today."""
    today = today or date.today().isoformat()
    first = {}
    if os.path.exists(prior_path):
        with open(prior_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                first[r.get("registry_id") or r.get("name")] = r.get("first_seen") or today
    for r in rows:
        k = r.get("registry_id") or r.get("name")
        r["first_seen"] = first.get(k, today)
        r["last_seen"] = today
        rid = r.get("registry_id", "")
        r["echo_url"] = (f"https://echo.epa.gov/detailed-facility-report?fid={rid}" if rid else "")
    return rows


def apply_review(rows, path=REVIEW):
    """Copy each hand-kept verdict onto its queue row, by registry id."""
    verdicts = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("registry_id"):
                    verdicts[r["registry_id"]] = r
    for row in rows:
        v = verdicts.get(row.get("registry_id", ""), {})
        row["review_verdict"] = v.get("verdict", "")
        row["review_priority"] = v.get("priority", "")
        row["review_note"] = v.get("basis", "")
    return rows


def write(rows, path=OUT_CSV):
    order = {"unmatched": 0, "proposal_match": 1, "facility_match": 2, "no_coordinates": 3}
    rows.sort(key=lambda r: (order.get(r["match"], 9), bool(r.get("review_verdict")),
                             r.get("state", ""), r.get("name", "")))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def log_run(rows, states, path=LOG_CSV):
    new = not os.path.exists(path)
    today = date.today().isoformat()
    counts = {k: sum(1 for r in rows if r["match"] == k)
              for k in ("proposal_match", "facility_match", "unmatched", "no_coordinates")}
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(["run_date", "states", "facilities", "new_today", *counts])
        w.writerow([today, len(states), len(rows),
                    sum(1 for r in rows if r["first_seen"] == today), *counts.values()])


def run(raw, states):
    rows = normalize(raw)
    projects = load_points(PROPOSALS, "id", "name", "state")
    facilities = load_points(FACILITIES, "facility_id", "name", "state")
    rows = apply_review(merge_history(classify(rows, projects, facilities)))
    write(rows)
    log_run(rows, states)
    c = {k: sum(1 for r in rows if r["match"] == k) for k in
         ("proposal_match", "facility_match", "unmatched")}
    print(f"fetch_air_permits: {len(rows)} data center air facilities {c} -> {OUT_CSV}")


def selftest():
    checks = []

    def check(label, ok):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    raw = [
        {"AIRName": "PROJECT X DATA CENTER", "AIRState": "AL", "FacLat": "33.40",
         "FacLong": "-86.90", "RegistryID": "110001", "AIRNAICS": "518210", "_found_by": "naics"},
        {"AIRName": "PROJECT X DATA CENTER", "AIRState": "AL", "FacLat": "33.40",
         "FacLong": "-86.90", "RegistryID": "110001", "_found_by": "name"},
        {"AIRName": "OLD DC", "AIRState": "VA", "FacLat": "39.00", "FacLong": "-77.50",
         "RegistryID": "110002", "_found_by": "naics"},
        {"AIRName": "MYSTERY DC", "AIRState": "OH", "FacLat": "40.00", "FacLong": "-83.00",
         "RegistryID": "110003", "_found_by": "name"},
        {"AIRName": "NO GEO DC", "AIRState": "OH", "FacLat": "", "FacLong": "",
         "RegistryID": "110004", "_found_by": "name"},
    ]
    rows = normalize(raw)
    check("a facility found by both queries is kept once", len(rows) == 4)
    check("and records both ways it was found",
          next(r for r in rows if r["registry_id"] == "110001")["found_by"] == "naics; name")
    projects = [("7", "Project X", "AL", 33.41, -86.90)]
    facilities = [("fac_1", "Old DC", "VA", 39.001, -77.500)]
    classify(rows, projects, facilities)
    by = {r["registry_id"]: r for r in rows}
    check("a permit near a proposal in the same state corroborates it",
          by["110001"]["match"] == "proposal_match" and by["110001"]["match_project_id"] == "prj_7")
    check("a permit at a known facility is context", by["110002"]["match"] == "facility_match")
    check("a permit nobody knows about is the review queue", by["110003"]["match"] == "unmatched")
    check("a permit without coordinates is never matched", by["110004"]["match"] == "no_coordinates")
    check("a proposal across a state line is not matched",
          classify([dict(by["110001"], state="GA")], projects, [])[0]["match"] != "proposal_match")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prior.csv")
        with open(p, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerow({"registry_id": "110003", "first_seen": "2026-01-01"})
        merge_history(rows, p, today="2026-09-28")
    check("first_seen is carried across runs", by["110003"]["first_seen"] == "2026-01-01")
    check("a facility seen for the first time is stamped today",
          by["110001"]["first_seen"] == "2026-09-28")
    with tempfile.TemporaryDirectory() as d:
        rp = os.path.join(d, "review.csv")
        with open(rp, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["registry_id", "verdict", "priority", "basis"])
            w.writeheader()
            w.writerow({"registry_id": "110003", "verdict": "lead_untracked",
                        "priority": "high", "basis": "operator not named"})
        apply_review(rows, rp)
        op = os.path.join(d, "out.csv")
        extra = dict(by["110003"], registry_id="110009", name="AAA UNREVIEWED",
                     review_verdict="", review_priority="", review_note="")
        write(rows + [extra], op)
        with open(op, newline="", encoding="utf-8") as fh:
            unmatched = [r["registry_id"] for r in csv.DictReader(fh) if r["match"] == "unmatched"]
    check("a hand-kept verdict is carried onto its permit",
          by["110003"]["review_verdict"] == "lead_untracked"
          and by["110003"]["review_priority"] == "high")
    check("a permit with no verdict stays unreviewed", by["110001"]["review_verdict"] == "")
    check("unreviewed permits sort ahead of reviewed ones", unmatched[:2] == ["110009", "110003"])
    try:
        normalize([{"Foo": 1}])
        check("an unresolvable schema aborts", False)
    except SystemExit as e:
        check("an unresolvable schema aborts and prints the keys", "Keys received" in str(e))
    n = sum(checks)
    print(f"\n{n}/{len(checks)} checks passed")
    return 0 if n == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="EPA ECHO data center air permits")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--states", default="")
    ap.add_argument("--fixture")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    states = [s.strip().upper() for s in a.states.split(",") if s.strip()] or STATES
    if a.fixture:
        run(json.load(open(a.fixture, encoding="utf-8")), states)
    elif a.fetch:
        run(fetch(states), states)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
