#!/usr/bin/env python3
"""
stakeholder_positions.py

The qualitative layer: what named public officials and governing bodies have
actually done on the record about data centers.

stakeholder_registry.py answers "who decides here". This module answers the
next question a client asks and the harder one to answer honestly: "and what
do they think". It answers it only from acts already recorded in this
repository -- a roll-call vote, a bill sponsorship, a published statement of
priorities, a governing body's own decision -- each carrying the URL it came
from. Nothing here is inferred from a party label, a title, a district's
politics, or a model. If an official has left no record, this module publishes
no position for them, and the county page says so.

WHY THAT RULE IS ABSOLUTE. A stakeholder map is the artifact most likely to be
quoted at someone, and a wrong attributed position is not a data quality
problem, it is a defamation problem and a blown client meeting. Every other
layer in this repository can be wrong by a percentage point. This one is wrong
by naming a person. So the gate is built to withhold far more than it
publishes, and the QC report is written to make what it withheld actionable
rather than invisible.

FOUR EVIDENCE CLASSES
---------------------

roll_call_vote      A legislator's recorded vote on a bill this repository has
                    matched to data center activity. The single strongest
                    evidence class available: a dated, sourced, individually
                    attributed act. 32,000 of them are already in
                    data/bill_sync_votes.csv and were, until now, never read.

bill_sponsorship    A legislator put their name on a bill. Already the basis
                    on which stakeholder_registry.py admits state legislators.

stated_priority     A verbatim, sourced statement of priorities from an
                    official page, carried in the registry's relevance_note.
                    Quoted, never paraphrased into a stance.

governing_body_action
                    A decision by a county commission, city council, township
                    board, planning commission, utility commission or governor,
                    as coded in the opposition tracker. Attributed to the body,
                    never to an individual member -- a 4-3 vote is the board's
                    act, and this module does not know who was in the four.

TWO GATES ON A VOTE
-------------------

1. SUBJECT. The bill's title must contain an explicit data center or large-load
   term. This is not optional caution: bill_sync matched, among others, a
   Colorado bill on child restraints in vehicles, a Minnesota immigration bill
   and an Ohio budget bill. Reading those 800-odd roll calls as data center
   positions would put fabricated positions next to real names. A bill whose
   title does not establish its subject is WITHHELD, listed in the QC report
   ranked by how many votes it would have contributed, and can be promoted by a
   human in data/bill_subject_overrides.csv with a citation. That file is a
   source of record, the same contract stakeholder_registry.py uses for county
   seeds: this module reads it and never writes to it.

2. DIRECTION. Which way the bill cuts, from an ordered rule list on the title.
   Order matters and is the whole design: "sales and use tax exemption; repeal"
   is a restrictive bill, so the repeal rule has to fire before the incentive
   rule or the classification inverts. Every published row carries the rule
   that fired, so any classification can be argued with rather than trusted.

   A bill whose direction no rule establishes is NOT dropped. The vote is
   published as a recorded fact with stance `recorded_vote_no_direction` --
   "voted yes on HB 1234, a data center bill" is true and useful. It simply
   carries no stance.

3. SCOPE, which only an override can set. Half the bills worth promoting are
   vehicles: Maryland's Utility RELIEF Act runs from net metering to grid
   planning and happens to create a data center registry; Oregon HB 4084 is an
   enterprise zone extension that happens to carve data centers out of it.
   Declaring such a bill `partial` publishes its roll calls -- they happened --
   and denies them a direction, because a vote on the vehicle is not a position
   on the provision and the roll call cannot tell the two apart. The rule binds
   both ways: an override may assert a direction only on a bill it also
   declares `primary`, and `partial` suppresses a direction from every source,
   the title rules included.

WHAT A STANCE IS AND IS NOT
---------------------------

Stances are kept on separate axes and are never collapsed into a single
pro-or-anti score. Supporting a reporting requirement is not opposing data
centers; opposing a tax exemption is not opposing data centers either. A
one-dimensional score would be more quotable and would be wrong, so the summary
file labels a record rather than netting it out: `consistently_restrictive` for
a record with nothing on the other side, `mostly_restrictive` for a clear lean,
`mixed` for a genuine split, `single_act` for one vote. The counts sit in their
own columns beside the label, so a reader never has to take the label's word.

Reads
  data/bill_sync_votes.csv          roll calls on matched bills
  data/bill_sync_matches.csv        bill titles, stages, OpenStates links
  data/stakeholder_registry.csv     sponsors, stated priorities, offices
  data/bill_subject_overrides.csv   human confirmations (source of record)
  master_opposition_clean.csv       governing body actions
  data/county_aggregate.csv         FIPS resolution

Writes
  data/stakeholder_positions.csv          published evidence rows
  data/stakeholder_position_summary.csv   one row per person, counts by stance
  data/stakeholder_positions_held.csv     withheld rows, with reasons
  data/stakeholder_positions_qc.md        the gate result, and what to promote
  data/stakeholder_positions_manifest.json

Usage
  python stakeholder_positions.py --selftest
  python stakeholder_positions.py --build
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

VOTES = os.path.join(DATA, "bill_sync_votes.csv")
MATCHES = os.path.join(DATA, "bill_sync_matches.csv")
REGISTRY = os.path.join(DATA, "stakeholder_registry.csv")
OVERRIDES = os.path.join(DATA, "bill_subject_overrides.csv")
OPPOSITION = os.path.join(HERE, "master_opposition_clean.csv")
AGGREGATE = os.path.join(DATA, "county_aggregate.csv")

OUT_POSITIONS = os.path.join(DATA, "stakeholder_positions.csv")
OUT_BILLS = os.path.join(DATA, "position_bills.csv")
OUT_SUMMARY = os.path.join(DATA, "stakeholder_position_summary.csv")
OUT_HELD = os.path.join(DATA, "stakeholder_positions_held.csv")
OUT_QC = os.path.join(DATA, "stakeholder_positions_qc.md")
OUT_MANIFEST = os.path.join(DATA, "stakeholder_positions_manifest.json")

csv.field_size_limit(10_000_000)

# ---------------------------------------------------------------------------
# Gate 1: subject
#
# An explicit term, not a theme. "Energy" is not on this list and will not be:
# every state legislature passes energy bills every session and almost none of
# them are about data centers.
# ---------------------------------------------------------------------------

SUBJECT_TERMS = [
    "data center", "data centre", "datacenter",
    "large energy use", "large-energy-use",
    "large load", "large-load",
    "high energy use", "high-energy-use",
    "high impact data", "high-impact data",
    "high load facilit", "high-load facilit",
    "large energy consumer",
    "hyperscale",
    "large energy user", "large electricity user",
]


def subject_hit(title):
    t = (title or "").lower()
    for term in SUBJECT_TERMS:
        if term in t:
            return term
    return None


# ---------------------------------------------------------------------------
# Gate 2: direction
#
# Ordered. First match wins. `restrictive` means the bill constrains, taxes,
# reviews or removes support from the industry; `enabling` means it grants,
# exempts or accelerates; `disclosure` means it requires information without
# by itself constraining -- kept separate precisely because a legislator who
# votes for a reporting bill has not taken a side.
# ---------------------------------------------------------------------------

DIRECTION_RULES = [
    # Repeal beats incentive: a bill that repeals an exemption mentions the
    # exemption, and a naive incentive rule would read it backwards.
    #
    # The gap between the two terms deliberately spans semicolons. Legislative
    # titles are clause lists -- "State Sales and Use Taxes; the data center
    # equipment sales and use tax exemption; repeal" puts the verb two clauses
    # away from its object. An earlier version of these patterns excluded `;`
    # from the gap and classified that Georgia repeal bill as `enabling`,
    # inverting every vote on it. Sentence-final periods still bound the
    # window, which is enough: a bill title is one bill.
    ("repeal_incentive", "restrictive", [
        r"repeal[a-z]*\b[^.]{0,80}\b(exemption|incentive|credit|abatement|rebate|tax relief)",
        r"\b(exemption|incentive|credit|abatement|rebate|tax relief)\b[^.]{0,80}\brepeal",
        r"\beliminat\w*\b[^.]{0,80}\b(exemption|incentive|credit|abatement)",
        r"\bremov\w*\b[^.]{0,60}\btax exemption",
        r"\bend\w*\b[^.]{0,40}\btax (exemption|break)",
    ]),
    ("moratorium_or_prohibition", "restrictive", [
        r"\bmoratori(um|a)\b",
        r"\bprohibit",
        r"\bban on\b",
        r"\bforbid",
    ]),
    ("ratepayer_protection", "restrictive", [
        r"\bratepayer protection\b",
        r"\bprotect\w*\b[^.;]{0,90}\b(customer|ratepayer|resident|family|families)",
        r"\bcost shift",
        r"\bfrom increased (cost|utility|rate)",
        r"\bnon-large load\b",
        r"\bnon-data cent",
    ]),
    ("local_control", "restrictive", [
        r"\blocal control\b",
        r"\blocal approval\b",
        r"\brestore local\b",
        r"\bnondisclosure agreement",
    ]),
    ("siting_or_permit_condition", "restrictive", [
        r"\bsiting\b", r"\bzon(ing|ed)\b", r"\bsetback",
        r"\bpermit requirement", r"\bemission limit",
        r"\bsite assessment\b", r"\bsound profile\b", r"\bnoise\b",
        r"\bcertificate of (operation|need|public)",
        r"\bwater[- ]quantity review\b",
        r"\bimpact (review|assessment)\b",
        r"\bconditions? for\b[^.;]{0,60}\bapprov",
    ]),
    # Disclosure before incentive: "reporting on the tax exemption" is a
    # disclosure bill, not a grant of one.
    ("disclosure_or_study", "disclosure", [
        r"\breport(ing)?\b", r"\bstud(y|ies|ying)\b", r"\btransparen",
        r"\bdisclos", r"\bstudy commission\b", r"\binventor(y|ies)\b",
        r"\b(state lands|public lands)[;,]?\s*map\b", r"\bmapping\b",
    ]),
    ("grant_incentive", "enabling", [
        r"\bsales (and use )?tax exemption\b",
        r"\btax exemption for\b",
        r"\btax (credit|abatement|rebate)\b",
        r"\bincentive",
        r"\bstreamlin",
        r"\bexpedit",
    ]),
]

DIRECTION_COMPILED = [
    (name, direction, [re.compile(p, re.I) for p in pats])
    for name, direction, pats in DIRECTION_RULES
]


def classify_direction(title):
    """Returns (direction, rule_name). ('unclassified', '') when nothing fires."""
    t = (title or "")
    if not t.strip():
        return "unclassified", ""
    for name, direction, pats in DIRECTION_COMPILED:
        for p in pats:
            if p.search(t):
                return direction, name
    return "unclassified", ""


# ---------------------------------------------------------------------------
# Stance
#
# (direction, vote option) -> stance. Deliberately six labels on three axes
# rather than one axis: netting them out would produce a number that reads as
# a data center approval rating and means nothing of the kind.
# ---------------------------------------------------------------------------

STANCE_BY_VOTE = {
    ("restrictive", "yes"): "supported_restriction",
    ("restrictive", "no"): "opposed_restriction",
    ("enabling", "yes"): "supported_industry_incentive",
    ("enabling", "no"): "opposed_industry_incentive",
    ("disclosure", "yes"): "supported_disclosure",
    ("disclosure", "no"): "opposed_disclosure",
}

# Options that are a matter of record but not a position. "Not voting" on a
# contested bill is sometimes the most interesting fact about a legislator, so
# it is published; it is simply not called a stance.
NON_POSITION_OPTIONS = {"not voting", "absent", "excused", "abstain", "other"}

STANCE_LABEL = {
    "supported_restriction": "Voted for a restriction",
    "opposed_restriction": "Voted against a restriction",
    "supported_industry_incentive": "Voted for an industry incentive",
    "opposed_industry_incentive": "Voted against an industry incentive",
    "supported_disclosure": "Voted for disclosure",
    "opposed_disclosure": "Voted against disclosure",
    "sponsored_restriction": "Sponsored a restriction",
    "sponsored_industry_incentive": "Sponsored an industry incentive",
    "sponsored_disclosure": "Sponsored a disclosure bill",
    "sponsored_bill_no_direction": "Sponsored a data center bill",
    "recorded_vote_no_direction": "Recorded vote, direction not established",
    "did_not_vote": "Did not vote",
    "stated_priority": "Stated priority (quoted)",
    "governing_body_action": "Governing body action",
}

# Stances that assert a direction. Only these are counted into a record label.
DIRECTIONAL_STANCES = set(STANCE_BY_VOTE.values()) | {
    "sponsored_restriction", "sponsored_industry_incentive", "sponsored_disclosure"}

AUTHORITY_BODIES = {
    "county_commission": "County commission",
    "city_council": "City council",
    "township_board": "Township board",
    "village_board": "Village board",
    "planning_commission": "Planning commission",
    "utility_commission": "Utility commission",
    "state_legislature": "State legislature",
    "governor": "Governor",
}

# A decision, not a pending item. An agenda item is not a position.
TERMINAL_STATUS = {"passed", "approved", "defeated", "denied", "rejected",
                   "adopted", "enacted", "dead", "expired", "withdrawn",
                   "cancelled", "vetoed"}

COUNTY_SUFFIX = re.compile(
    r"\s+(county|parish|borough|census area|city and borough|municipality|municipio)$")


# ---------------------------------------------------------------------------
# io helpers
# ---------------------------------------------------------------------------

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def is_url(s):
    return bool(re.match(r"^https?://", str(s or "").strip(), re.I))


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(s or "").lower())).strip("-")


def stable_id(*parts):
    """Short deterministic id. The same vote must yield the same position_id on
    every rebuild or a client's saved export stops joining to the live file."""
    h = hashlib.blake2b("|".join(str(p) for p in parts).encode("utf-8"), digest_size=6)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# bill frame
# ---------------------------------------------------------------------------

def build_bill_frame():
    """(state, identifier) -> bill dict, with the subject and direction gates
    already applied. Titles are taken from the longest title seen for the bill
    across bill_sync_matches rows, since the same bill appears once per matched
    incident and some rows carry a truncated title."""
    overrides = {}
    for r in read_csv(OVERRIDES):
        st = (r.get("state") or "").strip().upper()
        ident = (r.get("identifier") or "").strip()
        if not st or not ident:
            continue
        overrides[(st, ident)] = r

    bills = {}
    for r in read_csv(MATCHES):
        st = (r.get("state") or "").strip().upper()
        ident = (r.get("identifier") or "").strip()
        if not st or not ident:
            continue
        key = (st, ident)
        title = (r.get("title") or "").strip()
        cur = bills.get(key)
        if cur is None:
            bills[key] = {
                "state": st, "identifier": ident, "title": title,
                "session": r.get("session", ""), "stage": r.get("stage", ""),
                "stage_date": r.get("stage_date", ""),
                "openstates_url": r.get("openstates_url", ""),
                "lookup_status": r.get("lookup_status", ""),
            }
        else:
            # Prefer the longest title, and prefer a 'matched' lookup_status
            # when one exists, because it is the row whose session is certain.
            if len(title) > len(cur["title"]):
                cur["title"] = title
            if r.get("lookup_status") == "matched":
                cur["lookup_status"] = "matched"
                if r.get("openstates_url"):
                    cur["openstates_url"] = r["openstates_url"]
                if r.get("stage"):
                    cur["stage"] = r["stage"]
                    cur["stage_date"] = r.get("stage_date", "")

    for key, b in bills.items():
        ov = overrides.get(key)
        term = subject_hit(b["title"])
        scope = (ov.get("subject_scope") or "").strip().lower() if ov else ""
        if scope not in ("primary", "partial"):
            scope = ""
        b["subject_scope"] = scope

        if term:
            b["subject_ok"] = True
            b["subject_basis"] = "title_term:" + term
            # A title that names the subject is the subject; only an override
            # can say a bill is a vehicle carrying data center provisions.
            b["subject_scope"] = scope or "primary"
        elif ov and str(ov.get("confirmed_data_center_bill", "")).strip() == "1" \
                and is_url(ov.get("source_url")):
            b["subject_ok"] = True
            b["subject_basis"] = "human_override" + (":" + scope if scope else "")
            b["override_source"] = ov.get("source_url", "")
        else:
            b["subject_ok"] = False
            b["subject_basis"] = ""
            b["subject_scope"] = ""

        direction, rule = classify_direction(b["title"])
        # A human override may also settle a direction the title does not, but
        # only towards one of the three declared axes, only with a source, and
        # ONLY on a bill whose principal purpose is the data center provisions.
        #
        # That last condition is the one that took research to arrive at. Half
        # the bills worth promoting are omnibus vehicles: Maryland's Utility
        # RELIEF Act runs from net metering to grid planning and happens to
        # create a data center registry; Oregon HB 4084 is an enterprise-zone
        # extension that happens to carve data centers out of it. Their roll
        # calls are real and worth publishing, but a vote on the vehicle is not
        # a position on the provision -- a legislator may have voted for
        # Oregon's tax-break extension and against its data center carve-out in
        # the same breath, and the roll call cannot tell them apart. So a
        # `partial` bill publishes its votes with no stance attached, and the
        # only way to attach one is to declare the bill `primary` and be able
        # to defend that with the cited source.
        if ov and direction == "unclassified" and b.get("subject_scope") == "primary":
            od = (ov.get("direction") or "").strip().lower()
            if od in ("restrictive", "enabling", "disclosure") and is_url(ov.get("source_url")):
                direction, rule = od, "human_override"

        # `partial` suppresses a direction from EVERY source, including the
        # title rules. North Carolina SB 730 is the case that forced this: its
        # title is "Ratepayer Protection Act", so the ratepayer rule fires and
        # would have given every vote on it a stance -- but the same bill
        # reshapes long-range power planning and carries fossil and nuclear
        # provisions that drew their own opposition, which is why a human
        # marked it a vehicle. A title that declares a direction is still only
        # the title; if the bill is a vehicle, the roll call still cannot
        # separate a vote about the data center provisions from a vote about
        # everything else riding with them. Only a human declaring the bill
        # `primary` lets a direction through, and that declaration has to be
        # defensible from the cited source.
        if b.get("subject_scope") == "partial":
            direction, rule = "unclassified", ""

        b["direction"] = direction
        b["direction_rule"] = rule

    return bills


# ---------------------------------------------------------------------------
# evidence builders
# ---------------------------------------------------------------------------

# The position file is normalised against data/position_bills.csv rather than
# carrying a bill's title, stage and classification on every one of its ~10,000
# vote rows. The denormalised version of this file was 7.8 MB, most of it the
# same 72 bill titles repeated; the browser has to fetch it alongside a 3.5 MB
# opposition file, so the repetition was the difference between a page that
# loads and one that does not. `bill_key` is "STATE:IDENTIFIER".
#
# evidence_summary is likewise left blank on vote rows, where it was a sentence
# mechanically composed from fields already in the row. It is populated only
# where it carries something the other columns do not -- a quoted statement of
# priorities, or a governing body's own description of what it decided.
POSITION_COLS = [
    "position_id", "level", "state", "fips", "county_name", "jurisdiction",
    "person_name", "person_id", "office", "office_class", "party",
    "stance", "stance_basis", "bill_key", "bill_identifier",
    "vote_option", "vote_result", "vote_motion", "vote_chamber",
    "evidence_date", "evidence_summary", "source_url", "confidence",
    "qc_status", "qc_flags",
]

BILL_COLS = [
    "bill_key", "state", "identifier", "title", "session", "stage", "stage_date",
    "direction", "direction_rule", "subject_basis", "subject_scope",
    "lookup_status", "openstates_url", "n_positions",
]


def bill_key(state, identifier):
    return "%s:%s" % (state, identifier) if state and identifier else ""

HELD_COLS = ["level", "state", "identifier", "person_name", "reason",
             "detail", "rows_affected"]


def build_vote_positions(bills, held, counters):
    rows = []
    votes = read_csv(VOTES)
    counters["votes_read"] = len(votes)

    withheld_by_bill = Counter()
    withheld_reason = {}

    for v in votes:
        st = (v.get("state") or "").strip().upper()
        ident = (v.get("identifier") or "").strip()
        name = (v.get("legislator_name") or "").strip()
        key = (st, ident)
        b = bills.get(key)

        if not name:
            counters["vote_no_name"] += 1
            continue
        if b is None:
            withheld_by_bill[key] += 1
            withheld_reason[key] = ("bill_not_in_match_file",
                                    "vote references a bill with no row in bill_sync_matches")
            continue
        if not b["title"].strip():
            withheld_by_bill[key] += 1
            withheld_reason[key] = ("bill_title_missing",
                                    "no title available, so subject cannot be established")
            continue
        if not b["subject_ok"]:
            withheld_by_bill[key] += 1
            withheld_reason[key] = (
                "subject_not_established",
                "title carries no explicit data center or large-load term: " +
                b["title"][:140])
            continue

        url = (v.get("openstates_url") or b.get("openstates_url") or "").strip()
        if not is_url(url):
            counters["vote_no_source"] += 1
            withheld_by_bill[key] += 1
            withheld_reason[key] = ("no_source_url",
                                    "no OpenStates URL on the vote or the bill")
            continue

        option = (v.get("option") or "").strip().lower()
        direction = b["direction"]
        flags = []

        if option in ("yes", "no") and direction in ("restrictive", "enabling", "disclosure"):
            stance = STANCE_BY_VOTE[(direction, option)]
            confidence = "high" if b["lookup_status"] == "matched" else "medium"
            if b["lookup_status"] != "matched":
                flags.append("bill_session_ambiguous")
        elif option in ("yes", "no"):
            stance = "recorded_vote_no_direction"
            confidence = "recorded_only"
            flags.append("direction_not_established")
        elif option in NON_POSITION_OPTIONS:
            stance = "did_not_vote"
            confidence = "recorded_only"
        else:
            counters["vote_option_unreadable"] += 1
            continue

        motion = (v.get("motion_text") or "").strip()
        vote_date = (v.get("vote_date") or "").strip()

        rows.append({
            "position_id": "vote-" + stable_id(st, ident, v.get("legislator_id") or name,
                                               vote_date, motion, option),
            "level": "state",
            "state": st,
            "fips": "",
            "county_name": "",
            "jurisdiction": st + " legislature",
            "person_name": name,
            "person_id": (v.get("legislator_id") or "").strip(),
            # The chamber lives in vote_chamber, not glued into the office
            # string. Rendered inline it produced a "State legislator (upper"
            # column that clipped in every table it appeared in, and it is a
            # field of its own, not part of the office's name.
            "office": "State legislator",
            "office_class": "legislator",
            "party": "",
            "stance": stance,
            "stance_basis": "roll_call_vote",
            "bill_key": bill_key(st, ident),
            "bill_identifier": ident,
            "vote_option": option,
            "vote_result": (v.get("result") or "").strip(),
            "vote_motion": motion,
            "vote_chamber": (v.get("chamber") or "").strip(),
            "evidence_date": vote_date,
            "evidence_summary": "",
            "source_url": url,
            "confidence": confidence,
            "qc_status": "published",
            "qc_flags": ";".join(flags),
        })

    for key, n in withheld_by_bill.items():
        reason, detail = withheld_reason[key]
        b = bills.get(key)
        held.append({
            "level": "state", "state": key[0], "identifier": key[1],
            "person_name": "", "reason": reason,
            "detail": detail, "rows_affected": n,
        })
        counters["votes_withheld"] += n

    return rows


def build_sponsor_positions(bills, registry, held, counters):
    """Sponsorship rows already in the stakeholder registry, re-read as
    positions. The registry admitted them because they sponsored a matched
    bill; this attaches the bill's direction to that act."""
    rows = []
    for r in registry:
        if r.get("office_class") != "bill_sponsor":
            continue
        name = (r.get("name") or "").strip()
        st = (r.get("state") or "").strip().upper()
        office = (r.get("office") or "").strip()
        url = (r.get("source_url") or "").strip()
        if not name or not is_url(url):
            held.append({"level": "state", "state": st, "identifier": "",
                         "person_name": name, "reason": "sponsor_row_unusable",
                         "detail": "missing name or source url", "rows_affected": 1})
            continue

        # The registry records the bill in the office string, e.g.
        # "Sponsor, SB 526". Parse it rather than guess; no identifier means
        # no bill to attach a direction to.
        m = re.search(r"\b([A-Z]{1,4}\s?\d{1,5})\b", office)
        ident = m.group(1).strip() if m else ""
        if ident and " " not in ident:
            ident = re.sub(r"^([A-Z]+)(\d+)$", r"\1 \2", ident)
        b = bills.get((st, ident))

        if b is None or not b.get("subject_ok"):
            stance = "sponsored_bill_no_direction"
            direction, rule, basis = "unclassified", "", ""
            flags = ["bill_not_resolved"] if b is None else ["subject_not_established"]
        else:
            direction = b["direction"]
            rule = b["direction_rule"]
            basis = b["subject_basis"]
            stance = {
                "restrictive": "sponsored_restriction",
                "enabling": "sponsored_industry_incentive",
                "disclosure": "sponsored_disclosure",
            }.get(direction, "sponsored_bill_no_direction")
            flags = [] if direction != "unclassified" else ["direction_not_established"]

        rows.append({
            "position_id": "sponsor-" + stable_id(st, ident, name),
            "level": "state", "state": st, "fips": "", "county_name": "",
            "jurisdiction": (r.get("jurisdiction") or (st + " legislature")),
            "person_name": name, "person_id": "",
            "office": office, "office_class": "bill_sponsor",
            "party": (r.get("party") or "").strip(),
            "stance": stance,
            "stance_basis": "bill_sponsorship",
            "bill_key": bill_key(st, ident) if b else "",
            "bill_identifier": ident,
            "vote_option": "", "vote_result": "", "vote_motion": "", "vote_chamber": "",
            "evidence_date": (r.get("source_retrieved") or "").strip(),
            "evidence_summary": "%s is recorded as a sponsor of %s %s." % (
                name, st, ident) if ident else "%s is recorded as a bill sponsor." % name,
            "source_url": url,
            "confidence": "high" if (b and b.get("subject_ok")
                                     and direction != "unclassified") else "recorded_only",
            "qc_status": "published",
            "qc_flags": ";".join(flags),
        })
        counters["sponsor_rows"] += 1
    return rows


def build_stated_priorities(registry, held, counters):
    """The registry's relevance_note, republished as a quoted position.

    The note is carried verbatim. It is never summarised into a stance, and a
    note without its own source URL is withheld rather than attributed to the
    office page it sits next to -- the page that names an official is not
    automatically the page that records what they said.
    """
    rows = []
    for r in registry:
        note = (r.get("relevance_note") or "").strip()
        if not note:
            continue
        name = (r.get("name") or "").strip()
        src = (r.get("relevance_source_url") or "").strip()
        if not name:
            continue
        if not is_url(src):
            held.append({"level": r.get("level", ""), "state": r.get("state", ""),
                         "identifier": "", "person_name": name,
                         "reason": "priority_without_own_source",
                         "detail": "relevance_note carries no relevance_source_url",
                         "rows_affected": 1})
            counters["priority_withheld"] += 1
            continue
        rows.append({
            "position_id": "priority-" + stable_id(r.get("stakeholder_id") or name, src),
            "level": (r.get("level") or "").strip(),
            "state": (r.get("state") or "").strip().upper(),
            "fips": str(r.get("fips") or "").strip().zfill(5) if r.get("fips") else "",
            "county_name": (r.get("county_name") or "").strip(),
            "jurisdiction": (r.get("jurisdiction") or "").strip(),
            "person_name": name, "person_id": (r.get("stakeholder_id") or "").strip(),
            "office": (r.get("office") or "").strip(),
            "office_class": (r.get("office_class") or "").strip(),
            "party": (r.get("party") or "").strip(),
            "stance": "stated_priority",
            "stance_basis": "stated_priority",
            "bill_key": "", "bill_identifier": "",
            "vote_option": "", "vote_result": "", "vote_motion": "", "vote_chamber": "",
            "evidence_date": (r.get("source_retrieved") or "").strip(),
            "evidence_summary": note,
            "source_url": src,
            "confidence": "quoted_source",
            "qc_status": "published",
            "qc_flags": "",
        })
        counters["priority_rows"] += 1
    return rows


def build_body_actions(name_to_fips, held, counters):
    """Decisions by governing bodies, from the opposition tracker.

    Attributed to the body, never to a member. Only terminal statuses: a
    scheduled agenda item is not a position, and treating one as a decision is
    the most likely way this class would mislead.
    """
    rows = []
    if not os.path.exists(OPPOSITION):
        return rows
    for r in read_csv(OPPOSITION):
        auth = (r.get("Authority Level") or "").strip().lower()
        if auth not in AUTHORITY_BODIES:
            continue
        status = (r.get("Status") or "").strip().lower()
        if status not in TERMINAL_STATUS:
            counters["body_action_not_terminal"] += 1
            continue
        url = (r.get("Source URL") or "").strip()
        if not is_url(url):
            counters["body_action_no_source"] += 1
            continue

        st = (r.get("State") or "").strip()
        county = (r.get("County") or "").strip()
        fips = resolve_fips(county, st, name_to_fips)
        mech = (r.get("Opposition Type") or "").strip()
        date = (r.get("Date") or "").strip()[:10]
        loc = (r.get("City") or r.get("location_name") or county or "").strip()

        # The body's name is already its own column on every surface that
        # renders this row, so neither the jurisdiction nor the summary
        # repeats it. "Loudoun County County commission | County commission:
        # Loudoun County has scheduled..." was the result of doing so.
        jurisdiction = loc or county
        # evidence_summary is TRANSPORTED, never composed. Two reasons, and the
        # second is the one that matters.
        #
        # The tracker's Community Outcome field (win / loss / mixed) was
        # appended here in an earlier version. That is scorekeeping about
        # whether opponents got what they wanted, it has no place in a record
        # of what a governing body decided, and leak_audit.py blocked 531 rows
        # over it, correctly. The status already carries the fact.
        #
        # The status was then glued on as "(defeated)" instead, which left the
        # column composed -- and a composed column blocks at the audit even
        # when every word in it came from a source, because the audit cannot
        # tell which half we wrote. Status has its own column (vote_result) on
        # every surface that renders this row, so gluing it on bought nothing.
        # The column is now the source's own prose and only that, which is what
        # `evidence_summary` is registered as in leak_audit.INHERITED_FIELDS.
        summary = (r.get("Summary") or r.get("Incident") or "").strip()[:260] \
            or mech or "action"

        rows.append({
            "position_id": "body-" + stable_id(r.get("Incident") or "", url, date, auth),
            "level": "body",
            "state": _state_abbr(st),
            "fips": fips,
            "county_name": county,
            "jurisdiction": jurisdiction,
            "person_name": "", "person_id": "",
            "office": AUTHORITY_BODIES[auth],
            "office_class": auth,
            "party": "",
            "stance": "governing_body_action",
            "stance_basis": "governing_body_action",
            "bill_key": "", "bill_identifier": "",
            "vote_option": "", "vote_result": status, "vote_motion": mech,
            "vote_chamber": "",
            "evidence_date": date,
            "evidence_summary": summary,
            "source_url": url,
            "confidence": "tracker_coded",
            "qc_status": "published",
            "qc_flags": "" if fips else "county_unresolved",
        })
        counters["body_rows"] += 1
    return rows


# ---------------------------------------------------------------------------
# county resolution
# ---------------------------------------------------------------------------

STATE_NAME = {}


def build_fips_index():
    """(county lower, state lower) -> fips, using county_aggregate as the frame,
    the same join county-profile.html performs in the browser."""
    idx = {}
    for r in read_csv(AGGREGATE):
        fips = str(r.get("fips") or "").strip().zfill(5)
        parts = str(r.get("county_name") or "").split(",")
        cname = parts[0].strip().lower()
        sname = (parts[1] if len(parts) > 1 else "").strip().lower()
        abbr = (r.get("state") or "").strip().upper()
        if abbr and sname:
            STATE_NAME[abbr] = sname
        for c in {cname, COUNTY_SUFFIX.sub("", cname)}:
            if c and sname:
                idx[(c, sname)] = fips
            if c and abbr:
                idx[(c, abbr.lower())] = fips
    return idx


def _state_abbr(s):
    s = str(s or "").strip()
    if len(s) == 2:
        return s.upper()
    low = s.lower()
    for abbr, name in STATE_NAME.items():
        if name == low:
            return abbr
    return s


def resolve_fips(county, state, idx):
    c = re.sub(r"\s*\([^)]*\)\s*$", "", str(county or "")).strip().lower()
    if not c:
        return ""
    st = str(state or "").strip().lower()
    if len(st) == 2:
        st_name = STATE_NAME.get(st.upper(), "")
    else:
        st_name = st
    # Several tracker cells pack co-located counties; take the first resolvable.
    for part in re.split(r"[;/]", c):
        part = part.strip()
        if not part:
            continue
        for cand in (part, COUNTY_SUFFIX.sub("", part)):
            for s in (st_name, st):
                if s and (cand, s) in idx:
                    return idx[(cand, s)]
    return ""


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

SUMMARY_COLS = [
    "person_key", "person_name", "person_id", "level", "state", "fips",
    "county_name", "office", "chamber", "office_class", "party",
    "n_positions", "n_directional", "n_bills",
    "supported_restriction", "opposed_restriction",
    "supported_industry_incentive", "opposed_industry_incentive",
    "supported_disclosure", "opposed_disclosure",
    "sponsored_restriction", "sponsored_industry_incentive",
    "sponsored_disclosure",
    "recorded_vote_no_direction", "did_not_vote", "stated_priority",
    "record", "record_basis", "first_evidence", "last_evidence",
    "example_source_url",
]


def summarise(positions):
    """One row per person. `record` is a label over the directional evidence
    only, and it is never a score.

    The label is deliberately coarse. `consistently_restrictive` requires every
    directional act to point the same way; anything else is `mixed`. A person
    with one directional act is `single_act`, because one vote is a fact and
    not a pattern, and calling it a pattern is how a stakeholder map becomes
    an accusation.
    """
    by_person = defaultdict(list)
    for p in positions:
        if p["level"] == "body" or not p["person_name"]:
            continue
        key = (p["person_id"] or slugify(p["person_name"])) + "|" + p["state"]
        by_person[key].append(p)

    out = []
    for key, rows in by_person.items():
        counts = Counter(r["stance"] for r in rows)
        directional = [r for r in rows if r["stance"] in DIRECTIONAL_STANCES]
        restrictive_side = sum(
            1 for r in directional
            if r["stance"] in ("supported_restriction", "opposed_industry_incentive",
                               "sponsored_restriction"))
        industry_side = sum(
            1 for r in directional
            if r["stance"] in ("opposed_restriction", "supported_industry_incentive",
                               "sponsored_industry_incentive"))

        # Labelling rule. An all-or-nothing consistency test looks rigorous and
        # is useless in practice: a legislator who voted with a restriction 15
        # times and against it once came out as `mixed`, in the same bucket as
        # someone who split 8-7. Sides that do not lean are still `mixed`; a
        # lean of at least PREDOMINANT_SHARE is named as a lean and not as
        # consistency. The counts stay in their own columns either way, so a
        # reader never has to trust the label over the record.
        PREDOMINANT_SHARE = 0.8
        sided = restrictive_side + industry_side
        if not directional:
            record, basis = "no_directional_record", "only non-directional evidence on file"
        elif len(directional) == 1:
            record, basis = "single_act", "one directional act on file"
        elif sided == 0:
            # Every directional act was a disclosure vote, which takes no side.
            record = "disclosure_only"
            basis = "%d directional acts, all on disclosure" % len(directional)
        elif industry_side == 0:
            record = "consistently_restrictive"
            basis = "%d of %d on the restriction side" % (restrictive_side, sided)
        elif restrictive_side == 0:
            record = "consistently_industry_side"
            basis = "%d of %d on the industry side" % (industry_side, sided)
        elif restrictive_side / sided >= PREDOMINANT_SHARE:
            record = "mostly_restrictive"
            basis = "%d of %d on the restriction side" % (restrictive_side, sided)
        elif industry_side / sided >= PREDOMINANT_SHARE:
            record = "mostly_industry_side"
            basis = "%d of %d on the industry side" % (industry_side, sided)
        else:
            record = "mixed"
            basis = "%d restriction side, %d industry side" % (
                restrictive_side, industry_side)

        dates = sorted(d for d in (r["evidence_date"] for r in rows) if d)
        first = max(rows, key=lambda r: len(r.get("office") or ""))
        row = {
            "person_key": key,
            "person_name": first["person_name"],
            "person_id": first["person_id"],
            "level": first["level"],
            "state": first["state"],
            "fips": first["fips"],
            "county_name": first["county_name"],
            "office": first["office"],
            "chamber": next((r.get("vote_chamber", "") for r in rows
                             if r.get("vote_chamber")), ""),
            "office_class": first["office_class"],
            "party": next((r["party"] for r in rows if r["party"]), ""),
            "n_positions": len(rows),
            "n_directional": len(directional),
            "n_bills": len({r["bill_identifier"] for r in rows if r["bill_identifier"]}),
            "record": record,
            "record_basis": basis,
            "first_evidence": dates[0] if dates else "",
            "last_evidence": dates[-1] if dates else "",
            "example_source_url": next((r["source_url"] for r in rows if r["source_url"]), ""),
        }
        for stance in ["supported_restriction", "opposed_restriction",
                       "supported_industry_incentive", "opposed_industry_incentive",
                       "supported_disclosure", "opposed_disclosure",
                       "sponsored_restriction", "sponsored_industry_incentive",
                       "sponsored_disclosure", "recorded_vote_no_direction",
                       "did_not_vote", "stated_priority"]:
            row[stance] = counts.get(stance, 0)
        out.append(row)

    out.sort(key=lambda r: (r["state"], r["person_name"]))
    return out


# ---------------------------------------------------------------------------
# QC report
# ---------------------------------------------------------------------------

def write_qc(positions, summary, held, bills, counters):
    lines = ["# Stakeholder positions QC report", ""]
    lines.append("Generated " + datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".")
    lines.append("")
    lines.append("Every published row is an act on the record with the URL it came "
                 "from. No position is inferred from a party, a title, a district or "
                 "a model. An official with no recorded act carries no position here.")
    lines.append("")

    by_basis = Counter(p["stance_basis"] for p in positions)
    lines.append("## Published")
    lines.append("")
    lines.append("- Position rows: **%s**" % f"{len(positions):,}")
    lines.append("- People with a record: **%s**" % f"{len(summary):,}")
    lines.append("- Roll-call votes read: **%s**" % f"{counters['votes_read']:,}")
    lines.append("- Roll-call votes withheld by the subject gate: **%s**"
                 % f"{counters['votes_withheld']:,}")
    lines.append("")
    lines.append("| evidence class | rows |")
    lines.append("| --- | --- |")
    for basis, n in by_basis.most_common():
        lines.append("| %s | %s |" % (basis, f"{n:,}"))
    lines.append("")

    lines.append("## Stances")
    lines.append("")
    lines.append("| stance | rows |")
    lines.append("| --- | --- |")
    for stance, n in Counter(p["stance"] for p in positions).most_common():
        lines.append("| `%s` | %s |" % (stance, f"{n:,}"))
    lines.append("")

    lines.append("## Records")
    lines.append("")
    lines.append("| record | people |")
    lines.append("| --- | --- |")
    for rec, n in Counter(r["record"] for r in summary).most_common():
        lines.append("| `%s` | %s |" % (rec, f"{n:,}"))
    lines.append("")

    lines.append("## Bills that passed the subject gate")
    lines.append("")
    lines.append("| state | bill | subject | scope | direction | rule | stage | title |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    passing = [b for b in bills.values() if b.get("subject_ok")]
    passing.sort(key=lambda b: (b["state"], b["identifier"]))
    for b in passing:
        lines.append("| %s | %s | `%s` | `%s` | `%s` | `%s` | %s | %s |" % (
            b["state"], b["identifier"], b.get("subject_basis") or "—",
            b.get("subject_scope") or "—",
            b["direction"], b["direction_rule"] or "—",
            b["stage"] or "—", b["title"][:80].replace("|", "/")))
    lines.append("")
    lines.append("A bill at `partial` scope carries data center provisions inside a "
                 "vehicle that is mostly about something else. Its roll calls are "
                 "published and are never given a direction: a vote on the vehicle is "
                 "not a position on the provision, and the roll call cannot "
                 "distinguish them. The module enforces this — an override may assert "
                 "a direction only on a bill it also declares `primary`.")
    lines.append("")

    # The actionable half of the report. These are the promotions that would
    # add the most evidence, ranked by what they would unlock.
    lines.append("## Withheld, ranked by votes it would unlock")
    lines.append("")
    lines.append("A bill listed here has roll calls in the repository that are not "
                 "published as positions because its title does not establish that it "
                 "is a data center bill. To promote one, add a row to "
                 "`data/bill_subject_overrides.csv` with a source URL that shows the "
                 "bill's subject. This module reads that file and never writes to it.")
    lines.append("")
    lines.append("| state | bill | votes withheld | reason | detail |")
    lines.append("| --- | --- | --- | --- | --- |")
    ranked = sorted([h for h in held if h["rows_affected"] > 1],
                    key=lambda h: -h["rows_affected"])[:40]
    for h in ranked:
        lines.append("| %s | %s | %s | `%s` | %s |" % (
            h["state"], h["identifier"] or "—", f"{h['rows_affected']:,}",
            h["reason"], str(h["detail"])[:110].replace("|", "/")))
    lines.append("")

    lines.append("## Standing limits")
    lines.append("")
    lines.append("- A stance is never netted into a single pro-or-anti score. "
                 "Supporting a reporting requirement is not opposing data centers, "
                 "and opposing a tax exemption is not either.")
    lines.append("- A governing body action is attributed to the body, never to an "
                 "individual member. This layer does not know how a board split.")
    lines.append("- A `single_act` record is one vote, not a pattern.")
    lines.append("- A bill admitted by human override at `partial` scope publishes "
                 "its votes with no direction, because the vote was cast on the "
                 "whole vehicle rather than on its data center provisions.")
    lines.append("- Roll calls exist only for states where OpenStates publishes them "
                 "and only for bills this repository has already matched, so absence "
                 "of a record is not evidence of an absent position.")
    lines.append("- A `recorded_vote_no_direction` row states how someone voted and "
                 "makes no claim about what the vote meant.")
    lines.append("")

    with open(OUT_QC, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def build_bill_rows(bills, positions):
    """The lookup table the position file joins to. Only bills that actually
    carry a published position are written: a bill nobody voted on contributes
    nothing to the page and would be a row a reader has to skip past."""
    used = Counter(p["bill_key"] for p in positions if p["bill_key"])
    rows = []
    for (st, ident), b in bills.items():
        key = bill_key(st, ident)
        if key not in used:
            continue
        rows.append({
            "bill_key": key, "state": st, "identifier": ident,
            "title": b["title"], "session": b.get("session", ""),
            "stage": b.get("stage", ""), "stage_date": b.get("stage_date", ""),
            "direction": b["direction"], "direction_rule": b["direction_rule"],
            "subject_basis": b["subject_basis"],
            "subject_scope": b.get("subject_scope", ""),
            "lookup_status": b.get("lookup_status", ""),
            "openstates_url": b.get("openstates_url", ""),
            "n_positions": used[key],
        })
    rows.sort(key=lambda r: (r["state"], r["identifier"]))
    return rows


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build():
    # data/bill_subject_overrides.csv is a Layer D source of record that a human
    # maintains. This module reads it and never creates, writes or repairs it --
    # the layer audit flagged an earlier version that created the empty schema on
    # first run as a cross-layer write, and it was right: the contract in this
    # module's docstring says read-only, so the code has to say read-only too. A
    # missing file reads as no overrides.
    counters = Counter()
    held = []

    bills = build_bill_frame()
    registry = read_csv(REGISTRY)
    fips_idx = build_fips_index()

    positions = []
    positions += build_vote_positions(bills, held, counters)
    positions += build_sponsor_positions(bills, registry, held, counters)
    positions += build_stated_priorities(registry, held, counters)
    positions += build_body_actions(fips_idx, held, counters)

    positions.sort(key=lambda p: (p["state"], p["evidence_date"], p["person_name"],
                                  p["bill_identifier"]))
    summary = summarise(positions)

    bill_rows = build_bill_rows(bills, positions)

    write_csv(OUT_POSITIONS, POSITION_COLS, positions)
    write_csv(OUT_BILLS, BILL_COLS, bill_rows)
    write_csv(OUT_SUMMARY, SUMMARY_COLS, summary)
    write_csv(OUT_HELD, HELD_COLS, held)
    write_qc(positions, summary, held, bills, counters)

    passing = [b for b in bills.values() if b.get("subject_ok")]
    manifest = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "position_rows": len(positions),
        "people": len(summary),
        "bills_in_frame": len(bills),
        "bills_passing_subject_gate": len(passing),
        "bills_with_positions": len(bill_rows),
        "bills_with_direction": sum(1 for b in passing
                                    if b["direction"] != "unclassified"),
        "bills_partial_scope": sum(1 for b in passing
                                   if b.get("subject_scope") == "partial"),
        "votes_read": counters["votes_read"],
        "votes_withheld": counters["votes_withheld"],
        "states_covered": sorted({p["state"] for p in positions if p["state"]}),
        "counties_covered": len({p["fips"] for p in positions if p["fips"]}),
        "by_basis": dict(Counter(p["stance_basis"] for p in positions)),
        "by_stance": dict(Counter(p["stance"] for p in positions)),
        "by_record": dict(Counter(r["record"] for r in summary)),
        "override_rows": len(read_csv(OVERRIDES)),
        "inputs": {
            "bill_sync_votes.csv": _digest(VOTES),
            "bill_sync_matches.csv": _digest(MATCHES),
            "stakeholder_registry.csv": _digest(REGISTRY),
            "master_opposition_clean.csv": _digest(OPPOSITION),
        },
    }
    with open(OUT_MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    print("position rows       %6d" % len(positions))
    print("people              %6d" % len(summary))
    print("bills passing gate  %6d of %d" % (len(passing), len(bills)))
    print("votes read          %6d" % counters["votes_read"])
    print("votes withheld      %6d" % counters["votes_withheld"])
    print("counties covered    %6d" % manifest["counties_covered"])
    for path in (OUT_POSITIONS, OUT_BILLS, OUT_SUMMARY, OUT_HELD, OUT_QC, OUT_MANIFEST):
        print("wrote %s" % os.path.relpath(path, HERE))
    return manifest


def _digest(path):
    if not os.path.exists(path):
        return ""
    h = hashlib.blake2b(digest_size=8)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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

    # --- subject gate: the false matches this gate exists to stop ---
    check("child restraint bill is not a data center bill",
          subject_hit("Weight for Vehicles with Child Restraint System") is None)
    check("immigration bill is not a data center bill",
          subject_hit("Immigration law enforcement noncooperation ordinances") is None)
    check("state budget is not a data center bill",
          subject_hit("Make state operating appropriations for FY 2026-27") is None)
    check("weatherization pilot is not a data center bill",
          subject_hit("Electric utilities; pilot programs for energy assistance and "
                      "weatherization for certain individuals.") is None)
    check("a bare energy title is not enough",
          subject_hit("An Act Regarding Energy, Utilities And Technology") is None)
    check("explicit data center title passes",
          subject_hit("Data centers: reporting.") == "data center")
    check("large load title passes",
          subject_hit("special rules for large load customers") == "large load")
    check("high impact data center title passes",
          subject_hit("certification as a high impact data center") is not None)

    # --- direction: ordering is the point ---
    d, r = classify_direction(
        "State Sales and Use Taxes; the data center equipment sales and use tax "
        "exemption; repeal")
    check("repeal of an exemption is restrictive, not enabling",
          (d, r) == ("restrictive", "repeal_incentive"), "got %s/%s" % (d, r))
    d, r = classify_direction(
        "Providing a sales tax exemption for the construction or remodeling of a "
        "qualified data center in Kansas")
    check("granting an exemption is enabling",
          (d, r) == ("enabling", "grant_incentive"), "got %s/%s" % (d, r))
    d, r = classify_direction(
        "Removing a tax exemption for the replacement of equipment for data centers.")
    check("removing an exemption is restrictive", d == "restrictive", "got %s" % d)
    d, _r = classify_direction(
        "Local government; construction or development of new data centers for a "
        "specified time; prohibit")
    check("a prohibition is restrictive", d == "restrictive")
    d, _r = classify_direction("Data centers: reporting.")
    check("a reporting bill is disclosure, not restrictive", d == "disclosure")
    d, _r = classify_direction("Create the Data Center Study Commission")
    check("a study commission is disclosure", d == "disclosure")
    d, _r = classify_direction(
        "Requires electric public utilities to develop and apply special rules for "
        "large load customers to protect non-large load customers from increased costs")
    check("ratepayer protection is restrictive", d == "restrictive")
    d, _r = classify_direction(
        "Data centers; permit requirements, emission limits for certain "
        "engine-generator sets.")
    check("permit conditions are restrictive", d == "restrictive")
    d, _r = classify_direction("Relating to: certain requirements related to data centers.")
    check("a vague requirements bill stays unclassified", d == "unclassified",
          "got %s" % d)
    d, _r = classify_direction("AN ACT TO AMEND TITLE 26 RELATING TO LARGE ENERGY USE FACILITIES.")
    check("a bare relating-to title stays unclassified", d == "unclassified", "got %s" % d)
    check("empty title is unclassified", classify_direction("") == ("unclassified", ""))

    # --- the override path, and the scope rule that guards it ---
    # build_bill_frame reads OVERRIDES from disk, so these run against a
    # temporary file rather than the committed source of record.
    import tempfile as _tf

    def _frame(matches_rows, override_rows):
        global MATCHES, OVERRIDES
        sm, so = MATCHES, OVERRIDES
        fm = _tf.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                    newline="", encoding="utf-8")
        w = csv.DictWriter(fm, fieldnames=["state", "identifier", "title", "session",
                                           "stage", "stage_date", "openstates_url",
                                           "lookup_status"])
        w.writeheader()
        for r in matches_rows:
            w.writerow(r)
        fm.close()
        fo = _tf.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                    newline="", encoding="utf-8")
        w = csv.DictWriter(fo, fieldnames=["state", "identifier",
                                           "confirmed_data_center_bill",
                                           "subject_scope", "direction",
                                           "source_url", "note"])
        w.writeheader()
        for r in override_rows:
            w.writerow(r)
        fo.close()
        MATCHES, OVERRIDES = fm.name, fo.name
        try:
            return build_bill_frame()
        finally:
            os.unlink(fm.name)
            os.unlink(fo.name)
            MATCHES, OVERRIDES = sm, so

    # An omnibus energy act: real data center provisions, generic title.
    omni = [{"state": "MD", "identifier": "HB 1532",
             "title": "Utility RELIEF (Reducing Energy Load Inflation) Act",
             "session": "2026", "stage": "Signed into law", "stage_date": "",
             "openstates_url": "https://openstates.org/md/bills/2026/HB1532/",
             "lookup_status": "matched"}]

    f = _frame(omni, [])
    b = f[("MD", "HB 1532")]
    check("without an override an omnibus title stays withheld", b["subject_ok"] is False)

    f = _frame(omni, [{"state": "MD", "identifier": "HB 1532",
                       "confirmed_data_center_bill": "1", "subject_scope": "partial",
                       "direction": "restrictive",
                       "source_url": "https://governor.maryland.gov/x", "note": ""}])
    b = f[("MD", "HB 1532")]
    check("a sourced override admits the bill", b["subject_ok"] is True)
    check("the override records the scope it was admitted under",
          b["subject_basis"] == "human_override:partial", "got %s" % b["subject_basis"])
    # The rule this whole field exists for: a vote on a vehicle is not a
    # position on a provision inside it, so a partial bill cannot carry a
    # stance however confidently the override asserts one.
    check("a partial bill refuses an asserted direction",
          b["direction"] == "unclassified", "got %s" % b["direction"])

    f = _frame(omni, [{"state": "MD", "identifier": "HB 1532",
                       "confirmed_data_center_bill": "1", "subject_scope": "primary",
                       "direction": "restrictive",
                       "source_url": "https://governor.maryland.gov/x", "note": ""}])
    b = f[("MD", "HB 1532")]
    check("a primary bill accepts the asserted direction",
          (b["direction"], b["direction_rule"]) == ("restrictive", "human_override"),
          "got %s/%s" % (b["direction"], b["direction_rule"]))

    f = _frame(omni, [{"state": "MD", "identifier": "HB 1532",
                       "confirmed_data_center_bill": "1", "subject_scope": "primary",
                       "direction": "restrictive", "source_url": "not-a-url", "note": ""}])
    b = f[("MD", "HB 1532")]
    check("an override without a source url admits nothing",
          b["subject_ok"] is False and b["direction"] == "unclassified")

    f = _frame(omni, [{"state": "MD", "identifier": "HB 1532",
                       "confirmed_data_center_bill": "0", "subject_scope": "primary",
                       "direction": "restrictive",
                       "source_url": "https://governor.maryland.gov/x", "note": ""}])
    check("an override that does not confirm admits nothing",
          f[("MD", "HB 1532")]["subject_ok"] is False)

    # A self-declaring title does not survive a partial declaration either.
    nc = [{"state": "NC", "identifier": "SB 730", "title": "Ratepayer Protection Act.",
           "session": "2025", "stage": "", "stage_date": "",
           "openstates_url": "https://openstates.org/nc/bills/2025/SB730/",
           "lookup_status": "matched"}]
    b = _frame(nc, [])[("NC", "SB 730")]
    check("the title rule alone would give this bill a direction",
          b["direction"] == "restrictive", "got %s" % b["direction"])
    b = _frame(nc, [{"state": "NC", "identifier": "SB 730",
                     "confirmed_data_center_bill": "1", "subject_scope": "partial",
                     "direction": "", "source_url": "https://www.ncleg.gov/x",
                     "note": ""}])[("NC", "SB 730")]
    check("declaring it a vehicle suppresses the title-derived direction too",
          b["direction"] == "unclassified" and b["direction_rule"] == "",
          "got %s/%s" % (b["direction"], b["direction_rule"]))
    check("the vehicle still publishes as an admitted bill", b["subject_ok"] is True)

    # A title that names the subject needs no override and is primary by default.
    named = [{"state": "CA", "identifier": "AB 1577", "title": "Data centers: reporting.",
              "session": "2026", "stage": "", "stage_date": "",
              "openstates_url": "https://openstates.org/ca/bills/2026/AB1577/",
              "lookup_status": "matched"}]
    b = _frame(named, [])[("CA", "AB 1577")]
    check("a self-describing title is primary without an override",
          b["subject_scope"] == "primary" and b["subject_basis"] == "title_term:data center")
    check("a title-classified direction is unaffected by scope logic",
          b["direction"] == "disclosure")

    # The committed source of record must stay parseable and self-consistent.
    for r in read_csv(OVERRIDES):
        ident = "%s %s" % (r.get("state"), r.get("identifier"))
        check("override %s carries a source url" % ident, is_url(r.get("source_url")))
        check("override %s declares a scope" % ident,
              (r.get("subject_scope") or "").strip() in ("primary", "partial"))
        if (r.get("direction") or "").strip():
            check("override %s asserts a direction only when primary" % ident,
                  r.get("subject_scope") == "primary")

    # --- stance mapping keeps the axes apart ---
    check("yes on restrictive supports a restriction",
          STANCE_BY_VOTE[("restrictive", "yes")] == "supported_restriction")
    check("yes on disclosure is not read as restrictive",
          STANCE_BY_VOTE[("disclosure", "yes")] == "supported_disclosure")
    check("disclosure stances are not directional",
          "supported_disclosure" in DIRECTIONAL_STANCES)
    check("a non-vote is never a stance",
          "did_not_vote" not in DIRECTIONAL_STANCES)
    check("an unclassified vote is never a stance",
          "recorded_vote_no_direction" not in DIRECTIONAL_STANCES)

    # --- the vote builder end to end ---
    bills = {
        ("KS", "SB 526"): {
            "state": "KS", "identifier": "SB 526",
            "title": "Requiring data centers to be located on land zoned for industrial use",
            "session": "2026", "stage": "Failed floor vote", "stage_date": "",
            "openstates_url": "https://openstates.org/ks/bills/2026/SB526/",
            "lookup_status": "matched", "subject_ok": True,
            "subject_basis": "title_term:data center",
            "direction": "restrictive", "direction_rule": "siting_or_permit_condition",
        },
        ("CO", "SB 26"): {
            "state": "CO", "identifier": "SB 26",
            "title": "Weight for Vehicles with Child Restraint System",
            "session": "2026", "stage": "Signed into law", "stage_date": "",
            "openstates_url": "https://openstates.org/co/bills/2026/SB26/",
            "lookup_status": "matched", "subject_ok": False, "subject_basis": "",
            "direction": "unclassified", "direction_rule": "",
        },
    }
    votes_fixture = [
        {"state": "KS", "identifier": "SB 526", "legislator_name": "A Legislator",
         "legislator_id": "ocd-person/1", "option": "yes", "chamber": "upper",
         "vote_date": "2026-03-01", "result": "fail", "motion_text": "Final Action",
         "openstates_url": "https://openstates.org/ks/bills/2026/SB526/"},
        {"state": "KS", "identifier": "SB 526", "legislator_name": "B Legislator",
         "legislator_id": "ocd-person/2", "option": "not voting", "chamber": "upper",
         "vote_date": "2026-03-01", "result": "fail", "motion_text": "Final Action",
         "openstates_url": "https://openstates.org/ks/bills/2026/SB526/"},
        {"state": "CO", "identifier": "SB 26", "legislator_name": "C Legislator",
         "legislator_id": "ocd-person/3", "option": "yes", "chamber": "upper",
         "vote_date": "2026-02-01", "result": "pass", "motion_text": "Final",
         "openstates_url": "https://openstates.org/co/bills/2026/SB26/"},
        {"state": "KS", "identifier": "SB 526", "legislator_name": "D Legislator",
         "legislator_id": "ocd-person/4", "option": "no", "chamber": "upper",
         "vote_date": "2026-03-01", "result": "fail", "motion_text": "Final Action",
         "openstates_url": "not-a-url"},
    ]

    import tempfile
    global VOTES
    saved = VOTES
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(votes_fixture[0].keys()))
        w.writeheader()
        for v in votes_fixture:
            w.writerow(v)
        VOTES = fh.name
    try:
        held, counters = [], Counter()
        rows = build_vote_positions(bills, held, counters)
    finally:
        os.unlink(VOTES)
        VOTES = saved

    stances = {r["person_name"]: r["stance"] for r in rows}
    check("a yes on a siting bill is published as support",
          stances.get("A Legislator") == "supported_restriction",
          "got %s" % stances.get("A Legislator"))
    check("a not-voting row is published without a stance",
          stances.get("B Legislator") == "did_not_vote")
    check("the off-topic bill contributes no positions",
          "C Legislator" not in stances)
    check("a vote with no usable source is withheld",
          "D Legislator" not in stances)
    check("the withheld off-topic bill is reported",
          any(h["identifier"] == "SB 26" and h["reason"] == "subject_not_established"
              for h in held))
    check("withheld counts carry the vote volume",
          counters["votes_withheld"] >= 1)
    check("every published row carries a source url",
          all(is_url(r["source_url"]) for r in rows))
    check("every published row carries a date",
          all(r["evidence_date"] for r in rows))
    check("published rows carry the bill key they join on",
          rows[0]["bill_key"] == "KS:SB 526", "got %s" % rows[0]["bill_key"])

    # The normalised layout only works if the join is total. A position row
    # whose bill_key is absent from the bill file renders as a vote on nothing.
    bill_rows = build_bill_rows(bills, rows)
    bill_keys = {b["bill_key"] for b in bill_rows}
    check("every bill key in the positions resolves in the bill file",
          all(r["bill_key"] in bill_keys for r in rows if r["bill_key"]))
    check("the bill file carries the classifying rule",
          bill_rows[0]["direction_rule"] == "siting_or_permit_condition",
          "got %s" % bill_rows[0]["direction_rule"])
    check("a bill nobody voted on is not written",
          "CO:SB 26" not in bill_keys)
    check("the bill file counts its positions",
          bill_rows[0]["n_positions"] == sum(1 for r in rows if r["bill_key"] == "KS:SB 526"))

    # ids must be stable across rebuilds
    ids_a = sorted(r["position_id"] for r in rows)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(votes_fixture[0].keys()))
        w.writeheader()
        for v in votes_fixture:
            w.writerow(v)
        VOTES = fh.name
    try:
        rows_b = build_vote_positions(bills, [], Counter())
    finally:
        os.unlink(VOTES)
        VOTES = saved
    check("position ids are stable across rebuilds",
          ids_a == sorted(r["position_id"] for r in rows_b))

    # --- summary labelling ---
    def pos(name, stance, state="KS", bill="SB 1"):
        return {"person_name": name, "person_id": "", "level": "state", "state": state,
                "fips": "", "county_name": "", "office": "State legislator",
                "office_class": "legislator", "party": "", "stance": stance,
                "bill_identifier": bill, "evidence_date": "2026-01-01",
                "source_url": "https://example.org/x"}

    s = summarise([pos("One", "supported_restriction")])
    check("one directional act is single_act, never a pattern",
          s[0]["record"] == "single_act", "got %s" % s[0]["record"])
    s = summarise([pos("Two", "supported_restriction", bill="SB 1"),
                   pos("Two", "opposed_industry_incentive", bill="SB 2")])
    check("two acts on the same side read as consistent",
          s[0]["record"] == "consistently_restrictive", "got %s" % s[0]["record"])
    s = summarise([pos("Three", "supported_restriction", bill="SB 1"),
                   pos("Three", "supported_industry_incentive", bill="SB 2")])
    check("an even split reads as mixed", s[0]["record"] == "mixed")

    # The case that forced the label rule to change: an all-or-nothing test put
    # a 15-1 record in the same bucket as an 8-7 one, and every Virginia
    # legislator on the page came out "Mixed".
    lop = [pos("Six", "supported_restriction", bill="SB %d" % i) for i in range(15)]
    lop.append(pos("Six", "opposed_restriction", bill="SB 99"))
    s = summarise(lop)
    check("a 15-1 record is named as a lean, not a split",
          s[0]["record"] == "mostly_restrictive", "got %s" % s[0]["record"])
    check("the lean label carries its own counts",
          s[0]["record_basis"] == "15 of 16 on the restriction side",
          "got %s" % s[0]["record_basis"])
    even = [pos("Seven", "supported_restriction", bill="SB %d" % i) for i in range(8)]
    even += [pos("Seven", "opposed_restriction", bill="SB 1%d" % i) for i in range(7)]
    s = summarise(even)
    check("an 8-7 record is still mixed", s[0]["record"] == "mixed",
          "got %s" % s[0]["record"])
    s = summarise([pos("Eight", "supported_disclosure", bill="SB 1"),
                   pos("Eight", "opposed_disclosure", bill="SB 2")])
    check("a disclosure-only record takes no side",
          s[0]["record"] == "disclosure_only", "got %s" % s[0]["record"])
    s = summarise([pos("Four", "did_not_vote"), pos("Four", "recorded_vote_no_direction")])
    check("non-directional evidence yields no record",
          s[0]["record"] == "no_directional_record")
    s = summarise([pos("Five", "supported_disclosure", bill="SB 1"),
                   pos("Five", "supported_disclosure", bill="SB 2")])
    check("disclosure-only record is not called restrictive",
          s[0]["record"] not in ("consistently_restrictive", "consistently_industry_side"),
          "got %s" % s[0]["record"])
    body = {"person_name": "", "person_id": "", "level": "body", "state": "KS",
            "fips": "20091", "county_name": "Johnson", "office": "County commission",
            "office_class": "county_commission", "party": "",
            "stance": "governing_body_action", "bill_identifier": "",
            "evidence_date": "2026-01-01", "source_url": "https://example.org/y"}
    check("a body action never becomes a person row", len(summarise([body])) == 0)

    # --- county resolution ---
    STATE_NAME.clear()
    STATE_NAME.update({"VA": "virginia", "KS": "kansas"})
    idx = {("loudoun", "virginia"): "51107", ("loudoun", "va"): "51107"}
    check("county resolves by state name",
          resolve_fips("Loudoun County", "Virginia", idx) == "51107")
    check("county resolves by state abbreviation",
          resolve_fips("Loudoun", "VA", idx) == "51107")
    check("packed county cell resolves the first",
          resolve_fips("Loudoun County; Fauquier County", "VA", idx) == "51107")
    check("parenthetical annotation is stripped",
          resolve_fips("Loudoun County (partial)", "VA", idx) == "51107")
    check("unknown county resolves to blank, never a guess",
          resolve_fips("Nowhere", "VA", idx) == "")

    check("url validation rejects a bare word", not is_url("official page"))
    check("url validation accepts https", is_url("https://example.gov/a"))

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
