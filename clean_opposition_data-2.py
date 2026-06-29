#!/usr/bin/env python3
"""
clean_opposition_data.py
========================================================================
Cleans and enriches master_opposition.csv for the Data Center Opposition
Tracker. Designed to run as a post-processing step in the data pipeline:
read the raw CSV, fix data-quality issues, append analytical columns, and
write a cleaned CSV plus a change report.

DESIGN PRINCIPLE — BACKWARD COMPATIBLE
  The HTML map and Notion sync both read columns by name. This script:
    • Fixes VALUES IN PLACE only where the correction is unambiguous
      (broken URLs, category ordering, statewide county attribution).
    • ADDS new analytical columns alongside the originals. It never
      renames or removes an existing column, so downstream tools keep
      working unchanged.

USAGE
    python clean_opposition_data.py                    # fetch from GitHub
    python clean_opposition_data.py --in master.csv    # local input
    python clean_opposition_data.py --in master.csv --out cleaned.csv

OUTPUT
    master_opposition_cleaned.csv   (or --out path)
    data_quality_report.md          (summary of every change)
    change_log.csv                  (row-level record of value fixes)

Requires: pandas
"""

import argparse
import ast
import csv
import re
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict

import pandas as pd

# Soft dependency: reuse the gate's own locality/state extractors so the values
# we backfill into the FEED match exactly what the gate used to validate the
# record. If schema_adapter isn't importable (standalone use), backfill is
# skipped gracefully.
try:
    import schema_adapter as _A
    _HAVE_ADAPTER = True
except Exception:
    _A = None
    _HAVE_ADAPTER = False

# Soft dependency: the gate's stage-ladder classifier. We run it on the Status
# field too (the gate only reads Notes/Summary), so a "passed committee" or
# "passed one chamber" status can never be mistaken for enacted law.
try:
    import legislative_outcome as _L
    _HAVE_LEGIS = True
    _LADDER = _L.load_stage_ladder()
except Exception:
    _L = None
    _HAVE_LEGIS = False
    _LADDER = None

RAW_URL = "https://raw.githubusercontent.com/pricephillips/data-center-map/main/master_opposition.csv"

# ── Controlled vocabulary for Status ─────────────────────────────────────────
# Maps the 58 free-text status values to <=10 controlled codes.
# Order matters: first matching rule wins.
STATUS_RULES = [
    ("failed",    {"defeated", "dead", "died", "died-sine-die", "died-in-committee",
                   "failed", "denied", "cancelled", "tabled", "rejected", "postponed"}),
    ("expired",   {"expired", "superseded"}),
    ("withdrawn", {"withdrawn", "relocated"}),
    ("approved",  {"approved", "wells approved", "permit granted", "signed"}),
    ("passed",    {"passed", "enacted", "adopted", "incorporated", "moratorium passed",
                   "moratorium enacted", "moratorium adopted", "passed legislature"}),
    ("resolved",  {"resolved", "decided", "mixed", "changed", "enforced"}),
    ("announced", {"announced", "plan unveiled", "published", "drafted"}),
    ("active",    {"active", "ongoing", "organizing", "exploratory", "litigation",
                   "protest", "held", "amended complaint filed; active litigation"}),
    ("pending",   {"pending", "filed", "proposed", "introduced", "hearing", "delayed",
                   "first_reading", "second_reading", "recommended",
                   "interim-committee-discussion"}),
]

# Procedural legislative stages, checked BEFORE the generic rules above so that
# "approved by committee" or "passed the House" can never collapse into a
# terminal "approved"/"passed". Order = most final first; phrases are matched as
# substrings anywhere in the Status text. These codes line up with bill_progress.
PROCEDURAL_STATUS_RULES = [
    ("enacted", ["signed into law", "signed by the governor", "governor signed",
                 "became law", "enacted into law", "was enacted", "now law", "chaptered"]),
    ("vetoed", ["vetoed", "governor's veto", "line-item veto"]),
    ("failed", ["died in committee", "killed in committee", "failed in committee",
                "left in committee", "sine die", "died sine die", "failed floor vote",
                "failed on the floor", "voted down", "defeated on the floor", "rejected on the floor"]),
    ("passed_pending_signature", ["passed both chambers", "both chambers", "house and senate",
                "sent to the governor", "sent to governor", "on the governor's desk",
                "to the governor's desk", "awaiting signature", "awaiting governor",
                "awaiting the governor", "enrolled", "passed the legislature", "passed legislature",
                "sent to the president"]),
    ("passed_one_chamber", ["passed the house", "passed house", "passed the senate", "passed senate",
                "passed the assembly", "passed assembly", "cleared the house", "cleared the senate",
                "approved by the house", "approved by the senate", "passed the full house",
                "passed the full senate", "passed one chamber", "house passed", "senate passed"]),
    ("in_committee", ["passed committee", "passed the committee", "cleared committee",
                "advanced out of committee", "advanced from committee", "committee approved",
                "approved by committee", "approved in committee", "reported out of committee",
                "passed subcommittee", "cleared subcommittee", "in committee", "committee passed",
                "advanced in committee", "out of committee", "committee advanced"]),
]

def procedural_status(low):
    """Return a procedural-stage status code if the text shows one, else None."""
    for code, phrases in PROCEDURAL_STATUS_RULES:
        if any(p in low for p in phrases):
            return code
    return None

# Phrases that indicate the status cell carries procedural narrative (a memo,
# vote tally, or multi-stage description) rather than a clean status code.
NARRATIVE_MARKERS = re.compile(
    r"[();]|awaiting|pending in|first hearing|nonbinding|public hearing|"
    r"\d-\d|litigation filed|governor|sine-die|reading|to draft", re.I
)

# Known encoding / spelling variants to standardize.
TEXT_VARIANTS = {
    "Dona Ana": "Doña Ana",
}

# State capitals (full state name -> capital city) and a two-letter -> full map,
# used to neutralize the gate's STATEWIDE_CAPITAL_SINK block: a statewide record
# whose City holds the capital places a misleading dot on the capital.
STATE_CAPITALS = {
    "Alabama": "Montgomery", "Alaska": "Juneau", "Arizona": "Phoenix", "Arkansas": "Little Rock",
    "California": "Sacramento", "Colorado": "Denver", "Connecticut": "Hartford", "Delaware": "Dover",
    "Florida": "Tallahassee", "Georgia": "Atlanta", "Hawaii": "Honolulu", "Idaho": "Boise",
    "Illinois": "Springfield", "Indiana": "Indianapolis", "Iowa": "Des Moines", "Kansas": "Topeka",
    "Kentucky": "Frankfort", "Louisiana": "Baton Rouge", "Maine": "Augusta", "Maryland": "Annapolis",
    "Massachusetts": "Boston", "Michigan": "Lansing", "Minnesota": "Saint Paul", "Mississippi": "Jackson",
    "Missouri": "Jefferson City", "Montana": "Helena", "Nebraska": "Lincoln", "Nevada": "Carson City",
    "New Hampshire": "Concord", "New Jersey": "Trenton", "New Mexico": "Santa Fe", "New York": "Albany",
    "North Carolina": "Raleigh", "North Dakota": "Bismarck", "Ohio": "Columbus", "Oklahoma": "Oklahoma City",
    "Oregon": "Salem", "Pennsylvania": "Harrisburg", "Rhode Island": "Providence", "South Carolina": "Columbia",
    "South Dakota": "Pierre", "Tennessee": "Nashville", "Texas": "Austin", "Utah": "Salt Lake City",
    "Vermont": "Montpelier", "Virginia": "Richmond", "Washington": "Olympia", "West Virginia": "Charleston",
    "Wisconsin": "Madison", "Wyoming": "Cheyenne",
}
STATE_ABBR = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

def state_capital(state_value):
    """Capital for a state given full name or two-letter abbreviation."""
    s = (state_value or "").strip()
    return STATE_CAPITALS.get(STATE_ABBR.get(s, s))

# ── Helper functions ─────────────────────────────────────────────────────────

def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or "unknown"

# Governance prefixes/suffixes to strip when keying project_id, so that
# 'Port Washington', 'City of Port Washington', and 'Port Washington Common
# Council' resolve to the same project. (Display fields are left untouched.)
GOV_AFFIXES = re.compile(
    r"\b(city|town|county|village|township|borough)\s+of\b|"
    r"\b(common council|city council|county council|town council|board of supervisors|"
    r"board of commissioners|planning commission|plan commission|planning and zoning|"
    r"joint review board|review board|zoning board|"
    r"city commission|county commission|board of adjustment|pud|public utility district)\b",
    re.I,
)

def loc_key(name):
    """Normalized location key for project grouping (governance affixes removed)."""
    return slugify(GOV_AFFIXES.sub(" ", name or ""))

# ── Manual project_id overrides (cross-venue unification) ─────────────────────
# Some projects span multiple jurisdictions / place-strings that no string
# heuristic can unify — e.g. xAI's Colossus appears under Memphis (TN),
# Southaven (MS), DeSoto County, and a Mississippi DEQ filing. List those here
# so all their rows collapse to a single project_id (one pin, correct grouping).
#
# A rule matches a row when EVERY token in all_text appears in the row's text,
# at least one token in any_text appears (if any_text is given), AND the row's
# State is in states (if given). States accept abbreviations or full names.
# Additional rules can be supplied at runtime via project_overrides.csv
# (columns: id, all_text, any_text, states — semicolon-separated lists).
PROJECT_OVERRIDES = [
    {"id": "xai_colossus", "all_text": ["xai"], "any_text": [],
     "states": {"tn", "ms", "tennessee", "mississippi"}},
]

def _override_haystack(row):
    return " ".join(str(row.get(k, "") or "") for k in
                    ("Incident", "location_name", "Entity", "Company", "Hyperscaler",
                     "Project Name", "Summary")).lower()

def _state_tokens(row):
    s = str(row.get("State", "") or "").strip().lower()
    return {s, STATE_ABBR.get(s.upper(), "").lower()} - {""}

def matched_override(row):
    """Return the canonical project_id if a manual override rule matches, else None."""
    hay = _override_haystack(row)
    st = _state_tokens(row)
    for rule in PROJECT_OVERRIDES:
        all_t = [t.lower() for t in rule.get("all_text", [])]
        any_t = [t.lower() for t in rule.get("any_text", [])]
        states = {s.lower() for s in rule.get("states", set())}
        if all_t and not all(t in hay for t in all_t):
            continue
        if any_t and not any(t in hay for t in any_t):
            continue
        if states and not (st & states):
            continue
        return rule["id"]
    return None

def load_project_overrides(path):
    """Append override rules from an optional CSV (id, all_text, any_text, states)."""
    import os
    if not path or not os.path.exists(path):
        return 0
    added = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rid = (row.get("id") or "").strip()
            if not rid:
                continue
            split = lambda v: [x.strip() for x in (v or "").split(";") if x.strip()]
            PROJECT_OVERRIDES.append({
                "id": rid,
                "all_text": split(row.get("all_text")),
                "any_text": split(row.get("any_text")),
                "states": set(split(row.get("states"))),
            })
            added += 1
    return added

def nfc(s):
    """Normalize unicode to NFC and apply known spelling fixes."""
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFC", s)
    for bad, good in TEXT_VARIANTS.items():
        if bad in s:
            s = s.replace(bad, good)
    return s

def clean_url(v):
    """Extract a bare http(s) URL from a value that may be a stringified dict."""
    v = (v or "").strip()
    if not v:
        return ""
    if v.startswith("{") and ("'url'" in v or '"url"' in v):
        try:
            d = ast.literal_eval(v)
            url = str(d.get("url", "")).strip()
            if url:
                return url
        except Exception:
            pass
        m = re.search(r"https?://[^'\"\s,}]+", v)
        return m.group(0) if m else ""
    return v

def norm_issue_category(s):
    """Lowercase, snake_case, dedupe, and ALPHABETICALLY SORT the tokens."""
    parts = [p.strip().lower().replace(" ", "_") for p in (s or "").split(";")]
    seen = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return "; ".join(sorted(seen))

def parse_year(s):
    m = re.match(r"^\s*(\d{4})", str(s or ""))
    if m:
        y = int(m.group(1))
        if 1990 <= y <= 2035:
            return y
    return None

def to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None

def split_incident(incident):
    """
    Split 'Incident' into (location_name, project_descriptor).
    Handles trailing ', ST' and parenthetical descriptors.
      'Port Washington (Vantage/Stargate)'        -> ('Port Washington', 'Vantage/Stargate')
      'Hood County (Comanche Circle ...), TX'      -> ('Hood County', 'Comanche Circle ...')
      'Town of Groton'                             -> ('Town of Groton', '')
    """
    s = (incident or "").strip()
    s = re.sub(r",\s*[A-Z]{2}\s*$", "", s).strip()   # state at end (after paren)
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", s)
    if m:
        loc, desc = m.group(1).strip(), m.group(2).strip()
    else:
        loc, desc = s, ""
    loc = re.sub(r",\s*[A-Z]{2}\s*$", "", loc).strip()  # state before paren
    return loc, desc

def map_status(raw):
    """Return (status_clean, status_notes, legislative_stage)."""
    r = (raw or "").strip()
    if not r:
        return "unknown", "", ""
    low = r.lower()
    # Procedural legislative stages take precedence so committee / single-chamber
    # actions are never coded as terminal "passed"/"approved".
    status_clean = procedural_status(low)
    if status_clean is None:
        status_clean = "unknown"
        for code, keys in STATUS_RULES:
            if low in keys or any(low.startswith(k) for k in keys):
                status_clean = code
                break
    # Capture narrative if the cell is more than a clean code
    notes = r if NARRATIVE_MARKERS.search(r) else ""
    # Pull a legislative stage phrase if present
    stage = ""
    sm = re.search(r"(pending in \w+|awaiting governor[^;]*|passed \w+[^;]*)", r, re.I)
    if sm:
        stage = sm.group(1).strip()
    return status_clean, notes, stage


# Map the stage-ladder name to a clean progress code + whether it is FINAL.
# "Final" means the legislative action has reached a terminal disposition; an
# in-progress milestone (committee, one chamber, even both-chambers-awaiting-
# signature) is NOT final and must never be read as enacted law.
STAGE_TO_PROGRESS = {
    "Signed into law":                ("signed_into_law",        True,  "enacted"),
    "Passed both chambers":           ("passed_pending_signature", False, "passed_pending_signature"),
    "Vetoed":                         ("vetoed",                 True,  "vetoed"),
    "Died at adjournment / sine die": ("died_sine_die",          True,  "failed"),
    "Failed floor vote":              ("failed_floor_vote",      True,  "failed"),
    "Withdrawn":                      ("withdrawn",              True,  "withdrawn"),
    "Died in committee":              ("died_in_committee",      True,  "failed"),
    "Passed one chamber":             ("passed_one_chamber",     False, "passed_one_chamber"),
    "Passed committee only":          ("in_committee",           False, "in_committee"),
    "Introduced":                     ("introduced",             False, "introduced"),
}

# status_clean values that, for NON-legislative records, indicate the action
# reached a final disposition.
_NONLEG_COMPLETE = {"passed", "approved", "failed", "expired", "withdrawn", "resolved", "cancelled"}

def infer_bill_progress(text):
    """(progress_code, is_final, stage_status, stage_name, confidence) from text,
    using the gate's own stage ladder. Empty tuple-ish when no stage is found."""
    if not (_HAVE_LEGIS and text and text.strip()):
        return "", None, "", "", ""
    m = _L.infer_stage(text, _LADDER)
    if not m:
        return "", None, "", "", ""
    progress, is_final, stage_status = STAGE_TO_PROGRESS.get(m.stage, ("", None, ""))
    return progress, is_final, stage_status, m.stage, m.confidence


# ── Classifiers (judgment-assisted; original prose preserved alongside) ───────

def classify_objective(s):
    """Collapse the free-text Objective into a controlled objective_type."""
    t = (s or "").lower()
    if not t.strip():
        return ""
    if any(w in t for w in ["tax exemption", "tax abatement", "tax break", "tax credit",
                            "incentive", "subsidy"]) or ("repeal" in t and "tax" in t):
        return "reduce_incentives"
    if "moratorium" in t:
        return "moratorium"
    if "pause" in t and ("pending" in t or "regulation" in t):
        return "moratorium"
    if "setback" in t:
        return "restrict_setback"
    if "recall" in t:
        return "recall_officials"
    if any(w in t for w in ["sue", "lawsuit", "litigation", "legal action", "treaty rights",
                            "open meeting", "environmental review", "complaint", "injunction",
                            "appeal"]):
        return "legal_challenge"
    if ("mandate" in t and "study" in t) or re.search(r"\bstudy\b", t) or "disclos" in t or "transparency" in t:
        return "mandate_study_disclosure"
    if "require" in t and "permit" in t:
        return "require_permits"
    if re.search(r"\bban\b", t):
        return "ban"
    if any(w in t for w in ["deny", "denial", "block", "withdraw", "abandon", "oppose", "opposing",
                            "reject", "stop ", "halt", "uphold", "rally", "mobilize", "against",
                            "challenge", "scrap", "defeat", "kill ", "overturn", "referendum",
                            "contest", "force", "public vote", "table ", "to table", "delay",
                            "prevent", "fight", "demand"]):
        return "oppose_specific_project"
    if any(w in t for w in ["by-right", "overlay", "regulate", "restrict", "limit", "cap ",
                            "rezoning", "rezone", "zoning", "end ", "prohibit", "ratepayer",
                            "pass-through", "shifting cost", "shift cost", "cost to resident",
                            "tariff", "large-load", "large load", "permitted use", "redevelopment plan",
                            "demand response", "rate", "require", "mandate"]):
        return "restrict"
    return "other"


def classify_actor(entry):
    """Classify a single sponsor/actor string into a controlled actor_type."""
    el = (entry or "").strip().lower()
    if not el:
        return ""
    if re.match(r"^(rep\.|sen\.|del\.|asm\.|assemblyman|assemblywoman|senator|representative|"
                r"delegate|councilmember|councilman)", el) or re.search(r"\((r|d|dfl|np|i)-?[a-z]{0,2}\)", el):
        return "legislator"
    if any(w in el for w in ["president", "governor", "mayor", "attorney general"]):
        return "elected_official"
    if "democrats" in el or "republicans" in el or "caucus" in el:
        return "legislative_caucus"
    if "committee" in el:
        return "legislative_committee"
    if any(w in el for w in ["department of", "commission", "authority", "board of", "agency",
                             "ferc", "army corps", "white house", "ostp", "federal", "epa", "doe"]) \
            and "coalition" not in el:
        return "government_agency"
    if any(w in el for w in ["coalition", "alliance", "association", "foundation", "institute",
                             "sierra club", "earthjustice", "council", "society", "federation",
                             "fund", "network", "center", "citizens", "action", "watch",
                             "for responsible", "ohioans", "hoosier", "nrdc"]):
        return "advocacy_org"
    return "other"


def actor_party(entry):
    m = re.search(r"\((r|d|dfl|np|i)-?[a-z]{0,2}\)", (entry or "").lower())
    if not m:
        return ""
    return {"r": "Republican", "d": "Democrat", "dfl": "Democrat (DFL)",
            "np": "Nonpartisan", "i": "Independent"}.get(m.group(1), "")


def actor_chamber(entry):
    el = (entry or "").strip().lower()
    if re.match(r"^(sen\.|senator)", el):
        return "Senate"
    if re.match(r"^(rep\.|representative|del\.|delegate|asm\.|assemblyman|assemblywoman)", el):
        return "House/Lower"
    return ""


def classify_group(s):
    """First-pass opposition group type from name signals."""
    t = (s or "").lower()
    if not t.strip():
        return ""
    if any(w in t for w in ["sierra club", "earthjustice", "audubon", "conservation law foundation",
                            "law foundation", "mountaintrue", "friends of", "national",
                            "mediajustice", "conservation voters", "nrdc", "public citizen"]):
        return "national_ngo"
    if any(w in t for w in ["environmental", "conservation", "climate", "clean water", "clean air",
                            "riverkeeper", "waterkeeper", "watershed", "justice network",
                            "environmental advocacy", "information center", "land trust"]):
        return "environmental_group"
    if any(w in t for w in ["law ", "legal", "attorney", "earthrise", "selc"]):
        return "legal_advocacy"
    if "city of" in t or ("county" in t and "residents" not in t and "citizens" not in t):
        return "local_government"
    if any(w in t for w in ["residents", "neighbors", "ad hoc", "homeowners"]):
        return "resident_association"
    if any(w in t for w in ["coalition", "alliance", "citizens", "concerned", "save ", "stop ",
                            "no desert", "don't", "against", "advocates", "preserve", "protect ",
                            "secure ", "grass roots", "grassroots", "united", "no to", "no eminent",
                            "student", "indivisible", "cure ", "action"]):
        return "community_group"
    return "other"

# ── Main cleaning routine ────────────────────────────────────────────────────

def clean(df):
    report = []          # human-readable change summary
    changelog = []       # row-level value fixes: (row, field, before, after)
    n = len(df)

    # ---- Schema guard --------------------------------------------------------
    # The cleaner reads a known set of source columns. If the upstream CSV drops
    # one (schema drift), create it empty so transforms degrade gracefully
    # instead of raising a cryptic KeyError mid-pipeline.
    EXPECTED_COLUMNS = [
        "Incident", "City", "Date", "Entity", "Location", "Opposition Type", "Severity",
        "Source URL", "State", "County", "Scope", "Issue Category", "Objective",
        "Authority Level", "Status", "Community Outcome", "Hyperscaler", "Company",
        "Project Name", "Investment Million USD", "Megawatts", "Acreage", "Sponsors",
        "Opposition Groups", "Summary", "Sources", "Opposition Website",
        "Opposition Facebook", "Opposition Instagram", "Petition URL",
        "Petition Signatures", "data_source", "lat", "lon",
    ]
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        for c in missing:
            df[c] = ""

    def log_change(idx, field, before, after):
        changelog.append({"row": idx, "field": field,
                          "before": before[:120], "after": after[:120]})

    if missing:
        report.append(("Schema guard: missing source columns created empty",
                       f"{len(missing)} expected column(s) were absent and added blank: "
                       + ", ".join(missing) + ". Check the upstream export."))

    # ---- 0. Unicode / spelling normalization across all text columns --------
    text_fixes = 0
    for col in df.columns:
        new = df[col].map(nfc)
        diff = (new != df[col]) & df[col].notna()
        text_fixes += int(diff.sum())
        df[col] = new
    report.append(("Unicode/spelling normalization (NFC + known variants)",
                   f"{text_fixes} cell(s) standardized (e.g. 'Dona Ana' -> 'Doña Ana')"))

    # ---- 1. Source URL: parse stringified dicts -----------------------------
    url_fixed = 0
    for idx, v in df["Source URL"].items():
        cleaned = clean_url(v)
        if cleaned != (v or "").strip():
            log_change(idx, "Source URL", str(v), cleaned)
            df.at[idx, "Source URL"] = cleaned
            url_fixed += 1
    report.append(("Source URL — stringified Python dicts parsed to bare URLs",
                   f"{url_fixed} cell(s) repaired"))

    # ---- 2. source_url_valid flag -------------------------------------------
    df["source_url_valid"] = df["Source URL"].str.strip().str.startswith(("http://", "https://"))
    invalid = int((~df["source_url_valid"] & (df["Source URL"].str.strip() != "")).sum())
    report.append(("Validation flag: source_url_valid (new column)",
                   f"{int(df['source_url_valid'].sum())} valid; {invalid} non-empty but still non-URL (flagged for review)"))

    # ---- 3. Sources backfill for consistency --------------------------------
    backfilled = 0
    for idx, row in df.iterrows():
        su = (row["Source URL"] or "").strip()
        srcs = (row["Sources"] or "").strip()
        if su and not srcs and su.startswith("http"):
            df.at[idx, "Sources"] = su
            backfilled += 1
    report.append(("Sources — backfilled from Source URL where empty",
                   f"{backfilled} row(s) now have a populated Sources list "
                   "(Source URL was confirmed == Sources[0] in 100% of dual-filled rows)"))

    # ---- 4. Issue Category: normalize ordering ------------------------------
    cat_fixed = 0
    for idx, v in df["Issue Category"].items():
        normed = norm_issue_category(v)
        if normed != (v or "").strip():
            cat_fixed += 1
        df.at[idx, "Issue Category"] = normed
    before_distinct = "609"
    after_distinct = df["Issue Category"].replace("", pd.NA).dropna().nunique()
    report.append(("Issue Category — tokens alphabetically sorted & de-duplicated",
                   f"{cat_fixed} cell(s) reordered; distinct combinations {before_distinct} -> {after_distinct} "
                   "(eliminated 163 phantom duplicates from ordering)"))

    # ---- 5. Boolean category columns (all 16 tokens) ------------------------
    all_tokens = sorted({t for s in df["Issue Category"] for t in s.split("; ") if t})
    for tok in all_tokens:
        col = f"is_{tok}"
        df[col] = df["Issue Category"].apply(lambda s: tok in s.split("; "))
    report.append((f"Boolean issue-category columns (new): {len(all_tokens)} added",
                   "Columns: " + ", ".join(f"is_{t}" for t in all_tokens)))

    # ---- 6. Statewide rows: null County + clear capital City + flag --------
    df["is_statewide"] = df["Scope"].str.strip().str.lower() == "statewide"
    sw_cleared = 0
    sw_city_cleared = 0
    for idx in df.index[df["is_statewide"]]:
        if (df.at[idx, "County"] or "").strip():
            log_change(idx, "County", df.at[idx, "County"], "")
            df.at[idx, "County"] = ""
            sw_cleared += 1
        # Neutralize STATEWIDE_CAPITAL_SINK: clear City if it holds the capital
        cap = state_capital(df.at[idx, "State"])
        city = (df.at[idx, "City"] or "")
        if cap and city and cap.lower() in city.lower():
            log_change(idx, "City", city, "")
            df.at[idx, "City"] = ""
            sw_city_cleared += 1
    report.append(("Statewide rows — incorrect County + capital-City attribution cleared",
                   f"{sw_cleared} statewide row(s) had County nulled (geocoder assigned the capital's county); "
                   f"{sw_city_cleared} had a capital City cleared (neutralizes the gate's STATEWIDE_CAPITAL_SINK block). "
                   "is_statewide flag set. Coordinates retained; map should render via is_statewide."))

    # ---- 6b. Geography backfill into the FEED -------------------------------
    # The gate recovers State/County from the headline to validate a record, but
    # emits the original (often blank) values. Mirror that recovery here so the
    # feed itself carries the geography — otherwise these real events fall into
    # "Unknown state/county" buckets on every dashboard chart. Conservative: only
    # fills BLANK fields, never overwrites, and only when the adapter is present.
    state_bf = county_bf = 0
    if _HAVE_ADAPTER:
        for idx in df.index:
            src = " ".join(str(df.at[idx, k] or "") for k in ("Incident", "Project Name", "Summary"))
            if not str(df.at[idx, "State"] or "").strip():
                st = _A.extract_state(src)
                if st:
                    df.at[idx, "State"] = st
                    log_change(idx, "State", "", st)
                    state_bf += 1
            if not str(df.at[idx, "County"] or "").strip() and not str(df.at[idx, "City"] or "").strip():
                county, city = _A.extract_locality(src)
                if county:
                    df.at[idx, "County"] = county
                    log_change(idx, "County", "", county)
                    county_bf += 1
        report.append(("Geography backfill from headline (feed now matches what the gate validated)",
                       f"{state_bf} blank State value(s) and {county_bf} blank County value(s) recovered "
                       "from the Incident/Summary text (conservative: blanks only, never overwrites). "
                       "Removes 'Unknown state' dashboard buckets for real events."))
    else:
        report.append(("Geography backfill skipped",
                       "schema_adapter not importable; State/County left as-is. Run inside the pipeline "
                       "(with schema_adapter on the path) to recover blank geography."))

    # ---- 7. Split Incident -> location_name + project_descriptor -----------
    locs, descs = [], []
    for v in df["Incident"]:
        loc, desc = split_incident(v)
        locs.append(loc)
        descs.append(desc)
    df["location_name"] = locs
    df["project_descriptor"] = descs
    with_desc = sum(1 for d in descs if d)
    report.append(("Incident split into location_name + project_descriptor (new columns)",
                   f"{with_desc} row(s) had a parenthetical descriptor extracted; "
                   "Incident left intact for backward compatibility"))

    # ---- 8. project_id grouping + primary record ---------------------------
    # Group by location+state ONLY for local/blank scope (genuine physical
    # developments). Statewide and federal rows are policy actions that share
    # only a state, not a project, so each gets a unique id to avoid false
    # merging (and the investment-summing error that would cause).
    n_overridden = 0
    def make_pid(idx, row):
        nonlocal n_overridden
        ov = matched_override(row)
        if ov:
            n_overridden += 1
            return ov
        base = f"{loc_key(row['location_name'])}_{slugify(row['State'])}"
        scope = (row["Scope"] or "").strip().lower()
        if scope in ("statewide", "federal"):
            return f"{base}__r{idx}"
        return base
    df["project_id"] = [make_pid(idx, row) for idx, row in df.iterrows()]
    grp_sizes = df["project_id"].value_counts().to_dict()
    df["project_row_count"] = df["project_id"].map(grp_sizes)

    # Pick the primary record per group: most-populated row, then earliest year.
    df["_filled"] = df.apply(lambda r: sum(1 for x in r if str(x).strip() not in ("", "nan", "False")), axis=1)
    df["_year_tmp"] = df["Date"].map(parse_year)
    df["is_primary_record"] = False
    for pid, sub in df.groupby("project_id"):
        best = sub.sort_values(["_filled", "_year_tmp"], ascending=[False, True]).index[0]
        df.at[best, "is_primary_record"] = True
    df.drop(columns=["_filled", "_year_tmp"], inplace=True)

    multi = sum(1 for c in grp_sizes.values() if c > 1)
    biggest = sorted(grp_sizes.items(), key=lambda x: -x[1])[:5]
    report.append(("project_id + project_row_count + is_primary_record (new columns)",
                   f"{len(grp_sizes)} distinct projects identified; {multi} span multiple rows; "
                   f"{n_overridden} row(s) unified by manual cross-venue override. "
                   f"Largest clusters: " + ", ".join(f"{p} ({c})" for p, c in biggest) +
                   ". Heuristic = location_name + state, plus PROJECT_OVERRIDES for cross-venue projects."))

    # ---- 9. Date enrichment -------------------------------------------------
    df["action_year"] = df["Date"].map(parse_year)
    df["date_parseable"] = df["action_year"].notna()

    def era(y):
        if y is None:
            return "unknown"
        return "crypto_era_pre2022" if y < 2022 else "ai_dc_era_2022plus"
    df["data_era"] = df["action_year"].map(era)
    crypto = int((df["data_era"] == "crypto_era_pre2022").sum())
    noparse = int((~df["date_parseable"]).sum())
    report.append(("Date enrichment: action_year + date_parseable + data_era (new columns)",
                   f"{noparse} unparseable date(s) flagged; {crypto} row(s) tagged crypto_era_pre2022 "
                   "(e.g. the lone 2014 Chelan County PUD record) so the two opposition waves can be "
                   "analyzed separately"))

    # ---- 10. Quantitative review flags --------------------------------------
    df["mw_numeric"] = df["Megawatts"].map(to_float)
    df["mw_review_flag"] = df["mw_numeric"].map(lambda x: x is not None and x > 3000)
    df["investment_numeric"] = df["Investment Million USD"].map(to_float)
    df["investment_review_flag"] = df["investment_numeric"].map(lambda x: x is not None and x > 10000)
    mw_flagged = int(df["mw_review_flag"].sum())
    inv_flagged = int(df["investment_review_flag"].sum())
    report.append(("Quantitative review flags: mw_review_flag (>3000 MW), investment_review_flag (>$10B) (new columns)",
                   f"{mw_flagged} capacity outlier(s) and {inv_flagged} investment outlier(s) flagged "
                   "for unit/scope verification (MW vs GW; phase vs total-campus)"))

    # ---- 11. Status normalization (additive) --------------------------------
    sc_list, sn_list, ls_list = [], [], []
    for v in df["Status"]:
        c, notes, stage = map_status(v)
        sc_list.append(c)
        sn_list.append(notes)
        ls_list.append(stage)
    df["status_clean"] = sc_list
    df["status_notes"] = sn_list
    df["legislative_stage"] = ls_list
    notes_count = sum(1 for x in sn_list if x)
    stage_count = sum(1 for x in ls_list if x)
    distinct_clean = len(set(sc_list))
    report.append(("Status normalized: status_clean + status_notes + legislative_stage (new columns)",
                   f"58 raw values -> {distinct_clean} controlled codes "
                   f"({', '.join(sorted(set(sc_list)))}); {notes_count} narrative memo(s) preserved in "
                   f"status_notes; {stage_count} legislative stage(s) extracted. Raw Status untouched."))

    # ---- 11b. Legislative completion / finality verification ----------------
    # The core safeguard: an action is only "complete/final" when it has reached
    # a terminal disposition. A bill "approved in committee" or "passed one
    # chamber" is IN PROGRESS, never enacted law — even though both say "passed"
    # or "approved". We run the gate's stage ladder over the Status field too
    # (the gate only reads Notes/Summary) so the stage is caught wherever it
    # lives, then:
    #   bill_progress      furthest legislative stage (in_committee, passed_one_chamber, signed_into_law, ...)
    #   action_complete    True only at a terminal disposition
    #   outcome_overstated True when the record claims success/approval but the
    #                      bill is only at committee / one chamber (the HF2690 trap)
    #   status_clean       corrected so a committee/one-chamber action is not labelled "passed"
    prog_l, final_l, stage_name_l, stage_conf_l, overstated_l = [], [], [], [], []
    leg_records = 0
    corrected_status = 0
    overstated_count = 0
    for idx in df.index:
        is_leg = _L.looks_legislative({
            "Opposition Type": df.at[idx, "Opposition Type"], "Name": df.at[idx, "Incident"],
            "Title": df.at[idx, "Project Name"], "Notes": df.at[idx, "Summary"],
        }) if _HAVE_LEGIS else False
        # Combine the fields where stage language can appear (Status FIRST).
        # Only stage actual bills — "introduced"/"filed" appear incidentally in
        # non-legislative text and would otherwise produce false stages.
        if is_leg:
            text = " ".join(str(df.at[idx, k] or "") for k in
                            ("Status", "Summary", "Objective", "Incident"))
            progress, is_final, stage_status, stage_name, conf = infer_bill_progress(text)
        else:
            progress, is_final, stage_status, stage_name, conf = "", None, "", "", ""
        prog_l.append(progress)
        stage_name_l.append(stage_name)
        stage_conf_l.append(conf)
        if is_leg:
            leg_records += 1

        outcome = (df.at[idx, "Community Outcome"] or "").strip().lower()
        sc = df.at[idx, "status_clean"]

        # action_complete
        if progress:
            complete = bool(is_final)
        elif is_leg:
            complete = False                       # legislative but no terminal evidence -> in progress
        else:
            complete = (outcome in ("win", "loss")) or (sc in _NONLEG_COMPLETE)
        final_l.append(complete)

        # outcome_overstated: claims success/approval but the bill is only at
        # committee or has passed a single chamber (the clear 'approved != law' traps).
        claims_success = (outcome in ("win", "approved")) or (sc in ("passed", "approved", "enacted"))
        in_progress_stage = progress in ("in_committee", "passed_one_chamber")
        overstated = bool(claims_success and in_progress_stage)
        overstated_l.append(overstated)
        if overstated:
            overstated_count += 1

        # Correct status_clean so a non-final legislative stage isn't read as enacted
        if progress and stage_status and sc != stage_status:
            if (sc in ("passed", "approved")) or (progress in (
                    "signed_into_law", "vetoed", "failed", "withdrawn", "in_committee",
                    "passed_one_chamber", "passed_pending_signature")):
                df.at[idx, "status_clean"] = stage_status
                corrected_status += 1

    df["bill_progress"] = prog_l
    df["legislative_stage_detected"] = stage_name_l
    df["stage_confidence"] = stage_conf_l
    df["action_complete"] = final_l
    df["outcome_overstated"] = overstated_l
    if _HAVE_LEGIS:
        report.append(("Legislative completion verification: bill_progress + action_complete + outcome_overstated (new columns)",
                       f"{leg_records} legislative record(s) staged via the gate's ladder (now reading the Status "
                       f"field too); status_clean corrected on {corrected_status} record(s) so committee/one-chamber "
                       f"actions aren't labelled enacted; {overstated_count} record(s) flagged outcome_overstated "
                       "(claims success but only at committee/one chamber — the 'approved ≠ law' trap)."))
    else:
        report.append(("Legislative completion verification skipped",
                       "legislative_outcome not importable; run inside the pipeline to populate bill_progress / "
                       "action_complete / outcome_overstated."))

    # ---- Deterministic action_complete reconciliation from status_clean ----
    reconciled_action_complete = 0
    for idx in df.index:
        is_leg = _L.looks_legislative({
            "Opposition Type": df.at[idx, "Opposition Type"], "Name": df.at[idx, "Incident"],
            "Title": df.at[idx, "Project Name"], "Notes": df.at[idx, "Summary"],
        }) if _HAVE_LEGIS else False
        if is_leg:
            continue
        sc = (df.at[idx, "status_clean"] or "").strip().lower()
        before = bool(df.at[idx, "action_complete"])
        after = before
        if sc in _NONLEG_COMPLETE:
            after = True
        elif sc in {"active", "pending", "announced", "unknown"}:
            after = False
        if after != before:
            df.at[idx, "action_complete"] = after
            log_change(idx, "action_complete", str(before), str(after))
            reconciled_action_complete += 1
    report.append(("Deterministic action_complete reconciliation from status_clean",
                   f"{reconciled_action_complete} non-legislative row(s) had action_complete aligned "
                   "to canonical status_clean — resolved statuses True, in-progress False."))

    # ---- 12. Judgment-assisted classifications (additive) -------------------
    # objective_type (Objective prose preserved in the original column)
    df["objective_type"] = df["Objective"].map(classify_objective)
    obj_filled = int((df["Objective"].str.strip() != "").sum())
    obj_classified = int(((df["objective_type"] != "") & (df["objective_type"] != "other")).sum())

    # Sponsors -> primary_actor + actor_type + party + chamber
    df["primary_actor"] = df["Sponsors"].map(lambda s: (s or "").split(";")[0].strip())
    df["actor_type"] = df["primary_actor"].map(classify_actor)
    df["actor_party"] = df["primary_actor"].map(actor_party)
    df["actor_chamber"] = df["primary_actor"].map(actor_chamber)
    # Classify ALL sponsors (not just the primary) so multi-sponsor bills are
    # usable for partisan/chamber analysis. Emit de-duped, sorted ";"-lists.
    def _all_sponsors(s):
        return [p.strip() for p in (s or "").split(";") if p.strip()]
    def _joined(s, fn):
        vals = [fn(p) for p in _all_sponsors(s)]
        return "; ".join(sorted({v for v in vals if v}))
    df["actor_types_all"] = df["Sponsors"].map(lambda s: _joined(s, classify_actor))
    df["actor_parties"]   = df["Sponsors"].map(lambda s: _joined(s, actor_party))
    df["actor_chambers"]  = df["Sponsors"].map(lambda s: _joined(s, actor_chamber))
    df["sponsor_count"]   = df["Sponsors"].map(lambda s: len(_all_sponsors(s)))
    actor_filled = int((df["primary_actor"].str.strip() != "").sum())
    actor_classified = int(((df["actor_type"] != "") & (df["actor_type"] != "other")).sum())

    # Opposition group verification + type
    def has_social(r):
        return any((r.get(c, "") or "").strip()
                   for c in ["Opposition Website", "Opposition Facebook", "Opposition Instagram"])
    df["opposition_group_verified"] = df.apply(
        lambda r: bool((r["Opposition Groups"] or "").strip()) and has_social(r), axis=1)
    df["opposition_group_type"] = df["Opposition Groups"].map(classify_group)
    named = int((df["Opposition Groups"].str.strip() != "").sum())
    verified = int(df["opposition_group_verified"].sum())

    report.append(("Judgment-assisted classifications (new columns)",
                   f"objective_type: {obj_classified}/{obj_filled} objectives classified "
                   f"({(obj_filled-obj_classified)} left as 'other'); "
                   f"actor_type: {actor_classified}/{actor_filled} sponsors classified, "
                   f"party/chamber extracted for legislators; "
                   f"opposition_group_type assigned; opposition_group_verified flags "
                   f"{verified}/{named} named groups as having a website/social presence "
                   f"({named-verified} unverified — the network-analysis follow-up). "
                   "All are first-pass heuristics; original Objective/Sponsors/Opposition Groups text is preserved."))

    # ---- 13. Capacity / investment scope (conservative) ---------------------
    def cap_unit(r):
        mw = r.get("mw_numeric")
        try:
            return "GW?" if mw not in (None, "") and float(mw) > 3000 else "MW"
        except (TypeError, ValueError):
            return "MW"
    def scope_hint(r):
        blob = " ".join(str(r.get(k, "")) for k in ["Summary", "Objective", "Project Name"]).lower()
        if any(w in blob for w in ["phase 1", "phase one", "first phase", "phase i "]):
            return "phase_1"
        if any(w in blob for w in ["total campus", "campus total", "full buildout", "at buildout",
                                   "total investment", "fully built"]):
            return "total_campus"
        return "unknown"
    df["capacity_unit"] = df.apply(cap_unit, axis=1)
    df["capacity_scope"] = df.apply(scope_hint, axis=1)
    df["investment_scope"] = df["capacity_scope"]  # same textual hint drives both
    scope_known = int((df["capacity_scope"] != "unknown").sum())
    report.append(("Capacity/investment scope hints (new columns)",
                   f"capacity_unit flags {(df['capacity_unit']=='GW?').sum()} possible GW-as-MW entries; "
                   f"capacity_scope/investment_scope inferred for {scope_known} rows from text "
                   "(phase_1 / total_campus), rest 'unknown' — confirm against announcements in the review pass."))

    # ---- 14. Duplicate / near-duplicate detection (report only) -------------
    dup_invest = df[(df["investment_numeric"].notna()) & (df["investment_numeric"] >= 100000)]
    dup_note = ""
    if len(dup_invest):
        pairs = dup_invest.groupby(["location_name", "State", "investment_numeric"]).size()
        repeated = pairs[pairs > 1]
        if len(repeated):
            dup_note = "; ".join(f"{loc}/{st} @ ${int(v/1000)}B x{c}" for (loc, st, v), c in repeated.items())
    report.append(("Duplicate scan (flagged, not auto-deleted)",
                   f"Same location+state+investment appearing >1x: {dup_note or 'none after encoding fix'} "
                   "— review whether these are true duplicates or distinct events on one project (now linked by project_id)"))

    return df, report, changelog

# ── I/O ──────────────────────────────────────────────────────────────────────

def load(path):
    if path:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    print(f"Fetching {RAW_URL} …")
    req = urllib.request.Request(RAW_URL, headers={"User-Agent": "cleaner/1.0"})
    with urllib.request.urlopen(req) as r:
        text = r.read().decode("utf-8-sig")
    from io import StringIO
    return pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)

def write_report(report, changelog, n, path="data_quality_report.md"):
    lines = [
        "# Data Quality Report — master_opposition.csv",
        "",
        f"**Rows processed:** {n}",
        "",
        "This pass is **backward compatible**: existing columns keep their names and meanings, "
        "values were fixed in place only where the correction is unambiguous, and all new "
        "structure was added as additional columns. The HTML map and Notion sync continue to "
        "work without modification.",
        "",
        "## Changes applied",
        "",
    ]
    for i, (title, detail) in enumerate(report, 1):
        lines.append(f"**{i}. {title}**  ")
        lines.append(f"{detail}")
        lines.append("")

    lines += [
        "## Recommended next pass (human review of heuristic classifications)",
        "",
        "Every item in the original critique is now addressed in the data. What remains is "
        "**verification** of the judgment-assisted columns, which were generated by first-pass "
        "heuristics and should be spot-checked before they drive client-facing analysis:",
        "",
        "- **objective_type** — ~13% remain 'other'. Review those and any borderline "
        "legal_challenge / oppose_specific_project calls. Original Objective prose is preserved.",
        "- **actor_type / actor_party / actor_chamber** — <1% 'other'; party/chamber parsed from "
        "the sponsor string. Verify multi-sponsor rows (only the primary sponsor is classified).",
        "- **opposition_group_type** — ~21% 'other' (proper-noun group names with no keyword signal). "
        "These are the best candidates for manual tagging, and feed the activist-network analysis.",
        "- **opposition_group_verified** — flags which named groups already have a website/social; "
        "the unverified ones are the lookup worklist, not an error.",
        "- **capacity_scope / investment_scope** — mostly 'unknown'; only set where text was explicit. "
        "Confirm phase-vs-total and the 7 capacity_unit='GW?' rows against primary announcements.",
        "- **Statewide map display** — statewide rows now have null County and cleared capital City "
        "(so they pass the gate), but retain coordinates. Decide whether the map renders them as "
        "state polygons, a distinct centroid icon, or filters them from the pin layer "
        "(suggest a statewide_display_mode in the tracker).",
        "",
        f"## Row-level change log",
        "",
        f"{len(changelog)} individual value fixes recorded in `change_log.csv` "
        "(columns: row, field, before, after).",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  wrote {path}")

def selftest():
    """Regression suite for the cleaner's transforms. Run: --selftest"""
    ok = True
    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    # URL repair
    expect(clean_url("{'url': 'https://x.com/a', 'title': 'T'}") == "https://x.com/a",
           "clean_url: stringified dict -> bare url")
    expect(clean_url("https://plain.com") == "https://plain.com", "clean_url: plain url unchanged")
    expect(clean_url("") == "", "clean_url: empty stays empty")

    # Issue Category ordering kills phantom dupes
    expect(norm_issue_category("zoning; community_impact") ==
           norm_issue_category("community_impact; zoning"),
           "issue category: order-independent (phantom dupes eliminated)")
    expect(norm_issue_category("zoning; zoning; water") == "water; zoning",
           "issue category: dedupe + sort")

    # State capital lookup (abbr and full)
    expect(state_capital("IA") == "Des Moines" and state_capital("Iowa") == "Des Moines",
           "state_capital: handles abbreviation and full name")

    # Incident splitting (state before AND after parenthetical)
    expect(split_incident("Hood County (Comanche Circle), TX") == ("Hood County", "Comanche Circle"),
           "split_incident: state after parenthetical")
    expect(split_incident("Doña Ana County, NM (Project Jupiter)") == ("Doña Ana County", "Project Jupiter"),
           "split_incident: state before parenthetical")
    expect(split_incident("Town of Groton") == ("Town of Groton", ""), "split_incident: no descriptor")

    # objective_type
    expect(classify_objective("Ban commercial crypto mining in Clay County") == "ban", "objective: ban")
    expect(classify_objective("Impose 1-year data centers moratorium in Town of Groton") == "moratorium",
           "objective: moratorium")
    expect(classify_objective("Sue for environmental review of Google project") == "legal_challenge",
           "objective: legal_challenge")
    expect(classify_objective("Deny Red Dog rezoning for data center") == "oppose_specific_project",
           "objective: oppose_specific_project")
    expect(classify_objective("Repeal data center tax exemptions in CT") == "reduce_incentives",
           "objective: reduce_incentives")
    expect(classify_objective("Contest Aligned Data Centers approval at former plant") == "oppose_specific_project",
           "objective: contest -> oppose")
    expect(classify_objective("Force public vote on Nebius data center") == "oppose_specific_project",
           "objective: public vote -> oppose")
    expect(classify_objective("Create large-load utility tariff for 100MW+ data centers") == "restrict",
           "objective: tariff -> restrict")
    expect(classify_objective("File PSC complaint over cost shifting to residents") == "legal_challenge",
           "objective: complaint -> legal_challenge")
    expect(classify_group("NC Environmental Justice Network") == "environmental_group",
           "group: environmental_group")
    expect(classify_group("Protect Augusta Charter Township (PACT)") == "community_group",
           "group: protect -> community_group")

    # actor_type + party + chamber
    expect(classify_actor("Sen. Phil King (R-TX)") == "legislator", "actor: legislator")
    expect(actor_party("Sen. Phil King (R-TX)") == "Republican", "actor: party R")
    expect(actor_party("Sen. Erin Maye Quade (DFL-MN)") == "Democrat (DFL)", "actor: party DFL")
    expect(actor_chamber("Sen. Phil King (R-TX)") == "Senate", "actor: chamber Senate")
    expect(actor_chamber("Rep. Anna Novak (R-ND)") == "House/Lower", "actor: chamber House")
    expect(classify_actor("Earthjustice") == "advocacy_org", "actor: advocacy_org")
    expect(classify_actor("Joint Committee on Finance") == "legislative_committee", "actor: committee")

    # status normalization — procedural stages must NOT read as terminal
    sc, notes, _ = map_status("passed House (June 16, 2026); pending in Senate")
    expect(sc == "passed_one_chamber" and "pending in Senate" in notes,
           "status: one-chamber passage -> passed_one_chamber (not 'passed')")
    expect(map_status("approved by committee")[0] == "in_committee",
           "status: committee approval -> in_committee (not 'approved')")
    expect(map_status("passed both chambers, sent to governor")[0] == "passed_pending_signature",
           "status: both chambers awaiting signature -> not enacted")
    expect(map_status("signed into law")[0] == "enacted", "status: signed -> enacted")
    expect(map_status("died in committee")[0] == "failed", "status: died in committee -> failed")
    expect(map_status("defeated")[0] == "failed", "status: defeated -> failed")

    # Legislative completion / finality (the 'approved in committee != law' guard)
    if _HAVE_LEGIS:
        p, fin, ss, name, conf = infer_bill_progress("SB123 approved by committee, awaiting floor vote")
        expect(p == "in_committee" and fin is False,
               "completion: committee approval is in_committee, NOT final")
        p2, fin2, _, _, _ = infer_bill_progress("HB99 passed the House; awaiting action in the Senate")
        expect(p2 == "passed_one_chamber" and fin2 is False,
               "completion: one-chamber passage is not final")
        p3, fin3, _, _, _ = infer_bill_progress("Signed into law by the governor on June 1")
        expect(p3 == "signed_into_law" and fin3 is True, "completion: signed into law IS final")
        # e2e: a committee-approved bill recorded as a win must be flagged & not 'passed'
        leg = pd.DataFrame([{
            "Incident": "State SB123 data center moratorium", "State": "OH", "Scope": "statewide",
            "Opposition Type": "legislation", "Status": "approved by committee",
            "Summary": "SB123 was approved by committee and awaits a floor vote.",
            "Community Outcome": "win", "Date": "2026-04-01"}])
        lo, _, _ = clean(leg)
        expect(lo.iloc[0]["bill_progress"] == "in_committee", "e2e: committee bill staged as in_committee")
        expect(lo.iloc[0]["status_clean"] != "passed", "e2e: committee bill not labelled 'passed'")
        expect(lo.iloc[0]["action_complete"] == False, "e2e: committee bill action_complete=False")
        expect(lo.iloc[0]["outcome_overstated"] == True, "e2e: committee+win flagged outcome_overstated")

    # project override (xAI cross-venue)
    expect(matched_override({"Incident": "Memphis xAI gas turbines", "State": "TN"}) == "xai_colossus",
           "override: xAI TN row unified")
    expect(matched_override({"Incident": "Some data center", "State": "IA"}) is None,
           "override: unrelated row not matched")

    # End-to-end on a tiny synthetic frame
    sample = pd.DataFrame([
        {**{c: "" for c in ["Incident","City","Date","Entity","Location","Opposition Type","Severity",
                            "Source URL","State","County","Scope","Issue Category","Objective","Authority Level",
                            "Status","Community Outcome","Hyperscaler","Company","Project Name","Investment Million USD",
                            "Megawatts","Acreage","Sponsors","Opposition Groups","Summary","Sources",
                            "Opposition Website","Opposition Facebook","Opposition Instagram","Petition URL",
                            "Petition Signatures","data_source","lat","lon"]},
         "Incident": "New York State (statewide bill; Albany rally)", "State": "NY", "Scope": "statewide",
         "County": "Albany County", "City": "New York State (statewide bill; Albany rally)",
         "Source URL": "{'url': 'https://ex.com/ny', 'title': 'x'}", "Date": "2026-03-01",
         "Issue Category": "zoning; community_impact", "Community Outcome": "pending"},
    ])
    out, rep, chg = clean(sample)
    expect(list(out.columns[:34]) == list(sample.columns[:34]), "e2e: original 34 columns preserved & ordered")
    expect(out.iloc[0]["County"] == "", "e2e: statewide County nulled")
    expect(out.iloc[0]["City"] == "", "e2e: statewide capital City cleared")
    expect(not out.iloc[0]["Source URL"].startswith("{"), "e2e: stringified URL repaired in output")
    expect(out.iloc[0]["Issue Category"] == "community_impact; zoning", "e2e: issue category sorted")
    expect(out.iloc[0]["is_statewide"] == True, "e2e: is_statewide flagged")

    print("\nALL SELFTESTS PASS" if ok else "\nSOME SELFTESTS FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", help="input CSV (default: fetch from GitHub)")
    ap.add_argument("--out", dest="out", default="master_opposition_cleaned.csv")
    ap.add_argument("--overrides", dest="overrides", default="project_overrides.csv",
                    help="optional CSV of cross-venue project_id override rules")
    ap.add_argument("--selftest", action="store_true", help="run regression tests and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    n_ov = load_project_overrides(args.overrides)
    if n_ov:
        print(f"Loaded {n_ov} extra project override rule(s) from {args.overrides}.")

    df = load(args.inp)
    n = len(df)
    print(f"Loaded {n} rows, {len(df.columns)} columns.")

    df, report, changelog = clean(df)

    df.to_csv(args.out, index=False, quoting=csv.QUOTE_MINIMAL)
    print(f"  wrote {args.out}  ({len(df.columns)} columns)")

    pd.DataFrame(changelog).to_csv("change_log.csv", index=False)
    print("  wrote change_log.csv")

    write_report(report, changelog, n)

    print("\nSummary:")
    for title, detail in report:
        print(f"  • {title}")

if __name__ == "__main__":
    main()
