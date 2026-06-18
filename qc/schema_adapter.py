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


def _repair_url(v: str) -> str:
    """Recover the URL from a stringified dict like {'url': 'https://...', 'title': '...'}."""
    if v and v.lstrip().startswith("{"):
        m = _DICT_URL_RE.search(v)
        if m:
            return m.group(1)
    return v


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

    county = str(out.get("County", "")).strip()
    if county:
        out["County"] = _COUNTY_SUFFIX.sub("", county)

    state = str(out.get("State", "")).strip()
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
