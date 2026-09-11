#!/usr/bin/env python3
"""
stakeholder_registry.py

Publishes the stakeholder layer: the named public officials whose offices decide
data center siting, zoning, permitting and utility questions in a given county,
plus the state officials who set the frame above them.

Two source tiers, one published artifact.

  STATE tier is machine-acquired. OpenStates is queried for each state's
  governor, and for the primary sponsors of the data center bills this
  repository has already matched in data/bill_sync_matches.csv. This tier
  refreshes on a schedule and needs no human hand.

  Sponsorship, not committee membership, is what makes a state legislator a
  stakeholder here. The first build of this module went after the chair of
  each state's energy or utilities committee and got a 400 from nearly every
  state: OpenStates documents committee support as experimental and not yet
  available for all states. Sponsorship comes off the bills endpoint, which
  bill_sync.py has used in production against all 50 states, and it is the
  better signal anyway -- a legislator who put their name on a data center
  bill has a demonstrated, citable position, where a committee chair only has
  a subject-matter title.

  COUNTY tier is a seeded source of record. There is no national register of
  county board chairs, county administrators and city mayors, so the seed is
  assembled per jurisdiction from official government pages and committed to
  the repository. This module NEVER invents a county row and never edits one:
  it reads the seed, validates it, and publishes what passes. Promotion into
  the seed is a human act, the same rule refresh_external_census.py applies to
  the restriction census.

The QC gate is what makes auto-publish safe. Every row is checked before it
reaches the published file; anything that fails is withheld and reported rather
than shown. Two failure classes are deliberately distinguished:

  HELD   the record's identity is unusable or provably stale -- no name, no
         office, a source that is not a URL, an unresolvable county, a term
         that ended before today, or a duplicate office. The whole row is
         withheld.
  WARN   one contact or relevance field is unusable but the identity is sound.
         The offending field is blanked, the row is published, and the flag is
         recorded. Losing a phone number is not a reason to hide a chairman.

A term_end in the past is treated as HELD, not WARN. Elections are the main
way this layer goes wrong, and a confidently-rendered former official is worse
than a blank: it is the one error that would reach a client as fact.

Reads
  data/stakeholder_seed_county.csv      county tier, source of record
  data/county_aggregate.csv             for FIPS resolution
  data/bill_sync_matches.csv            which bills to pull sponsors for
  OpenStates v3 API                     state tier (--refresh-state)

Writes
  data/stakeholder_registry.csv         published rows, all QC-passing
  data/stakeholder_held.csv             withheld rows, with reasons
  data/stakeholder_qc_report.md         human-readable gate result
  data/stakeholder_manifest.json        freshness and coverage signal
  data/stakeholder_openstates_cache.json  request cache (quota protection)

Usage
  python stakeholder_registry.py --selftest
  python stakeholder_registry.py --build              # county tier only
  python stakeholder_registry.py --refresh-state      # + query OpenStates
  python stakeholder_registry.py --refresh-state --cache-days 7
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

SEED_COUNTY = os.path.join(DATA, "stakeholder_seed_county.csv")
BILL_MATCHES = os.path.join(DATA, "bill_sync_matches.csv")
COUNTY_AGG = os.path.join(DATA, "county_aggregate.csv")
OUT_CSV = os.path.join(DATA, "stakeholder_registry.csv")
OUT_HELD = os.path.join(DATA, "stakeholder_held.csv")
OUT_REPORT = os.path.join(DATA, "stakeholder_qc_report.md")
OUT_MANIFEST = os.path.join(DATA, "stakeholder_manifest.json")
CACHE_PATH = os.path.join(DATA, "stakeholder_openstates_cache.json")

COLUMNS = ["stakeholder_id", "level", "state", "fips", "county_name",
           "jurisdiction", "name", "office", "office_class", "party",
           "term_start", "term_end", "contact_email", "contact_phone",
           "contact_address", "relevance_note", "relevance_source_url",
           "source_url", "source_retrieved"]

OFFICE_CLASSES = {"governor", "bill_sponsor",
                  "county_board", "county_admin", "mayor"}

# How far a bill actually got, worst to best. A sponsor is more interesting the
# further their bill advanced, so this orders which bill a state is represented
# by. Blank and unrecognized stages sort last.
STAGE_RANK = {
    "Signed into law": 8,
    "Passed both chambers": 7,
    "Vetoed": 6,
    "Passed one chamber": 5,
    "Failed floor vote": 4,
    "Passed committee only": 3,
    "Died in committee": 2,
    "Withdrawn": 1,
    "Introduced": 1,
}

# Sponsors published per state. Two keeps the section readable next to the
# governor without turning a county page into a legislature roster.
SPONSORS_PER_STATE = 2

NA = {"", "n.a.", "n/a", "na", "none", "null", "unknown", "-", "tbd"}

_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
# Quoted nicknames ('Richard "Dick" Anderson') are how many official pages
# publish a name, so the quote characters are part of a valid name and not a
# sign of scraped markup. Straight and curly forms both appear in the wild.
_NAME_OK = re.compile(
    r"^[A-Za-z\u00c0-\u024f][A-Za-z\u00c0-\u024f'\"\u2018\u2019\u201c\u201d.\-\s,]*$")
_DIGITS = re.compile(r"\D")
_SLUG = re.compile(r"[^a-z0-9]+")

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


# ---------------------------------------------------------------- normalizers

def clean_text(value: str) -> str:
    """Strip markup and collapse whitespace. 'n.a.' spellings become ''."""
    s = _TAGS.sub(" ", str(value or ""))
    s = _WS.sub(" ", s).strip()
    return "" if s.lower() in NA else s


def slugify(value: str) -> str:
    return _SLUG.sub("-", str(value or "").lower()).strip("-")


def normalize_email(value: str) -> tuple[str, str]:
    """Return (email, flag). flag is '' when clean."""
    s = clean_text(value).lower()
    if not s:
        return "", ""
    s = s.removeprefix("mailto:")
    if not _EMAIL.match(s):
        return "", "email_unparseable"
    domain = s.rsplit("@", 1)[1]
    if not (domain.endswith(".gov") or domain.endswith(".us")
            or domain.endswith(".org")):
        return s, "email_domain_not_official"
    return s, ""


def normalize_phone(value: str) -> tuple[str, str]:
    """Normalize a NANP number to (XXX) XXX-XXXX. Extensions are preserved."""
    s = clean_text(value)
    if not s:
        return "", ""
    ext = ""
    m = re.search(r"(?:ext|x|extension)\.?\s*(\d{1,6})\s*$", s, re.I)
    if m:
        ext = m.group(1)
        s = s[:m.start()]
    digits = _DIGITS.sub("", s)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return "", "phone_unparseable"
    out = f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    return (f"{out} ext. {ext}" if ext else out), ""


def normalize_date(value: str) -> tuple[str, str]:
    """Accept YYYY-MM-DD, YYYY-MM or YYYY. Return (stored, flag)."""
    s = clean_text(value)
    if not s:
        return "", ""
    if re.fullmatch(r"\d{4}", s):
        return s, ""
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s, ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            dt.date.fromisoformat(s)
        except ValueError:
            return "", "date_invalid"
        return s, ""
    return "", "date_unparseable"


def date_upper_bound(value: str) -> dt.date | None:
    """Latest date a partial date could mean. '2028' -> 2028-12-31.

    Terms are published at varying precision and a year-only term_end must not
    be read as 1 January, which would hold every currently-serving official
    whose county publishes only the year.
    """
    s = clean_text(value)
    if re.fullmatch(r"\d{4}", s):
        return dt.date(int(s), 12, 31)
    if re.fullmatch(r"\d{4}-\d{2}", s):
        y, m = int(s[:4]), int(s[5:7])
        nxt = dt.date(y + (m == 12), (m % 12) + 1, 1)
        return nxt - dt.timedelta(days=1)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return dt.date.fromisoformat(s)
        except ValueError:
            return None
    return None


def is_http_url(value: str) -> bool:
    s = clean_text(value)
    if not s:
        return False
    p = urllib.parse.urlparse(s)
    return p.scheme in ("http", "https") and bool(p.netloc)


def make_stakeholder_id(rec: dict) -> str:
    scope = rec.get("fips") or rec.get("state") or "us"
    parts = [rec.get("level", ""), scope, rec.get("office_class", ""),
             slugify(rec.get("name", ""))]
    return "-".join(slugify(p) for p in parts if p)


# ------------------------------------------------------------------- QC gate

def qc_record(rec: dict, valid_fips: set[str], today: dt.date) -> tuple[str, list[str]]:
    """Validate and normalize a record in place.

    Returns (status, flags) where status is 'published' or 'held'. Contact and
    relevance problems blank the offending field and warn; identity problems
    hold the row.
    """
    flags: list[str] = []

    for key in COLUMNS:
        rec[key] = clean_text(rec.get(key, ""))

    if rec["level"] not in ("state", "county"):
        flags.append("level_invalid")
    if rec["office_class"] and rec["office_class"] not in OFFICE_CLASSES:
        flags.append("office_class_invalid")

    if not rec["name"]:
        flags.append("name_missing")
    elif len(rec["name"]) > 80 or not _NAME_OK.match(rec["name"]):
        flags.append("name_unusable")
    elif len(rec["name"].split()) < 2:
        flags.append("name_not_full")

    if not rec["office"]:
        flags.append("office_missing")

    if rec["state"] not in STATE_NAMES:
        flags.append("state_invalid")

    if rec["level"] == "county":
        if not re.fullmatch(r"\d{5}", rec["fips"]):
            flags.append("fips_malformed")
        elif valid_fips and rec["fips"] not in valid_fips:
            flags.append("fips_unresolved")
    else:
        rec["fips"] = ""

    if not is_http_url(rec["source_url"]):
        flags.append("source_url_invalid")

    for field in ("term_start", "term_end"):
        rec[field], flag = normalize_date(rec[field])
        if flag:
            flags.append(f"{field}_{flag}")

    bound = date_upper_bound(rec["term_end"])
    if bound is not None and bound < today:
        flags.append("term_expired")

    rec["contact_email"], flag = normalize_email(rec["contact_email"])
    if flag:
        flags.append(flag)
    rec["contact_phone"], flag = normalize_phone(rec["contact_phone"])
    if flag:
        flags.append(flag)

    if rec["relevance_note"] and not is_http_url(rec["relevance_source_url"]):
        rec["relevance_note"] = ""
        rec["relevance_source_url"] = ""
        flags.append("relevance_unsourced")

    rec["stakeholder_id"] = make_stakeholder_id(rec)

    holding = {"level_invalid", "office_class_invalid", "name_missing",
               "name_unusable", "name_not_full", "office_missing",
               "state_invalid", "fips_malformed", "fips_unresolved",
               "source_url_invalid", "term_expired",
               "term_start_date_invalid", "term_end_date_invalid",
               "term_start_date_unparseable", "term_end_date_unparseable"}
    status = "held" if holding.intersection(flags) else "published"
    return status, flags


def dedupe(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """One record per office per jurisdiction. Later duplicates are held."""
    seen: dict[tuple, str] = {}
    kept, dropped = [], []
    for rec in records:
        key = (rec["level"], rec["state"], rec["fips"], rec["office_class"])
        if key in seen:
            rec["qc_flags"] = ";".join(
                filter(None, [rec.get("qc_flags", ""), "duplicate_office"]))
            rec["qc_status"] = "held"
            dropped.append(rec)
            continue
        seen[key] = rec["stakeholder_id"]
        kept.append(rec)
    return kept, dropped


# --------------------------------------------------------------- county tier

def load_valid_fips(path: str = COUNTY_AGG) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        return {(r.get("fips") or "").strip().zfill(5)
                for r in csv.DictReader(fh) if (r.get("fips") or "").strip()}


def load_seed_county(path: str = SEED_COUNTY) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rec = {k: row.get(k, "") for k in COLUMNS}
            rec["level"] = "county"
            rec["fips"] = (row.get("fips") or "").strip().zfill(5)
            if not rec.get("source_retrieved"):
                rec["source_retrieved"] = (row.get("source_retrieved")
                                           or "").strip()
            out.append(rec)
    return out


# ---------------------------------------------------------------- state tier

class Cache:
    """Disk-backed GET cache. OpenStates free keys have a daily request cap
    shared with bill_sync.py, so an unchanged run must cost nothing."""

    def __init__(self, path: str, ttl_days: int):
        self.path = path
        self.ttl = dt.timedelta(days=ttl_days)
        self.store: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.store = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self.store = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        entry = self.store.get(key)
        if not entry:
            return None
        try:
            age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                entry["fetched"])
        except (KeyError, ValueError):
            return None
        if age > self.ttl:
            return None
        self.hits += 1
        return entry["payload"]

    def put(self, key: str, payload) -> None:
        self.misses += 1
        self.store[key] = {
            "fetched": dt.datetime.now(dt.timezone.utc).isoformat(),
            "payload": payload,
        }

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.store, fh, indent=1, sort_keys=True)
            fh.write("\n")


# The free tier allows roughly one request a second, and bill_sync.py settled on
# this spacing against the same key. The first build of this module slept 0.2s
# and collected 429s across a third of the states.
THROTTLE_S = 1.1
_last_call = [0.0]


def api_get(path: str, params: dict, api_key: str, cache: Cache) -> dict:
    # doseq spreads a list parameter into repeated keys, which is how the API
    # expects `include`; urlencode would otherwise send the list's repr.
    url = (f"https://v3.openstates.org{path}?"
           f"{urllib.parse.urlencode(params, doseq=True)}")
    cached = cache.get(url)
    if cached is not None:
        return cached
    req = urllib.request.Request(
        url, headers={"X-API-KEY": api_key, "User-Agent": "data-center-map"})
    for attempt in range(3):
        wait = THROTTLE_S - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            cache.put(url, payload)
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    return {}


def http_detail(exc: urllib.error.HTTPError) -> str:
    """Code plus the API's own explanation. A bare status code sent the first
    build of this module chasing the wrong parameter for an afternoon."""
    try:
        body = exc.read().decode("utf-8", "replace")[:200]
    except Exception:
        body = ""
    return f"{exc.code}{': ' + body if body else ''}"


def load_bill_matches(path: str = BILL_MATCHES) -> dict[str, list[dict]]:
    """Matched data center bills grouped by state, best bill first.

    Only rows bill_sync.py actually resolved are usable: an ambiguous or failed
    lookup has no reliable session, and the sponsorship call needs one.
    """
    if not os.path.exists(path):
        return {}
    by_state: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("lookup_status") or "").strip() != "matched":
                continue
            state = (row.get("state") or "").strip().upper()
            ident = (row.get("identifier") or "").strip()
            if state not in STATE_NAMES or not ident:
                continue
            by_state.setdefault(state, []).append(row)
    for rows in by_state.values():
        rows.sort(key=lambda r: (STAGE_RANK.get((r.get("stage") or "").strip(), 0),
                                 (r.get("stage_date") or "")),
                  reverse=True)
    return by_state


def primary_sponsors(bill: dict, limit: int = SPONSORS_PER_STATE) -> list[dict]:
    """Primary sponsors of a bill payload, falling back to cosponsors.

    A bill with no classified primary sponsor still has people attached to it,
    and a cosponsor on a data center bill is a real stakeholder; the office
    title records which it was, so the distinction is never lost.
    """
    primary, other = [], []
    for sp in bill.get("sponsorships") or []:
        if not (sp.get("name") or (sp.get("person") or {}).get("name")):
            continue
        (primary if sp.get("classification") == "primary" else other).append(sp)
    return (primary or other)[:limit]


def person_record(person: dict, state: str, office: str, office_class: str,
                  source_url: str, today: str) -> dict:
    offices = person.get("offices") or []
    phone, address = "", ""
    for off in offices:
        phone = phone or (off.get("voice") or "")
        address = address or (off.get("address") or "")
    return {
        "level": "state",
        "state": state,
        "fips": "",
        "county_name": "",
        "jurisdiction": STATE_NAMES.get(state, state),
        "name": person.get("name", ""),
        "office": office,
        "office_class": office_class,
        "party": person.get("party", "") or "",
        "term_start": "",
        "term_end": "",
        "contact_email": person.get("email", "") or "",
        "contact_phone": phone,
        "contact_address": address,
        "relevance_note": "",
        "relevance_source_url": "",
        "source_url": source_url,
        "source_retrieved": today,
    }


def fetch_state_layer(api_key: str, cache: Cache, states: list[str],
                      today: str) -> tuple[list[dict], list[str]]:
    """Governor plus the sponsors of the state's furthest-advanced bill."""
    records, notes = [], []
    bills_by_state = load_bill_matches()
    for abbrev in states:
        juris = abbrev.lower()
        try:
            execs = api_get("/people", {"jurisdiction": juris,
                                        "org_classification": "executive",
                                        "include": ["offices"],
                                        "per_page": 20}, api_key, cache)
        except urllib.error.HTTPError as exc:
            notes.append(f"{abbrev}: executive lookup failed ({http_detail(exc)})")
            execs = {}
        gov = None
        for person in execs.get("results", []):
            title = ((person.get("current_role") or {}).get("title") or "")
            if title.lower() == "governor":
                gov = person
                break
        if gov:
            records.append(person_record(
                gov, abbrev, "Governor", "governor",
                gov.get("openstates_url") or f"https://openstates.org/{abbrev.lower()}/",
                today))
        else:
            notes.append(f"{abbrev}: no governor returned by OpenStates")

        matches = bills_by_state.get(abbrev) or []
        if not matches:
            notes.append(f"{abbrev}: no matched data center bill to draw "
                         f"sponsors from")
            continue
        match = matches[0]
        ident, session = match["identifier"].strip(), (match.get("session") or "").strip()
        try:
            found = api_get("/bills", {"jurisdiction": juris,
                                       "identifier": ident,
                                       "include": ["sponsorships"],
                                       "per_page": 20,
                                       "sort": "updated_desc"}, api_key, cache)
        except urllib.error.HTTPError as exc:
            notes.append(f"{abbrev}: sponsor lookup for {ident} failed "
                         f"({http_detail(exc)})")
            continue
        results = found.get("results") or []
        bill = next((b for b in results if b.get("session") == session),
                    results[0] if results else None)
        if not bill:
            notes.append(f"{abbrev}: {ident} returned no bill")
            continue
        sponsors = primary_sponsors(bill)
        if not sponsors:
            notes.append(f"{abbrev}: {ident} lists no sponsors")
            continue
        bill_url = (match.get("openstates_url") or bill.get("openstates_url")
                    or "")
        stage = (match.get("stage") or "").strip()
        title = (match.get("title") or bill.get("title") or "").strip()
        for sp in sponsors:
            person = sp.get("person") or {}
            detail = dict(person)
            # A sponsorship may carry only the printed name, with no linked
            # person record; keep the name so the row is still publishable.
            detail.setdefault("name", sp.get("name") or "")
            role = ("Primary sponsor" if sp.get("classification") == "primary"
                    else "Co-sponsor")
            rec = person_record(
                detail, abbrev, f"{role}, {ident}", "bill_sponsor",
                detail.get("openstates_url") or bill_url, today)
            if bill_url:
                rec["relevance_note"] = (
                    f"{role} of {ident}"
                    + (f", {title}" if title else "")
                    + (f" ({stage})" if stage else ""))
                rec["relevance_source_url"] = bill_url
            records.append(rec)
    return records, notes


# ---------------------------------------------------------------- publishing

def build_registry(county_rows: list[dict], state_rows: list[dict],
                   valid_fips: set[str], today: dt.date
                   ) -> tuple[list[dict], list[dict]]:
    published, held = [], []
    for rec in list(state_rows) + list(county_rows):
        status, flags = qc_record(rec, valid_fips, today)
        rec["qc_status"] = status
        rec["qc_flags"] = ";".join(flags)
        (published if status == "published" else held).append(rec)
    published, dropped = dedupe(published)
    held.extend(dropped)
    published.sort(key=lambda r: (r["level"] != "county", r["state"],
                                  r["fips"], r["office_class"], r["name"]))
    return published, held


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_report(published: list[dict], held: list[dict],
                 notes: list[str], today: dt.date) -> None:
    flag_counts: dict[str, int] = {}
    for rec in published + held:
        for flag in filter(None, rec.get("qc_flags", "").split(";")):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    warned = sum(1 for r in published if r.get("qc_flags"))
    lines = [
        "# Stakeholder registry QC report",
        "",
        f"Generated {today.isoformat()}.",
        "",
        f"- Published rows: **{len(published)}** "
        f"({sum(1 for r in published if r['level'] == 'county')} county, "
        f"{sum(1 for r in published if r['level'] == 'state')} state)",
        f"- Published with a warning flag: **{warned}**",
        f"- Withheld rows: **{len(held)}**",
        f"- Counties covered: **{len({r['fips'] for r in published if r['fips']})}**",
        f"- States covered: **{len({r['state'] for r in published})}**",
        "",
        "A withheld row is one whose identity could not be trusted: no name,",
        "no office, an unresolvable county, a source that is not a URL, a",
        "duplicate office, or a term that has already ended. A warned row is",
        "published with the unusable contact or relevance field blanked.",
        "",
        "## Flag counts",
        "",
    ]
    if flag_counts:
        lines += ["| flag | rows |", "| --- | --- |"]
        lines += [f"| {k} | {v} |"
                  for k, v in sorted(flag_counts.items(), key=lambda i: -i[1])]
    else:
        lines.append("No flags raised.")
    if held:
        lines += ["", "## Withheld rows", "",
                  "| level | state | fips | office_class | name | flags |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for rec in held[:200]:
            lines.append(
                f"| {rec.get('level','')} | {rec.get('state','')} "
                f"| {rec.get('fips','')} | {rec.get('office_class','')} "
                f"| {rec.get('name','') or '(blank)'} "
                f"| {rec.get('qc_flags','')} |")
        if len(held) > 200:
            lines.append(f"| ... | | | | | {len(held) - 200} more |")
    if notes:
        lines += ["", "## Acquisition notes", ""]
        lines += [f"- {n}" for n in notes]
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def write_manifest(published: list[dict], held: list[dict],
                   state_refreshed: bool, notes: list[str]) -> dict:
    by_county: dict[str, int] = {}
    for rec in published:
        if rec["fips"]:
            by_county[rec["fips"]] = by_county.get(rec["fips"], 0) + 1
    manifest = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "published_rows": len(published),
        "withheld_rows": len(held),
        "warned_rows": sum(1 for r in published if r.get("qc_flags")),
        "county_rows": sum(1 for r in published if r["level"] == "county"),
        "state_rows": sum(1 for r in published if r["level"] == "state"),
        "counties_covered": len(by_county),
        "states_covered": sorted({r["state"] for r in published}),
        "state_tier_refreshed": state_refreshed,
        "with_email": sum(1 for r in published if r["contact_email"]),
        "with_phone": sum(1 for r in published if r["contact_phone"]),
        "with_relevance": sum(1 for r in published if r["relevance_note"]),
        "acquisition_notes": notes,
    }
    os.makedirs(os.path.dirname(OUT_MANIFEST), exist_ok=True)
    with open(OUT_MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return manifest


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    today = dt.date(2026, 9, 11)
    fips_ok = {"51107", "42069"}

    def rec(**kw):
        base = {c: "" for c in COLUMNS}
        base.update({"level": "county", "state": "VA", "fips": "51107",
                     "county_name": "Loudoun County, Virginia",
                     "name": "Jane Q Public", "office": "Chair",
                     "office_class": "county_board",
                     "source_url": "https://loudoun.gov/bos"})
        base.update(kw)
        return base

    status, flags = qc_record(rec(), fips_ok, today)
    check("a clean county row publishes", status == "published" and not flags)

    status, flags = qc_record(rec(term_end="2028"), fips_ok, today)
    check("year-only future term publishes", status == "published")
    status, flags = qc_record(rec(term_end="2026"), fips_ok, today)
    check("year-only current term is not held mid-year",
          status == "published")
    status, flags = qc_record(rec(term_end="2025"), fips_ok, today)
    check("expired term is held",
          status == "held" and "term_expired" in flags)
    status, flags = qc_record(rec(term_end="2026-08"), fips_ok, today)
    check("month precision expiry is held",
          status == "held" and "term_expired" in flags)
    status, flags = qc_record(rec(term_end="2026-09-30"), fips_ok, today)
    check("term ending later this month publishes", status == "published")

    status, flags = qc_record(rec(name="n.a."), fips_ok, today)
    check("n.a. name is held", status == "held" and "name_missing" in flags)
    status, flags = qc_record(rec(name="Cher"), fips_ok, today)
    check("single-token name is held", "name_not_full" in flags)
    status, flags = qc_record(rec(name="<b>Jane Public</b>"), fips_ok, today)
    check("markup is stripped from the name rather than held",
          status == "published")
    status, flags = qc_record(rec(name='Richard "Dick" Anderson'), fips_ok, today)
    check("a straight-quoted nickname publishes", status == "published")
    status, flags = qc_record(rec(name="Samip \u201cSam\u201d Joshi"), fips_ok, today)
    check("a curly-quoted nickname publishes", status == "published")
    status, flags = qc_record(rec(name="Mary-Jane O'Neill, Jr."), fips_ok, today)
    check("hyphens, apostrophes and suffixes publish", status == "published")

    status, flags = qc_record(rec(fips="99999"), fips_ok, today)
    check("unresolvable fips is held",
          status == "held" and "fips_unresolved" in flags)
    status, flags = qc_record(rec(fips="517"), fips_ok, today)
    check("malformed fips is held", "fips_malformed" in flags)
    status, flags = qc_record(rec(source_url="loudoun.gov"), fips_ok, today)
    check("non-URL source is held",
          status == "held" and "source_url_invalid" in flags)
    status, flags = qc_record(rec(state="XX"), fips_ok, today)
    check("bad state is held", "state_invalid" in flags)

    row = rec(contact_phone="703-777-0204 ext 12")
    status, flags = qc_record(row, fips_ok, today)
    check("phone normalizes with extension",
          row["contact_phone"] == "(703) 777-0204 ext. 12")
    row = rec(contact_phone="+1 (703) 777-0204")
    qc_record(row, fips_ok, today)
    check("leading country code is dropped",
          row["contact_phone"] == "(703) 777-0204")
    row = rec(contact_phone="call the office")
    status, flags = qc_record(row, fips_ok, today)
    check("unparseable phone warns and blanks, does not hold",
          status == "published" and row["contact_phone"] == ""
          and "phone_unparseable" in flags)

    row = rec(contact_email="mailto:BOS@LOUDOUN.GOV")
    qc_record(row, fips_ok, today)
    check("email lowercases and drops mailto",
          row["contact_email"] == "bos@loudoun.gov")
    row = rec(contact_email="chair@gmail.com")
    status, flags = qc_record(row, fips_ok, today)
    check("non-official email domain warns but is kept",
          status == "published" and row["contact_email"] == "chair@gmail.com"
          and "email_domain_not_official" in flags)
    row = rec(contact_email="not-an-email")
    status, flags = qc_record(row, fips_ok, today)
    check("unparseable email warns and blanks",
          status == "published" and row["contact_email"] == ""
          and "email_unparseable" in flags)

    row = rec(relevance_note="Votes on data center rezoning.")
    status, flags = qc_record(row, fips_ok, today)
    check("unsourced relevance note is dropped, row survives",
          status == "published" and row["relevance_note"] == ""
          and "relevance_unsourced" in flags)
    row = rec(relevance_note="Votes on data center rezoning.",
              relevance_source_url="https://loudoun.gov/zoning")
    status, flags = qc_record(row, fips_ok, today)
    check("sourced relevance note is kept",
          row["relevance_note"].startswith("Votes") and not flags)

    row = rec()
    qc_record(row, fips_ok, today)
    check("stakeholder_id is deterministic and slugged",
          row["stakeholder_id"] == "county-51107-county-board-jane-q-public")
    srow = {c: "" for c in COLUMNS}
    srow.update({"level": "state", "state": "VA", "fips": "51107",
                 "name": "Ann Officeholder", "office": "Governor",
                 "office_class": "governor",
                 "source_url": "https://openstates.org/va/"})
    status, flags = qc_record(srow, fips_ok, today)
    check("state row publishes and its stray fips is cleared",
          status == "published" and srow["fips"] == "")

    a, b = rec(name="First Person"), rec(name="Second Person")
    for r in (a, b):
        qc_record(r, fips_ok, today)
        r["qc_flags"] = ""
    kept, dropped = dedupe([a, b])
    check("two chairs for one county keeps one and holds one",
          len(kept) == 1 and len(dropped) == 1
          and "duplicate_office" in dropped[0]["qc_flags"])
    c = rec(name="Third Person", office_class="mayor",
            jurisdiction="Town of Leesburg")
    qc_record(c, fips_ok, today)
    c["qc_flags"] = ""
    kept, dropped = dedupe([a, c])
    check("different office classes both survive dedupe", len(kept) == 2)

    check("an enacted bill outranks an introduced one",
          STAGE_RANK["Signed into law"] > STAGE_RANK["Introduced"])
    check("an unrecognized stage ranks below every known stage",
          STAGE_RANK.get("Referred to nowhere", 0) < min(STAGE_RANK.values()))
    bill = {"sponsorships": [
        {"name": "Co One", "classification": "cosponsor"},
        {"name": "Prime Two", "classification": "primary"},
        {"name": "", "classification": "primary"},
        {"name": "Prime Three", "classification": "primary"},
        {"name": "Prime Four", "classification": "primary"}]}
    picked = primary_sponsors(bill)
    check("primary sponsors are preferred over cosponsors",
          [s["name"] for s in picked] == ["Prime Two", "Prime Three"])
    check("a nameless sponsorship is skipped",
          all(s["name"] for s in picked))
    check("cosponsors are used when no primary is classified",
          [s["name"] for s in primary_sponsors(
              {"sponsorships": [{"name": "Co One",
                                 "classification": "cosponsor"}]})]
          == ["Co One"])
    check("a sponsorship carrying only a linked person is kept",
          len(primary_sponsors({"sponsorships": [
              {"classification": "primary",
               "person": {"name": "Linked Person"}}]})) == 1)
    check("a bill with no sponsorships yields nothing",
          primary_sponsors({}) == [])

    check("date_upper_bound expands a year",
          date_upper_bound("2027") == dt.date(2027, 12, 31))
    check("date_upper_bound expands a month",
          date_upper_bound("2027-02") == dt.date(2027, 2, 28))
    check("date_upper_bound handles december",
          date_upper_bound("2027-12") == dt.date(2027, 12, 31))
    check("date_upper_bound rejects junk", date_upper_bound("soon") is None)

    published, held = build_registry(
        [rec(name="Board Chair"), rec(name="Bad Row", fips="00000")],
        [srow.copy()], fips_ok, today)
    check("build_registry splits published from held",
          len(published) == 2 and len(held) == 1)
    check("county rows sort ahead of state rows",
          published[0]["level"] == "county")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cache = Cache(os.path.join(tmp, "c.json"), ttl_days=7)
        cache.put("k", {"v": 1})
        check("cache returns a fresh entry", cache.get("k") == {"v": 1})
        cache.store["old"] = {"fetched": "2020-01-01T00:00:00+00:00",
                              "payload": {"v": 2}}
        check("cache expires a stale entry", cache.get("old") is None)
        cache.save()
        check("cache round-trips to disk",
              Cache(os.path.join(tmp, "c.json"), 7).get("k") == {"v": 1})

        bills_csv = os.path.join(tmp, "bill_matches.csv")
        with open(bills_csv, "w", encoding="utf-8", newline="") as fh:
            fh.write("state,identifier,lookup_status,session,stage,stage_date\n"
                     "VA,HB 1,matched,2026,Introduced,2026-01-05\n"
                     "VA,HB 2,matched,2026,Signed into law,2026-04-01\n"
                     "VA,HB 3,ambiguous_session,2026,Signed into law,2026-05-01\n"
                     "ZZ,HB 4,matched,2026,Introduced,2026-01-05\n"
                     "TX,,matched,2026,Introduced,2026-01-05\n")
        grouped = load_bill_matches(bills_csv)
        check("the furthest-advanced bill leads its state",
              grouped["VA"][0]["identifier"] == "HB 2")
        check("an unmatched lookup is excluded",
              [b["identifier"] for b in grouped["VA"]] == ["HB 2", "HB 1"])
        check("an invalid state is excluded", "ZZ" not in grouped)
        check("a row with no identifier is excluded", "TX" not in grouped)
        check("a missing bill file yields no groups",
              load_bill_matches(os.path.join(tmp, "absent.csv")) == {})

    failed = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--build", action="store_true",
                    help="publish from the county seed only")
    ap.add_argument("--refresh-state", action="store_true",
                    help="also query OpenStates for the state tier")
    ap.add_argument("--cache-days", type=int, default=14,
                    help="reuse cached OpenStates responses this long")
    ap.add_argument("--states", default="",
                    help="comma-separated abbreviations; default is every "
                         "state present in the county seed")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not (args.build or args.refresh_state):
        ap.error("pass --build or --refresh-state (or --selftest)")

    today = dt.date.today()
    valid_fips = load_valid_fips()
    county_rows = load_seed_county()
    print(f"county seed: {len(county_rows)} rows")

    state_rows, notes = [], []
    if args.refresh_state:
        api_key = os.environ.get("OPENSTATES_API_KEY", "").strip()
        if not api_key:
            print("ERROR: OPENSTATES_API_KEY is not set. Get a free key at "
                  "https://open.pluralpolicy.com/accounts/profile/")
            return 2
        if args.states:
            states = [s.strip().upper() for s in args.states.split(",")
                      if s.strip()]
        else:
            states = sorted({r["state"].strip().upper() for r in county_rows
                             if r.get("state")})
        states = [s for s in states if s in STATE_NAMES]
        if not states:
            print("no states to refresh")
            return 2
        cache = Cache(CACHE_PATH, args.cache_days)
        state_rows, notes = fetch_state_layer(api_key, cache, states,
                                              today.isoformat())
        cache.save()
        print(f"state tier: {len(state_rows)} rows from {len(states)} states "
              f"(cache {cache.hits} hit / {cache.misses} miss)")
    else:
        # Preserve the state tier already published so --build never silently
        # deletes it.
        if os.path.exists(OUT_CSV):
            with open(OUT_CSV, encoding="utf-8") as fh:
                state_rows = [r for r in csv.DictReader(fh)
                              if r.get("level") == "state"]
            print(f"state tier: {len(state_rows)} rows carried forward")

    published, held = build_registry(county_rows, state_rows, valid_fips, today)
    write_csv(OUT_CSV, published, COLUMNS + ["qc_status", "qc_flags"])
    write_csv(OUT_HELD, held, COLUMNS + ["qc_status", "qc_flags"])
    write_report(published, held, notes, today)
    manifest = write_manifest(published, held, args.refresh_state, notes)

    print(f"published {manifest['published_rows']} rows "
          f"({manifest['county_rows']} county / {manifest['state_rows']} state) "
          f"across {manifest['counties_covered']} counties")
    print(f"withheld {manifest['withheld_rows']} rows, "
          f"{manifest['warned_rows']} published with warnings")
    print(f"contact coverage: {manifest['with_email']} email, "
          f"{manifest['with_phone']} phone, "
          f"{manifest['with_relevance']} sourced relevance")
    for note in notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
