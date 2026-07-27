"""
emergence_analysis.py — analysis layer over the verified-negative audit.

negative_audit.py registers the frame, emits the worklist, validates codings,
and reports coverage. This module does the inferential work that sits on top of
it, and it is deliberately separate so the registered audit design in
negative_audit.py is never edited to accommodate an analysis choice.

Additive. Reads existing files, writes only NEW files:

  data/emergence_analysis.md            the analysis and the gate decision
  data/emergence_bounds.csv             stratum-level rates and bounds
  data/audit_discovered_opposition.csv  projects the audit found opposition for
                                        that the tracker does not carry
  data/audit_data_flags.csv             data-quality problems the audit surfaced

Nothing here writes to master_opposition.csv, proposals.csv, or the codings
file. Every output is a review worklist or a report.

Three things this module enforces that were previously prose-only:

1. Stratum separation. The worklist sorts blocked_confirmed rows first because
   blocked-with-no-recorded-opposition is the most anomalous cell. That makes
   those rows a PURPOSIVE cell, not a random draw, and the audit docstring
   already forbids extrapolating from batches containing them. This module
   splits the codings into the purposive stratum and the random stratum and
   refuses to compute a frame-level emergence rate from purposive rows. A
   headline coverage percentage over the combined frame is misleading whenever
   the coded set is purposive-heavy, so coverage is reported per stratum.

2. Undeterminable as partial identification, not missing data. An
   undeterminable coding means the project's documentary footprint was too thin
   to decide, and those rows are not missing at random: they skew toward
   low-footprint projects, which plausibly also draw less opposition. Dropping
   them assumes the skew away. Imputing them invents data. The defensible
   treatment is a bound: the emergence rate lies between the case where every
   undeterminable row faced no opposition (lower) and the case where every one
   faced opposition (upper). These are Manski-style worst-case bounds and their
   width equals the undeterminable share, which has a direct operational
   consequence spelled out in the report.

3. A registered unlock gate. Emergence modeling does not begin on partial,
   purposive-heavy coverage. Thresholds are fixed here and changing them
   requires a new registration date, same convention as the audit frame:

     G1  random-stratum coverage                        >= 0.60
     G2  undeterminable share in the random stratum     <= 0.35
     G3  determinate codings in the random stratum      >= 40
     G4  Manski bound width at current coverage         <= 0.20

   All four required. G4 is the one that cannot be satisfied by more coding
   alone; it is satisfied by a protocol that resolves more rows.

Registered 2026-07-27. Run from repo root:  python3 emergence_analysis.py
Self-test (no data files needed):  python3 emergence_analysis.py --selftest
"""

from __future__ import annotations

import csv
import math
import os
import re
import sys
from collections import Counter
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)  # noqa: E731

WORKLIST_CSV = P("data", "negative_audit_worklist.csv")
CODINGS_CSV = P("data", "negative_audit_codings.csv")

OUT_REPORT = P("data", "emergence_analysis.md")
OUT_BOUNDS = P("data", "emergence_bounds.csv")
OUT_DISCOVERED = P("data", "audit_discovered_opposition.csv")
OUT_FLAGS = P("data", "audit_data_flags.csv")

REGISTERED = "2026-07-27"
PURPOSIVE_OUTCOME = "blocked_confirmed"
VALID_CODINGS = ("verified_opposition", "verified_none", "undeterminable")
DETERMINATE = ("verified_opposition", "verified_none")

G1_RANDOM_COVERAGE = 0.60
G2_MAX_UNDETERMINABLE = 0.35
G3_MIN_DETERMINATE = 40
G4_MAX_BOUND_WIDTH = 0.20

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

# Phrases batch 1 used in the free-text notes column to record follow-on work.
# Provenance is tracked: a tag declared in a structured `flags` column is
# recorded as declared, a tag recovered from prose is recorded as detected, and
# the two are never merged. Prose detection is a bridge for batch 1, not a
# permanent interface; new batches should populate `flags`.
NOTE_PATTERNS = [
    ("missing_opposition_events", r"should be added to master_opposition"),
    ("duplicate_project", r"DUPLICATE FLAG|RESOLVED AS DUPLICATE"),
    ("geography_error", r"Data flag:|probable state miscoding|county listed as"),
    ("outcome_review", r"Defensibility flag:|outcome coding .*is also wrong|"
                       r"coding should be reviewed"),
    ("mechanism_review", r"Mechanism flag:"),
]


# ---------------------------------------------------------------------------
# Statistics (stdlib only)
# ---------------------------------------------------------------------------

def binom_cdf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial interval by bisection on the binomial CDF. Returns
    (lower, upper). Degenerate cases are handled at the boundary."""
    if n == 0:
        return (0.0, 1.0)

    def bisect(fn, target, lo, hi, iters=80):
        """fn must be non-increasing in p. Returns the p where fn(p) == target."""
        for _ in range(iters):
            mid = (lo + hi) / 2
            if fn(mid) > target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    # Lower bound solves P(X >= k) = alpha/2. Expressed through the CDF so the
    # function handed to bisect is non-increasing in p:
    #   1 - CDF(k-1, n, p) = alpha/2  is  CDF(k-1, n, p) = 1 - alpha/2
    lower = 0.0 if k == 0 else bisect(
        lambda p: binom_cdf(k - 1, n, p), 1.0 - alpha / 2, 0.0, 1.0)
    upper = 1.0 if k == n else bisect(
        lambda p: binom_cdf(k, n, p), alpha / 2, 0.0, 1.0)
    return (lower, upper)


def finite_population_shrink(lo: float, hi: float, n: int, N: int) -> tuple[float, float]:
    """Shrink a sampling interval toward the point estimate by the finite
    population correction. The audit frame is a census, so as coverage
    approaches the frame the sampling component of uncertainty vanishes and
    only the undeterminable ambiguity remains. At n == N the interval is a
    point."""
    if N <= 1 or n <= 0:
        return (lo, hi)
    n = min(n, N)
    fpc = math.sqrt(max(0.0, (N - n) / (N - 1)))
    mid = (lo + hi) / 2
    return (mid - (mid - lo) * fpc, mid + (hi - mid) * fpc)


def manski_bounds(n_opp: int, n_none: int, n_undet: int) -> tuple[float, float, float]:
    """Worst-case bounds on the emergence rate under an undeterminable set of
    unknown composition. Returns (lower, upper, naive_determinate_rate).
    Lower treats every undeterminable row as no opposition, upper treats every
    one as opposition. Bound width equals the undeterminable share."""
    total = n_opp + n_none + n_undet
    if total == 0:
        return (0.0, 1.0, float("nan"))
    lower = n_opp / total
    upper = (n_opp + n_undet) / total
    det = n_opp + n_none
    naive = (n_opp / det) if det else float("nan")
    return (lower, upper, naive)


def projected_bound_width(undet_share: float) -> float:
    """At full coverage the bound width equals the undeterminable share, so the
    projection is the share itself. Stated as a function to make the identity
    explicit rather than implicit in the report text."""
    return undet_share


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def split_strata(worklist, codings):
    """Return (frame_by_stratum, coded_by_stratum, out_of_frame).

    Strata: 'purposive' for frame rows whose lifecycle_outcome is the anomalous
    cell the worklist front-loads, 'random' for everything else. Later coding
    rows for the same universe_id supersede earlier ones."""
    stratum_of = {}
    for r in worklist:
        stratum_of[r["universe_id"]] = (
            "purposive" if r.get("lifecycle_outcome") == PURPOSIVE_OUTCOME
            else "random")

    frame = Counter(stratum_of.values())
    coded, out_of_frame = {"purposive": {}, "random": {}}, []
    for c in codings:
        uid = (c.get("universe_id") or "").strip()
        cd = (c.get("coding") or "").strip()
        if cd not in VALID_CODINGS:
            continue
        st = stratum_of.get(uid)
        if st is None:
            out_of_frame.append(c)
            continue
        coded[st][uid] = c
    return frame, coded, out_of_frame


def stratum_stats(coded_map, frame_n):
    mix = Counter(c["coding"] for c in coded_map.values())
    n_coded = len(coded_map)
    n_opp = mix.get("verified_opposition", 0)
    n_none = mix.get("verified_none", 0)
    n_undet = mix.get("undeterminable", 0)
    lower, upper, naive = manski_bounds(n_opp, n_none, n_undet)
    det = n_opp + n_none
    cp = clopper_pearson(n_opp, det) if det else (0.0, 1.0)
    cp_fpc = finite_population_shrink(cp[0], cp[1], n_coded, frame_n)
    return {
        "frame_n": frame_n, "n_coded": n_coded,
        "coverage": (n_coded / frame_n) if frame_n else 0.0,
        "n_opp": n_opp, "n_none": n_none, "n_undet": n_undet,
        "n_determinate": det,
        "undet_share": (n_undet / n_coded) if n_coded else 0.0,
        "manski_lower": lower, "manski_upper": upper,
        "bound_width": upper - lower,
        "naive_rate": naive,
        "cp_lower": cp[0], "cp_upper": cp[1],
        "cp_fpc_lower": cp_fpc[0], "cp_fpc_upper": cp_fpc[1],
    }


# ---------------------------------------------------------------------------
# Follow-on worklists
# ---------------------------------------------------------------------------

def extract_flags(codings, worklist):
    """Pull structured follow-on work out of the codings. Two provenances:
    'declared' from a `flags` column, 'detected' from note prose. Never
    merged, because a detected tag is an inference about what a human meant."""
    meta = {r["universe_id"]: r for r in worklist}
    discovered, flags = [], []
    for c in codings:
        uid = (c.get("universe_id") or "").strip()
        cd = (c.get("coding") or "").strip()
        notes = c.get("notes") or ""
        m = meta.get(uid, {})
        declared = [t.strip() for t in (c.get("flags") or "").split(";") if t.strip()]

        if cd == "verified_opposition":
            discovered.append({
                "universe_id": uid,
                "name": m.get("name", ""),
                "state": m.get("state", ""),
                "county": m.get("county", ""),
                "lifecycle_outcome": m.get("lifecycle_outcome", ""),
                "evidence_url": (c.get("evidence_url") or "").strip(),
                "tracker_action": ("add opposition events" if
                                   re.search(NOTE_PATTERNS[0][1], notes, re.I)
                                   else "confirm whether tracker carries events"),
                "coded_by": c.get("coded_by", ""),
                "coded_date": c.get("coded_date", ""),
                "notes": notes,
            })

        for tag in declared:
            flags.append({"universe_id": uid, "flag": tag, "provenance": "declared",
                          "name": m.get("name", ""), "state": m.get("state", ""),
                          "coding": cd, "notes": notes})
        for tag, pat in NOTE_PATTERNS:
            if tag in declared:
                continue
            if re.search(pat, notes, re.I):
                flags.append({"universe_id": uid, "flag": tag,
                              "provenance": "detected_from_notes",
                              "name": m.get("name", ""), "state": m.get("state", ""),
                              "coding": cd, "notes": notes})
    return discovered, flags


DISCOVERED_COLS = ["universe_id", "name", "state", "county", "lifecycle_outcome",
                   "evidence_url", "tracker_action", "coded_by", "coded_date",
                   "notes"]
FLAG_COLS = ["universe_id", "flag", "provenance", "name", "state", "coding", "notes"]
BOUNDS_COLS = ["stratum", "frame_n", "n_coded", "coverage", "n_opp", "n_none",
               "n_undet", "n_determinate", "undet_share", "naive_rate",
               "manski_lower", "manski_upper", "bound_width",
               "cp_lower", "cp_upper", "cp_fpc_lower", "cp_fpc_upper"]


def write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def pct(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) \
        else f"{100 * x:.1f}%"


def num(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) \
        else f"{x:.{nd}f}"


def build_report(rand, purp, gates, gate_open, discovered, flags, out_of_frame):
    L = []
    a = L.append
    a("# Emergence Analysis: Verified-Negative Audit")
    a("")
    a(f"Generated {date.today().isoformat()}. Gate thresholds registered "
      f"{REGISTERED}. This is the analysis layer over negative_audit.py; the "
      f"audit frame design itself is registered separately and is not modified "
      f"here.")
    a("")
    a("## Gate decision")
    a("")
    a(f"**Emergence modeling: {'UNLOCKED' if gate_open else 'LOCKED'}**")
    a("")
    a("| # | Criterion | Threshold | Observed | Met |")
    a("| :-- | :-- | :-- | :-- | :-- |")
    for g in gates:
        a(f"| {g[0]} | {g[1]} | {g[2]} | {g[3]} | {'yes' if g[4] else 'no'} |")
    a("")

    a("## Stratum separation")
    a("")
    a("The worklist front-loads rows whose outcome is blocked_confirmed, "
      "because a project recorded as stopped with no recorded opposition is "
      "the most anomalous cell in the data. That ordering makes those rows a "
      "purposive cell rather than a random draw, so they are reported "
      "separately and never pooled into a frame-level rate.")
    a("")
    a("| Stratum | Frame | Coded | Coverage |")
    a("| :-- | :-- | :-- | :-- |")
    a(f"| purposive (blocked_confirmed) | {purp['frame_n']} | {purp['n_coded']} "
      f"| {pct(purp['coverage'])} |")
    a(f"| random (all other frame rows) | {rand['frame_n']} | {rand['n_coded']} "
      f"| {pct(rand['coverage'])} |")
    a("")
    if purp["n_coded"] and not rand["n_coded"]:
        a("Note the shape of the coded set: every coded row to date falls in "
          "the purposive cell and none in the random stratum. A combined "
          "coverage figure over the whole frame would therefore overstate "
          "progress toward an emergence estimate, which depends entirely on "
          "the random stratum. Random-stratum coverage is the number that "
          "matters and it is the one in the gate table above.")
        a("")

    a("## Purposive cell: blocked with no recorded opposition")
    a("")
    if purp["n_coded"]:
        a(f"{purp['n_coded']} of {purp['frame_n']} coded. Findings: "
          f"{purp['n_opp']} verified_opposition, {purp['n_none']} "
          f"verified_none, {purp['n_undet']} undeterminable.")
        a("")
        a("This cell was a diagnostic question rather than an estimation "
          "target, and it has an answer. Where the cell is dominated by "
          "verified_opposition, the reading is that these projects did face "
          "opposition and the tracker did not carry it, which makes the cell "
          "a detection gap rather than a real population of quietly blocked "
          "projects. Consequence for existing statistics: opposition presence "
          "among blocked projects was understated, and the affected projects "
          "are listed in data/audit_discovered_opposition.csv for entry "
          "through the normal sourced-URL path.")
        a("")
        a("The rate in this cell is not an emergence rate and must not be "
          "quoted as one. It is a census of the anomalous cell only.")
    else:
        a("No rows coded in this cell yet.")
    a("")

    a("## Emergence rate, random stratum")
    a("")
    if rand["n_determinate"]:
        a(f"Determinate codings: {rand['n_determinate']} "
          f"({rand['n_opp']} verified_opposition, {rand['n_none']} "
          f"verified_none). Undeterminable: {rand['n_undet']} "
          f"({pct(rand['undet_share'])} of coded).")
        a("")
        a(f"- Rate over determinate codings only: {num(rand['naive_rate'])}. "
          f"This figure assumes the undeterminable rows resemble the "
          f"determinate ones, which is exactly the assumption the audit "
          f"exists to avoid making. It is reported for completeness and should "
          f"not be the quoted number.")
        a(f"- Worst-case bounds over all coded rows: "
          f"[{num(rand['manski_lower'])}, {num(rand['manski_upper'])}], "
          f"width {num(rand['bound_width'])}. This is the defensible interval.")
        a(f"- Sampling interval on the determinate rate, exact binomial: "
          f"[{num(rand['cp_lower'])}, {num(rand['cp_upper'])}]; after finite "
          f"population correction against a frame of {rand['frame_n']}: "
          f"[{num(rand['cp_fpc_lower'])}, {num(rand['cp_fpc_upper'])}].")
        a("")
        a("The two intervals answer different questions and both belong in any "
          "external statement. The sampling interval narrows as coding "
          "proceeds and collapses to a point at full coverage, because the "
          "frame is a census rather than a sample. The worst-case bounds do "
          "not narrow with coverage at all; their width is the undeterminable "
          "share.")
    else:
        a("No determinate codings in the random stratum yet, so no emergence "
          "rate is computable. Nothing in this section can be filled in by "
          "analysis; it requires coded rows.")
    a("")

    a("## What actually binds")
    a("")
    a(f"At full coverage of the random stratum the worst-case bound width "
      f"equals the undeterminable share. Observed share so far across all "
      f"coded rows is {pct(_overall_undet(rand, purp))}, which projects to a "
      f"bound roughly that wide even after every row in the frame is coded.")
    a("")
    a("| If undeterminable share is | Bound width at full coverage |")
    a("| :-- | :-- |")
    for s in (0.05, 0.10, 0.20, 0.30, 0.40):
        a(f"| {pct(s)} | {num(projected_bound_width(s), 2)} |")
    a("")
    a("The operational consequence is the main finding of this pass. Coding "
      "more rows buys sampling precision, which the census will deliver "
      "anyway. It does not buy identification. The binding constraint is the "
      "share of rows the protocol cannot resolve, so protocol work is worth "
      "more per hour than volume work. Concretely: the current protocol is "
      "four news-style queries, and a row fails when no coverage of the "
      "approval process exists. Adding a municipal-records step (agenda or "
      "minutes search for the jurisdiction and date window) targets exactly "
      "the failure mode, and it is the same civic-scraper capability already "
      "sitting at Tier 2 of the tooling scan. That link is the argument for "
      "moving it up.")
    a("")

    a("## Follow-on worklists")
    a("")
    a(f"- data/audit_discovered_opposition.csv: {len(discovered)} projects "
      f"where the audit found sourced opposition. Each row carries the "
      f"evidence URL and needs entry through the normal path; the audit does "
      f"not write to the tracker.")
    a(f"- data/audit_data_flags.csv: {len(flags)} data-quality flags. "
      f"Provenance is recorded per row. A flag marked detected_from_notes was "
      f"recovered from prose and is an inference about intent; confirm it "
      f"before acting. A flag marked declared came from a structured column "
      f"and can be worked directly.")
    if flags:
        a("")
        a("| Flag | n |")
        a("| :-- | :-- |")
        for k, v in sorted(Counter(f["flag"] for f in flags).items()):
            a(f"| {k} | {v} |")
    a("")
    a("Batches after this one should populate a `flags` column in the codings "
      "file rather than relying on prose detection. The column is optional and "
      "its absence changes nothing, so adding it is backward compatible.")
    a("")

    if out_of_frame:
        a("## Coded rows outside the frame")
        a("")
        a(f"{len(out_of_frame)} coded row(s) reference a universe_id not in the "
          f"current frame. This is expected when a project was later suppressed "
          f"as a duplicate; the coding is retained as an audit trail and "
          f"excluded from every rate above.")
        for c in out_of_frame:
            a(f"- {c.get('universe_id', '')}: {c.get('coding', '')}")
        a("")

    a("## Standing rules observed")
    a("")
    a("Bounds rather than point estimates wherever identification is partial. "
      "Purposive and random strata never pooled. Undeterminable rows never "
      "dropped and never imputed. No scorekeeping vocabulary. No em-dashes. "
      "Nothing written to any source-of-truth file.")
    a("")
    return "\n".join(L) + "\n"


def _overall_undet(rand, purp):
    n = rand["n_coded"] + purp["n_coded"]
    u = rand["n_undet"] + purp["n_undet"]
    return (u / n) if n else 0.0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  FAIL {label}")
        else:
            print(f"  pass {label}")

    lo, hi, naive = manski_bounds(3, 5, 2)
    check(abs(lo - 0.3) < 1e-9, "manski lower treats undeterminable as none")
    check(abs(hi - 0.5) < 1e-9, "manski upper treats undeterminable as opposition")
    check(abs((hi - lo) - 0.2) < 1e-9, "bound width equals undeterminable share")
    check(abs(naive - 0.375) < 1e-9, "naive rate uses determinate only")

    lo, hi, _ = manski_bounds(4, 6, 0)
    check(abs(lo - hi) < 1e-9, "no undeterminable gives a point")
    lo, hi, naive = manski_bounds(0, 0, 5)
    check(lo == 0.0 and hi == 1.0, "all undeterminable gives no information")
    check(math.isnan(naive), "naive rate undefined with no determinate rows")
    lo, hi, _ = manski_bounds(0, 0, 0)
    check(lo == 0.0 and hi == 1.0, "empty is uninformative not a crash")

    l, u = clopper_pearson(5, 10)
    check(l < 0.5 < u, "exact interval brackets the estimate")
    check(0.18 < l < 0.20 and 0.80 < u < 0.82, "exact interval matches known value")
    l, u = clopper_pearson(0, 10)
    check(l == 0.0 and 0.30 < u < 0.32, "zero successes lower bound is zero")
    l, u = clopper_pearson(10, 10)
    check(u == 1.0 and 0.68 < l < 0.70, "all successes upper bound is one")
    l, u = clopper_pearson(0, 0)
    check(l == 0.0 and u == 1.0, "empty interval is uninformative")

    l, u = finite_population_shrink(0.2, 0.8, 10, 100)
    check(0.2 < l < 0.5 and 0.5 < u < 0.8, "fpc shrinks toward the middle")
    l, u = finite_population_shrink(0.2, 0.8, 100, 100)
    check(abs(l - 0.5) < 1e-9 and abs(u - 0.5) < 1e-9, "census collapses to a point")
    l, u = finite_population_shrink(0.2, 0.8, 0, 100)
    check(l == 0.2 and u == 0.8, "zero coded leaves the interval alone")

    wl = [
        {"universe_id": "p1", "lifecycle_outcome": "blocked_confirmed", "name": "A",
         "state": "IA", "county": "x"},
        {"universe_id": "p2", "lifecycle_outcome": "pending", "name": "B",
         "state": "IA", "county": "y"},
        {"universe_id": "p3", "lifecycle_outcome": "advanced_confirmed", "name": "C",
         "state": "OH", "county": "z"},
    ]
    cods = [
        {"universe_id": "p1", "coding": "verified_opposition",
         "notes": "Opposition events should be added to master_opposition."},
        {"universe_id": "p2", "coding": "undeterminable", "notes": ""},
        {"universe_id": "p9", "coding": "verified_none", "notes": "duplicate"},
        {"universe_id": "p3", "coding": "bogus", "notes": ""},
    ]
    frame, coded, oof = split_strata(wl, cods)
    check(frame["purposive"] == 1 and frame["random"] == 2, "frame split by outcome")
    check(list(coded["purposive"]) == ["p1"], "purposive coding routed")
    check(list(coded["random"]) == ["p2"], "random coding routed")
    check(len(oof) == 1 and oof[0]["universe_id"] == "p9", "out-of-frame captured")
    check(all("p3" not in d for d in coded.values()), "invalid coding ignored")

    dup = [{"universe_id": "p2", "coding": "verified_none", "notes": ""},
           {"universe_id": "p2", "coding": "verified_opposition", "notes": ""}]
    _, coded2, _ = split_strata(wl, dup)
    check(coded2["random"]["p2"]["coding"] == "verified_opposition",
          "later coding supersedes earlier")

    disc, fl = extract_flags(cods, wl)
    check(len(disc) == 1 and disc[0]["tracker_action"] == "add opposition events",
          "discovered opposition extracted with action")
    check(any(f["flag"] == "missing_opposition_events"
              and f["provenance"] == "detected_from_notes" for f in fl),
          "prose flag detected with provenance")

    cods2 = [{"universe_id": "p1", "coding": "verified_opposition",
              "evidence_url": "http://x", "flags": "geography_error",
              "notes": "Data flag: county wrong"}]
    _, fl2 = extract_flags(cods2, wl)
    tags = [(f["flag"], f["provenance"]) for f in fl2]
    check(("geography_error", "declared") in tags, "declared flag kept")
    check(("geography_error", "detected_from_notes") not in tags,
          "declared flag not duplicated by prose detection")

    st = stratum_stats({"a": {"coding": "verified_opposition"},
                        "b": {"coding": "verified_none"},
                        "c": {"coding": "undeterminable"}}, 30)
    check(st["n_determinate"] == 2 and st["n_undet"] == 1, "stratum stats counts")
    check(abs(st["coverage"] - 0.1) < 1e-9, "coverage computed against frame")
    check(abs(st["bound_width"] - 1 / 3) < 1e-9, "stratum bound width")
    st0 = stratum_stats({}, 30)
    check(st0["n_coded"] == 0 and st0["coverage"] == 0.0, "empty stratum safe")

    check(abs(projected_bound_width(0.22) - 0.22) < 1e-9, "projection identity")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    worklist = load_csv(WORKLIST_CSV)
    if not worklist:
        print("ERROR: data/negative_audit_worklist.csv missing; "
              "run negative_audit.py first")
        return 1
    codings = load_csv(CODINGS_CSV)

    frame, coded, out_of_frame = split_strata(worklist, codings)
    rand = stratum_stats(coded["random"], frame.get("random", 0))
    purp = stratum_stats(coded["purposive"], frame.get("purposive", 0))

    g1 = rand["coverage"] >= G1_RANDOM_COVERAGE
    g2 = (rand["undet_share"] <= G2_MAX_UNDETERMINABLE) if rand["n_coded"] else False
    g3 = rand["n_determinate"] >= G3_MIN_DETERMINATE
    g4 = (rand["bound_width"] <= G4_MAX_BOUND_WIDTH) if rand["n_coded"] else False
    gates = [
        ["G1", "random-stratum coverage", f">= {G1_RANDOM_COVERAGE:.2f}",
         pct(rand["coverage"]), g1],
        ["G2", "undeterminable share, random stratum",
         f"<= {G2_MAX_UNDETERMINABLE:.2f}",
         pct(rand["undet_share"]) if rand["n_coded"] else "no coded rows", g2],
        ["G3", "determinate codings, random stratum", f">= {G3_MIN_DETERMINATE}",
         str(rand["n_determinate"]), g3],
        ["G4", "worst-case bound width", f"<= {G4_MAX_BOUND_WIDTH:.2f}",
         num(rand["bound_width"], 2) if rand["n_coded"] else "not computable", g4],
    ]
    gate_open = all([g1, g2, g3, g4])

    discovered, flags = extract_flags(codings, worklist)

    write_csv(OUT_BOUNDS,
              [{"stratum": "random", **rand}, {"stratum": "purposive", **purp}],
              BOUNDS_COLS)
    write_csv(OUT_DISCOVERED, discovered, DISCOVERED_COLS)
    write_csv(OUT_FLAGS, flags, FLAG_COLS)
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(build_report(rand, purp, gates, gate_open, discovered, flags,
                              out_of_frame))

    print(f"frame: {frame.get('random', 0)} random + {frame.get('purposive', 0)} "
          f"purposive = {sum(frame.values())}")
    print(f"coded: {rand['n_coded']} random, {purp['n_coded']} purposive"
          + (f", {len(out_of_frame)} out of frame" if out_of_frame else ""))
    if rand["n_determinate"]:
        print(f"random-stratum emergence bounds: "
              f"[{num(rand['manski_lower'])}, {num(rand['manski_upper'])}]")
    else:
        print("random-stratum emergence rate: not computable, no determinate codings")
    if purp["n_coded"]:
        print(f"purposive cell: {purp['n_opp']} verified_opposition, "
              f"{purp['n_none']} verified_none, {purp['n_undet']} undeterminable "
              f"of {purp['n_coded']} coded (diagnostic only, not an emergence rate)")
    for g in gates:
        print(f"  {g[0]} {'MET    ' if g[4] else 'NOT MET'}  {g[1]}: {g[3]}")
    print(f"emergence modeling: {'UNLOCKED' if gate_open else 'LOCKED'}")
    print(f"discovered opposition: {len(discovered)} projects, "
          f"data flags: {len(flags)}")

    dirty = []
    for p in (OUT_REPORT, OUT_BOUNDS, OUT_DISCOVERED, OUT_FLAGS):
        hits = sum(1 for line in open(p, encoding="utf-8") if LEAK_RE.search(line))
        name = os.path.relpath(p, ROOT)
        if not hits:
            print(f"leak audit {name}: clean")
        elif p in (OUT_DISCOVERED, OUT_FLAGS):
            print(f"leak audit {name}: {hits} hits in verbatim coder notes "
                  "(review-only file, accepted)")
        else:
            dirty.append(name)
            print(f"LEAK AUDIT {name}: {hits} hits, inspect before use")
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())
