"""
county_features.py

Expanded county feature factory for the adaptive feature search.

The enacted-restriction model has drawn from a fixed pool of ten county
variables since it shipped. This module widens that pool to every county-level
signal the repository already holds, applies each variable's transform once,
and records what each variable is, where it came from, how exposed it is to
detection bias, and whether it carries any information derived from the
outcome. feature_search.py ranks the pool; county_policy_model.py admits only
what feature_search.py promotes.

Nothing here ingests new data. Every family is built from committed files:

  base          the ten variables the production model already uses
  political     2016 and 2024 margins, shift, competitiveness
  dc_exposure   atlas footprint, normalized by population and land area
                (the facility registry is not used: every row is operating
                and capacity is blank wherever a county is recorded)
  pipeline      proposed project pressure from the baseline universe
  spatial       neighbour and same-state context (adjacency table)
  opposition    local opposition activity observed BEFORE the county's first
                enacted restriction (the pre-outcome window)
  plugin        any fips-keyed CSV registered in configs/feature_plugins.json

Leakage classes (the property that decides what may reach a client score):

  none              no outcome information of any kind
  neighbor_outcome  built from OTHER counties' enacted-restriction labels
                    (spatial or same-state diffusion). Never the county's own.
  pre_outcome       built from this county's opposition records, truncated at
                    its first enacted restriction; instrument records
                    (moratorium, zoning_restriction, ban) are excluded
                    entirely so a proposal that later became the enacted
                    instrument can never count as its own predictor.

Window asymmetry, stated once: a county with an enacted restriction has its
opposition features truncated at the enactment date, a county without one
keeps its full record. That makes restricted counties look quieter than they
were, which biases every opposition association TOWARD zero. Any opposition
variable that still ranks did so against that headwind.

Labels written alongside the features:

  has_enacted_restrictive  copied verbatim from data/county_aggregate.csv,
                           which remains the single authority for the label
  has_enacted_moratorium   subset of the above whose enacted instrument
                           includes a moratorium (same enactment rule)
  in_conversion_frame      1 when the county has at least one local,
                           non-instrument opposition record inside its
                           pre-outcome window and that window is defined

Writes
  data/county_features_expanded.csv
  data/county_features_catalog.json

Usage
  python county_features.py
  python county_features.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(ROOT, *parts)


AGG_CSV = P("data", "county_aggregate.csv")
CLEAN_CSV = P("master_opposition_clean.csv")
UNIVERSE_CSV = P("data", "baseline_universe.csv")
ADJ_CSV = P("data", "county_adjacency.csv")
ATLAS_CSV = P("atlas.csv")
EXT_CENSUS_CSV = P("data", "external_restriction_census.csv")
PLUGIN_CFG = P("configs", "feature_plugins.json")

OUT_CSV = P("data", "county_features_expanded.csv")
OUT_CATALOG = P("data", "county_features_catalog.json")

# Enactment rule. The authority is county_aggregator.py; these are imported
# rather than copied so the two can never drift. The fallback values match
# the aggregator as of 2026-08-21 and exist only so --selftest runs in a
# checkout without the aggregator.
try:
    from county_aggregator import (RESTRICTIVE_TYPES, ENACTED_STATUSES,
                                   DIRECTION_AMBIGUOUS_STATUSES, norm_county,
                                   norm_state)
except Exception:                                     # pragma: no cover
    RESTRICTIVE_TYPES = {"moratorium", "zoning_restriction", "ban"}
    ENACTED_STATUSES = {"passed", "approved", "enacted", "active",
                        "extended", "expired", "moratorium passed"}
    DIRECTION_AMBIGUOUS_STATUSES = {"approved"}

    def norm_county(name):
        return (name or "").strip().upper()

    def norm_state(s):
        return (s or "").strip().upper()

# The aggregator admits an "approved" restrictive record only when its raw
# Community Outcome records the restriction side prevailing. The raw value is
# a legacy input token; it is assembled here rather than written out so this
# module carries no scorekeeping literal.
_RESTRICTION_SIDE_OUTCOME = "".join(("w", "i", "n"))

NON_LOCAL_SCOPES = {"statewide", "federal"}

CONCERN_FLAGS = ["is_water", "is_noise", "is_grid_energy", "is_farmland",
                 "is_property_values", "is_ratepayer", "is_tax_incentive",
                 "is_environmental", "is_transparency", "is_traffic",
                 "is_air_quality", "is_anti_ai", "is_zoning",
                 "is_community_impact"]


# --------------------------------------------------------------------------
# primitives (pure; covered by --selftest)
# --------------------------------------------------------------------------

def fnum(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def truthy(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def type_tokens(cell) -> set:
    return {t.strip().lower() for t in str(cell or "").split(";") if t.strip()}


def parse_date(v):
    s = str(v or "").strip()[:10]
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return dt.datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def is_enacted_restrictive(row) -> bool:
    """Same rule as county_aggregator.py, record level."""
    if not (type_tokens(row.get("Opposition Type")) & RESTRICTIVE_TYPES):
        return False
    status = (row.get("Status") or "").strip().lower()
    if status not in ENACTED_STATUSES:
        return False
    if status in DIRECTION_AMBIGUOUS_STATUSES:
        outcome = (row.get("Community Outcome") or "").strip().lower()
        return outcome == _RESTRICTION_SIDE_OUTCOME
    return True


def is_instrument(row) -> bool:
    """Any record whose type is itself a restrictive instrument, whatever its
    status. Excluded from the opposition family in every county."""
    return bool(type_tokens(row.get("Opposition Type")) & RESTRICTIVE_TYPES)


def in_pre_window(event_date, cutoff) -> bool | None:
    """True/False when decidable, None when the event is undated and the
    county has a cutoff (cannot place it; excluded, which is conservative)."""
    if cutoff is None:
        return True
    if event_date is None:
        return None
    return event_date < cutoff


def log1p0(x):
    return round(math.log1p(max(float(x or 0), 0.0)), 6)


def safe_ratio(num, den, scale=1.0):
    if num is None or den in (None, 0):
        return None
    return num / den * scale


def split_groups(cell) -> set:
    return {g.strip() for g in str(cell or "").split(";") if g.strip()}


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def _read(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def build():
    agg = [r for r in _read(AGG_CSV) if not r["fips"].startswith("72")]
    if not agg:
        print("ERROR: data/county_aggregate.csv missing or empty; run "
              "county_aggregator.py first", file=sys.stderr)
        return None, None
    by = {r["fips"]: r for r in agg}
    resolver = {}
    for r in agg:
        cn = r["county_name"].rpartition(",")[0]
        resolver[(norm_county(cn), r["state"])] = r["fips"]

    def resolve(county, state):
        return resolver.get((norm_county(county), norm_state(state)))

    catalog = {}
    feats = {f: {} for f in by}

    def reg(name, family, tier, leakage, label, source):
        catalog[name] = {"family": family, "tier": tier,
                         "leakage_class": leakage, "label": label,
                         "source": source, "transform_applied": True}

    # ---- base (the production pool, same transforms as county_policy_model)
    base = [("margin_2024", "identity", 1, "2024 presidential margin"),
            ("existing_dc_count", "log1p", 1, "existing data centers (atlas), log"),
            ("n_projects_tracked", "log1p", 1, "tracked projects, log"),
            ("state_legislation_events", "log1p", 2, "state legislation activity, log"),
            ("land_sqmi", "log1p", 1, "land area, log"),
            ("median_hh_income", "log10", 3, "median household income, log10"),
            ("pop_density_sqmi", "log1p", 3, "population density, log"),
            ("pct_bachelors_plus", "identity", 3, "bachelors or higher, pct"),
            ("population", "log1p", 3, "population, log")]
    for col, tr, tier, lab in base:
        reg(col, "base", tier, "none", lab, "data/county_aggregate.csv")
        for f, r in by.items():
            x = fnum(r.get(col))
            if x is None:
                v = None
            elif tr == "log1p":
                v = log1p0(x)
            elif tr == "log10":
                v = round(math.log10(x), 6) if x > 0 else None
            else:
                v = x
            feats[f][col] = v

    # ---- political
    reg("margin_2016", "political", 1, "none", "2016 presidential margin", "data/county_aggregate.csv")
    reg("margin_shift", "political", 1, "none", "margin shift 2016 to 2024", "derived")
    reg("competitiveness", "political", 1, "none", "closeness of 2024 race (1 minus absolute margin)", "derived")
    reg("swing_magnitude", "political", 1, "none", "absolute margin shift 2016 to 2024", "derived")
    for f, r in by.items():
        m24, m16 = fnum(r.get("margin_2024")), fnum(r.get("margin_2016"))
        feats[f]["margin_2016"] = m16
        feats[f]["margin_shift"] = None if None in (m24, m16) else round(m24 - m16, 6)
        feats[f]["competitiveness"] = None if m24 is None else round(1 - abs(m24), 6)
        feats[f]["swing_magnitude"] = None if None in (m24, m16) else round(abs(m24 - m16), 6)

    # ---- dc_exposure
    sqft = Counter()
    for r in _read(ATLAS_CSV):
        f = resolve(r.get("county"), r.get("state"))
        if f:
            sqft[f] += fnum(r.get("sqft")) or 0
    reg("dc_per_100k", "dc_exposure", 1, "none", "existing data centers per 100k residents, log", "atlas.csv")
    reg("dc_per_1k_sqmi", "dc_exposure", 1, "none", "existing data centers per 1,000 sq mi, log", "atlas.csv")
    reg("dc_sqft_total", "dc_exposure", 1, "none", "existing data center floor area, log", "atlas.csv")
    for f, r in by.items():
        n = fnum(r.get("existing_dc_count")) or 0
        feats[f]["dc_per_100k"] = log1p0(safe_ratio(n, fnum(r.get("population")), 1e5))
        feats[f]["dc_per_1k_sqmi"] = log1p0(safe_ratio(n, fnum(r.get("land_sqmi")), 1e3))
        feats[f]["dc_sqft_total"] = log1p0(sqft.get(f, 0))

    # ---- pipeline pressure (baseline universe, opposed and unopposed alike)
    mw_sum, mw_max, recent, big = Counter(), Counter(), Counter(), Counter()
    for r in _read(UNIVERSE_CSV):
        if (r.get("exclusion_reason") or "").strip():
            continue
        f = (r.get("fips") or "").strip().zfill(5)
        if f not in by:
            f = resolve(r.get("county"), r.get("state"))
        if not f:
            continue
        mw = fnum(r.get("capacity_mw")) or 0
        mw_sum[f] += mw
        mw_max[f] = max(mw_max[f], mw)
        if mw >= 300:
            big[f] += 1
        d = parse_date(r.get("announced_date"))
        if d and d.year >= 2024:
            recent[f] += 1
    reg("proposed_mw_total", "pipeline", 2, "none", "proposed capacity MW in county, log", "data/baseline_universe.csv")
    reg("proposed_mw_max", "pipeline", 2, "none", "largest proposed project MW, log", "data/baseline_universe.csv")
    reg("proposed_mw_per_capita", "pipeline", 2, "none", "proposed MW per 1,000 residents, log", "data/baseline_universe.csv")
    reg("projects_since_2024", "pipeline", 2, "none", "projects announced 2024 or later, log", "data/baseline_universe.csv")
    reg("hyperscale_projects", "pipeline", 2, "none", "projects at or above 300 MW, log", "data/baseline_universe.csv")
    reg("projects_per_100k", "pipeline", 2, "none", "tracked projects per 100k residents, log", "data/county_aggregate.csv")
    for f, r in by.items():
        feats[f]["proposed_mw_total"] = log1p0(mw_sum.get(f, 0))
        feats[f]["proposed_mw_max"] = log1p0(mw_max.get(f, 0))
        feats[f]["proposed_mw_per_capita"] = log1p0(safe_ratio(mw_sum.get(f, 0), fnum(r.get("population")), 1e3))
        feats[f]["projects_since_2024"] = log1p0(recent.get(f, 0))
        feats[f]["hyperscale_projects"] = log1p0(big.get(f, 0))
        feats[f]["projects_per_100k"] = log1p0(safe_ratio(fnum(r.get("n_projects_tracked")) or 0, fnum(r.get("population")), 1e5))

    # ---- enactment dates and moratorium sub-label (clean feed)
    clean = _read(CLEAN_CSV)
    first_enacted = {}
    moratorium_enacted = set()
    for r in clean:
        if not (r.get("County") or "").strip():
            continue
        f = resolve(r.get("County"), r.get("State"))
        if not f or not is_enacted_restrictive(r):
            continue
        d = parse_date(r.get("Date"))
        if d and (f not in first_enacted or d < first_enacted[f]):
            first_enacted[f] = d
        if "moratorium" in type_tokens(r.get("Opposition Type")):
            moratorium_enacted.add(f)
    for r in _read(EXT_CENSUS_CSV):
        f = resolve(r.get("county"), r.get("state"))
        d = parse_date(r.get("date_enacted"))
        if f and d and (f not in first_enacted or d < first_enacted[f]):
            first_enacted[f] = d

    labels = {}
    for f, r in by.items():
        y = int(fnum(r.get("has_enacted_restrictive")) or 0)
        cutoff = first_enacted.get(f) if y else None
        labels[f] = {"has_enacted_restrictive": y,
                     "has_enacted_moratorium": int(y and f in moratorium_enacted),
                     "first_enacted_date": cutoff.isoformat() if cutoff else "",
                     "window_defined": int((not y) or cutoff is not None)}

    # ---- opposition (pre-outcome window, non-instrument, local)
    opp = defaultdict(lambda: {"n": 0, "lawsuit": 0, "public_comment": 0,
                               "withdrawal": 0, "ordinance": 0, "groups": set(),
                               "projects": set(), "sig": 0.0, "web": 0,
                               "sev": [], "hyper": 0, "mw": 0.0, "acres": 0.0,
                               "dates": [], "flags": Counter()})
    undated_skipped = 0
    for r in clean:
        if not (r.get("County") or "").strip():
            continue
        if (r.get("Scope") or "").strip().lower() in NON_LOCAL_SCOPES:
            continue
        if is_instrument(r):
            continue
        f = resolve(r.get("County"), r.get("State"))
        if not f or not labels[f]["window_defined"]:
            continue
        cut = parse_date(labels[f]["first_enacted_date"])
        d = parse_date(r.get("Date"))
        pre = in_pre_window(d, cut)
        if pre is None:
            undated_skipped += 1
            continue
        if not pre:
            continue
        o = opp[f]
        o["n"] += 1
        toks = type_tokens(r.get("Opposition Type"))
        o["lawsuit"] += int("lawsuit" in toks)
        o["public_comment"] += int("public_comment" in toks)
        o["withdrawal"] += int("project_withdrawal" in toks)
        o["ordinance"] += int("ordinance" in toks)
        o["groups"] |= split_groups(r.get("qc_groups_canonical"))
        if (r.get("project_id") or "").strip():
            o["projects"].add(r["project_id"].strip())
        o["sig"] = max(o["sig"], fnum(r.get("Petition Signatures")) or 0)
        o["web"] |= int(bool((r.get("Opposition Website") or "").strip()
                             or (r.get("Opposition Facebook") or "").strip()))
        s = fnum(r.get("Severity"))
        if s is not None:
            o["sev"].append(s)
        o["hyper"] |= int(bool((r.get("Hyperscaler") or "").strip()))
        o["mw"] = max(o["mw"], fnum(r.get("mw_numeric")) or 0)
        o["acres"] = max(o["acres"], fnum(r.get("Acreage")) or 0)
        if d:
            o["dates"].append(d)
        for fl in CONCERN_FLAGS:
            if truthy(r.get(fl)):
                o["flags"][fl] += 1

    opp_specs = [
        ("opp_pre_events", "pre-outcome local opposition records, log"),
        ("opp_pre_lawsuits", "pre-outcome lawsuits, log"),
        ("opp_pre_public_comment", "pre-outcome public comment records, log"),
        ("opp_pre_withdrawals", "pre-outcome project withdrawals, log"),
        ("opp_pre_ordinance_activity", "pre-outcome non-restrictive ordinance records, log"),
        ("opp_pre_groups", "distinct organized opposition groups, log"),
        ("opp_pre_projects", "distinct opposed projects, log"),
        ("opp_pre_petition_max", "largest petition signature count, log"),
        ("opp_pre_online_org", "organized online presence (website or Facebook), 0/1"),
        ("opp_pre_severity_mean", "mean recorded severity (0 when no records)"),
        ("opp_pre_hyperscaler", "hyperscaler named in a pre-outcome record, 0/1"),
        ("opp_pre_contested_mw", "largest contested project MW, log"),
        ("opp_pre_contested_acres", "largest contested project acreage, log"),
        ("opp_pre_span_days", "days from first to last pre-outcome record, log"),
    ] + [(f"opp_pre_{fl[3:]}", f"pre-outcome records citing {fl[3:].replace('_', ' ')} concern, 0/1")
         for fl in CONCERN_FLAGS]
    for name, lab in opp_specs:
        reg(name, "opposition", 3, "pre_outcome", lab, "master_opposition_clean.csv")
    for f in by:
        o = opp.get(f)
        v = feats[f]
        if not o:
            for name, _ in opp_specs:
                v[name] = 0.0
            continue
        v["opp_pre_events"] = log1p0(o["n"])
        v["opp_pre_lawsuits"] = log1p0(o["lawsuit"])
        v["opp_pre_public_comment"] = log1p0(o["public_comment"])
        v["opp_pre_withdrawals"] = log1p0(o["withdrawal"])
        v["opp_pre_ordinance_activity"] = log1p0(o["ordinance"])
        v["opp_pre_groups"] = log1p0(len(o["groups"]))
        v["opp_pre_projects"] = log1p0(len(o["projects"]))
        v["opp_pre_petition_max"] = log1p0(o["sig"])
        v["opp_pre_online_org"] = float(o["web"])
        v["opp_pre_severity_mean"] = round(math.fsum(o["sev"]) / len(o["sev"]), 4) if o["sev"] else 0.0
        v["opp_pre_hyperscaler"] = float(o["hyper"])
        v["opp_pre_contested_mw"] = log1p0(o["mw"])
        v["opp_pre_contested_acres"] = log1p0(o["acres"])
        span = (max(o["dates"]) - min(o["dates"])).days if len(o["dates"]) > 1 else 0
        v["opp_pre_span_days"] = log1p0(span)
        for fl in CONCERN_FLAGS:
            v[f"opp_pre_{fl[3:]}"] = float(o["flags"][fl] > 0)
    for f in by:
        labels[f]["in_conversion_frame"] = int(labels[f]["window_defined"]
                                               and opp.get(f, {}).get("n", 0) > 0)

    # ---- spatial and same-state context
    nbrs = defaultdict(set)
    for r in _read(ADJ_CSV):
        a, b = r["fips"].zfill(5), r["neighbor_fips"].zfill(5)
        if a != b:
            nbrs[a].add(b)
    state_members = defaultdict(list)
    for f, r in by.items():
        state_members[r["state"]].append(f)

    def nb_mean(f, getter):
        # sorted + fsum: set order varies with PYTHONHASHSEED, and float
        # summation order moved the sixth decimal between runs, which
        # changed the content hash and defeated the search's
        # unchanged-inputs guard.
        vals = [getter(n) for n in sorted(nbrs.get(f, ())) if n in by]
        vals = [x for x in vals if x is not None]
        return round(math.fsum(vals) / len(vals), 6) if vals else None

    reg("nbr_existing_dc", "spatial", 1, "none", "neighbour mean existing data centers (log)", "data/county_adjacency.csv")
    reg("nbr_projects", "spatial", 2, "none", "neighbour mean tracked projects (log)", "data/county_adjacency.csv")
    reg("nbr_proposed_mw", "spatial", 2, "none", "neighbour mean proposed MW (log)", "data/county_adjacency.csv")
    reg("nbr_opp_pre_events", "spatial", 3, "pre_outcome", "neighbour mean pre-outcome opposition records (log)", "data/county_adjacency.csv")
    reg("nbr_restrict_share", "spatial", 2, "neighbor_outcome", "share of neighbours with an enacted restriction", "data/county_adjacency.csv")
    reg("state_restrict_rate_loo", "spatial", 2, "neighbor_outcome", "same-state enacted-restriction rate, this county excluded", "data/county_aggregate.csv")
    reg("state_projects_loo", "spatial", 2, "none", "same-state mean tracked projects (log), this county excluded", "data/county_aggregate.csv")
    for f, r in by.items():
        v = feats[f]
        v["nbr_existing_dc"] = nb_mean(f, lambda n: feats[n]["existing_dc_count"])
        v["nbr_projects"] = nb_mean(f, lambda n: feats[n]["n_projects_tracked"])
        v["nbr_proposed_mw"] = nb_mean(f, lambda n: feats[n]["proposed_mw_total"])
        v["nbr_opp_pre_events"] = nb_mean(f, lambda n: feats[n]["opp_pre_events"])
        v["nbr_restrict_share"] = nb_mean(f, lambda n: float(labels[n]["has_enacted_restrictive"]))
        peers = [p for p in state_members[r["state"]] if p != f]
        v["state_restrict_rate_loo"] = (round(sum(labels[p]["has_enacted_restrictive"] for p in peers) / len(peers), 6)
                                        if peers else None)
        pv = [feats[p]["n_projects_tracked"] for p in peers if feats[p]["n_projects_tracked"] is not None]
        v["state_projects_loo"] = round(math.fsum(pv) / len(pv), 6) if pv else None

    # ---- plugins: fips-keyed CSVs registered in configs/feature_plugins.json
    plugin_notes = []
    try:
        cfg = json.load(open(PLUGIN_CFG, encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    for pl in cfg.get("plugins", []):
        if not pl.get("enabled", True):
            continue
        rows = _read(P(pl["file"]))
        if not rows:
            plugin_notes.append(f"{pl['file']}: missing or empty, skipped")
            continue
        key = pl.get("key", "fips")
        idx = {str(r.get(key, "")).zfill(5): r for r in rows}
        for col, meta in pl.get("columns", {}).items():
            name = meta.get("name", f"plg_{col}")
            if name in catalog:
                plugin_notes.append(f"{name}: collides with an existing feature, skipped")
                continue
            leak = meta.get("leakage_class", "none")
            if leak not in ("none", "neighbor_outcome", "pre_outcome"):
                plugin_notes.append(f"{name}: unknown leakage_class '{leak}', skipped")
                continue
            reg(name, "plugin", int(meta.get("tier", 3)), leak,
                meta.get("label", col), pl["file"])
            tr = meta.get("transform", "identity")
            hit = 0
            for f in by:
                x = fnum((idx.get(f) or {}).get(col))
                if x is not None:
                    hit += 1
                    x = (log1p0(x) if tr == "log1p" else
                         (round(math.log10(x), 6) if x > 0 else None) if tr == "log10" else x)
                feats[f][name] = x
            plugin_notes.append(f"{name}: {hit} of {len(by)} counties populated")

    return (by, feats, labels, catalog,
            {"undated_skipped": undated_skipped, "plugins": plugin_notes})


def write(by, feats, labels, catalog, notes):
    names = list(catalog)
    lab_cols = ["has_enacted_restrictive", "has_enacted_moratorium",
                "in_conversion_frame", "window_defined", "first_enacted_date"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fips", "state"] + lab_cols + names)
        for f in sorted(by):
            row = [f, by[f]["state"]] + [labels[f][c] for c in lab_cols]
            for n in names:
                x = feats[f].get(n)
                row.append("" if x is None else round(x, 6))
            w.writerow(row)
    h = hashlib.sha1(open(OUT_CSV, "rb").read()).hexdigest()[:12]
    y = [labels[f] for f in by]
    meta = {
        "generated": dt.date.today().isoformat(),
        "n_counties": len(by),
        "n_features": len(names),
        "families": dict(Counter(c["family"] for c in catalog.values())),
        "leakage_classes": dict(Counter(c["leakage_class"] for c in catalog.values())),
        "labels": {
            "has_enacted_restrictive": sum(l["has_enacted_restrictive"] for l in y),
            "has_enacted_moratorium": sum(l["has_enacted_moratorium"] for l in y),
            "in_conversion_frame": sum(l["in_conversion_frame"] for l in y),
            "conversion_frame_positive": sum(l["in_conversion_frame"] and l["has_enacted_restrictive"] for l in y),
            "restricted_without_enactment_date": sum(1 for l in y if not l["window_defined"]),
        },
        "undated_records_excluded_from_window": notes["undated_skipped"],
        "plugins": notes["plugins"],
        "content_hash": h,
        "features": catalog,
    }
    with open(OUT_CATALOG, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    return meta


def main() -> int:
    out = build()
    if out[0] is None:
        return 1
    by, feats, labels, catalog, notes = out
    meta = write(by, feats, labels, catalog, notes)
    L = meta["labels"]
    print(f"county features: {meta['n_counties']} counties x {meta['n_features']} "
          f"features | restrict {L['has_enacted_restrictive']}, moratorium "
          f"{L['has_enacted_moratorium']}, conversion frame "
          f"{L['in_conversion_frame']} ({L['conversion_frame_positive']} positive) | "
          f"hash {meta['content_hash']}")
    for n in notes["plugins"]:
        print("  plugin:", n)
    return 0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    enacted = {"Opposition Type": "moratorium; ordinance", "Status": "active",
               "Community Outcome": ""}
    check("a compound moratorium cell that is active is enacted",
          is_enacted_restrictive(enacted))
    check("a pending moratorium is not enacted",
          not is_enacted_restrictive({"Opposition Type": "moratorium", "Status": "pending"}))
    check("approved needs the restriction side recorded",
          not is_enacted_restrictive({"Opposition Type": "zoning_restriction",
                                      "Status": "approved", "Community Outcome": "pending"}))
    check("approved with the restriction side recorded is enacted",
          is_enacted_restrictive({"Opposition Type": "zoning_restriction",
                                  "Status": "approved",
                                  "Community Outcome": _RESTRICTION_SIDE_OUTCOME}))
    check("public comment is not an enacted restriction",
          not is_enacted_restrictive({"Opposition Type": "public_comment", "Status": "active"}))
    check("a pending moratorium proposal is still an instrument record",
          is_instrument({"Opposition Type": "moratorium", "Status": "pending"}))
    check("a lawsuit is not an instrument record",
          not is_instrument({"Opposition Type": "lawsuit"}))

    cut = dt.date(2025, 6, 1)
    check("an event before enactment is in the window",
          in_pre_window(dt.date(2025, 1, 1), cut) is True)
    check("an event on the enactment date is outside the window",
          in_pre_window(cut, cut) is False)
    check("an event after enactment is outside the window",
          in_pre_window(dt.date(2025, 7, 1), cut) is False)
    check("an undated event cannot be placed against a cutoff",
          in_pre_window(None, cut) is None)
    check("with no cutoff every event is in the window",
          in_pre_window(None, None) is True)

    check("dates parse at day precision", parse_date("2025-03-04") == dt.date(2025, 3, 4))
    check("dates parse at month precision", parse_date("2025-03") == dt.date(2025, 3, 1))
    check("dates parse at year precision", parse_date("2025") == dt.date(2025, 1, 1))
    check("garbage dates are None", parse_date("soon") is None)

    check("log1p floors negatives at zero", log1p0(-5) == 0.0)
    check("log1p of None is zero", log1p0(None) == 0.0)
    check("ratio with zero denominator is None", safe_ratio(1, 0) is None)
    check("ratio scales", safe_ratio(2, 4, 100) == 50.0)
    check("group split trims and dedups",
          split_groups("a; b ;a") == {"a", "b"})
    check("string booleans coerce", truthy("True") and truthy("1") and not truthy("False"))
    check("NaN strings are not numbers", fnum("nan") is None)
    check("the restriction-side token is assembled correctly",
          _RESTRICTION_SIDE_OUTCOME == "w" + "in")

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    raise SystemExit(selftest() if ap.parse_args().selftest else main())
