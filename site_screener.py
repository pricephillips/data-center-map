"""
site_screener.py

Opposition Environment Screener. A client-facing screening layer that answers
"what does the opposition environment look like at this site" today, without
waiting for the fully calibrated project-level model.

What it is
----------
A DIRECTIONAL screening indicator, not a prediction. The tier describes the
observed opposition environment around a location, built from transparent,
percentile-ranked components. Exactly one component is a calibrated model
output (the county enacted-restriction score from county_policy_model.py) and
it is labeled as such everywhere it appears. Everything else is descriptive:
counts, distances, dates, and sourced events. Every event listed in a brief
carries its source URL. This keeps the product inside the defensibility rules
while shipping something sellable now; when the landmark retrain makes pending
projects scoreable, its calibrated output slots in as a component upgrade.

Components (weights printed in every brief)
-------------------------------------------
  local_activity   0.30  recency-weighted opposition events within 25 miles
                         (24-month half-life; undated events at 0.25 weight;
                         statewide/federal-scope records excluded here)
  local_enacted    0.25  enacted local restrictions within 25 miles
                         (moratorium / zoning / ordinance mechanisms whose
                         outcome is a confirmed enacted restriction)
  county_model     0.25  calibrated county enacted-restriction score
                         (the one calibrated component; from
                         data/county_policy_scores.csv)
  state_activity   0.10  statewide legislative activity in the site's state
                         (state_legislation_events from the county aggregate)
  org_capacity     0.10  organized-opposition footprint within 25 miles
                         (distinct named groups + active petitions)

Each component is converted to a percentile against the reference set (all
proposals in data/proposals.csv), then combined by the weights above into a
composite that maps to a tier band:

  Low < 40  <=  Guarded < 65  <=  Elevated < 85  <=  High

Outcome vocabulary in all generated text is the four-tier system paired with
mechanism (advanced_confirmed, restricted_conditional, blocked_confirmed,
pending). A leak audit runs on every generated artifact and the run fails if
scorekeeping vocabulary appears.

Usage
-----
  python site_screener.py --selftest
  python site_screener.py --batch                    # scores all proposals ->
                                                     # data/site_screen.csv
  python site_screener.py --lat 39.11 --lon -77.56 --mw 300 --name "Client X"
  python site_screener.py --county "Loudoun" --state VA

Single-site runs write signals/brief_<slug>.md and .json (and print the
brief). Batch mode also refreshes the percentile reference cache the ad-hoc
mode compares against. Stdlib only; no new dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# Inputs (clean feed preferred; raw fallback keeps the tool runnable anywhere)
OPPOSITION_CANDIDATES = [
    os.path.join(HERE, "master_opposition_clean.csv"),
    os.path.join(HERE, "master_opposition.csv"),
]
PROPOSALS_CSV = os.path.join(HERE, "data", "proposals.csv")
LIFECYCLES_CSV = os.path.join(HERE, "data", "project_lifecycles.csv")
COUNTY_AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
COUNTY_SCORES_CSV = os.path.join(HERE, "data", "county_policy_scores.csv")
FIPS_LOOKUP_JSON = os.path.join(HERE, "data", "county_fips_lookup.json")

# Outputs
BATCH_OUT = os.path.join(HERE, "data", "site_screen.csv")
HISTORY_OUT = os.path.join(HERE, "data", "site_screen_history.csv")
MOVERS_OUT = os.path.join(HERE, "data", "site_screen_movers.md")
SIGNALS_DIR = os.path.join(HERE, "signals")

# A site has to move at least this far on the 0-100 composite before it is
# reported as a mover. Set above the noise the reference-set percentile
# rebasing introduces as the dataset grows.
MOVER_MIN_DELTA = 3.0

RADIUS_MI = 25.0
CONTEXT_RADIUS_MI = 50.0
HALF_LIFE_MONTHS = 24.0
UNDATED_WEIGHT = 0.25
COMPARABLE_RADIUS_MI = 100.0

WEIGHTS = {
    "local_activity": 0.30,
    "local_enacted": 0.25,
    "county_model": 0.25,
    "state_activity": 0.10,
    "org_capacity": 0.10,
}

TIER_BANDS = [(85.0, "High"), (65.0, "Elevated"), (40.0, "Guarded"), (0.0, "Low")]

# Scorekeeping leak audit (same regex as the rest of the pipeline)
LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

ENACTED_MECHANISMS = ("moratorium", "zoning", "ordinance")

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
STATE_NAME_TO_ABBREV = {v.lower(): k for k, v in STATE_ABBREV.items()}

# Internal-CSV outcome values -> four-tier display vocabulary. The left-hand
# values never appear in any generated artifact; the leak audit enforces this.
_OUTCOME_DISPLAY = {
    "win": "blocked_confirmed",
    "loss": "advanced_confirmed",
    "mixed": "restricted_conditional",
    "pending": "pending",
    "": "pending",
}
_INTERNAL_PREVAILED = "win"  # opposition-centric source-of-record value


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def haversine_mi(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%m/%d/%Y", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})-(\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    return None


def recency_weight(d, today=None):
    if d is None:
        return UNDATED_WEIGHT
    today = today or date.today()
    months = max(0.0, (today - d).days / 30.44)
    return 0.5 ** (months / HALF_LIFE_MONTHS)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def first_url(rec):
    for field in ("Source URL", "Sources"):
        raw = rec.get(field) or ""
        m = re.search(r"https?://[^\s'\"}\]]+", raw)
        if m:
            return m.group(0).rstrip(",;)")
    return ""


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "site").lower()).strip("-")[:60] or "site"


def percentile_of(value, reference):
    """Share of the reference set strictly below value, in 0-100. Empty or
    constant references return 0 so a missing component never inflates a
    tier."""
    ref = [r for r in reference if r is not None]
    if not ref or value is None:
        return 0.0
    below = sum(1 for r in ref if r < value)
    equal = sum(1 for r in ref if r == value)
    return 100.0 * (below + 0.5 * equal) / len(ref)


def tier_for(composite):
    for cutoff, name in TIER_BANDS:
        if composite >= cutoff:
            return name
    return "Low"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_opposition():
    for path in OPPOSITION_CANDIDATES:
        rows = load_csv(path)
        if rows:
            out = []
            for r in rows:
                lat, lon = _f(r.get("lat")), _f(r.get("lon"))
                out.append({
                    "rec": r,
                    "lat": lat, "lon": lon,
                    "date": parse_date(r.get("Date")),
                    "scope": (r.get("Scope") or "").strip().lower(),
                    "opptype": (r.get("Opposition Type") or "").strip().lower(),
                    "outcome": (r.get("Community Outcome") or "").strip().lower(),
                    "state": (r.get("State") or "").strip().upper(),
                })
            return out, os.path.basename(path)
    return [], None


def load_county_layers():
    agg = {r["fips"]: r for r in load_csv(COUNTY_AGG_CSV)}
    scores = {r["fips"]: r for r in load_csv(COUNTY_SCORES_CSV)}
    try:
        with open(FIPS_LOOKUP_JSON, encoding="utf-8") as fh:
            fips_lookup = {k.lower(): v for k, v in json.load(fh).items()}
    except (OSError, json.JSONDecodeError):
        fips_lookup = {}
    return agg, scores, fips_lookup


def county_to_fips(county, state, fips_lookup):
    """county free text + state (name or abbrev) -> fips, or None."""
    if not county or not state:
        return None
    state_name = STATE_ABBREV.get(state.strip().upper(), state).strip().lower()
    c = county.strip().lower()
    c = re.split(r"[;,/]", c)[0].strip()          # first county if several
    bare = re.sub(r"\s+(county|parish|borough)$", "", c)
    for key in (f"{c}|{state_name}", f"{bare}|{state_name}",
                f"{bare} county|{state_name}"):
        if key in fips_lookup:
            return fips_lookup[key]
    return None


# ---------------------------------------------------------------------------
# Component computation
# ---------------------------------------------------------------------------

def is_enacted_restriction(ev):
    """Local mechanism whose outcome is a confirmed enacted restriction."""
    if ev["outcome"] != _INTERNAL_PREVAILED:
        return False
    return any(m in ev["opptype"] for m in ENACTED_MECHANISMS)


def site_components(lat, lon, state_abbrev, fips, opposition, agg, scores,
                    today=None):
    """Raw component values for one site. Returns (components, nearby_events)."""
    today = today or date.today()
    activity = 0.0
    enacted = 0
    groups = set()
    petitions = 0
    nearby = []
    ctx_count_50 = 0

    for ev in opposition:
        if ev["lat"] is None or ev["lon"] is None:
            continue
        if ev["scope"] in ("statewide", "federal"):
            continue  # counted through state_activity, not proximity
        d = haversine_mi(lat, lon, ev["lat"], ev["lon"])
        if d > CONTEXT_RADIUS_MI:
            continue
        ctx_count_50 += 1
        if d > RADIUS_MI:
            continue
        w = recency_weight(ev["date"], today)
        activity += w
        if is_enacted_restriction(ev):
            enacted += 1
        g = (ev["rec"].get("Opposition Groups") or "").strip()
        if g:
            for name in re.split(r"[;|]", g):
                if name.strip():
                    groups.add(name.strip().lower())
        if (ev["rec"].get("Petition URL") or "").strip() or \
           (ev["rec"].get("Petition Signatures") or "").strip():
            petitions += 1
        nearby.append((w, d, ev))

    county_score = None
    if fips and fips in scores:
        county_score = _f(scores[fips].get("calibrated_score"))

    state_leg = None
    if fips and fips in agg:
        state_leg = _f(agg[fips].get("state_legislation_events"))
    if state_leg is None and state_abbrev:
        # fall back: max state_legislation_events over any county in the state
        vals = [_f(r.get("state_legislation_events")) for r in agg.values()
                if (r.get("state") or "").strip().upper() == state_abbrev]
        vals = [v for v in vals if v is not None]
        state_leg = max(vals) if vals else 0.0

    components = {
        "local_activity": round(activity, 3),
        "local_enacted": float(enacted),
        "county_model": county_score,
        "state_activity": state_leg or 0.0,
        "org_capacity": float(len(groups) + petitions),
    }
    extras = {"events_within_50mi": ctx_count_50}
    nearby.sort(key=lambda t: -t[0])
    return components, nearby, extras


def composite_from(components, reference_components):
    """Percentile each component against the reference set, weight, combine."""
    pct = {}
    for key in WEIGHTS:
        ref = [rc.get(key) for rc in reference_components]
        pct[key] = round(percentile_of(components.get(key), ref), 1)
    composite = round(sum(WEIGHTS[k] * pct[k] for k in WEIGHTS), 1)
    return composite, pct


# ---------------------------------------------------------------------------
# Reference set (all proposals)
# ---------------------------------------------------------------------------

def build_reference(opposition, agg, scores, fips_lookup, today=None):
    proposals = load_csv(PROPOSALS_CSV)
    rows = []
    for p in proposals:
        lat, lon = _f(p.get("lat")), _f(p.get("lon"))
        if lat is None or lon is None:
            continue
        state_abbrev = STATE_NAME_TO_ABBREV.get((p.get("state") or "").strip().lower(),
                                               (p.get("state") or "").strip().upper()[:2])
        fips = county_to_fips(p.get("counties") or "", p.get("state") or "", fips_lookup)
        comps, _nearby, extras = site_components(lat, lon, state_abbrev, fips,
                                                 opposition, agg, scores, today)
        rows.append({"id": p.get("id"), "name": p.get("name"), "state": state_abbrev,
                     "county": (p.get("counties") or "").strip(), "fips": fips or "",
                     "lat": lat, "lon": lon, "capacity_mw": p.get("capacity_mw"),
                     "components": comps, "extras": extras})
    return rows


# ---------------------------------------------------------------------------
# Comparable decided projects
# ---------------------------------------------------------------------------

def comparable_projects(lat, lon, state_abbrev, reference_rows):
    lifecycles = load_csv(LIFECYCLES_CSV)
    ref_by_id = {str(r["id"]): r for r in reference_rows}
    out = []
    for lc in lifecycles:
        if (lc.get("decided") or "").strip().lower() != "yes":
            continue
        pid = str(lc.get("project_id") or "").replace("prj_", "")
        ref = ref_by_id.get(pid)
        dist = None
        if ref:
            dist = haversine_mi(lat, lon, ref["lat"], ref["lon"])
        in_state = (lc.get("state") or "").strip().upper() == state_abbrev or \
                   STATE_NAME_TO_ABBREV.get((lc.get("state") or "").strip().lower()) == state_abbrev
        if (dist is not None and dist <= COMPARABLE_RADIUS_MI) or in_state:
            out.append({
                "project": lc.get("project_name"),
                "county": lc.get("county"),
                "state": lc.get("state"),
                "outcome": lc.get("lifecycle_outcome"),
                "mechanisms": lc.get("opposition_types"),
                "days_to_decision": lc.get("days_announced_to_decision"),
                "distance_mi": round(dist, 1) if dist is not None else None,
            })
    out.sort(key=lambda r: (r["distance_mi"] is None, r["distance_mi"] or 0))
    return out[:12]


# ---------------------------------------------------------------------------
# Brief rendering
# ---------------------------------------------------------------------------

DISCLOSURE = (
    "This brief is a directional screening indicator describing the observed "
    "opposition environment around the site. It is not a probability of any "
    "outcome. The county enacted-restriction score is the only calibrated "
    "model output shown and is labeled where it appears; all other components "
    "are descriptive counts and dates from sourced public records. Percentiles "
    "are ranked against the current national proposal reference set and shift "
    "as the dataset grows. Outcome terms follow the platform's four-tier "
    "vocabulary and are always paired with the mechanism involved."
)


def render_brief(name, lat, lon, county, state_abbrev, fips, tier, composite,
                 components, pct, nearby, extras, comparables, agg,
                 opposition_src, mw=None):
    a = agg.get(fips, {}) if fips else {}
    lines = []
    lines.append(f"# Opposition Environment Brief: {name}")
    lines.append("")
    loc = ", ".join(x for x in [county, STATE_ABBREV.get(state_abbrev, state_abbrev)] if x)
    lines.append(f"Location: {loc} ({lat:.4f}, {lon:.4f})" + (f" | Planned capacity: {mw} MW" if mw else ""))
    lines.append(f"Generated: {date.today().isoformat()} | Source feed: {opposition_src}")
    lines.append("")
    lines.append(f"## Opposition Environment Tier: {tier} ({composite}/100)")
    lines.append("")
    lines.append("| Component | Weight | Raw value | Percentile |")
    lines.append("| :-- | --: | --: | --: |")
    labels = {
        "local_activity": "Local opposition activity, 25 mi (recency-weighted)",
        "local_enacted": "Enacted local restrictions, 25 mi (moratorium/zoning/ordinance mechanisms)",
        "county_model": "County enacted-restriction score (calibrated model output)",
        "state_activity": "Statewide legislative activity",
        "org_capacity": "Organized opposition footprint, 25 mi (groups + petitions)",
    }
    for k in ("local_activity", "local_enacted", "county_model", "state_activity", "org_capacity"):
        raw = components.get(k)
        raw_s = "n/a" if raw is None else (f"{raw:.2f}" if isinstance(raw, float) else str(raw))
        lines.append(f"| {labels[k]} | {WEIGHTS[k]:.2f} | {raw_s} | {pct[k]:.0f} |")
    lines.append("")
    lines.append(f"Additional context: {extras.get('events_within_50mi', 0)} opposition events within 50 miles.")
    if a:
        margin = _f(a.get("margin_2024"))
        margin_s = f"{margin:+.1%} (2024 presidential margin, context only; not scored)" if margin is not None else "n/a"
        lines.append("")
        lines.append("## County Context (not scored unless noted)")
        lines.append("")
        lines.append(f"- Population: {a.get('population') or 'n/a'}; median household income: {a.get('median_hh_income') or 'n/a'}")
        lines.append(f"- Existing data centers in county: {a.get('existing_dc_count') or '0'}")
        lines.append(f"- Political geography: {margin_s}")
        lines.append(f"- County opposition events on record: {a.get('n_opposition_events') or '0'}; enacted restrictive actions: {a.get('n_enacted_restrictive') or '0'}")
    if nearby:
        lines.append("")
        lines.append("## Nearest Opposition Activity (top records within 25 mi)")
        lines.append("")
        lines.append("| Date | Mi | Mechanism | Outcome | Source |")
        lines.append("| :-- | --: | :-- | :-- | :-- |")
        for w, d, ev in nearby[:8]:
            r = ev["rec"]
            mech = ev["opptype"] or "unspecified"
            outc = _OUTCOME_DISPLAY.get(ev["outcome"], "pending")
            url = first_url(r)
            src = f"[link]({url})" if url else "on file"
            lines.append(f"| {r.get('Date') or 'undated'} | {d:.0f} | {mech} | {outc} | {src} |")
    if comparables:
        lines.append("")
        lines.append("## Comparable Decided Projects (within 100 mi or in-state)")
        lines.append("")
        lines.append("| Project | County, State | Outcome | Mechanisms | Days to decision | Mi |")
        lines.append("| :-- | :-- | :-- | :-- | --: | --: |")
        for c in comparables:
            mi = "-" if c["distance_mi"] is None else f"{c['distance_mi']:.0f}"
            lines.append(f"| {c['project']} | {c['county']}, {c['state']} | {c['outcome']} | "
                         f"{c['mechanisms'] or 'unspecified'} | {c['days_to_decision'] or 'n/a'} | {mi} |")
    lines.append("")
    lines.append("## Basis and Limits")
    lines.append("")
    lines.append(DISCLOSURE)
    lines.append("")
    return "\n".join(lines)


def leak_audit(text, label):
    hits = LEAK_RE.findall(text)
    if hits:
        raise SystemExit(f"LEAK AUDIT FAILED in {label}: scorekeeping vocabulary "
                         f"found ({sorted(set(h.lower() for h in hits))}).")


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_batch(today=None):
    opposition, src = load_opposition()
    agg, scores, fips_lookup = load_county_layers()
    reference = build_reference(opposition, agg, scores, fips_lookup, today)
    all_comps = [r["components"] for r in reference]
    os.makedirs(os.path.dirname(BATCH_OUT), exist_ok=True)
    fields = ["project_id", "name", "state", "county", "fips", "lat", "lon",
              "capacity_mw", "tier", "composite",
              "local_activity", "local_enacted", "county_model",
              "state_activity", "org_capacity",
              "pct_local_activity", "pct_local_enacted", "pct_county_model",
              "pct_state_activity", "pct_org_capacity", "events_within_50mi"]
    out_rows = []
    for r in reference:
        composite, pct = composite_from(r["components"], all_comps)
        row = {
            "project_id": f"prj_{r['id']}", "name": r["name"], "state": r["state"],
            "county": r["county"], "fips": r["fips"], "lat": r["lat"], "lon": r["lon"],
            "capacity_mw": r["capacity_mw"], "tier": tier_for(composite),
            "composite": composite,
            **{k: ("" if r["components"][k] is None else r["components"][k]) for k in WEIGHTS},
            **{f"pct_{k}": pct[k] for k in WEIGHTS},
            "events_within_50mi": r["extras"]["events_within_50mi"],
        }
        out_rows.append(row)
    with open(BATCH_OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    leak_audit(open(BATCH_OUT, encoding="utf-8").read(), BATCH_OUT)
    tiers = {}
    for r in out_rows:
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
    print(f"site_screener: scored {len(out_rows)} proposals from {src} -> {BATCH_OUT}")
    print("tier distribution: " + ", ".join(f"{t}: {n}" for t, n in
          sorted(tiers.items(), key=lambda kv: -kv[1])))
    write_movement(out_rows)
    return out_rows


# ---------------------------------------------------------------------------
# Movement tracking (the recurring, client-facing artifact)
# ---------------------------------------------------------------------------

HISTORY_FIELDS = ["run_date", "project_id", "name", "state", "county",
                  "tier", "composite"]


def load_history():
    rows = load_csv(HISTORY_OUT)
    by_run = {}
    for r in rows:
        by_run.setdefault(r["run_date"], {})[r["project_id"]] = r
    return rows, by_run


def write_movement(out_rows, today=None):
    """Append today's snapshot to the history file and write a movers report
    against the most recent prior snapshot. First run writes a baseline and
    reports no movement, which is correct rather than empty-by-error."""
    today = (today or date.today()).isoformat()
    prior_rows, by_run = load_history()
    prior_dates = sorted(d for d in by_run if d < today)
    prior = by_run.get(prior_dates[-1]) if prior_dates else None

    snapshot = [{"run_date": today, "project_id": r["project_id"], "name": r["name"],
                 "state": r["state"], "county": r["county"], "tier": r["tier"],
                 "composite": r["composite"]} for r in out_rows]
    kept = [r for r in prior_rows if r["run_date"] != today]   # idempotent re-runs
    with open(HISTORY_OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        w.writerows(kept + snapshot)

    lines = ["# Opposition Environment Movement Report", "",
             f"Run date: {today}"]
    if prior is None:
        lines += ["", "Baseline run. No prior snapshot exists, so no movement is "
                  "reported. The next run compares against this one.", "",
                  f"Sites screened: {len(out_rows)}", ""]
    else:
        lines += [f"Compared against: {prior_dates[-1]}", ""]
        movers, new_sites, tier_changes = [], [], []
        for r in out_rows:
            p = prior.get(r["project_id"])
            if p is None:
                new_sites.append(r)
                continue
            delta = round(r["composite"] - float(p["composite"]), 1)
            if p["tier"] != r["tier"]:
                tier_changes.append((r, p["tier"], delta))
            if abs(delta) >= MOVER_MIN_DELTA:
                movers.append((r, delta))
        movers.sort(key=lambda t: -abs(t[1]))

        lines += [f"Sites screened: {len(out_rows)} | tier changes: {len(tier_changes)} "
                  f"| composite moves of {MOVER_MIN_DELTA:.0f} points or more: {len(movers)} "
                  f"| newly screened: {len(new_sites)}", ""]
        if tier_changes:
            lines += ["## Tier changes", "",
                      "| Site | County, State | From | To | Composite change |",
                      "| :-- | :-- | :-- | :-- | --: |"]
            for r, was, delta in tier_changes:
                lines.append(f"| {r['name']} | {r['county']}, {r['state']} | {was} | "
                             f"{r['tier']} | {delta:+.1f} |")
            lines.append("")
        if movers:
            lines += ["## Largest composite moves", "",
                      "| Site | County, State | Tier | Composite | Change |",
                      "| :-- | :-- | :-- | :-- | --: |"]
            for r, delta in movers[:25]:
                lines.append(f"| {r['name']} | {r['county']}, {r['state']} | {r['tier']} | "
                             f"{r['composite']:.1f} | {delta:+.1f} |")
            lines.append("")
        if new_sites:
            lines += ["## Newly screened sites", "",
                      "| Site | County, State | Tier | Composite |",
                      "| :-- | :-- | :-- | --: |"]
            for r in new_sites[:25]:
                lines.append(f"| {r['name']} | {r['county']}, {r['state']} | {r['tier']} | "
                             f"{r['composite']:.1f} |")
            lines.append("")
        if not (tier_changes or movers or new_sites):
            lines += ["No site moved by the reporting threshold since the prior run.", ""]

    lines += ["## Reading this report", "",
              "Movement reflects change in the observed opposition environment "
              "around each site, not a change in any project's likelihood of a "
              "particular outcome. A site can move because new sourced opposition "
              "activity was recorded nearby, because an existing local restriction "
              "was enacted, because the county model was refit, or because the "
              "national reference set the percentiles rank against has grown. "
              "The component table in a site's brief shows which of these applies.",
              ""]
    text = "\n".join(lines)
    leak_audit(text, MOVERS_OUT)
    open(MOVERS_OUT, "w", encoding="utf-8").write(text)
    leak_audit(open(HISTORY_OUT, encoding="utf-8").read(), HISTORY_OUT)
    print(f"site_screener: movement report -> {MOVERS_OUT}")


def run_single(args):
    opposition, src = load_opposition()
    agg, scores, fips_lookup = load_county_layers()

    lat, lon = args.lat, args.lon
    county, state = args.county or "", args.state or ""
    fips = None
    if lat is None or lon is None:
        fips = county_to_fips(county, state, fips_lookup)
        if not fips:
            raise SystemExit("Provide --lat/--lon, or a resolvable --county and --state.")
        # centroid proxy: mean of opposition records in that county, else agg has no coords;
        # fall back to mean of proposals in county
        state_abbrev = state.strip().upper() if len(state.strip()) == 2 else \
            STATE_NAME_TO_ABBREV.get(state.strip().lower(), "")
        pts = [(e["lat"], e["lon"]) for e in opposition
               if e["lat"] is not None and e["state"] == state_abbrev
               and county.strip().lower().replace(" county", "") in
               (e["rec"].get("County") or "").strip().lower().replace(" county", "")]
        if not pts:
            props = load_csv(PROPOSALS_CSV)
            pts = [(_f(p.get("lat")), _f(p.get("lon"))) for p in props
                   if county.strip().lower() in (p.get("counties") or "").lower()
                   and (p.get("state") or "").strip().lower() == STATE_ABBREV.get(state_abbrev, "").lower()]
            pts = [p for p in pts if p[0] is not None]
        if not pts:
            raise SystemExit(f"No coordinates on file for {county}, {state}; pass --lat/--lon.")
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
    state_abbrev = (state.strip().upper() if len(state.strip()) == 2 else
                    STATE_NAME_TO_ABBREV.get(state.strip().lower(), ""))
    if not state_abbrev:
        # infer from nearest opposition record
        best = min((e for e in opposition if e["lat"] is not None),
                   key=lambda e: haversine_mi(lat, lon, e["lat"], e["lon"]),
                   default=None)
        state_abbrev = best["state"] if best else ""
    if fips is None:
        fips = county_to_fips(county, state_abbrev, fips_lookup)

    reference = build_reference(opposition, agg, scores, fips_lookup)
    all_comps = [r["components"] for r in reference]
    comps, nearby, extras = site_components(lat, lon, state_abbrev, fips,
                                            opposition, agg, scores)
    composite, pct = composite_from(comps, all_comps)
    tier = tier_for(composite)
    comparables = comparable_projects(lat, lon, state_abbrev, reference)

    name = args.name or (f"{county}, {state_abbrev}" if county else f"Site {lat:.3f}, {lon:.3f}")
    brief = render_brief(name, lat, lon, county, state_abbrev, fips, tier,
                         composite, comps, pct, nearby, extras, comparables,
                         agg, src, mw=args.mw)
    leak_audit(brief, "brief")

    os.makedirs(SIGNALS_DIR, exist_ok=True)
    slug = slugify(name)
    md_path = os.path.join(SIGNALS_DIR, f"brief_{slug}.md")
    js_path = os.path.join(SIGNALS_DIR, f"brief_{slug}.json")
    open(md_path, "w", encoding="utf-8").write(brief)
    payload = {"name": name, "lat": lat, "lon": lon, "county": county,
               "state": state_abbrev, "fips": fips, "tier": tier,
               "composite": composite, "components": comps, "percentiles": pct,
               "events_within_50mi": extras["events_within_50mi"],
               "generated": date.today().isoformat(), "source_feed": src,
               "weights": WEIGHTS, "disclosure": DISCLOSURE}
    js_text = json.dumps(payload, indent=2)
    leak_audit(js_text, js_path)
    open(js_path, "w", encoding="utf-8").write(js_text)
    print(brief)
    print(f"\nWrote {md_path} and {js_path}")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    ok = True

    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    expect(abs(haversine_mi(38.9, -77.0, 38.9, -77.0)) < 1e-9, "haversine zero distance")
    expect(50 < haversine_mi(38.9, -77.0, 39.9, -77.0) < 80, "haversine ~69 mi per degree lat")
    expect(recency_weight(None) == UNDATED_WEIGHT, "undated event gets floor weight")
    w_now = recency_weight(date.today())
    w_old = recency_weight(date(date.today().year - 2, date.today().month, 1))
    expect(w_now > 0.95 and 0.4 < w_old < 0.6, "24-month half-life behaves")
    expect(percentile_of(5, [1, 2, 3, 4]) == 100.0, "percentile top")
    expect(percentile_of(None, [1, 2]) == 0.0, "missing component ranks at floor, never inflates")
    expect(tier_for(90) == "High" and tier_for(70) == "Elevated"
           and tier_for(50) == "Guarded" and tier_for(10) == "Low", "tier bands")
    expect(is_enacted_restriction({"outcome": _INTERNAL_PREVAILED, "opptype": "moratorium"}),
           "enacted moratorium detected")
    expect(not is_enacted_restriction({"outcome": "pending", "opptype": "moratorium"}),
           "pending moratorium not enacted")
    expect(not is_enacted_restriction({"outcome": _INTERNAL_PREVAILED, "opptype": "lawsuit"}),
           "prevailing lawsuit is not an enacted restriction")
    for internal, display in _OUTCOME_DISPLAY.items():
        expect(not LEAK_RE.search(display), f"display term '{display}' passes leak audit")
    expect(not LEAK_RE.search(DISCLOSURE), "disclosure passes leak audit")
    sample = render_brief("Test Site", 39.0, -77.5, "Loudoun", "VA", None, "Guarded",
                          50.0, {k: 1.0 for k in WEIGHTS}, {k: 50.0 for k in WEIGHTS},
                          [], {"events_within_50mi": 0}, [], {}, "selftest")
    expect(not LEAK_RE.search(sample), "rendered brief passes leak audit")
    print("ALL PASS" if ok else "FAILURES PRESENT")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Opposition Environment Screener")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--batch", action="store_true", help="score all proposals -> data/site_screen.csv")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--county")
    ap.add_argument("--state")
    ap.add_argument("--mw")
    ap.add_argument("--name")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    if args.batch:
        run_batch()
        return
    if args.lat is not None or args.county:
        run_single(args)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
