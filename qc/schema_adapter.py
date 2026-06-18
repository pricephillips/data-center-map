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
        if re.search(r"\b" + re.escape(name) + r"\b", low):
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


if __name__ == "__main__":
    import csv, json, sys
    path = sys.argv[1] if len(sys.argv) > 1 else "master_opposition.csv"
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    norm = normalize_records(rows)
    sample = norm[0]
    print(f"Normalized {len(norm)} records. Example canonical fields on row 1:")
    for k in ("Name", "Outcome", "Notes", "State", "County", "Latitude", "Longitude", "Operator"):
        print(f"  {k:<10} = {sample.get(k, '')!r}")
