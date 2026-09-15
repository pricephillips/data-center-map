#!/usr/bin/env python3
"""
bill_taxonomy.py

Classifies every bill this repository has matched, so that "is this a data
center bill" stops being a yes/no question answered by looking for two words in
a title.

THE PROBLEM WITH THE BINARY GATE. The first version of the position layer
admitted a bill only if its title contained an explicit data center term. That
is safe and it is also wrong about most of the corpus: it withheld 21,477 of
32,024 roll calls, and the bills it withheld were not junk. Maryland's Utility
RELIEF Act creates a data center registry and never says so in its title.
Arizona HB 2756 regulates "extra high load factor customers", which in 2026
means data centers and in 1996 meant steel mills. Ohio HB 96 is a budget bill
that carried the data center sales tax exemption. Promoting those one at a time
by hand does not scale past the handful anybody has patience for.

TWO AXES, NOT ONE FLAG. A law reaches data centers in one of several ways, and
does one of several things to them. Those are different questions and they are
classified separately.

  REACH -- who the instrument binds:

    data_center_specific  the title names data centers, hyperscale facilities
                          or large-scale data facilities. Unambiguous.

    large_load_class      the title binds a load or size class that data
                          centers dominate without naming them: large load
                          customers, high load factor customers, large energy
                          use facilities, a megawatt threshold. The class is
                          the regulated entity, so a vote on it IS a position
                          on how that class is treated.

    sector_vehicle        the title is about utilities, tax, land use or
                          appropriations generally, and the data center content
                          is established by the incident that put the bill in
                          this repository rather than by the bill's own title.
                          Real, and much weaker: a vote on the vehicle is not a
                          position on the provision riding inside it.

    lookup_suspect        the incident says data centers and the fetched bill's
                          title is about immigration, or child restraints in
                          vehicles. That is not a coverage gap, it is a wrong
                          bill, and it is reported as a defect to repair rather
                          than buried with the honest misses.

    unestablished         nothing establishes reach. Withheld.

  INSTRUMENT -- what the law does: moratorium, siting and zoning, ratepayer
  cost allocation, incentive grant, incentive repeal, disclosure, water and
  environmental, supply enablement, local control. Direction (restrictive /
  enabling / disclosure) is derived from the instrument rather than guessed
  separately, so the two can never disagree.

STANCE ELIGIBILITY IS THE POINT. Only `data_center_specific` and
`large_load_class` bills can carry a stance. A `sector_vehicle` bill publishes
its roll calls -- they happened, they are citable -- with no stance attached,
because the roll call cannot separate a vote about the data center provision
from a vote about everything else in the vehicle. That was a hand-set
`subject_scope` field before this module existed; it is now derived.

WHY THE INCIDENT IS ADMISSIBLE EVIDENCE. Every bill is in this repository
because an opposition event referenced it, and that event is a tracker record
with a curated `Objective`, a summary and coded mechanism flags. It is the
reason the bill is here at all, so it is the natural place to look for what the
bill does. Two guards make it usable:

  BILL-SPECIFIC. The incident text must name THIS bill. One incident routinely
  names several bill numbers -- an Arizona record names HB 2756, HB 2795 and
  HB 2457 in one summary -- and bill_sync fans the match out to all of them. An
  unguarded read would attach the first bill's data center content to all three.
  Identifier matching is boundary-aware in both directions: "SB 26" must not
  match Colorado's "SB 26-102", and "HB 275" must not match "HB 2756".

  DOMAIN-COMPATIBLE. The fetched title's subject has to be one a data center
  provision could plausibly ride in. When an incident names a bill and the
  title that came back is about immigration enforcement, the identifier was
  resolved against the wrong bill; that is `lookup_suspect`.

TRUNCATED TITLES. bill_sync stores the first 160 characters of a title, and 18
bills sit at that cap. West Virginia HB 4983's title reaches "certification as
a high i" and stops one word before "impact data center". Absence of a term in
a truncated title is therefore not evidence of absence: a truncated title can
never produce `unestablished` on its own, it falls through to the incident
evidence and is flagged.

Reads
  data/bill_sync_matches.csv        the matched bills and their titles
  master_opposition_clean.csv       the incident each match came from
  data/bill_subject_overrides.csv   human confirmations (source of record)

Writes
  data/bill_taxonomy.csv            one row per bill, with its evidence
  data/bill_taxonomy_report.md      the classification, and what to repair
  data/bill_taxonomy_manifest.json

Usage
  python bill_taxonomy.py --selftest
  python bill_taxonomy.py --build
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

MATCHES = os.path.join(DATA, "bill_sync_matches.csv")
OPPOSITION = os.path.join(HERE, "master_opposition_clean.csv")
OVERRIDES = os.path.join(DATA, "bill_subject_overrides.csv")

OUT_TAXONOMY = os.path.join(DATA, "bill_taxonomy.csv")
OUT_REPORT = os.path.join(DATA, "bill_taxonomy_report.md")
OUT_MANIFEST = os.path.join(DATA, "bill_taxonomy_manifest.json")

csv.field_size_limit(10_000_000)

# bill_sync.py stores `title[:160]`. A title at the cap may have been cut
# mid-subject, so its silence proves nothing.
TITLE_CAP = 160

# ---------------------------------------------------------------------------
# Reach lexicons
# ---------------------------------------------------------------------------

# Tier 1. Names the thing.
SPECIFIC_RE = re.compile(
    r"data[\s\-]?cent(?:er|re)|datacenter|hyperscale|"
    r"large[\s\-]scale data|high[\s\-]impact data", re.I)

# Tier 2. Names a load or size class whose regulated population is dominated by
# data centers. Deliberately excludes bare "energy" and bare "utility": every
# legislature passes those every session.
LARGE_LOAD_RE = re.compile(
    r"large[\s\-]load|large energy use|large energy user|large energy consumer|"
    r"high energy use|high[\s\-]load factor|extra high[\s\-]load|"
    r"high[\s\-]load facilit|large electricity user|large[\s\-]scale load|"
    r"\b\d{2,4}\s*(?:mw|megawatt)", re.I)

# Subjects a data center provision can plausibly ride inside. Presence here is
# not evidence the bill is about data centers -- the incident supplies that --
# it only says the title is compatible with the claim.
COMPATIBLE_DOMAINS = [
    ("energy_utility", re.compile(
        r"\butilit|\belectric|\benergy\b|\bgrid\b|\bpower\b|rate\s?payer|"
        r"interconnect|microgrid|generation|tariff|transmission|"
        r"public service commission|corporation commission|nuclear|"
        r"small modular reactor", re.I)),
    ("taxation", re.compile(
        r"\btax(?:es|ation)?\b|exemption|appropriation|revenue|"
        r"\bcredit\b|abatement|rebate|budget", re.I)),
    ("land_use", re.compile(
        r"zoning|land use|land development|planning code|municipalit|"
        r"local government|county|comprehensive plan|siting|subdivision", re.I)),
    ("environment_water", re.compile(
        r"\bwater\b|environmental|emission|\bnoise\b|groundwater|"
        r"conservation", re.I)),
    ("economic_development", re.compile(
        r"economic development|enterprise zone|\bcommerce\b|incentive|"
        r"job creation", re.I)),
    ("technology", re.compile(
        r"technolog|artificial intelligence|computing|telecommunication", re.I)),
]

# Subjects a data center bill is essentially never titled as. A hit here, when
# the incident insists the bill is about data centers, means the identifier was
# resolved against the wrong bill.
INCOMPATIBLE_DOMAINS = [
    ("immigration", re.compile(r"immigration|noncooperation|sanctuary", re.I)),
    ("criminal_justice", re.compile(
        r"criminal|felony|misdemeanor|sentencing|firearm|\bweapon", re.I)),
    ("health", re.compile(
        r"abortion|medicaid|medicare|health insurance|opioid|vaccin|"
        r"abortion-inducing", re.I)),
    ("vehicles_transport", re.compile(
        r"child restraint|motor vehicle|driver license|seat belt|"
        r"speed limit|highway safety", re.I)),
    ("education", re.compile(
        r"school district|curriculum|tuition|student loan", re.I)),
    ("elections", re.compile(r"\belection|\bballot\b|voter registration", re.I)),
    ("family_social", re.compile(
        r"child care|foster care|adoption|marriage|divorce", re.I)),
    ("food_agriculture", re.compile(
        r"grocery tax|\blivestock\b|\bpoultry\b|meat processing", re.I)),
]

REACH_SPECIFIC = "data_center_specific"
REACH_CLASS = "large_load_class"
REACH_VEHICLE = "sector_vehicle"
REACH_SUSPECT = "lookup_suspect"
REACH_NONE = "unestablished"

# Only these two carry a stance. The whole typology exists to draw this line.
STANCE_ELIGIBLE_REACH = {REACH_SPECIFIC, REACH_CLASS}

# ---------------------------------------------------------------------------
# Instrument lexicon
#
# Ordered, first match wins, same discipline as the direction rules it
# replaces: "sales and use tax exemption; repeal" must hit the repeal rule
# before the grant rule or the classification inverts. Gaps between terms span
# semicolons because legislative titles are clause lists.
# ---------------------------------------------------------------------------

INSTRUMENTS = [
    ("moratorium_prohibition", "restrictive", [
        r"\bmoratori(?:um|a)\b", r"\bprohibit", r"\bban on\b", r"\bforbid",
        r"\bpause\b[^.]{0,40}\b(?:permit|development|application|approval)",
        r"\btemporary limitation\b",
    ]),
    ("incentive_repeal", "restrictive", [
        r"repeal[a-z]*\b[^.]{0,80}\b(?:exemption|incentive|credit|abatement|rebate|tax relief)",
        r"\b(?:exemption|incentive|credit|abatement|rebate|tax relief)\b[^.]{0,80}\brepeal",
        r"\beliminat\w*\b[^.]{0,80}\b(?:exemption|incentive|credit|abatement)",
        r"\bremov\w*\b[^.]{0,60}\btax exemption",
        r"\bexclude\b[^.]{0,60}\b(?:tax|incentive|exemption)",
        r"\breduce\b[^.]{0,40}\btax exemption",
        r"\bend\w*\b[^.]{0,40}\btax (?:exemption|break)",
    ]),
    ("ratepayer_cost_allocation", "restrictive", [
        r"\brate\s?payer protection\b",
        r"\bprotect\w*\b[^.]{0,90}\b(?:customer|ratepayer|resident|family|families)",
        r"\bcost shift", r"\bfrom increased (?:cost|utility|rate)",
        r"\bnon-large load\b", r"\bnon-data cent", r"\brate\s?payer\b",
        r"\bfund\b[^.]{0,40}\bgrid upgrade", r"\bpay\b[^.]{0,40}\bgrid upgrade",
        r"\bcost[\s\-]of[\s\-]service\b", r"\bcost causation\b",
        r"\bpass[\s\-]through\b", r"\bminimum bill",
    ]),
    ("local_control", "restrictive", [
        r"\blocal control\b", r"\blocal approval\b", r"\brestore local\b",
        r"\bnondisclosure agreement", r"\bpreempt",
    ]),
    ("siting_zoning", "restrictive", [
        r"\bsiting\b", r"\bzon(?:ing|ed)\b", r"\bsetback",
        r"\bpermit requirement", r"\bcertificate of (?:operation|need|public)",
        r"\bimpact (?:review|assessment)\b", r"\bpermitted use\b",
        r"\bspecial exception\b", r"\bland development application",
    ]),
    ("water_environmental", "restrictive", [
        r"\bwater (?:use|quantity|consumption|disclosure|review)",
        r"\bclosed[\s\-]loop\b", r"\bemission limit", r"\bnoise (?:limit|study|ordinance)",
        r"\bsound profile\b", r"\bgroundwater\b",
    ]),
    # Disclosure before grant: "reporting on the tax exemption" is a disclosure
    # bill, not a grant of one.
    ("disclosure_reporting", "disclosure", [
        r"\breport(?:ing)?\b", r"\bstud(?:y|ies)\b", r"\btransparen",
        r"\bdisclos", r"\binventor(?:y|ies)\b", r"\bmapping\b",
        r"\bcoordination council\b", r"\badvisory (?:committee|council)\b",
        r"\bregist(?:er|ry|ration)\b",
    ]),
    ("supply_enablement", "enabling", [
        r"\bself[\s\-]generat", r"\bco[\s\-]?locat", r"\bmicrogrid\b",
        r"\bbehind[\s\-]the[\s\-]meter\b", r"\bexpedit", r"\bstreamlin",
        r"\bfast[\s\-]track",
    ]),
    ("incentive_grant", "enabling", [
        r"\bsales (?:and use )?tax exemption\b", r"\btax exemption for\b",
        r"\btax (?:credit|abatement|rebate)\b", r"\bincentive",
        r"\bexemption\b[^.]{0,40}\bqualified\b",
    ]),
]

INSTRUMENTS_COMPILED = [
    (name, direction, [re.compile(p, re.I) for p in pats])
    for name, direction, pats in INSTRUMENTS
]

INSTRUMENT_DIRECTION = {name: d for name, d, _p in INSTRUMENTS}

DATA_CENTER_IN_TEXT = re.compile(
    r"data[\s\-]?cent(?:er|re)|datacenter|hyperscale|large[\s\-]scale data", re.I)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def is_url(s):
    return bool(re.match(r"^https?://", str(s or "").strip(), re.I))


def bill_key(state, identifier):
    return "%s:%s" % (state, identifier) if state and identifier else ""


def opp_event_id(row):
    """Same construction as bill_sync.opp_event_id and
    project_resolution.opp_event_id. Duplicated rather than imported to keep
    this module free of an import chain, exactly as bill_sync duplicates it."""
    key = "|".join([str(row.get(k) or "").strip()
                    for k in ("Incident", "Date", "State", "Source URL")])
    return "opp_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def names_bill(text, identifier):
    """Does `text` name this exact bill?

    Boundary-aware in both directions, because both failure modes are live in
    this corpus: "SB 26" must not match Colorado's "SB 26-102" (a different
    bill under that state's compound numbering), and "HB 275" must not match
    "HB 2756". Separators vary -- "LB1261", "HB 2756", "SB.26" all appear.
    """
    m = re.match(r"^\s*([A-Za-z]{1,4})\s*0*(\d{1,5})\s*$", str(identifier or ""))
    if not m:
        return False
    prefix, number = m.group(1), m.group(2)
    pat = re.compile(r"(?<![A-Za-z0-9])" + prefix + r"[\s.\-]*0*" + number +
                     r"(?![\d\-])", re.I)
    return bool(pat.search(text or ""))


def title_domain(title):
    """(compatible_domain, incompatible_domain). Either may be None."""
    t = title or ""
    incompatible = next((n for n, rx in INCOMPATIBLE_DOMAINS if rx.search(t)), None)
    compatible = next((n for n, rx in COMPATIBLE_DOMAINS if rx.search(t)), None)
    return compatible, incompatible


def classify_instrument(*texts):
    """(instrument, direction, source_index). Reads the texts in order and
    returns the first hit, so the caller can pass title first and incident
    second and know which one decided."""
    for idx, text in enumerate(texts):
        if not text:
            continue
        for name, direction, pats in INSTRUMENTS_COMPILED:
            for p in pats:
                if p.search(text):
                    return name, direction, idx
    return "unclassified", "unclassified", -1


# ---------------------------------------------------------------------------
# reach
# ---------------------------------------------------------------------------

def classify_reach(title, truncated, incident_text, identifier):
    """Returns (reach, basis, evidence).

    `basis` names the rule that decided, `evidence` quotes what it matched, so
    every classification on the published page can be argued with.
    """
    m = SPECIFIC_RE.search(title or "")
    if m:
        return REACH_SPECIFIC, "title_term", m.group(0)

    m = LARGE_LOAD_RE.search(title or "")
    if m:
        return REACH_CLASS, "title_class_term", m.group(0)

    # Beyond here the title says nothing. The incident is the only evidence,
    # and it only counts if it is about THIS bill.
    incident_says_dc = bool(DATA_CENTER_IN_TEXT.search(incident_text or ""))
    incident_names_this = names_bill(incident_text, identifier)

    if not (incident_says_dc and incident_names_this):
        return REACH_NONE, ("incident_silent" if not incident_says_dc
                            else "incident_names_another_bill"), ""

    # An ABSENT title is not an unrecognised one, and the difference is
    # operational. 58 bills here have no title at all because the Open States
    # lookup came back http_429 or not_found -- a spent API quota, not a
    # classification miss. Filing those under the same basis as a genuine
    # miss buries a defect that a re-run with quota simply fixes, so they get
    # their own basis and their own queue in the report.
    if not (title or "").strip():
        return REACH_NONE, "title_unavailable", ""

    compatible, incompatible = title_domain(title)
    if incompatible:
        return REACH_SUSPECT, "title_domain_incompatible", incompatible
    if compatible:
        return REACH_VEHICLE, "incident_corroborated:" + compatible, compatible
    if truncated:
        # A cut title cannot be read as a silent one. WV HB 4983 stops one word
        # before "impact data center".
        return REACH_VEHICLE, "incident_corroborated:title_truncated", "truncated"
    return REACH_NONE, "title_domain_unrecognised", ""


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

TAXONOMY_COLS = [
    "bill_key", "state", "identifier", "title", "title_truncated",
    "session", "stage", "stage_date", "lookup_status", "openstates_url",
    "reach", "reach_basis", "reach_evidence",
    "instrument", "instrument_basis", "direction",
    "stance_eligible", "in_frame_via", "incident_id", "flags",
]


def load_incidents():
    idx = {}
    for r in read_csv(OPPOSITION):
        idx[opp_event_id(r)] = r
    return idx


def load_bills():
    """(state, identifier) -> best row. The same bill appears once per matched
    incident; keep the longest title and prefer a `matched` lookup_status,
    whose session is the certain one."""
    bills = {}
    for r in read_csv(MATCHES):
        st = (r.get("state") or "").strip().upper()
        ident = (r.get("identifier") or "").strip()
        if not st or not ident:
            continue
        key = (st, ident)
        cur = bills.get(key)
        if cur is None:
            bills[key] = dict(r, state=st, identifier=ident,
                              opp_ids={r.get("opp_id") or ""})
            continue
        cur.setdefault("opp_ids", set()).add(r.get("opp_id") or "")
        if len(r.get("title") or "") > len(cur.get("title") or ""):
            cur["title"] = r["title"]
        if r.get("lookup_status") == "matched":
            cur["lookup_status"] = "matched"
            for f in ("openstates_url", "stage", "stage_date", "session", "opp_id"):
                if r.get(f):
                    cur[f] = r[f]
    return bills


def build():
    bills = load_bills()
    incidents = load_incidents()
    overrides = {}
    for r in read_csv(OVERRIDES):
        st = (r.get("state") or "").strip().upper()
        ident = (r.get("identifier") or "").strip()
        if st and ident:
            overrides[(st, ident)] = r

    rows = []
    for (st, ident), b in sorted(bills.items()):
        title = (b.get("title") or "").strip()
        truncated = len(title) >= TITLE_CAP
        # Every incident that matched this bill, not just the preferred row's.
        # The same bill is matched from several incidents, and the row chosen
        # for its title is not necessarily the one carrying the evidence:
        # Colorado SB 26's preferred row joins to a silent incident while
        # another row joins to the one that says "Ban data center development
        # NDAs via SB 26". Reading only the preferred row filed a wrong-bill
        # defect as an honest miss.
        joined = [incidents[i] for i in sorted(b.get("opp_ids") or [])
                  if i in incidents]
        if not joined and b.get("opp_id") in incidents:
            joined = [incidents[b["opp_id"]]]
        inc = joined[0] if joined else None
        objective = next((( j.get("Objective") or "").strip() for j in joined
                          if (j.get("Objective") or "").strip()), "")
        incident_text = " ".join(
            [(j.get("Objective") or "") + " " + (j.get("Summary") or "")
             for j in joined])

        reach, basis, evidence = classify_reach(title, truncated, incident_text, ident)

        # A human override still outranks the automation, in both directions:
        # it can admit a bill the rules missed, and it can demote one they
        # over-read. It can never promote a bill to a stance-bearing reach it
        # has not earned from the title, because that is the judgement the
        # override file is least able to defend.
        ov = overrides.get((st, ident))
        flags = []
        if ov and str(ov.get("confirmed_data_center_bill", "")).strip() == "1" \
                and is_url(ov.get("source_url")):
            scope = (ov.get("subject_scope") or "").strip().lower()
            if reach in (REACH_NONE, REACH_SUSPECT):
                reach = REACH_VEHICLE if scope != "primary" else REACH_CLASS
                basis, evidence = "human_override", ov.get("source_url", "")
                flags.append("admitted_by_override")
            elif scope == "partial" and reach in STANCE_ELIGIBLE_REACH:
                reach = REACH_VEHICLE
                basis = "human_override_demoted"
                flags.append("demoted_by_override")

        instrument, direction, src = classify_instrument(title, objective)
        instrument_basis = ("title" if src == 0 else
                            "incident_objective" if src == 1 else "")

        # Reach decides whether a direction may be published at all. A vehicle
        # keeps its instrument -- it is useful to know the bill is a tax repeal
        # -- and loses its direction, because the vote was cast on the vehicle.
        stance_eligible = reach in STANCE_ELIGIBLE_REACH
        if not stance_eligible:
            direction = "unclassified"

        if truncated:
            flags.append("title_truncated")
        if reach == REACH_SUSPECT:
            flags.append("verify_bill_identity")
        if not inc:
            flags.append("no_incident_join")

        rows.append({
            "bill_key": bill_key(st, ident), "state": st, "identifier": ident,
            "title": title, "title_truncated": "1" if truncated else "0",
            "session": b.get("session", ""), "stage": b.get("stage", ""),
            "stage_date": b.get("stage_date", ""),
            "lookup_status": b.get("lookup_status", ""),
            "openstates_url": b.get("openstates_url", ""),
            "reach": reach, "reach_basis": basis, "reach_evidence": evidence,
            "instrument": instrument, "instrument_basis": instrument_basis,
            "direction": direction,
            "stance_eligible": "1" if stance_eligible else "0",
            "in_frame_via": objective,
            "incident_id": b.get("opp_id", ""),
            "flags": ";".join(flags),
        })

    with open(OUT_TAXONOMY, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TAXONOMY_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    manifest = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bills": len(rows),
        "by_reach": dict(Counter(r["reach"] for r in rows)),
        "by_instrument": dict(Counter(r["instrument"] for r in rows)),
        "by_direction": dict(Counter(r["direction"] for r in rows)),
        "stance_eligible": sum(1 for r in rows if r["stance_eligible"] == "1"),
        "titles_truncated": sum(1 for r in rows if r["title_truncated"] == "1"),
        "lookup_suspect": sum(1 for r in rows if r["reach"] == REACH_SUSPECT),
        "admitted_by_override": sum(1 for r in rows
                                    if "admitted_by_override" in r["flags"]),
        "inputs": {
            "bill_sync_matches.csv": _digest(MATCHES),
            "master_opposition_clean.csv": _digest(OPPOSITION),
            "bill_subject_overrides.csv": _digest(OVERRIDES),
        },
    }
    with open(OUT_MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    write_report(rows, manifest)

    print("bills              %5d" % len(rows))
    for k, v in sorted(manifest["by_reach"].items(), key=lambda kv: -kv[1]):
        print("  %-22s %5d" % (k, v))
    print("stance eligible    %5d" % manifest["stance_eligible"])
    print("lookup suspect     %5d" % manifest["lookup_suspect"])
    for p in (OUT_TAXONOMY, OUT_REPORT, OUT_MANIFEST):
        print("wrote %s" % os.path.relpath(p, HERE))
    return manifest


def _digest(path):
    if not os.path.exists(path):
        return ""
    h = hashlib.blake2b(digest_size=8)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


REACH_BLURB = {
    REACH_SPECIFIC: "the title names data centers. A vote is a position.",
    REACH_CLASS: "the title binds a load or size class data centers dominate. "
                 "A vote is a position on how that class is treated.",
    REACH_VEHICLE: "the data center content is established by the incident that "
                   "put the bill here, not by the title. Votes publish with NO "
                   "stance.",
    REACH_SUSPECT: "the incident says data centers and the fetched title is "
                   "about something else entirely. A wrong bill, not a gap.",
    REACH_NONE: "nothing establishes reach. Withheld.",
}


def write_report(rows, manifest):
    lines = ["# Bill taxonomy", ""]
    lines.append("Generated " + datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".")
    lines.append("")
    lines.append("Every bill this repository has matched, classified on two axes: "
                 "how the law reaches data centers, and what it does to them. "
                 "Reach decides whether a roll call on the bill may carry a stance; "
                 "only `data_center_specific` and `large_load_class` may.")
    lines.append("")
    lines.append("## Reach")
    lines.append("")
    lines.append("| reach | bills | stance? | what it means |")
    lines.append("| --- | --- | --- | --- |")
    for reach in (REACH_SPECIFIC, REACH_CLASS, REACH_VEHICLE, REACH_SUSPECT, REACH_NONE):
        n = manifest["by_reach"].get(reach, 0)
        lines.append("| `%s` | %d | %s | %s |" % (
            reach, n, "yes" if reach in STANCE_ELIGIBLE_REACH else "no",
            REACH_BLURB[reach]))
    lines.append("")

    lines.append("## Instrument")
    lines.append("")
    lines.append("| instrument | direction | bills |")
    lines.append("| --- | --- | --- |")
    for inst, n in sorted(manifest["by_instrument"].items(), key=lambda kv: -kv[1]):
        lines.append("| `%s` | `%s` | %d |" % (
            inst, INSTRUMENT_DIRECTION.get(inst, "—"), n))
    lines.append("")

    suspect = [r for r in rows if r["reach"] == REACH_SUSPECT]
    lines.append("## Bills to repair (%d)" % len(suspect))
    lines.append("")
    lines.append("The incident names the bill and says data centers, and the title "
                 "that came back is about something else. These are not coverage "
                 "gaps: the identifier resolved against the wrong bill, most often "
                 "because a state reuses short numbers across sessions or uses a "
                 "compound numbering the lookup flattened. Repair belongs in "
                 "`bill_sync.py`, not here.")
    lines.append("")
    if suspect:
        lines.append("| state | bill | title that came back | domain | in frame via |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in suspect:
            lines.append("| %s | %s | %s | `%s` | %s |" % (
                r["state"], r["identifier"], r["title"][:60].replace("|", "/"),
                r["reach_evidence"], r["in_frame_via"][:60].replace("|", "/")))
        lines.append("")

    nofetch = [r for r in rows if r["reach_basis"] == "title_unavailable"]
    lines.append("## Bills to re-fetch (%d)" % len(nofetch))
    lines.append("")
    lines.append("These have no title at all: the Open States lookup came back "
                 "`http_429` or `not_found`, so there is nothing to classify "
                 "against. That is a spent API quota rather than a coverage "
                 "judgement, and re-running `bill_sync.py` with quota available "
                 "resolves it without anyone deciding anything. They are listed "
                 "separately from the honest misses for exactly that reason.")
    lines.append("")
    if nofetch:
        lines.append("| state | bill | lookup | in frame via |")
        lines.append("| --- | --- | --- | --- |")
        for r in sorted(nofetch, key=lambda r: (r["state"], r["identifier"]))[:60]:
            lines.append("| %s | %s | `%s` | %s |" % (
                r["state"], r["identifier"], r["lookup_status"] or "—",
                r["in_frame_via"][:70].replace("|", "/")))
        lines.append("")

    trunc = [r for r in rows if r["title_truncated"] == "1"]
    lines.append("## Truncated titles (%d)" % len(trunc))
    lines.append("")
    lines.append("`bill_sync.py` stores the first %d characters of a title. A title "
                 "at that cap may have been cut mid-subject, so its silence is not "
                 "evidence: these fall through to the incident rather than being "
                 "classified `unestablished` on the title alone. West Virginia "
                 "HB 4983 is the case that made this necessary -- its stored title "
                 "ends at \"certification as a high i\", one word before \"impact "
                 "data center\"." % TITLE_CAP)
    lines.append("")

    lines.append("## Every classified bill")
    lines.append("")
    lines.append("| state | bill | reach | basis | instrument | direction | stance | title |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    order = {REACH_SPECIFIC: 0, REACH_CLASS: 1, REACH_VEHICLE: 2,
             REACH_SUSPECT: 3, REACH_NONE: 4}
    for r in sorted(rows, key=lambda r: (order.get(r["reach"], 9),
                                         r["state"], r["identifier"])):
        lines.append("| %s | %s | `%s` | `%s` | `%s` | `%s` | %s | %s |" % (
            r["state"], r["identifier"], r["reach"], r["reach_basis"],
            r["instrument"], r["direction"],
            "yes" if r["stance_eligible"] == "1" else "no",
            r["title"][:55].replace("|", "/")))
    lines.append("")

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest():
    fails = []

    def check(name, cond, detail=""):
        if cond:
            print("  ok   %s" % name)
        else:
            print("  FAIL %s %s" % (name, detail))
            fails.append(name)

    # --- identifier matching: both boundary failures are live in this corpus ---
    check("names the bill it names", names_bill("Repeal data center tax exemptions via HB 96", "HB 96"))
    check("tolerates a missing space", names_bill("LB1261 was signed April 10", "LB 1261"))
    check("tolerates a leading zero", names_bill("passed HB 0096 yesterday", "HB 96"))
    check("SB 26 does not match Colorado's SB 26-102",
          not names_bill("SB 26-102 requires data centers to pay", "SB 26"))
    check("HB 275 does not match HB 2756",
          not names_bill("Require data centers to fund grid upgrades via HB 2756", "HB 275"))
    check("HB 2756 matches itself in that same sentence",
          names_bill("Require data centers to fund grid upgrades via HB 2756", "HB 2756"))
    check("a different chamber is a different bill",
          not names_bill("via HB 96", "SB 96"))
    check("does not match inside a longer token",
          not names_bill("see AHB 96X", "HB 96"))

    # --- reach ---
    r, basis, ev = classify_reach("Data centers: reporting.", False, "", "AB 1577")
    check("an explicit title is data_center_specific",
          (r, basis) == (REACH_SPECIFIC, "title_term"), "got %s/%s" % (r, basis))
    r, basis, ev = classify_reach("utilities; high load factor customers", False, "", "HB 2756")
    check("a load-class title is large_load_class",
          (r, basis) == (REACH_CLASS, "title_class_term"), "got %s/%s" % (r, basis))
    check("a megawatt threshold is a load class",
          classify_reach("relating to facilities over 100 MW", False, "", "HB 1")[0] == REACH_CLASS)
    r, _b, _e = classify_reach("An Act Regarding Taxation", False,
                               "Exclude data centers from Maine BETE via LD 713", "LD 713")
    check("a generic tax title corroborated by its own incident is a vehicle",
          r == REACH_VEHICLE, "got %s" % r)
    r, basis, ev = classify_reach(
        "Immigration law enforcement noncooperation ordinances and policies prohibited",
        False, "Minnesota's HF 16 established comprehensive data center regulation", "HF 16")
    check("an immigration title with a data center incident is a wrong bill",
          (r, ev) == (REACH_SUSPECT, "immigration"), "got %s/%s" % (r, ev))
    r, _b, ev = classify_reach("Weight for Vehicles with Child Restraint System", False,
                               "Ban data center development NDAs via SB 26", "SB 26")
    check("a child restraint title with a data center incident is a wrong bill",
          (r, ev) == (REACH_SUSPECT, "vehicles_transport"), "got %s/%s" % (r, ev))
    r, basis, _e = classify_reach(
        "small modular reactors; zoning; approval", False,
        "Require data centers to fund grid upgrades via HB 2756", "HB 2795")
    check("a bill the incident does not name gets no reach from it",
          (r, basis) == (REACH_NONE, "incident_names_another_bill"),
          "got %s/%s" % (r, basis))
    r, basis, _e = classify_reach("An Act Regarding Energy", False,
                                  "a dispute about a wind farm", "LD 9")
    check("a silent incident establishes nothing",
          (r, basis) == (REACH_NONE, "incident_silent"), "got %s/%s" % (r, basis))

    # The truncation rule, which exists because of WV HB 4983.
    cut = "Authorizing the Department of Commerce to promulgate a legislative rule " \
          "relating to certification of a microgrid district or certification as a high i"
    r, basis, _e = classify_reach(cut, True,
                                  "Mandate data center disclosure via HB 4983", "HB 4983")
    check("the real truncated WV title reaches sector_vehicle",
          r == REACH_VEHICLE, "got %s/%s" % (r, basis))

    # The truncation branch itself only decides when the domain is ALSO
    # unrecognised -- the WV title above carries "microgrid" and so is already
    # domain-compatible. This pair isolates it: same title, same incident, and
    # the only difference is whether the title was cut.
    odd = "An Act Concerning Sundry Matters and Ancillary Provisions Thereto"
    r_cut, basis_cut, _e = classify_reach(odd, True, "Mandate data center "
                                          "disclosure via HB 4983", "HB 4983")
    check("an unrecognised title that was cut falls through to the incident",
          r_cut == REACH_VEHICLE and "truncated" in basis_cut,
          "got %s/%s" % (r_cut, basis_cut))
    r_full, _b, _e = classify_reach(odd, False, "Mandate data center "
                                    "disclosure via HB 4983", "HB 4983")
    check("the same title uncut establishes nothing",
          r_full == REACH_NONE, "got %s" % r_full)

    # --- instrument, and the ordering that carries the direction ---
    i, d, _s = classify_instrument(
        "State Sales and Use Taxes; the data center equipment sales and use tax "
        "exemption; repeal")
    check("a repeal is an incentive_repeal, not a grant",
          (i, d) == ("incentive_repeal", "restrictive"), "got %s/%s" % (i, d))
    i, d, _s = classify_instrument(
        "Providing a sales tax exemption for a qualified data center")
    check("a grant is an incentive_grant", (i, d) == ("incentive_grant", "enabling"))
    i, d, _s = classify_instrument("Data centers: reporting.")
    check("reporting is disclosure, not restriction",
          (i, d) == ("disclosure_reporting", "disclosure"))
    i, _d, _s = classify_instrument("Local government; new data centers; prohibit")
    check("a prohibition is a moratorium", i == "moratorium_prohibition")
    i, _d, _s = classify_instrument("Certified Microgrid Program")
    check("a microgrid programme is supply enablement", i == "supply_enablement")
    i, _d, src = classify_instrument("An Act Regarding Taxation",
                                     "Exclude data centers from Maine tax incentives")
    check("the incident classifies what the title cannot",
          (i, src) == ("incentive_repeal", 1), "got %s/%s" % (i, src))
    i, _d, src = classify_instrument("Data centers; permit requirements",
                                     "Repeal data center tax exemptions")
    check("the title wins when both could classify",
          (i, src) == ("siting_zoning", 0), "got %s/%s" % (i, src))
    check("nothing classifiable stays unclassified",
          classify_instrument("An Act Concerning Sundry Matters")[0] == "unclassified")

    # --- every instrument declares a legal direction ---
    check("all instrument directions are legal",
          all(d in ("restrictive", "enabling", "disclosure")
              for _n, d, _p in INSTRUMENTS))
    check("only the two strong reaches are stance eligible",
          STANCE_ELIGIBLE_REACH == {REACH_SPECIFIC, REACH_CLASS})

    # --- the incident join must actually join ---
    if os.path.exists(OPPOSITION) and os.path.exists(MATCHES):
        inc = load_incidents()
        bills = load_bills()
        joined = sum(1 for b in bills.values() if b.get("opp_id") in inc)
        check("most matched bills join to their incident",
              bills and joined / len(bills) > 0.8,
              "%d of %d joined" % (joined, len(bills)))

    print("")
    if fails:
        print("%d check(s) FAILED: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("all checks passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.build:
        build()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
