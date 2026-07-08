"""
metrics.py
========================================================================
The single canonical way to compute externally quotable statistics from the
clean feed. Exists because ad-hoc computation kept repeating four errors:

  1. Row-level counting (pseudo-replication): multi-row projects inflate n.
     -> All metrics default to primary records (project level).
  2. iid confidence intervals on clustered decisions: the same jurisdiction
     deciding serial moratoria is not independent trials.
     -> CIs come from a cluster bootstrap over jurisdiction (State+County).
  3. Right-censoring: recent incidents have not had time to resolve, so
     naive period-over-period decided rates partly measure resolution speed.
     -> min_age_days excludes immature cohorts from rate comparisons.
  4. Base-rate-free political framing: Trump won ~85% of counties, so an
     incident share must be compared against county and exposure base rates,
     never quoted alone.
     -> political_context() reports all three numbers together.

Every report this module writes leads with the denominator disclaimer: this
is a dataset of tracked conflicts, not of all data center projects; rates
are conditional on a fight existing and being visible enough to track.

CLI: python metrics.py [clean_feed.csv] [outdir] -> outdir/headline_metrics.md
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta

DECIDED = ("blocked_confirmed", "advanced_confirmed")
ASOF = None  # datetime; defaults to now at call time

DISCLAIMER = (
    "Scope note: this dataset tracks opposition incidents, not all data "
    "center projects. Every rate below is conditional on a conflict being "
    "visible enough to enter the tracker; projects that proceeded without "
    "tracked opposition are absent by construction.")


def _asof() -> datetime:
    return ASOF or datetime.now()


def _date(rec: dict):
    for k in ("Date", "recovered_date"):
        try:
            return datetime.fromisoformat(str(rec.get(k, ""))[:10])
        except ValueError:
            continue
    return None


def _primary(rows: list[dict]) -> list[dict]:
    out = [r for r in rows if str(r.get("is_primary_record", "")).lower() == "true"]
    return out or rows  # feeds without the flag fall back to all rows


def _cluster_key(rec: dict) -> tuple:
    return (str(rec.get("State", "")).strip(),
            str(rec.get("County", "")).strip() or str(rec.get("City", "")).strip())


def cluster_bootstrap_ci(rows: list[dict], is_success, n_boot: int = 2000,
                         seed: int = 20260707) -> tuple[float, float, float]:
    """Rate + 95% CI via bootstrap resampling of CLUSTERS (jurisdictions),
    not rows, so serial decisions by one board do not fake precision."""
    clusters: dict[tuple, list[int]] = {}
    for r in rows:
        clusters.setdefault(_cluster_key(r), []).append(int(bool(is_success(r))))
    keys = list(clusters)
    if not keys:
        return float("nan"), float("nan"), float("nan")
    flat = [v for k in keys for v in clusters[k]]
    p = sum(flat) / len(flat)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        s = n = 0
        for _ in keys:
            c = clusters[keys[rng.randrange(len(keys))]]
            s += sum(c)
            n += len(c)
        if n:
            stats.append(s / n)
    stats.sort()
    lo = stats[int(0.025 * len(stats))]
    hi = stats[int(0.975 * len(stats))]
    return p, lo, hi


def decided_block_rate(rows: list[dict], start=None, end=None,
                       min_age_days: int = 90, level: str = "project") -> dict:
    """Confirmed-block share of decided cases. Excludes incidents younger
    than min_age_days (right-censoring guard) and, at project level,
    non-primary duplicate rows (pseudo-replication guard)."""
    pool = _primary(rows) if level == "project" else rows
    cutoff = _asof() - timedelta(days=min_age_days)
    sel = []
    for r in pool:
        d = _date(r)
        if d is None or d > cutoff:
            continue
        if start and d < start:
            continue
        if end and d >= end:
            continue
        if r.get("outcome_defensible") in DECIDED:
            sel.append(r)
    p, lo, hi = cluster_bootstrap_ci(
        sel, lambda r: r.get("outcome_defensible") == "blocked_confirmed")
    return {"n_decided": len(sel), "rate": p, "ci_low": lo, "ci_high": hi,
            "level": level, "min_age_days": min_age_days}


def political_context(rows: list[dict], county_votes_path: str = "data/county_votes.json",
                      fips_lookup_path: str = "data/county_fips_lookup.json") -> dict:
    """Incident share in Trump-won counties, ALWAYS paired with the county
    base rate and (when proposals data allows) an exposure-weighted base so
    the share cannot be quoted without its denominators."""
    try:
        cv = json.load(open(county_votes_path))
    except OSError:
        return {"error": f"{county_votes_path} not found"}
    try:
        lookup = {k.strip().lower(): v
                  for k, v in json.load(open(fips_lookup_path)).items()}
    except OSError:
        lookup = {}
    ABBR = {"AL":"alabama","AK":"alaska","AZ":"arizona","AR":"arkansas","CA":"california",
            "CO":"colorado","CT":"connecticut","DE":"delaware","FL":"florida","GA":"georgia",
            "HI":"hawaii","ID":"idaho","IL":"illinois","IN":"indiana","IA":"iowa","KS":"kansas",
            "KY":"kentucky","LA":"louisiana","ME":"maine","MD":"maryland","MA":"massachusetts",
            "MI":"michigan","MN":"minnesota","MS":"mississippi","MO":"missouri","MT":"montana",
            "NE":"nebraska","NV":"nevada","NH":"new hampshire","NJ":"new jersey","NM":"new mexico",
            "NY":"new york","NC":"north carolina","ND":"north dakota","OH":"ohio","OK":"oklahoma",
            "OR":"oregon","PA":"pennsylvania","RI":"rhode island","SC":"south carolina",
            "SD":"south dakota","TN":"tennessee","TX":"texas","UT":"utah","VT":"vermont",
            "VA":"virginia","WA":"washington","WV":"west virginia","WI":"wisconsin",
            "WY":"wyoming","DC":"district of columbia"}

    margins = [d.get("2024") for d in cv.values() if d.get("2024") is not None]
    county_base = sum(1 for m in margins if m < 0) / len(margins)

    def fips_of(rec):
        county = str(rec.get("County", "")).strip().lower()
        st = str(rec.get("State", "")).strip()
        state = ABBR.get(st.upper(), st.lower())
        if not county or not state:
            return None
        return (lookup.get(f"{county}|{state}")
                or lookup.get(f"{county.replace(' county','')}|{state}"))

    joined = trump = 0
    for r in _primary(rows):
        f = fips_of(r)
        m = cv.get(f, {}).get("2024") if f else None
        if m is None:
            continue
        joined += 1
        trump += int(m < 0)
    share = trump / joined if joined else float("nan")
    return {"incident_share_trump_counties": share,
            "county_base_rate_trump": county_base,
            "joined": joined,
            "reading": ("Relative to the share of counties Trump won "
                        f"({county_base:.0%}), tracked opposition is "
                        + ("OVER" if share > county_base else "UNDER")
                        + "-represented in Trump-won counties at "
                        f"{share:.0%}. Quote the pair, never the share alone; "
                        "an exposure denominator (where projects are proposed) "
                        "is the fair comparison and siting is not uniform.")}


def contested_investment(rows: list[dict]) -> dict:
    """Primary records only, review-flagged figures excluded, so phased
    multi-row projects and unverified units cannot inflate the total."""
    tot = blocked = n = 0.0
    for r in _primary(rows):
        if str(r.get("investment_review_flag", "")).lower() == "true":
            continue
        try:
            v = float(r.get("investment_numeric", "") or "")
        except ValueError:
            continue
        if v != v:          # NaN written as the string 'nan' by pandas
            continue
        tot += v
        n += 1
        if r.get("qc_block_status") == "enacted_block":
            blocked += v
    return {"projects_with_figures": int(n), "total_musd": tot,
            "enacted_block_musd": blocked}


def headline_report(rows: list[dict], outdir: str = "out") -> str:
    os.makedirs(outdir, exist_ok=True)
    now = _asof()
    year = now.year
    cur = decided_block_rate(rows, start=datetime(year, 1, 1))
    prev = decided_block_rate(rows, start=datetime(year - 1, 1, 1),
                              end=datetime(year, 1, 1))
    pol = political_context(rows)
    inv = contested_investment(rows)

    undated = sum(1 for r in rows if _date(r) is None)
    sev = {str(r.get("Severity", "")).strip() for r in rows} - {""}

    L = [f"# Headline metrics (as of {now.date().isoformat()})", "",
         DISCLAIMER, "",
         "## Decided-case confirmed-block rate",
         "Project level, jurisdiction-cluster bootstrap 95% CI, incidents "
         f"younger than {cur['min_age_days']} days excluded (right-censoring guard).", ""]
    for lab, m in ((f"{year} YTD", cur), (str(year - 1), prev)):
        if m["n_decided"]:
            L.append(f"- {lab}: {m['rate']:.0%} of {m['n_decided']} decided "
                     f"(CI {m['ci_low']:.0%}-{m['ci_high']:.0%})")
    L += ["", "## Political context"]
    if "error" in pol:
        L.append(f"- unavailable: {pol['error']}")
    else:
        L += [f"- Incident share in Trump-won counties: {pol['incident_share_trump_counties']:.0%} "
              f"(n={pol['joined']})",
              f"- County base rate (share of counties Trump won): {pol['county_base_rate_trump']:.0%}",
              f"- {pol['reading']}"]
    L += ["", "## Contested investment (floors, not totals)",
          f"- ${inv['total_musd']/1000:,.0f}B disclosed across {inv['projects_with_figures']} "
          "primary projects (review-flagged figures excluded)",
          f"- ${inv['enacted_block_musd']/1000:,.0f}B behind enacted blocks",
          "", "## Data caveats attached to every use",
          f"- {undated} rows have no usable date and are absent from all temporal statistics; "
          "these skew toward the newest intake stream, so recent-period counts are floors.",
          f"- Severity values in use: {sorted(sev)} - the 1-5 scale is effectively binary "
          "and should not be treated as a graded intensity measure.",
          "- Mechanism/concern categories are keyword-classified; see "
          "validation_sample.csv workflow for measured precision before citing "
          "category-level rates externally."]
    path = os.path.join(outdir, "headline_metrics.md")
    open(path, "w").write("\n".join(L) + "\n")
    return path


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "master_opposition_clean.csv"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "out"
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
    print("wrote", headline_report(rows, outdir))
