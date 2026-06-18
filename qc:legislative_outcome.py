"""
legislative_outcome.py

Legislative outcome classifier for the master opposition database QC agent.

Purpose
-------
Given a record's free-text Notes and its recorded Outcome, this module infers
the FURTHEST stage in the bill lifecycle that has textual evidence, maps that
stage to the outcome it should have, and reports any conflict with the recorded
Outcome along with a severity. This is the check that prevents the HF2690 class
of error, where a bill that only "passed committee" or "passed one chamber"
gets recorded as "Approved."

Design notes
------------
1. The stage ladder (stage name, plain-English meaning, correct outcome) is
   loaded from stage_ladder.csv so it can be edited without touching code.
   If the CSV is missing the module falls back to DEFAULT_LADDER below.
2. Stage detection uses plain lowercase substring matching, no regex, exactly
   as requested. To add coverage, append phrases to SIGNALS for a stage.
3. Precedence, not row order, decides the winner. Terminal dispositions
   (signed, vetoed, died, failed, withdrawn) outrank in-progress milestones
   (passed both chambers, one chamber, committee, introduced). So when notes
   contain both "passed committee" and "sine die," the sine die death wins.
4. Confidence: a stage matched only through a "soft" phrase is LOW confidence,
   and its flag is emitted at LOW severity so weak evidence does not raise an
   alarm it cannot support. Strong phrases produce full-severity flags.

Public API
----------
load_stage_ladder(csv_path=None) -> list[Stage]
infer_stage(notes, ladder=None) -> StageMatch | None
evaluate(notes, recorded_outcome, ladder=None) -> OutcomeIssue | None
evaluate_record(record, ladder=None) -> OutcomeIssue | None   # for the QC agent
looks_legislative(record) -> bool
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Canonical outcome vocabulary
# ---------------------------------------------------------------------------

# Database Outcome vocabulary, kept consistent with the existing tracker.
# The field already used Approved / Blocked / Pending / Mixed, so no new value
# (no "Rejected", no "Withdrawn") is introduced.
CANONICAL_OUTCOMES = {"Approved", "Blocked", "Pending", "Mixed"}

# How a recorded Outcome value maps onto the canonical set for comparison.
# "Rejected" and "Withdrawn", if anyone typed them, fold into "Blocked" so the
# vocabulary stays clean. Values still outside the set (Mixed, Unknown, blank)
# are treated as "not comparable" rather than forced into a false conflict.
RECORDED_SYNONYMS = {
    "approved": "Approved",
    "passed": "Approved",
    "enacted": "Approved",
    "signed": "Approved",
    "blocked": "Blocked",
    "rejected": "Blocked",
    "denied": "Blocked",
    "failed": "Blocked",
    "dead": "Blocked",
    "defeated": "Blocked",
    "withdrawn": "Blocked",
    "pending": "Pending",
    "in progress": "Pending",
    "under review": "Pending",
    "mixed": "Mixed",
    "split": "Mixed",
}

# ---------------------------------------------------------------------------
# Stage signals (plain lowercase substrings, no regex)
# Strong phrases are unambiguous. Soft phrases are weaker evidence and produce
# LOW-confidence, LOW-severity flags.
# ---------------------------------------------------------------------------

SIGNALS: dict[str, dict[str, list[str]]] = {
    "Signed into law": {
        "strong": [
            "signed into law", "signed by the governor", "governor signed",
            "became law", "enacted into law", "was enacted", "now law",
        ],
        "soft": [],
    },
    "Passed both chambers": {
        "strong": [
            "passed both chambers", "both chambers", "house and senate",
            "sent to the governor", "sent to governor", "on the governor's desk",
            "to the governor's desk", "enrolled and sent", "passed the legislature",
        ],
        "soft": [],
    },
    "Vetoed": {
        "strong": ["vetoed", "governor's veto", "veto by the governor", "line-item veto"],
        "soft": [],
    },
    "Died at adjournment / sine die": {
        "strong": [
            "sine die", "adjourned sine die", "adjourned without", "died at adjournment",
            "session ended without", "did not become law", "failed to pass before adjournment",
            "expired at adjournment", "died without a vote", "carried over without action",
        ],
        "soft": ["did not pass", "was not passed", "never passed"],
    },
    "Failed floor vote": {
        "strong": [
            "failed floor vote", "failed on the floor", "voted down",
            "defeated on the floor", "floor vote failed", "rejected on the floor",
            "failed to pass the floor",
        ],
        "soft": ["defeated"],
    },
    "Died in committee": {
        "strong": [
            "died in committee", "stalled in committee", "killed in committee",
            "left in committee", "never advanced out of committee",
        ],
        "soft": [
            "committee took no action", "no action in committee", "no committee action",
            "did not advance from committee", "stuck in committee",
        ],
    },
    "Withdrawn": {
        "strong": [
            "withdrawn", "was withdrawn", "sponsor withdrew", "sponsor pulled",
            "pulled the bill", "bill was pulled",
        ],
        "soft": [],
    },
    "Passed one chamber": {
        "strong": [
            "passed the house", "passed the senate", "cleared the house", "cleared the senate",
            "approved by the house", "approved by the senate",
            "passed the full house", "passed the full senate",
        ],
        "soft": [],
    },
    "Passed committee only": {
        "strong": [
            "passed committee", "passed the committee", "cleared committee",
            "advanced out of committee", "advanced from committee", "committee approved",
            "approved by committee", "reported out of committee", "passed subcommittee",
            "cleared subcommittee", "cleared the subcommittee",
        ],
        "soft": ["advanced in committee", "moved out of committee"],
    },
    "Introduced": {
        "strong": [
            "introduced", "was filed", "bill filed", "prefiled", "pre-filed",
            "referred to committee", "first reading",
        ],
        "soft": ["filed"],
    },
}

# Precedence, highest priority first. The matched stage highest on this list
# wins. Terminal dispositions sit above in-progress milestones.
PRECEDENCE: list[str] = [
    "Signed into law",
    "Vetoed",
    "Died at adjournment / sine die",
    "Failed floor vote",
    "Withdrawn",
    "Died in committee",
    "Passed both chambers",
    "Passed one chamber",
    "Passed committee only",
    "Introduced",
]

# Fallback ladder if the CSV is unavailable. Matches stage_ladder.csv.
DEFAULT_LADDER = [
    ("Signed into law", "Governor signature confirmed", "Approved", ""),
    ("Passed both chambers", "On governor's desk", "Approved", ""),
    ("Vetoed", "Governor rejected it", "Blocked", ""),
    ("Died at adjournment / sine die", "Session ended without passage", "Blocked", ""),
    ("Failed floor vote", "Chamber voted it down", "Blocked", ""),
    ("Died in committee", "Committee took no action", "Blocked", ""),
    ("Withdrawn", "Sponsor pulled it", "Blocked", "withdrawn"),
    ("Passed one chamber", "Only House or Senate", "Pending", "not Approved"),
    ("Passed committee only", "Advanced out of committee", "Pending", "not Approved"),
    ("Introduced", "Filed / referred", "Pending", ""),
]

# Legislative bill identifiers across states (HF/SF Iowa, HB/SB most states,
# AB California/Wisconsin, LB Nebraska, LD/HP/SP Maine, etc.). Used only to
# decide whether a record is legislative. Requires a known prefix plus digits.
_BILL_RE = re.compile(
    r"\b(?:HF|SF|HB|SB|AB|HSB|SSB|HJR|SJR|HCR|SCR|HJ|SJ|HR|SR|LB|LD|HP|SP)\s?\d{1,5}\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    name: str
    meaning: str
    correct_outcome: str          # canonical: Approved / Rejected / Pending / Withdrawn
    caveat: str                   # e.g. "not Approved"
    priority: int                 # higher wins
    strong: tuple[str, ...]
    soft: tuple[str, ...]


@dataclass(frozen=True)
class StageMatch:
    stage: str
    meaning: str
    correct_outcome: str
    caveat: str
    confidence: str               # HIGH / LOW
    matched_phrase: str


@dataclass(frozen=True)
class OutcomeIssue:
    severity: str                 # CRITICAL / HIGH / MEDIUM / LOW
    code: str
    recorded_outcome: str
    inferred_stage: str
    inferred_outcome: str
    confidence: str
    matched_phrase: str
    message: str


# ---------------------------------------------------------------------------
# Ladder loading
# ---------------------------------------------------------------------------

def _parse_outcome(raw: str) -> tuple[str, str]:
    """Split 'Pending - not Approved ... trap' into ('Pending', 'not Approved')."""
    base = re.split(r"\s*[—–]\s*|\s+-\s+", raw.strip())[0].strip()
    canonical = base.split()[0].strip().title() if base else ""
    caveat = raw[len(base):]
    caveat = re.split(r"←", caveat)[0]                       # drop trap annotation
    caveat = caveat.lstrip(" —–-").strip()
    return canonical, caveat


def load_stage_ladder(csv_path: str | None = None) -> list[Stage]:
    """Load the stage ladder from CSV, attach signals and precedence."""
    rows: list[tuple[str, str, str, str]]
    if csv_path is None:
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stage_ladder.csv")

    if os.path.exists(csv_path):
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                stage = (row.get("Stage") or "").strip()
                meaning = (row.get("What it means") or "").strip()
                outcome_raw = (row.get("Correct outcome") or "").strip()
                if not stage:
                    continue
                canonical, caveat = _parse_outcome(outcome_raw)
                rows.append((stage, meaning, canonical, caveat))
    else:
        rows = list(DEFAULT_LADDER)

    ladder: list[Stage] = []
    n = len(PRECEDENCE)
    for stage, meaning, outcome, caveat in rows:
        sig = SIGNALS.get(stage, {"strong": [], "soft": []})
        priority = (n - PRECEDENCE.index(stage)) if stage in PRECEDENCE else 0
        ladder.append(Stage(
            name=stage,
            meaning=meaning,
            correct_outcome=outcome,
            caveat=caveat,
            priority=priority,
            strong=tuple(sig.get("strong", [])),
            soft=tuple(sig.get("soft", [])),
        ))
    return ladder


_LADDER_CACHE: list[Stage] | None = None


def _ladder(ladder: list[Stage] | None) -> list[Stage]:
    global _LADDER_CACHE
    if ladder is not None:
        return ladder
    if _LADDER_CACHE is None:
        _LADDER_CACHE = load_stage_ladder()
    return _LADDER_CACHE


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def _normalize_recorded(value: str) -> str | None:
    """Map a recorded Outcome to the canonical set, or None if not comparable."""
    if not value:
        return None
    key = value.strip().lower()
    if key in RECORDED_SYNONYMS:
        return RECORDED_SYNONYMS[key]
    titled = value.strip().title()
    return titled if titled in CANONICAL_OUTCOMES else None


def infer_stage(notes: str, ladder: list[Stage] | None = None) -> StageMatch | None:
    """Return the highest-precedence stage with textual evidence in notes."""
    if not notes:
        return None
    text = notes.lower()
    candidates: list[tuple[int, str, str, Stage]] = []  # (priority, conf, phrase, stage)

    for st in _ladder(ladder):
        hit = next((p for p in st.strong if p in text), None)
        if hit:
            candidates.append((st.priority, "HIGH", hit, st))
            continue
        hit = next((p for p in st.soft if p in text), None)
        if hit:
            candidates.append((st.priority, "LOW", hit, st))

    if not candidates:
        return None

    # Highest precedence wins. Tie-break toward the strong (HIGH) match.
    candidates.sort(key=lambda c: (c[0], c[1] == "HIGH"), reverse=True)
    priority, conf, phrase, st = candidates[0]
    return StageMatch(
        stage=st.name,
        meaning=st.meaning,
        correct_outcome=st.correct_outcome,
        caveat=st.caveat,
        confidence=conf,
        matched_phrase=phrase,
    )


def _severity(recorded: str, inferred: str, inferred_stage: str) -> str | None:
    if recorded == inferred:
        return None
    pair = {recorded, inferred}
    if pair == {"Approved", "Blocked"}:
        return "CRITICAL"                       # dead bill recorded as passed, or vice versa
    if recorded == "Approved" and inferred == "Pending":
        # Overstated passage. Committee-only is the worst form of this (HF2690 trap).
        return "CRITICAL" if "committee" in inferred_stage.lower() else "HIGH"
    if recorded == "Pending" and inferred == "Approved":
        return "HIGH"
    if recorded == "Pending" and inferred == "Blocked":
        return "MEDIUM"
    if recorded == "Blocked" and inferred == "Pending":
        return "MEDIUM"
    return "MEDIUM"


def evaluate(notes: str, recorded_outcome: str, ladder: list[Stage] | None = None) -> OutcomeIssue | None:
    """Compare the recorded Outcome against the stage inferred from notes."""
    match = infer_stage(notes, ladder)
    if match is None:
        return None

    recorded = _normalize_recorded(recorded_outcome)
    if recorded is None:
        # Outcome is Mixed/Unknown/blank. Surface as a low-severity note so the
        # agent knows a legislative record was not validated, not a false conflict.
        return OutcomeIssue(
            severity="LOW",
            code="OUTCOME_NOT_COMPARABLE",
            recorded_outcome=recorded_outcome or "(blank)",
            inferred_stage=match.stage,
            inferred_outcome=match.correct_outcome,
            confidence=match.confidence,
            matched_phrase=match.matched_phrase,
            message=(
                f"Notes indicate stage '{match.stage}' (should be "
                f"'{match.correct_outcome}'), but recorded Outcome "
                f"'{recorded_outcome or 'blank'}' cannot be auto-validated."
            ),
        )

    base = _severity(recorded, match.correct_outcome, match.stage)
    if base is None:
        return None                              # recorded and inferred agree

    severity = "LOW" if match.confidence == "LOW" else base
    caveat = f" ({match.caveat})" if match.caveat else ""
    message = (
        f"Outcome conflict: recorded '{recorded_outcome}', but notes show "
        f"'{match.stage}' which should be '{match.correct_outcome}'{caveat}. "
        f"Matched on \"{match.matched_phrase}\" ({match.confidence} confidence)."
    )
    return OutcomeIssue(
        severity=severity,
        code="OUTCOME_CONFLICT",
        recorded_outcome=recorded_outcome,
        inferred_stage=match.stage,
        inferred_outcome=match.correct_outcome,
        confidence=match.confidence,
        matched_phrase=match.matched_phrase,
        message=message,
    )


# ---------------------------------------------------------------------------
# QC-agent integration helpers
# ---------------------------------------------------------------------------

def _as_text(value) -> str:
    """Coerce a plain string or a Notion-style property dict to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("plain_text", "name", "content", "string"):
            if isinstance(value.get(key), str):
                return value[key]
    if isinstance(value, list):
        return " ".join(_as_text(v) for v in value)
    return str(value)


def looks_legislative(record: dict) -> bool:
    """True if the record is a bill: type says legislation, or name/notes carry a bill ID."""
    opp_type = _as_text(record.get("Opposition Type") or record.get("Type")).lower()
    if "legislat" in opp_type or "bill" in opp_type:
        return True
    blob = " ".join(_as_text(record.get(k)) for k in ("Name", "Title", "Notes"))
    return bool(_BILL_RE.search(blob))


def evaluate_record(record: dict, ladder: list[Stage] | None = None) -> OutcomeIssue | None:
    """Entry point the QC agent calls on each record flagged as legislative."""
    if not looks_legislative(record):
        return None
    notes = _as_text(record.get("Notes"))
    outcome = _as_text(record.get("Outcome"))
    return evaluate(notes, outcome, ladder)


# ---------------------------------------------------------------------------
# Self-test: the conflict scenarios the classifier must resolve correctly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ladder = load_stage_ladder()

    scenarios = [
        ("HF2690 sine die trap",
         "HF2690 passed committee on Feb 23, 2026, but Iowa adjourned sine die on May 3, 2026 without passage.",
         "Approved", "CRITICAL", "Died at adjournment / sine die"),
        ("Committee-only trap",
         "The bill cleared committee but has not advanced any further this session.",
         "Approved", "CRITICAL", "Passed committee only"),
        ("One-chamber overstated",
         "Passed the House; awaiting action in the Senate.",
         "Approved", "HIGH", "Passed one chamber"),
        ("Committee death, soft signal",
         "The committee took no action and the measure did not advance.",
         "Pending", "LOW", "Died in committee"),
        ("Clean: signed into law",
         "Signed into law by the governor on June 1, 2026.",
         "Approved", None, "Signed into law"),
        ("Clean: vetoed",
         "The governor vetoed the bill.",
         "Blocked", None, "Vetoed"),
        ("Clean: withdrawn",
         "The sponsor withdrew the bill before a committee hearing.",
         "Blocked", None, "Withdrawn"),
        ("Both chambers, recorded Pending",
         "Passed both chambers and was sent to the governor.",
         "Pending", "HIGH", "Passed both chambers"),
        ("Signed, recorded Blocked (reverse error)",
         "The bill was signed into law last week.",
         "Blocked", "CRITICAL", "Signed into law"),
    ]

    print(f"{'Scenario':<34}{'Stage inferred':<34}{'Outcome':<10}{'Sev':<10}{'OK'}")
    print("-" * 100)
    all_ok = True
    for name, notes, recorded, want_sev, want_stage in scenarios:
        issue = evaluate(notes, recorded, ladder)
        got_sev = issue.severity if issue else None
        got_stage = (infer_stage(notes, ladder).stage if infer_stage(notes, ladder) else None)
        ok = (got_sev == want_sev) and (got_stage == want_stage)
        all_ok = all_ok and ok
        print(f"{name:<34}{str(got_stage):<34}"
              f"{(issue.inferred_outcome if issue else '-'):<10}"
              f"{str(got_sev):<10}{'PASS' if ok else 'FAIL'}")
        if issue:
            print(f"    -> {issue.message}")

    print("-" * 100)
    print("ALL SCENARIOS PASS" if all_ok else "SOME SCENARIOS FAILED")
