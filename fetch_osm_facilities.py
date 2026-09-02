#!/usr/bin/env python3
"""
fetch_osm_facilities.py — OpenStreetMap data centers via the Overpass API,
written to the Layer A candidate stream.

Why this source, and why now

configs/facility_sources.json records the IM3 atlas (atlas.csv, the largest
file in the repository) with acquisition status `needs_manual_pin`, blocked on
"a direct download URL for the current release, plus the release cadence",
because the CI egress proxy cannot reach osti.gov, msdlive.org or the atlas
site. That blocker has an obvious way around it that nobody had taken: the IM3
atlas is *derived from OpenStreetMap*, and OpenStreetMap is queryable directly,
live, with no key, from anywhere. Pulling the upstream removes the dependency
on a snapshot nobody can refresh and on a landing page CI cannot open.

What this does and does not assert

An OSM object tagged as a data center is evidence that someone mapped a data
center at a place. It is not evidence about capacity, operator, or status, and
OSM coverage is strong in major markets and thin for small or low-profile
sites. So this writes CANDIDATES, exactly like every other acquisition path
here, and facility_registry.py's existing gate decides what becomes a registry
row. Nothing here writes to atlas.csv: the standing rule in
configs/facility_sources.json is that a re-pull never overwrites the snapshot,
it diffs into candidates, and this obeys it.

Anti-redundancy, and why this module adds no deduplication of its own

Three mechanisms already stand between this file and a duplicated registry:

  1. `facility_registry.facility_id()` keys on source, normalized name, state,
     county and coordinates rounded to ten metres, so re-running this fetch
     over unchanged OSM data mints the same ids and `gate()` blocks every one
     of them as "already in the registry".
  2. `facility_registry.cluster()` already groups rows describing one physical
     site across sources. A campus that OSM maps as five buildings behaves
     exactly like the atlas rows for the same campus, which is the case that
     function was written for.
  3. The osm_type/osm_id pair is a stable upstream key and is carried on every
     row, so a later reconciliation can tell "the same object, moved" from
     "a different object".

Writing a fourth deduplication pass here would not remove a duplicate any of
those three miss, and it would put a second opinion about facility identity in
the repository, which is the thing that makes identity questions unanswerable.
What this module does own is deduplication *within a single pull*: Overpass
returns an object once per matching tag, so an object carrying two of the
tags queried arrives twice, and those collapse on (osm_type, osm_id).

Usage
  python fetch_osm_facilities.py --states AZ,NV,OR      # named states
  python fetch_osm_facilities.py                        # all 50 + DC
  python fetch_osm_facilities.py --fixture path/to.json # offline replay
  python fetch_osm_facilities.py --selftest

Stdlib only. Network access to overpass-api.de for live runs; the selftest is
fully offline.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

OUT_CSV = os.path.join(DATA, "facility_candidates_osm.csv")
LOG_CSV = os.path.join(DATA, "facility_candidates_osm_log.csv")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = ("hawthorn-dc-tracker/1.0 (facility layer; OSM data centers; "
              "contact repo owner)")

FIELDS = ["osm_type", "osm_id", "name", "operator", "state", "county",
          "lat", "lon", "signal", "evidence_url", "tags", "seen_date"]

LOG_FIELDS = ["run_date", "states_queried", "states_failed", "objects",
              "rows_written", "named_rows"]

STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
]

# The three tagging conventions in live use for a data center. telecom is the
# documented one; building and man_made are both common enough in US data that
# querying only the documented tag would miss real sites.
TAG_FILTERS = [
    '["telecom"="data_center"]',
    '["building"="data_center"]',
    '["man_made"="data_center"]',
]

# Per-state query. ISO3166-2 areas keep each request small enough to finish
# inside the public endpoint's budget; one nationwide query against a
# admin_level=2 area is the reliable way to get throttled instead of answered.
QUERY_TEMPLATE = """[out:json][timeout:{timeout}];
area["ISO3166-2"="US-{state}"][admin_level=4]->.a;
(
{clauses}
);
out center tags;
"""


def build_query(state: str, timeout: int = 180) -> str:
    clauses = "\n".join(f"  nwr(area.a){f};" for f in TAG_FILTERS)
    return QUERY_TEMPLATE.format(timeout=timeout, state=state, clauses=clauses)


def overpass(query: str, timeout: int = 240, retries: int = 3) -> dict:
    """POST one Overpass query. Retries on the endpoint's rate limiting, which
    is routine rather than exceptional and is answered with 429 or 504."""
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            OVERPASS_URL, data=data,
            headers={"User-Agent": USER_AGENT,
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 504):
                raise
        except Exception as exc:                       # timeout, reset, json
            last = exc
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"overpass failed after {retries} attempts: {last}")


def signal_for(tags: dict) -> str:
    """Which facility signal an OSM object supports.

    Deliberately narrow. A mapped object with no construction marker is
    evidence the thing is there, which is `operational`; a construction marker
    downgrades it to `construction_start`. Neither is an announcement, and the
    registry's gate blocks announcements anyway — an announced data center is
    Layer B.
    """
    blob = " ".join(f"{k}={v}" for k, v in tags.items()).lower()
    if "construction" in blob or tags.get("proposed:telecom") == "data_center":
        return "construction_start"
    return "operational"


def element_row(el: dict, state: str, seen: str) -> dict | None:
    tags = el.get("tags") or {}
    osm_type = (el.get("type") or "").strip()
    osm_id = str(el.get("id") or "").strip()
    if not osm_type or not osm_id:
        return None
    lat = el.get("lat")
    lon = el.get("lon")
    if lat is None or lon is None:
        centre = el.get("center") or {}
        lat, lon = centre.get("lat"), centre.get("lon")
    name = (tags.get("name") or tags.get("official_name") or "").strip()
    return {
        "osm_type": osm_type,
        "osm_id": osm_id,
        "name": name,
        "operator": (tags.get("operator") or tags.get("brand") or "").strip(),
        "state": state,
        "county": "",          # the registry accepts coordinates in its place
        "lat": "" if lat is None else f"{float(lat):.6f}",
        "lon": "" if lon is None else f"{float(lon):.6f}",
        "signal": signal_for(tags),
        "evidence_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "tags": json.dumps(tags, sort_keys=True, ensure_ascii=False),
        "seen_date": seen,
    }


def rows_from_response(payload: dict, state: str, seen: str) -> list[dict]:
    out = []
    for el in (payload or {}).get("elements", []):
        row = element_row(el, state, seen)
        if row:
            out.append(row)
    return out


def dedupe(rows: list[dict]) -> list[dict]:
    """Collapse on the upstream key.

    Overpass returns an object once per matching clause in the union, so an
    object tagged both telecom=data_center and building=data_center arrives
    twice in one response. This is the only deduplication this module owns;
    identity against the registry is facility_registry.py's job, and doing it
    twice in two places is how two answers to one question get into a
    repository.
    """
    by_key: dict[tuple[str, str], dict] = {}
    for r in rows:
        by_key.setdefault((r["osm_type"], r["osm_id"]), r)
    return sorted(by_key.values(),
                  key=lambda r: (r["state"], r["osm_type"], int(r["osm_id"])))


def write_rows(rows: list[dict], path: str = OUT_CSV) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def append_log(row: dict, path: str = LOG_CSV) -> None:
    exists = os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS, lineterminator="\n")
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LOG_FIELDS})


def fetch(states: list[str], seen: str) -> tuple[list[dict], list[str], int]:
    """(rows, states that failed, raw object count). A state that fails is
    recorded and skipped rather than fatal: one throttled state must not cost
    the other fifty, and the previous file stays on disk until a run has
    something to replace it with."""
    rows: list[dict] = []
    failed: list[str] = []
    raw = 0
    for st in states:
        try:
            payload = overpass(build_query(st))
        except Exception as exc:
            print(f"osm: {st} failed ({exc}); continuing")
            failed.append(st)
            continue
        got = rows_from_response(payload, st, seen)
        raw += len(got)
        rows.extend(got)
        print(f"osm: {st} {len(got)} objects")
    return rows, failed, raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="",
                    help="comma-separated USPS codes; default is all")
    ap.add_argument("--fixture", help="replay a saved Overpass JSON response")
    ap.add_argument("--out", default=OUT_CSV)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    seen = dt.date.today().isoformat()
    if args.fixture:
        with open(args.fixture, encoding="utf-8") as fh:
            payload = json.load(fh)
        rows = rows_from_response(payload, "XX", seen)
        failed, raw, states = [], len(rows), ["fixture"]
    else:
        states = ([s.strip().upper() for s in args.states.split(",") if s.strip()]
                  or STATES)
        rows, failed, raw = fetch(states, seen)

    rows = dedupe(rows)

    # An empty return is almost always throttling rather than an empty world,
    # and overwriting a good file with nothing is how a scheduled job quietly
    # deletes a source. Same rule signal_harvest.py applies to its worklist.
    if not rows and os.path.exists(args.out):
        print("osm: 0 objects returned; keeping the existing file rather than "
              "clearing it. An empty return is usually a throttled endpoint.")
        return 1 if failed else 0

    write_rows(rows, args.out)
    named = sum(1 for r in rows if r["name"])
    append_log({"run_date": seen, "states_queried": len(states),
                "states_failed": ",".join(failed), "objects": raw,
                "rows_written": len(rows), "named_rows": named})
    print(f"osm: {len(rows)} data center objects ({named} named) -> "
          f"{os.path.relpath(args.out, HERE)}")
    print("Candidates only. facility_registry.py gates what becomes a registry "
          "row; an unnamed object is held, not promoted.")
    return 1 if failed else 0


def selftest() -> int:
    ok = True

    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    q = build_query("AZ")
    expect('"ISO3166-2"="US-AZ"' in q, "query scopes to the requested state")
    expect(q.count("nwr(area.a)") == 3, "all three tagging conventions queried")
    expect("out center tags" in q,
           "center output requested, so ways and relations carry coordinates")

    payload = {"elements": [
        {"type": "way", "id": 1, "center": {"lat": 33.4, "lon": -112.1},
         "tags": {"telecom": "data_center", "name": "Example Campus",
                  "operator": "Example Corp"}},
        # same object, returned again by the second clause of the union
        {"type": "way", "id": 1, "center": {"lat": 33.4, "lon": -112.1},
         "tags": {"building": "data_center", "name": "Example Campus"}},
        {"type": "node", "id": 2, "lat": 33.5, "lon": -112.2,
         "tags": {"building": "data_center", "construction": "yes"}},
        {"type": "relation", "id": 3, "tags": {"telecom": "data_center"}},
    ]}
    rows = rows_from_response(payload, "AZ", "2026-09-02")
    expect(len(rows) == 4, "every element becomes a row before deduplication")
    rows = dedupe(rows)
    expect(len(rows) == 3, "the same osm object returned twice collapses to one")

    by_id = {r["osm_id"]: r for r in rows}
    expect(by_id["1"]["lat"] == "33.400000",
           "a way's center becomes its coordinates")
    expect(by_id["1"]["evidence_url"] ==
           "https://www.openstreetmap.org/way/1", "evidence url cites the object")
    expect(by_id["2"]["signal"] == "construction_start",
           "a construction marker downgrades the signal")
    expect(by_id["1"]["signal"] == "operational",
           "a mapped object with no construction marker is operational")
    expect(by_id["3"]["lat"] == "" and by_id["3"]["name"] == "",
           "an object with neither name nor coordinates still round-trips, "
           "for the registry gate to hold")

    expect(dedupe(rows) == dedupe(list(reversed(rows))),
           "deduplication is order-independent, so a re-pull is byte-stable")

    expect(signal_for({"telecom": "data_center"}) == "operational",
           "plain tagging is operational")
    expect(signal_for({"building": "construction"}) == "construction_start",
           "construction tagging is construction_start")

    print("\nSELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
