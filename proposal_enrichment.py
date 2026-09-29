#!/usr/bin/env python3
"""
proposal_enrichment.py -- one analytic row per proposed project, and the
pipeline-level numbers built from them.

The Proposed Centers page showed a list: name, state, phase, MW, jobs. Every
analytic the repository computes about a project lived in a different file --
opposition in project_lifecycles.csv, the screener tier in site_screen.csv,
the county restriction score in county_policy_scores.csv, county water, drought
and power prices under data/features/ -- and none of it reached the page. This
module joins all of it onto every project, adds the grid, generation, permit,
news and history layers built in the 2026-09-28 pass, and computes the
pipeline analytics the page renders.

Every joined value keeps its provenance. A column that could not be filled is
left blank, never defaulted, and the coverage block in the metrics file says
how many projects carry each one. Joins to files keyed on project_id are
checked by name as well as id, because the source's ids are not stable (see
proposal_history.py); a row whose name disagrees is not joined.

Descriptive, not predictive. Block rates by region or by opposition presence
are observed shares with their denominators, not effects. The one modeled
quantity, an acreage-based capacity estimate for projects that report no MW,
is carried in its own columns with its range and basis and never replaces a
reported figure.

Inputs (all optional except data/proposals.csv)
  data/proposals.csv, data/proposals_detail.json
  data/project_lifecycles.csv, data/site_screen.csv
  data/county_policy_scores.csv, data/county_aggregate.csv, data/features/*.csv
  data/county_grid_territory.csv, configs/grid_iso_crosswalk.json
  data/grid_planned_generation.csv
  data/proposal_candidates_airpermits.csv, data/proposal_candidates_news.csv
  data/pipeline_intel_project_history.csv, data/pipeline_intel_events.csv,
  data/pipeline_intel_history.json

Outputs
  data/pipeline_intel_enriched.csv   one row per project
  data/pipeline_intel_metrics.json   pipeline analytics for the page
  data/pipeline_intel_report.md      the same, readable

Usage
  python proposal_enrichment.py
  python proposal_enrichment.py --selftest

Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
D = lambda *p: os.path.join(ROOT, "data", *p)
PROPOSALS = D("proposals.csv")
OUT_CSV = D("pipeline_intel_enriched.csv")
OUT_JSON = D("pipeline_intel_metrics.json")
OUT_MD = D("pipeline_intel_report.md")
CROSSWALK = os.path.join(ROOT, "configs", "grid_iso_crosswalk.json")

GEN_KM = 16.0            # ~10 miles: planned generation "within reach"
GEN_NEAR_KM = 5.0        # co-located
CLUSTER_KM = 25.0
AIR_KM = 3.0
TIERS = ["Low", "Guarded", "Moderate", "Elevated", "High"]

ABBR = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
        "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
        "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
        "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
        "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
        "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
        "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
        "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
        "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
        "Puerto Rico": "PR", "Rhode Island": "RI", "South Carolina": "SC",
        "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
        "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
        "Wyoming": "WY"}

PHASE_ORDER = ["preliminary", "proposed", "delayed", "approved", "construction",
               "expansion", "operational", "withdrawn", "rejected"]
BLOCKED = {"withdrawn", "rejected"}
ADVANCED = {"approved", "construction", "expansion", "operational"}

FIELDS = [
    # identity
    "project_id", "name", "state", "county", "fips", "lat", "lon", "location_confidence",
    "phase", "source_reclassified_from", "stage_group", "type", "developer", "companies",
    # size and money
    "capacity_mw", "capacity_max_mw", "capacity_basis", "capacity_mw_est",
    "capacity_mw_est_low", "capacity_mw_est_high", "size_acres", "facility_sqft",
    "n_buildings", "project_cost_usd", "jobs_long_term",
    # dates and history
    "announced_date", "date_online", "days_since_source_update", "first_seen",
    "phase_since", "days_in_phase", "n_phase_changes", "phase_path",
    # power and water
    "grid_region", "grid_region_basis", "utilities", "btm_power", "power_source",
    "n_generators", "dedicated_substation", "planned_gen_mw_16km", "planned_gas_mw_16km",
    "nearest_planned_plant", "nearest_planned_km", "colocated_generation",
    "cooling_source", "county_drought_d1_pct", "county_water_withdrawal_per_sqmi",
    "county_industrial_price_cents",
    # community and policy
    "lifecycle_outcome", "outcome_group", "decided", "n_opposition_events", "n_opposition_groups",
    "has_lawsuit", "first_opposition_date", "screen_tier", "screen_composite",
    "county_restriction_score", "county_restriction_decile", "county_has_enacted_restriction",
    "county_existing_dcs", "county_margin_2024", "county_farmland_share", "nda",
    # density
    "projects_within_25km", "mw_within_25km", "county_projects", "developer_projects",
    # corroboration
    "air_permit", "air_permit_registry_id", "news_mentions", "source_citations",
    "corroboration_count", "corroboration_signals",
]


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def num(v):
    try:
        x = float(str(v).replace(",", ""))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def haversine_km(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def norm_name(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def same_project(a, b):
    """Name check for id joins. The source's ids move; names mostly don't."""
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return True
    if a == b or a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / max(1, len(ta | tb)) >= 0.5


def norm_county(c):
    c = str(c or "").split(";")[0].strip()
    return re.sub(r"\s+(county|parish|borough|city and borough|census area)$", "", c, flags=re.I)


def by_key(rows, key):
    return {r.get(key, ""): r for r in rows}


DECIDED_OUTCOMES = {"blocked_confirmed", "restricted_conditional", "advanced_confirmed"}


def outcome_group(lifecycle_outcome):
    """Constitution I: decided means a terminal disposition from
    project_resolution.py, never a raw source phase. blocked_confirmed is the
    only blocked outcome; everything unverified or pending is undecided."""
    o = (lifecycle_outcome or "").strip()
    if o == "blocked_confirmed":
        return "blocked"
    if o in DECIDED_OUTCOMES:
        return "advanced"
    return "undecided"


def stage_group(phase):
    p = (phase or "").lower()
    if p in BLOCKED:
        return "blocked"
    if p in ADVANCED:
        return "advancing"
    return "pending"


# ---------------------------------------------------------------------------
# grid region
# ---------------------------------------------------------------------------

def load_crosswalk(path=CROSSWALK):
    cw = read_json(path, {}) or {}
    state_default = {s: reg for reg, states in cw.get("state_default", {}).items() for s in states}
    split = {s: v.get("default", "") for s, v in cw.get("state_split", {}).items()}
    overrides = {}
    for reg, by_state in cw.get("county_override", {}).items():
        for st, counties in by_state.items():
            for c in counties:
                overrides[(st, norm_county(c).lower())] = reg
    return state_default, split, overrides


def grid_region(st, county, fips, territory, cw):
    """(region, basis, utilities). EIA-861 first, then the hand crosswalk."""
    t = territory.get(fips) if fips else None
    if t and t.get("primary_region"):
        basis = "eia861_split" if t.get("region_split") == "yes" else "eia861"
        return t["primary_region"], basis, t.get("utilities", "")
    state_default, split, overrides = cw
    c = norm_county(county).lower()
    if (st, c) in overrides:
        return overrides[(st, c)], "county_override", ""
    if st in split:
        return split[st], "state_split", ""
    if st in state_default:
        return state_default[st], "state_default", ""
    return "", "", ""


# ---------------------------------------------------------------------------
# enrichment
# ---------------------------------------------------------------------------

def capacity_ratio(rows):
    """Median, q25, q75 of MW per acre over projects reporting both."""
    ratios = sorted(num(r["capacity_mw"]) / num(r["size_acres"]) for r in rows
                    if num(r.get("capacity_mw")) and num(r.get("size_acres")))
    if len(ratios) < 20:
        return None
    q = statistics.quantiles(ratios, n=4)
    return {"median": statistics.median(ratios), "q25": q[0], "q75": q[2], "n": len(ratios)}


def enrich(inputs, today=None):
    today = today or date.today().isoformat()
    props = inputs["proposals"]
    fips_lookup = inputs.get("fips_lookup", {})
    life = by_key(inputs.get("lifecycles", []), "project_id")
    screen = by_key(inputs.get("screen", []), "project_id")
    hist = by_key(inputs.get("history", []), "project_id")
    scores = by_key(inputs.get("county_scores", []), "fips")
    agg = by_key(inputs.get("county_agg", []), "fips")
    feats = inputs.get("features", {})
    territory = by_key(inputs.get("territory", []), "fips")
    cw = inputs.get("crosswalk") or ({}, {}, {})
    gen = [g for g in inputs.get("planned_gen", []) if num(g.get("lat")) and num(g.get("lon"))]
    air = [a for a in inputs.get("air", []) if a.get("match") == "proposal_match"]
    news = Counter(n.get("match_project_id") for n in inputs.get("news", [])
                   if n.get("outcome") == "corroborates")
    ratio = capacity_ratio(props)
    # A source vocabulary change (2026-09-22: "approved" folded into "proposed")
    # is surfaced beside the phase, never silently reversed. Ruling 1: the
    # phase goes back to "approved" only where project_resolution.py found a
    # sourced record of a final approval, and the lifecycle row carries it.
    reclass = {e["project_id"]: e["old_value"] for e in inputs.get("events", [])
               if e.get("event_type") == "source_reclassified"}

    dev_count = Counter()
    for r in props:
        dev = (r.get("companies") or "").split(";")[0].strip()
        if dev:
            dev_count[dev] += 1
    county_count = Counter((r.get("state"), norm_county(r.get("counties")).lower()) for r in props)

    out = []
    for r in props:
        pid = f"prj_{str(r.get('id')).strip()}"
        st = ABBR.get(r.get("state", ""), r.get("state", "")[:2].upper())
        county = norm_county(r.get("counties"))
        lat, lon = num(r.get("lat")), num(r.get("lon"))
        sc = screen.get(pid)
        if sc and not same_project(sc.get("name"), r.get("name")):
            sc = None
        fips = (sc or {}).get("fips") or fips_lookup.get(f"{county.lower()}|{r.get('state','').lower()}") \
            or fips_lookup.get(f"{county.lower()} county|{r.get('state','').lower()}", "")
        lc = life.get(pid)
        if lc and not same_project(lc.get("project_name"), r.get("name")):
            lc = None
        h = hist.get(pid)
        if h and not same_project(h.get("name"), r.get("name")):
            h = None
        region, rbasis, utilities = grid_region(st, county, fips, territory, cw)

        mw = num(r.get("capacity_mw"))
        acres = num(r.get("size_acres"))
        est = lo = hi = ""
        if mw:
            basis = "reported"
        elif num(r.get("capacity_max_mw")):
            basis = "reported_max_only"
        elif acres and ratio:
            basis = "estimate_from_acreage"
            est, lo, hi = (round(acres * ratio[k]) for k in ("median", "q25", "q75"))
        else:
            basis = "unknown"

        g16 = gas16 = 0.0
        nearest = (None, None)
        coloc = False
        if lat is not None and lon is not None:
            for g in gen:
                d = haversine_km(lat, lon, num(g["lat"]), num(g["lon"]))
                if d <= GEN_KM:
                    g16 += num(g.get("planned_mw")) or 0
                    if g.get("primary_fuel") == "gas":
                        gas16 += num(g.get("planned_mw")) or 0
                    if d <= GEN_NEAR_KM and (g.get("dc_named") == "yes" or g.get("primary_fuel") == "gas"):
                        coloc = True
                if nearest[0] is None or d < nearest[0]:
                    nearest = (d, g)
        a_hit = next((a for a in air if a.get("match_project_id") == pid), None)

        signals = ["tracker"]
        if a_hit:
            signals.append("federal_air_permit")
        if news.get(pid):
            signals.append("news")
        if coloc:
            signals.append("colocated_generation")
        if lc and (num(lc.get("n_opposition_events")) or 0) > 0:
            signals.append("local_record")
        if num(r.get("n_sources")) and num(r.get("n_sources")) >= 2:
            signals.append("cited_sources")

        cscore = scores.get(fips, {})
        cagg = agg.get(fips, {})
        f = lambda name, col: feats.get(name, {}).get(fips, {}).get(col, "")
        lu = r.get("lastUpdated") or ""
        try:
            parts = [int(x) for x in re.split(r"[-T]", lu)[:3] if x.isdigit()]
            while len(parts) < 3:
                parts.append(1)
            days_upd = (date.fromisoformat(today) - date(*parts[:3])).days
        except (ValueError, TypeError):
            days_upd = ""
        dev = (r.get("companies") or "").split(";")[0].strip()
        phase = r.get("phase", "")
        if reclass.get(pid) and (lc or {}).get("phase") == reclass[pid]:
            phase = lc["phase"]
        out.append({
            "project_id": pid, "name": r.get("name", ""), "state": st, "county": county,
            "fips": fips, "lat": r.get("lat", ""), "lon": r.get("lon", ""),
            "location_confidence": r.get("locationConfidence", ""),
            "phase": phase,
            "source_reclassified_from": (reclass.get(pid, "")
                                         if h and reclass.get(pid) and "reclassified" in h.get("phase_path", "")
                                         else ""),
            "stage_group": stage_group(phase),
            "type": r.get("type", ""), "developer": dev, "companies": r.get("companies", ""),
            "capacity_mw": r.get("capacity_mw", ""), "capacity_max_mw": r.get("capacity_max_mw", ""),
            "capacity_basis": basis, "capacity_mw_est": est,
            "capacity_mw_est_low": lo, "capacity_mw_est_high": hi,
            "size_acres": r.get("size_acres", ""), "facility_sqft": r.get("facility_sqft", ""),
            "n_buildings": r.get("n_buildings", ""), "project_cost_usd": r.get("project_cost_usd", ""),
            "jobs_long_term": r.get("jobsLongTerm", ""),
            "announced_date": (lc or {}).get("announced_date") or r.get("date", ""),
            "date_online": r.get("date_online", ""), "days_since_source_update": days_upd,
            "first_seen": (h or {}).get("first_seen", ""),
            "phase_since": (h or {}).get("phase_since", ""),
            "days_in_phase": (h or {}).get("days_in_phase", ""),
            "n_phase_changes": (h or {}).get("n_phase_changes", ""),
            "phase_path": (h or {}).get("phase_path", ""),
            "grid_region": region, "grid_region_basis": rbasis, "utilities": utilities,
            "btm_power": r.get("btm_power", ""), "power_source": r.get("power_source", ""),
            "n_generators": r.get("n_generators", ""),
            "dedicated_substation": r.get("dedicated_substation", ""),
            "planned_gen_mw_16km": round(g16) if gen else "",
            "planned_gas_mw_16km": round(gas16) if gen else "",
            "nearest_planned_plant": (nearest[1] or {}).get("plant_name", "") if gen else "",
            "nearest_planned_km": round(nearest[0], 1) if gen and nearest[0] is not None else "",
            "colocated_generation": ("yes" if coloc else "no") if gen else "",
            "cooling_source": r.get("cooling_source", ""),
            "county_drought_d1_pct": f("drought", "drought_d1_mean_pct"),
            "county_water_withdrawal_per_sqmi": f("water_use", "fw_withdrawal_per_sqmi"),
            "county_industrial_price_cents": f("retail_price", "price_ind_cents"),
            "lifecycle_outcome": (lc or {}).get("lifecycle_outcome", ""),
            "outcome_group": outcome_group((lc or {}).get("lifecycle_outcome", "")),
            "decided": (lc or {}).get("decided", ""),
            "n_opposition_events": (lc or {}).get("n_opposition_events", ""),
            "n_opposition_groups": (lc or {}).get("n_opposition_groups", ""),
            "has_lawsuit": (lc or {}).get("has_lawsuit", ""),
            "first_opposition_date": (lc or {}).get("first_opposition_date", ""),
            "screen_tier": (sc or {}).get("tier", ""),
            "screen_composite": (sc or {}).get("composite", ""),
            "county_restriction_score": cscore.get("calibrated_score", ""),
            "county_restriction_decile": cscore.get("score_decile", ""),
            "county_has_enacted_restriction": cagg.get("has_enacted_restrictive", ""),
            "county_existing_dcs": cagg.get("existing_dc_count", ""),
            "county_margin_2024": cagg.get("margin_2024", ""),
            "county_farmland_share": f("farmland", "farmland_share"),
            "nda": r.get("nda", ""),
            "projects_within_25km": "", "mw_within_25km": "",
            "county_projects": county_count[(r.get("state"), county.lower())],
            "developer_projects": dev_count.get(dev, "") if dev else "",
            "air_permit": ("yes" if a_hit else "no") if inputs.get("air") else "",
            "air_permit_registry_id": (a_hit or {}).get("registry_id", ""),
            "news_mentions": news.get(pid, 0) if inputs.get("news") else "",
            "source_citations": r.get("n_sources", ""),
            "corroboration_count": len(signals),
            "corroboration_signals": "; ".join(signals),
        })

    # clustering pass
    pts = [(o, num(o["lat"]), num(o["lon"])) for o in out]
    for o, la, lo_ in pts:
        if la is None or lo_ is None:
            continue
        n, mw_sum = 0, 0.0
        for o2, la2, lo2 in pts:
            if o2 is o or la2 is None or lo2 is None:
                continue
            if haversine_km(la, lo_, la2, lo2) <= CLUSTER_KM:
                n += 1
                mw_sum += num(o2["capacity_mw"]) or 0
        o["projects_within_25km"], o["mw_within_25km"] = n, round(mw_sum)
    return out, ratio


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def _mw(rows, col="capacity_mw"):
    return round(sum(num(r.get(col)) or 0 for r in rows))


def _rate(k, n):
    return {"n": n, "k": k, "share": round(k / n, 3) if n else None}


def metrics(rows, ratio, history=None, events=None, queues=None, today=None):
    today = today or date.today().isoformat()
    n = len(rows)
    opp = lambda r: (num(r.get("n_opposition_events")) or 0) > 0

    def group(key, top=None, min_n=1):
        g = defaultdict(list)
        for r in rows:
            g[r.get(key) or "(unknown)"].append(r)
        out = []
        for k, rs in g.items():
            if len(rs) < min_n:
                continue
            dec = [x for x in rs if x["outcome_group"] in ("blocked", "advanced")]
            out.append({key: k, "projects": len(rs), "mw_reported": _mw(rs),
                        "mw_reported_n": sum(1 for x in rs if num(x.get("capacity_mw"))),
                        "mw_est_unreported": _mw(rs, "capacity_mw_est"),
                        "phase_pending": sum(1 for x in rs if x["stage_group"] == "pending"),
                        "phase_advancing": sum(1 for x in rs if x["stage_group"] == "advancing"),
                        "phase_blocked": sum(1 for x in rs if x["stage_group"] == "blocked"),
                        "blocked_confirmed": sum(1 for x in rs if x["outcome_group"] == "blocked"),
                        "block_rate_decided": _rate(sum(1 for x in dec if x["outcome_group"] == "blocked"), len(dec)),
                        "with_opposition": sum(1 for x in rs if opp(x))})
        out.sort(key=lambda x: -x["projects"])
        return out[:top] if top else out

    def block_rate(sel):
        d = [r for r in rows if sel(r) and r["outcome_group"] in ("blocked", "advanced")]
        return _rate(sum(1 for r in d if r["outcome_group"] == "blocked"), len(d))

    ann = Counter()
    for r in rows:
        a = str(r.get("announced_date") or "")
        if re.match(r"^\d{4}-\d{1,2}", a):
            y, m = a.split("-")[:2]
            ann[f"{y}-Q{(int(m) - 1) // 3 + 1}"] += 1
    cov = lambda col: round(sum(1 for r in rows if str(r.get(col) or "").strip()) / n, 3) if n else 0

    recent = []
    if events:
        cutoff = date.fromisoformat(today).toordinal() - 30
        names = {r["project_id"]: r["name"] for r in rows}
        for e in events:
            try:
                if date.fromisoformat(e["seen_date"]).toordinal() < cutoff:
                    continue
            except ValueError:
                continue
            if e["event_type"] in ("first_seen", "phase_change", "removed", "reappeared"):
                recent.append({"seen_date": e["seen_date"], "project_id": e["project_id"],
                               "name": names.get(e["project_id"], ""),
                               "event": e["event_type"], "from": e["old_value"],
                               "to": e["new_value"]})
        recent.sort(key=lambda x: x["seen_date"], reverse=True)

    deciles = Counter(r["county_restriction_decile"] for r in rows if r["county_restriction_decile"])
    return {
        "_generated": today,
        "_note": ("Descriptive shares with their denominators. Decided means a "
                  "terminal lifecycle outcome (blocked_confirmed, restricted_conditional, "
                  "advanced_confirmed); block rates are observed among those, not effects. Estimated MW is "
                  "an acreage-based range for projects reporting no capacity and is "
                  "never added into reported totals."),
        "totals": {
            "projects": n, "states": len({r["state"] for r in rows}),
            "mw_reported": _mw(rows),
            "mw_reported_projects": sum(1 for r in rows if num(r.get("capacity_mw"))),
            "mw_est_unreported": _mw(rows, "capacity_mw_est"),
            "mw_est_unreported_low": _mw(rows, "capacity_mw_est_low"),
            "mw_est_unreported_high": _mw(rows, "capacity_mw_est_high"),
            "mw_est_projects": sum(1 for r in rows if r["capacity_basis"] == "estimate_from_acreage"),
            "acres": _mw(rows, "size_acres"),
            "project_cost_usd": _mw(rows, "project_cost_usd"),
            "project_cost_projects": sum(1 for r in rows if num(r.get("project_cost_usd"))),
            "decided": sum(1 for r in rows if r["outcome_group"] in ("blocked", "advanced")),
            "blocked_confirmed": sum(1 for r in rows if r["outcome_group"] == "blocked"),
            "reclassified_by_source": sum(1 for r in rows if r.get("source_reclassified_from")),
            "reclassified_restored": sum(1 for r in rows if r.get("source_reclassified_from")
                                         and r["phase"] == r["source_reclassified_from"]),
            "phase_blocked": sum(1 for r in rows if r["stage_group"] == "blocked"),
            "phase_advancing": sum(1 for r in rows if r["stage_group"] == "advancing"),
            "phase_pending": sum(1 for r in rows if r["stage_group"] == "pending"),
            "with_opposition": sum(1 for r in rows if opp(r)),
        },
        "capacity_estimate": ratio and {"mw_per_acre_median": round(ratio["median"], 3),
                                        "q25": round(ratio["q25"], 3),
                                        "q75": round(ratio["q75"], 3), "n": ratio["n"]},
        "funnel": sorted(group("phase"), key=lambda x: PHASE_ORDER.index(x["phase"])
                         if x["phase"] in PHASE_ORDER else 99),
        "by_region": group("grid_region"),
        "by_state": group("state", top=20),
        "by_developer": [d for d in group("developer", top=30, min_n=2)
                         if not re.search(r"undisclosed|unknown|^multiple", d["developer"], re.I)][:25],
        "by_screen_tier": sorted(group("screen_tier"), key=lambda x: TIERS.index(x["screen_tier"])
                                 if x["screen_tier"] in TIERS else 99),
        "outcomes": {
            "block_rate_all_decided": block_rate(lambda r: True),
            "block_rate_with_opposition": block_rate(opp),
            "block_rate_without_recorded_opposition": block_rate(lambda r: not opp(r)),
            "block_rate_with_lawsuit": block_rate(lambda r: r.get("has_lawsuit") == "yes"),
            "block_rate_nda_reported": block_rate(lambda r: str(r.get("nda") or "").strip() not in ("", "False", "false")),
            "block_rate_top3_county_deciles": block_rate(lambda r: (num(r.get("county_restriction_decile")) or 0) >= 8),
            "block_rate_bottom7_county_deciles": block_rate(lambda r: 0 < (num(r.get("county_restriction_decile")) or 0) < 8),
            "block_rate_colocated_generation": block_rate(lambda r: r.get("colocated_generation") == "yes"),
        },
        "announcements_by_quarter": [{"quarter": q, "projects": ann[q]} for q in sorted(ann)],
        "power": {
            "btm_power": dict(Counter(r["btm_power"] or "(not reported)" for r in rows)),
            "colocated_generation": sum(1 for r in rows if r["colocated_generation"] == "yes"),
            "planned_gas_mw_within_16km_sum": _mw(rows, "planned_gas_mw_16km"),
            "region_basis": dict(Counter(r["grid_region_basis"] or "(none)" for r in rows)),
        },
        "water": {
            "cooling_source": dict(Counter(r["cooling_source"] or "(not reported)" for r in rows)),
            "projects_in_drought_counties": sum(1 for r in rows if (num(r["county_drought_d1_pct"]) or 0) >= 20),
        },
        "county_risk": {
            "projects_by_restriction_decile": {k: deciles[k] for k in sorted(deciles, key=lambda x: int(x))},
            "projects_in_top3_deciles": sum(v for k, v in deciles.items() if int(k) >= 8),
            "mw_in_top3_deciles": _mw([r for r in rows if (num(r.get("county_restriction_decile")) or 0) >= 8]),
        },
        "corroboration": {
            "distribution": dict(sorted(Counter(r["corroboration_count"] for r in rows).items())),
            "single_source_projects": sum(1 for r in rows if r["corroboration_count"] == 1),
            "signals": dict(Counter(s for r in rows for s in r["corroboration_signals"].split("; "))),
        },
        "coverage": {c: cov(c) for c in ("capacity_mw", "size_acres", "announced_date",
                                         "project_cost_usd", "cooling_source", "btm_power",
                                         "date_online", "fips", "grid_region", "screen_tier",
                                         "county_restriction_score", "first_seen")},
        "recent_changes_30d": recent[:60],
        "source_health": {k: (history or {}).get(k) for k in
                          ("panel_start", "panel_end", "n_snapshots", "source_renumberings",
                           "source_reclassifications", "degraded_snapshots")},
        "queues": queues or {},
    }


def _pct(x):
    return "" if x is None else f"{x:.0%}"


def write_report(m, path=OUT_MD):
    t = m["totals"]
    L = ["# Proposed data center pipeline: intelligence report", "",
         f"Generated {m['_generated']} by `proposal_enrichment.py`. {m['_note']}", "",
         "## Totals", "",
         f"- {t['projects']} projects in {t['states']} states. By source phase: {t['phase_pending']} pending, "
         f"{t['phase_advancing']} advancing, {t['phase_blocked']} withdrawn or rejected. By verified lifecycle "
         f"outcome: {t['decided']} decided, {t['blocked_confirmed']} blocked_confirmed.",
         f"- Reported capacity {t['mw_reported']:,} MW across {t['mw_reported_projects']} projects. "
         f"Another {t['mw_est_projects']} report acreage but no MW; at the observed MW-per-acre "
         f"range they would add {t['mw_est_unreported_low']:,} to {t['mw_est_unreported_high']:,} MW (estimate, not included above).",
         f"- {t['with_opposition']} projects carry recorded opposition.",
         f"- {t['reclassified_by_source']} projects were relabeled \"proposed\" when the source "
         "folded its \"approved\" phase into \"proposed\" on 2026-09-22 (listed in "
         f"`source_reclassified_from`). Under Ruling 1, {t.get('reclassified_restored', 0)} keep "
         "\"approved\" on a sourced record of a final approval "
         "(`data/project_decision_dates.csv`); the rest count as pending until such a record "
         "is added. This is a data correction: decided counts reported before the relabel "
         "included all of them.", "",
         "## Outcomes among decided projects (descriptive)", "",
         "| slice | blocked | decided | share |", "|---|---|---|---|"]
    for k, v in m["outcomes"].items():
        L.append(f"| {k.replace('block_rate_', '').replace('_', ' ')} | {v['k']} | {v['n']} | "
                 f"{_pct(v['share'])} |")
    L += ["", "## By grid region", "", "| region | projects | reported MW | blocked_confirmed | share of decided |",
          "|---|---|---|---|---|"]
    for r in m["by_region"]:
        s = r["block_rate_decided"]["share"]
        L.append(f"| {r['grid_region']} | {r['projects']} | {r['mw_reported']:,} | {r['blocked_confirmed']} | "
                 f"{_pct(s)} |")
    L += ["", "## Coverage", "", "| column | share of projects |", "|---|---|"]
    L += [f"| {k} | {v:.0%} |" for k, v in m["coverage"].items()]
    sh = m.get("source_health") or {}
    if sh.get("source_renumberings"):
        L += ["", "## Source health", "",
              f"The source renumbered its ids {len(sh['source_renumberings'])} times between "
              f"{sh['panel_start']} and {sh['panel_end']}; see `project_id_rekey.py`."]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def load_inputs():
    feats = {}
    for name in ("drought", "water_use", "retail_price", "farmland", "grid_generation"):
        feats[name] = by_key(read_csv(D("features", f"{name}.csv")), "fips")
    air = read_csv(D("proposal_candidates_airpermits.csv"))
    news = read_csv(D("proposal_candidates_news.csv"))
    return {
        "proposals": read_csv(PROPOSALS),
        "fips_lookup": read_json(D("county_fips_lookup.json"), {}) or {},
        "lifecycles": read_csv(D("project_lifecycles.csv")),
        "screen": read_csv(D("site_screen.csv")),
        "history": read_csv(D("pipeline_intel_project_history.csv")),
        "county_scores": read_csv(D("county_policy_scores.csv")),
        "county_agg": read_csv(D("county_aggregate.csv")),
        "features": feats,
        "territory": read_csv(D("county_grid_territory.csv")),
        "crosswalk": load_crosswalk(),
        "planned_gen": read_csv(D("grid_planned_generation.csv")),
        "air": air, "news": news,
        "events": read_csv(D("pipeline_intel_events.csv")),
        "_queues": {
            "news_new_candidates": sum(1 for r in news if r.get("outcome") == "new_candidate"),
            "news_possible_matches": sum(1 for r in news if r.get("outcome") == "possible_match"),
            "air_permits_unmatched": sum(1 for r in air if r.get("match") == "unmatched"),
            "air_permits_matched": sum(1 for r in air if r.get("match") == "proposal_match"),
        },
    }


def run():
    inp = load_inputs()
    rows, ratio = enrich(inp)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    m = metrics(rows, ratio, read_json(D("pipeline_intel_history.json"), {}),
                read_csv(D("pipeline_intel_events.csv")), inp["_queues"])
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(m, fh, indent=1)
        fh.write("\n")
    write_report(m)
    t = m["totals"]
    print(f"proposal_enrichment: {t['projects']} projects, {t['mw_reported']:,} MW reported "
          f"(+{t['mw_est_unreported_low']:,}-{t['mw_est_unreported_high']:,} est.), "
          f"coverage {m['coverage']} -> {OUT_CSV}")


def selftest():
    checks = []

    def check(label, ok):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    props = []
    for i in range(1, 31):
        props.append({"id": str(i), "name": f"Project {i}", "state": "Pennsylvania",
                      "counties": "Luzerne County", "lat": str(41 + i * 0.001), "lon": "-75.9",
                      "phase": "proposed", "capacity_mw": str(100 * i) if i <= 25 else "",
                      "size_acres": str(50 * i), "companies": "DevCo; Partner",
                      "lastUpdated": "2026-09-01", "n_sources": "3" if i == 1 else ""})
    props[1]["phase"] = "withdrawn"
    props[2]["phase"] = "construction"
    props.append({"id": "99", "name": "Far Away", "state": "Texas", "counties": "Potter",
                  "lat": "35.2", "lon": "-101.8", "phase": "rejected", "capacity_mw": "",
                  "size_acres": "", "companies": ""})
    inp = {
        "proposals": props,
        "fips_lookup": {"luzerne|pennsylvania": "42079"},
        "lifecycles": [{"project_id": "prj_2", "project_name": "Project 2", "n_opposition_events": "4",
                        "has_lawsuit": "yes", "lifecycle_outcome": "blocked_confirmed"},
                       {"project_id": "prj_4", "project_name": "Project 4",
                        "lifecycle_outcome": "advanced_confirmed"},
                       {"project_id": "prj_5", "project_name": "Project 5",
                        "lifecycle_outcome": "blocked_unverified"},
                       {"project_id": "prj_3", "project_name": "Totally Different Name",
                        "n_opposition_events": "9"}],
        "county_scores": [{"fips": "42079", "calibrated_score": "0.41", "score_decile": "10"}],
        "crosswalk": load_crosswalk(),
        "planned_gen": [{"plant_name": "Gas Peaker", "lat": "41.002", "lon": "-75.9",
                         "planned_mw": "300", "primary_fuel": "gas", "dc_named": "no"}],
        "air": [{"match": "proposal_match", "match_project_id": "prj_1", "registry_id": "1100"}],
        "news": [{"outcome": "corroborates", "match_project_id": "prj_1"}],
    }
    rows, ratio = enrich(inp, today="2026-09-28")
    by = {r["project_id"]: r for r in rows}
    check("every project gets a row", len(rows) == 31)
    check("FIPS resolves from county and state", by["prj_1"]["fips"] == "42079")
    check("the crosswalk places Pennsylvania in PJM by state default",
          by["prj_1"]["grid_region"] == "PJM" and by["prj_1"]["grid_region_basis"] == "state_default")
    check("a Panhandle county override lands in SPP",
          by["prj_99"]["grid_region"] == "SPP" and by["prj_99"]["grid_region_basis"] == "county_override")
    check("a reported MW is never replaced", by["prj_1"]["capacity_basis"] == "reported"
          and by["prj_1"]["capacity_mw_est"] == "")
    check("an unreported MW with acreage gets an estimate range",
          by["prj_26"]["capacity_basis"] == "estimate_from_acreage"
          and by["prj_26"]["capacity_mw_est_low"] <= by["prj_26"]["capacity_mw_est"]
          <= by["prj_26"]["capacity_mw_est_high"])
    check("no MW and no acres stays unknown", by["prj_99"]["capacity_basis"] == "unknown")
    check("lifecycle joins when the name agrees", by["prj_2"]["has_lawsuit"] == "yes")
    check("lifecycle does NOT join when the id matches but the name does not",
          by["prj_3"]["n_opposition_events"] == "")
    check("planned gas within reach is summed", by["prj_1"]["planned_gas_mw_16km"] == 300)
    check("gas within 5 km is flagged co-located", by["prj_1"]["colocated_generation"] == "yes")
    check("far-away projects see none", by["prj_99"]["planned_gen_mw_16km"] == 0)
    check("corroboration counts independent signals",
          by["prj_1"]["corroboration_count"] == 5
          and "federal_air_permit" in by["prj_1"]["corroboration_signals"])
    check("a single-source project counts one", by["prj_99"]["corroboration_count"] == 1)
    check("county restriction decile joins", by["prj_1"]["county_restriction_decile"] == "10")
    check("clusters are counted", by["prj_1"]["projects_within_25km"] == 29)
    check("stage groups follow phase", by["prj_2"]["stage_group"] == "blocked"
          and by["prj_3"]["stage_group"] == "advancing")
    check("developer portfolio size", by["prj_1"]["developer_projects"] == 30)
    m = metrics(rows, ratio, today="2026-09-28")
    check("reported and estimated MW are kept apart",
          m["totals"]["mw_reported"] == sum(100 * i for i in range(1, 26))
          and m["totals"]["mw_est_unreported"] > 0)
    check("decided is terminal lifecycle outcomes only, never raw phase or unverified",
          m["outcomes"]["block_rate_all_decided"] == {"n": 2, "k": 1, "share": 0.5})
    check("an unverified outcome is undecided", by["prj_5"]["outcome_group"] == "undecided")
    check("the funnel is ordered by stage", [f["phase"] for f in m["funnel"]][:2] == ["proposed", "construction"])
    check("coverage is reported per column", 0 < m["coverage"]["capacity_mw"] < 1)
    # Ruling 1: keep "approved" only on in-repo evidence of a final approval.
    import project_resolution as PR

    def _pr(pid, phase):
        return {"project_id": pid, "phase": phase, "raw": {},
                "lifecycle_outcome": PR.PHASE_TO_LIFECYCLE[phase]}
    prs = [_pr("prj_10", "proposed"), _pr("prj_11", "proposed"), _pr("prj_12", "proposed"),
           _pr("prj_13", "construction"), _pr("prj_14", "proposed")]
    dates = {"prj_10": {"decision_date_source": "County board 3-2 rezoning approval vote"},
             "prj_12": {"decision_date_source": "Developer withdrawal before the vote"},
             "prj_14": {"decision_date_source": "Approval voided by court for defective notice"}}
    rc = {p: "approved" for p in ("prj_10", "prj_11", "prj_12", "prj_13", "prj_14")}
    restored, pending = PR.apply_ruling_1(prs, rc, dates)
    check("ruling 1: a relabeled project with a sourced approval keeps approved",
          restored == ["prj_10"] and prs[0]["phase"] == "approved"
          and prs[0]["lifecycle_outcome"] == "advanced_confirmed")
    check("ruling 1: no decision record, a withdrawal or a voided approval stays pending",
          pending == ["prj_11", "prj_12", "prj_14"]
          and all(p["lifecycle_outcome"] == "pending" for p in (prs[1], prs[2], prs[4])))
    check("ruling 1: a project the source has since advanced is left alone",
          prs[3]["phase"] == "construction")
    inp2 = dict(inp, events=[{"project_id": "prj_7", "event_type": "source_reclassified",
                              "old_value": "approved"},
                             {"project_id": "prj_8", "event_type": "source_reclassified",
                              "old_value": "approved"}],
                history=[{"project_id": f"prj_{i}", "name": f"Project {i}",
                          "phase_path": "approved > proposed (reclassified from approved)"}
                         for i in (7, 8)],
                lifecycles=inp["lifecycles"] + [
                    {"project_id": "prj_7", "project_name": "Project 7", "phase": "approved",
                     "lifecycle_outcome": "advanced_confirmed"},
                    {"project_id": "prj_8", "project_name": "Project 8", "phase": "proposed",
                     "lifecycle_outcome": "pending"}])
    rows2, ratio2 = enrich(inp2, today="2026-09-28")
    by2 = {r["project_id"]: r for r in rows2}
    t2 = metrics(rows2, ratio2, today="2026-09-28")["totals"]
    check("ruling 1: enrichment shows the restored phase and counts it decided",
          by2["prj_7"]["phase"] == "approved" and by2["prj_7"]["stage_group"] == "advancing"
          and by2["prj_7"]["outcome_group"] == "advanced")
    check("ruling 1: an unevidenced relabel stays proposed and undecided",
          by2["prj_8"]["phase"] == "proposed" and by2["prj_8"]["outcome_group"] == "undecided")
    check("ruling 1: the report counts relabeled and restored projects",
          t2["reclassified_by_source"] == 2 and t2["reclassified_restored"] == 1
          and t2["decided"] == m["totals"]["decided"] + 1)
    check("same_project tolerates a suffix", same_project("Project Sail", "Project Sail Phase 2"))
    check("same_project rejects a different project", not same_project("Project Delta", "Armory Innovation Data Center"))
    k = sum(checks)
    print(f"\n{k}/{len(checks)} checks passed")
    return 0 if k == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="Proposed-project enrichment and pipeline analytics")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    run()


if __name__ == "__main__":
    main()
