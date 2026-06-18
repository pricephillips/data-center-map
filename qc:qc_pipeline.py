"""
qc_pipeline.py

Dataset-wide QC gate for the master opposition database.

This is the single validity filter that every downstream consumer reads from
(the Iowa tracker is one of several). Only records that pass strict,
context-aware tests move forward into the clean export feed; anything with a
serious validity concern is quarantined so it cannot reach any downstream
product. The gate is multi-state and handles several record kinds, including
studies and reports that live in the opposition file as evidence rather than
as opposition events.

How it works
------------
Each record is ROUTED to a brain by what kind of source it is, then tested with
checks built for that kind on top of a universal baseline.

  legislative      bills and resolutions (delegates to legislative_outcome.py)
  moratorium       local-government actions: moratoria, ordinances, zoning
  project          a specific data center project or proposal
  public_comment   public meetings, comment periods, hearings
  study            studies, reports, polls, datasets, academic/think-tank work
  generic          fallback

Event brains (everything except study) additionally require a valid event
Outcome. Studies do not, because a report is not "Approved" or "Blocked"; it is
evidence, judged instead on source quality and attribution.

Block policy
------------
A record is blocked if any issue is at or above the threshold.
Default BLOCK_AT = {"CRITICAL", "HIGH"}. MEDIUM and LOW are reported, not blocked.
Change BLOCK_AT in one place to tune strictness.

Outputs (written to --out, default ./qc_out)
--------------------------------------------
clean_export.json   records that passed (the only thing downstream may read)
quarantine.json     blocked records with the reasons
qc_report.json      full machine-readable report
qc_report.md        human summary, also usable as a GitHub Actions step summary

Run
---
python qc_pipeline.py --records records.json --out qc_out
python qc_pipeline.py                # runs the built-in demo dataset
python qc_pipeline.py --selftest     # runs the assertion battery

Non-destructive: it withholds bad records from the feed and quarantines them.
It does not delete anything from the source database.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import legislative_outcome as L

# ===========================================================================
# Config
# ===========================================================================

SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
BLOCK_AT = {"CRITICAL", "HIGH"}

ALLOWED_OUTCOMES = L.CANONICAL_OUTCOMES                 # Approved/Blocked/Pending/Mixed
ALLOWED_STATUS = {"Active", "Pending", "Monitoring", "Resolved"}

# Reference data. State capitals are used to detect statewide activity that has
# been pinned to a capital and is inflating that city's count.
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

# Capital counties (secondary sink signal). City-name detection is primary and
# covers all states; this list need not be exhaustive to be useful.
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

# Approximate, generous per-state bounding boxes for portfolio states
# (lat_min, lat_max, lon_min, lon_max). Other states fall back to the US box.
STATE_BBOX = {
    "Iowa": (40.3, 43.6, -96.7, -90.1),
    "West Virginia": (37.1, 40.7, -82.7, -77.7),
    "Pennsylvania": (39.7, 42.3, -80.6, -74.6),
    "Kentucky": (36.4, 39.2, -89.6, -81.9),
    "Illinois": (36.9, 42.6, -91.6, -87.0),
    "Texas": (25.8, 36.6, -106.7, -93.5),
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
    "iowapublicradio.org", "northwestiowanow.com", "claytoncountyianews.com",
    "siteselection.com", "wpr.org", "nbcnews.com", "wired.com", "reuters.com",
    "apnews.com", "politico.com", "bloomberg.com", "nytimes.com", "washingtonpost.com",
    "wsj.com", "cnn.com", "npr.org", "datacenterwatch.org", "fractracker.org",
    "cbs2iowa.com", "dailyiowan.com",
)
RESEARCH_DOMAINS = (
    "doi.org", "sciencedirect.com", "springer.com", "nature.com", "jstor.org",
    "gallup.com", "pewresearch.org", "brookings.edu", "rand.org", "naco.org",
    "nrel.gov", "pnnl.gov", "lbl.gov", "columbia.edu", "oecd.org", "iea.org",
    "nber.org", "ssrn.com", "researchgate.net",
)
SHORTENERS = ("bit.ly", "t.co", "lnkd.in", "tinyurl.com", "ow.ly", "goo.gl", "buff.ly")
SEARCH_URLS = ("google.com/search", "bing.com/search", "duckduckgo.com")

ERROR_TOKENS = ("#ref!", "#n/a", "#value!", "#div/0!", "#name?", "#null!", "#num!")
NULL_ARTIFACTS = ("nan", "none", "null")

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
# Field access (tolerant of plain strings or Notion property dicts)
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
    """Return the record's state, or '' if not given. No default: this is a
    multi-state dataset, so guessing a state would corrupt geography checks."""
    return text(record, "State")


def city_of(record: dict) -> str:
    return text(record, "City", "Locality", "Municipality")


def is_statewide(record: dict) -> bool:
    blob = " ".join(text(record, k) for k in ("Scope", "City", "Name", "Title", "Geography")).lower()
    return "statewide" in blob or L.looks_legislative(record)


def coords_of(record: dict):
    """Return (lat, lon) floats, None if absent, or 'INVALID' if unparseable."""
    lat = text(record, "Latitude", "Lat")
    lon = text(record, "Longitude", "Lon", "Lng")
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
    """Return one of: gov, news, research, social, unknown, none."""
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
# Baseline checks (run for every record, every brain)
# ===========================================================================

def check_id_present(record: dict) -> list[Issue]:
    if not text(record, "ID", "Id", "Record ID"):
        return [Issue("MEDIUM", "ID_MISSING", "ID",
                      "No stable ID. Downstream joins key on ID; falling back to Name is fragile.")]
    return []


def check_source_valid(record: dict) -> list[Issue]:
    raw = text(record, "Source URL", "Source", "Sources")
    if not raw:
        return [Issue("HIGH", "SOURCE_MISSING", "Source URL", "No source URL on the record.")]
    if _STRINGIFIED_DICT_RE.match(raw):
        return [Issue("HIGH", "SOURCE_STRINGIFIED_DICT", "Source URL",
                      "Source URL is a stringified dict, e.g. \"{'url': 'https://...'}\". "
                      "Ingest wrote the object instead of the URL.")]
    if not _URL_RE.match(raw):
        return [Issue("HIGH", "SOURCE_MALFORMED", "Source URL",
                      f"Source URL is not a valid http(s) link: '{raw[:60]}'.")]
    d = domain_of(raw)
    if any(s == d or s in d for s in SHORTENERS):
        return [Issue("MEDIUM", "SOURCE_SHORTENER", "Source URL",
                      f"Source is a link shortener ({d}); store the resolved URL so it can be verified.")]
    if any(s in raw.lower() for s in SEARCH_URLS):
        return [Issue("MEDIUM", "SOURCE_SEARCH_PAGE", "Source URL",
                      "Source is a search-results page, not a primary source.")]
    return []


def check_status_vocabulary(record: dict) -> list[Issue]:
    raw = text(record, "Status")
    if raw and raw not in ALLOWED_STATUS:
        return [Issue("MEDIUM", "STATUS_VOCAB", "Status",
                      f"Status '{raw}' is not valid. Allowed: " + ", ".join(sorted(ALLOWED_STATUS)) + ".")]
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
    if year < 2015:
        return [Issue("MEDIUM", "DATE_STALE", "Date", f"Date '{raw}' predates 2015, likely a bad import.")]
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
        return [Issue("HIGH", "COORD_NULL_ISLAND", "Coordinates",
                      "Coordinates are (0, 0), the classic missing-geocode value.")]
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return [Issue("HIGH", "COORD_RANGE", "Coordinates",
                      f"Coordinates ({lat}, {lon}) are outside valid lat/lon ranges.")]
    if not _in_box(lat, lon, US_BBOX):
        if _in_box(lon, lat, US_BBOX):
            return [Issue("HIGH", "COORD_SWAPPED", "Coordinates",
                          f"Coordinates ({lat}, {lon}) look like lat/lon were swapped.")]
        return [Issue("HIGH", "COORD_OUTSIDE_US", "Coordinates",
                      f"Coordinates ({lat}, {lon}) fall outside the US.")]
    state = state_of(record)
    box = STATE_BBOX.get(state)
    if box and not _in_box(lat, lon, box):
        return [Issue("MEDIUM", "COORD_OUTSIDE_STATE", "Coordinates",
                      f"Coordinates ({lat}, {lon}) fall outside {state}'s bounding box.")]
    return []


BASELINE_CHECKS = [
    check_id_present,
    check_source_valid,
    check_status_vocabulary,
    check_date_sanity,
    check_notes_quality,
    check_field_artifacts,
    check_coordinates,
]


# ===========================================================================
# Shared event-outcome checks (every brain except study)
# ===========================================================================

def check_outcome_required_and_vocab(record: dict) -> list[Issue]:
    raw = text(record, "Outcome")
    if not raw:
        return [Issue("HIGH", "OUTCOME_MISSING", "Outcome", "Outcome is blank on an event record.")]
    if raw not in ALLOWED_OUTCOMES:
        return [Issue("HIGH", "OUTCOME_VOCAB", "Outcome",
                      f"Outcome '{raw}' is not valid. Allowed: " + ", ".join(sorted(ALLOWED_OUTCOMES)) + ".")]
    return []


def check_outcome_status_logic(record: dict) -> list[Issue]:
    outcome = L._normalize_recorded(text(record, "Outcome"))
    status = text(record, "Status")
    out: list[Issue] = []
    if outcome in {"Approved", "Blocked"} and status == "Active":
        out.append(Issue("MEDIUM", "OUTCOME_STATUS_LOGIC", "Status",
                         f"Outcome is '{outcome}' (resolved) but Status is still 'Active'."))
    if status == "Resolved" and text(record, "Outcome") in {"", "Unknown"}:
        out.append(Issue("MEDIUM", "OUTCOME_STATUS_LOGIC", "Outcome",
                         "Status is 'Resolved' but Outcome is blank or Unknown."))
    if status == "Resolved" and outcome == "Pending":
        out.append(Issue("MEDIUM", "OUTCOME_STATUS_LOGIC", "Outcome",
                         "Status is 'Resolved' but Outcome is still 'Pending'."))
    return out


EVENT_OUTCOME_CHECKS = [check_outcome_required_and_vocab, check_outcome_status_logic]


def check_mappable_coords(record: dict) -> list[Issue]:
    """A local, mappable event with a locality but no coordinates is a map gap."""
    if is_statewide(record):
        return []
    if (text(record, "County") or city_of(record)) and coords_of(record) is None:
        return [Issue("MEDIUM", "COORD_MISSING", "Coordinates",
                      "Local event has a locality but no coordinates; it cannot be pinned precisely.")]
    return []


# ===========================================================================
# Statewide / capital attribution
# ===========================================================================

def check_statewide_attribution(record: dict) -> list[Issue]:
    if not is_statewide(record):
        return []
    problems: list[str] = []
    county = text(record, "County")
    district = text(record, "Congressional District", "District")
    state = state_of(record)
    cap_city = STATE_CAPITALS.get(state, "")
    cap_county = CAPITAL_COUNTIES.get(state, "")
    city = city_of(record)

    capital_sink = False
    if cap_city and city and city.lower() == cap_city.lower():
        problems.append(f"pinned to capital city {city}")
        capital_sink = True
    if cap_county and county and county.lower() == cap_county.lower():
        problems.append(f"assigned the capital county {county}")
        capital_sink = True
    if county and county.lower() != "statewide" and (not cap_county or county.lower() != cap_county.lower()):
        problems.append(f"County = '{county}'")
    if district:
        problems.append(f"District = '{district}'")

    if not problems:
        return []
    code = "STATEWIDE_CAPITAL_SINK" if capital_sink else "STATEWIDE_GEO_CONFLICT"
    note = (" This statewide activity is being attributed to the capital and will overrepresent it "
            "on the map; plot as statewide with no city pin." if capital_sink
            else " A statewide record should not carry local geography.")
    return [Issue("HIGH", code, "Geography",
                  "Statewide or legislative record carries local geography: " + "; ".join(problems) + "." + note)]


# ===========================================================================
# Legislative brain
# ===========================================================================

def check_legislative_outcome(record: dict) -> list[Issue]:
    iss = L.evaluate_record(record)
    return [] if iss is None else [Issue(iss.severity, iss.code, "Outcome", iss.message)]


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
    if kind == "unknown":
        return [Issue("MEDIUM", "LEG_SOURCE_UNVERIFIED", "Source URL",
                      "Legislative source is an unrecognized domain; prefer legis.*.gov, legiscan, or a known outlet.")]
    return []


LEGISLATIVE_CHECKS = EVENT_OUTCOME_CHECKS + [
    check_legislative_outcome,
    check_statewide_attribution,
    check_bill_id_present,
    check_legislative_source,
]


# ===========================================================================
# Moratorium brain (local-government outcome engine)
# ===========================================================================
# Action-verb proximity to an outcome noun, not fixed phrases, so phrasing
# variants resolve and a shared verb is disambiguated by the noun it modifies:
# "approved" near "moratorium" enacts a restriction (Blocked); "approved" near
# "project" lets development proceed (Approved).

_RESTRICT_VERBS = ("adopted", "passed", "approved", "imposed", "enacted", "established",
                   "instituted", "extended", "voted for", "voted to impose", "voted to adopt",
                   "voted to extend", "put in place", "placed a")
_REJECT_VERBS = ("voted down", "rejected", "defeated", "voted against", "against",
                 "did not pass", "failed", "struck down", "declined")
_DENY_VERBS = ("denied", "rejected", "blocked", "turned down", "struck down", "declined")
_GRANT_VERBS = ("approved", "granted", "authorized", "cleared", "greenlit", "green-lit", "okayed")
_PENDING_CUES = ("considering", "will consider", "to consider", "under consideration",
                 "proposed", "drafting", "weighing", "tabled", "scheduled", "public hearing",
                 "public meeting", "set a hearing", "mulling")
_RESTRICTION_NOUNS = ("moratorium", "moratoria", "ordinance")
_PROJECT_NOUNS = ("project", "permit", "application", "rezoning")
_WINDOW = 60


@dataclass(frozen=True)
class MorMatch:
    outcome: str
    confidence: str
    matched_phrase: str
    note: str


def _verb_near(text_l: str, noun: str, verbs: tuple[str, ...]) -> str | None:
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
    if not notes:
        return None
    t = notes.lower()
    blocked = approved = None
    for noun in _RESTRICTION_NOUNS:
        approved = approved or _verb_near(t, noun, _REJECT_VERBS)
        blocked = blocked or _verb_near(t, noun, _RESTRICT_VERBS)
    for noun in _PROJECT_NOUNS:
        blocked = blocked or _verb_near(t, noun, _DENY_VERBS)
        approved = approved or _verb_near(t, noun, _GRANT_VERBS)
    if blocked and approved:
        return MorMatch("Mixed", "HIGH", f"{blocked} + {approved}",
                        "both a restriction and a rejection are present")
    if blocked:
        return MorMatch("Blocked", "HIGH", blocked, "")
    if approved:
        return MorMatch("Approved", "HIGH", approved, "")
    cue = next((c for c in _PENDING_CUES if c in t), None)
    if cue:
        return MorMatch("Pending", "HIGH", cue, "")
    return None


def _mor_severity(recorded: str, inferred: str) -> str | None:
    if recorded == inferred:
        return None
    if {recorded, inferred} == {"Approved", "Blocked"}:
        return "CRITICAL"
    if inferred == "Mixed":
        return None if recorded == "Mixed" else "MEDIUM"
    if recorded == "Mixed":
        return "MEDIUM"
    if recorded in {"Approved", "Blocked"} and inferred == "Pending":
        return "HIGH"
    if recorded == "Pending" and inferred in {"Approved", "Blocked"}:
        return "MEDIUM"
    return "MEDIUM"


def check_moratorium_outcome(record: dict) -> list[Issue]:
    match = infer_local_action(text(record, "Notes"))
    if match is None:
        return []
    recorded = L._normalize_recorded(text(record, "Outcome"))
    if recorded is None:
        return []
    sev = _mor_severity(recorded, match.outcome)
    if sev is None:
        return []
    extra = f" ({match.note})" if match.note else ""
    return [Issue(sev, "MORATORIUM_OUTCOME_CONFLICT", "Outcome",
                  f"Outcome conflict: recorded '{text(record, 'Outcome')}', but notes describe a local "
                  f"action that should be '{match.outcome}'{extra}. Matched on \"{match.matched_phrase}\".")]


def check_local_geography(record: dict) -> list[Issue]:
    if is_statewide(record):
        return [Issue("HIGH", "LOCAL_TAGGED_STATEWIDE", "Geography",
                      "A local moratorium/ordinance record is tagged statewide.")]
    if not text(record, "County"):
        return [Issue("HIGH", "LOCAL_NO_COUNTY", "County",
                      "Local action has no county; it cannot be placed on the map.")]
    return []


def check_local_source(record: dict) -> list[Issue]:
    kind = classify_source(text(record, "Source URL", "Source", "Sources"))
    if kind in ("social", "unknown"):
        return [Issue("MEDIUM", "LOCAL_SOURCE_WEAK", "Source URL",
                      "Local action is sourced to social or an unrecognized domain; prefer a county/city record or local news.")]
    return []


MORATORIUM_CHECKS = EVENT_OUTCOME_CHECKS + [
    check_moratorium_outcome,
    check_local_geography,
    check_local_source,
    check_mappable_coords,
]


# ===========================================================================
# Project brain
# ===========================================================================

def check_operator_present(record: dict) -> list[Issue]:
    blob = " ".join(text(record, k) for k in ("Name", "Title", "Operator", "Notes")).lower()
    if not any(op in blob for op in OPERATORS):
        return [Issue("MEDIUM", "PROJECT_NO_OPERATOR", "Operator", "Project record names no recognized operator.")]
    return []


def check_project_location(record: dict) -> list[Issue]:
    if not text(record, "County") and not city_of(record):
        return [Issue("HIGH", "PROJECT_NO_LOCATION", "County/City",
                      "Project record has no county or city; it cannot be placed.")]
    return []


def check_capacity_sanity(record: dict) -> list[Issue]:
    blob = " ".join(text(record, k) for k in ("Capacity", "Notes"))
    mw = re.search(r"(\d{1,5})\s*mw", blob, re.IGNORECASE)
    if mw and not (1 <= int(mw.group(1)) <= 5000):
        return [Issue("MEDIUM", "CAPACITY_IMPLAUSIBLE", "Capacity",
                      f"Capacity {mw.group(1)} MW is outside a plausible range (1-5000 MW).")]
    return []


PROJECT_CHECKS = EVENT_OUTCOME_CHECKS + [
    check_operator_present,
    check_project_location,
    check_capacity_sanity,
    check_mappable_coords,
]


# ===========================================================================
# Public comment brain
# ===========================================================================

def check_public_locality(record: dict) -> list[Issue]:
    if not text(record, "County") and not city_of(record):
        return [Issue("HIGH", "PUBLIC_NO_LOCATION", "County/City",
                      "Public meeting/comment record has no county or city.")]
    return []


PUBLIC_COMMENT_CHECKS = EVENT_OUTCOME_CHECKS + [check_public_locality, check_mappable_coords]


# ===========================================================================
# Study / report brain
# ===========================================================================
# Studies and reports are evidence, not events. They are not "Approved" or
# "Blocked"; they are judged on source quality, attribution, and date.

def check_study_source(record: dict) -> list[Issue]:
    kind = classify_source(text(record, "Source URL", "Source", "Sources"))
    if kind == "social":
        return [Issue("HIGH", "STUDY_SOURCE_WEAK", "Source URL",
                      "A study/report is sourced only to social media; cite the publication directly.")]
    if kind == "unknown":
        return [Issue("MEDIUM", "STUDY_SOURCE_UNVERIFIED", "Source URL",
                      "Study/report source is an unrecognized domain; prefer the publisher, a journal, .edu, .gov, or a known outlet.")]
    return []


def check_study_attribution(record: dict) -> list[Issue]:
    has_attr = any(text(record, k) for k in ("Author", "Authors", "Publisher", "Organization", "Source Name"))
    kind = classify_source(text(record, "Source URL", "Source", "Sources"))
    if not has_attr and kind in ("unknown", "none"):
        return [Issue("MEDIUM", "STUDY_NO_ATTRIBUTION", "Author/Publisher",
                      "Study/report has no author or publisher and no identifiable source domain.")]
    return []


def check_study_outcome_misuse(record: dict) -> list[Issue]:
    raw = text(record, "Outcome")
    if raw in {"Approved", "Blocked"}:
        return [Issue("MEDIUM", "STUDY_EVENT_OUTCOME", "Outcome",
                      f"A study/report is tagged with the event outcome '{raw}'. A report is not approved or "
                      "blocked; leave Outcome blank or mark it informational.")]
    if raw and raw not in ALLOWED_OUTCOMES:
        return [Issue("MEDIUM", "OUTCOME_VOCAB", "Outcome",
                      f"Outcome '{raw}' is not a recognized value.")]
    return []


STUDY_CHECKS = [check_study_source, check_study_attribution, check_study_outcome_misuse]

GENERIC_CHECKS = list(EVENT_OUTCOME_CHECKS)


# ===========================================================================
# Router
# ===========================================================================

BRAINS = {
    "legislative": LEGISLATIVE_CHECKS,
    "moratorium": MORATORIUM_CHECKS,
    "project": PROJECT_CHECKS,
    "public_comment": PUBLIC_COMMENT_CHECKS,
    "study": STUDY_CHECKS,
    "generic": GENERIC_CHECKS,
}

_TYPE_HINTS = [
    ("legislative", ("legislat", "bill", "resolution")),
    ("study", ("study", "report", "poll", "survey", "research", "paper", "dataset",
               "database", "index", "guidebook", "whitepaper", "white paper", "brief", "analysis")),
    ("moratorium", ("moratorium", "ordinance", "zoning", "restriction", "local")),
    ("public_comment", ("public comment", "public meeting", "hearing", "listening")),
    ("project", ("project", "proposal", "facility", "permit", "campus")),
]

_SIGNALS = {
    "study": ("study", "report", "survey", "poll", "peer-reviewed", "working paper",
              "dataset", "white paper", "guidebook", "findings", "analysis published"),
    "moratorium": ("moratorium", "ordinance", "rezoning", "setback", "board of supervisors",
                   "supervisors voted", "city council", "county board", "unified development"),
    "project": ("data center", "campus", "facility", "broke ground", "megawatt", " mw",
                "gigawatt", "investment", "colocation", "proposed"),
    "public_comment": ("public comment", "public meeting", "public hearing", "listening session",
                       "comment period"),
}


def route(record: dict) -> str:
    """Pick the brain. Order: bill ID, explicit type, study-by-source, then signal scoring."""
    if L.looks_legislative(record):
        return "legislative"

    opp_type = text(record, "Opposition Type", "Type", "Category").lower()
    for brain, hints in _TYPE_HINTS:
        if any(h in opp_type for h in hints):
            return brain

    # A research-grade source with no event signals is almost always a study.
    if classify_source(text(record, "Source URL", "Source", "Sources")) == "research":
        return "study"

    blob = " ".join(text(record, k) for k in ("Name", "Title", "Notes", "Operator")).lower()
    scores = {b: sum(1 for s in sig if s in blob) for b, sig in _SIGNALS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "generic"


# ===========================================================================
# Dataset-level checks (operate across all records, key by index)
# ===========================================================================

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def check_duplicate_ids(records: list[dict]):
    """Two records sharing an explicit ID break downstream keying."""
    by_id: dict[str, list[int]] = defaultdict(list)
    for i, rec in enumerate(records):
        rid = text(rec, "ID", "Id", "Record ID")
        if rid:
            by_id[rid].append(i)
    for rid, idxs in by_id.items():
        if len(idxs) > 1:
            for i in idxs:
                yield i, Issue("CRITICAL", "DUPLICATE_ID", "ID",
                               f"ID '{rid}' is used by {len(idxs)} records. IDs must be unique.")


def check_content_duplicates(records: list[dict]):
    """Likely duplicate events. Legislative records key on name+date (county is
    irrelevant for a statewide bill); others key on name+county+date."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, rec in enumerate(records):
        name, date = _norm(record_name(rec)), _norm(text(rec, "Date"))
        if L.looks_legislative(rec):
            key = ("leg", name, date)
        else:
            key = ("loc", name, _norm(text(rec, "County")), date)
        groups[key].append(i)
    for idxs in groups.values():
        if len(idxs) > 1:
            ids = [record_id(records[i]) for i in idxs]
            for i in idxs:
                others = [x for x in ids if x != record_id(records[i])]
                yield i, Issue("HIGH", "DUPLICATE", "Name/Date",
                               f"Possible duplicate of: {', '.join(others)}. Held out of the feed until merged.")


def check_capital_overrepresentation(records: list[dict]):
    """Quantify how much statewide activity is inflating capital-city pins."""
    by_capital: dict[tuple, dict] = defaultdict(lambda: {"total": 0, "statewide": 0, "ids": []})
    for rec in records:
        state, cap, city = state_of(rec), STATE_CAPITALS.get(state_of(rec)), city_of(rec)
        if cap and city and city.lower() == cap.lower():
            e = by_capital[(state, cap)]
            e["total"] += 1
            if is_statewide(rec):
                e["statewide"] += 1
                e["ids"].append(record_id(rec))
    for (state, cap), e in by_capital.items():
        if e["statewide"] > 0:
            share = round(100 * e["statewide"] / e["total"]) if e["total"] else 0
            yield "__dataset__", Issue(
                "MEDIUM", "CAPITAL_OVERREP", "City",
                f"{cap}, {state}: {e['total']} pinned records, {e['statewide']} ({share}%) are statewide/"
                f"legislative activities not specific to {cap} ({', '.join(e['ids'])}). They inflate "
                f"{cap}'s share of the map. Genuinely local {cap} records: {e['total'] - e['statewide']}. "
                "Reclassify the statewide ones as statewide with no city pin.")


DATASET_CHECKS = [check_duplicate_ids, check_content_duplicates, check_capital_overrepresentation]


# ===========================================================================
# The gate (index-keyed to avoid record-ID collisions)
# ===========================================================================

def _max_severity(issues: list[Issue]) -> str | None:
    return max((i.severity for i in issues), key=lambda s: SEVERITY_RANK.get(s, -1), default=None)


def run(records: list[dict], dataset_checks=None, block_at: set[str] = BLOCK_AT) -> PipelineResult:
    dataset_checks = dataset_checks or DATASET_CHECKS
    issues_by_idx: dict[int, list[Issue]] = defaultdict(list)
    brain_by_idx: dict[int, str] = {}

    for i, rec in enumerate(records):
        brain = route(rec)
        brain_by_idx[i] = brain
        for chk in BASELINE_CHECKS + BRAINS[brain]:
            issues_by_idx[i].extend(chk(rec) or [])

    dataset_findings: list[Issue] = []
    for dchk in dataset_checks:
        for target, iss in dchk(records):
            if target == "__dataset__":
                dataset_findings.append(iss)
            else:
                issues_by_idx[int(target)].append(iss)

    verdicts, clean, quarantine = [], [], []
    for i, rec in enumerate(records):
        issues = issues_by_idx.get(i, [])
        max_sev = _max_severity(issues)
        blocked = max_sev in block_at
        verdicts.append(RecordVerdict(record_id(rec), record_name(rec), brain_by_idx[i],
                                      max_sev, blocked, issues))
        if blocked:
            quarantine.append({"record": rec, "brain": brain_by_idx[i],
                               "reasons": [asdict(x) for x in issues if x.severity in block_at],
                               "all_issues": [asdict(x) for x in issues]})
        else:
            clean.append(rec)
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
    with open(paths["clean"], "w", encoding="utf-8") as fh:
        json.dump(result.clean, fh, indent=2)
    with open(paths["quarantine"], "w", encoding="utf-8") as fh:
        json.dump(result.quarantine, fh, indent=2)
    report = {"summary": {"total": result.n_total, "clean": len(result.clean),
                          "blocked": result.n_blocked, "block_threshold": sorted(BLOCK_AT)},
              "dataset_findings": [asdict(i) for i in result.dataset_findings],
              "verdicts": [{"record_id": v.record_id, "name": v.name, "brain": v.brain,
                            "max_severity": v.max_severity, "blocked": v.blocked,
                            "issues": [asdict(i) for i in v.issues]} for v in result.verdicts]}
    with open(paths["report_json"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    with open(paths["report_md"], "w", encoding="utf-8") as fh:
        fh.write(render_markdown(result))
    return paths


def render_markdown(result: PipelineResult) -> str:
    lines = ["# Master Opposition QC Report", "",
             f"- Records scanned: **{result.n_total}**",
             f"- Passed to feed: **{len(result.clean)}**",
             f"- Blocked / quarantined: **{result.n_blocked}**",
             f"- Block threshold: {', '.join(sorted(BLOCK_AT))}", ""]
    if result.dataset_findings:
        lines += ["## Dataset findings", ""]
        lines += [f"- **{i.severity} {i.code}**: {i.message}" for i in result.dataset_findings] + [""]

    def table(rows, title, bold):
        out = [f"## {title}", "", "| Record | Brain | Severity | Issues |", "| :-- | :-- | :-- | :-- |"]
        for v in rows:
            shown = [x for x in v.issues if (x.severity in BLOCK_AT) or not bold]
            cells = "<br>".join((f"**{x.severity}** " if bold else f"{x.severity} ") + f"{x.code}: {x.message}"
                                for x in shown)
            out.append(f"| {v.name} | {v.brain} | {v.max_severity} | {cells} |")
        return out + [""]

    blocked = [v for v in result.verdicts if v.blocked]
    if blocked:
        lines += table(blocked, "Blocked records", bold=True)
    warn = [v for v in result.verdicts if not v.blocked and v.issues]
    if warn:
        lines += table(warn, "Passed with warnings (not blocking)", bold=False)
    passed = [v for v in result.verdicts if not v.blocked and not v.issues]
    if passed:
        lines += ["## Passed clean", ""] + [f"- {v.name} ({v.brain})" for v in passed] + [""]
    return "\n".join(lines)


# ===========================================================================
# Demo dataset (covers every brain and many failure modes)
# ===========================================================================

def _demo_records() -> list[dict]:
    return [
        {"ID": "IA-13", "Name": "HF 2690", "Opposition Type": "Legislation", "State": "Iowa",
         "City": "Des Moines", "Outcome": "Approved", "Status": "Resolved", "Date": "2026-02-23",
         "Source URL": "https://www.legis.iowa.gov/legislation/BillBook?ba=HF2690",
         "Notes": "Passed committee Feb 23, 2026. Iowa adjourned sine die May 3, 2026 without passage."},
        {"ID": "IA-10", "Name": "HF 2690", "Opposition Type": "Legislation", "State": "Iowa",
         "City": "Des Moines", "County": "Marshall", "Congressional District": "IA-02",
         "Outcome": "Approved", "Status": "Resolved", "Date": "2026-02-23",
         "Source URL": "https://www.legis.iowa.gov/legislation/BillBook?ba=HF2690",
         "Notes": "Statewide data center transparency bill. Passed committee, did not become law."},
        {"ID": "IA-17", "Name": "Dubuque County moratorium", "Opposition Type": "Local moratorium",
         "State": "Iowa", "County": "Dubuque", "Outcome": "Approved", "Status": "Resolved",
         "Date": "2026-05-26", "Latitude": "42.50", "Longitude": "-90.66",
         "Source URL": "https://www.kcrg.com/2026/05/26/dubuque-county-passes-12-month-moratorium-data-centers/",
         "Notes": "Supervisors adopted a 12-month moratorium after a developer approached landowners and contacted the city."},
        {"ID": "IA-50", "Name": "Clinton County / city split", "Opposition Type": "Ordinance",
         "State": "Iowa", "County": "Clinton", "Outcome": "Blocked", "Status": "Resolved",
         "Date": "2026-06-10", "Latitude": "41.84", "Longitude": "-90.19",
         "Source URL": "https://www.wvik.org/wvik-top-stories/2026-06-10/clinton-city-council-vote-down-data-center-moratorium",
         "Notes": "Clinton County adopted a moratorium linking wind and data mining; the city council voted 5-2 against a city moratorium."},
        {"ID": "IA-22", "Name": "Linn County ordinance", "Opposition Type": "Ordinance",
         "State": "Iowa", "County": "Linn", "Outcome": "Blocked", "Status": "Resolved", "Date": "2026-02-19",
         "Source URL": "{'url': 'https://www.linncountyiowa.gov/CivicAlerts.aspx?AID=4324'}",
         "Notes": "County adopted a detailed data center ordinance with water study requirements and setbacks."},
        {"ID": "IA-60", "Name": "Adair County proposal", "Opposition Type": "Project", "State": "Iowa",
         "County": "Adair", "Outcome": "Pending", "Status": "Active", "Date": "2024-04-15",
         "Latitude": "41.33", "Longitude": "-94.47", "Source URL": "https://cleanview.co/data-centers/iowa",
         "Notes": "Applied Digital proposed a roughly 200 MW, $1.5B data center campus; zoning framework is incomplete."},
        {"ID": "IA-70", "Name": "Unnamed proposal", "Opposition Type": "Project", "State": "Iowa",
         "Outcome": "Pending", "Status": "Active", "Date": "2026-03-01",
         "Source URL": "https://example-blog.test/post", "Notes": "A facility is rumored somewhere in the state."},
        {"ID": "STUDY-1", "Name": "Sabin Center opposition database", "Opposition Type": "Study", "State": "Iowa",
         "Outcome": "", "Status": "Monitoring", "Date": "2026-01-15",
         "Source URL": "https://climate.law.columbia.edu/content/opposition-renewable-energy-facilities",
         "Notes": "Academic database tracking coordinated opposition to renewable and infrastructure siting nationwide."},
        {"ID": "STUDY-2", "Name": "Gallup data center poll", "Opposition Type": "Poll", "State": "Iowa",
         "Outcome": "Blocked", "Status": "Monitoring", "Date": "2026-02-01",
         "Source URL": "https://www.facebook.com/groups/saynotodatacenters/posts/999/",
         "Notes": "Poll on public attitudes toward nearby data centers."},
        {"ID": "TX-1", "Name": "Travis County moratorium", "Opposition Type": "Local moratorium", "State": "Texas",
         "City": "Austin", "Scope": "statewide", "Outcome": "Blocked", "Status": "Resolved", "Date": "2026-04-01",
         "Source URL": "https://www.statesman.com/story/data-center-moratorium",
         "Notes": "A statewide advocacy effort recorded against data centers, mistakenly pinned to the capital."},
        {"ID": "BAD-1", "Name": "#REF!", "Opposition Type": "Project", "State": "Iowa", "County": "Polk",
         "Outcome": "Pending", "Status": "Active", "Date": "2027-09-01",
         "Latitude": "0", "Longitude": "0", "Source URL": "https://bit.ly/3xyzabc",
         "Notes": "Broken import row with a future date and null-island coordinates."},
        {"ID": "IA-30", "Name": "Johnson County moratorium", "Opposition Type": "Local moratorium", "State": "Iowa",
         "County": "Johnson", "Outcome": "Blocked", "Status": "Resolved", "Date": "2025-11-07",
         "Latitude": "41.67", "Longitude": "-91.58",
         "Source URL": "https://www.radioiowa.com/2025/11/07/temporary-moratorium-on-data-centers-in-eastern-iowa-county/",
         "Notes": "Adopted a moratorium after drafting UDO language for setbacks and related controls."},
    ]


# ===========================================================================
# Self-test battery (integration check)
# ===========================================================================

def selftest() -> bool:
    ok = True

    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    # Routing
    expect(route({"Name": "HF 2690", "Opposition Type": "Legislation"}) == "legislative", "route legislative")
    expect(route({"Name": "Dubuque moratorium", "Opposition Type": "Local moratorium",
                  "Notes": "adopted a moratorium"}) == "moratorium", "route moratorium")
    expect(route({"Name": "Gallup poll", "Opposition Type": "Poll"}) == "study", "route study by type")
    expect(route({"Name": "x", "Source URL": "https://climate.law.columbia.edu/x",
                  "Notes": "report"}) == "study", "route study by research source")
    expect(route({"Name": "Google campus", "Opposition Type": "Project",
                  "Notes": "google data center campus"}) == "project", "route project")

    # Study brain: a study is NOT forced to have an event outcome.
    study = {"ID": "S", "Name": "Sabin db", "Opposition Type": "Study", "Outcome": "",
             "Date": "2026-01-01", "Source URL": "https://climate.law.columbia.edu/x",
             "Notes": "A national database tracking coordinated opposition to siting projects."}
    r = run([study])
    expect(not r.verdicts[0].blocked, "study with blank outcome passes (not an event)")

    # Event record with blank outcome IS blocked.
    ev = {"ID": "E", "Name": "Some county vote", "Opposition Type": "Local moratorium", "Outcome": "",
          "County": "X", "Date": "2026-01-01", "Source URL": "https://www.kcrg.com/x",
          "Notes": "The county board met to discuss a proposed data center for forty-plus minutes."}
    r = run([ev])
    expect(r.verdicts[0].blocked, "event with blank outcome is blocked")

    # Duplicate ID -> CRITICAL on both.
    dup = [{"ID": "D", "Name": "A", "Opposition Type": "Project", "Outcome": "Pending", "County": "X",
            "Date": "2026-01-01", "Source URL": "https://www.kcrg.com/a", "Notes": "x" * 50},
           {"ID": "D", "Name": "B", "Opposition Type": "Project", "Outcome": "Pending", "County": "Y",
            "Date": "2026-01-02", "Source URL": "https://www.kcrg.com/b", "Notes": "y" * 50}]
    r = run(dup)
    expect(all(v.max_severity == "CRITICAL" for v in r.verdicts), "duplicate ID flagged CRITICAL on both")

    # Coordinate checks
    def coord_issue(rec):
        return [i.code for i in check_coordinates(rec)]
    expect("COORD_NULL_ISLAND" in coord_issue({"Latitude": "0", "Longitude": "0"}), "null island caught")
    expect("COORD_SWAPPED" in coord_issue({"Latitude": "-74.0", "Longitude": "40.7", "State": "New York"}),
           "swapped lat/lon caught")
    expect("COORD_OUTSIDE_STATE" in coord_issue({"Latitude": "30.0", "Longitude": "-95.0", "State": "Iowa"}),
           "outside-state coordinates caught")
    expect(coord_issue({"Latitude": "41.6", "Longitude": "-93.6", "State": "Iowa"}) == [],
           "valid Iowa coordinates pass")

    # Import artifact
    expect(any(i.code == "FIELD_ERROR_TOKEN" for i in check_field_artifacts({"County": "#N/A"})),
           "spreadsheet error token caught")

    # Capital sink, multi-state (Texas)
    tx = {"ID": "T", "Name": "x", "Opposition Type": "Local moratorium", "State": "Texas",
          "City": "Austin", "Scope": "statewide", "Outcome": "Blocked", "County": "",
          "Date": "2026-01-01", "Source URL": "https://www.reuters.com/x", "Notes": "z" * 50}
    expect(any(i.code == "STATEWIDE_CAPITAL_SINK" for i in check_statewide_attribution(tx)),
           "capital sink caught for a non-Iowa state")

    # Legislative classifier still correct on the HF2690 trap
    leg = {"ID": "L", "Name": "HF 2690", "Opposition Type": "Legislation", "Outcome": "Approved",
           "Date": "2026-02-23", "Source URL": "https://www.legis.iowa.gov/x",
           "Notes": "Passed committee but Iowa adjourned sine die without passage."}
    r = run([leg])
    expect(any(i.code == "OUTCOME_CONFLICT" and i.severity == "CRITICAL" for i in r.verdicts[0].issues),
           "HF2690 sine die trap still CRITICAL")

    print("\nALL SELFTESTS PASS" if ok else "\nSOME SELFTESTS FAILED")
    return ok


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the master opposition QC gate.")
    parser.add_argument("--records", help="Path to a JSON array of records. Omit to run the demo.")
    parser.add_argument("--out", default="qc_out", help="Output directory.")
    parser.add_argument("--selftest", action="store_true", help="Run the assertion battery and exit.")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    records = json.load(open(args.records, encoding="utf-8")) if args.records else _demo_records()
    result = run(records)
    paths = write_outputs(result, args.out)
    print(render_markdown(result))
    print(f"\nWrote: {', '.join(paths.values())}")
    sys.exit(1 if result.n_blocked else 0)
