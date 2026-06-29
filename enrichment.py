"""
enrichment.py

Derives nuanced, structured attributes for each opposition record so downstream
consumers can reason about *what kind* of action it is and *how much* it
restricts, rather than collapsing everything into win/loss. Also resolves the
governing jurisdiction so that a county action and a same-named city action are
never conflated.

Derived fields (namespaced qc_ so they never collide with source columns)
-------------------------------------------------------------------------
qc_mechanism            primary opposition mechanism (see MECHANISMS)
qc_mechanisms           all mechanisms detected (multi-label)
qc_restriction_strength 0-5 ordinal, or None for stance-ambiguous legislation
qc_strength_label       human label for the strength
qc_is_block             True only for halts/bans/denials; False for constraints;
                        None for legislation (stance cannot be inferred)
qc_highlight            True for significant non-block restrictions worth surfacing
qc_jurisdiction_level   county / city / township / state / federal / court / utility / local
qc_jurisdiction_key     STATE::locality::level, canonical key for dedup and grouping

Why this matters
----------------
A moratorium halts development (a block). The Linn County, Iowa ordinance does
not: data centers still proceed, but under setbacks, a water-use agreement, and
noise and light limits. Calling that a "block" overstates it. Here it is a
conditional_zoning mechanism at strength 3 with is_block False and highlight
True: meaningful and surfaced, but distinct from a halt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import legislative_outcome as L
    _looks_legislative = L.looks_legislative
except Exception:                       # keep enrichment usable standalone
    _BILL_RE = re.compile(r"\b(?:HF|SF|HB|SB|AB|LB|LD)\s?\d{1,5}\b", re.IGNORECASE)
    def _looks_legislative(rec):        # type: ignore
        blob = " ".join(str(rec.get(k, "")) for k in ("Opposition Type", "Type", "Name", "Title", "Notes"))
        return "legislat" in blob.lower() or bool(_BILL_RE.search(blob))


# ---------------------------------------------------------------------------
# Strength scale
# ---------------------------------------------------------------------------

STRENGTH_LABEL = {
    5: "prohibition",          # permanent ban
    4: "halt",                 # moratorium, project denial / withdrawal
    3: "conditional",          # setbacks, water, noise/light, zoning conditions
    2: "financial/disclosure", # cost or ratepayer rule, incentive repeal, disclosure
    1: "procedural/advocacy",  # public comment, petition, study, pending
    0: "none/unknown",
}

# Mechanisms, in priority order (strongest first). Each: name, strength,
# is_block, list of lowercase substrings to look for in Opposition Type + Summary.
MECHANISMS = [
    ("ban", 5, True, [
        "permanent ban", "permanently ban", "indefinite ban", "indefinitely ban",
        "outright ban", "ban on data cent", "banned data cent", "ban on new data",
        "prohibition on data", "prohibit data cent", "prohibits data cent",
        "prohibiting data", "prohibit data", "initiative to ban", "constitutional amendment",
        "ordinance prohibit",
    ]),
    ("moratorium", 4, True, ["moratorium", "moratoria", "pause on data", "puts a hold",
        "hits pause", "pushes pause", "hold on data center", "temporary pause",
        "pushing pause", "puts a pause", "hold on new data", "freeze on data"]),
    ("project_denial", 4, True, [
        "project_withdrawal", "denied the rezoning", "denied the application",
        "rejected the application", "denied the permit", "denied the special use",
        "denied the special-use", "withdrew its", "developer withdrew", "withdrew the proposal",
        "proposal was withdrawn", "project was withdrawn", "denied the conditional use",
        "rejected the rezoning", "denied rezoning",
    ]),
    ("conditional_zoning", 3, False, [
        "zoning_restriction", "setback", "water study", "water-use agreement", "water use agreement",
        "noise", "decibel", "light pollution", "buffer", "screening", "height limit",
        "unified development ordinance", " udo", "conditional use", "special use permit requirement",
        "overlay district", "sound limit", "waste management plan", "infrastructure cost",
        "community betterment", "road damage", "decommissioning", "zoning amendment",
        "zoning ordinance", "limiting data center zoning", "limit data center zoning",
        "revise zoning", "ordinance regulating", "ordinance making data", "zoning code",
    ]),
    ("infrastructure_opposition", 3, False, [
        "transmission line", "transmission corridor", "powerline", "power line",
        "substation", "pipeline", "67-mile", "transmission project",
    ]),
    ("cost_allocation", 2, False, [
        "ratepayer", "utility cost", "large load tariff", "large-load tariff", "tariff",
        "cost allocation", "cost-allocation", "cost causation", "cost-causation",
        "pay for their own", "bring your own generation", "rate impact", "raising utility costs",
        "raise utility costs", "energy cost", "rate increase",
    ]),
    ("incentive_repeal", 2, False, [
        "tax incentive", "tax abatement", "tax break", "tax exemption", "sales tax exemption",
        "eliminate the incentive", "revoke the incentive", "repeal the incentive",
        "eliminating certain tax", "remove tax",
    ]),
    ("community_benefit", 2, False, [
        "community benefit agreement", "community_benefit_agreement", "host agreement",
        "host community agreement", "property tax to surrounding", "neighborhood fund",
    ]),
    ("disclosure", 2, False, [
        "disclosure", "transparency", "reporting requirement", "report annually",
        "public reporting", "water usage reporting", "energy usage reporting", "annual report",
    ]),
    ("litigation", 2, False, [
        "lawsuit", " sued ", "litigation", "filed suit", "filed a suit", "injunction",
        "appeal", "court challenge", "legal challenge", "petition for review",
    ]),
    ("study", 1, False, [
        "study_or_report", "quantify damages", "quantify monetary", "feasibility study",
        "impact study", "commission a study", "research report", "explores limiting",
    ]),
    ("public_pressure", 1, False, [
        "public_comment", "public comment", "petition", "rally", "protest", "residents objected",
        "packed the", "spoke against", "opposition group", "town hall", "listening session",
        "pack the courthouse", "packed the courthouse", "group forms", "group formed",
        "backlash", "elect anti", "anti-data-center", "residents continue", "residents pack",
        "fight", "oppose", "resign amid",
    ]),
]

_LEGISLATION_TYPES = ("legislation", "bill", "regulatory_action", "utility_regulation",
                      "state_preemption", "tax_policy")


def _blob(record: dict) -> str:
    return " ".join(str(record.get(k, "") or "") for k in
                    ("Opposition Type", "Type", "Category", "Issue Category",
                     "Objective", "Name", "Title", "Project Name", "Notes", "Summary")).lower()


def classify_mechanism(record: dict) -> tuple[str, list[str], int | None, bool | None]:
    """Return (primary_mechanism, all_mechanisms, strength, is_block)."""
    blob = _blob(record)
    opp = str(record.get("Opposition Type", "") or "").lower()

    # Legislation is stance-ambiguous: a bill can be pro- or anti-industry, and
    # win/loss cannot be inferred from the mechanism. Tag it and stop.
    if _looks_legislative(record) or any(t in opp for t in _LEGISLATION_TYPES):
        # Still surface a sub-mechanism if the bill is clearly one of these.
        subs = [name for name, _s, _b, pats in MECHANISMS
                if name in ("cost_allocation", "incentive_repeal", "disclosure", "moratorium")
                and any(p in blob for p in pats)]
        return ("legislation", ["legislation"] + subs, None, None)

    detected: list[tuple[str, int, bool]] = []
    for name, strength, is_block, pats in MECHANISMS:
        if any(p in blob for p in pats):
            detected.append((name, strength, is_block))

    if not detected:
        return ("other", ["other"], 0, False)

    detected.sort(key=lambda d: d[1], reverse=True)        # strongest first
    primary = detected[0][0]
    strength = detected[0][1]
    is_block = any(d[2] for d in detected)
    return (primary, [d[0] for d in detected], strength, is_block)


# ---------------------------------------------------------------------------
# Jurisdiction
# ---------------------------------------------------------------------------

_AUTHORITY_LEVEL = [
    ("county", ("county_commission", "county_board", "board of supervisors", "county")),
    ("city", ("city_council", "city council", "village_board", "village", "town council", "municipal")),
    ("township", ("township_board", "township")),
    ("state", ("state_legislature", "governor", "state agency", "state_agency")),
    ("federal", ("federal_agency", "federal_legislature", "congress", "federal")),
    ("court", ("court",)),
    ("utility", ("utility_commission", "public utility", "puc", "psc")),
]

_LOC_SUFFIX = re.compile(
    r"(?i)\b(county|parish|borough|township|town|village|city|metro|metropolitan|pud)\b")
_CITY_OF = re.compile(r"(?i)\bcity of\b")


def _locality_stem(record: dict) -> str:
    base = str(record.get("County", "") or "").strip() or str(record.get("City", "") or "").strip()
    if not base:
        base = str(record.get("Name", "") or record.get("Incident", "") or "").strip()
    base = _CITY_OF.sub("", base)
    base = _LOC_SUFFIX.sub("", base)
    return re.sub(r"\s+", " ", base).strip().lower()


def jurisdiction_level(record: dict) -> str:
    auth = str(record.get("Authority Level", "") or "").lower()
    for level, keys in _AUTHORITY_LEVEL:
        if any(k in auth for k in keys):
            return level
    # planning_commission / voters / developer / advocacy_org / blank -> infer from fields
    if _looks_legislative(record):
        return "state"
    name = str(record.get("Name", "") or record.get("Incident", "") or "").lower()
    county = str(record.get("County", "") or "").strip()
    city = str(record.get("City", "") or "").strip()
    if "township" in name:
        return "township"
    if _CITY_OF.search(name) or ("city" in name and "county" not in name):
        return "city"
    if "county" in name or (county and not city):
        return "county"
    if city and not county:
        return "city"
    if county:
        return "county"
    return "local"


def jurisdiction_key(record: dict) -> str:
    state = str(record.get("State", "") or "").strip().lower()
    return f"{state}::{_locality_stem(record)}::{jurisdiction_level(record)}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_record(record: dict) -> dict:
    primary, mechanisms, strength, is_block = classify_mechanism(record)
    highlight = bool(is_block is False and (strength or 0) >= 2)
    return {
        "qc_mechanism": primary,
        "qc_mechanisms": mechanisms,
        "qc_restriction_strength": strength,
        "qc_strength_label": (STRENGTH_LABEL.get(strength) if strength is not None else "stance-ambiguous"),
        "qc_is_block": is_block,
        "qc_highlight": highlight,
        "qc_jurisdiction_level": jurisdiction_level(record),
        "qc_jurisdiction_key": jurisdiction_key(record),
    }


def enrich_records(records: list[dict]) -> list[dict]:
    return [enrich_record(r) for r in records]


# ---------------------------------------------------------------------------
# Standalone profile / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import csv, sys
    from collections import Counter
    path = sys.argv[1] if len(sys.argv) > 1 else "master_opposition.csv"
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    enr = enrich_records(rows)

    print(f"Enriched {len(rows)} records.\n")
    print("Mechanism distribution:")
    for m, n in Counter(e["qc_mechanism"] for e in enr).most_common():
        print(f"  {n:>4}  {m}")
    print("\nRestriction strength:")
    for s, n in Counter(e["qc_strength_label"] for e in enr).most_common():
        print(f"  {n:>4}  {s}")
    print("\nis_block:", dict(Counter(str(e["qc_is_block"]) for e in enr)))
    print("highlight (non-block but significant):", sum(1 for e in enr if e["qc_highlight"]))
    print("\nJurisdiction level:")
    for j, n in Counter(e["qc_jurisdiction_level"] for e in enr).most_common():
        print(f"  {n:>4}  {j}")

    # Show the Linn County ordinance specifically, if present
    for r, e in zip(rows, enr):
        if "linn" in (r.get("County", "") + r.get("Incident", "")).lower() and "ordinance" in (r.get("Opposition Type", "")).lower():
            print("\nLinn County example:")
            print(f"  recorded outcome = {r.get('Community Outcome')!r}")
            print(f"  qc_mechanism = {e['qc_mechanism']}  strength = {e['qc_strength_label']}  "
                  f"is_block = {e['qc_is_block']}  highlight = {e['qc_highlight']}")
            print(f"  qc_jurisdiction_key = {e['qc_jurisdiction_key']}")
            break
