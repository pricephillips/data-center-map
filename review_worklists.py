"""
review_worklists.py
========================================================================
Generates the two manual-review worklists from the clean feed:

  out/review_conflicts.csv      the outcome-conflict queue, grouped by reason,
                                each row carrying a suggested_resolution so the
                                review pass is triage, not investigation
  out/review_stale_pending.csv  'pending' rows older than STALE_DAYS, oldest
                                first, with Source URL for a quick re-check

Run standalone (python review_worklists.py [clean_feed.csv] [outdir]) or via
build_clean_feed, which calls write_worklists() automatically.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta

STALE_DAYS = 365

_SUGGESTIONS = [
    ("a conditional restriction; the project was not stopped",
     "Confirm the restriction is conditional (not a full stop). If so, restricted_conditional is correct; if the project was actually halted, set Status to a terminal code so it regrades."),
    ("finality rests on the recorded outcome alone",
     "Re-check the source: if the action reached a formal disposition, set Status to the terminal value (passed/denied/withdrawn/etc.); the grade will upgrade automatically on the next pipeline run."),
    ("on legislation at terminal status, but stage",
     "Look up the bill's final stage. If signed into law, set bill_progress=signed_into_law; if it merely passed one chamber or committee, the unverified grade is correct."),
    ("the restrictive bill failed",
     "Verify what event the recorded outcome refers to. If it was the defeat of a different (industry-favorable) measure, fix the Objective; if the restrictive bill genuinely died, the project likely advanced."),
    ("recorded outcome 'win' without finality evidence",
     "Action still in progress. Either update Status when it resolves or leave as-is; blocked_unverified is the correct current grade."),
    ("recorded outcome 'loss' without finality evidence",
     "Same as above, for records coded as the project advancing."),
    ("reached a disposition, but no stoppage is confirmable",
     "Disposition exists but mechanism does not show a block. Check whether the mechanism classification missed a denial/moratorium; if so, add the instrument wording to the Summary."),
]


def _suggest(reason: str) -> str:
    for key, tip in _SUGGESTIONS:
        if key in reason:
            return tip
    return "Manual review."


def write_worklists(records: list[dict], outdir: str = "out") -> dict:
    os.makedirs(outdir, exist_ok=True)
    cols = ["Incident", "State", "County", "Date", "qc_mechanism", "status_clean",
            "bill_progress", "outcome_overstated", "Community Outcome",
            "outcome_defensible", "finality_evidence",
            "outcome_conflict_reason", "suggested_resolution", "Source URL"]

    conflicts = [r for r in records
                 if str(r.get("outcome_conflict", "")).strip().lower() == "true"
                 or r.get("outcome_conflict") is True]
    conflicts.sort(key=lambda r: (str(r.get("outcome_conflict_reason", "")),
                                  str(r.get("State", ""))))
    cpath = os.path.join(outdir, "review_conflicts.csv")
    with open(cpath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in conflicts:
            row = dict(r)
            row["suggested_resolution"] = _suggest(str(r.get("outcome_conflict_reason", "")))
            w.writerow(row)

    cutoff = datetime.now() - timedelta(days=STALE_DAYS)
    stale = []
    for r in records:
        if str(r.get("outcome_defensible", "")) != "pending":
            continue
        raw = str(r.get("Date", "") or "").strip() or \
            str(r.get("recovered_date", "") or "").strip()
        try:
            dt = datetime.fromisoformat(raw[:10])
        except ValueError:
            continue
        if dt < cutoff:
            stale.append((dt, r))
    stale.sort(key=lambda t: t[0])
    spath = os.path.join(outdir, "review_stale_pending.csv")
    scols = ["Incident", "State", "County", "Date", "qc_mechanism",
             "status_clean", "Status", "Source URL"]
    with open(spath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=scols, extrasaction="ignore")
        w.writeheader()
        for _, r in stale:
            w.writerow(r)

    return {"conflicts": len(conflicts), "stale_pending": len(stale),
            "paths": [cpath, spath]}


def write_validation_sample(records: list[dict], outdir: str = "out",
                            per_stratum: int = 12, seed: int = 20260707) -> dict:
    """Stratified random sample for measuring classifier precision. Emits
    validation_sample.csv with blank human_* columns; a reviewer fills them,
    then score_validation() turns the labels into per-category precision.
    Until this exists with labels, keyword-classified category rates have no
    measured accuracy and should not be quoted externally."""
    import random
    rng = random.Random(seed)
    strata: dict[str, list[dict]] = {}
    for r in records:
        strata.setdefault(str(r.get("qc_mechanism", "") or "none"), []).append(r)
    sample = []
    for mech, pool in sorted(strata.items()):
        picks = rng.sample(pool, min(per_stratum, len(pool)))
        sample.extend(picks)
    path = os.path.join(outdir, "validation_sample.csv")
    cols = ["Incident", "State", "Date", "Summary", "qc_mechanism", "qc_concerns",
            "Community Outcome", "outcome_defensible",
            "human_mechanism", "human_concerns", "human_outcome", "reviewer_notes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sample:
            row = dict(r)
            row.update({"human_mechanism": "", "human_concerns": "",
                        "human_outcome": "", "reviewer_notes": ""})
            w.writerow(row)
    return {"sampled": len(sample), "strata": len(strata), "path": path}


def score_validation(sample_path: str) -> dict:
    """Per-mechanism precision from a filled validation sample. Rows with an
    empty human_mechanism are skipped."""
    rows = list(csv.DictReader(open(sample_path, newline="", encoding="utf-8")))
    labeled = [r for r in rows if str(r.get("human_mechanism", "")).strip()]
    by_mech: dict[str, list[int]] = {}
    for r in labeled:
        pred = str(r.get("qc_mechanism", "")).strip()
        truth = str(r.get("human_mechanism", "")).strip()
        by_mech.setdefault(pred, []).append(int(pred == truth))
    out = {m: {"n": len(v), "precision": round(sum(v) / len(v), 3)}
           for m, v in sorted(by_mech.items()) if v}
    out["_labeled_total"] = len(labeled)
    return out


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "master_opposition_clean.csv"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "out"
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    print(write_worklists(rows, outdir))
    print(write_validation_sample(rows, outdir))
    if len(sys.argv) > 3 and sys.argv[3] == "--score":
        print(score_validation(os.path.join(outdir, "validation_sample.csv")))
