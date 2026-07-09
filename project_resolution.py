"""
project_resolution.py — Phase 1 of the predictive modeling roadmap.

Shifts the unit of analysis from opposition EVENT to PROJECT. Links every
opposition record in master_opposition.csv to a project entity seeded from
data/proposals.csv, and constructs a per-project lifecycle skeleton with the
date fields needed for delay measurement.

Additive only. Reads existing files, writes three NEW files:

  data/project_links.csv        confirmed event->project links, with evidence
  data/project_lifecycles.csv   one row per project: lifecycle + opposition rollup
  data/project_link_review.csv  ambiguous candidates for human review (not linked)

Defensibility rules honored:
  - Only links with two independent signals (geo+company, geo+name,
    county+company, or strong name match) are auto-confirmed. Everything
    weaker goes to the review worklist, never into the confirmed set.
  - Statewide/legislative records are never geo-linked to a specific project;
    they require explicit name or company evidence.
  - lifecycle_outcome uses the activity-descriptive ladder only
    (advanced_confirmed / blocked_confirmed / pending). No scorekeeping terms.
  - "Decided" means terminal phases only (approved, construction, operational,
    expansion, rejected, withdrawn). delayed/proposed/preliminary = pending.
  - proposals.lastUpdated is a record-edit timestamp, NOT an event date. It is
    exported as last_status_update and never presented as a decision date.
    decision_date is left blank until a verifiable date is recovered; terminal
    projects missing one are flagged in the review file (DATE_RECOVERY rows).

Run from repo root:  python3 project_resolution.py
"""

from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date

# ---------------------------------------------------------------------------
# Paths (repo-root relative; override via env for tests)
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))
OPPOSITION_CSV = os.environ.get("PR_OPPOSITION", os.path.join(ROOT, "master_opposition.csv"))
PROPOSALS_CSV = os.environ.get("PR_PROPOSALS", os.path.join(ROOT, "data", "proposals.csv"))
OUT_LINKS = os.environ.get("PR_OUT_LINKS", os.path.join(ROOT, "data", "project_links.csv"))
OUT_LIFECYCLES = os.environ.get("PR_OUT_LIFE", os.path.join(ROOT, "data", "project_lifecycles.csv"))
OUT_REVIEW = os.environ.get("PR_OUT_REVIEW", os.path.join(ROOT, "data", "project_link_review.csv"))

# ---------------------------------------------------------------------------
# Vocabulary (activity-descriptive ladder; no scorekeeping terms)
# ---------------------------------------------------------------------------

TERMINAL_ADVANCED = {"approved", "construction", "operational", "expansion"}
TERMINAL_BLOCKED = {"rejected", "withdrawn"}
PENDING_PHASES = {"proposed", "preliminary", "delayed", ""}

PHASE_TO_LIFECYCLE = {}
for _p in TERMINAL_ADVANCED:
    PHASE_TO_LIFECYCLE[_p] = "advanced_confirmed"
for _p in TERMINAL_BLOCKED:
    PHASE_TO_LIFECYCLE[_p] = "blocked_confirmed"
for _p in PENDING_PHASES:
    PHASE_TO_LIFECYCLE[_p] = "pending"

# ---------------------------------------------------------------------------
# Matching thresholds
# ---------------------------------------------------------------------------

GEO_KM_CONFIRM = 20.0        # geo signal counts as confirming evidence within this
GEO_KM_REVIEW = 40.0         # geo-only proximity within this -> review candidate
NAME_JACCARD_STRONG = 0.60   # strong name match (usable with state alone)
NAME_JACCARD_SOFT = 0.34     # soft name match (needs a second signal)

COMPANY_STOPWORDS = {
    "llc", "inc", "corp", "corporation", "company", "co", "group", "holdings",
    "partners", "capital", "development", "developers", "properties", "ventures",
    "platforms", "technologies", "technology", "solutions", "services", "the",
}
NAME_STOPWORDS = {
    "data", "center", "centers", "centre", "campus", "project", "facility",
    "site", "park", "hub", "ai", "the", "of", "at", "a", "an", "and", "phase",
    "proposal", "proposed", "development",
}

LEGISLATIVE_TYPES = {"legislation", "utility_regulation", "regulatory_action"}
_BILL_RE = re.compile(
    r"\b(?:HF|SF|HB|SB|AB|HSB|SSB|HJR|SJR|HCR|SCR|LB|LD|HP|SP|HR|SR)\s?\d{1,5}\b",
    re.IGNORECASE,
)

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
STATE_FULL_TO_ABBREV = {v.lower(): k for k, v in STATE_ABBREV.items()}


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def norm_state(value: str) -> str:
    """Return a two-letter state code, or '' if unresolvable."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) == 2 and v.upper() in STATE_ABBREV:
        return v.upper()
    return STATE_FULL_TO_ABBREV.get(v.lower(), "")


_COUNTY_SUFFIX = re.compile(r"\s+(county|parish|borough)$", re.IGNORECASE)


def norm_county(value: str) -> str:
    v = (value or "").strip().lower()
    v = _COUNTY_SUFFIX.sub("", v)
    return v


def _tokens(value: str, stop: set[str]) -> frozenset[str]:
    toks = re.findall(r"[a-z0-9]+", (value or "").lower())
    return frozenset(t for t in toks if t not in stop and len(t) > 1)


def company_tokens(value: str) -> frozenset[str]:
    """Token set across all companies listed (';' separated)."""
    out: set[str] = set()
    for part in re.split(r"[;,/]| and ", (value or "").lower()):
        out |= _tokens(part, COMPANY_STOPWORDS)
    return frozenset(out)


def name_tokens(value: str) -> frozenset[str]:
    return _tokens(value, NAME_STOPWORDS)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_float(value: str) -> float | None:
    try:
        return float((value or "").strip())
    except ValueError:
        return None


_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "day"),
    (re.compile(r"^(\d{4})-(\d{1,2})$"), "month"),
    (re.compile(r"^(\d{4})$"), "year"),
]


def parse_partial_date(value: str) -> tuple[str, str]:
    """Return (iso_date, precision). Partial dates floor to period start."""
    v = (value or "").strip()
    for pat, precision in _DATE_PATTERNS:
        m = pat.match(v)
        if not m:
            continue
        parts = [int(g) for g in m.groups()]
        y = parts[0]
        mo = parts[1] if len(parts) > 1 else 1
        d = parts[2] if len(parts) > 2 else 1
        try:
            return date(y, mo, d).isoformat(), precision
        except ValueError:
            return "", ""
    # ISO timestamps (createdAt-style) — take date part
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T", v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "day"
    return "", ""


def opp_event_id(record: dict) -> str:
    """Stable id for an opposition row (schema has no native id)."""
    key = "|".join([
        (record.get("Incident") or "").strip(),
        (record.get("Date") or "").strip(),
        (record.get("State") or "").strip(),
        (record.get("Source URL") or "").strip(),
    ])
    return "opp_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def is_statewide_or_legislative(record: dict) -> bool:
    otype = (record.get("Opposition Type") or "").lower()
    if any(t in otype for t in LEGISLATIVE_TYPES):
        return True
    if (record.get("Scope") or "").strip().lower() in {"state", "statewide"}:
        return True
    blob = " ".join((record.get(k) or "") for k in ("Incident", "Project Name", "Summary"))
    return bool(_BILL_RE.search(blob[:400]))


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def prep_projects(rows: list[dict]) -> list[dict]:
    projects = []
    for r in rows:
        announced, precision = parse_partial_date(r.get("date", ""))
        last_upd, _ = parse_partial_date(r.get("lastUpdated", ""))
        phase = (r.get("phase") or "").strip().lower()
        projects.append({
            "project_id": f"prj_{(r.get('id') or '').strip()}",
            "raw": r,
            "name": (r.get("name") or "").strip(),
            "name_toks": name_tokens(r.get("name", "")),
            "co_toks": company_tokens(r.get("companies", "")),
            "state": norm_state(r.get("state", "")),
            "county": norm_county(r.get("counties", "")),
            "lat": parse_float(r.get("lat", "")),
            "lon": parse_float(r.get("lon", "")),
            "phase": phase,
            "lifecycle_outcome": PHASE_TO_LIFECYCLE.get(phase, "pending"),
            "announced_date": announced,
            "announced_precision": precision,
            "last_status_update": last_upd,
        })
    return projects


def prep_event(r: dict) -> dict:
    return {
        "opp_id": opp_event_id(r),
        "raw": r,
        "name_toks": name_tokens(" ".join([r.get("Project Name", ""), r.get("Incident", "")])),
        "pname_toks": name_tokens(r.get("Project Name", "")),
        "co_toks": company_tokens(" ".join([r.get("Company", ""), r.get("Hyperscaler", ""), r.get("Entity", "")])),
        "state": norm_state(r.get("State", "")),
        "county": norm_county(r.get("County", "")),
        "lat": parse_float(r.get("lat", "")),
        "lon": parse_float(r.get("lon", "")),
        "legislative": is_statewide_or_legislative(r),
        "date": (r.get("Date") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def score_pair(ev: dict, pr: dict) -> dict | None:
    """Return evidence dict for an event/project pair, or None if no signal."""
    if ev["state"] and pr["state"] and ev["state"] != pr["state"]:
        return None

    dist = None
    if None not in (ev["lat"], ev["lon"], pr["lat"], pr["lon"]):
        dist = haversine_km(ev["lat"], ev["lon"], pr["lat"], pr["lon"])

    co_j = jaccard(ev["co_toks"], pr["co_toks"])
    nm_j = max(jaccard(ev["name_toks"], pr["name_toks"]),
               jaccard(ev["pname_toks"], pr["name_toks"]))
    county_match = bool(ev["county"] and pr["county"] and ev["county"] == pr["county"])
    state_match = bool(ev["state"] and pr["state"] and ev["state"] == pr["state"])

    signals = []
    if dist is not None and dist <= GEO_KM_CONFIRM:
        signals.append(f"geo:{dist:.1f}km")
    if co_j > 0:
        signals.append(f"company:{co_j:.2f}")
    if nm_j >= NAME_JACCARD_SOFT:
        signals.append(f"name:{nm_j:.2f}")
    if county_match:
        signals.append("county")

    if not signals and not (dist is not None and dist <= GEO_KM_REVIEW):
        return None

    # Confirmation logic: two independent signals, or one very strong name.
    geo_ok = dist is not None and dist <= GEO_KM_CONFIRM
    strong_name = nm_j >= NAME_JACCARD_STRONG
    soft_name = nm_j >= NAME_JACCARD_SOFT
    has_company = co_j > 0

    if ev["legislative"]:
        # Statewide/legislative: geography is not evidence of a project link.
        confirmed = strong_name and (has_company or county_match)
        tier = "leg_name_plus" if confirmed else ""
    elif geo_ok and has_company:
        confirmed, tier = True, "geo_company"
    elif geo_ok and soft_name:
        confirmed, tier = True, "geo_name"
    elif county_match and has_company and state_match:
        confirmed, tier = True, "county_company"
    elif strong_name and state_match:
        confirmed, tier = True, "name_state"
    else:
        confirmed, tier = False, ""

    # Rank for choosing best project per event and for review ordering.
    rank = (co_j * 2.0) + (nm_j * 2.0) + (1.0 if county_match else 0.0)
    if dist is not None:
        rank += max(0.0, (GEO_KM_REVIEW - dist) / GEO_KM_REVIEW)

    return {
        "confirmed": confirmed,
        "tier": tier,
        "rank": rank,
        "distance_km": f"{dist:.1f}" if dist is not None else "",
        "company_jaccard": f"{co_j:.2f}",
        "name_jaccard": f"{nm_j:.2f}",
        "county_match": "yes" if county_match else "no",
        "signals": "; ".join(signals),
    }


def resolve(events: list[dict], projects: list[dict]):
    by_state = defaultdict(list)
    no_state = []
    for pr in projects:
        (by_state[pr["state"]] if pr["state"] else no_state).append(pr)

    links, review = [], []
    for ev in events:
        pool = by_state.get(ev["state"], []) + no_state if ev["state"] else projects
        best_confirmed, best_candidate = None, None
        for pr in pool:
            ev_dist = score_pair(ev, pr)
            if ev_dist is None:
                continue
            entry = (ev_dist["rank"], pr, ev_dist)
            if ev_dist["confirmed"]:
                if best_confirmed is None or entry[0] > best_confirmed[0]:
                    best_confirmed = entry
            else:
                if best_candidate is None or entry[0] > best_candidate[0]:
                    best_candidate = entry

        if best_confirmed:
            _, pr, evd = best_confirmed
            links.append({"event": ev, "project": pr, "evidence": evd})
        elif best_candidate and best_candidate[0] >= 0.9:
            _, pr, evd = best_candidate
            review.append({"event": ev, "project": pr, "evidence": evd,
                           "review_reason": "single_signal_candidate"})
    return links, review


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def write_links(links: list[dict], path: str) -> None:
    cols = ["opp_id", "project_id", "project_name", "match_tier", "signals",
            "distance_km", "company_jaccard", "name_jaccard", "county_match",
            "opp_incident", "opp_date", "opp_type", "opp_state", "opp_county"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for lk in links:
            ev, pr, evd = lk["event"], lk["project"], lk["evidence"]
            w.writerow({
                "opp_id": ev["opp_id"],
                "project_id": pr["project_id"],
                "project_name": pr["name"],
                "match_tier": evd["tier"],
                "signals": evd["signals"],
                "distance_km": evd["distance_km"],
                "company_jaccard": evd["company_jaccard"],
                "name_jaccard": evd["name_jaccard"],
                "county_match": evd["county_match"],
                "opp_incident": ev["raw"].get("Incident", ""),
                "opp_date": ev["date"],
                "opp_type": ev["raw"].get("Opposition Type", ""),
                "opp_state": ev["state"],
                "opp_county": ev["county"],
            })


def write_review(review: list[dict], date_recovery: list[dict], path: str) -> None:
    cols = ["review_type", "opp_id", "project_id", "project_name", "review_reason",
            "signals", "distance_km", "company_jaccard", "name_jaccard",
            "opp_incident", "opp_date", "opp_state", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for rv in review:
            ev, pr, evd = rv["event"], rv["project"], rv["evidence"]
            w.writerow({
                "review_type": "LINK_CANDIDATE",
                "opp_id": ev["opp_id"],
                "project_id": pr["project_id"],
                "project_name": pr["name"],
                "review_reason": rv["review_reason"],
                "signals": evd["signals"],
                "distance_km": evd["distance_km"],
                "company_jaccard": evd["company_jaccard"],
                "name_jaccard": evd["name_jaccard"],
                "opp_incident": ev["raw"].get("Incident", ""),
                "opp_date": ev["date"],
                "opp_state": ev["state"],
                "note": "Single-signal candidate; confirm or reject manually.",
            })
        for dr in date_recovery:
            w.writerow(dr)


def build_lifecycles(projects: list[dict], links: list[dict], path: str) -> list[dict]:
    per_project: dict[str, list[dict]] = defaultdict(list)
    for lk in links:
        per_project[lk["project"]["project_id"]].append(lk["event"])

    cols = ["project_id", "project_name", "state", "county", "phase",
            "lifecycle_outcome", "decided", "announced_date", "announced_precision",
            "decision_date", "decision_date_source", "last_status_update",
            "capacity_mw", "size_acres",
            "n_opposition_events", "first_opposition_date", "last_opposition_date",
            "opposition_span_days", "days_announced_to_first_opposition",
            "opposition_types", "n_opposition_groups", "has_lawsuit"]
    date_recovery_rows = []
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for pr in projects:
            evs = per_project.get(pr["project_id"], [])
            dates = sorted(e["date"] for e in evs if re.match(r"^\d{4}-\d{2}-\d{2}$", e["date"]))
            first_opp = dates[0] if dates else ""
            last_opp = dates[-1] if dates else ""
            span = ""
            if first_opp and last_opp:
                span = str((date.fromisoformat(last_opp) - date.fromisoformat(first_opp)).days)
            ann_to_opp = ""
            if first_opp and pr["announced_date"] and pr["announced_precision"] == "day":
                ann_to_opp = str((date.fromisoformat(first_opp) - date.fromisoformat(pr["announced_date"])).days)
            types = sorted({t.strip() for e in evs
                            for t in (e["raw"].get("Opposition Type") or "").split(";") if t.strip()})
            groups = {g.strip() for e in evs
                      for g in (e["raw"].get("Opposition Groups") or "").split(";") if g.strip()}
            decided = pr["lifecycle_outcome"] in {"advanced_confirmed", "blocked_confirmed"}
            w.writerow({
                "project_id": pr["project_id"],
                "project_name": pr["name"],
                "state": pr["state"],
                "county": pr["county"],
                "phase": pr["phase"],
                "lifecycle_outcome": pr["lifecycle_outcome"],
                "decided": "yes" if decided else "no",
                "announced_date": pr["announced_date"],
                "announced_precision": pr["announced_precision"],
                "decision_date": "",          # never inferred; filled via date recovery
                "decision_date_source": "",
                "last_status_update": pr["last_status_update"],
                "capacity_mw": pr["raw"].get("capacity_mw", ""),
                "size_acres": pr["raw"].get("size_acres", ""),
                "n_opposition_events": len(evs),
                "first_opposition_date": first_opp,
                "last_opposition_date": last_opp,
                "opposition_span_days": span,
                "days_announced_to_first_opposition": ann_to_opp,
                "opposition_types": "; ".join(types),
                "n_opposition_groups": len(groups),
                "has_lawsuit": "yes" if any("lawsuit" in t for t in types) else "no",
            })
            if decided:
                date_recovery_rows.append({
                    "review_type": "DATE_RECOVERY",
                    "opp_id": "",
                    "project_id": pr["project_id"],
                    "project_name": pr["name"],
                    "review_reason": "terminal_without_decision_date",
                    "signals": "", "distance_km": "", "company_jaccard": "",
                    "name_jaccard": "", "opp_incident": "",
                    "opp_date": "", "opp_state": pr["state"],
                    "note": f"Phase '{pr['phase']}' is terminal but no verifiable "
                            "decision date exists. Recover from sources before "
                            "using this project in delay models.",
                })
    return date_recovery_rows


LEAK_TERMS = re.compile(r'"(win|loss)"|\b(wins?|losses|lost)\b', re.IGNORECASE)


def leak_audit(paths: list[str]) -> list[str]:
    hits = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if LEAK_TERMS.search(line):
                    hits.append(f"{p}:{i}: {line.strip()[:100]}")
    return hits


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    opp_rows = load_csv(OPPOSITION_CSV)
    prop_rows = load_csv(PROPOSALS_CSV)
    projects = prep_projects(prop_rows)
    events = [prep_event(r) for r in opp_rows]

    links, review = resolve(events, projects)
    write_links(links, OUT_LINKS)
    date_recovery = build_lifecycles(projects, links, OUT_LIFECYCLES)
    write_review(review, date_recovery, OUT_REVIEW)

    n_linked_projects = len({lk["project"]["project_id"] for lk in links})
    decided = sum(1 for pr in projects
                  if pr["lifecycle_outcome"] in {"advanced_confirmed", "blocked_confirmed"})
    print(f"projects: {len(projects)}  (decided: {decided}, pending: {len(projects) - decided})")
    print(f"opposition events: {len(events)}")
    print(f"confirmed links: {len(links)}  -> {n_linked_projects} projects with opposition")
    print(f"review candidates: {len(review)}  |  date-recovery flags: {len(date_recovery)}")

    hits = leak_audit([OUT_LINKS, OUT_LIFECYCLES, OUT_REVIEW])
    if hits:
        print("LEAK AUDIT FAILED:")
        for h in hits[:20]:
            print("  " + h)
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
