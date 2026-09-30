"""
bill_sync.py — Open States bill-status sync (priority item 2).

Matches tracked legislative opposition records to Open States (Plural) bill
records, pulls the full action history, classifies the furthest stage each
bill has actually reached, and writes a review worklist flagging every record
whose coded status disagrees with the action history. This replaces manual
bill-status checking and hardens the legislative-outcome discipline: a bill
that passed committee or one chamber is Pending, never enacted law.

Additive and review-gated. NOTHING here writes to master_opposition.csv.
Reads existing files, writes only NEW files:

  data/bill_sync_worklist.csv   every legislative record with a parsed bill id
                                (built offline, no network)
  data/bill_sync_matches.csv    record-to-bill matches with evidence
  data/bill_status_review.csv   the disagreement worklist a human reviews
  data/bill_sync_report.md      run summary
  data/bill_sync_cache.json     raw API responses, so steady-state runs make
                                near-zero API calls (federal keys US:<congress>:...)
  data/bill_sync_federal.csv    every federal record: Congress.gov match or
                                the reason it has none (spec 007)

Stage discipline is the same ladder qc/legislative_outcome.py enforces, keyed
here off Open States' machine-coded action classifications instead of prose
substrings. Precedence is terminal-first: signed / vetoed / failed / withdrawn
outrank passed-both-chambers, which outranks one chamber, which outranks
committee, which outranks introduced. Open States emits no sine die action, so
a bill whose session has ended with no terminal action is flagged as
possible_sine_die at LOW confidence for human confirmation, never auto-coded
as dead.

Modes:
  python3 bill_sync.py --extract           offline: parse bill ids, build the
                                           worklist, no network
  python3 bill_sync.py --resolve           live: query the API, classify, and
                                           write matches + review worklist
  python3 bill_sync.py --resolve --limit 25   cap API lookups for a first pass
  python3 bill_sync.py --federal           federal pass only (Congress.gov),
                                           replaces only venue=federal review rows
  python3 bill_sync.py --selftest          fixture tests, no network, no deps

Requires for --resolve: the OPENSTATES_API_KEY environment variable (free key
from https://open.pluralpolicy.com/accounts/signup/). Stdlib only; no new
package dependency, same approach signal_harvest.py takes with GDELT.

Rate limits: the free tier allows roughly 1 request/second and 500/day. The
client throttles to 1.1s between calls and the cache means a bill is fetched
once and refreshed only when --refresh-days has elapsed (default 7).

Scope notes:
  - Open States covers state legislation only here. Its local-ordinance
    coverage is nil and its congressional coverage is not relied on; records
    with State == US keep lookup_status federal_skip in the worklist and go
    to the federal pass instead.
  - Federal pass (spec 007): federal identifiers (H.R., S., H.Res., S.J.Res.
    and the rest) are looked up on Congress.gov v3 with CONGRESS_API_KEY,
    classified onto the same ladder, and written to
    data/bill_sync_federal.csv (one row per federal record, matched or with
    the reason it is not) and to bill_status_review.csv with venue=federal.
    Without the key the pass is skipped, recorded, and the exit code is 0.
  - Identifier extraction requires a known bill prefix plus digits. NY and NJ
    single-letter formats (S731, A796) are recognized only for those two
    states, where that is the chamber convention, to avoid false positives
    elsewhere.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

OPPOSITION_CSV = os.environ.get("BS_OPPOSITION", os.path.join(ROOT, "master_opposition.csv"))
OUT_WORKLIST = os.path.join(DATA, "bill_sync_worklist.csv")
OUT_MATCHES = os.path.join(DATA, "bill_sync_matches.csv")
OUT_REVIEW = os.path.join(DATA, "bill_status_review.csv")
OUT_VOTES = os.path.join(DATA, "bill_sync_votes.csv")
OUT_REPORT = os.path.join(DATA, "bill_sync_report.md")
CACHE_PATH = os.path.join(DATA, "bill_sync_cache.json")

API_BASE = "https://v3.openstates.org/bills"
THROTTLE_S = 1.1
DEFAULT_REFRESH_DAYS = 7

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Bill identifier extraction
# ---------------------------------------------------------------------------

# Multi-letter prefixes are safe nationally. Single-letter S/A formats are the
# chamber convention only in NY and NJ and are matched only there.
_BILL_RE = re.compile(
    r"\b(HF|SF|HB|SB|AB|HSB|SSB|HJR|SJR|HCR|SCR|LB|LD|HP|SP|HR|SR|SJ|HJ)"
    r"\s?\.?-?\s?(\d{1,5})\b", re.IGNORECASE)
_NYNJ_RE = re.compile(r"\b([SA])\.?\s?-?(\d{2,5})[A-D]?\b")
_SINGLE_LETTER_STATES = {"NY", "NJ"}

_EMPTYISH = {"", "nan", "none", "null", "na", "n/a", "<na>", "nat"}


def _s(row: dict, key: str) -> str:
    v = row.get(key)
    if v is None:
        return ""
    v = str(v).strip()
    return "" if v.lower() in _EMPTYISH else v


def extract_bill_ids(text: str, state: str) -> list[str]:
    """Return normalized identifiers ('HB 1002', 'S 731') found in text.
    Order-preserving, deduplicated. Single-letter chamber prefixes are
    recognized only for NY and NJ."""
    out, seen = [], set()
    for prefix, num in _BILL_RE.findall(text or ""):
        ident = f"{prefix.upper()} {int(num)}"
        if ident not in seen:
            seen.add(ident)
            out.append(ident)
    if state in _SINGLE_LETTER_STATES:
        for prefix, num in _NYNJ_RE.findall(text or ""):
            ident = f"{prefix.upper()} {int(num)}"
            if ident not in seen:
                seen.add(ident)
                out.append(ident)
    return out


def looks_legislative(row: dict) -> bool:
    otype = _s(row, "Opposition Type").lower()
    scope = _s(row, "Scope").lower()
    return ("legislation" in otype or "utility_regulation" in otype
            or "regulatory_action" in otype or scope in {"state", "statewide"})


def record_year(row: dict) -> int | None:
    m = re.match(r"^(\d{4})", _s(row, "Date"))
    return int(m.group(1)) if m else None


def opp_event_id(row: dict) -> str:
    """Same construction as project_resolution.opp_event_id, duplicated here
    so --extract has no import chain."""
    import hashlib
    key = "|".join([_s(row, "Incident"), _s(row, "Date"), _s(row, "State"),
                    str(row.get("Source URL") or "").strip()])
    return "opp_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Stage classification from Open States action classifications
# ---------------------------------------------------------------------------

# Open States machine-codes each action with zero or more classification
# strings. Mapping to the repo's stage ladder, terminal-first. The correct
# outcome column matches qc/stage_ladder.csv exactly.
STAGES = [
    # (stage, correct_outcome, terminal)
    ("Signed into law", "Approved", True),
    ("Vetoed", "Blocked", True),
    ("Failed floor vote", "Blocked", True),
    ("Died in committee", "Blocked", True),
    ("Withdrawn", "Blocked", True),
    ("Passed both chambers", "Approved", False),
    ("Passed one chamber", "Pending", False),
    ("Passed committee only", "Pending", False),
    ("Introduced", "Pending", False),
]
STAGE_OUTCOME = {s: o for s, o, _ in STAGES}
STAGE_PRIORITY = {s: i for i, (s, _, _) in enumerate(STAGES)}

_CLS_TERMINAL = {
    "became-law": "Signed into law",
    "executive-signature": "Signed into law",
    "veto-override-passage": "Signed into law",
    "executive-veto": "Vetoed",
    "executive-veto-line-item": "Vetoed",
    "failure": "Failed floor vote",
    "committee-failure": "Died in committee",
    "committee-passage-unfavorable": "Died in committee",
    "withdrawal": "Withdrawn",
}
_CLS_COMMITTEE_PASS = {"committee-passage", "committee-passage-favorable"}
_CLS_INTRO = {"introduction", "filing", "referral-committee", "reading-1"}


def classify_actions(actions: list[dict]) -> tuple[str, str, str]:
    """Given Open States actions (each with classification list, organization,
    date), return (stage, stage_date, evidence). Terminal actions win over
    milestones; among milestones, chamber passage is counted per distinct
    chamber so passage in both chambers is distinguished from two votes in
    one."""
    best_stage, best_date, best_ev = "", "", ""

    def consider(stage, when, ev):
        nonlocal best_stage, best_date, best_ev
        if not best_stage or STAGE_PRIORITY[stage] < STAGE_PRIORITY[best_stage]:
            best_stage, best_date, best_ev = stage, when, ev

    passage_chambers = set()
    veto_overridden = False
    for a in actions or []:
        cls = a.get("classification") or []
        when = (a.get("date") or "")[:10]
        desc = (a.get("description") or "")[:120]
        org = ((a.get("organization") or {}).get("classification")
               or (a.get("organization") or {}).get("name") or "")
        if "veto-override-passage" in cls:
            veto_overridden = True
        for c in cls:
            if c in _CLS_TERMINAL:
                consider(_CLS_TERMINAL[c], when, f"{c}: {desc}")
        if "passage" in cls and "committee" not in str(org).lower():
            passage_chambers.add(str(org).lower() or "unknown")
        if any(c in _CLS_COMMITTEE_PASS for c in cls):
            consider("Passed committee only", when, f"committee-passage: {desc}")
        if any(c in _CLS_INTRO for c in cls):
            consider("Introduced", when, f"introduction: {desc}")

    # A sustained veto stays Vetoed; an overridden one became law and the
    # terminal map above already coded the override as Signed into law.
    if veto_overridden and best_stage == "Vetoed":
        best_stage = "Signed into law"

    if len(passage_chambers) >= 2:
        # Only upgrade if no terminal already won.
        if not best_stage or STAGE_PRIORITY["Passed both chambers"] < STAGE_PRIORITY[best_stage]:
            latest = max((a.get("date") or "")[:10] for a in actions
                         if "passage" in (a.get("classification") or []))
            best_stage, best_date = "Passed both chambers", latest
            best_ev = f"passage recorded in {len(passage_chambers)} chambers"
    elif len(passage_chambers) == 1:
        if not best_stage or STAGE_PRIORITY["Passed one chamber"] < STAGE_PRIORITY[best_stage]:
            latest = max((a.get("date") or "")[:10] for a in actions
                         if "passage" in (a.get("classification") or []))
            best_stage, best_date = "Passed one chamber", latest
            best_ev = f"passage in {next(iter(passage_chambers))}"

    return best_stage or "Introduced", best_date, best_ev or "no classified actions"


def classify_votes(votes: list[dict]) -> list[dict]:
    """Given Open States vote_events (each with organization, motion_text,
    result, and a votes[] array of {option, voter_name, voter}), return one
    flattened row per (chamber, legislator, option). No score is computed
    here; political_alignment_proxy.py assigns meaning to the raw tally."""
    out = []
    for ve in votes or []:
        chamber = ((ve.get("organization") or {}).get("classification")
                   or (ve.get("organization") or {}).get("name") or "")
        when = (ve.get("start_date") or "")[:10]
        result = ve.get("result") or ""
        motion = (ve.get("motion_text") or "")[:160]
        for pv in ve.get("votes") or []:
            voter = pv.get("voter") or {}
            out.append({
                "chamber": str(chamber).lower(),
                "vote_date": when,
                "result": result,
                "motion_text": motion,
                "legislator_name": pv.get("voter_name") or voter.get("name", ""),
                "legislator_id": voter.get("id", ""),
                "option": (pv.get("option") or "").lower(),
            })
    return out


# ---------------------------------------------------------------------------
# Recorded-status normalization and disagreement logic
# ---------------------------------------------------------------------------

_RECORDED_APPROVED = {"approved", "passed", "enacted", "signed",
                      "passed legislature, awaiting governor signature"}
_RECORDED_BLOCKED = {"defeated", "dead", "died", "failed", "vetoed", "withdrawn",
                     "cancelled", "died-sine-die", "died-in-committee", "rejected"}
_RECORDED_PENDING = {"active", "pending", "hearing", "filed", "proposed",
                     "introduced", "delayed", "ongoing", "in progress", ""}


def normalize_recorded(status: str) -> str:
    s = (status or "").strip().lower()
    if s in _RECORDED_APPROVED:
        return "Approved"
    if s in _RECORDED_BLOCKED:
        return "Blocked"
    if s in _RECORDED_PENDING:
        return "Pending"
    # Compound strings: first clause decides ("passed house (...); pending in
    # senate" is Pending because the record itself says pending).
    if "pending" in s or "awaiting" in s and "signature" not in s:
        return "Pending"
    if any(w in s for w in ("passed", "signed", "enacted", "approved")):
        return "Approved"
    if any(w in s for w in ("dead", "died", "defeat", "fail", "veto", "withdraw")):
        return "Blocked"
    return "Unclassified"


def disagreement(recorded: str, correct: str, stage: str) -> tuple[str, str]:
    """Return (flag, severity). The HF2690 class of error, a milestone coded
    as enacted law, is HIGH. A terminal disposition the record has not caught
    up with is MEDIUM. Everything consistent is a blank flag."""
    if recorded == "Unclassified":
        return "recorded_status_unclassifiable", "LOW"
    if recorded == correct:
        return "", ""
    if recorded == "Approved" and stage in ("Passed committee only",
                                            "Passed one chamber", "Introduced"):
        return "milestone_coded_as_enacted", "HIGH"
    if recorded == "Approved" and correct == "Blocked":
        return "recorded_approved_but_terminal_blocked", "HIGH"
    if recorded == "Blocked" and correct == "Approved":
        return "recorded_blocked_but_enacted", "HIGH"
    if recorded == "Pending" and correct in ("Approved", "Blocked"):
        return "terminal_disposition_not_yet_recorded", "MEDIUM"
    if recorded in ("Approved", "Blocked") and correct == "Pending":
        return "recorded_terminal_but_bill_in_progress", "MEDIUM"
    return "status_mismatch", "LOW"


# ---------------------------------------------------------------------------
# Open States client (stdlib, cached, throttled)
# ---------------------------------------------------------------------------

class Cache:
    def __init__(self, path: str):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except Exception:
                self.data = {}

    def get(self, key: str, refresh_days: int):
        entry = self.data.get(key)
        if not entry:
            return None
        try:
            fetched = datetime.fromisoformat(entry["fetched"])
        except Exception:
            return None
        if (datetime.utcnow() - fetched).days > refresh_days:
            # Terminal bills never change; only re-fetch non-terminal ones.
            if not entry.get("terminal"):
                return None
        return entry

    def put(self, key: str, payload, terminal: bool):
        self.data[key] = {"fetched": datetime.utcnow().isoformat(timespec="seconds"),
                          "terminal": terminal, "payload": payload}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)


_last_call = [0.0]


def api_get(params: dict, api_key: str) -> dict:
    wait = THROTTLE_S - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    url = API_BASE + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"X-API-KEY": api_key,
                                               "User-Agent": "hawthorn-bill-sync/1.0"})
    _last_call[0] = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def lookup_bill(state: str, identifier: str, year: int | None,
                cache: Cache, api_key: str, refresh_days: int) -> dict:
    """Fetch candidate bills for an identifier in a jurisdiction, choose the
    session whose activity best matches the record year, and return a compact
    result dict. Cached by (state, identifier)."""
    key = f"{state}:{identifier}"
    hit = cache.get(key, refresh_days)
    if hit is not None:
        return {"from_cache": True, **hit["payload"]}

    try:
        raw = api_get({"jurisdiction": state.lower(), "identifier": identifier,
                       "include": ["actions", "votes"], "per_page": 20,
                       "sort": "updated_desc"}, api_key)
    except urllib.error.HTTPError as exc:
        return {"lookup_status": f"http_{exc.code}", "candidates": 0}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"lookup_status": f"network_error:{exc}", "candidates": 0}

    results = raw.get("results") or []
    if not results:
        payload = {"lookup_status": "not_found", "candidates": 0}
        cache.put(key, payload, terminal=False)
        cache.save()
        return payload

    def action_years(b):
        ys = {int(a["date"][:4]) for a in (b.get("actions") or [])
              if (a.get("date") or "")[:4].isdigit()}
        return ys

    chosen, tie = None, False
    if year is not None:
        in_year = [b for b in results if year in action_years(b)
                   or (year - 1) in action_years(b)]
        if len(in_year) == 1:
            chosen = in_year[0]
        elif len(in_year) > 1:
            chosen, tie = in_year[0], True
    if chosen is None:
        chosen = results[0]
        tie = len(results) > 1

    stage, stage_date, ev = classify_actions(chosen.get("actions") or [])
    vote_rows = classify_votes(chosen.get("votes") or [])
    payload = {
        "lookup_status": "ambiguous_session" if tie else "matched",
        "candidates": len(results),
        "bill_id": chosen.get("id", ""),
        "openstates_url": chosen.get("openstates_url", ""),
        "session": chosen.get("session", ""),
        # 600, not 160. bill_taxonomy.py reads this field to decide whether a
        # bill reaches data centers, and at 160 characters the field was cutting
        # off the evidence: West Virginia HB 4983's title ended at
        # "certification as a high i", one word short of "impact data center",
        # so the bill read as silent on its own subject. 18 of 108 titles sat
        # at the old cap. A cap is still wanted -- a few states put an entire
        # summary in the title field -- but it belongs past the subject rather
        # than inside it. Bills already cached keep their truncated title until
        # the cache entry expires and refetches; the taxonomy flags those as
        # `title_truncated` and does not read their silence as evidence.
        "title": (chosen.get("title") or "")[:600],
        "latest_action_date": (chosen.get("latest_action_date") or "")[:10],
        "stage": stage, "stage_date": stage_date, "stage_evidence": ev,
        "correct_outcome": STAGE_OUTCOME[stage],
        "n_actions": len(chosen.get("actions") or []),
        "votes": vote_rows,
    }
    terminal = stage in ("Signed into law", "Vetoed", "Failed floor vote",
                         "Died in committee", "Withdrawn")
    cache.put(key, payload, terminal=terminal)
    cache.save()
    return payload


# ---------------------------------------------------------------------------
# Federal pass: Congress.gov API v3 (spec 007, US3)
#
# Open States' congressional coverage is not relied on, so the records with
# State == US were skipped outright. Congress.gov is the source of record for
# federal bills. The same discipline applies: actions are mapped onto the
# STAGES ladder above, terminal first, so a bill that passed one chamber is
# Pending however far it has travelled. A bill whose Congress has ended with
# no terminal action is flagged possible_sine_die for a person, never coded
# dead, exactly as the state pass treats a stale bill.
# ---------------------------------------------------------------------------

CONGRESS_API = "https://api.congress.gov/v3"
CONGRESS_THROTTLE_S = 0.8          # 5,000 requests/hour per key
OUT_FEDERAL = os.path.join(DATA, "bill_sync_federal.csv")

# Prefix letters are matched case-sensitively and "S" may not follow a letter
# or a dot, so "U.S. 50" and "Subpart 5" are not Senate bills.
_FED_RE = re.compile(
    r"(?<![A-Za-z.])"
    r"(H\.?\s?(?i:con)\.?\s?(?i:res)|S\.?\s?(?i:con)\.?\s?(?i:res)"
    r"|H\.?\s?J\.?\s?(?i:res)|S\.?\s?J\.?\s?(?i:res)"
    r"|H\.?\s?(?i:res)|S\.?\s?(?i:res)|H\.?\s?R|S)"
    r"\.?\s?(\d{1,5})\b")
FEDERAL_TYPES = {
    # code: (label, congress.gov URL slug)
    "hr": ("H.R.", "house-bill"),
    "s": ("S.", "senate-bill"),
    "hres": ("H.Res.", "house-resolution"),
    "sres": ("S.Res.", "senate-resolution"),
    "hjres": ("H.J.Res.", "house-joint-resolution"),
    "sjres": ("S.J.Res.", "senate-joint-resolution"),
    "hconres": ("H.Con.Res.", "house-concurrent-resolution"),
    "sconres": ("S.Con.Res.", "senate-concurrent-resolution"),
}
_AGENCY_RE = re.compile(
    r"\b(EPA|FERC|DOE|NERC|BLM|EIA|CEQ|NIST|CAISI|Army|Department|Commission|"
    r"Agency|Bureau|Administration|rule|rulemaking|directive|letter|probe|"
    r"inquiry|report)\b", re.IGNORECASE)


def extract_federal_ids(text: str) -> list[tuple[str, str, int]]:
    """[(label, congress.gov type code, number)], order-preserving, deduped."""
    out, seen = [], set()
    for prefix, num in _FED_RE.findall(text or ""):
        code = re.sub(r"[.\s]", "", prefix).lower()
        if code not in FEDERAL_TYPES:
            continue
        key = (code, int(num))
        if key in seen:
            continue
        seen.add(key)
        out.append((f"{FEDERAL_TYPES[code][0]} {int(num)}", code, int(num)))
    return out


def congress_for_year(year: int) -> int:
    return (year - 1789) // 2 + 1


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def congress_url(congress: int, code: str, number: int) -> str:
    return (f"https://www.congress.gov/bill/{_ordinal(congress)}-congress/"
            f"{FEDERAL_TYPES[code][1]}/{number}")


_FED_AMENDMENT_RE = re.compile(r"\b(amdt|amendment|motion to (table|proceed|recommit))\b",
                               re.IGNORECASE)


def classify_federal_actions(actions: list[dict]) -> tuple[str, str, str]:
    """Congress.gov actions -> (stage, stage_date, evidence) on the STAGES
    ladder. Terminal actions outrank milestones; chamber passage is counted
    per distinct chamber. Amendment and procedural-motion actions are ignored
    for passage and failure, since "S.Amdt. 12 not agreed to" says nothing
    about the bill's own fate."""
    best_stage, best_date, best_ev = "", "", ""

    def consider(stage, when, ev):
        nonlocal best_stage, best_date, best_ev
        if not best_stage or STAGE_PRIORITY[stage] < STAGE_PRIORITY[best_stage]:
            best_stage, best_date, best_ev = stage, when, ev

    passed, override = {}, set()
    for a in actions or []:
        typ = a.get("type") or ""
        text = a.get("text") or ""
        low = text.lower()
        when = (a.get("actionDate") or "")[:10]
        ev = f"{typ}: {text[:120]}"
        procedural = bool(_FED_AMENDMENT_RE.search(text))
        if typ == "BecameLaw" or re.search(r"became (public|private) law", low):
            consider("Signed into law", when, ev)
        if typ == "Veto" or re.search(r"\b(pocket )?vetoed by (the )?president", low):
            consider("Vetoed", when, ev)
        m = re.search(r"passed over veto.*in (house|senate)|(house|senate).*passed over veto", low)
        if m:
            override.add(m.group(1) or m.group(2))
        if not procedural:
            m = re.search(r"passed/agreed to in (house|senate)", low)
            if m:
                ch = m.group(1)
                passed[ch] = max(passed.get(ch, ""), when)
            if re.search(r"failed of passage|failed/not agreed to in (house|senate)", low):
                consider("Failed floor vote", when, ev)
        if re.search(r"ordered to be reported|reported by (the )?committee|reported \(amended\)|"
                     r"reported to (the )?(house|senate)|reported with an amendment", low):
            consider("Passed committee only", when, ev)
        if typ == "IntroReferral" or low.startswith("introduced"):
            consider("Introduced", when, ev)

    if len(override) >= 2 and best_stage == "Vetoed":
        best_stage, best_ev = "Signed into law", "veto overridden in both chambers"

    if len(passed) >= 2:
        if not best_stage or STAGE_PRIORITY["Passed both chambers"] < STAGE_PRIORITY[best_stage]:
            best_stage, best_date = "Passed both chambers", max(passed.values())
            best_ev = "passed/agreed to in House and Senate"
    elif len(passed) == 1:
        if not best_stage or STAGE_PRIORITY["Passed one chamber"] < STAGE_PRIORITY[best_stage]:
            ch, d = next(iter(passed.items()))
            best_stage, best_date = "Passed one chamber", d
            best_ev = f"passed/agreed to in {ch.title()} only"

    return best_stage or "Introduced", best_date, best_ev or "no classified actions"


_last_congress_call = [0.0]


def congress_get(path: str, api_key: str) -> dict:
    """GET a Congress.gov v3 path. The key goes in a header, never the URL,
    so it cannot leak into a log line or an error message."""
    wait = CONGRESS_THROTTLE_S - (time.time() - _last_congress_call[0])
    if wait > 0:
        time.sleep(wait)
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{CONGRESS_API}{path}{sep}format=json",
                                 headers={"X-Api-Key": api_key,
                                          "User-Agent": "hawthorn-bill-sync/1.0"})
    _last_congress_call[0] = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def lookup_federal_bill(congress: int, code: str, number: int, cache: Cache,
                        api_key: str, refresh_days: int,
                        getter=congress_get) -> dict:
    """Bill plus actions from Congress.gov, classified. Cached under
    US:<congress>:<type>:<number> in the same cache file as the state pass."""
    key = f"US:{congress}:{code}:{number}"
    hit = cache.get(key, refresh_days)
    if hit is not None:
        return {"from_cache": True, **hit["payload"]}
    base = f"/bill/{congress}/{code}/{number}"
    try:
        bill = (getter(base, api_key) or {}).get("bill") or {}
        actions = (getter(f"{base}/actions?limit=250", api_key) or {}).get("actions") or []
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            payload = {"lookup_status": "not_found"}
            cache.put(key, payload, terminal=False)
            return payload
        return {"lookup_status": f"http_{exc.code}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"lookup_status": f"network_error:{type(exc).__name__}"}
    if not bill:
        payload = {"lookup_status": "not_found"}
        cache.put(key, payload, terminal=False)
        return payload
    stage, stage_date, ev = classify_federal_actions(actions)
    payload = {
        "lookup_status": "matched", "congress": congress, "bill_type": code,
        "bill_number": number, "title": (bill.get("title") or "")[:600],
        "latest_action_date": ((bill.get("latestAction") or {}).get("actionDate") or "")[:10],
        "stage": stage, "stage_date": stage_date, "stage_evidence": ev,
        "correct_outcome": STAGE_OUTCOME[stage],
        "congress_url": congress_url(congress, code, number),
        "n_actions": len(actions),
    }
    cache.put(key, payload, terminal=stage in TERMINAL_STAGES)
    return payload


TERMINAL_STAGES = ("Signed into law", "Vetoed", "Failed floor vote",
                   "Died in committee", "Withdrawn")

FEDERAL_COLS = ["opp_id", "identifier", "congress", "bill_type", "bill_number",
                "lookup_status", "unmatched_reason", "title", "stage",
                "stage_date", "stage_evidence", "correct_outcome",
                "latest_action_date", "congress_url", "recorded_status",
                "incident", "date"]


def _no_id_reason(w: dict) -> str:
    reason = "no federal bill identifier in record text"
    if w.get("_agency"):
        reason += "; agency or oversight action, not a bill"
    return reason


def resolve_federal(worklist: list[dict], api_key: str, cache: Cache | None,
                    refresh_days: int, today: str | None = None,
                    getter=congress_get) -> tuple[list[dict], list[dict], dict]:
    """(federal rows, review rows, stats). Every federal record gets at least
    one row: matched, or unmatched with a reason (SC-002). Without an API key
    the pass is skipped and recorded, never failed."""
    today = today or date.today().isoformat()
    current = congress_for_year(int(today[:4]))
    fed = [w for w in worklist if w["lookup_status"] == "federal_skip"]
    rows, review = [], []
    stats = {"records": len(fed), "matched": 0, "not_found": 0, "no_bill_id": 0,
             "errors": 0, "skipped_no_key": 0, "api_calls": 0, "cache_hits": 0}
    for w in fed:
        base = {"opp_id": w["opp_id"], "recorded_status": w["recorded_status"],
                "incident": w["incident"], "date": w["date"]}
        ids = w.get("_federal_ids") or []
        if not ids:
            stats["no_bill_id"] += 1
            rows.append({**base, "lookup_status": "no_bill_id",
                         "unmatched_reason": _no_id_reason(w)})
            continue
        year = int(w["record_year"]) if str(w.get("record_year") or "").isdigit() else int(today[:4])
        for label, code, number in ids:
            congress = congress_for_year(year)
            row = {**base, "identifier": label, "congress": congress,
                   "bill_type": code, "bill_number": number}
            if not api_key:
                stats["skipped_no_key"] += 1
                rows.append({**row, "lookup_status": "skipped_no_key",
                             "unmatched_reason": "CONGRESS_API_KEY not set; federal pass skipped"})
                continue
            res = lookup_federal_bill(congress, code, number, cache, api_key,
                                      refresh_days, getter)
            if res.get("lookup_status") == "not_found":
                # A record dated early in a Congress can cite the previous one.
                res = lookup_federal_bill(congress - 1, code, number, cache,
                                          api_key, refresh_days, getter)
            stats["cache_hits" if res.get("from_cache") else "api_calls"] += 1
            status = res.get("lookup_status", "error")
            row.update({k: res.get(k, row.get(k, "")) for k in
                        ("congress", "title", "stage", "stage_date", "stage_evidence",
                         "correct_outcome", "latest_action_date", "congress_url")})
            row["lookup_status"] = status.split(":")[0]
            if status != "matched":
                stats["not_found" if status == "not_found" else "errors"] += 1
                row["unmatched_reason"] = (
                    f"{label} not found in the {_ordinal(congress)} or "
                    f"{_ordinal(congress - 1)} Congress" if status == "not_found"
                    else f"lookup failed ({status}); retried next run")
                rows.append(row)
                continue
            stats["matched"] += 1
            rows.append(row)
            recorded_norm = normalize_recorded(w["recorded_status"])
            flag, sev = disagreement(recorded_norm, res["correct_outcome"], res["stage"])
            ended = int(res.get("congress") or congress) < current
            possible_sine_die = "yes" if ended and res["correct_outcome"] == "Pending" else ""
            if possible_sine_die and not flag:
                flag, sev = "possible_sine_die_unconfirmed", "LOW"
            if flag:
                review.append({
                    "severity": sev, "flag": flag, "opp_id": w["opp_id"],
                    "state": "US", "identifier": label,
                    "recorded_status": w["recorded_status"],
                    "recorded_normalized": recorded_norm, "stage": res["stage"],
                    "correct_outcome": res["correct_outcome"],
                    "stage_date": res.get("stage_date", ""),
                    "stage_evidence": res.get("stage_evidence", ""),
                    "possible_sine_die": possible_sine_die,
                    "openstates_url": res.get("congress_url", ""),
                    "incident": w["incident"], "date": w["date"],
                    "note": "", "venue": "federal",
                })
    if cache is not None and api_key:
        cache.save()
    return rows, review, stats


def merge_review(existing: list[dict], new: list[dict], venue: str) -> list[dict]:
    """Replace one venue's rows in the review worklist and keep the other's.
    Rows written before the venue column existed are state rows."""
    kept = [dict(r, venue=(r.get("venue") or "state")) for r in existing
            if (r.get("venue") or "state") != venue]
    out = kept + [dict(r, venue=venue) for r in new]
    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    out.sort(key=lambda r: (sev_order.get(r.get("severity"), 3), r.get("venue", ""),
                            r.get("state", ""), r.get("identifier", "")))
    return out


def federal_report_lines(rows: list[dict], stats: dict) -> list[str]:
    from collections import Counter
    L = ["## Federal bills (Congress.gov)", ""]
    if stats.get("skipped_no_key"):
        L.append("CONGRESS_API_KEY is not set, so the federal lookup was skipped "
                 "this run. State sync is unaffected. Records without a bill "
                 "identifier are still listed with their reason.")
        L.append("")
    L.append(f"- Federal legislative records: {stats['records']}")
    L.append(f"- Matched: {stats['matched']}, not found: {stats['not_found']}, "
             f"no bill identifier: {stats['no_bill_id']}, skipped (no key): "
             f"{stats['skipped_no_key']}, errors: {stats['errors']}")
    L.append(f"- API calls: {stats['api_calls']}, cache hits: {stats['cache_hits']}")
    stages = Counter(r["stage"] for r in rows if r.get("stage"))
    if stages:
        L += ["", "| Stage reached | Bills |", "| :-- | :-- |"]
        L += [f"| {s} | {n} |" for s, n in sorted(stages.items(), key=lambda x: -x[1])]
    L += ["", "Every federal record is in data/bill_sync_federal.csv, matched or "
          "with the reason it is not. Stages follow the same ladder as the state "
          "pass: passage in one chamber is Pending.", ""]
    return L


def upsert_report_section(path: str, lines: list[str]) -> None:
    head = "## Federal bills (Congress.gov)"
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    if head in text:
        text = text[:text.index(head)].rstrip("\n") + "\n"
    if not text:
        text = "# Bill sync report\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.rstrip("\n") + "\n\n" + "\n".join(lines).rstrip("\n") + "\n")


# ---------------------------------------------------------------------------
# Worklist build (offline)
# ---------------------------------------------------------------------------

def build_worklist() -> list[dict]:
    try:
        import verification_status as vs
        loader = lambda rows: vs.countable_rows(rows)  # noqa: E731
    except ImportError:
        loader = lambda rows: rows  # noqa: E731

    with open(OPPOSITION_CSV, newline="", encoding="utf-8-sig") as fh:
        rows = loader(list(csv.DictReader(fh)))

    out = []
    for r in rows:
        if not looks_legislative(r):
            continue
        state = _s(r, "State").upper()
        blob = " ".join([_s(r, "Incident"), _s(r, "Project Name"),
                         _s(r, "Summary")])[:600]
        idents = extract_bill_ids(blob, state)
        if state == "US":
            status = "federal_skip"
        elif not state:
            status = "no_state"
        elif not idents:
            status = "no_bill_id"
        else:
            status = "ready"
        fed_blob = " ".join([_s(r, "Incident"), _s(r, "Project Name"),
                             _s(r, "Summary")])
        out.append({
            # Not written to the worklist CSV; read by resolve_federal().
            "_federal_ids": extract_federal_ids(fed_blob) if state == "US" else [],
            "_agency": bool(_AGENCY_RE.search(fed_blob)) if state == "US" else False,
            "opp_id": opp_event_id(r),
            "state": state,
            "bill_identifiers": "; ".join(idents),
            "record_year": record_year(r) or "",
            "recorded_status": _s(r, "Status"),
            "recorded_outcome": _s(r, "Community Outcome"),
            "lookup_status": status,
            "incident": _s(r, "Incident")[:120],
            "date": _s(r, "Date"),
        })
    return out


def write_csv(path: str, rows: list[dict], cols: list[str]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


WORKLIST_COLS = ["opp_id", "state", "bill_identifiers", "record_year",
                 "recorded_status", "recorded_outcome", "lookup_status",
                 "incident", "date"]
MATCH_COLS = ["opp_id", "state", "identifier", "lookup_status", "candidates",
              "session", "title", "stage", "stage_date", "stage_evidence",
              "correct_outcome", "latest_action_date", "openstates_url",
              "incident", "date"]
VOTE_COLS = ["opp_id", "state", "identifier", "chamber", "vote_date",
             "result", "motion_text", "legislator_name", "legislator_id",
             "option", "openstates_url", "incident", "date"]
REVIEW_COLS = ["severity", "flag", "opp_id", "state", "identifier",
               "recorded_status", "recorded_normalized", "stage",
               "correct_outcome", "stage_date", "stage_evidence",
               "possible_sine_die", "openstates_url", "incident", "date", "note",
               "venue"]   # appended by spec 007: state | federal


# ---------------------------------------------------------------------------
# Resolve (live)
# ---------------------------------------------------------------------------

def resolve(limit: int | None, refresh_days: int) -> tuple[list[dict], list[dict], list[dict], dict]:
    api_key = os.environ.get("OPENSTATES_API_KEY", "").strip()
    if not api_key:
        print("ERROR: OPENSTATES_API_KEY is not set. Get a free key at "
              "https://open.pluralpolicy.com/accounts/signup/ and export it.")
        sys.exit(1)

    worklist = build_worklist()
    write_csv(OUT_WORKLIST, worklist, WORKLIST_COLS)
    ready = [w for w in worklist if w["lookup_status"] == "ready"]

    cache = Cache(CACHE_PATH)
    matches, review, votes = [], [], []
    stats = {"records": len(worklist), "ready": len(ready), "api_calls": 0,
             "cache_hits": 0, "matched": 0, "not_found": 0, "errors": 0}
    today = date.today().isoformat()
    done = 0

    for w in ready:
        if limit is not None and done >= limit:
            break
        done += 1
        idents = [i.strip() for i in w["bill_identifiers"].split(";") if i.strip()]
        yr = int(w["record_year"]) if w["record_year"] else None
        for ident in idents:
            res = lookup_bill(w["state"], ident, yr, cache, api_key, refresh_days)
            if res.get("from_cache"):
                stats["cache_hits"] += 1
            else:
                stats["api_calls"] += 1
            status = res.get("lookup_status", "error")
            if status.startswith(("http_", "network_")):
                stats["errors"] += 1
            elif status == "not_found":
                stats["not_found"] += 1
            else:
                stats["matched"] += 1
            m = {**{k: "" for k in MATCH_COLS},
                 "opp_id": w["opp_id"], "state": w["state"], "identifier": ident,
                 "lookup_status": status, "candidates": res.get("candidates", 0),
                 "incident": w["incident"], "date": w["date"]}
            for k in ("session", "title", "stage", "stage_date", "stage_evidence",
                      "correct_outcome", "latest_action_date", "openstates_url"):
                m[k] = res.get(k, "")
            matches.append(m)

            for vr in res.get("votes") or []:
                votes.append({
                    "opp_id": w["opp_id"], "state": w["state"], "identifier": ident,
                    "chamber": vr["chamber"], "vote_date": vr["vote_date"],
                    "result": vr["result"], "motion_text": vr["motion_text"],
                    "legislator_name": vr["legislator_name"],
                    "legislator_id": vr["legislator_id"], "option": vr["option"],
                    "openstates_url": res.get("openstates_url", ""),
                    "incident": w["incident"], "date": w["date"],
                })

            if status in ("matched", "ambiguous_session") and res.get("stage"):
                recorded_norm = normalize_recorded(w["recorded_status"])
                flag, sev = disagreement(recorded_norm, res["correct_outcome"],
                                         res["stage"])
                # Sine die is not an Open States action. A non-terminal stage
                # with no activity in over a year is flagged for a human, not
                # auto-coded as dead.
                stale = (res["correct_outcome"] == "Pending"
                         and res.get("latest_action_date", "")
                         and res["latest_action_date"] < f"{int(today[:4]) - 1}-{today[5:]}")
                possible_sine_die = "yes" if stale else ""
                if stale and not flag:
                    flag, sev = "possible_sine_die_unconfirmed", "LOW"
                if flag:
                    review.append({
                        "severity": sev, "flag": flag, "opp_id": w["opp_id"],
                        "state": w["state"], "identifier": ident,
                        "recorded_status": w["recorded_status"],
                        "recorded_normalized": recorded_norm,
                        "stage": res["stage"],
                        "correct_outcome": res["correct_outcome"],
                        "stage_date": res.get("stage_date", ""),
                        "stage_evidence": res.get("stage_evidence", ""),
                        "possible_sine_die": possible_sine_die,
                        "openstates_url": res.get("openstates_url", ""),
                        "incident": w["incident"], "date": w["date"],
                        "note": "", "venue": "state",
                    })

    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    review.sort(key=lambda r: (sev_order.get(r["severity"], 3), r["state"],
                               r["identifier"]))
    return matches, review, votes, stats


def write_report(matches, review, stats, partial_note=""):
    from collections import Counter
    L = []
    a = L.append
    a("# Bill sync report")
    a("")
    a(f"Generated {date.today().isoformat()}. Source of stage truth: Open "
      f"States machine-classified action histories, mapped onto the "
      f"qc/stage_ladder.csv discipline. Nothing here writes to "
      f"master_opposition.csv; every row in data/bill_status_review.csv is a "
      f"human decision.")
    if partial_note:
        a("")
        a(partial_note)
    a("")
    a(f"- Legislative records on the worklist: {stats['records']}, of which "
      f"{stats['ready']} carry a parseable bill identifier")
    a(f"- API calls this run: {stats['api_calls']}, cache hits: "
      f"{stats['cache_hits']}")
    a(f"- Lookups matched: {stats['matched']}, not found: "
      f"{stats['not_found']}, errors: {stats['errors']}")
    a(f"- Review rows: {len(review)}")
    a("")
    if review:
        c = Counter((r["severity"], r["flag"]) for r in review)
        a("| Severity | Flag | Count |")
        a("| :-- | :-- | :-- |")
        for (sev, flag), n in sorted(c.items()):
            a(f"| {sev} | {flag} | {n} |")
        a("")
        a("HIGH rows are the milestone-coded-as-enacted class and terminal "
          "reversals; fix these before any statistic that touches "
          "legislative outcomes ships. MEDIUM rows are dispositions the "
          "record has not caught up with. possible_sine_die rows are LOW "
          "and need a session-calendar check, because Open States emits no "
          "sine die action and the flag is inferred from staleness alone.")
    else:
        a("No disagreements found on the resolved subset.")
    a("")
    stages = Counter(m["stage"] for m in matches if m.get("stage"))
    if stages:
        a("| Stage reached | Bills |")
        a("| :-- | :-- |")
        for s, n in sorted(stages.items(), key=lambda x: -x[1]):
            a(f"| {s} | {n} |")
        a("")
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


# ---------------------------------------------------------------------------
# Self-test (no network)
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  FAIL {label}")
        else:
            print(f"  pass {label}")

    check(extract_bill_ids("Iowa HF 2690 and HSB 123", "IA") == ["HF 2690", "HSB 123"],
          "multi-letter extraction")
    check(extract_bill_ids("HB1002 passed", "GA") == ["HB 1002"], "no-space form")
    check(extract_bill_ids("S731/A796 tariff", "NJ") == ["S 731", "A 796"],
          "NJ single-letter forms")
    check(extract_bill_ids("S731 tariff", "TX") == [], "single-letter blocked outside NY/NJ")
    check(extract_bill_ids("S.9144A/A.10141 moratorium", "NY") == ["S 9144", "A 10141"],
          "NY dotted amended forms")
    check(extract_bill_ids("HB 259, HB 259 again", "MT") == ["HB 259"], "dedup")

    def act(cls, d, org="lower", desc=""):
        return {"classification": cls, "date": d,
                "organization": {"classification": org}, "description": desc}

    st, _, _ = classify_actions([act(["introduction"], "2026-01-05"),
                                 act(["committee-passage"], "2026-02-01")])
    check(st == "Passed committee only", "committee milestone stays a milestone")
    check(STAGE_OUTCOME[st] == "Pending", "committee milestone maps to Pending (HF2690 rule)")

    st, _, _ = classify_actions([act(["passage"], "2026-02-01", "lower")])
    check(st == "Passed one chamber", "one-chamber passage")
    check(STAGE_OUTCOME[st] == "Pending", "one chamber maps to Pending")

    st, _, _ = classify_actions([act(["passage"], "2026-02-01", "lower"),
                                 act(["passage"], "2026-03-01", "upper")])
    check(st == "Passed both chambers", "two distinct chambers")

    st, _, _ = classify_actions([act(["passage"], "2026-02-01", "lower"),
                                 act(["passage"], "2026-03-01", "upper"),
                                 act(["executive-signature", "became-law"], "2026-04-01",
                                     "executive")])
    check(st == "Signed into law", "signature is terminal")

    st, _, _ = classify_actions([act(["passage"], "2026-02-01", "lower"),
                                 act(["executive-veto"], "2026-04-01", "executive")])
    check(st == "Vetoed", "veto outranks passage")

    st, _, _ = classify_actions([act(["executive-veto"], "2026-04-01", "executive"),
                                 act(["veto-override-passage"], "2026-05-01", "lower")])
    check(st == "Signed into law", "override supersedes veto")

    st, _, _ = classify_actions([act(["committee-passage"], "2026-02-01"),
                                 act(["failure"], "2026-03-01")])
    check(st == "Failed floor vote", "floor failure is terminal")

    st, _, _ = classify_actions([])
    check(st == "Introduced", "empty history floors to Introduced")

    check(normalize_recorded("passed") == "Approved", "recorded passed")
    check(normalize_recorded("died-sine-die") == "Blocked", "recorded sine die")
    check(normalize_recorded("hearing") == "Pending", "recorded hearing")
    check(normalize_recorded("passed house (june 16, 2026); pending in senate")
          == "Pending", "compound pending wins")

    f, s = disagreement("Approved", "Pending", "Passed committee only")
    check(f == "milestone_coded_as_enacted" and s == "HIGH", "HF2690 trap flagged HIGH")
    f, s = disagreement("Pending", "Blocked", "Vetoed")
    check(f == "terminal_disposition_not_yet_recorded" and s == "MEDIUM",
          "stale pending flagged MEDIUM")
    f, s = disagreement("Approved", "Approved", "Signed into law")
    check(f == "", "agreement produces no flag")

    check(_s({"x": "nan"}, "x") == "", "_EMPTYISH handling")
    check(_s({"x": None}, "x") == "", "None handling")

    def ve(org, motion, result, date, pvs):
        return {"organization": {"classification": org}, "motion_text": motion,
                "result": result, "start_date": date, "votes": pvs}

    rows = classify_votes([ve("lower", "Passage", "pass", "2026-02-01",
                              [{"option": "yes", "voter_name": "Jane Doe",
                                "voter": {"id": "ocd-person/1"}},
                               {"option": "no", "voter_name": "John Roe",
                                "voter": {"id": "ocd-person/2"}}])])
    check(len(rows) == 2, "classify_votes flattens one row per legislator")
    check(rows[0]["option"] == "yes" and rows[1]["option"] == "no",
          "classify_votes preserves each legislator's own option")
    check(rows[0]["chamber"] == "lower" and rows[0]["vote_date"] == "2026-02-01",
          "classify_votes carries chamber and date")
    check(classify_votes([]) == [], "classify_votes handles no vote events")
    check(classify_votes(None) == [], "classify_votes handles missing votes key")

    # --- federal pass (spec 007, US3) ---
    check([i[0] for i in extract_federal_ids(
        "United States (H.R. 8037); companion H.R.9442; S.4213; HR 7977")]
          == ["H.R. 8037", "H.R. 9442", "S. 4213", "H.R. 7977"],
          "federal ids in dotted, compact and spaced forms")
    check(extract_federal_ids("H.J.Res. 12 and S.Con.Res. 3 and H.Res. 5")
          == [("H.J.Res. 12", "hjres", 12), ("S.Con.Res. 3", "sconres", 3),
              ("H.Res. 5", "hres", 5)], "resolution types map to Congress.gov codes")
    check(extract_federal_ids("U.S. 50 corridor, NSPS Subpart 5, SB 123, section 403") == [],
          "U.S., Subpart, state bill and section numbers are not federal bills")
    check(congress_for_year(2026) == 119 and congress_for_year(2025) == 119
          and congress_for_year(2024) == 118, "Congress number from year")
    check(congress_url(119, "hr", 8037)
          == "https://www.congress.gov/bill/119th-congress/house-bill/8037",
          "congress.gov bill URL")

    fx = os.path.join(ROOT, "tests", "fixtures", "coverage_expansion")

    def load_fx(name):
        with open(os.path.join(fx, name), encoding="utf-8") as fh:
            return json.load(fh)

    one = load_fx("congress_actions_one_chamber.json")["actions"]
    signed = load_fx("congress_actions_signed.json")["actions"]
    st, sd, _ = classify_federal_actions(one)
    check(st == "Passed one chamber" and STAGE_OUTCOME[st] == "Pending" and sd == "2026-06-09",
          "a bill that passed one chamber codes as Pending, not enacted")
    st, _, _ = classify_federal_actions(signed)
    check(st == "Signed into law", "Became Public Law is terminal and outranks passage")
    st, _, _ = classify_federal_actions(
        one + [{"type": "Veto", "text": "Vetoed by President.", "actionDate": "2026-07-01"}])
    check(st == "Vetoed", "a veto outranks chamber passage")
    st, _, _ = classify_federal_actions(
        [{"type": "Floor", "text": "S.Amdt.12 Amendment SA 12 not agreed to in Senate by "
          "Yea-Nay Vote. Failed/not agreed to in Senate.", "actionDate": "2026-05-01"},
         {"type": "IntroReferral", "text": "Introduced in Senate", "actionDate": "2026-04-01"}])
    check(st == "Introduced", "a failed amendment is not a failed bill")
    st, _, _ = classify_federal_actions(
        [{"type": "Committee", "text": "Ordered to be Reported in the Nature of a Substitute.",
          "actionDate": "2026-05-01"}])
    check(st == "Passed committee only" and STAGE_OUTCOME[st] == "Pending",
          "committee report is a milestone (HF2690 rule)")

    bill = load_fx("congress_bill.json")

    def fake_getter(path, key):
        if path.endswith("/actions?limit=250"):
            return {"actions": one}
        if path.startswith("/bill/119/hr/7977"):
            return bill
        raise urllib.error.HTTPError(path, 404, "not found", {}, None)

    wl = [{"lookup_status": "federal_skip", "opp_id": "opp_a", "recorded_status": "passed",
           "incident": "US House (H.R. 7977)", "date": "2026-03-18", "record_year": 2026,
           "_federal_ids": extract_federal_ids("H.R. 7977"), "_agency": False},
          {"lookup_status": "federal_skip", "opp_id": "opp_b", "recorded_status": "active",
           "incident": "EPA", "date": "2026-04-16", "record_year": 2026,
           "_federal_ids": [], "_agency": True},
          {"lookup_status": "ready", "opp_id": "opp_state"}]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cache = Cache(os.path.join(td, "c.json"))
        rows, rev, stt = resolve_federal(wl, "k", cache, 7, "2026-09-29", fake_getter)
        by = {r["opp_id"]: r for r in rows}
        check(stt["records"] == 2 and len(rows) == 2,
              "one federal row per federal record, state records untouched")
        check(by["opp_a"]["lookup_status"] == "matched"
              and by["opp_a"]["correct_outcome"] == "Pending",
              "mocked Congress.gov one-chamber bill resolves as Pending")
        check(len(rev) == 1 and rev[0]["flag"] == "milestone_coded_as_enacted"
              and rev[0]["venue"] == "federal",
              "a record coded passed for a one-chamber bill is a HIGH federal review row")
        check(by["opp_b"]["lookup_status"] == "no_bill_id"
              and "agency or oversight action" in by["opp_b"]["unmatched_reason"],
              "a record with no bill id is listed with its reason")
        calls = []
        resolve_federal(wl, "k", cache, 7, "2026-09-29",
                        lambda p, k: calls.append(p) or fake_getter(p, k))
        check(calls == [], "a second run is served from the bill-sync cache")

        rows, rev, stt = resolve_federal(wl, "", None, 7, "2026-09-29", fake_getter)
        check(stt["skipped_no_key"] == 1 and rev == []
              and {r["lookup_status"] for r in rows} == {"skipped_no_key", "no_bill_id"},
              "without CONGRESS_API_KEY the federal pass is skipped and recorded")

        wl_old = [dict(wl[0], record_year=2024, _federal_ids=[("H.R. 7977", "hr", 7977)])]

        def old_getter(path, key):
            if path.startswith("/bill/118/hr/7977"):
                return {"bill": {"title": "Old bill"}} if "actions" not in path else {"actions": one}
            raise urllib.error.HTTPError(path, 404, "not found", {}, None)
        rows, rev, _ = resolve_federal(wl_old, "k", Cache(os.path.join(td, "d.json")), 7,
                                       "2026-09-29", old_getter)
        check(rev and rev[0]["possible_sine_die"] == "yes",
              "a Pending bill from an ended Congress is flagged possible sine die, not coded dead")

    merged = merge_review([{"severity": "LOW", "state": "IA", "identifier": "HF 1"},
                           {"severity": "HIGH", "state": "US", "identifier": "H.R. 1",
                            "venue": "federal"}],
                          [{"severity": "MEDIUM", "state": "US", "identifier": "S. 2"}],
                          "federal")
    check([(r["identifier"], r["venue"]) for r in merged]
          == [("S. 2", "federal"), ("HF 1", "state")],
          "merge_review replaces only the federal rows and labels legacy rows state")
    check(REVIEW_COLS[-1] == "venue", "venue is appended to the review file, not inserted")

    with tempfile.TemporaryDirectory() as td:
        rp = os.path.join(td, "r.md")
        with open(rp, "w", encoding="utf-8") as fh:
            fh.write("# Bill sync report\n\nstate body\n")
        upsert_report_section(rp, ["## Federal bills (Congress.gov)", "", "v1"])
        upsert_report_section(rp, ["## Federal bills (Congress.gov)", "", "v2"])
        with open(rp, encoding="utf-8") as fh:
            body = fh.read()
        check("state body" in body and "v2" in body and "v1" not in body,
              "the federal report section is replaced, the state report kept")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def leak_audit(paths):
    for path in paths:
        if not os.path.exists(path):
            continue
        hits = sum(1 for line in open(path, encoding="utf-8") if LEAK_RE.search(line))
        name = os.path.relpath(path, ROOT)
        if hits and path == OUT_REPORT:
            print(f"LEAK AUDIT {name}: {hits} hits, inspect before use")
        elif hits:
            print(f"leak audit {name}: {hits} hits in verbatim source/status text "
                  "(review-only file, accepted)")
        else:
            print(f"leak audit {name}: clean")


def read_review(path: str = OUT_REVIEW) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run_federal(refresh_days: int) -> int:
    """The federal pass on its own: data/bill_sync_federal.csv, the federal
    rows of the review worklist (state rows kept), and the report's federal
    section. Exit 0 whether or not CONGRESS_API_KEY is set."""
    api_key = os.environ.get("CONGRESS_API_KEY", "").strip()
    if not api_key:
        print("CONGRESS_API_KEY is not set; federal lookup skipped and recorded. "
              "State sync is unaffected.")
    worklist = build_worklist()
    cache = Cache(CACHE_PATH) if api_key else None
    rows, review, stats = resolve_federal(worklist, api_key, cache, refresh_days)
    write_csv(OUT_FEDERAL, rows, FEDERAL_COLS)
    existing = read_review()
    had_federal = any((r.get("venue") or "state") == "federal" for r in existing)
    if review or (had_federal and not stats["errors"] and api_key):
        write_csv(OUT_REVIEW, merge_review(existing, review, "federal"), REVIEW_COLS)
    elif existing and not had_federal and "venue" not in existing[0]:
        # First run after spec 007: label the state rows without touching them.
        write_csv(OUT_REVIEW, merge_review(existing, [], "federal"), REVIEW_COLS)
    upsert_report_section(OUT_REPORT, federal_report_lines(rows, stats))
    print(f"federal: {stats['records']} records, {stats['matched']} matched, "
          f"{stats['not_found']} not found, {stats['no_bill_id']} without a bill id, "
          f"{stats['skipped_no_key']} skipped (no key), {stats['errors']} errors; "
          f"{len(review)} review rows -> {os.path.relpath(OUT_FEDERAL, ROOT)}")
    leak_audit([OUT_FEDERAL])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true",
                    help="offline: build the worklist only")
    ap.add_argument("--resolve", action="store_true",
                    help="live: query Open States and write review worklist")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the number of records resolved this run")
    ap.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS,
                    help="re-fetch non-terminal bills older than this many days")
    ap.add_argument("--federal", action="store_true",
                    help="federal pass only (Congress.gov; CONGRESS_API_KEY)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.federal:
        return run_federal(args.refresh_days)

    if args.extract:
        worklist = build_worklist()
        write_csv(OUT_WORKLIST, worklist, WORKLIST_COLS)
        from collections import Counter
        c = Counter(w["lookup_status"] for w in worklist)
        print(f"worklist: {len(worklist)} legislative records -> "
              f"{os.path.relpath(OUT_WORKLIST, ROOT)}")
        for k, n in sorted(c.items()):
            print(f"  {k}: {n}")
        fed = [w for w in worklist if w["lookup_status"] == "federal_skip"]
        write_csv(OUT_FEDERAL, [
            {"opp_id": w["opp_id"], "identifier": "; ".join(i[0] for i in w["_federal_ids"]),
             "lookup_status": "pending_lookup" if w["_federal_ids"] else "no_bill_id",
             "unmatched_reason": "" if w["_federal_ids"] else _no_id_reason(w),
             "recorded_status": w["recorded_status"], "incident": w["incident"],
             "date": w["date"]} for w in fed], FEDERAL_COLS)
        print(f"federal: {len(fed)} records, "
              f"{sum(1 for w in fed if w['_federal_ids'])} with a bill identifier -> "
              f"{os.path.relpath(OUT_FEDERAL, ROOT)}")
        leak_audit([OUT_WORKLIST, OUT_FEDERAL])
        return 0

    if args.resolve:
        matches, review, votes, stats = resolve(args.limit, args.refresh_days)
        # Never overwrite a populated review worklist with an empty result
        # from a partial or failed run (signal-harvest lesson).
        partial_note = ""
        if not review and os.path.exists(OUT_REVIEW):
            with open(OUT_REVIEW, encoding="utf-8") as fh:
                existing = max(0, sum(1 for _ in fh) - 1)
            if existing and (stats["errors"] or args.limit is not None):
                print(f"refusing to overwrite populated review worklist "
                      f"({existing} rows) with an empty result from a "
                      f"partial/errored run")
                partial_note = ("Partial run; existing review worklist "
                                "preserved, matches updated only.")
                write_csv(OUT_MATCHES, matches, MATCH_COLS)
                write_csv(OUT_VOTES, votes, VOTE_COLS)
                write_report(matches, review, stats, partial_note)
                leak_audit([OUT_MATCHES, OUT_REPORT])
                return run_federal(args.refresh_days)
        write_csv(OUT_MATCHES, matches, MATCH_COLS)
        write_csv(OUT_REVIEW, review, REVIEW_COLS)
        write_csv(OUT_VOTES, votes, VOTE_COLS)
        if args.limit is not None:
            partial_note = (f"Partial run, limited to {args.limit} records. "
                            f"Review rows reflect only the resolved subset.")
        write_report(matches, review, stats, partial_note)
        print(f"resolved: {stats['matched']} matched, {stats['not_found']} not "
              f"found, {stats['errors']} errors; {stats['api_calls']} API calls, "
              f"{stats['cache_hits']} cache hits")
        print(f"review worklist: {len(review)} rows -> "
              f"{os.path.relpath(OUT_REVIEW, ROOT)}")
        leak_audit([OUT_MATCHES, OUT_REVIEW, OUT_VOTES, OUT_REPORT])
        # Federal pass after the state pass: it merges its own rows into the
        # review worklist the state pass just wrote (spec 007, US3).
        return run_federal(args.refresh_days)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
