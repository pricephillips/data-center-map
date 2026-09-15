#!/usr/bin/env python3
"""
county_benchmarks.py

Answers the question every county page raises and none of them previously
answered: compared to what?

A single county's numbers are not interpretable on their own. 41,000 opposition
events is a lot; 4 is not -- unless the county has 12,000 people, in which case
4 is a great deal. A 62% restriction resemblance score means nothing until you
know that the median county scores 9% and the median county that has actually
enacted a restriction scores 71%. This module publishes the reference frames
that turn a raw value into a read.

FIVE REFERENCE GROUPS, and the reason each one exists:

  national        every county in the model frame. The naive baseline, and the
                  one a client will assume you mean by "average".

  restrictive     counties with an enacted restrictive action on record. This
                  is the group that matters: it is the observed profile of a
                  county that already did the thing. "How close is this county
                  to the counties that acted" is the actual business question.

  non_restrictive the complement. Published so the restrictive profile can be
                  read as a contrast rather than as a level, which is the
                  difference between a comparison and an anecdote.

  dc_present      counties with at least one data center on record. A county
                  with no industry presence has no opposition for reasons that
                  have nothing to do with its politics, so the national median
                  is a misleading denominator for exposure questions.

  state           the county's own state. Siting, zoning and utility law are
                  set at the state level, so the within-state comparison is
                  often the only fair one.

  peer            the k nearest counties on standardised structural features.
                  See MATCHING below.

MATCHING. The peer group is a nearest-neighbour set on four standardised
features -- log population, log population density, share with a bachelor's
degree, and 2024 presidential margin -- chosen because they are the structural
covariates available for every county in the frame and because they are the
ones a reader will otherwise apply informally and badly ("it's a rural red
county, so..."). Distance is plain Euclidean over z-scores with equal weights.

This is a similarity set, NOT a causal matched control. It is not balanced on
treatment, carries no propensity model, and supports no counterfactual claim.
control_group.py is where matched-control work lives in this repository and
IDENTIFIABILITY.md records what that exercise concluded. A peer group here is
a reading aid: "counties that look like this one restrict at 18%, this one
scores 62%" is a legitimate sentence. "These peers show the county would have
restricted anyway" is not, and nothing in this file supports it.

PERCENTILES are computed by the standard mid-rank definition over non-missing
values in the group, so a county at the median reads 50 whatever the group
size. A metric missing for a county yields an empty percentile, never a zero:
the most common way a comparison layer lies is by scoring an absent value as a
low one.

DIRECTION. Each metric declares whether a higher value means more restriction
pressure, less, or neither. Without it a UI cannot colour a delta without
guessing, and a guessed direction is worse than none -- "median income is 14
points above the restrictive median" is not good news or bad news until you
know which way the model leans on income.

Reads
  data/county_aggregate.csv          the county frame and its descriptive fields
  data/county_policy_scores.csv      calibrated restriction resemblance score
  data/county_policy_intervals.csv   interval width, for a precision comparison

Writes
  data/county_benchmark_reference.csv   group x metric summary profiles
  data/county_benchmarks.csv            one row per county: percentiles + peers
  data/county_benchmarks_manifest.json  freshness, coverage, group sizes
  data/county_benchmarks_report.md      human-readable read of the groups

Usage
  python county_benchmarks.py --selftest
  python county_benchmarks.py --build
  python county_benchmarks.py --build --peers 12
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

AGGREGATE = os.path.join(DATA, "county_aggregate.csv")
SCORES = os.path.join(DATA, "county_policy_scores.csv")
INTERVALS = os.path.join(DATA, "county_policy_intervals.csv")

OUT_REFERENCE = os.path.join(DATA, "county_benchmark_reference.csv")
OUT_BENCHMARKS = os.path.join(DATA, "county_benchmarks.csv")
OUT_MANIFEST = os.path.join(DATA, "county_benchmarks_manifest.json")
OUT_REPORT = os.path.join(DATA, "county_benchmarks_report.md")

DEFAULT_PEERS = 10

# ---------------------------------------------------------------------------
# Metric declarations
#
# `direction` is how the UI is allowed to colour a delta:
#   "up_is_more_restrictive"   above the reference = more restriction pressure
#   "up_is_less_restrictive"   above the reference = less restriction pressure
#   "neutral"                  no defensible reading; show the delta, no colour
#
# Every direction here is either mechanical (an event count is an event count)
# or taken from the fitted sign in county_policy_model.py. Nothing is asserted
# from intuition: where the model does not settle the sign, the metric is
# neutral and the page shows a number without a verdict.
# ---------------------------------------------------------------------------

METRICS = [
    # key, label, source file, direction, decimals
    ("calibrated_score", "Restriction resemblance score", "score",
     "up_is_more_restrictive", 4),
    ("va_width", "Score interval width", "interval", "neutral", 4),
    ("population", "Population", "agg", "neutral", 0),
    ("pop_density_sqmi", "Population density (per sq mi)", "agg", "neutral", 2),
    ("median_hh_income", "Median household income", "agg", "neutral", 0),
    ("pct_bachelors_plus", "Bachelor's degree or higher (%)", "agg", "neutral", 2),
    ("margin_2024", "2024 presidential margin", "agg", "neutral", 4),
    ("margin_2016", "2016 presidential margin", "agg", "neutral", 4),
    ("land_sqmi", "Land area (sq mi)", "agg", "neutral", 1),
    ("existing_dc_count", "Data center records in the atlas", "agg", "neutral", 0),
    ("n_opposition_events", "Opposition events", "agg", "up_is_more_restrictive", 0),
    ("n_moratorium_events", "Moratorium events", "agg", "up_is_more_restrictive", 0),
    ("n_zoning_events", "Zoning events", "agg", "up_is_more_restrictive", 0),
    ("n_ban_events", "Ban events", "agg", "up_is_more_restrictive", 0),
    ("n_legislation_events", "Legislation events", "agg", "up_is_more_restrictive", 0),
    ("n_lawsuit_events", "Lawsuit events", "agg", "up_is_more_restrictive", 0),
    ("n_enacted_restrictive", "Enacted restrictive actions", "agg",
     "up_is_more_restrictive", 0),
    ("state_legislation_events", "Statewide legislation events", "agg",
     "up_is_more_restrictive", 0),
    ("n_projects_tracked", "Projects tracked", "agg", "neutral", 0),
    ("n_projects_opposed", "Projects opposed", "agg", "up_is_more_restrictive", 0),
    ("n_decided", "Projects decided", "agg", "neutral", 0),
    ("n_blocked_confirmed", "Projects blocked (confirmed)", "agg",
     "up_is_more_restrictive", 0),
    ("n_advanced_confirmed", "Projects advanced (confirmed)", "agg",
     "up_is_less_restrictive", 0),
    ("n_restricted_conditional", "Projects restricted or conditional", "agg",
     "up_is_more_restrictive", 0),
    ("median_days_to_decision", "Median days to decision", "agg", "neutral", 0),
    # Derived rates. A count is not comparable across counties of different
    # sizes, and the opposition tables are the single most misread thing on the
    # county page, so the rates ship alongside the counts rather than leaving a
    # reader to divide in their head.
    ("opposition_events_per_100k", "Opposition events per 100k residents",
     "derived", "up_is_more_restrictive", 3),
    ("opposition_events_per_dc", "Opposition events per data center record",
     "derived", "up_is_more_restrictive", 3),
    ("opposed_share_of_tracked", "Share of tracked projects opposed", "derived",
     "up_is_more_restrictive", 4),
    ("blocked_share_of_decided", "Share of decided projects blocked", "derived",
     "up_is_more_restrictive", 4),
]

METRIC_BY_KEY = {m[0]: m for m in METRICS}

# Features the peer match runs on. Kept deliberately short: every one is
# present for essentially the whole frame, and adding a sparse feature would
# silently shrink the matched population rather than improve the match.
MATCH_FEATURES = [
    ("log_population", lambda r: _safe_log(r.get("population"))),
    ("log_pop_density", lambda r: _safe_log(r.get("pop_density_sqmi"))),
    ("pct_bachelors_plus", lambda r: _num(r.get("pct_bachelors_plus"))),
    ("margin_2024", lambda r: _num(r.get("margin_2024"))),
]


# ---------------------------------------------------------------------------
# small numerics
# ---------------------------------------------------------------------------

def _num(v):
    """Parse to float, returning None for blank/unparseable rather than 0.0."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _safe_log(v):
    """log1p, which keeps a zero-population or zero-density county in frame."""
    f = _num(v)
    if f is None or f < 0:
        return None
    return math.log1p(f)


def median(values):
    vs = sorted(values)
    n = len(vs)
    if n == 0:
        return None
    mid = n // 2
    return vs[mid] if n % 2 else (vs[mid - 1] + vs[mid]) / 2.0


def quantile(values, q):
    """Linear-interpolation quantile on a sorted copy. Matches numpy's default
    so a reader checking these numbers in pandas gets the same answer."""
    vs = sorted(values)
    n = len(vs)
    if n == 0:
        return None
    if n == 1:
        return vs[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vs[lo]
    return vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)


def mean(values):
    return sum(values) / len(values) if values else None


def stdev(values):
    """Sample standard deviation. None below two observations, because a
    z-score against a one-county group is not a statement about anything."""
    n = len(values)
    if n < 2:
        return None
    mu = sum(values) / n
    var = sum((v - mu) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def percentile_of(value, sorted_values):
    """Mid-rank percentile of `value` within `sorted_values`, 0-100.

    Mid-rank (counting half the ties) rather than "fraction strictly below",
    so a county sitting on a heavily tied value -- zero opposition events,
    which is most counties -- reads at the middle of that tie rather than at
    its bottom. Scoring every zero-event county at the 0th percentile would be
    the single most misleading number this module could publish.
    """
    n = len(sorted_values)
    if n == 0 or value is None:
        return None
    below = _bisect_left(sorted_values, value)
    equal = _bisect_right(sorted_values, value) - below
    return 100.0 * (below + equal / 2.0) / n


def _bisect_left(arr, x):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(arr, x):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if x < arr[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _round(v, dp):
    if v is None:
        return ""
    if dp == 0:
        return str(int(round(v)))
    return ("%." + str(dp) + "f") % v


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def build_counties():
    """Join aggregate + score + interval into one record per county, and attach
    the derived rates. Returns the list in aggregate file order."""
    agg = read_csv(AGGREGATE)
    if not agg:
        raise SystemExit("county_aggregate.csv is missing or empty; nothing to benchmark.")

    scores = {r["fips"]: r for r in read_csv(SCORES)}
    intervals = {r["fips"]: r for r in read_csv(INTERVALS)}

    out = []
    for r in agg:
        fips = str(r.get("fips", "")).strip().zfill(5)
        if not fips or fips == "00000":
            continue
        rec = {
            "fips": fips,
            "county_name": r.get("county_name", ""),
            "state": r.get("state", ""),
            "has_enacted_restrictive": str(r.get("has_enacted_restrictive", "")).strip() == "1",
            "agg": r,
            "score": scores.get(fips),
            "interval": intervals.get(fips),
        }
        rec["values"] = metric_values(rec)
        out.append(rec)
    return out


def metric_values(rec):
    """Every declared metric for one county, as float or None."""
    a = rec["agg"]
    s = rec["score"] or {}
    iv = rec["interval"] or {}
    v = {}

    for key, _label, src, _direction, _dp in METRICS:
        if src == "agg":
            v[key] = _num(a.get(key))
        elif src == "score":
            v[key] = _num(s.get(key))
        elif src == "interval":
            v[key] = _num(iv.get(key))

    pop = _num(a.get("population"))
    dcs = _num(a.get("existing_dc_count"))
    opp = _num(a.get("n_opposition_events"))
    tracked = _num(a.get("n_projects_tracked"))
    opposed = _num(a.get("n_projects_opposed"))
    decided = _num(a.get("n_decided"))
    blocked = _num(a.get("n_blocked_confirmed"))

    # Rates are defined only where the denominator is a real population, not
    # merely non-null. A county with no data centers has no events-per-data-
    # center; publishing a 0 there would read as "lots of industry, no
    # opposition", the exact opposite of the truth.
    v["opposition_events_per_100k"] = (
        (opp / pop * 100000.0) if (opp is not None and pop) else None)
    v["opposition_events_per_dc"] = (
        (opp / dcs) if (opp is not None and dcs) else None)
    v["opposed_share_of_tracked"] = (
        (opposed / tracked) if (opposed is not None and tracked) else None)
    v["blocked_share_of_decided"] = (
        (blocked / decided) if (blocked is not None and decided) else None)
    return v


# ---------------------------------------------------------------------------
# reference groups
# ---------------------------------------------------------------------------

def group_members(counties):
    """group_key -> (label, [county records]). State groups are emitted for
    every state present in the frame, so the page never has to fall back to a
    national comparison for a county whose state happens to be small."""
    groups = {"national": ("All counties in the frame", list(counties))}

    restrictive = [c for c in counties if c["has_enacted_restrictive"]]
    groups["restrictive"] = (
        "Counties with an enacted restrictive action on record", restrictive)
    groups["non_restrictive"] = (
        "Counties with no enacted restriction on record",
        [c for c in counties if not c["has_enacted_restrictive"]])

    dc_present = [c for c in counties
                  if (_num(c["agg"].get("existing_dc_count")) or 0) > 0]
    groups["dc_present"] = ("Counties with a data center on record", dc_present)
    groups["dc_absent"] = (
        "Counties with no data center on record",
        [c for c in counties if (_num(c["agg"].get("existing_dc_count")) or 0) <= 0])

    # Counties with any tracked activity at all. The opposition metrics are
    # media-detected and most counties are structural zeros, so a percentile
    # against the national frame answers "is this county unusual for having
    # anything at all", while this group answers "among counties that are on
    # the board, how active is this one" -- a different and usually more
    # useful question.
    active = [c for c in counties
              if (_num(c["agg"].get("n_opposition_events")) or 0) > 0]
    groups["tracked_activity"] = (
        "Counties with at least one tracked opposition event", active)

    by_state = {}
    for c in counties:
        st = (c.get("state") or "").strip().upper()
        if st:
            by_state.setdefault(st, []).append(c)
    for st, members in by_state.items():
        groups["state:" + st] = (st + " counties", members)

    return groups


def summarise_group(members, metric_key):
    vals = [m["values"].get(metric_key) for m in members]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "n_obs": len(vals),
        "mean": mean(vals),
        "median": median(vals),
        "p10": quantile(vals, 0.10),
        "p25": quantile(vals, 0.25),
        "p75": quantile(vals, 0.75),
        "p90": quantile(vals, 0.90),
        "sd": stdev(vals),
        "min": min(vals),
        "max": max(vals),
        "sorted": sorted(vals),
    }


def build_reference(counties):
    """group_key -> metric_key -> summary dict (with the sorted vector kept in
    memory for percentile lookups; it is not written to the file)."""
    groups = group_members(counties)
    ref = {}
    for gkey, (_label, members) in groups.items():
        ref[gkey] = {}
        for key, _label2, _src, _dir, _dp in METRICS:
            s = summarise_group(members, key)
            if s:
                ref[gkey][key] = s
    return groups, ref


def write_reference(groups, ref):
    cols = ["group_key", "group_label", "group_n", "metric", "metric_label",
            "direction", "n_obs", "mean", "median", "p10", "p25", "p75", "p90",
            "sd", "min", "max"]
    rows = []
    for gkey in sorted(ref.keys()):
        label, members = groups[gkey]
        for key, mlabel, _src, direction, dp in METRICS:
            s = ref[gkey].get(key)
            if not s:
                continue
            rows.append({
                "group_key": gkey,
                "group_label": label,
                "group_n": len(members),
                "metric": key,
                "metric_label": mlabel,
                "direction": direction,
                "n_obs": s["n_obs"],
                "mean": _round(s["mean"], dp + 2),
                "median": _round(s["median"], dp + 2),
                "p10": _round(s["p10"], dp + 2),
                "p25": _round(s["p25"], dp + 2),
                "p75": _round(s["p75"], dp + 2),
                "p90": _round(s["p90"], dp + 2),
                "sd": _round(s["sd"], dp + 2),
                "min": _round(s["min"], dp + 2),
                "max": _round(s["max"], dp + 2),
            })
    _write(OUT_REFERENCE, cols, rows)
    return len(rows)


# ---------------------------------------------------------------------------
# peer matching
# ---------------------------------------------------------------------------

def standardise(counties):
    """z-score each match feature over the counties that have all of them.

    Counties missing any feature are excluded from matching entirely rather
    than mean-imputed. An imputed county would match to the centre of the
    distribution and look like a typical American county, which is precisely
    the error a client would never catch.
    """
    raw = {}
    for name, fn in MATCH_FEATURES:
        raw[name] = {}
        for c in counties:
            raw[name][c["fips"]] = fn(c["agg"])

    eligible = [c for c in counties
                if all(raw[name][c["fips"]] is not None for name, _ in MATCH_FEATURES)]

    stats = {}
    for name, _fn in MATCH_FEATURES:
        vals = [raw[name][c["fips"]] for c in eligible]
        mu = mean(vals)
        sd = stdev(vals)
        # A zero-variance feature would divide by zero and, more importantly,
        # carries no matching information, so it is dropped to a constant.
        stats[name] = (mu, sd if (sd and sd > 1e-12) else None)

    vectors = {}
    for c in eligible:
        vec = []
        for name, _fn in MATCH_FEATURES:
            mu, sd = stats[name]
            v = raw[name][c["fips"]]
            vec.append(0.0 if sd is None else (v - mu) / sd)
        vectors[c["fips"]] = vec
    return vectors, stats, eligible


def find_peers(counties, k):
    """fips -> {"peers": [(fips, distance)], "mean_distance": float}.

    Brute force over ~3,200 counties is ~5M distance evaluations in four
    dimensions. It runs in a few seconds and needs no dependency, which is
    worth more here than the speed of a KD-tree: this file has to run in CI on
    a stock python image with no numpy.
    """
    vectors, stats, eligible = standardise(counties)
    order = [c["fips"] for c in eligible]
    dims = len(MATCH_FEATURES)
    out = {}

    for fips in order:
        v = vectors[fips]
        # `best` holds SQUARED distances throughout. Mixing squared partial
        # sums with a rooted cutoff is the classic way to write a nearest-
        # neighbour search that silently returns the wrong neighbours -- for
        # any distance above 1 the square is larger, so a rooted cutoff prunes
        # candidates that were actually closer. The root is taken once, at the
        # end, after the ordering is settled.
        best = []  # (squared distance, fips), kept sorted ascending
        cutoff = None
        for other in order:
            if other == fips:
                continue
            w = vectors[other]
            d2 = 0.0
            pruned = False
            for i in range(dims):
                diff = v[i] - w[i]
                d2 += diff * diff
                if cutoff is not None and d2 > cutoff:
                    pruned = True
                    break
            if pruned:
                continue
            if len(best) < k:
                best.append((d2, other))
                best.sort()
            elif d2 < best[-1][0]:
                best[-1] = (d2, other)
                best.sort()
            if len(best) == k:
                cutoff = best[-1][0]
        peers = [(f, math.sqrt(d2)) for d2, f in best]
        out[fips] = {
            "peers": peers,
            "mean_distance": mean([d for _f, d in peers]),
        }
    return out, stats, len(eligible)


# ---------------------------------------------------------------------------
# per-county benchmark rows
# ---------------------------------------------------------------------------

# Which groups get a full percentile column set in the wide per-county file.
# The state group is per-county by construction and is resolved at build time.
WIDE_GROUPS = ["national", "restrictive", "tracked_activity"]

# Metrics carried in the wide per-county file. The reference file carries every
# metric for every group; this file is the subset a client page reads first, and
# it stays narrow so it can load in the fast wave.
WIDE_METRICS = [
    "calibrated_score", "n_opposition_events", "n_enacted_restrictive",
    "opposition_events_per_100k", "opposition_events_per_dc",
    "existing_dc_count", "n_projects_tracked", "blocked_share_of_decided",
    "population", "pop_density_sqmi", "median_hh_income",
    "pct_bachelors_plus", "margin_2024",
]


def build_benchmarks(counties, ref, peers_by_fips, k):
    by_fips = {c["fips"]: c for c in counties}
    rows = []

    for c in counties:
        fips = c["fips"]
        vals = c["values"]
        st_key = "state:" + (c.get("state") or "").strip().upper()
        row = {
            "fips": fips,
            "county_name": c["county_name"],
            "state": c["state"],
            "has_enacted_restrictive": "1" if c["has_enacted_restrictive"] else "0",
        }

        for mkey in WIDE_METRICS:
            dp = METRIC_BY_KEY[mkey][4]
            row[mkey] = _round(vals.get(mkey), dp)
            for gkey in WIDE_GROUPS:
                s = ref.get(gkey, {}).get(mkey)
                pct = percentile_of(vals.get(mkey), s["sorted"]) if s else None
                row[mkey + "_pct_" + gkey] = _round(pct, 1)
            s_state = ref.get(st_key, {}).get(mkey)
            pct_state = percentile_of(vals.get(mkey), s_state["sorted"]) if s_state else None
            row[mkey + "_pct_state"] = _round(pct_state, 1)

        # ---- peer group ----
        pinfo = peers_by_fips.get(fips)
        peer_fips = [f for f, _d in pinfo["peers"]] if pinfo else []
        row["peer_fips"] = ";".join(peer_fips)
        row["peer_n"] = len(peer_fips)
        row["peer_mean_distance"] = _round(pinfo["mean_distance"], 4) if pinfo else ""
        row["peer_matched"] = "1" if peer_fips else "0"

        peer_recs = [by_fips[f] for f in peer_fips if f in by_fips]
        if peer_recs:
            restr = sum(1 for p in peer_recs if p["has_enacted_restrictive"])
            row["peer_restriction_rate"] = _round(restr / len(peer_recs), 4)
            row["peer_restriction_count"] = restr
            for mkey in WIDE_METRICS:
                dp = METRIC_BY_KEY[mkey][4]
                pvals = [p["values"].get(mkey) for p in peer_recs]
                pvals = [v for v in pvals if v is not None]
                row[mkey + "_peer_median"] = _round(median(pvals), dp + 2) if pvals else ""
                row[mkey + "_pct_peer"] = (
                    _round(percentile_of(vals.get(mkey), sorted(pvals)), 1) if pvals else "")
        else:
            row["peer_restriction_rate"] = ""
            row["peer_restriction_count"] = ""
            for mkey in WIDE_METRICS:
                row[mkey + "_peer_median"] = ""
                row[mkey + "_pct_peer"] = ""

        # ---- the headline comparison ----
        # The raw gap between this county's score and the median score of the
        # counties that actually enacted something.
        #
        # An earlier build of this file also published a 0-100 "proximity
        # index" scaled between the non-restrictive and restrictive medians.
        # It was removed rather than fixed. Those two medians sit about nine
        # points apart, so the denominator is small and the index ran past 400
        # for ordinary high-scoring counties -- a number that reads as broken
        # and, worse, would be quoted. The percentile within the restrictive
        # group (`calibrated_score_pct_restrictive`) answers the same question
        # without a fragile denominator: it says how this county's score ranks
        # against the counties that have already acted, and it cannot blow up.
        row["score_gap_to_restrictive_median"] = ""
        s_here = vals.get("calibrated_score")
        r_med = (ref.get("restrictive", {}).get("calibrated_score") or {}).get("median")
        if s_here is not None and r_med is not None:
            row["score_gap_to_restrictive_median"] = _round(s_here - r_med, 4)

        rows.append(row)

    cols = ["fips", "county_name", "state", "has_enacted_restrictive"]
    for mkey in WIDE_METRICS:
        cols.append(mkey)
        for gkey in WIDE_GROUPS:
            cols.append(mkey + "_pct_" + gkey)
        cols.append(mkey + "_pct_state")
        cols.append(mkey + "_pct_peer")
        cols.append(mkey + "_peer_median")
    cols += ["peer_fips", "peer_n", "peer_matched", "peer_mean_distance",
             "peer_restriction_count", "peer_restriction_rate",
             "score_gap_to_restrictive_median"]
    return cols, rows


def _write(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

REPORT_METRICS = ["calibrated_score", "n_opposition_events",
                  "opposition_events_per_100k", "existing_dc_count",
                  "population", "pop_density_sqmi", "median_hh_income",
                  "pct_bachelors_plus", "margin_2024"]


def write_report(groups, ref, peer_n, k, stats):
    lines = []
    lines.append("# County benchmark reference")
    lines.append("")
    lines.append("Generated " + datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".")
    lines.append("")
    lines.append("Reference profiles for the county comparison layer. Every figure "
                 "is a summary over the county frame in `data/county_aggregate.csv` "
                 "joined to the published policy scores. Nothing here is an estimate; "
                 "these are descriptions of groups that exist.")
    lines.append("")
    lines.append("## Group sizes")
    lines.append("")
    lines.append("| group | counties | description |")
    lines.append("| --- | --- | --- |")
    for gkey in ["national", "restrictive", "non_restrictive", "dc_present",
                 "dc_absent", "tracked_activity"]:
        if gkey in groups:
            label, members = groups[gkey]
            lines.append("| `%s` | %s | %s |" % (gkey, f"{len(members):,}", label))
    state_keys = [g for g in groups if g.startswith("state:")]
    lines.append("| `state:XX` | varies | %d state groups, one per state in the frame |"
                 % len(state_keys))
    lines.append("")

    lines.append("## The comparison that matters")
    lines.append("")
    lines.append("Median values for the three groups a county page reads against.")
    lines.append("")
    lines.append("| metric | all counties | enacted a restriction | no restriction |")
    lines.append("| --- | --- | --- | --- |")
    for mkey in REPORT_METRICS:
        dp = METRIC_BY_KEY[mkey][4]
        cells = []
        for gkey in ["national", "restrictive", "non_restrictive"]:
            s = ref.get(gkey, {}).get(mkey)
            cells.append(_round(s["median"], dp) if s else "—")
        lines.append("| %s | %s | %s | %s |"
                     % (METRIC_BY_KEY[mkey][1], cells[0], cells[1], cells[2]))
    lines.append("")

    lines.append("## Peer matching")
    lines.append("")
    lines.append("Each county is matched to its **%d** nearest counties on "
                 "standardised log population, log population density, share with "
                 "a bachelor's degree, and 2024 presidential margin. "
                 "**%s** counties carry all four features and are matchable; the "
                 "rest are published with an empty peer set rather than an "
                 "imputed one." % (k, f"{peer_n:,}"))
    lines.append("")
    lines.append("| feature | mean | sd |")
    lines.append("| --- | --- | --- |")
    for name, _fn in MATCH_FEATURES:
        mu, sd = stats[name]
        lines.append("| `%s` | %s | %s |"
                     % (name, _round(mu, 4), _round(sd, 4) if sd else "constant"))
    lines.append("")
    lines.append("A peer set is a similarity group, not a matched control. It "
                 "supports statements of the form \"counties that look like this one "
                 "restrict at X%\". It does not support any statement about what "
                 "would have happened in this county, and `IDENTIFIABILITY.md` "
                 "records why no such statement is available from this data.")
    lines.append("")

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build(k=DEFAULT_PEERS):
    counties = build_counties()
    groups, ref = build_reference(counties)
    n_ref_rows = write_reference(groups, ref)

    peers_by_fips, stats, matchable = find_peers(counties, k)
    cols, rows = build_benchmarks(counties, ref, peers_by_fips, k)
    _write(OUT_BENCHMARKS, cols, rows)
    write_report(groups, ref, matchable, k, stats)

    manifest = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counties": len(counties),
        "counties_matchable": matchable,
        "peers_per_county": k,
        "reference_rows": n_ref_rows,
        "metrics": len(METRICS),
        "groups": {gkey: len(members) for gkey, (_l, members) in groups.items()
                   if not gkey.startswith("state:")},
        "state_groups": sum(1 for g in groups if g.startswith("state:")),
        "match_features": [name for name, _fn in MATCH_FEATURES],
        # How far apart the two groups actually are on the score. Published
        # because it is the number that decides how much weight a comparison
        # against the restrictive median can carry: a small separation means
        # the score ranks counties usefully but does not cleanly divide them,
        # and a page quoting the comparison should say so.
        "score_separation_restrictive_minus_non": _separation(ref),
        "medians": {
            gkey: {
                mkey: (ref.get(gkey, {}).get(mkey) or {}).get("median")
                for mkey in REPORT_METRICS
                if ref.get(gkey, {}).get(mkey)
            }
            for gkey in ["national", "restrictive", "non_restrictive",
                         "dc_present", "tracked_activity"]
        },
        "inputs": {
            "county_aggregate.csv": _digest(AGGREGATE),
            "county_policy_scores.csv": _digest(SCORES),
            "county_policy_intervals.csv": _digest(INTERVALS),
        },
    }
    with open(OUT_MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    print("counties            %6d" % len(counties))
    print("matchable           %6d" % matchable)
    print("reference rows      %6d" % n_ref_rows)
    print("restrictive group   %6d" % len(groups["restrictive"][1]))
    print("wrote %s" % os.path.relpath(OUT_REFERENCE, HERE))
    print("wrote %s" % os.path.relpath(OUT_BENCHMARKS, HERE))
    print("wrote %s" % os.path.relpath(OUT_REPORT, HERE))
    print("wrote %s" % os.path.relpath(OUT_MANIFEST, HERE))
    return manifest


def _separation(ref):
    r = (ref.get("restrictive", {}).get("calibrated_score") or {}).get("median")
    n = (ref.get("non_restrictive", {}).get("calibrated_score") or {}).get("median")
    if r is None or n is None:
        return None
    return round(r - n, 6)


def _digest(path):
    import hashlib
    if not os.path.exists(path):
        return ""
    h = hashlib.blake2b(digest_size=8)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest():
    fails = []

    def check(name, cond, detail=""):
        if cond:
            print("  ok   %s" % name)
        else:
            print("  FAIL %s %s" % (name, detail))
            fails.append(name)

    # --- numerics ---
    check("median odd", median([3, 1, 2]) == 2)
    check("median even", median([1, 2, 3, 4]) == 2.5)
    check("median empty", median([]) is None)
    check("quantile matches numpy default", abs(quantile([1, 2, 3, 4], 0.25) - 1.75) < 1e-9)
    check("quantile p50 equals median", abs(quantile([1, 2, 3, 4], 0.5) - 2.5) < 1e-9)
    check("stdev of one value is None", stdev([5]) is None)
    check("stdev sample", abs(stdev([2, 4, 4, 4, 5, 5, 7, 9]) - 2.13808993529939) < 1e-9)

    # --- the tie rule, which is the whole reason percentile_of is hand-written ---
    zeros = sorted([0.0] * 9 + [5.0])
    check("mid-rank puts a tied zero mid-tie, not at 0",
          abs(percentile_of(0.0, zeros) - 45.0) < 1e-9,
          "got %s" % percentile_of(0.0, zeros))
    check("mid-rank tops out below 100 for the max",
          abs(percentile_of(5.0, zeros) - 95.0) < 1e-9)
    check("percentile of None is None", percentile_of(None, zeros) is None)
    check("percentile against empty group is None", percentile_of(1.0, []) is None)
    check("median value reads 50",
          abs(percentile_of(3, [1, 2, 3, 4, 5]) - 50.0) < 1e-9)

    # --- parsing ---
    check("blank parses to None not zero", _num("") is None)
    check("garbage parses to None", _num("n/a") is None)
    check("zero parses to zero", _num("0") == 0.0)
    check("nan rejected", _num("nan") is None)
    check("safe_log of zero is finite", _safe_log(0) == 0.0)
    check("safe_log of blank is None", _safe_log("") is None)

    # --- derived rates guard their denominators ---
    rec = {"agg": {"population": "0", "existing_dc_count": "0",
                   "n_opposition_events": "4", "n_projects_tracked": "0",
                   "n_projects_opposed": "0", "n_decided": "0",
                   "n_blocked_confirmed": "0"},
           "score": None, "interval": None}
    v = metric_values(rec)
    check("no per-100k rate on zero population", v["opposition_events_per_100k"] is None)
    check("no per-dc rate on zero data centers", v["opposition_events_per_dc"] is None)
    check("no opposed share on zero tracked", v["opposed_share_of_tracked"] is None)
    check("no blocked share on zero decided", v["blocked_share_of_decided"] is None)

    rec2 = {"agg": {"population": "200000", "existing_dc_count": "2",
                    "n_opposition_events": "4", "n_projects_tracked": "8",
                    "n_projects_opposed": "2", "n_decided": "4",
                    "n_blocked_confirmed": "1"},
            "score": None, "interval": None}
    v2 = metric_values(rec2)
    check("per-100k rate arithmetic", abs(v2["opposition_events_per_100k"] - 2.0) < 1e-9)
    check("per-dc rate arithmetic", abs(v2["opposition_events_per_dc"] - 2.0) < 1e-9)
    check("blocked share arithmetic", abs(v2["blocked_share_of_decided"] - 0.25) < 1e-9)

    # --- peer matching on a synthetic frame ---
    def fake(fips, pop, dens, ba, margin, restr):
        r = {"fips": fips, "county_name": fips, "state": "ZZ",
             "has_enacted_restrictive": restr,
             "agg": {"population": pop, "pop_density_sqmi": dens,
                     "pct_bachelors_plus": ba, "margin_2024": margin,
                     "existing_dc_count": "0", "n_opposition_events": "0"},
             "score": None, "interval": None}
        r["values"] = metric_values(r)
        return r

    frame = [fake("00001", 100000, 100, 30, 0.1, False),
             fake("00002", 101000, 101, 30.5, 0.11, True),
             fake("00003", 5000000, 9000, 60, 0.6, False),
             fake("00004", 5100000, 9100, 61, 0.61, True),
             fake("00005", "", 50, 12, -0.4, False)]
    peers, _stats, matchable = find_peers(frame, 2)
    check("county missing a feature is not matched", "00005" not in peers)
    check("matchable count excludes it", matchable == 4)
    check("nearest peer is the structural twin",
          peers["00001"]["peers"][0][0] == "00002",
          "got %s" % peers["00001"]["peers"][0][0])
    check("large-metro county matches the other metro",
          peers["00003"]["peers"][0][0] == "00004")
    check("peer distances are sorted",
          peers["00001"]["peers"][0][1] <= peers["00001"]["peers"][1][1])
    check("no county is its own peer",
          all(f != "00001" for f, _d in peers["00001"]["peers"]))

    # --- proximity index arithmetic, including the deliberate lack of clipping ---
    groups, ref = build_reference(frame)
    check("state group emitted", "state:ZZ" in ref)
    check("restrictive group has two members", len(groups["restrictive"][1]) == 2)

    # --- the pruned search must agree with an unpruned brute force ---
    # The pruning in find_peers compares squared partial sums against a squared
    # cutoff. Getting that mixed up returns plausible-looking but wrong
    # neighbours, which no eyeball check would catch, so it is verified against
    # a naive implementation on a spread-out random frame where distances run
    # well above 1 -- the range where a rooted cutoff would misbehave.
    import random
    rng = random.Random(20260915)
    rand_frame = []
    for i in range(120):
        rand_frame.append(fake("%05d" % (10000 + i),
                               rng.uniform(500, 6_000_000),
                               rng.uniform(1, 12000),
                               rng.uniform(5, 70),
                               rng.uniform(-0.9, 0.9), False))
    pruned, _s, _n = find_peers(rand_frame, 5)
    naive_vectors, _st, naive_eligible = standardise(rand_frame)
    mismatches = 0
    for c in naive_eligible:
        f = c["fips"]
        v = naive_vectors[f]
        alld = []
        for o in naive_eligible:
            if o["fips"] == f:
                continue
            w = naive_vectors[o["fips"]]
            alld.append((math.sqrt(sum((v[i] - w[i]) ** 2 for i in range(len(v)))),
                         o["fips"]))
        alld.sort()
        want = [x[1] for x in alld[:5]]
        got = [x[0] for x in pruned[f]["peers"]]
        if want != got:
            mismatches += 1
    check("pruned nearest-neighbour search matches brute force",
          mismatches == 0, "%d of %d counties differed" % (mismatches, len(naive_eligible)))
    check("random-frame distances exceed 1, so pruning was exercised",
          max(d for f in pruned for _p, d in pruned[f]["peers"]) > 1.0)

    synthetic_ref = {
        "restrictive": {"calibrated_score": {"median": 0.70, "sorted": [0.70]}},
        "non_restrictive": {"calibrated_score": {"median": 0.10, "sorted": [0.10]}},
        "national": {}, "tracked_activity": {},
    }
    probe = fake("00006", 100000, 100, 30, 0.1, False)
    probe["values"]["calibrated_score"] = 0.40
    _c, rows = build_benchmarks([probe], synthetic_ref, {}, 2)
    check("gap to restrictive median is signed and raw",
          rows[0]["score_gap_to_restrictive_median"] == "-0.3000",
          "got %s" % rows[0]["score_gap_to_restrictive_median"])
    check("no unbounded proximity index is published",
          "restrictive_proximity_index" not in rows[0])
    check("unmatched county reports peer_matched=0", rows[0]["peer_matched"] == "0")
    check("unmatched county has blank peer restriction rate",
          rows[0]["peer_restriction_rate"] == "")

    # --- every declared metric has a legal direction ---
    legal = {"up_is_more_restrictive", "up_is_less_restrictive", "neutral"}
    check("all metric directions legal",
          all(m[3] in legal for m in METRICS))
    check("wide metrics are all declared",
          all(m in METRIC_BY_KEY for m in WIDE_METRICS))

    print("")
    if fails:
        print("%d check(s) FAILED: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("all checks passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="build the benchmark layer")
    ap.add_argument("--selftest", action="store_true", help="run internal checks")
    ap.add_argument("--peers", type=int, default=DEFAULT_PEERS,
                    help="peers per county (default %d)" % DEFAULT_PEERS)
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.build:
        build(k=args.peers)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
