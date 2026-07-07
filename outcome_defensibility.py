"""
outcome_defensibility.py
========================================================================
The defensibility layer. Sits after cleaning + enrichment and converts the
source-coded Community Outcome into a graded, externally-quotable ladder,
so a conditional restriction is never presented as an outright win and a
proposed moratorium is never presented as an enacted block.

Adds four columns (all additive; nothing existing is overwritten):

qc_block_status      enacted_block | proposed_block | not_block | stance_ambiguous
                     A block-mechanism record only counts as an ENACTED block
                     when there is finality evidence (action_complete, a
                     terminal status_clean, or a terminal bill_progress).
                     "County board considers moratorium" stays proposed_block.

qc_leg_stance        restrictive | supportive | unclear  (legislation rows only)
                     Conservative keyword inference from Objective/Summary so
                     the 300+ stance-ambiguous legislation rows can enter
                     mechanism/blocking analysis once a stance is clear.
                     Preemption of local control codes as supportive
                     (industry-favorable), disclosure/moratorium/setback
                     mandates as restrictive. Anything mixed -> unclear.

outcome_defensible   The graded outcome ladder for external numbers:
                       win_confirmed   recorded win + enacted block + final
                       win_conditional recorded win but mechanism is a
                                       conditional restriction, not a block
                       win_unverified  recorded win without finality evidence
                       loss_confirmed  recorded loss + final
                       loss_unverified recorded loss without finality evidence
                       mixed           recorded mixed
                       pending         everything else
                     External "wins" should cite win_confirmed;
                     win_conditional and win_unverified are shown separately.

outcome_conflict     True when the recorded outcome claims more than the
                     mechanism/finality evidence supports, with the reason in
outcome_conflict_reason  (for the manual review queue).

Usage: apply_defensibility(records) mutates a list of dicts in place and
returns summary counts. Works on gate output (qc_* already attached) or on
cleaner-only output (computes enrichment directly; enrichment.py required).
"""

from __future__ import annotations

import re

try:
    import enrichment as _E
    _HAVE_ENRICH = True
except Exception:
    _HAVE_ENRICH = False

# status_clean codes that constitute terminal evidence for a block-mechanism
# record (the action reached a disposition, not merely a proposal).
_TERMINAL_STATUS = {"passed", "approved", "enacted", "failed", "vetoed",
                    "expired", "withdrawn", "resolved"}
# terminal codes that specifically CONFIRM the block took effect
_BLOCK_CONFIRMING_STATUS = {"passed", "approved", "enacted", "resolved"}
_TERMINAL_PROGRESS = {"signed_into_law", "vetoed", "failed_floor_vote",
                      "died_sine_die", "died_in_committee", "withdrawn"}

# ── Legislation stance ───────────────────────────────────────────────────────
# Conservative: a stance is assigned only on unambiguous signals; mixed or
# absent signals stay "unclear". Restrictive = constrains data-center
# development; supportive = enables it or strips local authority to constrain.
_RESTRICTIVE = re.compile(
    r"\b(moratorium|moratoria|ban\b|prohibit|setback|disclosure|transparen|"
    r"reporting requirement|(repeal|eliminat\w*|reduce|remove|end)\W+(?:\w+\W+){0,4}"
    r"(incentive|exemption|abatement|tax break|subsid)\w*|ratepayer protection|"
    r"protect ratepayers|cost.?allocation|large.?load tariff|"
    r"water.?use (limit|reporting)|noise limit|impact stud|"
    r"environmental review requirement|require.{0,30}permit|restrict|"
    r"limit data center|community benefit|oppose|denial|deny|reject|stop\b|halt)\b", re.I)
_SUPPORTIVE = re.compile(
    r"\b(preempt|pre-empt|prohibit(s|ing)? (local|counties|cities|municipalit)|"
    r"strip.{0,20}local|override.{0,20}local|streamlin|fast.?track|expedite|"
    r"by.?right|as.?of.?right)\b", re.I)


def leg_stance(rec: dict) -> str:
    """Stance of the tracked action toward the industry. The Objective field
    states the goal directly and is the reliable signal; Summary prose is
    context and misfires, so it is only consulted when Objective is empty.
    In an opposition dataset the Objective describes what opponents seek, so
    restrictive language there means the tracked action restricts development;
    preemption/streamlining language means the tracked bill favors it."""
    primary = " ".join(str(rec.get(k, "") or "") for k in
                       ("Objective", "objective_type", "Incident")).lower().strip()
    t = primary or str(rec.get("Summary", "") or "").lower()
    sup = bool(_SUPPORTIVE.search(t))
    res = bool(_RESTRICTIVE.search(t))
    if sup and not res:
        return "supportive"
    if res:
        return "restrictive"
    return "unclear"


# ── Finality evidence ────────────────────────────────────────────────────────

def _truthy(v) -> bool:
    return str(v).strip().lower() == "true" or v is True


def _is_final(rec: dict) -> bool:
    if _truthy(rec.get("action_complete")):
        return True
    if str(rec.get("bill_progress", "") or "").strip() in _TERMINAL_PROGRESS:
        return True
    return str(rec.get("status_clean", "") or "").strip() in _TERMINAL_STATUS


def _block_confirmed(rec: dict) -> bool:
    """Finality evidence that the BLOCK itself took effect (not merely that
    the process ended). A denial/withdrawal mechanism is confirmed by any
    terminal disposition; a moratorium/ban needs an adopting disposition."""
    mech = str(rec.get("qc_mechanism", "") or "")
    sc = str(rec.get("status_clean", "") or "").strip()
    bp = str(rec.get("bill_progress", "") or "").strip()
    if mech == "project_denial":
        return _is_final(rec)
    if bp == "signed_into_law":
        return True
    if sc in _BLOCK_CONFIRMING_STATUS:
        return True
    # outcome says win AND the row is final — accept, the win refers to the block
    return _truthy(rec.get("action_complete")) and \
        str(rec.get("Community Outcome", "") or "").strip().lower() == "win"


# ── Main API ─────────────────────────────────────────────────────────────────

def _ensure_enriched(rec: dict) -> None:
    """If qc_* enrichment is absent (cleaner-only path), compute it here so
    the defensibility ladder always has a mechanism to reason from."""
    if "qc_mechanism" in rec and str(rec.get("qc_mechanism", "")).strip():
        return
    if _HAVE_ENRICH:
        rec.update(_E.enrich_record(rec))


def classify_record(rec: dict) -> dict:
    _ensure_enriched(rec)
    mech = str(rec.get("qc_mechanism", "") or "")
    is_block = str(rec.get("qc_is_block", "")).strip().lower()
    outcome = str(rec.get("Community Outcome", "") or "").strip().lower()
    final = _is_final(rec)

    # qc_block_status
    if mech == "legislation":
        stance = leg_stance(rec)
        block_status = "stance_ambiguous" if stance == "unclear" else (
            "enacted_block" if (stance == "restrictive" and
                                str(rec.get("bill_progress", "")).strip() == "signed_into_law")
            else ("proposed_block" if stance == "restrictive" else "not_block"))
    else:
        stance = ""
        if is_block == "true":
            block_status = "enacted_block" if _block_confirmed(rec) else "proposed_block"
        else:
            block_status = "not_block"

    # outcome_defensible ladder + conflicts
    conflict, reason = False, ""
    if outcome == "win" and mech == "legislation":
        bp = str(rec.get("bill_progress", "") or "").strip()
        sc = str(rec.get("status_clean", "") or "").strip()
        bill_dead = bp in ("failed_floor_vote", "died_sine_die", "died_in_committee", "vetoed") \
            or sc in ("failed", "vetoed", "withdrawn", "expired")
        bill_law = bp == "signed_into_law" or sc == "enacted"
        if stance == "supportive" and bill_dead:
            grade = "win_confirmed"            # opposition defeated an industry-favorable bill
        elif stance == "restrictive" and bill_law:
            grade = "win_confirmed"            # restrictive measure became law
        elif stance == "restrictive" and bill_dead:
            grade = "win_unverified"
            conflict, reason = True, "recorded win but the restrictive bill failed - verify what the win refers to"
        elif final:
            grade = "win_unverified"
            conflict, reason = True, "legislative win at terminal status but stage/stance not confirmable"
        else:
            grade = "win_unverified"
            conflict, reason = True, "recorded win on in-progress legislation"
    elif outcome == "win":
        if is_block == "false" and mech in ("conditional_zoning", "cost_allocation",
                                            "incentive_repeal", "disclosure",
                                            "community_benefit", "infrastructure_opposition"):
            grade = "win_conditional"
            conflict, reason = True, f"recorded win but mechanism is {mech} (conditional restriction, not a block)"
        elif block_status == "enacted_block" and final:
            grade = "win_confirmed"
        elif final:
            grade = "win_confirmed" if mech in ("project_denial",) else "win_unverified"
            if grade == "win_unverified":
                conflict, reason = True, "recorded win reached a disposition but block not confirmable from mechanism/status"
        else:
            grade = "win_unverified"
            conflict, reason = True, "recorded win without finality evidence (action still in progress)"
    elif outcome == "loss":
        grade = "loss_confirmed" if final else "loss_unverified"
        if grade == "loss_unverified":
            conflict, reason = True, "recorded loss without finality evidence"
    elif outcome == "mixed":
        grade = "mixed"
    else:
        grade = "pending"

    return {
        "qc_block_status": block_status,
        "qc_leg_stance": stance,
        "outcome_defensible": grade,
        "outcome_conflict": conflict,
        "outcome_conflict_reason": reason,
    }


def apply_defensibility(records: list[dict]) -> dict:
    """Mutate records in place; return summary counts for the pipeline report."""
    from collections import Counter
    grades, blocks, conflicts = Counter(), Counter(), 0
    for rec in records:
        out = classify_record(rec)
        rec.update(out)
        grades[out["outcome_defensible"]] += 1
        blocks[out["qc_block_status"]] += 1
        conflicts += int(out["outcome_conflict"])
    return {"grades": dict(grades), "block_status": dict(blocks), "conflicts": conflicts}


if __name__ == "__main__":
    import csv, sys, json
    path = sys.argv[1] if len(sys.argv) > 1 else "master_opposition_clean.csv"
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    summary = apply_defensibility(rows)
    print(json.dumps(summary, indent=2))
