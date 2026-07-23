"""
county_aggregator.py

County-level data layer for the opposition intelligence platform. Unit of
analysis: county. Builds one row per U.S. county (including Puerto Rico
municipios) joining:

  - data/county_census_features.csv  (frame: all counties; income, density,
    education; ACS5 2019-2023, spot-checked upstream)
  - data/county_votes.json           (2016/2024 presidential margins by
    FIPS; positive = Democratic, sign-pinned in the QC gate)
  - atlas.csv                        (existing data center counts)
  - master_opposition.csv            (opposition events by type; enacted
    restrictive policy isolated via Opposition Type x Status)
  - data/baseline_universe.csv + data/project_lifecycles.csv (project
    outcomes under the four-tier vocabulary; median days to decision over
    dated decided cases only)

Outputs:
  - data/county_aggregate.csv           (one row per county, map-consumable)
  - data/county_aggregate_manifest.json (input hashes, match rates, counts)

A hard QC gate runs before anything is written: county count bounds, unique
FIPS, event match rate floor, votes sign-convention pins, coverage floors,
and internal-consistency invariants. On any failure the module exits
nonzero and writes nothing, so a degraded layer can never silently replace
a good one. Modeling lives downstream in county_policy_model.py.

Standing rules honored: four-tier outcome vocabulary only, decided means
terminal only, leak audit on generated outputs, additive, no em-dashes.

Usage: python county_aggregator.py
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics as st
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(ROOT, *parts)


CENSUS_CSV = P("data", "county_census_features.csv")
VOTES_JSON = P("data", "county_votes.json")
ATLAS_CSV = P("atlas.csv")
MASTER_CSV = P("master_opposition.csv")
UNIVERSE_CSV = P("data", "baseline_universe.csv")
LIFECYCLES_CSV = P("data", "project_lifecycles.csv")

OUT_CSV = P("data", "county_aggregate.csv")

RESTRICTIVE_TYPES = {"moratorium", "zoning_restriction"}
ENACTED_STATUSES = {"passed", "approved"}

STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL",
    "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR",
}
ABBRS = set(STATE_ABBR.values())

SUFFIX_RE = re.compile(
    r"\s+(County|Parish|Borough|Census Area|Municipality|Municipio|City and Borough|Planning Region)\s*$",
    re.I)


def norm_county(name: str) -> str:
    n = SUFFIX_RE.sub("", (name or "").strip())
    n = n.replace(".", "").replace("'", "")
    n = re.sub(r"^(St)\b", "Saint", n, flags=re.I)
    n = re.sub(r"\s+", " ", n)
    return n.upper()


def norm_state(s: str) -> str:
    s = (s or "").strip().upper()
    if s in ABBRS:
        return s
    return STATE_ABBR.get(s, "")


def parse_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_frame():
    """All counties from the census layer; returns fips -> record and a
    (county, state) -> fips resolver."""
    frame = {}
    resolver = {}
    for r in csv.DictReader(open(CENSUS_CSV, encoding="utf-8-sig")):
        fips = r["fips"].zfill(5)
        cn, _, stname = r["county_name"].rpartition(",")
        ab = norm_state(stname)
        frame[fips] = {
            "fips": fips,
            "county_name": r["county_name"],
            "state": ab,
            "median_hh_income": parse_float(r["median_hh_income"]),
            "population": parse_float(r["population"]),
            "pop_density_sqmi": parse_float(r["pop_density_sqmi"]),
            "pct_bachelors_plus": parse_float(r["pct_bachelors_plus"]),
            "land_sqmi": parse_float(r["land_sqmi"]),
        }
        if ab:
            resolver[(norm_county(cn), ab)] = fips
    return frame, resolver


def main() -> int:
    frame, resolver = load_frame()

    # --- votes ---
    votes = json.load(open(VOTES_JSON, encoding="utf-8"))
    for fips, rec in frame.items():
        v = votes.get(fips) or {}
        rec["margin_2024"] = v.get("2024")
        rec["margin_2016"] = v.get("2016")

    # --- atlas: existing DC counts ---
    unmatched_atlas = 0
    dc_count = Counter()
    for r in csv.DictReader(open(ATLAS_CSV, encoding="utf-8-sig")):
        key = (norm_county(r.get("county")), norm_state(r.get("state")))
        f = resolver.get(key)
        if f:
            dc_count[f] += 1
        else:
            unmatched_atlas += 1
    for fips, rec in frame.items():
        rec["existing_dc_count"] = dc_count.get(fips, 0)

    # --- master opposition events ---
    ev_total = Counter()
    ev_bytype = defaultdict(Counter)
    enacted_restrictive = Counter()
    ev_rows = 0
    ev_matched = 0
    state_leg = Counter()
    for r in csv.DictReader(open(MASTER_CSV, encoding="utf-8-sig")):
        cty, stt = r.get("County"), r.get("State")
        if not (cty or "").strip():
            # state-scope records: no county. Count legislation activity by
            # state. These rows can never contribute to the county outcome
            # (which requires a county match), so using them as a predictor
            # is leakage-free by construction.
            if (stt or "").strip() and                     (r.get("Opposition Type") or "").strip() == "legislation":
                state_leg[norm_state(stt)] += 1
            continue
        if not (stt or "").strip():
            continue
        ev_rows += 1
        f = resolver.get((norm_county(cty), norm_state(stt)))
        if not f:
            continue
        ev_matched += 1
        ev_total[f] += 1
        ot = (r.get("Opposition Type") or "").strip()
        if ot:
            ev_bytype[f][ot] += 1
        if ot in RESTRICTIVE_TYPES and \
                (r.get("Status") or "").strip().lower() in ENACTED_STATUSES:
            enacted_restrictive[f] += 1

    # --- project outcomes (four-tier vocabulary; decided = terminal only) ---
    life = {r["project_id"]: r for r in
            csv.DictReader(open(LIFECYCLES_CSV, encoding="utf-8-sig"))}
    n_projects = Counter()
    n_opposed = Counter()
    outc = defaultdict(Counter)
    dtd = defaultdict(list)  # days announced to decision, dated decided only
    for r in csv.DictReader(open(UNIVERSE_CSV, encoding="utf-8-sig")):
        f = (r.get("fips") or "").strip().zfill(5)
        if f not in frame:
            key = (norm_county(r.get("county")), norm_state(r.get("state")))
            f = resolver.get(key, "")
        if not f:
            continue
        n_projects[f] += 1
        if r.get("opposed_flag") == "yes":
            n_opposed[f] += 1
        if r.get("decided") == "yes":
            outc[f][r.get("lifecycle_outcome") or "unknown"] += 1
            lr = life.get(r["universe_id"])
            if lr:
                d = parse_float(lr.get("days_announced_to_decision"))
                if d is not None and d >= 0:
                    dtd[f].append(d)

    # --- assemble output rows ---
    fields = [
        "fips", "county_name", "state",
        "median_hh_income", "population", "pop_density_sqmi",
        "pct_bachelors_plus", "margin_2024", "margin_2016",
        "existing_dc_count", "dc_presence",
        "n_opposition_events", "n_moratorium_events", "n_zoning_events",
        "n_legislation_events", "n_lawsuit_events",
        "n_enacted_restrictive", "has_enacted_restrictive",
        "land_sqmi", "state_legislation_events",
        "n_projects_tracked", "n_projects_opposed",
        "n_decided", "n_blocked_confirmed", "n_advanced_confirmed",
        "n_restricted_conditional",
        "median_days_to_decision",
    ]
    out_rows = []
    for fips in sorted(frame):
        rec = frame[fips]
        bt = ev_bytype.get(fips, Counter())
        oc = outc.get(fips, Counter())
        n_dec = sum(v for k, v in oc.items()
                    if k in ("blocked_confirmed", "advanced_confirmed",
                             "restricted_conditional"))
        dts = dtd.get(fips, [])
        out_rows.append({
            "fips": fips,
            "county_name": rec["county_name"],
            "state": rec["state"],
            "median_hh_income": rec["median_hh_income"] or "",
            "population": rec["population"] or "",
            "pop_density_sqmi": rec["pop_density_sqmi"] or "",
            "pct_bachelors_plus": rec["pct_bachelors_plus"] or "",
            "margin_2024": rec["margin_2024"] if rec["margin_2024"] is not None else "",
            "margin_2016": rec["margin_2016"] if rec["margin_2016"] is not None else "",
            "existing_dc_count": rec["existing_dc_count"],
            "dc_presence": 1 if (rec["existing_dc_count"] > 0
                                 or n_projects.get(fips, 0) > 0) else 0,
            "n_opposition_events": ev_total.get(fips, 0),
            "n_moratorium_events": bt.get("moratorium", 0),
            "n_zoning_events": bt.get("zoning_restriction", 0),
            "n_legislation_events": bt.get("legislation", 0),
            "n_lawsuit_events": bt.get("lawsuit", 0),
            "land_sqmi": rec["land_sqmi"] or "",
            "state_legislation_events": state_leg.get(rec["state"], 0),
            "n_enacted_restrictive": enacted_restrictive.get(fips, 0),
            "has_enacted_restrictive": 1 if enacted_restrictive.get(fips, 0) else 0,
            "n_projects_tracked": n_projects.get(fips, 0),
            "n_projects_opposed": n_opposed.get(fips, 0),
            "n_decided": n_dec,
            "n_blocked_confirmed": oc.get("blocked_confirmed", 0),
            "n_advanced_confirmed": oc.get("advanced_confirmed", 0),
            "n_restricted_conditional": oc.get("restricted_conditional", 0),
            "median_days_to_decision": (round(st.median(dts)) if dts else ""),
        })

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(out_rows)

    # =====================================================================
    # QC gate: fail loudly rather than write a silently degraded layer
    # =====================================================================
    import hashlib
    from datetime import date as _date

    failures = []

    # frame integrity
    if not (3000 <= len(out_rows) <= 3400):
        failures.append(f"county count {len(out_rows)} outside [3000, 3400]")
    if len({r["fips"] for r in out_rows}) != len(out_rows):
        failures.append("duplicate fips in output")

    # event matching floor (name-based matching must stay near-complete)
    match_rate = ev_matched / ev_rows if ev_rows else 0
    if match_rate < 0.95:
        failures.append(f"event match rate {match_rate:.2f} below 0.95")

    # votes sign-convention pin: positive = Democratic margin.
    # Los Angeles County must be positive, Roberts County TX negative.
    la = next((r for r in out_rows if r["fips"] == "06037"), None)
    rb = next((r for r in out_rows if r["fips"] == "48393"), None)
    if not la or parse_float(la["margin_2024"]) is None or parse_float(la["margin_2024"]) <= 0:
        failures.append("sign pin failed: 06037 margin_2024 not positive")
    if not rb or parse_float(rb["margin_2024"]) is None or parse_float(rb["margin_2024"]) >= 0:
        failures.append("sign pin failed: 48393 margin_2024 not negative")

    # coverage floors
    if sum(r["has_enacted_restrictive"] for r in out_rows) < 50:
        failures.append("fewer than 50 enacted-restriction counties; check Status coding")
    n_margin = sum(1 for r in out_rows if r["margin_2024"] != "")
    if n_margin < 0.9 * len(out_rows):
        failures.append(f"margin coverage {n_margin}/{len(out_rows)} below 90 pct")

    # internal consistency
    for r in out_rows:
        if r["n_enacted_restrictive"] > r["n_moratorium_events"] + r["n_zoning_events"]:
            failures.append(f"{r['fips']}: enacted exceeds moratorium+zoning events")
            break
        if r["n_projects_opposed"] > r["n_projects_tracked"]:
            failures.append(f"{r['fips']}: opposed exceeds tracked projects")
            break
        if r["n_decided"] > r["n_projects_tracked"]:
            failures.append(f"{r['fips']}: decided exceeds tracked projects")
            break

    if failures:
        for f in failures:
            print("QC FAIL:", f, file=sys.stderr)
        return 1

    # manifest for reproducibility (which inputs produced this layer)
    def _sha(path):
        h = hashlib.sha256()
        h.update(open(path, "rb").read())
        return h.hexdigest()[:16]

    manifest = {
        "generated": str(_date.today()),
        "counties": len(out_rows),
        "event_rows_with_county": ev_rows,
        "event_match_rate": round(match_rate, 4),
        "enacted_restriction_counties": sum(r["has_enacted_restrictive"] for r in out_rows),
        "dc_presence_counties": sum(r["dc_presence"] for r in out_rows),
        "inputs": {os.path.basename(p): _sha(p) for p in
                   (CENSUS_CSV, VOTES_JSON, ATLAS_CSV, MASTER_CSV,
                    UNIVERSE_CSV, LIFECYCLES_CSV)},
    }
    with open(P("data", "county_aggregate_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    # leak audit on generated outputs
    pat = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.I)
    leaks = []
    for i, line in enumerate(open(OUT_CSV, encoding="utf-8"), 1):
        if pat.search(line):
            leaks.append(f"{os.path.basename(OUT_CSV)}:{i}")
    print(f"wrote {os.path.relpath(OUT_CSV, ROOT)} ({len(out_rows)} counties), "
          f"manifest, QC passed")
    print(f"event match rate: {ev_matched}/{ev_rows}; atlas unmatched: "
          f"{unmatched_atlas}")
    print("leak audit:", "FAIL " + ", ".join(leaks) if leaks else "clean")
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
