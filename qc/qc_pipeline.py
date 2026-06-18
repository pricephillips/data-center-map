"""
qc_pipeline.py

Dataset-wide QC gate for the master opposition database.

This is the single validity filter every downstream consumer reads from. Only
records that pass strict, context-aware tests move forward into the clean
export feed; anything with a serious validity concern is quarantined.

Schema
------
Records are normalized by schema_adapter.py first, so the gate reads the real
master_opposition.csv columns (Community Outcome, Summary, lat/lon, two-letter
states, "X County" names) under canonical names.

Outcome vocabulary is the dataset's own, opposition-centric set:
  win      the opposition succeeded (project blocked, restriction enacted)
  loss     the opposition failed (project approved, restriction defeated)
  pending  undecided
  mixed    split result
This is the database's source of record, so the gate validates against it
directly rather than translating to a project-centric Approved/Blocked scheme.

Brains
------
  legislative      bills and resolutions
  moratorium       local-government actions: moratoria, ordinances, zoning
  project          a specific data center project or proposal
  public_comment   public meetings, comment periods, hearings
  study            studies, reports, polls, datasets (evidence, not events)
  generic          fallback

Note on legislation: the bill-lifecycle outcome check is intentionally NOT run.
On this dataset win/loss depends on whether a bill was pro- or anti-industry,
which the lifecycle alone cannot determine and which no structured field
encodes. The other legislative checks still run.

Block policy
------------
A record is blocked if any issue is HIGH or CRITICAL (BLOCK_AT). MEDIUM and LOW
are reported, not blocked. Change BLOCK_AT in one place to tune strictness.

Run
---
python qc_pipeline.py --records records.json --out qc_out
python qc_pipeline.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import legislative_outcome as L          # used for _as_text, looks_legislative, _BILL_RE
import schema_adapter as A
import enrichment as E

# ===========================================================================
# Config
# ===========================================================================

SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
BLOCK_AT = {"CRITICAL", "HIGH"}

ALLOWED_OUTCOMES = {"win", "loss", "pending", "mixed"}   # opposition-centric

STATE_CAPITALS = {
    "Alabama": "Montgomery", "Alaska": "Juneau", "Arizona": "Phoenix",
    "Arkansas": "Little Rock", "California": "Sacramento", "Colorado": "Denver",
    "Connecticut": "Hartford", "Delaware": "Dover", "Florida": "Tallahassee",
    "Georgia": "Atlanta", "Hawaii": "Honolulu", "Idaho": "Boise",
    "Illinois": "Springfield", "Indiana": "Indianapolis", "Iowa": "Des Moines",
    "Kansas": "Topeka", "Kentucky": "Frankfort", "Louisiana": "Baton Rouge",
    "Maine": "Augusta", "Maryland": "Annapolis", "Massachusetts": "Boston",
    "Michigan": "Lansing", "Minnesota": "Saint Paul", "Mississippi": "Jackson",
    "Missouri": "Jefferson City", "Montana": "Helena", "Nebraska": "Lincoln",
    "Nevada": "Carson City", "New Hampshire": "Concord", "New Jersey": "Trenton",
    "New Mexico": "Santa Fe", "New York": "Albany", "North Carolina": "Raleigh",
    "North Dakota": "Bismarck", "Ohio": "Columbus", "Oklahoma": "Oklahoma City",
    "Oregon": "Salem", "Pennsylvania": "Harrisburg", "Rhode Island": "Providence",
    "South Carolina": "Columbia", "South Dakota": "Pierre", "Tennessee": "Nashville",
    "Texas": "Austin", "Utah": "Salt Lake City", "Vermont": "Montpelier",
    "Virginia": "Richmond", "Washington": "Olympia", "West Virginia": "Charleston",
    "Wisconsin": "Madison", "Wyoming": "Cheyenne",
}
CAPITAL_COUNTIES = {
    "Alabama": "Montgomery", "Arizona": "Maricopa", "Arkansas": "Pulaski",
    "California": "Sacramento", "Colorado": "Denver", "Connecticut": "Hartford",
    "Delaware": "Kent", "Florida": "Leon", "Georgia": "Fulton", "Idaho": "Ada",
    "Illinois": "Sangamon", "Indiana": "Marion", "Iowa": "Polk", "Kansas": "Shawnee",
    "Kentucky": "Franklin", "Louisiana": "East Baton Rouge", "Maine": "Kennebec",
    "Maryland": "Anne Arundel", "Massachusetts": "Suffolk", "Michigan": "Ingham",
    "Minnesota": "Ramsey", "Mississippi": "Hinds", "Missouri": "Cole",
    "Montana": "Lewis and Clark", "Nebraska": "Lancaster", "New Hampshire": "Merrimack",
    "New Jersey": "Mercer", "New Mexico": "Santa Fe", "New York": "Albany",
    "North Carolina": "Wake", "North Dakota": "Burleigh", "Ohio": "Franklin",
    "Oklahoma": "Oklahoma", "Oregon": "Marion", "Pennsylvania": "Dauphin",
    "Rhode Island": "Providence", "South Carolina": "Richland", "South Dakota": "Hughes",
    "Tennessee": "Davidson", "Texas": "Travis", "Utah": "Salt Lake", "Vermont": "Washington",
    "Washington": "Thurston", "West Virginia": "Kanawha", "Wisconsin": "Dane",
    "Wyoming": "Laramie",
}
STATE_BBOX = {
    "Iowa": (40.3, 43.6, -96.7, -90.1), "West Virginia": (37.1, 40.7, -82.7, -77.7),
    "Pennsylvania": (39.7, 42.3, -80.6, -74.6), "Kentucky": (36.4, 39.2, -89.6, -81.9),
    "Illinois": (36.9, 42.6, -91.6, -87.0), "Texas": (25.8, 36.6, -106.7, -93.5),
}
US_BBOX = (17.0, 72.0, -180.0, -64.0)

OPERATORS = [
    "google", "meta", "microsoft", "apple", "amazon", "aws", "qts",
    "applied digital", "openai", "oracle", "equinix", "digital realty",
    "nvidia", "vantage", "switch", "cyrusone", "edgeconnex", "stack infrastructure",
]
SOCIAL_DOMAINS = ("facebook.com", "instagram.com", "reddit.com", "twitter.com",
                  "x.com", "youtube.com", "tiktok.com", "threads.net")
NEWS_DOMAINS = (
    "kcrg.com", "radioiowa.com", "wvik.org", "desmoinesregister.com", "axios.com",
    "spglobal.com", "datacenterdynamics.com", "insideclimatenews.org", "usatoday.com",
    "iowapublicradio.org", "wenatcheeworld.com", "siteselection.com", "wpr.org",
    "nbcnews.com", "wired.com", "reuters.com", "apnews.com", "politico.com",
    "bloomberg.com", "nytimes.com", "washingtonpost.com", "wsj.com", "cnn.com",
    "npr.org", "datacenterwatch.org", "fractracker.org", "statesman.com",
)
RESEARCH_DOMAINS = (
    "doi.org", "sciencedirect.com", "springer.com", "nature.com", "jstor.org",
    "gallup.com", "pewresearch.org", "brookings.edu", "rand.org", "naco.org",
    "nrel.gov", "pnnl.gov", "lbl.gov", "columbia.edu", "oecd.org", "iea.org",
    "nber.org", "ssrn.com",
)
SHORTENERS = ("bit.ly", "t.co", "lnkd.in", "tinyurl.com", "ow.ly", "goo.gl", "buff.ly")
SEARCH_URLS = ("google.com/search", "bing.com/search", "duckduckgo.com")
ERROR_TOKENS = ("#ref!", "#n/a", "#value!", "#div/0!", "#name?", "#null!", "#num!")
NULL_ARTIFACTS = ("nan", "none", "null")

_RESOLVED_STATUS = {"passed", "signed", "approved", "enacted", "defeated", "dead", "died",
                    "cancelled", "canceled", "expired", "withdrawn", "vetoed", "failed",
                    "denied", "rejected", "adopted"}
_INPROGRESS_STATUS = {"active", "pending", "ongoing", "proposed", "filed", "hearing",
                      "delayed", "introduced", "monitoring", "considering", "review"}

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_STRINGIFIED_DICT_RE = re.compile(r"^\s*\{.*['\"]url['\"]\s*:", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
CURRENT_YEAR = 2026


# ===========================================================================
# Data structures
# ===========================================================================

@dataclass
class Issue:
    severity: str
    code: str
    field: str
    message: str


@dataclass
class RecordVerdict:
    record_id: str
    name: str
    brain: str
    max_severity: str | None
    blocked: bool
    issues: list[Issue] = field(default_factory=list)


@dataclass
class PipelineResult:
    verdicts: list[RecordVerdict]
    clean: list[dict]
    quarantine: list[dict]
    dataset_findings: list[Issue] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.verdicts)

    @property
    def n_blocked(self) -> int:
        return len(self.quarantine)


# ===========================================================================
# Field access
# ===========================================================================

def text(record: dict, *keys: str) -> str:
    for key in keys:
        if key in record:
            val = L._as_text(record[key])
            if val:
                return val.strip()
    return ""


def record_id(record: dict) -> str:
    return text(record, "ID", "Id", "Record ID") or text(record, "Name", "Title") or "(unnamed)"


def record_name(record: dict) -> str:
    return text(record, "Name", "Title") or record_id(record)


def state_of(record: dict) -> str:
    return text(record, "State")


def city_of(record: dict) -> str:
    return text(record, "City", "Locality", "Municipality")


def is_statewide(record: dict) -> bool:
    scope = text(record, "Scope").lower()
    if scope in ("statewide", "federal"):
        return scope == "statewide"
    blob = " ".join(text(record, k) for k in ("Scope", "City", "Name", "Title", "Geography")).lower()
    return "statewide" in blob or L.looks_legislative(record)


def normalize_outcome(value: str) -> str | None:
    v = (value or "").strip().lower()
    return v if v in ALLOWED_OUTCOMES else None


def pinned_to_capital(record: dict) -> bool:
    cap = STATE_CAPITALS.get(state_of(record))
    city = city_of(record)
    return bool(cap and city and cap.lower() in city.lower())


def coords_of(record: dict):
    lat = text(record, "Latitude", "Lat", "lat")
    lon = text(record, "Longitude", "Lon", "Lng", "lon")
    if not (lat and lon):
        combo = text(record, "Coordinates", "Coords", "LatLng")
        if combo and "," in combo:
            parts = combo.split(",")
            lat, lon = parts[0].strip(), parts[1].strip()
    if not (lat and lon):
        return None
    try:
        return (float(lat), float(lon))
    except ValueError:
        return "INVALID"


def domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url.strip(), re.IGNORECASE)
    return (m.group(1) if m else "").lower()


def classify_source(url: str) -> str:
    if not url:
        return "none"
    d = domain_of(url)
    if not d:
        return "unknown"
    if any(s in d for s in SOCIAL_DOMAINS):
        return "social"
    if d.endswith(".gov") or ".gov" in d or "legiscan.com" in d:
        return "gov"
    if d.endswith(".edu") or any(r in d for r in RESEARCH_DOMAINS):
        return "research"
    if any(n in d for n in NEWS_DOMAINS):
        return "news"
    return "unknown"


def _in_box(lat: float, lon: float, box) -> bool:
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


# ===========================================================================
# Baseline checks (run for every record)
# ===========================================================================

def check_source_valid(record: dict) -> list[Issue]:
    raw = text(record, "Source URL", "Source", "Sources")
    if not raw:
        return [Issue("HIGH", "SOURCE_MISSING", "Source URL", "No source URL on the record.")]
    if _STRINGIFIED_DICT_RE.match(raw):
        return [Issue("HIGH", "SOURCE_STRINGIFIED_DICT", "Source URL",
                      "Source URL is a stringified dict; ingest wrote the object instead of the URL.")]
    if not _URL_RE.match(raw):
        return [Issue("HIGH", "SOURCE_MALFORMED", "Source URL",
                      f"Source URL is not a valid http(s) link: '{raw[:60]}'.")]
    d = domain_of(raw)
    if any(s == d or s in d for s in SHORTENERS):
        return [Issue("MEDIUM", "SOURCE_SHORTENER", "Source URL",
                      f"Source is a link shortener ({d}); store the resolved URL.")]
    if any(s in raw.lower() for s in SEARCH_URLS):
        return [Issue("MEDIUM", "SOURCE_SEARCH_PAGE", "Source URL",
                      "Source is a search-results page, not a primary source.")]
    return []


def check_date_sanity(record: dict) -> list[Issue]:
    raw = text(record, "Date")
    if not raw:
        return [Issue("MEDIUM", "DATE_MISSING", "Date", "No date on the record.")]
    m = _DATE_RE.search(raw)
    if not m:
        return [Issue("LOW", "DATE_FORMAT", "Date", f"Date '{raw}' is not in YYYY-MM-DD form.")]
    year = int(m.group(1))
    if year > CURRENT_YEAR:
        return [Issue("HIGH", "DATE_FUTURE", "Date", f"Date '{raw}' is in the future.")]
    if year < 2010:
        return [Issue("MEDIUM", "DATE_STALE", "Date", f"Date '{raw}' predates 2010, likely a bad import.")]
    return []


def check_notes_quality(record: dict) -> list[Issue]:
    notes = text(record, "Notes")
    if notes.strip().lower() in NULL_ARTIFACTS:
        return [Issue("MEDIUM", "NOTES_NULL_ARTIFACT", "Notes",
                      f"Notes contain the literal '{notes}', a failed-ingest artifact.")]
    if 0 < len(notes) < 40:
        return [Issue("LOW", "NOTES_THIN", "Notes", "Notes are under 40 characters; record may be too thin to cite.")]
    return []


def check_field_artifacts(record: dict) -> list[Issue]:
    out: list[Issue] = []
    for fname in ("Name", "Title", "County", "City", "Outcome", "Status"):
        val = text(record, fname)
        low = val.lower()
        if any(tok in low for tok in ERROR_TOKENS):
            out.append(Issue("HIGH", "FIELD_ERROR_TOKEN", fname,
                             f"Field '{fname}' contains a spreadsheet error token: '{val}'."))
        elif fname in ("Name", "County", "City") and low in NULL_ARTIFACTS:
            out.append(Issue("HIGH", "FIELD_NULL_ARTIFACT", fname,
                             f"Field '{fname}' is the literal '{val}', a failed-ingest artifact."))
    return out


def check_coordinates(record: dict) -> list[Issue]:
    c = coords_of(record)
    if c is None:
        return []
    if c == "INVALID":
        return [Issue("HIGH", "COORD_INVALID", "Coordinates", "Coordinates are non-numeric.")]
    lat, lon = c
    if lat == 0 and lon == 0:
        return [Issue("HIGH", "COORD_NULL_ISLAND", "Coordinates", "Coordinates are (0, 0), a missing geocode.")]
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return [Issue("HIGH", "COORD_RANGE", "Coordinates",
                      f"Coordinates ({lat}, {lon}) are outside valid lat/lon ranges.")]
    if not _in_box(lat, lon, US_BBOX):
        if _in_box(lon, lat, US_BBOX):
            return [Issue("HIGH", "COORD_SWAPPED", "Coordinates",
                          f"Coordinates ({lat}, {lon}) look like lat/lon were swapped.")]
        return [Issue("HIGH", "COORD_OUTSIDE_US", "Coordinates",
                      f"Coordinates ({lat}, {lon}) fall outside the US.")]
    box = STATE_BBOX.get(state_of(record))
    if box and not _in_box(lat, lon, box):
        return [Issue("MEDIUM", "COORD_OUTSIDE_STATE", "Coordinates",
                      f"Coordinates ({lat}, {lon}) fall outside {state_of(record)}'s bounding box.")]
    return []


def check_statewide_attribution(record: dict) -> list[Issue]:
    if not is_statewide(record):
        return []
    # The only genuine map corruption is a city/coordinate pin sitting on the
    # capital. That stays HIGH (blocking).
    if pinned_to_capital(record):
        return [Issue("HIGH", "STATEWIDE_CAPITAL_SINK", "Geography",
                      f"Statewide record pinned to capital city {city_of(record)}. This places a dot on "
                      "the capital and inflates it; clear the city or plot as statewide.")]
    # A county or district tag (including the capital county) on a statewide
    # record is a labeling issue, not corruption. Keep the record in the feed and
    # flag it: downstream should rely on qc_jurisdiction_level == 'state' to avoid
    # pinning it, or the county should be cleared.
    minor: list[str] = []
    county = text(record, "County")
    district = text(record, "Congressional District", "District")
    if county and county.lower() != "statewide":
        minor.append(f"County = '{county}'")
    if district:
        minor.append(f"District = '{district}'")
    if minor:
        return [Issue("MEDIUM", "STATEWIDE_GEO_TAG", "Geography",
                      "Statewide record carries local geography: " + "; ".join(minor)
                      + ". Clear it, or have the map skip pins where qc_jurisdiction_level is 'state'.")]
    return []


BASELINE_CHECKS = [
    check_source_valid,
    check_date_sanity,
    check_notes_quality,
    check_field_artifacts,
    check_coordinates,
    check_statewide_attribution,
]


# ===========================================================================
# Shared event-outcome checks (every brain except study)
# ===========================================================================

def check_outcome_value(record: dict) -> list[Issue]:
    raw = text(record, "Outcome")
    if not raw:
        return [Issue("MEDIUM", "OUTCOME_MISSING", "Outcome", "Outcome not yet assessed (blank).")]
    if raw.strip().lower() not in ALLOWED_OUTCOMES:
        return [Issue("HIGH", "OUTCOME_VOCAB", "Outcome",
                      f"Outcome '{raw}' is not valid. Allowed: win, loss, pending, mixed.")]
    return []


def check_outcome_status_logic(record: dict) -> list[Issue]:
    outcome = normalize_outcome(text(record, "Outcome"))
    status = text(record, "Status").strip().lower()
    if not outcome or not status:
        return []
    if outcome in {"win", "loss"} and status in _INPROGRESS_STATUS:
        return [Issue("MEDIUM", "OUTCOME_STATUS_LOGIC", "Status",
                      f"Outcome '{outcome}' is final but Status '{status}' is still in progress.")]
    if outcome == "pending" and status in _RESOLVED_STATUS:
        return [Issue("MEDIUM", "OUTCOME_STATUS_LOGIC", "Outcome",
                      f"Outcome is 'pending' but Status '{status}' indicates the matter is resolved.")]
    return []


def check_outcome_mechanism_consistency(record: dict) -> list[Issue]:
    """Flag a 'win' recorded on an action that constrains rather than blocks
    (e.g., a conditional zoning ordinance like Linn County). Non-blocking: it
    highlights a nuance for review, it does not remove the record."""
    enr = E.enrich_record(record)
    if enr["qc_is_block"] is None:        # legislation / stance-ambiguous: skip
        return []
    outcome = normalize_outcome(text(record, "Outcome"))
    strength = enr["qc_restriction_strength"] or 0
    if outcome == "win" and enr["qc_is_block"] is False and strength >= 2:
        return [Issue("MEDIUM", "OUTCOME_OVERSTATED", "Outcome",
                      f"Recorded 'win' (a block), but this is a {enr['qc_strength_label']} action "
                      f"({enr['qc_mechanism']}) that constrains rather than halts data centers. "
                      "Consider 'mixed' or a partial-effect note; highlighted, not a block.")]
    return []


EVENT_OUTCOME_CHECKS = [check_outcome_value, check_outcome_status_logic, check_outcome_mechanism_consistency]


def check_mappable_coords(record: dict) -> list[Issue]:
    if is_statewide(record):
        return []
    if (text(record, "County") or city_of(record)) and coords_of(record) is None:
        return [Issue("MEDIUM", "COORD_MISSING", "Coordinates",
                      "Local event has a locality but no coordinates; it cannot be pinned precisely.")]
    return []


# ===========================================================================
# Legislative brain  (bill-lifecycle outcome check intentionally omitted)
# ===========================================================================

def check_bill_id_present(record: dict) -> list[Issue]:
    blob = " ".join(text(record, k) for k in ("Name", "Title", "Notes"))
    if not L._BILL_RE.search(blob):
        return [Issue("MEDIUM", "BILL_ID_MISSING", "Name",
                      "Legislative record has no bill identifier (HF/SF/HB/SB/etc.) to anchor it.")]
    return []


def check_legislative_source(record: dict) -> list[Issue]:
    kind = classify_source(text(record, "Source URL", "Source", "Sources"))
    if kind == "social":
        return [Issue("HIGH", "LEG_SOURCE_WEAK", "Source URL",
                      "A claim about a bill's fate is sourced only to social media. Use the legislature site or a news report.")]
    return []


LEGISLATIVE_CHECKS = EVENT_OUTCOME_CHECKS + [check_bill_id_present, check_legislative_source]


# ===========================================================================
# Moratorium brain (local-government outcome engine, opposition-centric)
# ===========================================================================

_RESTRICT_VERBS = ("adopted", "passed", "approved", "imposed", "enacted", "established",
                   "instituted", "extended", "voted for", "voted to impose", "voted to adopt",
                   "voted to extend", "put in place", "placed a")
_REJECT_VERBS = ("voted down", "rejected", "defeated", "voted against", "against",
                 "did not pass", "failed", "struck down", "declined")
_PENDING_CUES = ("considering", "will consider", "to consider", "under consideration",
                 "proposed", "drafting", "weighing", "tabled", "scheduled", "public hearing",
                 "public meeting", "set a hearing", "mulling")
# Only the literal word "moratorium" is reliably anti-industry, so outcome is
# inferred from moratorium passage/defeat alone. Ordinances, rezonings, permits,
# and projects are direction-ambiguous in a win/loss dataset and are NOT inferred.
_RESTRICTION_NOUNS = ("moratorium", "moratoria")
_WINDOW = 60


@dataclass(frozen=True)
class MorMatch:
    outcome: str
    matched_phrase: str
    note: str


def _verb_near(text_l: str, noun: str, verbs) -> str | None:
    start = 0
    while True:
        i = text_l.find(noun, start)
        if i < 0:
            return None
        start = i + len(noun)
        chunk = text_l[max(0, i - _WINDOW): i + len(noun) + _WINDOW]
        hit = next((v for v in verbs if v in chunk), None)
        if hit:
            return f"{hit} ... {noun}"


def infer_local_action(notes: str) -> MorMatch | None:
    """Returns the opposition outcome implied by the notes: win / loss / mixed / pending.
    Only infers from 'moratorium'/'moratoria' — direction-ambiguous nouns like
    ordinance, permit, rezoning are intentionally excluded."""
    if not notes:
        return None
    t = notes.lower()
    win = loss = None
    for noun in _RESTRICTION_NOUNS:
        loss = loss or _verb_near(t, noun, _REJECT_VERBS)
        win  = win  or _verb_near(t, noun, _RESTRICT_VERBS)
    if win and loss:
        return MorMatch("mixed", f"{win} + {loss}", "both a restriction and a rejection are present")
    if win:
        return MorMatch("win", win, "")
    if loss:
        return MorMatch("loss", loss, "")
    # No reliable enactment or defeat signal next to 'moratorium'. Do NOT guess
    # 'pending' from words like 'proposed' or 'public hearing': summaries that
    # narrate the full arc (proposed -> hearing -> passed) would trip that and
    # flag genuine wins. Infer nothing instead.
    return None


def _mor_severity(recorded: str, inferred: str) -> str | None:
    if recorded == inferred:
        return None
    if {recorded, inferred} == {"win", "loss"}:
        return "CRITICAL"
    if inferred == "mixed":
        return None if recorded == "mixed" else "MEDIUM"
    if recorded == "mixed":
        return "MEDIUM"
    if recorded in {"win", "loss"} and inferred == "pending":
        return "HIGH"
    if recorded == "pending" and inferred in {"win", "loss"}:
        return "MEDIUM"
    return "MEDIUM"


def check_moratorium_outcome(record: dict) -> list[Issue]:
    match = infer_local_action(text(record, "Notes"))
    if match is None or match.outcome == "pending":
        return []
    recorded = normalize_outcome(text(record, "Outcome"))
    if recorded is None or recorded == match.outcome:
        return []
    verb = {"win": "enacted", "loss": "defeated", "mixed": "split"}.get(match.outcome, match.outcome)
    # Review-only (MEDIUM, non-blocking): a moratorium can pass and later expire
    # or be repealed, so a differing recorded value is not automatically an error.
    return [Issue("MEDIUM", "MORATORIUM_OUTCOME_REVIEW", "Outcome",
                  f"Recorded '{text(record, 'Outcome')}', but the notes describe a moratorium that was "
                  f"{verb} (\"{match.matched_phrase}\"). Confirm the final outcome; it may have later "
                  "expired or been repealed.")]


def _has_coords(record: dict) -> bool:
    lat, lon = text(record, "Latitude", "lat"), text(record, "Longitude", "lon")
    try:
        la, lo = float(lat), float(lon)
    except ValueError:
        return False
    return abs(la) <= 90 and abs(lo) <= 180 and not (la == 0 and lo == 0)


def _placeable(record: dict) -> bool:
    """A record can be mapped if it has a county, a city, or valid coordinates."""
    return bool(text(record, "County") or city_of(record) or _has_coords(record))


def check_local_geography(record: dict) -> list[Issue]:
    if is_statewide(record):
        return []                            # a statewide moratorium effort is plausible
    if not _placeable(record):
        return [Issue("HIGH", "UNPLACEABLE", "County/City",
                      "Local action has no county, city, or coordinates; it cannot be placed on the map.")]
    return []


def check_local_source(record: dict) -> list[Issue]:
    kind = classify_source(text(record, "Source URL", "Source", "Sources"))
    if kind == "social":
        return [Issue("MEDIUM", "LOCAL_SOURCE_WEAK", "Source URL",
                      "Local action is sourced to social media; prefer a county/city record or local news.")]
    return []


MORATORIUM_CHECKS = EVENT_OUTCOME_CHECKS + [
    check_moratorium_outcome, check_local_geography, check_local_source, check_mappable_coords,
]


# ===========================================================================
# Project brain
# ===========================================================================

def check_project_location(record: dict) -> list[Issue]:
    if not _placeable(record):
        return [Issue("HIGH", "UNPLACEABLE", "County/City",
                      "Project record has no county, city, or coordinates; it cannot be placed.")]
    return []


def check_capacity_sanity(record: dict) -> list[Issue]:
    blob = " ".join(text(record, k) for k in ("Capacity", "Notes"))
    mw = re.search(r"(\d{1,6})\s*mw", blob, re.IGNORECASE)
    if mw and not (1 <= int(mw.group(1)) <= 8000):
        return [Issue("MEDIUM", "CAPACITY_IMPLAUSIBLE", "Capacity",
                      f"Capacity {mw.group(1)} MW is outside a plausible range (1-8000 MW).")]
    return []


PROJECT_CHECKS = EVENT_OUTCOME_CHECKS + [check_project_location, check_capacity_sanity, check_mappable_coords]


# ===========================================================================
# Public comment brain
# ===========================================================================

def check_public_locality(record: dict) -> list[Issue]:
    if is_statewide(record):
        return []
    if not text(record, "County") and not city_of(record):
        return [Issue("HIGH", "PUBLIC_NO_LOCATION", "County/City",
                      "Public meeting/comment record has no county or city.")]
    return []


PUBLIC_COMMENT_CHECKS = EVENT_OUTCOME_CHECKS + [check_public_locality, check_mappable_coords]


# ===========================================================================
# Study / report brain
# ===========================================================================

def check_study_source(record: dict) -> list[Issue]:
    kind = classify_source(text(record, "Source URL", "Source", "Sources"))
    if kind == "social":
        return [Issue("HIGH", "STUDY_SOURCE_WEAK", "Source URL",
                      "A study/report is sourced only to social media; cite the publication directly.")]
    return []


def check_study_outcome_misuse(record: dict) -> list[Issue]:
    raw = text(record, "Outcome").strip().lower()
    if raw in {"win", "loss"}:
        return [Issue("MEDIUM", "STUDY_EVENT_OUTCOME", "Outcome",
                      f"A study/report is tagged with the event outcome '{raw}'. A report is evidence, "
                      "not a win or loss; leave Outcome blank or mark it pending/mixed.")]
    return []


STUDY_CHECKS = [check_study_source, check_study_outcome_misuse]
GENERIC_CHECKS = list(EVENT_OUTCOME_CHECKS)


# ===========================================================================
# Router
# ===========================================================================

BRAINS = {
    "legislative": LEGISLATIVE_CHECKS, "moratorium": MORATORIUM_CHECKS,
    "project": PROJECT_CHECKS, "public_comment": PUBLIC_COMMENT_CHECKS,
    "study": STUDY_CHECKS, "generic": GENERIC_CHECKS,
}
_TYPE_HINTS = [
    ("legislative", ("legislat", "bill", "resolution")),
    ("study", ("study", "report", "poll", "survey", "research", "paper", "dataset",
               "database", "index", "guidebook", "whitepaper", "white paper", "brief", "analysis")),
    ("moratorium", ("moratorium", "ordinance", "zoning", "restriction", "setback")),
    ("public_comment", ("public comment", "public meeting", "hearing", "listening")),
    ("project", ("project", "proposal", "facility", "permit", "campus", "withdrawal")),
]
_SIGNALS = {
    "study": ("study", "report", "survey", "poll", "peer-reviewed", "working paper", "dataset"),
    "moratorium": ("moratorium", "ordinance", "rezoning", "setback", "board of supervisors",
                   "supervisors voted", "city council", "county board"),
    "project": ("data center", "campus", "facility", "broke ground", "megawatt", " mw"),
    "public_comment": ("public comment", "public meeting", "public hearing", "listening session"),
}


def route(record: dict) -> str:
    if L.looks_legislative(record):
        return "legislative"
    opp_type = text(record, "Opposition Type", "Type", "Category").lower().replace("_", " ")
    for brain, hints in _TYPE_HINTS:
        if any(h in opp_type for h in hints):
            return brain
    if classify_source(text(record, "Source URL", "Source", "Sources")) == "research":
        return "study"
    blob = " ".join(text(record, k) for k in ("Name", "Title", "Notes", "Operator")).lower()
    scores = {b: sum(1 for s in sig if s in blob) for b, sig in _SIGNALS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "generic"


# ===========================================================================
# Dataset-level checks
# ===========================================================================

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def check_content_duplicates(records: list[dict]):
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, rec in enumerate(records):
        name, date = _norm(record_name(rec)), _norm(text(rec, "Date"))
        if L.looks_legislative(rec):
            key = ("leg", name, date)
        else:
            key = ("loc", E.jurisdiction_key(rec), name, date)
        groups[key].append(i)
    for idxs in groups.values():
        if len(idxs) > 1:
            ids = [record_id(records[i]) for i in idxs]
            for i in idxs:
                others = [x for x in ids if x != record_id(records[i])]
                yield i, Issue("HIGH", "DUPLICATE", "Name/Date",
                               f"Possible duplicate of: {', '.join(others) or '(same name/date)'}. "
                               "Held out of the feed until merged.")


def check_capital_overrepresentation(records: list[dict]):
    by_capital: dict[tuple, dict] = defaultdict(lambda: {"total": 0, "statewide": 0, "ids": []})
    for rec in records:
        if pinned_to_capital(rec):
            state, cap = state_of(rec), STATE_CAPITALS.get(state_of(rec))
            e = by_capital[(state, cap)]
            e["total"] += 1
            if is_statewide(rec):
                e["statewide"] += 1
                e["ids"].append(record_id(rec))
    for (state, cap), e in by_capital.items():
        if e["statewide"] > 0:
            share = round(100 * e["statewide"] / e["total"]) if e["total"] else 0
            ids = ", ".join(e["ids"][:8]) + (" ..." if len(e["ids"]) > 8 else "")
            yield "__dataset__", Issue(
                "MEDIUM", "CAPITAL_OVERREP", "City",
                f"{cap}, {state}: {e['total']} pinned records, {e['statewide']} ({share}%) are statewide "
                f"activities not specific to {cap} ({ids}). They inflate {cap}'s share of the map.")


DATASET_CHECKS = [check_content_duplicates, check_capital_overrepresentation]


# ===========================================================================
# The gate
# ===========================================================================

def _max_severity(issues: list[Issue]) -> str | None:
    return max((i.severity for i in issues), key=lambda s: SEVERITY_RANK.get(s, -1), default=None)


def run(records: list[dict], dataset_checks=None, block_at: set[str] = BLOCK_AT) -> PipelineResult:
    """records are the original rows; they are normalized for checking and the
    originals are what get emitted to the clean feed and quarantine."""
    dataset_checks = dataset_checks or DATASET_CHECKS
    norm = A.normalize_records(records)        # canonical views for checking

    issues_by_idx: dict[int, list[Issue]] = defaultdict(list)
    brain_by_idx: dict[int, str] = {}
    for i, nrec in enumerate(norm):
        brain = route(nrec)
        brain_by_idx[i] = brain
        for chk in BASELINE_CHECKS + BRAINS[brain]:
            issues_by_idx[i].extend(chk(nrec) or [])

    dataset_findings: list[Issue] = []
    for dchk in dataset_checks:
        for target, iss in dchk(norm):
            if target == "__dataset__":
                dataset_findings.append(iss)
            else:
                issues_by_idx[int(target)].append(iss)

    verdicts, clean, quarantine = [], [], []
    for i, original in enumerate(records):
        nrec = norm[i]
        issues = issues_by_idx.get(i, [])
        max_sev = _max_severity(issues)
        blocked = max_sev in block_at
        verdicts.append(RecordVerdict(record_id(nrec), record_name(nrec), brain_by_idx[i],
                                      max_sev, blocked, issues))
        if blocked:
            quarantine.append({"record": original, "enrichment": E.enrich_record(nrec),
                               "brain": brain_by_idx[i],
                               "reasons": [asdict(x) for x in issues if x.severity in block_at],
                               "all_issues": [asdict(x) for x in issues]})
        else:
            clean.append({**original, **E.enrich_record(nrec)})
    return PipelineResult(verdicts, clean, quarantine, dataset_findings)


# ===========================================================================
# Output
# ===========================================================================

def write_outputs(result: PipelineResult, out_dir: str) -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {"clean": os.path.join(out_dir, "clean_export.json"),
             "quarantine": os.path.join(out_dir, "quarantine.json"),
             "report_json": os.path.join(out_dir, "qc_report.json"),
             "report_md": os.path.join(out_dir, "qc_report.md")}
    json.dump(result.clean, open(paths["clean"], "w", encoding="utf-8"), indent=2)
    json.dump(result.quarantine, open(paths["quarantine"], "w", encoding="utf-8"), indent=2)
    report = {"summary": {"total": result.n_total, "clean": len(result.clean),
                          "blocked": result.n_blocked, "block_threshold": sorted(BLOCK_AT)},
              "dataset_findings": [asdict(i) for i in result.dataset_findings],
              "verdicts": [{"record_id": v.record_id, "name": v.name, "brain": v.brain,
                            "max_severity": v.max_severity, "blocked": v.blocked,
                            "issues": [asdict(i) for i in v.issues]} for v in result.verdicts]}
    json.dump(report, open(paths["report_json"], "w", encoding="utf-8"), indent=2)
    open(paths["report_md"], "w", encoding="utf-8").write(render_markdown(result))
    return paths


def render_markdown(result: PipelineResult) -> str:
    from collections import Counter
    codes = Counter(i.code for v in result.verdicts for i in v.issues if v.blocked for _ in [0]
                    if i.severity in BLOCK_AT)
    lines = ["# Master Opposition QC Report", "",
             f"- Records scanned: **{result.n_total}**",
             f"- Passed to feed: **{len(result.clean)}**",
             f"- Blocked / quarantined: **{result.n_blocked}**",
             f"- Block threshold: {', '.join(sorted(BLOCK_AT))}", ""]
    if codes:
        lines += ["## Why records were blocked (counts)", ""]
        lines += [f"- {code}: {n}" for code, n in codes.most_common()] + [""]
    if result.dataset_findings:
        lines += ["## Dataset findings", ""]
        lines += [f"- **{i.severity} {i.code}**: {i.message}" for i in result.dataset_findings] + [""]
    blocked = [v for v in result.verdicts if v.blocked]
    if blocked:
        lines += ["## Blocked records", "", "| Record | Brain | Severity | Issues |", "| :-- | :-- | :-- | :-- |"]
        for v in blocked[:200]:
            cells = "<br>".join(f"**{x.severity}** {x.code}: {x.message}"
                                for x in v.issues if x.severity in BLOCK_AT)
            lines.append(f"| {v.name} | {v.brain} | {v.max_severity} | {cells} |")
        if len(blocked) > 200:
            lines.append(f"| ...and {len(blocked) - 200} more | | | see quarantine.json |")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Self-test battery
# ===========================================================================

def selftest() -> bool:
    ok = True

    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    expect(route({"Name": "HF 2690", "Opposition Type": "legislation"}) == "legislative", "route legislation")
    expect(route({"Name": "x", "Opposition Type": "public_comment", "County": "X"}) == "public_comment",
           "route public_comment (underscore)")
    expect(route({"Name": "x", "Opposition Type": "zoning_restriction"}) == "moratorium", "route zoning_restriction")
    expect(route({"Name": "Gallup poll", "Opposition Type": "poll"}) == "study", "route study")

    # win/loss vocabulary
    expect(check_outcome_value({"Outcome": "win"}) == [], "win is a valid outcome")
    expect(any(i.code == "OUTCOME_VOCAB" for i in check_outcome_value({"Outcome": "Approved"})),
           "old 'Approved' value now flagged invalid")
    expect(any(i.code == "OUTCOME_MISSING" and i.severity == "MEDIUM"
               for i in check_outcome_value({"Outcome": ""})), "blank outcome is MEDIUM, not blocking")

    # moratorium engine in win/loss terms (moratorium noun only)
    m = infer_local_action("Supervisors adopted a 12-month moratorium on data centers.")
    expect(m and m.outcome == "win", "adopted moratorium -> win")
    m = infer_local_action("The council voted down the proposed moratorium.")
    expect(m and m.outcome == "loss", "moratorium voted down -> loss")
    m = infer_local_action("The rezoning application was denied by the board.")
    expect(m is None, "direction-ambiguous rezoning denial -> no inference (correct)")
    r = run([{"Name": "Some county", "Opposition Type": "moratorium", "Outcome": "loss",
              "County": "X", "Date": "2026-01-01", "Source URL": "https://www.kcrg.com/x",
              "Notes": "The county adopted a one-year moratorium on new data center construction countywide."}])
    expect(any(i.code == "MORATORIUM_OUTCOME_REVIEW" and i.severity == "MEDIUM"
               for i in r.verdicts[0].issues) and not r.verdicts[0].blocked,
           "adopted moratorium recorded as loss -> MEDIUM review, not blocked")
    r = run([{"Name": "Proposed only", "Opposition Type": "moratorium", "Outcome": "win",
              "County": "Y", "Date": "2026-01-01", "Source URL": "https://www.kcrg.com/y",
              "Notes": "A data center moratorium was proposed and a public hearing was scheduled for next month."}])
    expect(not any(i.code.startswith("MORATORIUM_OUTCOME") for i in r.verdicts[0].issues),
           "proposed/hearing language no longer produces a false 'should be pending' flag")

    # legislative bill-lifecycle check is OFF
    r = run([{"Name": "HF 2690", "Opposition Type": "legislation", "Outcome": "loss",
              "Date": "2026-02-23", "Source URL": "https://www.legis.iowa.gov/x",
              "Notes": "Bill passed committee but the legislature adjourned sine die without passage."}])
    expect(not any(i.code == "OUTCOME_CONFLICT" for i in r.verdicts[0].issues),
           "bill-lifecycle outcome check is disabled")

    # schema adapter wiring: raw CSV-style row routes and validates
    raw = {"Incident": "Dubuque County", "Opposition Type": "moratorium", "State": "IA",
           "County": "Dubuque County", "Community Outcome": "win", "Status": "passed",
           "Date": "2026-05-26", "Source URL": "https://www.kcrg.com/x", "lat": "42.5", "lon": "-90.66",
           "Summary": "Supervisors adopted a 12-month moratorium on new data center construction in the county."}
    r = run([raw])
    expect(not r.verdicts[0].blocked, "raw CSV-shaped moratorium row (win + adopted) passes")
    expect(r.verdicts[0].brain == "moratorium", "raw row routed to moratorium")

    # capital sink, two-letter state, City of form
    tx = {"Incident": "x", "Opposition Type": "moratorium", "State": "TX", "City": "City of Austin",
          "Scope": "statewide", "Community Outcome": "win", "Status": "passed", "Date": "2026-01-01",
          "Source URL": "https://www.statesman.com/x", "Summary": "A statewide effort recorded against data centers." }
    r = run([tx])
    expect(any(i.code == "STATEWIDE_CAPITAL_SINK" for i in r.verdicts[0].issues),
           "statewide pinned to 'City of Austin' caught as capital sink")

    # enrichment: conditional ordinance is not a block, win on it is flagged as overstated
    linn = {"Name": "Linn County ordinance", "Opposition Type": "zoning_restriction", "State": "IA",
            "County": "Linn County", "Authority Level": "county_commission", "Community Outcome": "win",
            "Status": "passed", "Date": "2026-02-01", "Source URL": "https://insideclimatenews.org/x",
            "Summary": "The county approved a zoning ordinance with 1,000-foot setbacks, a water-use agreement, and noise and light limits."}
    enr = E.enrich_record(A.normalize_record(linn))
    expect(enr["qc_mechanism"] == "conditional_zoning" and enr["qc_is_block"] is False and enr["qc_highlight"],
           "conditional ordinance -> not a block, highlighted")
    r = run([linn])
    expect(any(i.code == "OUTCOME_OVERSTATED" for i in r.verdicts[0].issues) and not r.verdicts[0].blocked,
           "win on a conditional ordinance -> MEDIUM overstated, not blocked")
    expect("qc_mechanism" in r.clean[0], "enrichment fields attached to clean export")

    # county vs city with the same name are not merged
    pair = [
        {"Name": "Springfield moratorium", "Opposition Type": "moratorium", "State": "OH",
         "County": "Clark County", "Authority Level": "county_commission", "Community Outcome": "win",
         "Status": "passed", "Date": "2026-03-01", "Source URL": "https://www.reuters.com/a",
         "Notes": "The county adopted a moratorium on new data center construction across the county."},
        {"Name": "Springfield moratorium", "Opposition Type": "moratorium", "State": "OH",
         "City": "Springfield", "Authority Level": "city_council", "Community Outcome": "win",
         "Status": "passed", "Date": "2026-03-01", "Source URL": "https://www.reuters.com/b",
         "Notes": "The city council adopted a moratorium on new data center construction within city limits."}]
    r = run(pair)
    expect(not any(i.code == "DUPLICATE" for v in r.verdicts for i in v.issues),
           "same-name county and city actions are not flagged as duplicates")

    # placeability: a city alone (or a county sitting in the city field) is mappable
    r = run([{"Incident": "Prince William County", "City": "Prince William County",
              "Opposition Type": "moratorium", "State": "VA", "Date": "2026-02-01",
              "Source URL": "https://www.reuters.com/z", "Notes": "A data center moratorium was proposed."}])
    expect(not r.verdicts[0].blocked, "county name in the city field is placeable, not blocked")
    r = run([{"Incident": "Flint City Council passes 12-month moratorium on data centers",
              "Opposition Type": "moratorium", "Date": "2026-02-01",
              "Source URL": "https://www.mlive.com/z", "Notes": "City council passed it."}])
    expect(not r.verdicts[0].blocked and A.normalize_record(
               {"Incident": "Flint City Council passes 12-month moratorium on data centers"}).get("City") == "Flint",
           "headline with a city is recovered and placeable")
    r = run([{"Incident": "Most Americans Want a National Data Center Moratorium",
              "Opposition Type": "moratorium", "Date": "2026-02-01", "Source URL": "https://www.reuters.com/n",
              "Notes": "National poll."}])
    expect(any(i.code == "UNPLACEABLE" for i in r.verdicts[0].issues),
           "national headline with no place stays UNPLACEABLE")

    print("\nALL SELFTESTS PASS" if ok else "\nSOME SELFTESTS FAILED")
    return ok


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the master opposition QC gate.")
    parser.add_argument("--records", help="Path to a JSON array of records.")
    parser.add_argument("--csv", help="Path to a CSV file (converted in-process).")
    parser.add_argument("--out", default="qc_out")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    if args.csv:
        import csv
        records = list(csv.DictReader(open(args.csv, newline="", encoding="utf-8")))
    elif args.records:
        records = json.load(open(args.records, encoding="utf-8"))
    else:
        sys.exit("Provide --records records.json or --csv master_opposition.csv (or --selftest).")

    result = run(records)
    paths = write_outputs(result, args.out)
    print(render_markdown(result))
    print(f"\nWrote: {', '.join(paths.values())}")
    sys.exit(0)
