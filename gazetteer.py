#!/usr/bin/env python3
"""
gazetteer.py — place-name to county lookup, so a headline that names a town
resolves to a county.

Why this exists

`signal_harvest.locate()` resolved geography by matching headlines against
county names and nothing else. That is a regionally skewed filter rather than a
neutral one, because the jurisdiction that acts on a data center differs by
region. In Virginia and Ohio the county is the acting body and the headline
says so — "Loudoun County", "Prince William County". West of the Rockies the
acting body is a city or a town, and the headline says Tucson, Chandler,
Prineville, Hermiston, Quincy, Eagle Mountain, Cheyenne, Reno. None of those
carried a county name, so none of them resolved, and the row landed in the
worklist with `location_confidence = none` where it could not be joined to a
county without a person doing it by hand. At the time this module was written
164 of the 227 rows in data/signal_candidates.csv were in that state.

Sources, both additive and both stamped per row

  census_cousub   Census Gazetteer national county-subdivision file. Chosen
                  over the *place* file for one specific reason: a county
                  subdivision's GEOID is state + county + subdivision, so the
                  county FIPS is already in the file. The place file carries no
                  county at all, and getting one would need either a
                  relationship file, a point-in-polygon pass against county
                  polygons, or ~32,000 reverse-geocoder calls. None of those is
                  worth it when a file that already answers the question
                  exists. The coverage this buys is also the coverage wanted:
                  minor civil divisions carry the township names the Midwest
                  and New England headlines use, and in the twenty-odd states
                  that have no MCDs the Census county divisions are named for
                  their principal community, which is how Tucson, Prineville
                  and Quincy get in.

  repo_records    (City, County, State) triples already recorded and verified
                  in master_opposition_clean.csv, and (towns, counties, state)
                  in data/proposals.csv. Needs no network, so it is what ships
                  and what the selftest runs against; the Census build layers
                  on top of it in CI.

Ambiguity is the whole problem, and the rules are conservative

A wrong county is worse than no county: it moves an event into a county that
did not have one, and the county layer is the frame every model and every map
reads. So resolution refuses far more often than it guesses.

  * A name that occurs in more than one state resolves only when the headline
    also names one of those states. Springfield never resolves on its own.
  * A name that occurs in more than one county inside the resolved state does
    not resolve to a county. It resolves to the state, or to nothing.
  * Names shorter than four characters are never matched, and names that
    collide with a state name, a month, or a common English word are dropped
    at build time.

Anti-redundancy

The build is deterministic and idempotent. Rows are keyed on
(place_norm, state, fips) and deduplicated on that key, so a place carried by
both sources appears exactly once with both provenances joined; re-running the
build over an unchanged input produces a byte-identical file, and re-running it
after a Census refresh changes only the rows that actually moved.

Usage
  python gazetteer.py --build                # census + repo records (network)
  python gazetteer.py --build --offline      # repo records only, no network
  python gazetteer.py --lookup Tucson --state AZ
  python gazetteer.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

OUT_CSV = os.path.join(DATA, "place_gazetteer.csv")
COUNTY_AGG_CSV = os.path.join(DATA, "county_aggregate.csv")
OPPOSITION_CSV = os.path.join(HERE, "master_opposition_clean.csv")
PROPOSALS_CSV = os.path.join(DATA, "proposals.csv")
MANIFEST_JSON = os.path.join(DATA, "place_gazetteer_manifest.json")

# Census Gazetteer, national county subdivisions. Pinned to a vintage rather
# than "latest" so a rebuild is reproducible and a vintage change is a visible
# edit rather than a silent drift in the county frame.
CENSUS_VINTAGE = "2024"
CENSUS_COUSUB_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    f"{CENSUS_VINTAGE}_Gazetteer/{CENSUS_VINTAGE}_Gaz_cousubs_national.zip"
)

USER_AGENT = "hawthorn-dc-tracker/1.0 (place gazetteer build; contact repo owner)"

FIELDS = ["place", "place_norm", "state", "county", "fips", "source"]

# Legal/statistical suffixes the Census appends to a subdivision name. Stripped
# so "Groton town", "Butler township" and "Tucson CCD" all key on the name a
# headline would actually print.
LSAD_SUFFIXES = (
    "ccd", "ccd (historical)", "city", "town", "township", "village",
    "borough", "plantation", "gore", "grant", "location", "purchase",
    "precinct", "district", "reservation", "charter township",
    "unorganized territory", "county subdivision not defined",
)

# Names that are never distinctive enough to carry a county on their own, even
# when the index happens to hold exactly one of them. Every one of these is a
# word that appears in headlines for reasons unrelated to place.
STOP_PLACES = {
    "washington", "york", "union", "center", "centre", "liberty", "franklin",
    "jackson", "jefferson", "madison", "monroe", "clay", "grant", "lincoln",
    "marion", "clinton", "greene", "troy", "salem", "springfield", "fairview",
    "riverside", "oak", "hill", "hills", "lake", "lakes", "river", "valley",
    "summit", "eagle", "aurora", "florida", "nevada", "oregon", "wyoming",
    "delaware", "indiana", "louisiana", "michigan", "ohio", "utah", "kansas",
    "california", "colorado", "texas", "virginia", "georgia", "montana",
    "north", "south", "east", "west", "middle", "new", "old", "mount",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "energy", "power", "water", "school", "district", "county", "state",
    "united states", "america", "national", "federal", "capital", "city",
    # Real incorporated places whose names are ordinary English. Each of these
    # produced a false county attribution on the live worklist before it was
    # listed: "Industry warns of blackouts" is not about Industry, California,
    # and "The Rapid Buildout of Data Centers" is not about Rapid City.
    "industry", "rapid", "commerce", "enterprise", "normal", "surprise",
    "hope", "home", "blue", "gold", "silver", "sun", "star", "general",
    "standard", "superior", "sterling", "service", "security", "advance",
    "progress", "reliance", "hazard", "boring", "protection", "economy",
    "bland", "climax", "index", "loyal", "rich", "sharp", "speed", "peace",
}

# A match preceded by one of these is part of a longer name the index does not
# hold: "El Reno" is not Reno, "San Marcos" is not Marcos. Cheap, closed, and
# explainable, where a general "is the previous word capitalized" test is not —
# headlines are frequently title-cased, so capitalization carries no signal.
NAME_PREFIX_PARTICLES = {
    "el", "la", "las", "los", "san", "santa", "sao", "ste", "st", "saint",
    "new", "north", "south", "east", "west", "upper", "lower", "old", "big",
    "little", "fort", "ft", "mount", "mt", "port", "lake", "cape", "bay",
    "great", "grand", "green", "white", "black", "red", "blue", "long",
}

# A match followed by one of these is part of an organization or masthead, not
# a dateline: "Buckeye Country 105.5" is a radio station.
ENTITY_SUFFIX_WORDS = {
    "country", "radio", "news", "times", "herald", "gazette", "journal",
    "tribune", "post", "press", "magazine", "network", "media", "broadcasting",
    "tv", "fm", "am", "channel", "podcast", "review", "report", "digest",
    "bank", "insurance", "airlines", "motors", "brands", "holdings",
}

MIN_PLACE_LEN = 4


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------

def norm_place(name: str) -> str:
    """Lowercase, punctuation folded, legal suffix removed. Used for matching
    only, never for display — the `place` column keeps the printable form."""
    t = (name or "").strip().lower()
    t = re.sub(r"\s*\(.*?\)\s*", " ", t)          # "Tucson CCD (part)"
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for suf in sorted(LSAD_SUFFIXES, key=len, reverse=True):
        if t.endswith(" " + suf):
            t = t[: -(len(suf) + 1)].strip()
            break
    # "City of East Wenatchee", "Town of Groton" — the repo records carry these.
    t = re.sub(r"^(city|town|village|borough|township) of ", "", t)
    return re.sub(r"\s+", " ", t).strip()


def usable_place(name_norm: str) -> bool:
    """A name distinctive enough to be worth indexing at all."""
    if len(name_norm) < MIN_PLACE_LEN:
        return False
    if name_norm in STOP_PLACES:
        return False
    if name_norm.endswith(" county") or name_norm.endswith(" parish"):
        return False       # county names are the other index's job
    return True


def norm_county(name: str) -> str:
    """Bare county name, matching the convention in county_aggregate.csv."""
    raw = (name or "").split(",")[0].strip()
    return re.sub(
        r"\s+(County|Parish|Borough|Census Area|Municipality|City and Borough)$",
        "", raw, flags=re.IGNORECASE).strip()


# ---------------------------------------------------------------------------
# repo-derived source
# ---------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def county_fips_index() -> dict[tuple[str, str], str]:
    """(county_norm_lower, state_abbrev) -> fips, from the county frame."""
    idx = {}
    for r in read_csv(COUNTY_AGG_CSV):
        name = norm_county(r.get("county_name") or "")
        st = (r.get("state") or "").strip().upper()
        fips = (r.get("fips") or "").strip()
        if name and st and fips:
            idx[(name.lower(), st)] = fips
    return idx


def rows_from_repo_records(fips_idx: dict) -> list[dict]:
    """(City, County, State) and (towns, counties, state) triples we already
    hold. These are records a person entered or verified, so the pairing is
    evidence rather than inference — but only where the county resolves to a
    FIPS in the current frame, because a place pointing at a county the frame
    does not carry cannot be joined to anything downstream."""
    out = []

    def add(place, county, state, source):
        pn = norm_place(place)
        if not usable_place(pn):
            return
        st = (state or "").strip().upper()
        if len(st) != 2:
            return
        cn = norm_county(county)
        fips = fips_idx.get((cn.lower(), st))
        if not cn or not fips:
            return
        out.append({"place": (place or "").strip(), "place_norm": pn,
                    "state": st, "county": cn, "fips": fips, "source": source})

    for r in read_csv(OPPOSITION_CSV):
        add(r.get("City"), r.get("County"), r.get("State"), "repo_records")

    for r in read_csv(PROPOSALS_CSV):
        # proposals.csv carries the full state name and can list several towns
        # and counties. Only the unambiguous one-town/one-county rows are used:
        # pairing the second town with the first county is exactly the kind of
        # guess this module refuses to make.
        towns = [t.strip() for t in (r.get("towns") or "").split(",") if t.strip()]
        counties = [c.strip() for c in (r.get("counties") or "").split(",") if c.strip()]
        state_full = (r.get("state") or "").strip()
        st = STATE_BY_NAME.get(state_full.lower(), "")
        if len(towns) == 1 and len(counties) == 1 and st:
            add(towns[0], counties[0], st, "repo_records")

    return out


# ---------------------------------------------------------------------------
# Census source
# ---------------------------------------------------------------------------

STATE_BY_NAME = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}


def fetch_census_cousubs(timeout: int = 120) -> list[dict]:
    """National county-subdivision gazetteer. Returns raw parsed rows.

    Runs in GitHub Actions, where egress is unrestricted. Any failure is the
    caller's to handle: a Census outage must degrade the build to the repo
    records rather than leaving no gazetteer at all, because a missing file
    silently returns signal_harvest to county-only matching.
    """
    req = urllib.request.Request(CENSUS_COUSUB_URL,
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        blob = resp.read()
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".csv"))]
    if not names:
        raise RuntimeError("census gazetteer zip carried no text member")
    with zf.open(names[0]) as fh:
        text = io.TextIOWrapper(fh, encoding="latin-1")
        return list(csv.DictReader(text, delimiter="\t"))


def rows_from_census(raw: list[dict], fips_idx: dict) -> list[dict]:
    """Census rows -> gazetteer rows. GEOID is state(2) + county(3) +
    subdivision(5), so the county FIPS is the first five characters and no
    geocoding is required."""
    # county fips -> printable county name, from the frame we already carry
    name_by_fips = {v: k[0] for k, v in fips_idx.items()}
    out = []
    for r in raw:
        # Column names in the Census file carry stray whitespace often enough
        # that indexing them directly is a coin flip; normalize the keys once.
        rec = {(k or "").strip().upper(): (v or "").strip()
               for k, v in r.items() if k}
        geoid = rec.get("GEOID", "")
        name = rec.get("NAME", "")
        st = rec.get("USPS", "").upper()
        if len(geoid) < 5 or not name or len(st) != 2:
            continue
        fips = geoid[:5]
        pn = norm_place(name)
        if not usable_place(pn):
            continue
        county = name_by_fips.get(fips, "")
        if not county:
            continue          # a subdivision in a county outside the frame
        out.append({"place": name, "place_norm": pn, "state": st,
                    "county": county.title(), "fips": fips,
                    "source": "census_cousub"})
    return out


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def merge(*row_sets: list[dict]) -> list[dict]:
    """Deduplicate on (place_norm, state, fips) and join provenance.

    This is the anti-redundancy block for the build. Both sources describe the
    same world and overlap heavily by design — the repo records exist to cover
    what the Census file cannot see, not to restate it — so without a merge key
    a rebuild would double every shared row and inflate the ambiguity counts
    that the resolution rules are computed from. Sorting the output makes the
    file byte-identical across runs on unchanged input.
    """
    by_key: dict[tuple[str, str, str], dict] = {}
    for rows in row_sets:
        for r in rows:
            key = (r["place_norm"], r["state"], r["fips"])
            cur = by_key.get(key)
            if cur is None:
                by_key[key] = dict(r)
                continue
            sources = set(cur["source"].split("+")) | {r["source"]}
            cur["source"] = "+".join(sorted(sources))
            # Prefer the Census spelling for display; it is the published form.
            if r["source"] == "census_cousub":
                cur["place"] = r["place"]
    return sorted(by_key.values(),
                  key=lambda r: (r["place_norm"], r["state"], r["fips"]))


def write_gazetteer(rows: list[dict], path: str = OUT_CSV) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def build(offline: bool = False) -> tuple[list[dict], dict]:
    fips_idx = county_fips_index()
    repo_rows = rows_from_repo_records(fips_idx)
    census_rows: list[dict] = []
    note = "offline: repo records only"
    if not offline:
        try:
            census_rows = rows_from_census(fetch_census_cousubs(), fips_idx)
            note = f"census {CENSUS_VINTAGE} county subdivisions + repo records"
        except Exception as exc:                    # network, zip, schema
            note = (f"census fetch failed ({exc.__class__.__name__}: {exc}); "
                    f"built from repo records only")
    rows = merge(census_rows, repo_rows)
    manifest = {
        # National only when the Census file actually landed. resolve() gates
        # its uniqueness inference on this, so it must never be optimistic:
        # a degraded build that still claimed national coverage would hand the
        # resolver exactly the false confidence this flag exists to withhold.
        "national": bool(census_rows),
        "vintage": CENSUS_VINTAGE if census_rows else None,
        "census_url": CENSUS_COUSUB_URL if census_rows else None,
        "rows": len(rows),
        "rows_census": len(census_rows),
        "rows_repo_records": len(repo_rows),
        "distinct_places": len({r["place_norm"] for r in rows}),
        "note": note,
    }
    return rows, manifest


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def load_index(path: str = OUT_CSV) -> dict[str, list[tuple[str, str, str]]]:
    """place_norm -> [(county, state, fips)]. Empty when the file is absent,
    which is the no-regression path: a caller with no gazetteer behaves exactly
    as it did before this module existed."""
    idx: dict[str, list[tuple[str, str, str]]] = {}
    for r in read_csv(path):
        pn = (r.get("place_norm") or "").strip()
        if not pn:
            continue
        idx.setdefault(pn, []).append(((r.get("county") or "").strip(),
                                       (r.get("state") or "").strip().upper(),
                                       (r.get("fips") or "").strip()))
    return idx


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def _in_longer_name(tokens: list[str], start: int, span: int) -> bool:
    """True when the matched span is part of a longer proper name.

    Two closed lists rather than a capitalization test, because headlines are
    routinely title-cased and capitalization therefore carries no signal at
    all. Both cases below came off the live worklist: "El Reno" resolved to
    Reno, Nevada, and "Buckeye Country 105.5" resolved to Maricopa County.
    """
    before = tokens[start - 1] if start > 0 else ""
    after = tokens[start + span] if start + span < len(tokens) else ""
    return before in NAME_PREFIX_PARTICLES or after in ENTITY_SUFFIX_WORDS


def resolve(text: str, idx: dict, named_states: list[str] | None = None,
            national: bool = False):
    """Best place match in `text`. Returns (county, state, fips, confidence)
    or None.

    Longest name first, so "Eagle Mountain" is tried before "Eagle" and a
    two-word town is not shadowed by one of its own words.

    `national` is the honest guard on the uniqueness inference. "This name
    occurs once in the index, so it is unambiguous" is only sound when the
    index covers the country. Built from repo records alone the index is
    sparse, not national: it holds exactly one Portland, so a headline reading
    "Portland moves to keep data center deals out of the shadows" resolved to
    Chautauqua County, New York, when it is about Oregon. With national=False
    a place resolves only when the headline also names the state, which needs
    no uniqueness inference at all. The Census build sets the flag and unlocks
    the rest.
    """
    named_states = [s for s in (named_states or []) if s]
    tokens = _tokens(text)
    if not tokens:
        return None
    joined = " " + " ".join(tokens) + " "

    for pn in sorted(idx, key=len, reverse=True):
        if f" {pn} " not in joined:
            continue
        parts = pn.split()
        starts = [i for i in range(len(tokens) - len(parts) + 1)
                  if tokens[i:i + len(parts)] == parts]
        if all(_in_longer_name(tokens, i, len(parts)) for i in starts):
            continue
        options = idx[pn]
        states = {st for _, st, _ in options}

        if named_states:
            scoped = [o for o in options if o[1] in named_states]
            if not scoped:
                continue          # the name is real but not in the named state
            counties = {(c, s, f) for c, s, f in scoped}
            if len({f for _, _, f in counties}) == 1:
                c, s, f = sorted(counties)[0]
                return (c, s, f, "high")
            # One state, several counties: the state is still known.
            if len({s for _, s, _ in counties}) == 1:
                return ("", sorted(counties)[0][1], "", "state_only")
            continue

        if not national:
            continue              # uniqueness is not knowable from this index
        if len(states) > 1:
            continue              # Springfield, with no state named
        fipses = {f for _, _, f in options}
        if len(fipses) == 1:
            c, s, f = sorted(options)[0]
            return (c, s, f, "medium")
        return ("", sorted(states)[0], "", "state_only")
    return None


def index_is_national(path: str = MANIFEST_JSON) -> bool:
    """Whether the built gazetteer covers the country, from the build manifest.

    Absent or unreadable manifest reads as not national, which is the
    conservative direction: it withholds the uniqueness inference rather than
    granting it on a file whose provenance nothing recorded.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return bool((json.load(fh) or {}).get("national"))
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    expect(norm_place("Tucson CCD") == "tucson", "CCD suffix stripped")
    expect(norm_place("Groton town") == "groton", "town suffix stripped")
    expect(norm_place("Butler township") == "butler", "township suffix stripped")
    expect(norm_place("City of East Wenatchee") == "east wenatchee",
           "'City of' prefix stripped")
    expect(norm_place("St. Mary's") == "st mary s", "punctuation folded")
    expect(norm_place("Tucson CCD (part)") == "tucson", "parenthetical dropped")

    expect(not usable_place("ada"), "names under four characters are not indexed")
    expect(not usable_place("springfield"), "stop-list name refused")
    expect(not usable_place("loudoun county"), "county names left to the county index")
    expect(usable_place("tucson"), "a distinctive name is indexed")

    idx = {
        "tucson": [("Pima", "AZ", "04019")],
        "aurora": [("Adams", "CO", "08001"), ("Kane", "IL", "17089")],
        "quincy": [("Grant", "WA", "53025")],
        "eagle mountain": [("Utah", "UT", "49049")],
        "eagle": [("Ada", "ID", "16001")],
        "portland": [("Multnomah", "OR", "41051"), ("Washington", "OR", "41067")],
    }

    r = resolve("Tucson rejects Project Blue data center", idx, national=True)
    expect(r == ("Pima", "AZ", "04019", "medium"),
           "a nationally unique town resolves with no state named")

    r = resolve("Tucson rejects Project Blue data center", idx)
    expect(r is None,
           "the same town refuses to resolve when the index is not national")

    r = resolve("Aurora weighs data center moratorium", idx, national=True)
    expect(r is None, "a town name in two states refuses to resolve")

    r = resolve("Aurora, Colorado weighs data center moratorium", idx,
                named_states=["CO"])
    expect(r == ("Adams", "CO", "08001", "high"),
           "the same name resolves once the state is named")

    r = resolve("Portland council hears data center plan", idx,
                named_states=["OR"])
    expect(r == ("", "OR", "", "state_only"),
           "a town spanning two counties resolves to the state, never a county")

    r = resolve("Eagle Mountain approves data center", idx, national=True)
    expect(r == ("Utah", "UT", "49049", "medium"),
           "the longer name takes precedence over a substring of itself")

    r = resolve("Nothing here names a place", idx, national=True)
    expect(r is None, "no match returns None rather than a guess")

    # Adjacency guards. Both of these resolved to a county on the live
    # worklist before the guards existed.
    reno = {"reno": [("Washoe", "NV", "32031")]}
    expect(resolve("El Reno data center water line leak", reno, national=True) is None,
           "a name-prefix particle blocks a match inside a longer name")
    expect(resolve("Reno council hears data center plan", reno, national=True)
           == ("Washoe", "NV", "32031", "medium"),
           "the same name still resolves when it stands alone")
    buckeye = {"buckeye": [("Maricopa", "AZ", "04013")]}
    expect(resolve("Moratorium passed by Chillicothe Council | Buckeye Country 105.5",
                   buckeye, national=True) is None,
           "an entity suffix blocks a masthead from reading as a dateline")

    r = resolve("Quincy data center hearing", idx, named_states=["OR"])
    expect(r is None, "a real name outside the named state does not resolve")

    a = [{"place": "Tucson CCD", "place_norm": "tucson", "state": "AZ",
          "county": "Pima", "fips": "04019", "source": "census_cousub"}]
    b = [{"place": "Tucson", "place_norm": "tucson", "state": "AZ",
          "county": "Pima", "fips": "04019", "source": "repo_records"}]
    m = merge(a, b)
    expect(len(m) == 1, "the same place from two sources merges to one row")
    expect(m[0]["source"] == "census_cousub+repo_records",
           "merged row carries both provenances")
    expect(m[0]["place"] == "Tucson CCD", "census spelling takes precedence for display")
    expect(merge(a, b) == merge(b, a),
           "merge is order-independent, so a rebuild is byte-stable")

    print("\nSELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="skip the Census fetch; build from repo records only")
    ap.add_argument("--lookup")
    ap.add_argument("--state", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.build:
        rows, manifest = build(offline=args.offline)
        write_gazetteer(rows)
        with open(MANIFEST_JSON, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"place gazetteer: {manifest['rows']} rows, "
              f"{manifest['distinct_places']} distinct names "
              f"({manifest['note']}) -> {os.path.relpath(OUT_CSV, HERE)}")
        return 0

    if args.lookup:
        idx = load_index()
        if not idx:
            print("no gazetteer built yet; run --build")
            return 1
        states = [args.state.upper()] if args.state else []
        hit = resolve(args.lookup, idx, named_states=states)
        print(hit if hit else "no confident match")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
