"""
schema_adapter.py

Maps the master_opposition.csv schema onto the field names and conventions the
QC gate reads, so the gate can run on the real data without renaming columns in
the source file. Call normalize_records() on the parsed CSV before run().

What it does
------------
1. Column aliases: Community Outcome -> Outcome, Summary -> Notes, lat/lon ->
   Latitude/Longitude, Company/Hyperscaler -> Operator, Megawatts -> Capacity,
   Project Name/Incident/Entity -> Name. Originals are preserved; canonical
   keys are added only when missing, so the clean export keeps every column.
2. State codes: two-letter (WA) expand to full names (Washington) so the
   capital and bounding-box checks resolve.
3. County names: a trailing " County"/"Parish"/"Borough" is stripped so capital
   -county comparisons match.

This adapter intentionally does NOT translate the outcome vocabulary. This
dataset's Community Outcome is opposition-centric (win/loss/pending/mixed),
which is the gate's source of record. The gate config should accept those
values directly rather than be force-mapped onto Approved/Blocked.
"""

from __future__ import annotations

import re

COLUMN_ALIASES = {
    # canonical gate field : CSV columns to source it from, first non-empty wins
    "Outcome":   ["Community Outcome"],
    "Notes":     ["Summary"],
    "Latitude":  ["lat", "Latitude", "Lat"],
    "Longitude": ["lon", "Longitude", "Lon", "Lng"],
    "Operator":  ["Company", "Hyperscaler", "Entity"],
    "Capacity":  ["Megawatts"],
    "Name":      ["Project Name", "Incident", "Entity"],
}

STATE_ABBREV = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

# State normalizer (ADDITIVE, 2026-09-29, spec 005 US2). normalize_state() is
# the one place a free-text State value becomes a USPS code. It never guesses:
# national values and anything it cannot place return "" and go on the review
# list the caller passes. It reads no coordinates, so it can never override the
# geographic gate in state_bounds.py; a row that says Kentucky but is plotted
# in Arkansas normalizes to KY and still fails that gate.
_NAME_TO_CODE = {name.lower(): code for code, name in STATE_ABBREV.items()}
STATE_VARIANTS = {
    # AP-style and common short forms, lower-cased, periods kept
    "ala.": "AL", "ariz.": "AZ", "ark.": "AR", "calif.": "CA", "cal.": "CA",
    "colo.": "CO", "conn.": "CT", "del.": "DE", "fla.": "FL", "ga.": "GA",
    "ill.": "IL", "ind.": "IN", "kan.": "KS", "kans.": "KS", "ky.": "KY",
    "la.": "LA", "md.": "MD", "mass.": "MA", "mich.": "MI", "minn.": "MN",
    "miss.": "MS", "mo.": "MO", "mont.": "MT", "neb.": "NE", "nebr.": "NE",
    "nev.": "NV", "n.h.": "NH", "n.j.": "NJ", "n.m.": "NM", "n.y.": "NY",
    "n.c.": "NC", "n.d.": "ND", "okla.": "OK", "ore.": "OR", "oreg.": "OR",
    "pa.": "PA", "penn.": "PA", "penna.": "PA", "r.i.": "RI", "s.c.": "SC",
    "s.d.": "SD", "tenn.": "TN", "tex.": "TX", "vt.": "VT", "va.": "VA",
    "wash.": "WA", "w.va.": "WV", "w. va.": "WV", "wis.": "WI", "wisc.": "WI",
    "wyo.": "WY", "d.c.": "DC", "washington dc": "DC", "washington d.c.": "DC",
    "washington, d.c.": "DC", "washington, dc": "DC",
}
NOT_A_STATE = {"us", "usa", "u.s.", "u.s.a.", "united states",
               "united states of america", "national", "federal", "nationwide"}
_STATE_PREFIX = re.compile(r"^(commonwealth|state) of\s+", re.IGNORECASE)


def normalize_state(value, review: list | None = None) -> str:
    """USPS code for a State value, or "" when it cannot be placed.

    Accepts codes in any case (a trailing period is allowed: "va."), full
    names, "Commonwealth of"/"State of" prefixes and AP abbreviations. National
    values ("US") and unknown values return "" and, when a list is passed,
    are appended to it as (value, reason) with reason "not_a_state" or
    "unknown". A blank value returns "" and is not a review item.
    """
    raw = "" if value is None else str(value)
    v = re.sub(r"\s+", " ", raw).strip()
    if not v:
        return ""
    low = v.lower()
    if low in NOT_A_STATE:
        if review is not None:
            review.append((raw, "not_a_state"))
        return ""
    if low in STATE_VARIANTS:
        return STATE_VARIANTS[low]
    bare = low.rstrip(".").strip()
    if len(bare) == 2 and bare.upper() in STATE_ABBREV:
        return bare.upper()
    name = _STATE_PREFIX.sub("", bare).strip()
    if name in _NAME_TO_CODE:
        return _NAME_TO_CODE[name]
    if review is not None:
        review.append((raw, "unknown"))
    return ""


# The dataset's native, opposition-centric outcome vocabulary.
OUTCOME_VOCAB = {"win", "loss", "pending", "mixed"}

_COUNTY_SUFFIX = re.compile(r"\s+(County|Parish|Borough)$", re.IGNORECASE)
_DICT_URL_RE = re.compile(r"['\"]url['\"]\s*:\s*['\"]([^'\"]+)['\"]")

# Conservative locality extraction from a headline or summary, used only when a
# record has neither County nor City. Every pattern requires an explicit
# governmental/geographic cue, so it does not invent locations.
_CAP = r"[A-Z][a-zA-Z.'\-]+(?:\s+[A-Z][a-zA-Z.'\-]+){0,2}"
_COUNTY_RE = re.compile(rf"\b({_CAP}\s+(?:County|Parish))\b")
_CITYOF_RE = re.compile(rf"\bCity of\s+({_CAP})\b")
_COUNCIL_RE = re.compile(rf"\b({_CAP})\s+City Council\b")
_COMMISSION_RE = re.compile(rf"\b({_CAP})\s+City Commission\b")
_TOWNSHIP_RE = re.compile(rf"\b({_CAP}\s+Township)\b")
_BOROUGH_RE = re.compile(rf"\b({_CAP}\s+Borough)\b")
_COUNCIL_LC_RE = re.compile(rf"\b({_CAP})\s+council\b")
_VERB_CITY_RE = re.compile(
    rf"^({_CAP})\s+(?:approves|approved|passes|passed|extends|extended|rejects|rejected|"
    r"adopts|adopted|imposes|imposed|votes|voted|enacts|enacted|bans|banned|puts)\b")


def _repair_url(v: str) -> str:
    """Recover the URL from a stringified dict like {'url': 'https://...', 'title': '...'}."""
    if v and v.lstrip().startswith("{"):
        m = _DICT_URL_RE.search(v)
        if m:
            return m.group(1)
    return v


def extract_locality(text: str) -> tuple[str, str]:
    """Best-effort (county, city) from a headline/summary; '' when unknown."""
    if not text:
        return "", ""
    county = city = ""
    m = _COUNTY_RE.search(text)
    if m:
        county = m.group(1)
    m = _CITYOF_RE.search(text) or _COUNCIL_RE.search(text) or _COMMISSION_RE.search(text)
    if m:
        city = m.group(1)
    if not city and not county:
        m = _TOWNSHIP_RE.search(text) or _BOROUGH_RE.search(text)
        if m:
            city = m.group(1)
    if not city and not county:
        m = _COUNCIL_LC_RE.search(text) or _VERB_CITY_RE.search(text)
        if m:
            city = m.group(1)
    return county, city


_FULL_STATES = {name.lower(): name for name in STATE_ABBREV.values()}
_TRAIL_ABBR_RE = re.compile(r",\s*([A-Z]{2})\b")
_PLACE_AFTER = re.compile(r"\s+(county|parish|township|borough|city|avenue|street|st\b)")
_PLACE_BEFORE = re.compile(r"\b(port|fort|ft\.?|lake|mount|mt\.?)\s+$")
_DASH_STATE_RE = re.compile(r"[-–—]\s*([A-Za-z][A-Za-z ]+?)\s*$")


def extract_state(text: str) -> str:
    """Best-effort full state name from a headline/summary; '' when unknown.
    Priority: trailing ', XX' code, then '- StateName' suffix, then a full-name
    phrase. Conservative and used only to fill an empty State."""
    if not text:
        return ""
    abbrs = [a for a in _TRAIL_ABBR_RE.findall(text) if a.upper() in STATE_ABBREV]
    if abbrs:
        return STATE_ABBREV[abbrs[-1].upper()]          # last code wins (", OH" at the end)
    m = _DASH_STATE_RE.search(text)
    if m and m.group(1).strip().lower() in _FULL_STATES:
        return _FULL_STATES[m.group(1).strip().lower()]
    low = text.lower()
    for name in sorted(_FULL_STATES, key=len, reverse=True):   # multi-word names first
        for m in re.finditer(r"\b" + re.escape(name) + r"\b", low):
            # A state name inside a place name is not the state: "Washington
            # County", "Delaware County" and "Port Washington" (Wisconsin)
            # were all backfilled as the state before 2026-09-29 (spec 005).
            if _PLACE_AFTER.match(low, m.end()) or _PLACE_BEFORE.search(low, 0, m.start()):
                continue
            return _FULL_STATES[name]
    return ""


def _first_nonempty(rec: dict, keys) -> str:
    for k in keys:
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def normalize_record(rec: dict) -> dict:
    out = dict(rec)
    for canon, sources in COLUMN_ALIASES.items():
        if not (str(out.get(canon, "")).strip()):
            v = _first_nonempty(rec, sources)
            if v:
                out[canon] = v

    # Recover a locality from the headline/summary when neither field is present
    # (a batch of news-sourced rows arrives with the place only in the title).
    if not str(out.get("County", "")).strip() and not str(out.get("City", "")).strip():
        src = " ".join(str(out.get(k, "") or "") for k in ("Name", "Incident", "Title", "Project Name"))
        county, city = extract_locality(src)
        if not (county or city):
            county, city = extract_locality(str(out.get("Summary", "") or out.get("Notes", "") or ""))
        if county:
            out["County"] = county
        if city and not str(out.get("City", "")).strip():
            out["City"] = city

    # If the city field actually holds a county name (e.g. "Prince William County"),
    # reclassify it so the record is placed as a county.
    if not str(out.get("County", "")).strip():
        cityval = str(out.get("City", "")).strip()
        if re.search(r"(?i)\b(County|Parish|Borough)$", cityval):
            out["County"] = cityval
            out["City"] = ""

    county = str(out.get("County", "")).strip()
    if county:
        out["County"] = _COUNTY_SUFFIX.sub("", county)

    state = str(out.get("State", "")).strip()
    if not state:
        src = " ".join(str(out.get(k, "") or "") for k in ("Name", "Incident", "Title"))
        state = extract_state(src) or extract_state(str(out.get("Summary", "") or out.get("Notes", "") or ""))
        if state:
            out["State"] = state
    if len(state) == 2 and state.upper() in STATE_ABBREV:
        out["State"] = STATE_ABBREV[state.upper()]

    for f in ("Source URL", "Source", "Sources"):
        if isinstance(out.get(f), str) and out[f].strip():
            out[f] = _repair_url(out[f].strip())

    return out


def normalize_records(records: list[dict]) -> list[dict]:
    return [normalize_record(r) for r in records]


def selftest() -> int:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import state_bounds
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    for v in ("Virginia", "VA", "va", "va.", "Va.", "Commonwealth of Virginia",
              " virginia ", "State of Virginia"):
        check(f"{v!r} -> VA", normalize_state(v) == "VA")
    check("District of Columbia -> DC", normalize_state("District of Columbia") == "DC")
    check("D.C. -> DC", normalize_state("D.C.") == "DC")
    check("Commonwealth of Pennsylvania -> PA",
          normalize_state("Commonwealth of Pennsylvania") == "PA")
    check("New  York (double space) -> NY", normalize_state("New  York") == "NY")
    rev = []
    check("US -> '' (never guessed)", normalize_state("US", rev) == "")
    check("US lands on review as not_a_state", rev == [("US", "not_a_state")])
    rev = []
    check("Freedonia -> ''", normalize_state("Freedonia", rev) == "")
    check("unknown value lands on review", rev == [("Freedonia", "unknown")])
    rev = []
    check("blank -> '' with no review item",
          normalize_state("", rev) == "" and normalize_state(None, rev) == "" and rev == [])
    check("Puerto Rico is not in the 51-code set", normalize_state("PR") == "")
    check("every full name round-trips",
          all(normalize_state(n) == c for c, n in STATE_ABBREV.items()))
    # prj_61 (now prj_78): state Kentucky, coordinates in Clark County, Arkansas.
    check("Kentucky -> KY", normalize_state("Kentucky") == "KY")
    check("geographic gate still flags the KY row plotted in Arkansas",
          state_bounds.in_state(34.0537, -93.1059, normalize_state("Kentucky")) is False)
    check("extract_state: Texas headline", extract_state("Permit moratorium ordered on new Texas data centers") == "Texas")
    check("extract_state: Washington County is not the state",
          extract_state("Washington County commissioner calls for moratorium") == "")
    check("extract_state: Port Washington is not the state",
          extract_state("Judge dismisses lawsuit challenging Port Washington data center") == "")
    check("extract_state: Delaware County is not the state",
          extract_state("More Delaware County communities address data center zoning") == "")
    check("extract_state: a later genuine mention still counts",
          extract_state("Washington County board meets as Ohio weighs rules") == "Ohio")
    check("extract_state: trailing code wins", extract_state("Vote in Salix, IA") == "Iowa")
    check("normalize_record still expands codes for the gate",
          normalize_record({"State": "WA"})["State"] == "Washington")
    print(f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    import csv, json, sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    path = sys.argv[1] if len(sys.argv) > 1 else "master_opposition.csv"
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    norm = normalize_records(rows)
    sample = norm[0]
    print(f"Normalized {len(norm)} records. Example canonical fields on row 1:")
    for k in ("Name", "Outcome", "Notes", "State", "County", "Latitude", "Longitude", "Operator"):
        print(f"  {k:<10} = {sample.get(k, '')!r}")
