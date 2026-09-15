#!/usr/bin/env python3
"""
restriction_evidence.py

Turns "no restriction record was found" into "these sources were checked on
this date and found none", per county, for the whole national frame.

Why this module exists
----------------------
data/county_aggregate.csv carries 3222 counties. 329 are labeled
has_enacted_restrictive = 1 and 2893 are labeled 0. Before this module, every
one of those 2893 negatives was an inference from silence: no row in the
tracker asserted a restriction, so the county was trained, mapped and reported
as unrestricted. Nothing recorded a single source consulted for any of them,
and no schema field could have held one.

That is a different and larger exposure than the coverage gap the platform
already measures. coverage_audit.py asks "of the counties an external census
says are restricted, how many do we hold?" and reports national recall_any
0.989, which reads like coverage is close to solved. It is measured against a
census of 87 counties in 17 states, which is 2.7 percent of the frame, while
the tracker itself asserts positives across 43 states. The 26 states of
positives with no external corroboration, and the entire negative class, sit
outside that denominator. Recall against a census cannot see them, because a
census that never lists a county produces no gap row for it.

So the unit here is the COUNTY, not the gap, and the frame is every county,
not the census. The output is an evidence ledger: for each county, which
source families were consulted, when, what they returned, and what grade the
resulting claim earns.

What it does not do
-------------------
It does not change has_enacted_restrictive, does not write master_opposition.csv,
does not infer a restriction, and does not decide that a county is clear. It
records what was checked and grades the strength of the resulting claim. A
county nothing covers is graded U and reported as unverified, which is the
honest state and is the state most of the frame starts in. Raising a grade
requires a probe to have actually run and returned something.

Independence classes
--------------------
Grades count DISTINCT independence classes (primary_law, proceeding,
secondary), never distinct sources. Two compilations that both read press
coverage can be wrong together, and counting them as two checks would
manufacture confidence the evidence does not support. See
configs/restriction_evidence_sources.json for the registered rule.

A census can raise a positive grade and can never on its own clear a county.
A census is a lower bound: it lists restrictions it found, and its silence
about a county is not a finding. Registering that asymmetry in the config
(clears_negative: false) rather than in the grader keeps it visible.

Conflicts are the ongoing QC check
----------------------------------
Two directions, both written to data/restriction_evidence_conflicts.csv:

  label_negative_source_hit    the county is labeled clear and a registered
                               source reports a restriction. A false negative
                               in the model's target variable, and the highest
                               priority row the platform can produce.
  label_positive_no_support    the county is labeled restrictive and no
                               registered source corroborates it. Not
                               necessarily wrong, since the tracker sees things
                               a census does not, but it is a claim resting on
                               one reading of one record.

This is the recurring external check on updates: the conflict file is rebuilt
on every pipeline run, so a newly promoted row that disagrees with an external
source surfaces on the next build rather than at the next manual audit.

Inputs (every one optional; an absent input degrades the grade, never crashes)
  data/county_aggregate.csv              the frame and the current label
  configs/restriction_evidence_sources.json   source registry
  data/restriction_probe_cache.json      probe results (restriction_probe.py)
  data/external_restriction_census.csv   census positives
  data/coverage_gap_report.csv           known gaps, for cross-reference

Outputs
  data/restriction_evidence.csv           one row per county, merge-ready
  data/restriction_evidence_conflicts.csv rows needing human resolution
  data/restriction_evidence_history.csv   append-only grade-change log
  data/restriction_evidence_report.md     national and per-state summary

Standing rules honored: four-tier vocabulary only, additive, writes only its
own files, no em-dashes, no scorekeeping vocabulary, LF line endings, stdlib
only, --selftest with no data or network dependency.

Usage
  python restriction_evidence.py
  python restriction_evidence.py --state IN
  python restriction_evidence.py --strict      exit nonzero on QC failure
  python restriction_evidence.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(HERE, *parts)


AGG_CSV = P("data", "county_aggregate.csv")
REGISTRY = P("configs", "restriction_evidence_sources.json")
PROBE_CACHE = P("data", "restriction_probe_cache.json")
CENSUS_CSV = P("data", "external_restriction_census.csv")
# Upstream rows the refresh surfaced but nobody has reviewed yet. Held to a
# lower standing than the seeded census on purpose: it can raise a
# CANDIDATE false negative and it can never corroborate a positive or
# clear a county.
DELTA_CSV = P("data", "external_restriction_census_delta.csv")
GAP_CSV = P("data", "coverage_gap_report.csv")

OUT_CSV = P("data", "restriction_evidence.csv")
OUT_CONFLICTS = P("data", "restriction_evidence_conflicts.csv")
OUT_HISTORY = P("data", "restriction_evidence_history.csv")
OUT_MD = P("data", "restriction_evidence_report.md")

FIELDS = [
    "fips", "county_name", "state",
    "label_state", "evidence_grade",
    "families_checked", "families_covered", "independence_classes",
    "sources_clear", "sources_hit", "sources_unreachable",
    "last_checked", "staleness_days", "probe_status",
    "in_force_as_of", "evidence_url_primary",
    "conflict", "needs_recheck",
]

CONFLICT_FIELDS = [
    "fips", "county_name", "state", "conflict", "label_state",
    "evidence_grade", "detail", "source_id", "source_url", "observed_at",
]

GRADES = ("A", "B", "C", "D", "U")

# Default freshness if the registry is absent. The registry is the source of
# record for both values; these exist so --selftest and a degraded run behave.
DEFAULT_FRESHNESS_DAYS = 180
DEFAULT_RECHECK_DAYS = 90


# ---------------------------------------------------------------------------
# small IO helpers
# ---------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


# Consolidated city-county governments, mirroring coverage_audit.py. A census
# names the consolidated government ("Athens-Clarke County") while the national
# frame names the county ("Clarke County, Georgia"). State-scoped because the
# short name is not unique across states.
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
    """Normalize a county name for joining.

    Mirrors coverage_audit.norm_county: strip the state suffix, parentheticals
    and governing-body suffixes, then apply the consolidated-government alias
    table. Kept local rather than imported so --selftest has no import
    dependency.

    The state-suffix split is load-bearing and was missing from the first cut of
    this module. data/county_aggregate.csv spells a county "Autauga County,
    Alabama" while every census spells it "Autauga County", so without the
    split every one of the 94 census rows failed to join, the ledger recorded
    zero corroborated positives, and all 329 positives read as
    label_positive_no_support. A join failure here is silent by construction:
    it produces a clean-looking ledger full of unsupported labels rather than an
    error, which is the same defect coverage_audit.py documents in its own
    docstring. The selftest below pins the frame spelling for that reason.
    """
    s = (name or "").split(",")[0].strip().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(fiscal court|board of supervisors|board of commissioners|"
               r"county commission|commissioners court)\b", " ", s)
    s = re.sub(r"\b(county|parish|borough|municipio|census area|city and borough)\b",
               " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    n = " ".join(s.split())
    alias_key = (str(state or "").strip().upper(), n.replace(" ", ""))
    return CONSOLIDATED_ALIASES.get(alias_key, n)


def today() -> dt.date:
    return dt.date.today()


def parse_date(v: str):
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def load_registry(path: str = REGISTRY) -> dict:
    reg = read_json(path, {})
    if not reg:
        # A missing registry means nothing is registered as covering anything,
        # which grades the whole frame U. That is the correct degraded result:
        # it never invents coverage.
        reg = {"families": {}, "freshness_days": DEFAULT_FRESHNESS_DAYS,
               "recheck_days": DEFAULT_RECHECK_DAYS}
    reg.setdefault("freshness_days", DEFAULT_FRESHNESS_DAYS)
    reg.setdefault("recheck_days", DEFAULT_RECHECK_DAYS)
    reg.setdefault("families", {})
    return reg


def family_class(reg: dict, family: str) -> str:
    return (reg["families"].get(family, {}) or {}).get(
        "independence_class", "secondary")


def family_clears_negative(reg: dict, family: str) -> bool:
    """Whether a clean result from this family is evidence of absence.

    False for censuses: a compilation's silence about a county is not a
    finding about the county.
    """
    fam = reg["families"].get(family, {}) or {}
    return bool(fam.get("clears_negative", True))


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------

def grade_county(probes: list[dict], reg: dict, label_positive: bool,
                 asof: dt.date):
    """Grade the evidence behind one county's restriction label.

    probes: list of {family, source_id, result, observed_at, url, detail}
            result in {clear, hit, unreachable, not_covered}

    Returns (grade, detail dict). The grade describes the strength of the
    CHECK, not of the restriction.
    """
    fresh_days = reg["freshness_days"]

    clear_fresh, clear_stale = set(), set()
    hit_fresh, hit_stale = set(), set()
    unreachable = set()
    covered = set()
    checked = set()
    dates = []
    hit_urls = []
    in_force = []

    for p in probes:
        fam = p.get("family", "")
        res = (p.get("result") or "").strip()
        if res == "not_covered":
            continue
        covered.add(fam)
        if res == "unreachable":
            unreachable.add(fam)
            checked.add(fam)
            continue
        if res == "hit_unreviewed":
            # An upstream assertion no one has checked. It is enough to ask a
            # question about a county labeled clear and not enough to answer
            # one, so it never reaches a grade in either direction. Treating it
            # as corroboration would let an unreviewed row raise a positive's
            # grade, which is the defensibility rule this platform is built on.
            continue
        if res == "pending_instrument":
            # A sought-but-unenacted instrument. It neither corroborates an
            # enacted label nor clears the county, and it must not raise a
            # false-negative conflict: has_enacted_restrictive is a record of
            # ENACTMENT, so a county with only a pending moratorium is
            # correctly labeled 0. Counted as covered so the county grades D
            # (a source reaches it) rather than U (nothing does).
            continue
        if res not in ("clear", "hit"):
            continue
        checked.add(fam)
        obs = parse_date(p.get("observed_at", ""))
        if obs:
            dates.append(obs)
        fresh = bool(obs and (asof - obs).days <= fresh_days)
        cls = family_class(reg, fam)
        if res == "hit":
            (hit_fresh if fresh else hit_stale).add(cls)
            if p.get("url"):
                hit_urls.append(p["url"])
            if p.get("in_force_as_of"):
                in_force.append(p["in_force_as_of"])
        else:
            # A family that cannot clear a negative still counts as checked,
            # and still contributes nothing to a clearance grade.
            if family_clears_negative(reg, fam):
                (clear_fresh if fresh else clear_stale).add(cls)

    if label_positive:
        supporting_fresh, supporting_stale = hit_fresh, hit_stale
    else:
        supporting_fresh, supporting_stale = clear_fresh, clear_stale

    n_fresh = len(supporting_fresh)
    n_any = len(supporting_fresh | supporting_stale)

    if n_fresh >= 2:
        grade = "A"
    elif n_fresh == 1 or n_any >= 2:
        grade = "B"
    elif unreachable or n_any == 1:
        grade = "C"
    elif covered:
        grade = "D"
    else:
        grade = "U"

    last = max(dates) if dates else None
    detail = {
        "families_checked": sorted(checked),
        "families_covered": sorted(covered),
        "independence_classes": sorted(supporting_fresh | supporting_stale),
        "sources_clear": sum(1 for p in probes if p.get("result") == "clear"),
        "sources_hit": sum(1 for p in probes if p.get("result") == "hit"),
        "sources_pending": sum(1 for p in probes
                               if p.get("result") == "pending_instrument"),
        "sources_unreachable": len(unreachable),
        "last_checked": last.isoformat() if last else "",
        "staleness_days": (asof - last).days if last else "",
        "hit_urls": hit_urls,
        "in_force_as_of": max(in_force) if in_force else "",
        "any_hit": bool(hit_fresh or hit_stale),
    }
    return grade, detail


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def census_probes(census_rows: list[dict], asof: dt.date,
                  reviewed: bool = True, source_id: str = "external_restriction_census") -> dict:
    """Census rows become restriction_census probes keyed by (state, county).

    The census asserts positives only, so it emits a hit and never a clear.

    reviewed=False marks rows from the refresh delta: upstream asserts them,
    nobody here has checked them, and they are graded as hit_unreviewed.
    """
    out = defaultdict(list)
    for r in census_rows:
        state = (r.get("state") or "").strip().upper()
        key = (state, norm_county(r.get("county", ""), state))
        if not key[0] or not key[1]:
            continue
        status = (r.get("census_status") or "").strip().lower()
        enacted = parse_date(r.get("date_enacted", ""))
        # Status decides whether this row asserts an ENACTED instrument.
        # "pending" describes one that was sought and not adopted, so it
        # cannot corroborate has_enacted_restrictive and cannot contradict a
        # county labeled clear. Every other census status describes an
        # instrument that was adopted at some point, which is what the
        # historical label records, including ones later expired, replaced or
        # rescinded.
        result = ("pending_instrument" if status == "pending"
                  else ("hit" if reviewed else "hit_unreviewed"))
        out[key].append({
            "family": "restriction_census",
            "source_id": source_id,
            "result": result,
            "observed_at": asof.isoformat(),
            "url": (r.get("source") or "").strip(),
            "detail": f"{r.get('instrument','')} ({status})",
            # Only an in-force status asserts the instrument still binds.
            "in_force_as_of": (enacted.isoformat()
                               if enacted and status in ("active", "extended")
                               else ""),
        })
    return out


def build(frame: list[dict], probe_cache: dict, census_rows: list[dict],
          reg: dict, asof: dt.date, delta_rows: list[dict] | None = None):
    """Build the evidence ledger over the full county frame."""
    cen = census_probes(census_rows, asof)
    for key, probes in census_probes(
            delta_rows or [], asof, reviewed=False,
            source_id="external_restriction_census_delta").items():
        cen.setdefault(key, []).extend(probes)
    rows, conflicts = [], []

    for rec in frame:
        fips = (rec.get("fips") or "").strip()
        if not fips:
            continue
        state = (rec.get("state") or "").strip().upper()
        cname = (rec.get("county_name") or "").strip()
        label_positive = str(rec.get("has_enacted_restrictive", "")).strip() == "1"

        probes = list(probe_cache.get(fips, []))
        probes.extend(cen.get((state, norm_county(cname, state)), []))

        grade, d = grade_county(probes, reg, label_positive, asof)

        label_state = ("asserted_restrictive" if label_positive
                       else ("asserted_clear" if grade in ("A", "B")
                             else "unverified"))

        # Conflicts. Both directions are reported, neither is reconciled here:
        # resolving one is a records decision, not a measurement.
        any_unreviewed = any(p.get("result") == "hit_unreviewed" for p in probes)
        conflict = ""
        if not label_positive and d["any_hit"]:
            conflict = "label_negative_source_hit"
        elif not label_positive and any_unreviewed:
            # An upstream row the refresh surfaced asserts a restriction here
            # and no reviewed source does. A candidate false negative: it needs
            # a person to confirm the instrument before it can move a label.
            conflict = "label_negative_upstream_hit"
        elif label_positive and not d["any_hit"]:
            conflict = "label_positive_no_support"

        if conflict:
            src = next((p for p in probes
                        if p.get("result") in ("hit", "hit_unreviewed")), {})
            conflicts.append({
                "fips": fips, "county_name": cname, "state": state,
                "conflict": conflict, "label_state": label_state,
                "evidence_grade": grade,
                "detail": (src.get("detail", "") if conflict.endswith("hit")
                           else "no reviewed source corroborates the label"),
                "source_id": src.get("source_id", ""),
                "source_url": src.get("url", ""),
                "observed_at": src.get("observed_at", ""),
            })

        stale = d["staleness_days"]
        needs = 1 if (grade in ("C", "D", "U")
                      or (isinstance(stale, int) and stale > reg["recheck_days"])
                      or conflict) else 0

        rows.append({
            "fips": fips, "county_name": cname, "state": state,
            "label_state": label_state,
            "evidence_grade": grade,
            "families_checked": "|".join(d["families_checked"]),
            "families_covered": "|".join(d["families_covered"]),
            "independence_classes": "|".join(d["independence_classes"]),
            "sources_clear": d["sources_clear"],
            "sources_hit": d["sources_hit"],
            "sources_unreachable": d["sources_unreachable"],
            "last_checked": d["last_checked"],
            "staleness_days": stale,
            "probe_status": ("never_probed" if not d["families_checked"]
                             else ("partial" if d["sources_unreachable"]
                                   else "complete")),
            "in_force_as_of": d["in_force_as_of"],
            "evidence_url_primary": (d["hit_urls"][0] if d["hit_urls"] else ""),
            "conflict": conflict,
            "needs_recheck": needs,
        })

    rows.sort(key=lambda r: r["fips"])
    _conflict_rank = {"label_negative_source_hit": 0,
                      "label_negative_upstream_hit": 1,
                      "label_positive_no_support": 2}
    conflicts.sort(key=lambda r: (_conflict_rank.get(r["conflict"], 9),
                                  r["state"], r["county_name"]))
    return rows, conflicts


# ---------------------------------------------------------------------------
# QC gate
# ---------------------------------------------------------------------------

def qc(rows: list[dict], frame: list[dict], conflicts: list[dict]) -> list[str]:
    """Blocking checks. A degraded ledger must never replace a good one."""
    failures = []

    frame_fips = {(r.get("fips") or "").strip() for r in frame if r.get("fips")}
    row_fips = {r["fips"] for r in rows}
    if row_fips != frame_fips:
        missing = len(frame_fips - row_fips)
        extra = len(row_fips - frame_fips)
        failures.append(
            f"frame parity: {missing} county(ies) in the aggregate with no "
            f"evidence row, {extra} evidence row(s) with no county")
    if len(row_fips) != len(rows):
        failures.append("duplicate fips in the evidence ledger")

    bad = [r["fips"] for r in rows if r["evidence_grade"] not in GRADES]
    if bad:
        failures.append(f"{len(bad)} row(s) carry a grade outside {GRADES}")

    # A county cannot be both asserted clear and carrying a source hit. That
    # combination means the grader cleared a county a source says is
    # restricted, which would put a false negative into the model silently.
    both = [r["fips"] for r in rows
            if r["label_state"] == "asserted_clear"
            and (int(r["sources_hit"] or 0) or r["conflict"].startswith("label_negative"))]
    if both:
        failures.append(
            f"{len(both)} county(ies) asserted clear while a source reports a "
            f"restriction")

    # Grade A demands two independence classes by construction. If one appears
    # with fewer, the grader and the registered rule have diverged.
    thin = [r["fips"] for r in rows
            if r["evidence_grade"] == "A"
            and len([c for c in r["independence_classes"].split("|") if c]) < 2]
    if thin:
        failures.append(f"{len(thin)} grade-A row(s) rest on one independence class")

    if conflicts and not rows:
        failures.append("conflicts reported against an empty ledger")

    return failures


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------

def write_csv(rows: list[dict], path: str, fields: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def append_history(rows: list[dict], path: str, asof: dt.date) -> int:
    """Append grade changes only. The ledger holds current state; this holds
    how it got there, so a grade that silently regresses is visible."""
    prior = {}
    if os.path.exists(path):
        for r in read_csv(path):
            prior[r.get("fips", "")] = r.get("evidence_grade", "")

    changed = [r for r in rows if prior.get(r["fips"]) != r["evidence_grade"]]
    if not changed:
        return 0

    fields = ["observed_at", "fips", "county_name", "state",
              "evidence_grade", "prior_grade", "label_state", "conflict"]
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        if new_file:
            w.writeheader()
        for r in changed:
            w.writerow({
                "observed_at": asof.isoformat(),
                "fips": r["fips"], "county_name": r["county_name"],
                "state": r["state"], "evidence_grade": r["evidence_grade"],
                "prior_grade": prior.get(r["fips"], ""),
                "label_state": r["label_state"], "conflict": r["conflict"],
            })
    return len(changed)


def summarize(rows: list[dict]) -> dict:
    by_state = defaultdict(lambda: Counter())
    natl = Counter()
    for r in rows:
        natl[r["evidence_grade"]] += 1
        natl[r["label_state"]] += 1
        by_state[r["state"]][r["evidence_grade"]] += 1
        by_state[r["state"]][r["label_state"]] += 1
        if r["conflict"]:
            natl[r["conflict"]] += 1
            by_state[r["state"]][r["conflict"]] += 1
    return {"national": dict(natl),
            "states": {k: dict(v) for k, v in sorted(by_state.items())},
            "total": len(rows)}


def write_report(rows: list[dict], conflicts: list[dict], reg: dict,
                 path: str, asof: dt.date) -> None:
    s = summarize(rows)
    n = s["total"] or 1
    natl = s["national"]

    def pct(k):
        return f"{100.0 * natl.get(k, 0) / n:.1f}"

    L = []
    L.append("# County Restriction Evidence Ledger")
    L.append("")
    L.append("Auto-generated by restriction_evidence.py. Do not edit by hand; "
             "rerun the module after data updates.")
    L.append("")
    L.append(f"Frame: {s['total']} counties. Generated {asof.isoformat()}. "
             f"Freshness window {reg['freshness_days']} days, "
             f"recheck cadence {reg['recheck_days']} days.")
    L.append("")
    L.append("**The grade describes how hard we looked, not how likely a "
             "restriction is.** For a county recorded as clear it counts "
             "independent checks that found nothing; for a county recorded as "
             "restrictive it counts independent sources that corroborate it. "
             "It is never a probability that a project draws opposition.")
    L.append("")
    L.append("## Label state")
    L.append("")
    L.append("| State of the claim | Counties | Share |")
    L.append("|---|---:|---:|")
    for k in ("asserted_restrictive", "asserted_clear", "unverified"):
        L.append(f"| {k} | {natl.get(k, 0)} | {pct(k)} pct |")
    L.append("")
    L.append("`asserted_clear` requires grade A or B: at least one independent "
             "family checked the county and found nothing, within the freshness "
             "window. Everything else is `unverified`, which is an honest "
             "statement that the county has not been checked, not a claim that "
             "it is restricted.")
    L.append("")
    L.append("## Evidence grade")
    L.append("")
    L.append("| Grade | Meaning | Counties | Share |")
    L.append("|---|---|---:|---:|")
    for g in GRADES:
        L.append(f"| {g} | {reg.get('grading', {}).get(g, '')} | "
                 f"{natl.get(g, 0)} | {pct(g)} pct |")
    L.append("")
    L.append("## Conflicts")
    L.append("")
    fn = natl.get("label_negative_source_hit", 0)
    ns = natl.get("label_positive_no_support", 0)
    L.append(f"- `label_negative_source_hit`: **{fn}**. The county is recorded "
             f"as clear and a registered source reports a restriction. Each one "
             f"is a false negative in the county model's target variable.")
    uh = natl.get("label_negative_upstream_hit", 0)
    L.append(f"- `label_negative_upstream_hit`: **{uh}**. The county is recorded "
             f"as clear and an UNREVIEWED upstream row asserts a restriction. A "
             f"candidate false negative: it needs a person to confirm the "
             f"instrument before it can move a label, and it is counted "
             f"separately from the confirmed ones above for that reason.")
    L.append(f"- `label_positive_no_support`: **{ns}**. The county is recorded "
             f"as restrictive and no reviewed source corroborates it. Not "
             f"necessarily wrong, since the tracker sees local records a "
             f"compilation does not, and worth a second reading.")
    L.append("")
    L.append("Full rows: `data/restriction_evidence_conflicts.csv`. Neither "
             "direction is reconciled by this module; resolving one is a "
             "records decision.")
    L.append("")
    L.append("## Per state")
    L.append("")
    L.append("| State | Counties | A | B | C | D | U | Clear | Unverified | Conflicts |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for st, c in sorted(s["states"].items()):
        tot = sum(c.get(g, 0) for g in GRADES)
        confl = (c.get("label_negative_source_hit", 0)
                 + c.get("label_negative_upstream_hit", 0)
                 + c.get("label_positive_no_support", 0))
        L.append(f"| {st} | {tot} | " + " | ".join(str(c.get(g, 0)) for g in GRADES)
                 + f" | {c.get('asserted_clear', 0)} | "
                   f"{c.get('unverified', 0)} | {confl} |")
    L.append("")
    L.append("## Standing caveats")
    L.append("")
    L.append("- A census can raise a positive grade and can never on its own "
             "clear a county. Its silence about a county is not a finding.")
    L.append("- Grade A requires two distinct independence classes. Two "
             "compilations that both read press coverage are one class, not two.")
    L.append("- `in_force_as_of` is populated only where a source reports an "
             "active or extended instrument. It is blank for historical "
             "instruments, and blank is not a statement that one lapsed.")
    L.append("- This ledger is additive. It does not change "
             "has_enacted_restrictive, and no model reads a grade as a feature.")
    L.append("")

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L))


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    asof = dt.date(2026, 9, 15)
    reg = {
        "freshness_days": 180,
        "recheck_days": 90,
        "grading": {g: "" for g in GRADES},
        "families": {
            "municipal_code": {"independence_class": "primary_law"},
            "meeting_portal": {"independence_class": "proceeding"},
            "restriction_census": {"independence_class": "secondary",
                                   "clears_negative": False},
        },
    }
    fresh = asof.isoformat()
    stale = (asof - dt.timedelta(days=400)).isoformat()
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: expected {want}, got {got}")

    # Two fresh classes clear -> A.
    g, _ = grade_county([
        {"family": "municipal_code", "result": "clear", "observed_at": fresh},
        {"family": "meeting_portal", "result": "clear", "observed_at": fresh},
    ], reg, False, asof)
    check("two fresh classes", g, "A")

    # Same class twice is one class, not two -> B.
    g, _ = grade_county([
        {"family": "municipal_code", "source_id": "municode",
         "result": "clear", "observed_at": fresh},
        {"family": "municipal_code", "source_id": "amlegal",
         "result": "clear", "observed_at": fresh},
    ], reg, False, asof)
    check("two sources one class", g, "B")

    # A census cannot clear a negative: its clean result contributes nothing.
    g, _ = grade_county([
        {"family": "restriction_census", "result": "clear", "observed_at": fresh},
    ], reg, False, asof)
    check("census cannot clear", g, "D")

    # Unreachable is C, never a clean check.
    g, _ = grade_county([
        {"family": "municipal_code", "result": "unreachable", "observed_at": fresh},
    ], reg, False, asof)
    check("unreachable", g, "C")

    # No family covers the county -> U, and not_covered never counts as covered.
    g, _ = grade_county([
        {"family": "municipal_code", "result": "not_covered", "observed_at": fresh},
    ], reg, False, asof)
    check("not covered", g, "U")

    # Nothing at all -> U.
    g, _ = grade_county([], reg, False, asof)
    check("no probes", g, "U")

    # Stale demotes A to B.
    g, _ = grade_county([
        {"family": "municipal_code", "result": "clear", "observed_at": stale},
        {"family": "meeting_portal", "result": "clear", "observed_at": stale},
    ], reg, False, asof)
    check("two stale classes", g, "B")

    # A positive grades on corroboration, not on clearance.
    g, _ = grade_county([
        {"family": "restriction_census", "result": "hit", "observed_at": fresh},
        {"family": "municipal_code", "result": "hit", "observed_at": fresh},
    ], reg, True, asof)
    check("positive corroborated twice", g, "A")

    # A pending instrument neither clears nor corroborates, and leaves the
    # county covered (D) rather than uncovered (U).
    g, _ = grade_county([
        {"family": "restriction_census", "result": "pending_instrument",
         "observed_at": fresh},
    ], reg, False, asof)
    check("pending does not clear", g, "D")
    g, _ = grade_county([
        {"family": "restriction_census", "result": "pending_instrument",
         "observed_at": fresh},
    ], reg, True, asof)
    check("pending does not corroborate", g, "D")

    # A county whose only census row is pending is correctly labeled clear and
    # must NOT be reported as a false negative.
    pend_frame = [{"fips": "19175", "county_name": "Union County, Iowa",
                   "state": "IA", "has_enacted_restrictive": "0"}]
    pend_census = [{"state": "IA", "county": "Union County",
                    "instrument": "moratorium", "census_status": "pending",
                    "date_enacted": "", "source": "https://example.org/union"}]
    prows, pconf = build(pend_frame, {}, pend_census, reg, asof)
    check("pending raises no false negative", prows[0]["conflict"], "")
    check("pending conflicts empty", len(pconf), 0)

    # An unreviewed upstream row raises a CANDIDATE false negative, and must
    # never corroborate a positive or clear a county.
    g, _ = grade_county([
        {"family": "restriction_census", "result": "hit_unreviewed",
         "observed_at": fresh},
    ], reg, True, asof)
    check("unreviewed does not corroborate", g, "D")

    up_frame = [{"fips": "39035", "county_name": "Cuyahoga County, Ohio",
                 "state": "OH", "has_enacted_restrictive": "0"},
                {"fips": "39001", "county_name": "Adams County, Ohio",
                 "state": "OH", "has_enacted_restrictive": "1"}]
    up_delta = [{"state": "OH", "county": "Cuyahoga County",
                 "instrument": "moratorium", "census_status": "active",
                 "date_enacted": "2026-05-01", "source": "https://example.org/cuy"},
                {"state": "OH", "county": "Adams County",
                 "instrument": "moratorium", "census_status": "active",
                 "date_enacted": "2026-05-02", "source": "https://example.org/ada"}]
    urows, uconf = build(up_frame, {}, [], reg, asof, delta_rows=up_delta)
    uby = {r["fips"]: r for r in urows}
    check("unreviewed raises candidate conflict",
          uby["39035"]["conflict"], "label_negative_upstream_hit")
    check("unreviewed is distinguishable from reviewed",
          uconf[0]["source_id"], "external_restriction_census_delta")
    check("unreviewed does not clear a positive's no-support conflict",
          uby["39001"]["conflict"], "label_positive_no_support")
    check("unreviewed does not count as a hit", uby["39035"]["sources_hit"], 0)

    # A hit on a county labeled clear must raise the false-negative conflict.
    frame = [
        {"fips": "18017", "county_name": "Cass County", "state": "IN",
         "has_enacted_restrictive": "0"},
        {"fips": "19169", "county_name": "Story County", "state": "IA",
         "has_enacted_restrictive": "1"},
        {"fips": "01001", "county_name": "Autauga County", "state": "AL",
         "has_enacted_restrictive": "0"},
    ]
    cache = {
        "18017": [{"family": "municipal_code", "result": "hit",
                   "observed_at": fresh, "url": "https://example.org/cass",
                   "detail": "ban", "source_id": "municode"}],
        "01001": [{"family": "municipal_code", "result": "clear",
                   "observed_at": fresh},
                  {"family": "meeting_portal", "result": "clear",
                   "observed_at": fresh}],
    }
    rows, conflicts = build(frame, cache, [], reg, asof)
    check("ledger rows", len(rows), 3)

    by = {r["fips"]: r for r in rows}
    check("cass conflict", by["18017"]["conflict"], "label_negative_source_hit")
    check("story conflict", by["19169"]["conflict"], "label_positive_no_support")
    check("autauga clear", by["01001"]["label_state"], "asserted_clear")
    check("autauga grade", by["01001"]["evidence_grade"], "A")
    check("autauga no conflict", by["01001"]["conflict"], "")
    check("conflict rows", len(conflicts), 2)
    # The false negative must sort first: it is the costlier defect.
    check("conflict ordering", conflicts[0]["conflict"],
          "label_negative_source_hit")

    # A county nothing covers stays unverified rather than becoming clear.
    check("story unverified-or-restrictive", by["19169"]["label_state"],
          "asserted_restrictive")

    # Census rows join through the normalizer and corroborate a positive.
    census = [{"state": "IA", "county": "Story County Board of Supervisors",
               "instrument": "moratorium", "census_status": "active",
               "date_enacted": "2026-02-02", "source": "https://example.org/story"}]
    rows2, conflicts2 = build(frame, cache, census, reg, asof)
    by2 = {r["fips"]: r for r in rows2}
    check("census joins through suffix", by2["19169"]["sources_hit"], 1)
    check("census clears the conflict", by2["19169"]["conflict"], "")
    check("in force recorded", by2["19169"]["in_force_as_of"], "2026-02-02")

    # QC gate: frame parity must fail loudly when a county goes missing.
    bad = [r for r in rows if r["fips"] != "01001"]
    if not qc(bad, frame, []):
        failures.append("qc: frame parity gap not detected")
    if qc(rows, frame, conflicts):
        failures.append(f"qc: clean ledger reported failures: "
                        f"{qc(rows, frame, conflicts)}")

    # Normalizer parity with the census join. The first four pin the exact
    # spellings that appear on each side of the join: the frame carries a state
    # suffix and the census does not, and the first cut of this module dropped
    # every census row because of it.
    check("norm frame spelling", norm_county("Autauga County, Alabama"), "autauga")
    check("norm census spelling", norm_county("Autauga County"), "autauga")
    check("frame and census agree",
          norm_county("Story County, Iowa") == norm_county("Story County"), True)
    check("norm consolidated",
          norm_county("Athens-Clarke County", "GA"), "clarke")
    check("alias is state scoped",
          norm_county("Athens-Clarke County", "IA"), "athens clarke")
    check("norm parentheticals", norm_county("Oliver County (Phase 2)"), "oliver")
    check("norm fiscal court", norm_county("Meade County Fiscal Court"), "meade")

    # End to end: a census row must corroborate a positive whose frame name
    # carries the state suffix. This is the regression test for the join.
    suffix_frame = [{"fips": "19169", "county_name": "Story County, Iowa",
                     "state": "IA", "has_enacted_restrictive": "1"}]
    suffix_census = [{"state": "IA", "county": "Story County",
                      "instrument": "moratorium", "census_status": "active",
                      "date_enacted": "2026-02-02",
                      "source": "https://example.org/story"}]
    srows, _ = build(suffix_frame, {}, suffix_census, reg, asof)
    check("census joins across the state suffix", srows[0]["sources_hit"], 1)
    check("suffix join clears the conflict", srows[0]["conflict"], "")

    if failures:
        print("SELFTEST FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("restriction_evidence.py selftest: OK")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--state", help="restrict the printed summary to one state")
    ap.add_argument("--strict", action="store_true",
                    help="exit nonzero on a QC failure")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    reg = load_registry()
    frame = read_csv(AGG_CSV)
    if not frame:
        print(f"no county frame at {AGG_CSV}; run county_aggregator.py first",
              file=sys.stderr)
        return 1

    cache = read_json(PROBE_CACHE, {})
    census = read_csv(CENSUS_CSV)
    delta = read_csv(DELTA_CSV)
    asof = today()

    rows, conflicts = build(frame, cache, census, reg, asof, delta_rows=delta)
    failures = qc(rows, frame, conflicts)
    if failures:
        print("QC FAIL")
        for f in failures:
            print("  " + f)
        if args.strict:
            return 1

    write_csv(rows, OUT_CSV, FIELDS)
    write_csv(conflicts, OUT_CONFLICTS, CONFLICT_FIELDS)
    n_hist = append_history(rows, OUT_HISTORY, asof)
    write_report(rows, conflicts, reg, OUT_MD, asof)

    s = summarize(rows)
    natl = s["national"]
    print(f"counties in frame: {s['total']}")
    print(f"  asserted_restrictive: {natl.get('asserted_restrictive', 0)}")
    print(f"  asserted_clear:       {natl.get('asserted_clear', 0)}")
    print(f"  unverified:           {natl.get('unverified', 0)}")
    print("  grades: " + "  ".join(f"{g} {natl.get(g, 0)}" for g in GRADES))
    print(f"conflicts: "
          f"label_negative_source_hit {natl.get('label_negative_source_hit', 0)}, "
          f"label_negative_upstream_hit "
          f"{natl.get('label_negative_upstream_hit', 0)}, "
          f"label_positive_no_support {natl.get('label_positive_no_support', 0)}")
    print(f"grade changes appended to history: {n_hist}")

    if args.state:
        c = s["states"].get(args.state.upper())
        if c:
            print(f"\n{args.state.upper()}: " +
                  "  ".join(f"{g} {c.get(g, 0)}" for g in GRADES))

    for p in (OUT_CSV, OUT_CONFLICTS, OUT_MD):
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
