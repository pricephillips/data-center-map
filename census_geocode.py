"""
census_geocode.py

Resolves county FIPS for proposals the name-based lookup cannot place, using a
ladder that spends offline steps first and calls the Census Geocoding Services
API only when it has to.

Why a ladder and not just a geocoder
------------------------------------
The four proposals that fail FIPS resolution fail for four different reasons,
and only two of them are geocoding problems:

  prj_31   county recorded as "Marlon County", IN     typo for Marion
  prj_39   county recorded as "La Porte County", IN   lookup holds "LaPorte"
  prj_158  county blank, coordinates good             reverse geocode
  prj_149  county blank, coordinates 0.0/0.0          "Catawba County" is in
                                                      the project name

A fifth case is worse than an unresolved row because it looks resolved. The
five Connecticut proposals all carry a FIPS from the lookup table, and all five
are retired codes. Connecticut replaced its eight counties with nine planning
regions in 2022, so the county layer keys on 09110 through 09190 while the
lookup still returns 09001 through 09015. A retired code matches nothing, the
county model component falls to the percentile floor, and the site scores lower
than the evidence supports with no error anywhere.

The guard for that is general rather than Connecticut-specific: a resolved FIPS
is only accepted if it exists in data/county_aggregate.csv, which is the
current universe of 3,222 county-equivalents. A lookup hit that fails that
check is treated as unresolved and the ladder continues.

The ladder
----------
  1 lookup_exact       current behavior, unchanged
  2 lookup_normalized  spacing and punctuation variants (LaPorte / La Porte,
                       St. / Saint, hyphens, apostrophes)
  3 census_reverse     coordinates -> current county via the Census API. This
                       is authoritative and returns planning regions for CT
                       without needing a crosswalk.
  4 name_extract       "X County" pulled from the project name or info text,
                       then run back through the lookup
  5 census_forward     one-line address -> coordinates plus geography. Not
                       needed by any current row; it is the path that makes
                       address-level ingest from permit and agenda sources
                       possible later.
  6 lookup_nearmatch   single close county name within the same state. Low
                       confidence by construction, so it is written to a review
                       file and never applied.

Only high and medium confidence results reach the overlay. Low confidence
results go to a worklist for a person to confirm, consistent with not inferring
a value that cannot be verified.

Licensing note: the Census Geocoding Services API is public and needs no key.
The popular censusgeocode wrapper is GPL-3.0, so this calls the REST endpoint
directly with the standard library, per docs/tooling_scan.md.

Usage
-----
  python census_geocode.py                    full ladder, network allowed
  python census_geocode.py --offline          skip every network step
  python census_geocode.py --limit 10         cap new API calls this run
  python census_geocode.py --address "100 Main St, Ashburn, VA"
  python census_geocode.py --selftest

Outputs
-------
  data/county_fips_overlay.csv      project_id -> county, fips, method (applied)
  data/fips_resolution_review.csv   low-confidence candidates (not applied)
  data/fips_resolution_report.md    what resolved, how, and what is left
  data/geocode_cache.csv            append-only API cache, safe to commit
"""

import argparse
import csv
import difflib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
PROPOSALS_CSV = os.path.join(HERE, "data", "proposals.csv")
FIPS_LOOKUP_JSON = os.path.join(HERE, "data", "county_fips_lookup.json")
COUNTY_AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")

OVERLAY_CSV = os.path.join(HERE, "data", "county_fips_overlay.csv")
REVIEW_CSV = os.path.join(HERE, "data", "fips_resolution_review.csv")
REPORT_MD = os.path.join(HERE, "data", "fips_resolution_report.md")
CACHE_CSV = os.path.join(HERE, "data", "geocode_cache.csv")

GEOCODER = "https://geocoding.geo.census.gov/geocoder"
BENCHMARK = "Public_AR_Current"
VINTAGE = "Current_Current"
USER_AGENT = ("hawthorn-dc-tracker/1.0 (data center opposition monitoring; "
              "contact repo owner)")
TIMEOUT = 30

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

CONFIDENCE = {
    "lookup_exact": "high",
    "lookup_normalized": "high",
    "census_reverse": "high",
    "census_forward": "high",
    "name_extract": "medium",
    "lookup_nearmatch": "low",
}
APPLIED = ("high", "medium")

STATE_ABBREV = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico",
}
STATE_NAME_TO_ABBREV = {v.lower(): k for k, v in STATE_ABBREV.items()}

SUFFIX_RE = re.compile(
    r"\s+(county|parish|borough|census area|municipality|city and borough|"
    r"planning region)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_lookup():
    if not os.path.exists(FIPS_LOOKUP_JSON):
        return {}
    with open(FIPS_LOOKUP_JSON, encoding="utf-8") as fh:
        return {str(k).lower(): str(v) for k, v in json.load(fh).items()}


def load_valid_fips():
    """The current county-equivalent universe. A FIPS outside this set cannot
    join to any county layer, so it is not a usable answer."""
    out = {}
    for r in load_csv(COUNTY_AGG_CSV):
        f = (r.get("fips") or "").strip()
        if f:
            out[f] = (r.get("county_name") or "").strip()
    return out


def state_name_of(state):
    s = (state or "").strip()
    if not s:
        return ""
    if len(s) == 2 and s.upper() in STATE_ABBREV:
        return STATE_ABBREV[s.upper()].lower()
    return s.lower()


def _f(v):
    try:
        out = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return out


def usable_coords(lat, lon):
    """0.0/0.0 is a placeholder, not a location in the Gulf of Guinea."""
    la, lo = _f(lat), _f(lon)
    if la is None or lo is None:
        return None
    if abs(la) < 0.001 and abs(lo) < 0.001:
        return None
    if not (-90 <= la <= 90) or not (-180 <= lo <= 180):
        return None
    return la, lo


# ---------------------------------------------------------------------------
# Ladder steps that need no network
# ---------------------------------------------------------------------------

def _keys_for(county, state_name):
    c = (county or "").strip().lower()
    c = re.split(r"[;,/]", c)[0].strip()
    if not c or not state_name:
        return []
    bare = SUFFIX_RE.sub("", c).strip()
    forms = {c, bare, f"{bare} county"}
    return [f"{f}|{state_name}" for f in forms if f]


def lookup_exact(county, state, lookup):
    for k in _keys_for(county, state_name_of(state)):
        if k in lookup:
            return lookup[k]
    return None


def _variants(bare):
    """Spelling variants that mean the same county."""
    v = {bare}
    v.add(bare.replace(" ", ""))
    v.add(bare.replace("-", " "))
    v.add(bare.replace("-", ""))
    v.add(bare.replace("'", ""))
    v.add(bare.replace(".", ""))
    v.add(re.sub(r"^st\.?\s+", "saint ", bare))
    v.add(re.sub(r"^saint\s+", "st ", bare))
    v.add(re.sub(r"^saint\s+", "st. ", bare))
    v.add(re.sub(r"^ste\.?\s+", "sainte ", bare))
    # "La Porte" and "LaPorte", "De Kalb" and "DeKalb"
    m = re.match(r"^(la|le|de|du|van|mc|o)\s+(.+)$", bare)
    if m:
        v.add(m.group(1) + m.group(2))
    m2 = re.match(r"^(la|le|de|du|van|mc)([a-z]{3,})$", bare)
    if m2:
        v.add(m2.group(1) + " " + m2.group(2))
    return {x.strip() for x in v if x.strip()}


def lookup_normalized(county, state, lookup):
    state_name = state_name_of(state)
    c = (county or "").strip().lower()
    c = re.split(r"[;,/]", c)[0].strip()
    if not c or not state_name:
        return None
    bare = SUFFIX_RE.sub("", c).strip()
    for form in sorted(_variants(bare)):
        for key in (f"{form}|{state_name}", f"{form} county|{state_name}"):
            if key in lookup:
                return lookup[key]
    return None


def counties_in_state(lookup, state_name):
    """Bare county names the lookup knows for one state."""
    out = set()
    tail = "|" + state_name
    for k in lookup:
        if k.endswith(tail):
            out.add(SUFFIX_RE.sub("", k[: -len(tail)]).strip())
    return {x for x in out if x}


def lookup_nearmatch(county, state, lookup):
    """One close county name in the same state, or nothing. Low confidence."""
    state_name = state_name_of(state)
    c = (county or "").strip().lower()
    c = re.split(r"[;,/]", c)[0].strip()
    bare = SUFFIX_RE.sub("", c).strip()
    if not bare or not state_name:
        return None, None
    pool = sorted(counties_in_state(lookup, state_name))
    hits = difflib.get_close_matches(bare, pool, n=3, cutoff=0.8)
    if len(hits) != 1:
        return None, None
    name = hits[0]
    for key in (f"{name}|{state_name}", f"{name} county|{state_name}"):
        if key in lookup:
            return lookup[key], name
    return None, None


COUNTY_IN_TEXT_RE = re.compile(
    r"\b([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){0,2})\s+"
    r"(County|Parish|Borough)\b")


def name_extract(texts, state, lookup):
    """A county named in the project name or description, validated against the
    lookup for that state so an arbitrary capitalized phrase cannot pass."""
    state_name = state_name_of(state)
    if not state_name:
        return None, None
    for t in texts:
        for m in COUNTY_IN_TEXT_RE.finditer(t or ""):
            words = m.group(1).split()
            # The capture can pick up a leading company or product name, as in
            # "Microsoft Catawba County". Try the shortest tail first so the
            # county name is preferred over the phrase around it.
            for n in range(1, len(words) + 1):
                cand = " ".join(words[-n:]).strip()
                fips = (lookup_exact(cand, state, lookup)
                        or lookup_normalized(cand, state, lookup))
                if fips:
                    return fips, cand
    return None, None


# ---------------------------------------------------------------------------
# Census Geocoding Services API
# ---------------------------------------------------------------------------

def _get_json(url, opener=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _county_layer(geographies):
    """The county layer's name varies by vintage, so match on the shape rather
    than a fixed key."""
    if not isinstance(geographies, dict):
        return []
    for key in ("Counties", "County", "2020 Census Blocks"):
        if key in geographies and isinstance(geographies[key], list):
            if key.startswith("Count"):
                return geographies[key]
    for k, v in geographies.items():
        if "count" in k.lower() and isinstance(v, list):
            return v
    return []


def _geoid_from(entries):
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        g = str(e.get("GEOID") or "").strip()
        if len(g) == 5 and g.isdigit():
            return g, str(e.get("NAME") or "").strip()
        st, co = str(e.get("STATE") or ""), str(e.get("COUNTY") or "")
        if st and co:
            return (st.zfill(2) + co.zfill(3)), str(e.get("NAME") or "").strip()
    return None, ""


def census_reverse(lat, lon, opener=None):
    """Coordinates to current county FIPS. Returns (fips, name) or (None, '')."""
    q = urllib.parse.urlencode({
        "x": f"{float(lon):.6f}", "y": f"{float(lat):.6f}",
        "benchmark": BENCHMARK, "vintage": VINTAGE,
        "layers": "Counties", "format": "json",
    })
    try:
        data = _get_json(f"{GEOCODER}/geographies/coordinates?{q}", opener)
    except Exception:
        return None, ""
    geos = (data.get("result") or {}).get("geographies") or {}
    return _geoid_from(_county_layer(geos))


def census_forward(address, opener=None):
    """One-line address to (fips, name, lat, lon). The path that makes future
    address-level ingest possible."""
    q = urllib.parse.urlencode({
        "address": address, "benchmark": BENCHMARK, "vintage": VINTAGE,
        "layers": "Counties", "format": "json",
    })
    try:
        data = _get_json(f"{GEOCODER}/geographies/onelineaddress?{q}", opener)
    except Exception:
        return None, "", None, None
    matches = (data.get("result") or {}).get("addressMatches") or []
    if not matches:
        return None, "", None, None
    m = matches[0]
    fips, name = _geoid_from(_county_layer(m.get("geographies") or {}))
    coords = m.get("coordinates") or {}
    return fips, name, _f(coords.get("y")), _f(coords.get("x"))


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHE_FIELDS = ["request_key", "kind", "fips", "county_name", "lat", "lon", "status"]


def load_cache():
    return {r["request_key"]: r for r in load_csv(CACHE_CSV) if r.get("request_key")}


def append_cache(records):
    if not records:
        return
    os.makedirs(os.path.dirname(CACHE_CSV), exist_ok=True)
    new = not os.path.exists(CACHE_CSV)
    with open(CACHE_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CACHE_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in records:
            w.writerow({k: r.get(k, "") for k in CACHE_FIELDS})


# ---------------------------------------------------------------------------
# Resolution over the proposals file
# ---------------------------------------------------------------------------

OVERLAY_FIELDS = ["project_id", "name", "state", "county_original",
                  "county_resolved", "fips", "method", "confidence", "note"]


def resolve_all(proposals, lookup, valid_fips, allow_network=True, limit=None,
                opener=None):
    cache = load_cache()
    fresh = []
    applied, review, unresolved = [], [], []
    calls = 0
    stats = Counter()

    for p in proposals:
        pid = "prj_" + str(p.get("id") or "").strip()
        state = (p.get("state") or "").strip()
        county = (p.get("counties") or "").strip()
        name = (p.get("name") or "").strip()
        coords = usable_coords(p.get("lat"), p.get("lon"))

        method = fips = None
        resolved_name = ""
        note = ""

        f = lookup_exact(county, state, lookup)
        if f and f in valid_fips:
            stats["already_resolved"] += 1
            continue
        if f and f not in valid_fips:
            note = (f"lookup returned {f}, which is not in the current county "
                    f"universe; treated as unresolved")
            stats["retired_code"] += 1

        f2 = lookup_normalized(county, state, lookup)
        if f2 and f2 in valid_fips:
            method, fips = "lookup_normalized", f2

        if fips is None and coords and allow_network:
            key = f"rev:{coords[0]:.5f},{coords[1]:.5f}"
            if key in cache:
                c = cache[key]
                if c.get("fips") and c["fips"] in valid_fips:
                    method, fips, resolved_name = "census_reverse", c["fips"], c.get("county_name", "")
            elif limit is None or calls < limit:
                rf, rname = census_reverse(coords[0], coords[1], opener)
                calls += 1
                fresh.append({"request_key": key, "kind": "reverse",
                              "fips": rf or "", "county_name": rname,
                              "lat": coords[0], "lon": coords[1],
                              "status": "ok" if rf else "no_match"})
                if rf and rf in valid_fips:
                    method, fips, resolved_name = "census_reverse", rf, rname

        if fips is None:
            f4, cand = name_extract([name, p.get("info")], state, lookup)
            if f4 and f4 in valid_fips:
                method, fips, resolved_name = "name_extract", f4, cand

        if fips is None:
            addr = (p.get("address") or "").strip()
            if addr and allow_network and (limit is None or calls < limit):
                one = f"{addr}, {state}"
                key = "fwd:" + one.lower()
                if key in cache:
                    c = cache[key]
                    if c.get("fips") and c["fips"] in valid_fips:
                        method, fips = "census_forward", c["fips"]
                        resolved_name = c.get("county_name", "")
                else:
                    ff, fname, flat, flon = census_forward(one, opener)
                    calls += 1
                    fresh.append({"request_key": key, "kind": "forward",
                                  "fips": ff or "", "county_name": fname,
                                  "lat": flat if flat is not None else "",
                                  "lon": flon if flon is not None else "",
                                  "status": "ok" if ff else "no_match"})
                    if ff and ff in valid_fips:
                        method, fips, resolved_name = "census_forward", ff, fname

        near_fips, near_name = (None, None)
        if fips is None:
            near_fips, near_name = lookup_nearmatch(county, state, lookup)

        rec = {"project_id": pid, "name": name, "state": state,
               "county_original": county, "county_resolved": resolved_name or "",
               "fips": fips or "", "method": method or "", "note": note}

        if fips:
            rec["confidence"] = CONFIDENCE.get(method, "medium")
            # Prefer the aggregate's canonical name so the overlay always
            # spells a county the same way the county layer does.
            rec["county_resolved"] = valid_fips.get(fips) or resolved_name
            applied.append(rec)
            stats[method] += 1
        elif near_fips and near_fips in valid_fips:
            rec.update({"fips": near_fips, "method": "lookup_nearmatch",
                        "confidence": "low",
                        "county_resolved": valid_fips.get(near_fips, near_name),
                        "note": (note + "; " if note else "") +
                                f"closest county name in {state} is "
                                f"'{near_name}'. Confirm before applying."})
            review.append(rec)
            stats["nearmatch_review"] += 1
        else:
            rec["confidence"] = ""
            if coords and not allow_network:
                why = ("coordinates are present; a reverse geocode resolves this "
                       "row, so run without --offline")
            elif coords:
                why = "reverse geocode returned no county for these coordinates"
            else:
                why = ("no county, no usable coordinates, and no county named "
                       "in the text")
            rec["note"] = (note + "; " if note else "") + why
            unresolved.append(rec)
            stats["unresolved"] += 1

    append_cache(fresh)
    return applied, review, unresolved, stats, calls


def write_rows(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OVERLAY_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in OVERLAY_FIELDS})
    return path


def render_report(applied, review, unresolved, stats, n_total, offline):
    L = ["# County FIPS resolution", "",
         f"Proposals examined: {n_total}",
         f"Already resolved by the existing lookup: {stats.get('already_resolved', 0)}",
         f"Applied by this pass: {len(applied)}",
         f"Held for confirmation: {len(review)}",
         f"Still unresolved: {len(unresolved)}",
         "",
         "Network steps were skipped." if offline else
         "Network steps were allowed.", "",
         "## Method", "", "| method | confidence | resolved |",
         "| :-- | :-- | --: |"]
    for m in ("lookup_normalized", "census_reverse", "name_extract",
              "census_forward"):
        if stats.get(m):
            L.append(f"| `{m}` | {CONFIDENCE[m]} | {stats[m]} |")
    if stats.get("nearmatch_review"):
        L.append(f"| `lookup_nearmatch` | low | {stats['nearmatch_review']} "
                 f"(not applied) |")
    L += ["",
          f"Retired codes caught: {stats.get('retired_code', 0)}. A retired "
          f"code is a FIPS the lookup still returns that no longer exists in "
          f"the county universe, so it joins to nothing while looking valid. "
          f"Connecticut is the live case: it replaced counties with planning "
          f"regions in 2022.", ""]
    if applied:
        L += ["## Applied", "",
              "| project | state | recorded county | resolved | fips | method |",
              "| :-- | :-- | :-- | :-- | :-- | :-- |"]
        for r in applied:
            L.append(f"| {r['project_id']} | {r['state']} | "
                     f"{r['county_original'] or '(blank)'} | "
                     f"{r['county_resolved']} | {r['fips']} | `{r['method']}` |")
        L.append("")
    if review:
        L += ["## Held for confirmation", "",
              "Close name matches only. Each needs a person to confirm before "
              "it is applied.", "",
              "| project | state | recorded county | closest match | fips |",
              "| :-- | :-- | :-- | :-- | :-- |"]
        for r in review:
            L.append(f"| {r['project_id']} | {r['state']} | "
                     f"{r['county_original'] or '(blank)'} | "
                     f"{r['county_resolved']} | {r['fips']} |")
        L.append("")
    if unresolved:
        L += ["## Still unresolved", "",
              "| project | state | recorded county | why |",
              "| :-- | :-- | :-- | :-- |"]
        for r in unresolved:
            L.append(f"| {r['project_id']} | {r['state']} | "
                     f"{r['county_original'] or '(blank)'} | {r['note']} |")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            ok = False

    lookup = {
        "marion|indiana": "18097", "marion county|indiana": "18097",
        "laporte|indiana": "18091", "laporte county|indiana": "18091",
        "morgan|indiana": "18109", "catawba|north carolina": "37035",
        "catawba county|north carolina": "37035",
        "wake|north carolina": "37183",
        "st. louis|missouri": "29189", "hartford|connecticut": "09003",
        "capitol planning region|connecticut": "09110",
    }
    valid = {"18097": "Marion County, Indiana", "18091": "LaPorte County, Indiana",
             "18109": "Morgan County, Indiana", "37035": "Catawba County, North Carolina",
             "37183": "Wake County, North Carolina", "29189": "St. Louis County, Missouri",
             "09110": "Capitol Planning Region, Connecticut"}

    check("exact lookup still works",
          lookup_exact("Marion County", "IN", lookup) == "18097")
    check("spacing variant resolves",
          lookup_normalized("La Porte County", "Indiana", lookup) == "18091")
    check("state name or abbrev both accepted",
          lookup_exact("Marion", "Indiana", lookup) ==
          lookup_exact("Marion", "IN", lookup))
    check("retired code is rejected as invalid",
          lookup_exact("Hartford County", "CT", lookup) == "09003"
          and "09003" not in valid)
    check("typo does not resolve exactly",
          lookup_exact("Marlon County", "IN", lookup) is None)
    nf, nn = lookup_nearmatch("Marlon County", "IN", lookup)
    check("typo reaches near-match with the right county",
          nf == "18097" and nn == "marion")
    check("near-match refuses when ambiguous",
          lookup_nearmatch("Xyzzy", "IN", lookup) == (None, None))
    check("county named in the project name is extracted",
          name_extract(["Microsoft Catawba County Campus Cluster"], "NC",
                       lookup)[0] == "37035")
    check("capitalized phrase that is not a county is refused",
          name_extract(["Northern Gateway County Line Project"], "NC",
                       lookup)[0] is None)

    check("0,0 coordinates are not a location", usable_coords(0.0, 0.0) is None)
    check("real coordinates pass", usable_coords(35.65, -78.95) is not None)
    check("blank coordinates are not a location", usable_coords("", "") is None)

    class _Resp:
        def __init__(self, payload):
            self._b = json.dumps(payload).encode()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    rev_payload = {"result": {"geographies": {"Counties": [
        {"GEOID": "37183", "NAME": "Wake", "STATE": "37", "COUNTY": "183"}]}}}
    ct_payload = {"result": {"geographies": {"Counties": [
        {"GEOID": "09110", "NAME": "Capitol Planning Region"}]}}}
    fwd_payload = {"result": {"addressMatches": [
        {"coordinates": {"x": -77.5, "y": 39.1},
         "geographies": {"Counties": [{"GEOID": "51107", "NAME": "Loudoun"}]}}]}}

    check("reverse geocode parses",
          census_reverse(35.65, -78.95, lambda r, timeout=None: _Resp(rev_payload))
          == ("37183", "Wake"))
    check("reverse geocode returns a CT planning region",
          census_reverse(41.88, -72.74, lambda r, timeout=None: _Resp(ct_payload))[0]
          == "09110")
    f, n, la, lo = census_forward("100 Main St, VA",
                                  lambda r, timeout=None: _Resp(fwd_payload))
    check("forward geocode parses", f == "51107" and la == 39.1 and lo == -77.5)

    def boom(req, timeout=None):
        raise OSError("no network")

    check("network failure is contained",
          census_reverse(35.0, -78.0, boom) == (None, ""))
    check("layer key variation is tolerated",
          _geoid_from(_county_layer({"County": [{"GEOID": "18097"}]}))[0] == "18097")
    check("state/county parts assemble a geoid",
          _geoid_from([{"STATE": "9", "COUNTY": "110"}])[0] == "09110")

    props = [
        {"id": "31", "name": "Metrobloks", "state": "Indiana",
         "counties": "Marlon County", "lat": "39.80", "lon": "-86.10"},
        {"id": "39", "name": "Project Maize", "state": "Indiana",
         "counties": "La Porte County", "lat": "41.71", "lon": "-86.84"},
        {"id": "149", "name": "Microsoft Catawba County Campus Cluster",
         "state": "North Carolina", "counties": "", "lat": "0.0", "lon": "0.0"},
        {"id": "99", "name": "Already Fine", "state": "Indiana",
         "counties": "Marion County", "lat": "39.8", "lon": "-86.1"},
    ]
    applied, review, unresolved, stats, calls = resolve_all(
        props, lookup, valid, allow_network=False)
    ids = {r["project_id"]: r for r in applied}
    check("normalized spacing is applied offline",
          ids.get("prj_39", {}).get("fips") == "18091")
    check("name extraction is applied offline",
          ids.get("prj_149", {}).get("fips") == "37035")
    check("already-resolved rows are left alone", stats["already_resolved"] == 1)
    check("typo goes to review, not to the overlay",
          [r["project_id"] for r in review] == ["prj_31"])
    check("no network calls in offline mode", calls == 0)

    md = render_report(applied, review, unresolved, stats, len(props), True)
    check("report has no scorekeeping language", not LEAK_RE.search(md))
    check("report has no em-dash", "\u2014" not in md)

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="skip every step that needs the Census API")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap new API calls this run")
    ap.add_argument("--address", default=None,
                    help="geocode one address and print the result")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.address:
        fips, name, lat, lon = census_forward(a.address)
        if not fips:
            print(f"census_geocode: no match for {a.address!r}")
            return 1
        print(f"census_geocode: {a.address!r} -> {name} ({fips}) at {lat}, {lon}")
        return 0

    proposals = load_csv(PROPOSALS_CSV)
    if not proposals:
        print(f"census_geocode: no proposals read from {PROPOSALS_CSV}")
        return 1
    lookup = load_lookup()
    valid = load_valid_fips()
    if not valid:
        print(f"census_geocode: {COUNTY_AGG_CSV} is required and was not found. "
              f"Run county_aggregator.py first.")
        return 1

    applied, review, unresolved, stats, calls = resolve_all(
        proposals, lookup, valid, allow_network=not a.offline, limit=a.limit)

    write_rows(OVERLAY_CSV, applied)
    write_rows(REVIEW_CSV, review)
    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    open(REPORT_MD, "w", encoding="utf-8").write(
        render_report(applied, review, unresolved, stats, len(proposals), a.offline))

    print(f"census_geocode: {len(proposals)} proposals, "
          f"{stats.get('already_resolved', 0)} already resolved")
    for m in ("lookup_normalized", "census_reverse", "name_extract",
              "census_forward"):
        if stats.get(m):
            print(f"  {m:<20} {stats[m]}")
    if stats.get("retired_code"):
        print(f"  retired codes caught {stats['retired_code']}")
    print(f"  applied              {len(applied)}")
    print(f"  held for confirmation{len(review):>2}")
    print(f"  unresolved           {len(unresolved)}")
    print(f"  API calls this run   {calls}")
    print(f"  wrote {OVERLAY_CSV}")
    print(f"  wrote {REVIEW_CSV}")
    print(f"  wrote {REPORT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
