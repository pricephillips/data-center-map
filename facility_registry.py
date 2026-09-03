#!/usr/bin/env python3
"""
facility_registry.py

Layer A registry: existing and operating data centers, with stable identifiers.

The facility layer is two hand-placed snapshots. They have no key, so nothing
can point at a facility, no second source can be reconciled against them, and a
project that reaches operating status has nowhere to graduate to. This module
wraps them additively, exactly as the permit machinery wraps its sources: the
snapshots are never rewritten, the registry is derived from them and
regenerable, and everything arriving from anywhere else is a candidate until a
gate promotes it.

What it does

  1. Builds one registry row per snapshot row, with a deterministic
     facility_id derived from the source, the normalized name and the
     geography. The same row yields the same id on every run and on every
     machine.
  2. Clusters rows that describe the same physical site across sources, so a
     campus present in both snapshots can be counted once without either
     source losing its provenance.
  3. Preserves first_seen across regenerations. A derived file that resets its
     own history every run cannot answer when something appeared, which is
     most of what a registry is for.
  4. Reads two candidate streams and gates them:
       data/facility_candidates.csv        signal harvester (Layer A routing)
       Layer B graduations                 projects that reached operating
     Every decision is written to data/facility_promotion_report.csv.

The gate, and why it holds most rows

A registry row asserts that a specific facility exists at a specific place. A
news headline usually cannot support that: it names an operator and a county
but not a facility, and promoting it would put a row in Layer A that no one can
verify later. So a candidate promotes only with a name, a state, a location and
a source URL, and everything else is held with the reason recorded. A held row
is not a rejected row; it is a row waiting for the one field that makes it
checkable.

An announcement is deliberately not promotable. An announced data center is a
proposed project, which is Layer B, and treating it as Layer A would put
buildings that do not exist into a register of buildings that do.

Reads
  configs/facility_sources.json, the declared snapshots
  data/facility_candidates.csv, data/proposals.csv, data/project_lifecycles.csv
  data/facility_registry.csv, for first_seen continuity

Writes
  data/facility_registry.csv
  data/facility_promotion_report.csv   append-only decision trail
  data/facility_registry_summary.json

Usage
  python facility_registry.py
  python facility_registry.py --promote
  python facility_registry.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, OrderedDict

from promotion_trail import new_decisions

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SOURCES = os.path.join(HERE, "configs", "facility_sources.json")
REGISTRY = os.path.join(DATA, "facility_registry.csv")
CANDIDATES = os.path.join(DATA, "facility_candidates.csv")
OSM_CANDIDATES = os.path.join(DATA, "facility_candidates_osm.csv")
PROPOSALS = os.path.join(DATA, "proposals.csv")
REPORT = os.path.join(DATA, "facility_promotion_report.csv")
SUMMARY = os.path.join(DATA, "facility_registry_summary.json")

FIELDS = ["facility_id", "cluster_id", "source_id", "name", "operator",
          "state", "county", "lat", "lon", "sqft", "capacity_mw", "status",
          "evidence_url", "first_seen", "last_seen"]

REPORT_FIELDS = ["run_date", "stream", "action", "facility_id", "name",
                 "state", "county", "reason", "evidence_url"]

# Signals that assert a facility exists or is being built. An announcement is
# absent on purpose: an announced data center is a Layer B project.
PROMOTABLE_SIGNALS = {"operational", "construction_start", "expansion"}

# Layer B phases that mean the project became a facility.
GRADUATING_PHASES = {"operational"}

CLUSTER_KM = 1.5          # two rows this close with a shared name token are one site

FORM_WORDS = {"data", "center", "centre", "campus", "facility", "llc", "inc",
              "corporation", "corp", "company", "co", "the", "and", "of"}

# Some sources carry a free-text address and no state column. Pulling the
# state out matters because clustering requires state agreement, and a row with
# no state can never join a cluster.
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())


def state_from_address(address: str) -> str:
    """Two-letter state from a free-text US address, or "" if none is certain.

    Deliberately conservative: the full name anywhere in the string, or a bare
    two-letter code in a position an address puts one. A guess here would
    place a facility in the wrong state, which is worse than leaving the field
    empty and letting it sit outside every cluster.
    """
    t = (address or "").strip()
    if not t:
        return ""
    low = t.lower()
    for name, code in STATE_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return code
    m = re.search(r"\b([A-Z]{2})\b(?:\s+\d{5}(?:-\d{4})?)?\s*$", t)
    if m and m.group(1) in STATE_CODES:
        return m.group(1)
    m = re.search(r",\s*([A-Z]{2})\b", t)
    if m and m.group(1) in STATE_CODES:
        return m.group(1)
    return ""


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def norm_name(name: str) -> str:
    """Lowercase, punctuation-free, form words dropped. Used for identity and
    clustering, never for display."""
    t = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    words = [w for w in t.split() if w and w not in FORM_WORDS]
    return " ".join(words)


def facility_id(source_id: str, name: str, state: str, county: str,
                lat: str = "", lon: str = "") -> str:
    """Deterministic and stable: the same row yields the same id everywhere.

    Coordinates are part of the identity, to four decimal places, roughly ten
    metres. Name plus county alone is not enough: the atlas carries many
    buildings sharing a campus name inside one county, and keying on the name
    collapses 1,479 rows to 1,075, silently destroying the distinction between
    a campus and its buildings. Kilometre precision still loses 234 of them.
    At ten metres every distinct row survives and the five exact duplicates in
    the snapshot still collapse, which is the behaviour wanted from both.

    An upstream re-pull that moves a coordinate mints a new id. That is the
    accepted cost: continuity across a moved coordinate is the cluster's job,
    not the identifier's, and an identifier that tolerated the move would have
    to be loose enough to merge neighbouring buildings.
    """
    key = norm_name(name)
    raw = "|".join([source_id or "", key, (state or "").upper().strip(),
                    (county or "").lower().strip(),
                    _round(lat), _round(lon)])
    return "fac_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _round(v, places: int = 4) -> str:
    try:
        return f"{round(float(v), places):.{places}f}"
    except (TypeError, ValueError):
        return ""


def haversine_km(lat1, lon1, lat2, lon2) -> float | None:
    try:
        a1, o1, a2, o2 = (float(lat1), float(lon1), float(lat2), float(lon2))
    except (TypeError, ValueError):
        return None
    r = 6371.0
    p1, p2 = math.radians(a1), math.radians(a2)
    dp, do = math.radians(a2 - a1), math.radians(o2 - o1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(do / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def same_site(a: dict, b: dict) -> bool:
    """Two registry rows describing one physical site.

    Requires agreement on state, plus either an identical normalized name or a
    shared distinctive name token within CLUSTER_KM. Distance alone is never
    enough: two operators can build on adjacent parcels, and merging them would
    undercount the layer this registry exists to count.
    """
    if (a.get("state") or "").upper() != (b.get("state") or "").upper():
        return False
    na, nb = norm_name(a.get("name")), norm_name(b.get("name"))
    if na and na == nb:
        return True
    d = haversine_km(a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon"))
    if d is None or d > CLUSTER_KM:
        return False
    ta, tb = set(na.split()), set(nb.split())
    return bool(ta & tb)


def cluster(rows: list[dict]) -> None:
    """Assign cluster_id in place. Rows in one cluster share a physical site."""
    by_state: dict[str, list[dict]] = {}
    for r in rows:
        by_state.setdefault((r.get("state") or "").upper(), []).append(r)
    for state_rows in by_state.values():
        clusters: list[list[dict]] = []
        for r in state_rows:
            for c in clusters:
                if any(same_site(r, other) for other in c):
                    c.append(r)
                    break
            else:
                clusters.append([r])
        for c in clusters:
            cid = min(r["facility_id"] for r in c).replace("fac_", "clu_")
            for r in c:
                r["cluster_id"] = cid


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _pick(row: dict, *names) -> str:
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def rows_from_snapshot(source_id: str, rows: list[dict],
                       us_only: bool) -> list[dict]:
    out = []
    for r in rows:
        country = _pick(r, "Country", "country")
        if us_only and country and country.lower() not in (
                "united states", "usa", "us", "u.s.",
                "united states of america"):
            continue
        name = _pick(r, "name", "Name")
        state = _pick(r, "state", "State")
        if not state:
            state = state_from_address(_pick(r, "Address", "address"))
        county = _pick(r, "county", "County")
        lat, lon = _pick(r, "lat", "Lat"), _pick(r, "lon", "Lon")
        out.append({
            "facility_id": facility_id(source_id, name, state, county, lat, lon),
            "cluster_id": "",
            "source_id": source_id,
            "name": name,
            "operator": _pick(r, "operator", "Owner", "Operator"),
            "state": state,
            "county": county,
            "lat": lat,
            "lon": lon,
            "sqft": _pick(r, "sqft"),
            "capacity_mw": _pick(r, "Current power (MW)", "capacity_mw"),
            "status": "operating",
            "evidence_url": _pick(r, "Selected Sources", "source_url"),
            "first_seen": "",
            "last_seen": "",
        })
    return out


def build_registry(config: dict, root: str = HERE,
                   today: str | None = None) -> list[dict]:
    today = today or dt.date.today().isoformat()
    prior = {r["facility_id"]: r for r in read_csv(os.path.join(
        root, "data", "facility_registry.csv"))}
    rows: list[dict] = []
    seen: set[str] = set()
    for src in config["sources"]:
        # A planned source is declared before it is acquired and has no file
        # yet. facility_manifest.py grew this guard when the planned sources
        # were added and this module did not, which is the whole defect: the
        # two modules read the same config and only one of them was taught
        # that a source can exist on paper before it exists on disk.
        if not src.get("file"):
            continue
        path = os.path.join(root, src["file"])
        if not os.path.exists(path):
            continue
        us_only = "global" in (src.get("geography") or "").lower()
        for row in rows_from_snapshot(src["source_id"], read_csv(path), us_only):
            if row["facility_id"] in seen:
                # Same source, same name, same county, same kilometre: one row
                # duplicated inside the snapshot, not two buildings.
                continue
            seen.add(row["facility_id"])
            was = prior.get(row["facility_id"])
            row["first_seen"] = (was or {}).get("first_seen") or today
            row["last_seen"] = today
            rows.append(row)
    cluster(rows)
    rows.sort(key=lambda r: (r["state"], r["name"], r["facility_id"]))
    return rows


# --------------------------------------------------------------------------
# candidates and the gate
# --------------------------------------------------------------------------

def graduation_candidates(proposals: list[dict]) -> list[dict]:
    """Layer B rows that reached operating status.

    A project that is built is a facility, and until the registry existed there
    was nowhere for it to go. Detection is mechanical: the registry's phase
    vocabulary, plus a stated opening year in the past.
    """
    today_year = dt.date.today().year
    out = []
    for p in proposals:
        phase = (p.get("phase") or "").strip().lower()
        year = re.match(r"(\d{4})", (p.get("yearOpened") or "").strip())
        opened = int(year.group(1)) if year else None
        if phase not in GRADUATING_PHASES and not (opened and opened <= today_year):
            continue
        out.append({
            "stream": "layer_b_graduation",
            "name": (p.get("name") or "").strip(),
            "operator": (p.get("companies") or "").strip(),
            "state": (p.get("state") or "").strip(),
            "county": (p.get("counties") or "").strip(),
            "lat": (p.get("lat") or "").strip(),
            "lon": (p.get("lon") or "").strip(),
            "capacity_mw": (p.get("capacity_mw") or "").strip(),
            "signal": "operational" if phase in GRADUATING_PHASES else "opened",
            "evidence_url": (p.get("info") or "").strip(),
            "project_id": (p.get("id") or "").strip(),
        })
    return out


def harvest_candidates(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "stream": "signal_harvest",
            "name": "",                      # a headline is not a facility name
            "operator": "",
            "state": (r.get("state") or "").strip(),
            "county": (r.get("county") or "").strip(),
            "lat": "", "lon": "", "capacity_mw": "",
            "signal": (r.get("facility_signal") or "").split(";")[0].strip(),
            "evidence_url": (r.get("url") or "").strip(),
            "headline": (r.get("title") or "").strip(),
        })
    return out


def osm_candidates(rows: list[dict]) -> list[dict]:
    """OpenStreetMap objects tagged as data centers, from
    fetch_osm_facilities.py.

    Unlike the harvest stream this one can carry a real facility name and real
    coordinates, so it is the first candidate stream capable of clearing the
    gate on its own evidence rather than waiting for a person to supply the
    missing field. An unnamed object still holds: OSM maps plenty of data
    centers with no name tag, and a registry row has to name what it asserts
    exists.

    No deduplication happens here. facility_id() keys on name, state, county
    and coordinates, so a re-pull of unchanged OSM data mints ids the gate
    already knows and blocks; see the note in fetch_osm_facilities.py on why
    a second opinion about facility identity does not belong in this file.
    """
    out = []
    for r in rows:
        out.append({
            "stream": "osm",
            "name": (r.get("name") or "").strip(),
            "operator": (r.get("operator") or "").strip(),
            "state": (r.get("state") or "").strip(),
            "county": (r.get("county") or "").strip(),
            "lat": (r.get("lat") or "").strip(),
            "lon": (r.get("lon") or "").strip(),
            "capacity_mw": "",
            "signal": (r.get("signal") or "").strip(),
            "evidence_url": (r.get("evidence_url") or "").strip(),
            "osm_id": f"{(r.get('osm_type') or '').strip()}/"
                      f"{(r.get('osm_id') or '').strip()}",
        })
    return out


def gate(candidate: dict, known_ids: set[str]) -> tuple[str, str, str]:
    """(action, reason, facility_id). action is promoted, held or blocked."""
    signal = (candidate.get("signal") or "").strip()
    if signal == "announcement":
        return ("blocked", "an announced data center is a proposed project, "
                           "which is Layer B, not a facility", "")
    if signal and signal not in PROMOTABLE_SIGNALS and signal != "opened":
        return ("held", f"signal '{signal}' does not assert that a facility "
                        f"exists", "")
    if not candidate.get("name"):
        return ("held", "no facility name: a registry row has to name what it "
                        "asserts exists", "")
    if not candidate.get("state"):
        return ("held", "no state", "")
    if not (candidate.get("county") or
            (candidate.get("lat") and candidate.get("lon"))):
        return ("held", "no location: needs a county or coordinates", "")
    if not candidate.get("evidence_url"):
        return ("held", "no source URL", "")
    fid = facility_id(candidate["stream"], candidate["name"],
                      candidate["state"], candidate["county"],
                      candidate.get("lat", ""), candidate.get("lon", ""))
    if fid in known_ids:
        return ("blocked", "already in the registry", fid)
    return ("promoted", "", fid)


def run_gate(candidates: list[dict], registry: list[dict],
             today: str) -> tuple[list[dict], list[dict]]:
    """(new registry rows, decision rows). Never mutates the snapshots."""
    known = {r["facility_id"] for r in registry}
    # A candidate matching an existing row on name and state is already known
    # even when its id differs, because ids carry the source.
    known_names = {(norm_name(r["name"]), (r["state"] or "").upper())
                   for r in registry if r["name"]}
    promoted, decisions = [], []
    for c in candidates:
        action, reason, fid = gate(c, known)
        if action == "promoted" and (norm_name(c["name"]),
                                     (c["state"] or "").upper()) in known_names:
            action, reason = "blocked", "already in the registry under another source"
        decisions.append({
            "run_date": today, "stream": c["stream"], "action": action,
            "facility_id": fid, "name": c.get("name", ""),
            "state": c.get("state", ""), "county": c.get("county", ""),
            "reason": reason, "evidence_url": c.get("evidence_url", ""),
        })
        if action != "promoted":
            continue
        promoted.append({
            "facility_id": fid, "cluster_id": "", "source_id": c["stream"],
            "name": c["name"], "operator": c.get("operator", ""),
            "state": c["state"], "county": c.get("county", ""),
            "lat": c.get("lat", ""), "lon": c.get("lon", ""), "sqft": "",
            "capacity_mw": c.get("capacity_mw", ""), "status": "operating",
            "evidence_url": c["evidence_url"], "first_seen": today,
            "last_seen": today,
        })
        known.add(fid)
        known_names.add((norm_name(c["name"]), (c["state"] or "").upper()))
    return promoted, decisions


def summarize(registry: list[dict], decisions: list[dict]) -> dict:
    clusters = {r["cluster_id"] for r in registry if r["cluster_id"]}
    return {
        "generated": dt.date.today().isoformat(),
        "rows": len(registry),
        "distinct_sites": len(clusters),
        "by_source": dict(Counter(r["source_id"] for r in registry).most_common()),
        "by_state": dict(Counter(r["state"] for r in registry
                                 if r["state"]).most_common(10)),
        "with_coordinates": sum(1 for r in registry if r["lat"] and r["lon"]),
        "decisions_this_run": dict(Counter(d["action"]
                                           for d in decisions).most_common()),
        "decisions_note": ("what the gate decided this run. A decision is "
                           "written to the trail only when it differs from "
                           "the last one recorded for that candidate, so a "
                           "steady-state run decides without recording."),
        "note": ("distinct_sites counts clusters, not rows: a campus present "
                 "in two snapshots is one site with two provenance records. "
                 "The snapshots themselves are never rewritten by this "
                 "module; the registry is derived and regenerable."),
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    check("form words dropped from identity",
          norm_name("Aligned Data Center, LLC") == "aligned")
    check("identity is stable across runs",
          facility_id("s", "Aligned", "VA", "Loudoun")
          == facility_id("s", "Aligned", "VA", "Loudoun"))
    check("identity separates sources",
          facility_id("a", "X", "VA", "L") != facility_id("b", "X", "VA", "L"))
    check("identity survives punctuation and case",
          facility_id("s", "ALIGNED DATA CENTER", "VA", "Loudoun")
          == facility_id("s", "Aligned Data Center", "VA", "Loudoun"))
    check("coordinates are part of the identity",
          facility_id("s", "Campus", "VA", "L", "39.00", "-77.50")
          != facility_id("s", "Campus", "VA", "L", "39.40", "-77.90"))
    check("two buildings ten metres apart stay distinct",
          facility_id("s", "Campus", "VA", "L", "39.0000", "-77.5000")
          != facility_id("s", "Campus", "VA", "L", "39.0002", "-77.5000"))
    check("an exact duplicate collapses",
          facility_id("s", "Campus", "VA", "L", "39.0", "-77.5")
          == facility_id("s", "Campus", "VA", "L", "39.00000", "-77.50000"))
    check("a nameless row still gets an identity from geography",
          facility_id("s", "", "VA", "L", "39.0", "-77.5")
          != facility_id("s", "", "VA", "L", "41.0", "-80.0"))

    check("state read from a full name in an address",
          state_from_address("1200 Mega Way, New Carlisle, Indiana 46552") == "IN")
    check("state read from a trailing code",
          state_from_address("100 Main St, Ashburn, VA 20147") == "VA")
    check("no state guessed from a bare town",
          state_from_address("Somewhere near the river") == "")

    a = {"name": "Aligned Campus", "state": "VA", "lat": "39.0", "lon": "-77.5"}
    b = {"name": "Aligned Data Center", "state": "VA", "lat": "39.001", "lon": "-77.501"}
    c = {"name": "Vantage Campus", "state": "VA", "lat": "39.0005", "lon": "-77.5005"}
    d = {"name": "Aligned Campus", "state": "OH", "lat": "40.0", "lon": "-83.0"}
    check("shared token and proximity is one site", same_site(a, b))
    check("proximity alone is not enough", not same_site(a, c))
    check("same name in another state is not one site", not same_site(a, d))

    rows = [dict(x, facility_id=f"fac_{i}", cluster_id="")
            for i, x in enumerate([a, b, c])]
    cluster(rows)
    check("clustering groups the two and leaves the third",
          rows[0]["cluster_id"] == rows[1]["cluster_id"]
          and rows[2]["cluster_id"] != rows[0]["cluster_id"])

    registry = [{"facility_id": "fac_known", "name": "Known Site",
                 "state": "VA", "cluster_id": ""}]
    good = {"stream": "t", "name": "New Site", "state": "IA",
            "county": "Story", "signal": "operational",
            "evidence_url": "https://example.com/a"}
    promoted, decisions = run_gate([good], registry, "2026-08-26")
    check("a complete candidate promotes", len(promoted) == 1)
    check("the promotion is recorded", decisions[0]["action"] == "promoted")

    cases = [
        ({"stream": "t", "name": "X", "state": "IA", "county": "Story",
          "signal": "announcement", "evidence_url": "u"}, "blocked",
         "announcement is Layer B"),
        ({"stream": "t", "name": "", "state": "IA", "county": "Story",
          "signal": "operational", "evidence_url": "u"}, "held",
         "a nameless headline is held, not promoted"),
        ({"stream": "t", "name": "X", "state": "", "county": "Story",
          "signal": "operational", "evidence_url": "u"}, "held",
         "no state is held"),
        ({"stream": "t", "name": "X", "state": "IA", "county": "",
          "signal": "operational", "evidence_url": "u"}, "held",
         "no location is held"),
        ({"stream": "t", "name": "X", "state": "IA", "county": "Story",
          "signal": "operational", "evidence_url": ""}, "held",
         "no source URL is held"),
        ({"stream": "t", "name": "Known Site", "state": "VA", "county": "L",
          "signal": "operational", "evidence_url": "u"}, "blocked",
         "an existing facility is not promoted twice"),
    ]
    for candidate, want, label in cases:
        _, dec = run_gate([candidate], registry, "2026-08-26")
        check(label, dec[0]["action"] == want)

    grads = graduation_candidates([
        {"id": "prj_1", "name": "Built One", "phase": "operational",
         "state": "IA", "counties": "Story", "info": "u", "yearOpened": ""},
        {"id": "prj_2", "name": "Opened One", "phase": "construction",
         "state": "OH", "counties": "Licking", "info": "u",
         "yearOpened": "2024"},
        {"id": "prj_3", "name": "Future One", "phase": "approved",
         "state": "TX", "counties": "Ellis", "info": "u",
         "yearOpened": "2031"},
        {"id": "prj_4", "name": "Proposed", "phase": "proposed",
         "state": "VA", "counties": "Loudoun", "info": "u", "yearOpened": ""},
    ])
    ids = {g["name"] for g in grads}
    check("an operational project graduates", "Built One" in ids)
    check("a past opening year graduates", "Opened One" in ids)
    check("a future opening year does not", "Future One" not in ids)
    check("a proposal does not graduate", "Proposed" not in ids)

    harvest = harvest_candidates([
        {"state": "IA", "county": "Story", "facility_signal": "operational",
         "url": "https://example.com/a", "title": "Data center opens"}])
    check("harvest rows carry no invented name", harvest[0]["name"] == "")
    _, hdec = run_gate(harvest, registry, "2026-08-26")
    check("so a harvest row is held for a reviewer",
          hdec[0]["action"] == "held" and "facility name" in hdec[0]["reason"])

    osm = osm_candidates([
        {"osm_type": "way", "osm_id": "1", "name": "Desert Campus",
         "operator": "Example", "state": "AZ", "county": "", "lat": "33.4",
         "lon": "-112.1", "signal": "operational",
         "evidence_url": "https://www.openstreetmap.org/way/1"},
        {"osm_type": "node", "osm_id": "2", "name": "", "operator": "",
         "state": "AZ", "county": "", "lat": "33.5", "lon": "-112.2",
         "signal": "operational",
         "evidence_url": "https://www.openstreetmap.org/node/2"}])
    opro, odec = run_gate(osm, registry, "2026-08-26")
    by_url = {d["evidence_url"]: d for d in odec}
    check("a named OSM object with coordinates clears the gate",
          by_url["https://www.openstreetmap.org/way/1"]["action"] == "promoted")
    check("an unnamed OSM object is held, not promoted",
          by_url["https://www.openstreetmap.org/node/2"]["action"] == "held")
    check("OSM promotion carries the object as its evidence",
          opro and opro[0]["evidence_url"].startswith(
              "https://www.openstreetmap.org/"))
    # The anti-redundancy claim in fetch_osm_facilities.py, asserted rather
    # than described: a second pull of unchanged data promotes nothing.
    again = run_gate(osm, registry + opro, "2026-08-27")[1]
    check("re-pulling unchanged OSM data promotes nothing a second time",
          all(d["action"] != "promoted" for d in again))

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "data"))
        with open(os.path.join(tmp, "snap.csv"), "w", encoding="utf-8",
                  newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["name", "operator", "state", "county", "lat", "lon", "sqft"])
            w.writerow(["Alpha", "Op", "VA", "Loudoun", "39.0", "-77.5", "100"])
            w.writerow(["Beta", "Op", "IA", "Story", "42.0", "-93.5", "200"])
        cfg = {"sources": [
            {"source_id": "snap", "file": "snap.csv",
             "geography": "United States"},
            # Declared but not yet acquired. Reading the same config as
            # facility_manifest.py means this module has to survive it.
            {"source_id": "planned", "file": None,
             "geography": "United States"},
            {"source_id": "missing", "file": "not_on_disk.csv",
             "geography": "United States"},
        ]}
        first = build_registry(cfg, root=tmp, today="2026-08-01")
        check("registry built from the snapshot", len(first) == 2)
        check("a planned source with no file is skipped, not a crash",
              all(r["source_id"] == "snap" for r in first))
        with open(os.path.join(tmp, "data", "facility_registry.csv"), "w",
                  encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(first)
        second = build_registry(cfg, root=tmp, today="2026-09-01")
        check("first_seen is preserved across regeneration",
              all(r["first_seen"] == "2026-08-01" for r in second))
        check("last_seen moves", all(r["last_seen"] == "2026-09-01"
                                     for r in second))
        original = open(os.path.join(tmp, "snap.csv"), encoding="utf-8").read()
        check("the snapshot is never rewritten", "Alpha" in original
              and original.count("\n") == 3)

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--promote", action="store_true",
                    help="apply the gate and write promoted rows into the "
                         "registry; without it the gate runs and reports only")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    today = dt.date.today().isoformat()
    with open(SOURCES, encoding="utf-8") as fh:
        config = json.load(fh)

    registry = build_registry(config)
    candidates = (harvest_candidates(read_csv(CANDIDATES))
                  + osm_candidates(read_csv(OSM_CANDIDATES))
                  + graduation_candidates(read_csv(PROPOSALS)))
    promoted, decisions = run_gate(candidates, registry, today)

    if args.promote and promoted:
        registry.extend(promoted)
        cluster(registry)
        registry.sort(key=lambda r: (r["state"], r["name"], r["facility_id"]))
    elif promoted:
        for d in decisions:
            if d["action"] == "promoted":
                d["action"] = "promotable"
                d["reason"] = "gate passed; run with --promote to apply"

    os.makedirs(DATA, exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(registry)

    # Append decisions, not re-statements of decisions. This runs on the
    # nightly schedule and re-decides the same candidates every time, so
    # appending unconditionally buried 10 real decisions under 40 rows inside
    # one day. The Data Operations page reports these counts as evidence that
    # the platform maintains itself, and a count inflated by repetition
    # overstates that evidence.
    fresh, suppressed = new_decisions(
        read_csv(REPORT), decisions,
        key_fields=("stream", "name", "state", "county"),
        state_fields=("action", "reason"))
    exists = os.path.exists(REPORT)
    if fresh:
        with open(REPORT, "a", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=REPORT_FIELDS,
                               lineterminator="\n")
            if not exists:
                w.writeheader()
            w.writerows(fresh)

    # Summarize what the gate decided, not merely what was newly written.
    # A steady-state run decides ten things and records none of them, and a
    # summary reporting an empty decision set would read as "the gate did
    # not run" rather than "nothing changed".
    summary = summarize(registry, decisions)
    summary["decisions_newly_recorded"] = len(fresh)
    summary["decisions_unchanged_since_last_run"] = suppressed
    with open(SUMMARY, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")

    print(f"facility registry: {summary['rows']} rows, "
          f"{summary['distinct_sites']} distinct sites")
    for source, count in summary["by_source"].items():
        print(f"  {source}: {count}")
    if decisions:
        print("candidate decisions this run: " + ", ".join(
            f"{k}={v}" for k, v in summary["decisions_this_run"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
