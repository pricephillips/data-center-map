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
                                near-zero API calls

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
  python3 bill_sync.py --selftest          fixture tests, no network, no deps

Requires for --resolve: the OPENSTATES_API_KEY environment variable (free key
from https://open.pluralpolicy.com/accounts/signup/). Stdlib only; no new
package dependency, same approach signal_harvest.py takes with GDELT.

Rate limits: the free tier allows roughly 1 request/second and 500/day. The
client throttles to 1.1s between calls and the cache means a bill is fetched
once and refreshed only when --refresh-days has elapsed (default 7).

Scope notes:
  - State legislation only. Open States' local-ordinance coverage is nil and
    its congressional coverage is not relied on; records with State == US are
    written to the worklist with lookup_status federal_skip.
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
        "title": (chosen.get("title") or "")[:160],
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
        out.append({
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
        w = csv.DictWriter(fh, fieldnames=cols)
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
               "possible_sine_die", "openstates_url", "incident", "date", "note"]


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
                        "note": "",
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
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.extract:
        worklist = build_worklist()
        write_csv(OUT_WORKLIST, worklist, WORKLIST_COLS)
        from collections import Counter
        c = Counter(w["lookup_status"] for w in worklist)
        print(f"worklist: {len(worklist)} legislative records -> "
              f"{os.path.relpath(OUT_WORKLIST, ROOT)}")
        for k, n in sorted(c.items()):
            print(f"  {k}: {n}")
        leak_audit([OUT_WORKLIST])
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
                return 0
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
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
