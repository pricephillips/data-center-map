"""
control_group.py — Phase 2 of the predictive modeling roadmap.

The opposition tracker is a selection-biased sample: it contains only projects
that faced opposition. To quantify opposition's marginal impact, every opposed
project needs unopposed comparables. This module builds that control group
from sources already in the repository.

Additive only. Reads existing files, writes three NEW files:

  data/baseline_universe.csv   unified registry of U.S. data center developments
                               with opposed/control designation and exclusions
  data/matched_controls.csv    k nearest unopposed comparables per opposed
                               project, with full covariate audit trail
  data/control_group_notes.md  documented limitations and assumptions
                               (defensibility layer — read before any use)

Baseline sources (strongest control tier first):
  proposals_unopposed  proposals.csv rows with zero linked opposition events —
                       contemporaneous proposals, same lifecycle universe
  ai_centers           major AI sites (ai_centers.csv) not in the opposed set
  atlas                OSTI IM3 Open Source Atlas built facilities (atlas.csv) —
                       weakest tier: survivorship-biased, capacity mostly absent

Defensibility rules honored:
  - "Unopposed" strictly means "no opposition recorded in the tracker" —
    absence of evidence, stated as such everywhere, never as verified absence.
  - Contamination exclusion: a baseline record in the same county as, or
    within CONTAMINATION_KM of, any opposed project is EXCLUDED from the
    control pool (county-level actions like moratoriums expose the whole
    county). Exclusions are flagged with reasons, never silently dropped.
  - Every match records both sides' covariate values and the distance score,
    so any pairing can be audited or manually rejected.
  - No scorekeeping vocabulary in any output.
  - Nothing here estimates cost or effect. This is dataset construction only;
    inference is Phase 3+ and requires the limitations in the notes file.

Run from repo root:  python3 control_group.py
Depends on data/project_lifecycles.csv (run project_resolution.py first).
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

LIFECYCLES_CSV = P("data", "project_lifecycles.csv")
PROPOSALS_CSV = P("data", "proposals.csv")
ATLAS_CSV = P("atlas.csv")
AI_CENTERS_CSV = P("ai_centers.csv")
COUNTY_VOTES_JSON = P("data", "county_votes.json")
FIPS_LOOKUP_JSON = P("data", "county_fips_lookup.json")

OUT_UNIVERSE = P("data", "baseline_universe.csv")
OUT_MATCHES = P("data", "matched_controls.csv")
OUT_NOTES = P("data", "control_group_notes.md")

K_CONTROLS = 3               # comparables per opposed project
CONTAMINATION_KM = 15.0      # baseline within this of an opposed site is excluded
MARGIN_CALIPER = 0.10        # max |county margin difference| for calipered stages
OUT_OF_STATE_PENALTY = 1.5   # added to distance when no in-state candidate pool
TIER_PENALTY = {"proposals_unopposed": 0.0, "ai_centers": 0.35, "atlas": 0.7}

STATE_ABBREV = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
FULL_TO_ABBREV = {v.lower(): k for k, v in STATE_ABBREV.items()}


def norm_state(v: str) -> str:
    v = (v or "").strip()
    if len(v) == 2 and v.upper() in STATE_ABBREV:
        return v.upper()
    return FULL_TO_ABBREV.get(v.lower(), "")


_COUNTY_SUFFIX = re.compile(r"\s+(county|parish|borough)$", re.IGNORECASE)


def norm_county(v: str) -> str:
    return _COUNTY_SUFFIX.sub("", (v or "").strip().lower())


def parse_float(v) -> float | None:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def parse_coords(lat_v, lon_v) -> tuple[float | None, float | None]:
    """Coordinate pair; (0, 0) is a known placeholder, treated as missing."""
    lat, lon = parse_float(lat_v), parse_float(lon_v)
    if lat == 0.0 and lon == 0.0:
        return None, None
    return lat, lon


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


class Covariates:
    """FIPS + 2024 presidential margin per (county, state)."""

    def __init__(self):
        self.fips = json.load(open(FIPS_LOOKUP_JSON, encoding="utf-8"))
        self.votes = json.load(open(COUNTY_VOTES_JSON, encoding="utf-8"))

    def lookup(self, county: str, state_abbrev: str) -> tuple[str, float | None]:
        if not county or not state_abbrev:
            return "", None
        full = STATE_ABBREV.get(state_abbrev, "").lower()
        for key in (f"{county} county|{full}", f"{county}|{full}"):
            fips = self.fips.get(key)
            if fips:
                margin = (self.votes.get(fips) or {}).get("2024")
                return fips, margin
        return "", None


def build_universe(cov: Covariates):
    """Assemble baseline records + opposed project records into one registry."""
    life = load_csv(LIFECYCLES_CSV)
    opposed = [r for r in life if int(r["n_opposition_events"] or 0) > 0]
    opposed_ids = {r["project_id"] for r in opposed}
    opposed_sites = []           # (lat, lon) of opposed projects, for contamination
    opposed_counties = set()     # (county, state)

    props_by_id = {f"prj_{r['id'].strip()}": r for r in load_csv(PROPOSALS_CSV)}

    records = []

    def add(source, uid, name, operator, state, county, lat, lon, mw, sqft,
            opposed_flag, lifecycle_row=None):
        st = norm_state(state)
        co = norm_county(county)
        fips, margin = cov.lookup(co, st)
        records.append({
            "universe_id": uid, "source": source, "name": name.strip(),
            "operator": (operator or "").strip(), "state": st, "county": co,
            "fips": fips,
            "county_margin_2024": f"{margin:.4f}" if margin is not None else "",
            "lat": lat if lat is not None else "", "lon": lon if lon is not None else "",
            "capacity_mw": mw if mw is not None else "",
            "sqft": sqft if sqft is not None else "",
            "opposed_flag": "yes" if opposed_flag else "no",
            "lifecycle_outcome": (lifecycle_row or {}).get("lifecycle_outcome", ""),
            "decided": (lifecycle_row or {}).get("decided", ""),
            "n_opposition_events": (lifecycle_row or {}).get("n_opposition_events", ""),
            "exclusion_reason": "",
        })
        return records[-1]

    # --- opposed projects (the treatment side) ---
    for r in life:
        pid = r["project_id"]
        p = props_by_id.get(pid, {})
        is_opposed = pid in opposed_ids
        lat, lon = parse_coords(p.get("lat"), p.get("lon"))
        rec = add(
            "proposals_opposed" if is_opposed else "proposals_unopposed",
            pid, r["project_name"], p.get("companies", ""), r["state"], r["county"],
            lat, lon, parse_float(p.get("capacity_mw")), None,
            opposed_flag=is_opposed, lifecycle_row=r,
        )
        if is_opposed:
            if lat is not None and lon is not None:
                opposed_sites.append((lat, lon))
            if rec["county"] and rec["state"]:
                opposed_counties.add((rec["county"], rec["state"]))

    # --- ai_centers baseline ---
    for i, r in enumerate(load_csv(AI_CENTERS_CSV)):
        if (r.get("Country") or "").strip().lower() not in {"united states", "usa", "us", ""}:
            continue
        # crude state from address tail; ai_centers has no state column
        addr = r.get("Address", "")
        st = ""
        m = re.search(r",\s*([A-Za-z ]+?)(?:\s+\d{5})?\s*$", addr)
        if m:
            st = norm_state(m.group(1).strip()) or norm_state(m.group(1).strip().split()[-1])
        aic_lat, aic_lon = parse_coords(r.get("lat"), r.get("lon"))
        add("ai_centers", f"aic_{i:04d}", r.get("Name", ""), r.get("Owner", ""),
            st, "", aic_lat, aic_lon,
            parse_float(r.get("Current power (MW)")), None, opposed_flag=False)

    # --- atlas baseline ---
    for i, r in enumerate(load_csv(ATLAS_CSV)):
        atl_lat, atl_lon = parse_coords(r.get("lat"), r.get("lon"))
        add("atlas", f"atl_{i:05d}", r.get("name", ""), r.get("operator", ""),
            r.get("state", ""), r.get("county", ""),
            atl_lat, atl_lon,
            None, parse_float(r.get("sqft")), opposed_flag=False)

    # --- contamination exclusions on the control pool ---
    for rec in records:
        if rec["opposed_flag"] == "yes" or rec["source"] == "proposals_opposed":
            continue
        if rec["county"] and (rec["county"], rec["state"]) in opposed_counties:
            rec["exclusion_reason"] = "county_shared_with_opposed_project"
            continue
        lat, lon = parse_float(rec["lat"]), parse_float(rec["lon"])
        if lat is None or lon is None:
            rec["exclusion_reason"] = "no_coordinates"
            continue
        for olat, olon in opposed_sites:
            if haversine_km(lat, lon, olat, olon) <= CONTAMINATION_KM:
                rec["exclusion_reason"] = f"within_{int(CONTAMINATION_KM)}km_of_opposed_project"
                break

    return records, [r for r in life if r["project_id"] in opposed_ids]


def match_controls(records: list[dict], opposed_life: list[dict]):
    """k-nearest unopposed comparables per opposed project.

    Selection uses a relaxation ladder so the closest-comparable definition
    is as strict as the pool allows, and every match records which stage
    produced it (`match_relaxation`):

      1 state+tercile+caliper  in-state, same margin tercile, |margin diff| <= caliper
      2 state+caliper          in-state, |margin diff| <= caliper
      3 state                  in-state, no margin constraint
      4 national+caliper       any state, |margin diff| <= caliper (penalized)
      5 national               any state (penalized)

    The caliper directly bounds per-pair margin disparity, which is what the
    balance diagnostic (control_comparison.py) measures.
    """
    pool = [r for r in records if r["opposed_flag"] == "no"
            and not r["exclusion_reason"] and r["source"] != "proposals_opposed"]
    by_state = defaultdict(list)
    for r in pool:
        by_state[r["state"]].append(r)
    by_id = {r["universe_id"]: r for r in records}

    # margin terciles over the eligible pool (the sampling frame for controls)
    pool_margins = sorted(m for m in (parse_float(r["county_margin_2024"]) for r in pool)
                          if m is not None)
    if len(pool_margins) >= 3:
        t1 = pool_margins[len(pool_margins) // 3]
        t2 = pool_margins[2 * len(pool_margins) // 3]
    else:
        t1 = t2 = None

    def tercile(margin: float | None) -> str:
        if margin is None or t1 is None:
            return ""
        return "T1" if margin <= t1 else ("T2" if margin <= t2 else "T3")

    def dist(op: dict, ct: dict) -> tuple[float, str]:
        parts, notes = [], []
        m1, m2 = parse_float(op["county_margin_2024"]), parse_float(ct["county_margin_2024"])
        if m1 is not None and m2 is not None:
            parts.append(abs(m1 - m2))
            notes.append("margin")
        w1, w2 = parse_float(op["capacity_mw"]), parse_float(ct["capacity_mw"])
        if w1 and w2 and w1 > 0 and w2 > 0:
            parts.append(abs(math.log10(w1) - math.log10(w2)))
            notes.append("log_mw")
        base = sum(parts) / len(parts) if parts else 1.0
        if not parts:
            notes.append("no_shared_covariates")
        base += TIER_PENALTY[ct["source"]]
        return base, "+".join(notes)

    def stages(op: dict):
        """Yield (stage_label, candidates, penalized) strictest-first."""
        om = parse_float(op["county_margin_2024"])
        ot = tercile(om)
        in_state = by_state.get(op["state"], [])

        def calipered(cands):
            if om is None:
                return []
            out = []
            for c in cands:
                cm = parse_float(c["county_margin_2024"])
                if cm is not None and abs(om - cm) <= MARGIN_CALIPER:
                    out.append(c)
            return out

        st_cal = calipered(in_state)
        if ot:
            yield ("state+tercile+caliper",
                   [c for c in st_cal if tercile(parse_float(c["county_margin_2024"])) == ot],
                   False)
        yield ("state+caliper", st_cal, False)
        # margin-comparable out-of-state controls are preferred over
        # margin-distant in-state ones: the caliper is what the balance
        # diagnostic measures, and out-of-state picks stay penalized+labeled
        yield ("national+caliper", calipered(pool), True)
        yield ("state", in_state, False)
        yield ("national", pool, True)

    rows = []
    for lr in opposed_life:
        op = by_id.get(lr["project_id"])
        if op is None:
            continue
        chosen: list[tuple[float, str, dict, str]] = []
        seen: set[str] = set()
        for stage, cands, penalized in stages(op):
            if len(chosen) >= K_CONTROLS:
                break
            scored = []
            for ct in cands:
                if ct["universe_id"] in seen:
                    continue
                d, basis = dist(op, ct)
                if penalized and ct["state"] != op["state"]:
                    d += OUT_OF_STATE_PENALTY
                scored.append((d, basis, ct, stage))
            scored.sort(key=lambda x: x[0])
            for entry in scored[:K_CONTROLS - len(chosen)]:
                chosen.append(entry)
                seen.add(entry[2]["universe_id"])
        for rank, (d, basis, ct, stage) in enumerate(chosen, 1):
            rows.append({
                "opposed_project_id": op["universe_id"],
                "opposed_project_name": op["name"],
                "opposed_state": op["state"],
                "opposed_lifecycle_outcome": op["lifecycle_outcome"],
                "opposed_decided": op["decided"],
                "opposed_capacity_mw": op["capacity_mw"],
                "opposed_county_margin_2024": op["county_margin_2024"],
                "control_rank": rank,
                "control_universe_id": ct["universe_id"],
                "control_name": ct["name"],
                "control_source": ct["source"],
                "control_state": ct["state"],
                "control_capacity_mw": ct["capacity_mw"],
                "control_county_margin_2024": ct["county_margin_2024"],
                "match_distance": f"{d:.4f}",
                "match_basis": basis,
                "match_relaxation": stage,
                "match_scope": "in_state" if ct["state"] == op["state"] else "national_fallback",
            })
    return rows


NOTES = """# Control Group Construction — Limitations & Assumptions

Generated by `control_group.py`. Read fully before any analytical or
client-facing use of `baseline_universe.csv` or `matched_controls.csv`.

## What "unopposed" means here
A control is a development with **no opposition recorded in the tracker**.
This is absence of evidence, not verified absence of opposition. Untracked
local opposition may exist. All downstream language must say "no recorded
opposition," never "faced no opposition."

## Control tiers (strongest first)
1. **proposals_unopposed** — contemporaneous proposals with zero linked
   opposition events. Same lifecycle universe as the opposed set; best tier.
2. **ai_centers** — major AI sites not in the opposed set. Small n; capacity
   data strong; selection skews to very large projects.
3. **atlas** — built, operating facilities. **Survivorship-biased**: these
   completed by definition, and most predate the current opposition wave.
   Capacity is unavailable (sqft only). Weakest tier; a distance penalty is
   applied and the tier is recorded on every match for sensitivity analysis.

## Contamination exclusions
Baseline records in the same county as any opposed project, or within 15 km
of one, are excluded from the control pool (county-level instruments such as
moratoriums expose the entire county). Exclusions are flagged with reasons in
`baseline_universe.csv`, not dropped.

## Matching
k=3 nearest comparables per opposed project, selected through a relaxation
ladder recorded per match in `match_relaxation` (strictest first):
in-state + same margin tercile + caliper (|margin diff| <= 0.10), then
in-state + caliper, national + caliper, in-state uncalipered, national.
Margin-comparable out-of-state controls are deliberately preferred over
margin-distant in-state ones; out-of-state matches are penalized and
labeled (`match_scope`), and the share of out-of-state matches is a
sensitivity axis alongside control tier. Distance covariates: county 2024
presidential margin, log10 capacity (when both sides have MW), plus the
tier penalty. Matches with `match_basis = no_shared_covariates` rest on
state/tier alone and should be down-weighted or manually reviewed.

## What this does NOT support yet
- No causal or cost claims. This is dataset construction (roadmap Phase 2).
- Delay comparisons additionally require verified decision dates
  (see `data/project_decision_dates.csv` workflow).
- Client-facing deliverables must not cite matched-control comparisons until
  Phase 3 validation (calibration + sensitivity across control tiers).
"""


def leak_audit(paths: list[str]) -> list[str]:
    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = []
    for p in paths:
        for i, line in enumerate(open(p, encoding="utf-8"), 1):
            if pat.search(line):
                hits.append(f"{p}:{i}: {line.strip()[:90]}")
    return hits


def main() -> int:
    if not os.path.exists(LIFECYCLES_CSV):
        print("ERROR: data/project_lifecycles.csv missing — run project_resolution.py first")
        return 1
    cov = Covariates()
    records, opposed_life = build_universe(cov)

    universe_cols = ["universe_id", "source", "name", "operator", "state",
                     "county", "fips", "county_margin_2024", "lat", "lon",
                     "capacity_mw", "sqft", "opposed_flag", "lifecycle_outcome",
                     "decided", "n_opposition_events", "exclusion_reason"]
    with open(OUT_UNIVERSE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=universe_cols)
        w.writeheader()
        w.writerows(records)

    matches = match_controls(records, opposed_life)
    match_cols = ["opposed_project_id", "opposed_project_name", "opposed_state",
                  "opposed_lifecycle_outcome", "opposed_decided",
                  "opposed_capacity_mw", "opposed_county_margin_2024",
                  "control_rank", "control_universe_id", "control_name",
                  "control_source", "control_state", "control_capacity_mw",
                  "control_county_margin_2024", "match_distance", "match_basis",
                  "match_relaxation", "match_scope"]
    with open(OUT_MATCHES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=match_cols)
        w.writeheader()
        w.writerows(matches)

    with open(OUT_NOTES, "w", encoding="utf-8") as fh:
        fh.write(NOTES)

    pool = [r for r in records if r["opposed_flag"] == "no"
            and not r["exclusion_reason"] and r["source"] != "proposals_opposed"]
    excl = [r for r in records if r["exclusion_reason"]]
    from collections import Counter
    print(f"universe: {len(records)} records "
          f"({sum(1 for r in records if r['opposed_flag']=='yes')} opposed)")
    print(f"eligible control pool: {len(pool)}  by tier: "
          f"{dict(Counter(r['source'] for r in pool))}")
    print(f"excluded: {len(excl)}  reasons: "
          f"{dict(Counter(r['exclusion_reason'] for r in excl))}")
    print(f"matches: {len(matches)} rows for "
          f"{len({m['opposed_project_id'] for m in matches})} opposed projects "
          f"(k={K_CONTROLS})")
    scoped = Counter(m["match_scope"] for m in matches)
    print(f"match scope: {dict(scoped)}")

    hits = leak_audit([OUT_UNIVERSE, OUT_MATCHES, OUT_NOTES])
    if hits:
        print("LEAK AUDIT FAILED:")
        for h in hits[:20]:
            print("  " + h)
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
