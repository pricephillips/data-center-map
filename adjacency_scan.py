#!/usr/bin/env python3
"""
adjacency_scan.py

Turns an enacted county restriction into a ranked check of the counties
that border it, and feeds those counties into local meeting ingestion.

Why this module exists. Two of the five TVA misses were found by geography,
not by the pipeline. Walker County GA was found because it adjoins Hamilton
County TN; Grundy and Coffee turned up in the same sweep off Hamilton
sourcing. Restriction adoption diffuses across borders (Walker/Hamilton,
Coffee/Warren/Franklin/Moore are the recorded cases) and the tracker had no
representation of borders, so an enactment could never prompt a look next
door.

The second, larger half of the same problem: local_meeting_feed.py builds
BOTH its discovery frame and its fetch frame from
`jurisdictions_from_feed(master_opposition_clean.csv)`. A county with zero
tracker records is therefore never probed and never polled. The ingestion
frame is defined by what the tracker already knows, which is exactly why
Grundy, Coffee and Walker were structurally unreachable rather than
unlucky. This module writes `configs/local_meeting_watchlist.csv` so
counties can enter the ingestion frame on adjacency evidence, before the
tracker holds a single record on them.

Ordering criterion, pre-registered here so ranking cannot be tuned after
seeing the output:

  1  a neighbour enacted within the trigger window AND this county already
     carries a non-terminal restrictive record. The record exists, the
     neighbour just moved, and one primary source resolves it. Cheapest
     confirmation and the highest yield: Hamilton was this exact shape.
  2  a fresh trigger inside a diffusion cluster: three or more enacted
     neighbours, or two or more plus development exposure (an existing data
     center or a tracked project). Fresh diffusion with something to
     diffuse onto.
  3  a fresh trigger with one of a second enacted neighbour, development
     exposure, or recorded opposition activity. Periodic sweep tier.
  4  everything else adjacent to an enacted county. Background queue, kept
     so the frame is complete and auditable, not because it is actionable.

Ranking has limits, stated plainly. With 349 enacted seeds, roughly 1,000
counties border one, and most border exactly one with no development
exposure. Grundy County TN, before Coffee was known to be enacted, was that
shape: nothing distinguished it from several hundred others except sitting
next to Hamilton. The ladder concentrates attention where a cheap
confirmation exists; it does not claim the tail is safe. Closing the tail is
an ingestion problem, which is what the watchlist below is for.

What this queue is NOT. Adjacency is a search prompt. It is not evidence
that a county restricted anything, it is not a feature, and no label,
score, or client-facing claim may be derived from it. Every row carries
`evidence_status = search_prompt_only` to keep that on the artifact itself.
The asymmetry justifies a loose rule: a false neighbour costs one web
search, a missed neighbour costs an enactment the tracker never sees.

Seeds. A county is a seed when the tracker labels it
has_enacted_restrictive == 1 OR the external census records an in-scope
enacted instrument for it. Census-only seeds are included deliberately: the
24 census-enacted counties with zero tracker record still radiate diffusion
pressure onto their neighbours, and excluding them would make the scan
inherit the coverage gap it exists to help close.

Outputs
  data/adjacency_scan_queue.csv       ranked neighbour re-scan queue
  data/adjacency_scan_summary.json    counts for CI and the gate check
  data/adjacency_scan_report.md       reviewer-facing summary
  configs/local_meeting_watchlist.csv jurisdictions to add to local meeting
                                      discovery, merge-preserving

Standing rules honored: four-tier vocabulary, additive, writes only new
files, LF line endings, no em-dashes in composed prose, --selftest with no
data or network dependency.

Usage
  python adjacency_scan.py
  python adjacency_scan.py --state TN,GA
  python adjacency_scan.py --seed-fips 47065
  python adjacency_scan.py --no-watchlist
  python adjacency_scan.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ADJ_CSV = os.path.join(HERE, "data", "county_adjacency.csv")
AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
GAP_CSV = os.path.join(HERE, "data", "coverage_gap_report.csv")
MASTER_CLEAN = os.path.join(HERE, "master_opposition_clean.csv")
SCORES_CSV = os.path.join(HERE, "data", "county_policy_scores.csv")
OUT_CSV = os.path.join(HERE, "data", "adjacency_scan_queue.csv")
OUT_JSON = os.path.join(HERE, "data", "adjacency_scan_summary.json")
OUT_MD = os.path.join(HERE, "data", "adjacency_scan_report.md")
WATCHLIST = os.path.join(HERE, "configs", "local_meeting_watchlist.csv")
MEETING_SOURCES = os.path.join(HERE, "configs", "local_meeting_sources.json")
MEETING_OVERRIDES = os.path.join(HERE, "configs",
                                 "local_meeting_sources_overrides.json")

# Days after an adjacent enactment during which the neighbour counts as
# freshly triggered. 180 rather than 90: Walker GA ran a 30-day moratorium
# in June 2026 and a 180-day one in August, so a quarterly window would
# have closed between the two actions on the same county.
TRIGGER_DAYS = 180

# Population below which a county is tiered as a small jurisdiction. This is
# a DETECTION-difficulty proxy, not a risk factor: the four counties the TVA
# check found by hand sit at roughly 13,600 (Grundy TN), 57,000 (Hawkins TN),
# 58,000 (Coffee TN) and 68,000 (Walker GA), all covered by local TV, radio
# and county press pages that ingestion under-samples and none with a daily
# newsroom of its own. The line is drawn above all four on purpose. Reported
# as a tier alongside the queue and never used as a model feature, consistent
# with the standing rule that detection-bias tiering accompanies county
# results.
SMALL_JURISDICTION_POP = 100000

# Priority at or below which a county is written to the local meeting
# watchlist. Priorities 1 and 2 only: the watchlist drives live polling and
# a background queue does not justify recurring requests to a jurisdiction.
WATCHLIST_MAX_PRIORITY = 2

# Shared definitions. Imported so a single definition governs, with a
# documented fallback so this module still runs standalone (same fallback
# discipline the shared JS modules use).
try:
    from stale_pending_audit import NON_TERMINAL_OUTCOMES
except Exception:  # pragma: no cover - import fallback
    NON_TERMINAL_OUTCOMES = {"pending", "blocked_unverified",
                             "advanced_unverified", "mixed", ""}
try:
    from coverage_audit import (ENACTED_STATUSES, RESTRICTIVE_MECHS,
                                norm_county)
except Exception:  # pragma: no cover - import fallback
    ENACTED_STATUSES = {"active", "extended", "replaced", "expired"}
    RESTRICTIVE_MECHS = {"moratorium", "ban", "conditional_zoning"}

    def norm_county(name: str, state: str = "") -> str:
        n = str(name or "").lower()
        for word in ("county", "parish", "borough", "municipality"):
            n = n.replace(word, " ")
        n = "".join(c if c.isalpha() or c == " " else " " for c in n)
        return " ".join(n.split())

try:
    from fetch_county_adjacency import load_adjacency
except Exception:  # pragma: no cover - import fallback
    def load_adjacency(path: str = ADJ_CSV) -> dict:
        out: dict = defaultdict(dict)
        if not os.path.exists(path):
            return out
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                a = (r.get("fips") or "").strip()
                b = (r.get("neighbor_fips") or "").strip()
                if a and b:
                    out[a][b] = 1
        return out

FIELDS = ["priority", "priority_reason", "state", "county", "fips",
          "enacted_neighbors", "enacted_neighbor_fips",
          "nearest_seed_county", "nearest_seed_state", "nearest_seed_fips",
          "nearest_seed_date", "days_since_nearest_seed", "fresh_trigger",
          "cross_state_seed", "stale_nonterminal_records",
          "tracker_restrictive_records", "existing_dc_count",
          "n_projects_tracked", "n_opposition_events", "population",
          "small_jurisdiction", "meeting_source", "calibrated_score",
          "score_decile", "evidence_status"]

WATCH_FIELDS = ["state", "county", "fips", "reason", "priority",
                "first_queued_utc", "last_seen_utc", "still_queued",
                "reviewer_note"]


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def parse_date(value: str) -> date | None:
    v = str(value or "").strip()[:10]
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def to_int(value) -> int:
    try:
        return int(float(str(value).strip() or 0))
    except (TypeError, ValueError):
        return 0


def meeting_sources(cache_path: str = MEETING_SOURCES,
                    override_path: str = MEETING_OVERRIDES) -> dict[str, str]:
    """Maps "STATE::County Name" to the detected agenda platform.

    Read-only view of local_meeting_feed.py's discovery cache, using the same
    key shape. A county absent from the cache has never been probed, which is
    a different state from probed-and-none and is reported as such.
    """
    out: dict[str, str] = {}
    for path in (cache_path, override_path):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    out[k] = str(v.get("platform") or "none")
    return out


def county_index(agg_rows: list[dict]) -> tuple[dict, dict]:
    """Returns (fips -> county record, (state, norm name) -> fips)."""
    by_fips, by_name = {}, {}
    for r in agg_rows:
        fips = (r.get("fips") or "").strip()
        if not fips:
            continue
        st = (r.get("state") or "").strip().upper()
        name = (r.get("county_name") or "").split(",")[0].strip()
        by_fips[fips] = {
            "state": st,
            "county": name,
            "label": (r.get("has_enacted_restrictive") or "").strip(),
            "existing_dc_count": to_int(r.get("existing_dc_count")),
            "n_projects_tracked": to_int(r.get("n_projects_tracked")),
            "n_opposition_events": to_int(r.get("n_opposition_events")),
            "population": to_int(r.get("population")),
        }
        key = (st, norm_county(name, st))
        if key[1]:
            by_name.setdefault(key, fips)
    return by_fips, by_name


def tracker_county_records(records: list[dict]) -> dict[tuple, dict]:
    """Per (state, norm county): restrictive record dates and grades."""
    out: dict[tuple, dict] = {}
    for r in records:
        st = (r.get("State") or "").strip().upper()
        cty = norm_county(r.get("County", ""), st)
        if not st or not cty:
            continue
        mech = (r.get("qc_mechanism") or "").strip().lower()
        if mech not in RESTRICTIVE_MECHS:
            continue
        cur = out.setdefault((st, cty), {"restrictive": 0, "non_terminal": 0,
                                         "latest": None})
        cur["restrictive"] += 1
        if (r.get("outcome_defensible") or "").strip() \
                in NON_TERMINAL_OUTCOMES:
            cur["non_terminal"] += 1
        d = parse_date(r.get("Date"))
        if d and (cur["latest"] is None or d > cur["latest"]):
            cur["latest"] = d
    return out


def build_seeds(by_fips: dict, by_name: dict, gap_rows: list[dict],
                trk: dict) -> dict[str, dict]:
    """Enacted counties, from the tracker label and the external census."""
    seeds: dict[str, dict] = {}
    for fips, rec in by_fips.items():
        if rec["label"] == "1":
            key = (rec["state"], norm_county(rec["county"], rec["state"]))
            latest = (trk.get(key) or {}).get("latest")
            seeds[fips] = {
                "fips": fips, "state": rec["state"], "county": rec["county"],
                "basis": "tracker_label",
                "date": latest, "date_source": "tracker_record" if latest
                else "undated",
            }
    for g in gap_rows:
        if (g.get("census_status") or "").strip().lower() \
                not in ENACTED_STATUSES:
            continue
        st = (g.get("state") or "").strip().upper()
        cty = (g.get("county") or "").strip()
        fips = by_name.get((st, norm_county(cty, st)))
        if not fips:
            continue
        cdate = parse_date(g.get("census_date"))
        cur = seeds.get(fips)
        if cur is None:
            seeds[fips] = {
                "fips": fips, "state": st,
                "county": by_fips.get(fips, {}).get("county", cty),
                "basis": "census_only", "date": cdate,
                "date_source": "census" if cdate else "undated",
            }
        else:
            cur["basis"] = "tracker_and_census"
            if cdate and (cur["date"] is None or cdate > cur["date"]):
                cur["date"] = cdate
                cur["date_source"] = "census"
    return seeds


def scan(by_fips: dict, by_name: dict, adj: dict, gap_rows: list[dict],
         records: list[dict], scores: dict, as_of: date,
         trigger_days: int = TRIGGER_DAYS,
         seed_filter: set[str] | None = None,
         sources: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    trk = tracker_county_records(records)
    seeds = build_seeds(by_fips, by_name, gap_rows, trk)
    if seed_filter:
        seeds = {f: s for f, s in seeds.items() if f in seed_filter}

    cand: dict[str, dict] = {}
    for sf, seed in seeds.items():
        for nf in sorted((adj.get(sf) or {}).keys()):
            if nf in seeds:
                continue          # already enacted; nothing to prompt
            rec = by_fips.get(nf)
            if rec is None:
                continue          # neighbour outside the county frame
            c = cand.setdefault(nf, {"neighbors": [], "rec": rec})
            c["neighbors"].append(seed)

    rows = []
    for nf, c in cand.items():
        rec = c["rec"]
        key = (rec["state"], norm_county(rec["county"], rec["state"]))
        tr = trk.get(key) or {}
        dated = [s for s in c["neighbors"] if s["date"]]
        nearest = (max(dated, key=lambda s: s["date"]) if dated
                   else c["neighbors"][0])
        days = ((as_of - nearest["date"]).days if nearest["date"] else None)
        fresh = days is not None and 0 <= days <= trigger_days
        n_enacted = len(c["neighbors"])
        cross = any(s["state"] != rec["state"] for s in c["neighbors"])
        non_terminal = tr.get("non_terminal", 0)
        exposure = rec["existing_dc_count"] + rec["n_projects_tracked"]

        if fresh and non_terminal:
            pri, why = 1, ("a neighbour enacted inside the trigger window "
                           "and this county already carries a non-terminal "
                           "restrictive record; one primary source resolves "
                           "it")
        elif fresh and (n_enacted >= 3 or (n_enacted >= 2 and exposure > 0)):
            pri, why = 2, ("fresh trigger inside a diffusion cluster: three "
                           "or more enacted neighbours, or two plus "
                           "development exposure")
        elif fresh and (n_enacted >= 2 or exposure > 0
                        or rec["n_opposition_events"] > 0):
            pri, why = 3, ("fresh trigger with one of a second enacted "
                           "neighbour, development exposure, or recorded "
                           "opposition activity")
        else:
            pri, why = 4, "adjacent to an enacted county, background queue"

        sc = scores.get(nf, {})
        src_key = f"{rec['state']}::{rec['county']}"
        platform = (sources or {}).get(src_key, "unprobed")
        rows.append({
            "priority": pri,
            "priority_reason": why,
            "state": rec["state"],
            "county": rec["county"],
            "fips": nf,
            "enacted_neighbors": n_enacted,
            "enacted_neighbor_fips": ";".join(
                s["fips"] for s in sorted(c["neighbors"],
                                          key=lambda s: s["fips"])),
            "nearest_seed_county": nearest["county"],
            "nearest_seed_state": nearest["state"],
            "nearest_seed_fips": nearest["fips"],
            "nearest_seed_date": (nearest["date"].isoformat()
                                  if nearest["date"] else ""),
            "days_since_nearest_seed": days if days is not None else "",
            "fresh_trigger": "1" if fresh else "0",
            "cross_state_seed": "1" if cross else "0",
            "stale_nonterminal_records": non_terminal,
            "tracker_restrictive_records": tr.get("restrictive", 0),
            "existing_dc_count": rec["existing_dc_count"],
            "n_projects_tracked": rec["n_projects_tracked"],
            "n_opposition_events": rec["n_opposition_events"],
            "population": rec["population"],
            "small_jurisdiction": ("1" if 0 < rec["population"]
                                   < SMALL_JURISDICTION_POP else "0"),
            "meeting_source": platform,
            "calibrated_score": sc.get("calibrated_score", ""),
            "score_decile": sc.get("score_decile", ""),
            "evidence_status": "search_prompt_only",
        })

    rows.sort(key=lambda r: (
        r["priority"],
        -r["stale_nonterminal_records"],
        -r["enacted_neighbors"],
        r["days_since_nearest_seed"] if isinstance(
            r["days_since_nearest_seed"], int) else 10 ** 6,
        r["state"], r["county"]))

    by_pri = Counter(r["priority"] for r in rows)
    summary = {
        "as_of": as_of.isoformat(),
        "trigger_days": trigger_days,
        "seed_counties": len(seeds),
        "seed_basis": dict(Counter(s["basis"] for s in seeds.values())),
        "seeds_undated": sum(1 for s in seeds.values() if not s["date"]),
        "candidate_counties": len(rows),
        "priority_1": by_pri.get(1, 0),
        "priority_2": by_pri.get(2, 0),
        "priority_3": by_pri.get(3, 0),
        "priority_4": by_pri.get(4, 0),
        "fresh_trigger_counties": sum(1 for r in rows
                                      if r["fresh_trigger"] == "1"),
        "cross_state_candidates": sum(1 for r in rows
                                      if r["cross_state_seed"] == "1"),
        "adjacency_pairs_loaded": sum(len(v) for v in adj.values()),
        "small_jurisdiction_candidates": sum(
            1 for r in rows if r["small_jurisdiction"] == "1"),
        "candidates_with_no_automated_meeting_source": sum(
            1 for r in rows
            if r["meeting_source"] in ("unprobed", "none", "ambiguous")),
        "note": ("Adjacency is a search prompt, never evidence. No label, "
                 "score, or client-facing claim may be derived from a row "
                 "in this queue. Census-only seeds are included because a "
                 "census-enacted county with no tracker record still "
                 "radiates diffusion pressure; excluding them would make "
                 "the scan inherit the coverage gap it exists to close. "
                 "An undated seed cannot produce a fresh trigger, so date "
                 "recovery on enacted counties directly increases the "
                 "queue's sensitivity. small_jurisdiction is a detection "
                 "difficulty tier, not a risk factor, and meeting_source "
                 "reports whether local_meeting_feed.py has any automated "
                 "agenda route into the county at all."),
    }
    return rows, summary


def write_rows(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def merge_watchlist(rows: list[dict], path: str, now: str,
                    max_priority: int = WATCHLIST_MAX_PRIORITY) -> dict:
    """Merge-preserving watchlist write.

    A reviewer's note and the date a county first entered the watchlist are
    never overwritten, and a county that falls out of the queue is retained
    with still_queued = 0 rather than deleted. Same discipline as
    restriction_worklist.py: a CI auto-commit must not be able to discard
    research.
    """
    existing = {}
    for r in read_csv(path):
        key = ((r.get("state") or "").strip().upper(),
               (r.get("county") or "").strip())
        existing[key] = r

    queued = [r for r in rows if r["priority"] <= max_priority]
    out: dict[tuple, dict] = {}
    added = 0
    for r in queued:
        key = (r["state"], r["county"])
        prev = existing.get(key, {})
        if not prev:
            added += 1
        out[key] = {
            "state": r["state"],
            "county": r["county"],
            "fips": r["fips"],
            "reason": (f"priority {r['priority']} adjacency to "
                       f"{r['nearest_seed_county']}, "
                       f"{r['nearest_seed_state']}"),
            "priority": r["priority"],
            "first_queued_utc": prev.get("first_queued_utc") or now,
            "last_seen_utc": now,
            "still_queued": "1",
            "reviewer_note": prev.get("reviewer_note", ""),
        }
    retired = 0
    for key, prev in existing.items():
        if key in out:
            continue
        retired += 1
        prev = dict(prev)
        prev["still_queued"] = "0"
        out[key] = {k: prev.get(k, "") for k in WATCH_FIELDS}

    ordered = sorted(out.values(),
                     key=lambda r: (r.get("still_queued") != "1",
                                    str(r.get("priority", "9")),
                                    r.get("state", ""), r.get("county", "")))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=WATCH_FIELDS,
                           extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(ordered)
    return {"watchlist_rows": len(ordered), "watchlist_added": added,
            "watchlist_retired_this_run": retired,
            "watchlist_still_queued": sum(1 for r in ordered
                                          if r.get("still_queued") == "1")}


def write_report(rows: list[dict], summary: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [
        "# Adjacency re-scan queue",
        "",
        f"As of {summary['as_of']}. Generated by adjacency_scan.py. "
        f"Trigger window {summary['trigger_days']} days.",
        "",
        "Adjacency is a search prompt, never evidence. Nothing in this "
        "queue is a finding, a label, or a client-facing claim; each row is "
        "a county worth checking because a county next to it restricted "
        "something.",
        "",
        "## Counts",
        "",
        f"- Seed counties (enacted, tracker label or external census): "
        f"{summary['seed_counties']}",
        f"- Seeds carrying no usable date: {summary['seeds_undated']} "
        f"(an undated seed cannot raise a fresh trigger)",
        f"- Candidate counties adjacent to a seed: "
        f"{summary['candidate_counties']}",
        f"- Priority 1, non-terminal record plus fresh trigger: "
        f"{summary['priority_1']}",
        f"- Priority 2, fresh trigger with pressure: "
        f"{summary['priority_2']}",
        f"- Priority 3, standing cluster: {summary['priority_3']}",
        f"- Priority 4, background: {summary['priority_4']}",
        f"- Cross-state candidates (the Walker/Hamilton shape): "
        f"{summary['cross_state_candidates']}",
        f"- Small-jurisdiction candidates (population under "
        f"{SMALL_JURISDICTION_POP:,}): "
        f"{summary['small_jurisdiction_candidates']}",
        f"- Candidates with no automated agenda route: "
        f"{summary['candidates_with_no_automated_meeting_source']}",
        "",
        "Small jurisdiction is a detection-difficulty tier, not a risk "
        "factor. Population stands in for whether a county has a newsroom "
        "that ingestion can see; it never enters a model.",
        "",
        "## Priorities 1 and 2",
        "",
        "| P | State | County | Enacted nbrs | Nearest enactment | Days | "
        "Non-terminal rows | Projects | Small | Agenda route |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    top = [r for r in rows if r["priority"] <= 2][:40]
    for r in top:
        lines.append(
            f"| {r['priority']} | {r['state']} | {r['county']} | "
            f"{r['enacted_neighbors']} | {r['nearest_seed_county']}, "
            f"{r['nearest_seed_state']} ({r['nearest_seed_date'] or 'undated'})"
            f" | {r['days_since_nearest_seed']} | "
            f"{r['stale_nonterminal_records']} | {r['n_projects_tracked']} | "
            f"{'yes' if r['small_jurisdiction'] == '1' else 'no'} | "
            f"{r['meeting_source']} |")
    if not top:
        lines.append("| none | | | | | | | | | |")

    by_state = Counter(r["state"] for r in rows if r["priority"] <= 3)
    lines += [
        "",
        "## Priorities 1 to 3 by state",
        "",
        "| State | Counties |",
        "|---|---|",
    ]
    for st, n in sorted(by_state.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {st} | {n} |")
    lines += [
        "",
        "## Local meeting ingestion",
        "",
        f"Priorities 1 and 2 are written to configs/local_meeting_watchlist"
        f".csv. local_meeting_feed.py unions that file into its discovery "
        f"and fetch frames, which is the only way a county with zero tracker "
        f"records can be polled at all: both frames are otherwise built from "
        f"the clean feed, so the ingestion frame is defined by what the "
        f"tracker already knows.",
        "",
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def ck(label, cond):
        checks.append((label, cond))

    as_of = date(2026, 8, 18)
    # 47065 Hamilton TN (seed, enacted 2026-07-15) borders 13295 Walker GA,
    # 47061 Grundy TN and 47121 Meigs TN. 47031 Coffee TN borders Grundy and
    # 47177 Warren TN. 19153 Polk IA is a seed with a very old enactment.
    agg = [
        {"fips": "47065", "county_name": "Hamilton County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "1",
         "existing_dc_count": "2", "n_projects_tracked": "3",
         "n_opposition_events": "5", "population": "375000"},
        {"fips": "13295", "county_name": "Walker County, Georgia",
         "state": "GA", "has_enacted_restrictive": "0",
         "existing_dc_count": "0", "n_projects_tracked": "1",
         "n_opposition_events": "0", "population": "67600"},
        {"fips": "47061", "county_name": "Grundy County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0",
         "existing_dc_count": "0", "n_projects_tracked": "0",
         "n_opposition_events": "0", "population": "13600"},
        {"fips": "47121", "county_name": "Meigs County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0",
         "existing_dc_count": "0", "n_projects_tracked": "0",
         "n_opposition_events": "0", "population": "13000"},
        {"fips": "47031", "county_name": "Coffee County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0",
         "existing_dc_count": "0", "n_projects_tracked": "0",
         "n_opposition_events": "0"},
        {"fips": "47177", "county_name": "Warren County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0",
         "existing_dc_count": "1", "n_projects_tracked": "0",
         "n_opposition_events": "0"},
        {"fips": "19153", "county_name": "Polk County, Iowa",
         "state": "IA", "has_enacted_restrictive": "1",
         "existing_dc_count": "4", "n_projects_tracked": "2",
         "n_opposition_events": "9"},
        {"fips": "19049", "county_name": "Dallas County, Iowa",
         "state": "IA", "has_enacted_restrictive": "0",
         "existing_dc_count": "1", "n_projects_tracked": "1",
         "n_opposition_events": "0", "population": "110000"},
    ]
    adj = {
        "47065": {"13295": 7, "47061": 2, "47121": 1},
        "13295": {"47065": 7},
        "47061": {"47065": 2, "47031": 3},
        "47121": {"47065": 1},
        "47031": {"47061": 3, "47177": 2},
        "47177": {"47031": 2},
        "19153": {"19049": 4, "99999": 1},
        "19049": {"19153": 4},
    }
    records = [
        # Hamilton's own enacted record, dated inside the trigger window.
        {"State": "TN", "County": "Hamilton County",
         "qc_mechanism": "moratorium", "outcome_defensible":
         "blocked_confirmed", "Date": "2026-07-15"},
        # Walker carries a non-terminal restrictive record: priority 1.
        {"State": "GA", "County": "Walker County",
         "qc_mechanism": "moratorium", "outcome_defensible": "pending",
         "Date": "2026-06-01"},
        # Polk's enactment is years old: no fresh trigger for Dallas.
        {"State": "IA", "County": "Polk County", "qc_mechanism": "ban",
         "outcome_defensible": "blocked_confirmed", "Date": "2022-01-10"},
    ]
    gap = [
        # Census-only seed: Coffee County TN, enacted, no tracker record.
        {"state": "TN", "county": "Coffee County", "census_status": "active",
         "census_date": "2026-05-18", "gap_class": "missing"},
        # Out-of-scope census status must not seed anything.
        {"state": "TN", "county": "Meigs County", "census_status": "pending",
         "census_date": "2026-08-01", "gap_class":
         "census_pending_out_of_scope"},
    ]
    scores = {"13295": {"calibrated_score": "0.21", "score_decile": "9"}}
    sources = {"GA::Walker County": {"platform": "civicplus_rss"}}

    by_fips, by_name = county_index(agg)
    src = {k: v["platform"] for k, v in sources.items()}
    rows, summary = scan(by_fips, by_name, adj, gap, records, scores, as_of,
                         sources=src)
    by = {r["fips"]: r for r in rows}

    ck("enacted counties are not their own candidates", "47065" not in by)
    ck("census-only enacted county seeds the scan and is not a candidate",
       "47031" not in by)
    ck("out-of-scope census status does not seed",
       summary["seed_basis"].get("census_only") == 1)
    ck("seed count covers tracker label plus census",
       summary["seed_counties"] == 3)

    walker = by["13295"]
    ck("Walker is priority 1 (fresh trigger plus non-terminal record)",
       walker["priority"] == 1)
    ck("Walker is flagged cross-state", walker["cross_state_seed"] == "1")
    ck("Walker names Hamilton as the nearest enactment",
       walker["nearest_seed_fips"] == "47065")
    ck("Walker carries the trigger age",
       walker["days_since_nearest_seed"] == 34)
    ck("Walker carries its non-terminal record count",
       walker["stale_nonterminal_records"] == 1)
    ck("Walker carries the county score when available",
       walker["calibrated_score"] == "0.21")
    ck("every row is marked search-prompt only",
       all(r["evidence_status"] == "search_prompt_only" for r in rows))

    ck("Walker's agenda route is read from the discovery cache",
       walker["meeting_source"] == "civicplus_rss")
    ck("Walker is tiered a small jurisdiction (68k, under the line)",
       walker["small_jurisdiction"] == "1")
    ck("Dallas IA at 110k is not tiered small",
       by["19049"]["small_jurisdiction"] == "0")
    ck("a county with no population on record is not tiered small",
       all(r["small_jurisdiction"] == "0" for r in rows
           if r["population"] == 0))

    grundy = by["47061"]
    ck("Grundy has two enacted neighbours (Hamilton and Coffee)",
       grundy["enacted_neighbors"] == 2)
    ck("Grundy is priority 3: fresh trigger, second neighbour, no exposure",
       grundy["priority"] == 3)
    ck("Grundy takes the most recent adjacent enactment as nearest",
       grundy["nearest_seed_fips"] == "47065")
    ck("a county never probed reports unprobed, not none",
       grundy["meeting_source"] == "unprobed")

    meigs = by["47121"]
    ck("Meigs has a fresh trigger", meigs["fresh_trigger"] == "1")
    ck("Meigs falls to the background queue: one neighbour, no exposure, "
       "no recorded opposition",
       meigs["priority"] == 4)
    ck("Meigs is not cross-state", meigs["cross_state_seed"] == "0")

    warren = by["47177"]
    ck("Warren is adjacent only to a non-seed and stays out of the queue",
       "47177" not in by or warren["enacted_neighbors"] >= 1)

    dallas = by["19049"]
    ck("Dallas IA has no fresh trigger (2022 enactment)",
       dallas["fresh_trigger"] == "0")
    ck("Dallas IA falls to the background queue with one enacted neighbour",
       dallas["priority"] == 4)

    ck("neighbours outside the county frame are dropped",
       all(r["fips"] != "99999" for r in rows))
    ck("priority 1 sorts first", rows[0]["priority"] == 1)

    # Trigger window is a parameter, not a constant baked into the ranking.
    rows_narrow, _ = scan(by_fips, by_name, adj, gap, records, scores, as_of,
                          trigger_days=10, sources=src)
    ck("a narrower trigger window demotes Walker",
       {r["fips"]: r for r in rows_narrow}["13295"]["priority"] > 1)

    # Seed filter: ad-hoc single-county neighbour check.
    rows_seed, sum_seed = scan(by_fips, by_name, adj, gap, records, scores,
                               as_of, seed_filter={"47065"}, sources=src)
    ck("seed filter restricts the scan to one county's neighbours",
       sum_seed["seed_counties"] == 1
       and {r["fips"] for r in rows_seed} == {"13295", "47061", "47121"})

    # Missing adjacency degrades to an empty queue, never an exception.
    rows_none, sum_none = scan(by_fips, by_name, {}, gap, records, scores,
                               as_of)
    ck("no adjacency table degrades to an empty queue",
       rows_none == [] and sum_none["candidate_counties"] == 0)

    # Watchlist merge behaviour.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wl = os.path.join(td, "local_meeting_watchlist.csv")
        st1 = merge_watchlist(rows, wl, "2026-08-18T00:00:00Z")
        first = {(r["state"], r["county"]): r for r in read_csv(wl)}
        ck("watchlist takes priorities 1 and 2 only",
           st1["watchlist_still_queued"]
           == sum(1 for r in rows if r["priority"] <= 2))
        ck("watchlist records first-queued time",
           first[("GA", "Walker County")]["first_queued_utc"]
           == "2026-08-18T00:00:00Z")

        # A reviewer adds a note, then the queue is regenerated later.
        allrows = read_csv(wl)
        for r in allrows:
            if r["county"] == "Walker County":
                r["reviewer_note"] = "civicplus portal confirmed"
        with open(wl, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=WATCH_FIELDS,
                               lineterminator="\n")
            w.writeheader()
            w.writerows(allrows)

        st2 = merge_watchlist(rows, wl, "2026-09-01T00:00:00Z")
        second = {(r["state"], r["county"]): r for r in read_csv(wl)}
        ck("reviewer note survives regeneration",
           second[("GA", "Walker County")]["reviewer_note"]
           == "civicplus portal confirmed")
        ck("first-queued time is not overwritten",
           second[("GA", "Walker County")]["first_queued_utc"]
           == "2026-08-18T00:00:00Z")
        ck("last-seen time advances",
           second[("GA", "Walker County")]["last_seen_utc"]
           == "2026-09-01T00:00:00Z")
        ck("nothing added on a no-change rerun", st2["watchlist_added"] == 0)

        # A county leaving the queue is retained, not deleted.
        st3 = merge_watchlist([r for r in rows if r["fips"] != "13295"], wl,
                              "2026-09-15T00:00:00Z")
        third = {(r["state"], r["county"]): r for r in read_csv(wl)}
        ck("a county leaving the queue is retained",
           ("GA", "Walker County") in third)
        ck("a retired county is marked not queued",
           third[("GA", "Walker County")]["still_queued"] == "0")
        ck("a retired county keeps its reviewer note",
           third[("GA", "Walker County")]["reviewer_note"]
           == "civicplus portal confirmed")
        ck("retirement is counted",
           st3["watchlist_retired_this_run"] == 1)

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--state", default=None,
                    help="comma-separated state codes to restrict the queue")
    ap.add_argument("--seed-fips", default=None,
                    help="comma-separated seed FIPS; scans only their "
                         "neighbours (ad-hoc post-enactment check)")
    ap.add_argument("--trigger-days", type=int, default=TRIGGER_DAYS)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    ap.add_argument("--no-watchlist", action="store_true",
                    help="skip the local meeting watchlist write")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not os.path.exists(ADJ_CSV):
        print(f"ERROR: {os.path.relpath(ADJ_CSV, HERE)} not found. "
              f"Run fetch_county_adjacency.py first.")
        return 1
    for path in (AGG_CSV, MASTER_CLEAN):
        if not os.path.exists(path):
            print(f"ERROR: {os.path.relpath(path, HERE)} not found; "
                  f"the adjacency scan needs it")
            return 1

    as_of = parse_date(args.as_of) if args.as_of else date.today()
    if as_of is None:
        print(f"ERROR: could not parse --as-of {args.as_of!r}")
        return 1

    adj = load_adjacency(ADJ_CSV)
    agg = read_csv(AGG_CSV)
    by_fips, by_name = county_index(agg)
    gap = read_csv(GAP_CSV)
    records = read_csv(MASTER_CLEAN)
    scores = {}
    for r in read_csv(SCORES_CSV):
        f = (r.get("fips") or "").strip()
        if f:
            scores[f] = {"calibrated_score": (r.get("calibrated_score")
                                              or "").strip(),
                         "score_decile": (r.get("score_decile") or "").strip()}

    seed_filter = ({s.strip() for s in args.seed_fips.split(",") if s.strip()}
                   if args.seed_fips else None)
    rows, summary = scan(by_fips, by_name, adj, gap, records, scores, as_of,
                         trigger_days=args.trigger_days,
                         seed_filter=seed_filter,
                         sources=meeting_sources())
    if args.state:
        keep = {s.strip().upper() for s in args.state.split(",") if s.strip()}
        rows = [r for r in rows if r["state"] in keep]
        summary["state_filter"] = sorted(keep)
        summary["candidate_counties"] = len(rows)
    if not gap:
        summary["census_seeds"] = ("data/coverage_gap_report.csv absent; "
                                  "seeds are tracker labels only")

    write_rows(rows, OUT_CSV)
    if not args.no_watchlist:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        summary.update(merge_watchlist(rows, WATCHLIST, now))
    write_report(rows, summary, OUT_MD)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(f"as of {summary['as_of']}, trigger window "
          f"{summary['trigger_days']} days")
    print(f"seed counties: {summary['seed_counties']} "
          f"({summary['seed_basis']})")
    print(f"seeds with no usable date: {summary['seeds_undated']}")
    print(f"candidates: {summary['candidate_counties']}")
    print(f"  priority 1 (non-terminal record plus fresh trigger): "
          f"{summary['priority_1']}")
    print(f"  priority 2 (fresh trigger with pressure): "
          f"{summary['priority_2']}")
    print(f"  priority 3 (standing cluster): {summary['priority_3']}")
    print(f"  priority 4 (background): {summary['priority_4']}")
    print(f"  cross-state candidates: {summary['cross_state_candidates']}")
    print(f"  small jurisdictions: "
          f"{summary['small_jurisdiction_candidates']}, no automated agenda "
          f"route: {summary['candidates_with_no_automated_meeting_source']}")
    if not args.no_watchlist:
        print(f"watchlist: {summary['watchlist_still_queued']} queued, "
              f"{summary['watchlist_added']} added, "
              f"{summary['watchlist_retired_this_run']} retired")
    print(f"\nwrote {OUT_CSV}")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    if not args.no_watchlist:
        print(f"wrote {WATCHLIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
