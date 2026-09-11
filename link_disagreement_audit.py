"""
link_disagreement_audit.py — where the rule cascade and the scored model disagree.

`splink_spike.py` returned NO-GO on adoption: on the contested pairs that
motivated it, a field-comparison model has nothing left to discriminate on,
because the structured fields agree by construction. That verdict stands and
this module does not reopen it. `project_resolution.py` and human adjudication
remain the confirmation path.

What the spike also produced, and what nobody has worked, is a disagreement
surface: pairs the rules confirm that the model scores near zero, and pairs the
rules leave unlinked that the model scores at near-certainty. The spike's own
report called those "worth a review pass on their own terms" and no pass has
happened. This module turns that one-off observation into a standing worklist.

What is live and what is frozen
-------------------------------
Deliberately asymmetric, and the asymmetry is the point:

  frozen   the match probabilities, read from data/splink_spike_scores.csv.
           Splink is not a dependency of this repository and is not being made
           one. These are the registered spike run's numbers and they do not
           move.

  live     the rule state, re-read every run from data/project_links.csv,
           data/project_link_review.csv and data/project_links_manual.csv.

So a row leaves this worklist when the rules change their mind or a person
adjudicates the pair, which is what makes it a worklist rather than a snapshot.
It shrinks as it is worked.

The cost of the frozen half is real and is reported rather than hidden: a link
created after the spike ran has no score, cannot be audited here, and is
counted in the report as uncovered. When that count grows large enough to
matter, the answer is to re-run the spike, not to trust a shrinking sample.

Adjudicated pairs are excluded outright. A person who has already ruled on a
pair has settled it, and a model that disagrees with a recorded human decision
is not raising a question -- that is the case the spike's own evaluation
already scored, and it is where the model performed worst.

Writes:
  data/link_disagreement_worklist.csv   one row per open disagreement, ranked
  data/link_disagreement_report.md      counts, coverage, and what to do

Read-only with respect to every input. Creates no links and removes none.

Usage:
  python link_disagreement_audit.py
  python link_disagreement_audit.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

SCORES_CSV = P("data", "splink_spike_scores.csv")
LINKS_CSV = P("data", "project_links.csv")
REVIEW_CSV = P("data", "project_link_review.csv")
MANUAL_CSV = P("data", "project_links_manual.csv")

OUT_WORKLIST = P("data", "link_disagreement_worklist.csv")
OUT_REPORT = P("data", "link_disagreement_report.md")

# The spike's own thresholds, kept identical so the two documents describe the
# same surface. Changing either changes what "disagreement" means and should be
# a deliberate, recorded decision rather than a tuning pass.
LOW_SCORE = 0.5     # a rule-confirmed pair the model puts below this
HIGH_SCORE = 0.99   # an unlinked pair the model puts at or above this

KIND_RULE_LOW = "rule_confirmed_low_score"
KIND_UNLINKED_HIGH = "unlinked_high_score"


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def pair(row, opp_key="opp_id", proj_key="project_id"):
    return ((row.get(opp_key) or "").strip(), (row.get(proj_key) or "").strip())


def load_scores(rows):
    """pair -> float probability. Unparseable scores are dropped, not zeroed:
    a missing score means the pair was not scored, and zero would read as the
    model being confident it is wrong."""
    out = {}
    for r in rows:
        k = pair(r)
        if not k[0] or not k[1]:
            continue
        try:
            out[k] = float(r.get("match_probability") or "")
        except ValueError:
            continue
    return out


def disagreements(scores, linked, review, adjudicated,
                  low=LOW_SCORE, high=HIGH_SCORE):
    """Open disagreements between the frozen scores and the live rule state.

    linked / review / adjudicated are sets of (opp_id, project_id).
    Returns a list of dicts, most disagreeable first: rule-confirmed pairs
    ascending by score, then unlinked pairs descending.
    """
    rule_low, unlinked_high = [], []
    for k, prob in scores.items():
        if k in adjudicated:
            continue
        if k in linked:
            if prob < low:
                rule_low.append((k, prob))
        elif k not in review and prob >= high:
            unlinked_high.append((k, prob))

    rows = []
    for k, prob in sorted(rule_low, key=lambda x: x[1]):
        rows.append({"kind": KIND_RULE_LOW, "opp_id": k[0], "project_id": k[1],
                     "match_probability": prob})
    for k, prob in sorted(unlinked_high, key=lambda x: -x[1]):
        rows.append({"kind": KIND_UNLINKED_HIGH, "opp_id": k[0],
                     "project_id": k[1], "match_probability": prob})
    return rows


def coverage(linked, scores):
    """Current links the frozen scores cannot speak to."""
    unscored = [k for k in linked if k not in scores]
    return len(linked), len(unscored), sorted(unscored)


def build():
    score_rows = read_csv(SCORES_CSV)
    if not score_rows:
        sys.exit(f"link_disagreement_audit: {SCORES_CSV} not found or empty. "
                 "It is the registered output of splink_spike.py and is "
                 "required; this module does not re-score.")
    scores = load_scores(score_rows)
    context = {pair(r): r for r in score_rows}

    linked = {pair(r) for r in read_csv(LINKS_CSV)}
    review = {pair(r) for r in read_csv(REVIEW_CSV)}
    manual = read_csv(MANUAL_CSV)
    adjudicated = {pair(r) for r in manual}

    rows = disagreements(scores, linked, review, adjudicated)
    n_linked, n_unscored, unscored = coverage(linked, scores)

    with open(OUT_WORKLIST, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, lineterminator="\n", fieldnames=[
            "kind", "opp_id", "project_id", "match_probability",
            "project_name", "opp_state", "opp_date", "opp_incident"])
        w.writeheader()
        for r in rows:
            c = context.get((r["opp_id"], r["project_id"]), {})
            w.writerow({
                "kind": r["kind"], "opp_id": r["opp_id"],
                "project_id": r["project_id"],
                "match_probability": f"{r['match_probability']:.6f}",
                "project_name": c.get("project_name", ""),
                "opp_state": c.get("opp_state", ""),
                "opp_date": c.get("opp_date", ""),
                "opp_incident": (c.get("opp_incident", "") or "")[:160],
            })

    n_low = sum(1 for r in rows if r["kind"] == KIND_RULE_LOW)
    n_high = sum(1 for r in rows if r["kind"] == KIND_UNLINKED_HIGH)
    pct = (100.0 * (n_linked - n_unscored) / n_linked) if n_linked else 0.0

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(f"""# Link disagreement audit

Generated {date.today().isoformat()} by `link_disagreement_audit.py`.

Where the live rule cascade and the frozen model scores from `splink_spike.py`
disagree about the same pair. The spike's NO-GO verdict on adoption stands and
is not reopened here: the score is used only as a second opinion that can be
read against the rules, never as a decision.

| | count |
|---|---|
| rule-confirmed, model below {LOW_SCORE} | {n_low} |
| unlinked, model at or above {HIGH_SCORE} | {n_high} |
| **open disagreements** | **{len(rows)}** |

Rows are in `data/link_disagreement_worklist.csv`, most disagreeable first.

## How to read a row

A `{KIND_RULE_LOW}` row is a link the rules made and the model finds
implausible. Check whether the corroborating signals are as strong as the tier
implies, or whether one strong field is carrying the match alone.

An `{KIND_UNLINKED_HIGH}` row is a pair the rules never proposed and the model
finds near-certain. Check whether a real link is being missed because no rule
covers its shape.

Neither is evidence on its own. The model was measured at AUC 0.632 on
adjudicated pairs and 0.605 on contested ones, so it is a prompt to look, not a
verdict. Resolve a row by adjudicating the pair in
`data/project_links_manual.csv`; adjudicated pairs are excluded from the next
run, so this worklist shrinks as it is worked.

## Coverage, and the limit of this audit

The scores are frozen at the registered spike run. Splink is not a dependency
of this repository and this module does not re-score.

| | count |
|---|---|
| current rule links | {n_linked} |
| of those, carrying a model score | {n_linked - n_unscored} ({pct:.1f} pct) |
| of those, with no score | {n_unscored} |

A link created after the spike ran cannot be audited here and is counted above
rather than passed over. As that number grows the audit covers less of the
live frame, and the answer then is to re-run the spike, not to read a shrinking
sample as though it were the whole.
""")
        if unscored:
            fh.write("\nUnscored current links (first 20):\n\n")
            for k in unscored[:20]:
                fh.write(f"- {k[0]} -> {k[1]}\n")

    print(f"link disagreement audit: {len(rows)} open "
          f"({n_low} rule-confirmed low, {n_high} unlinked high); "
          f"coverage {n_linked - n_unscored}/{n_linked} links scored")
    print(f"wrote {OUT_WORKLIST}")
    print(f"wrote {OUT_REPORT}")
    return 0


def selftest():
    checks = []

    def check(label, ok):
        checks.append((label, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    scores = {("o1", "p1"): 0.10, ("o2", "p2"): 0.995,
              ("o3", "p3"): 0.80, ("o4", "p4"): 0.999,
              ("o5", "p5"): 0.20}
    linked = {("o1", "p1"), ("o3", "p3"), ("o5", "p5")}
    review = {("o4", "p4")}
    adjudicated = {("o5", "p5")}

    rows = disagreements(scores, linked, review, adjudicated)
    kinds = {(r["opp_id"], r["project_id"]): r["kind"] for r in rows}

    check("rule-confirmed pair below the floor is flagged",
          kinds.get(("o1", "p1")) == KIND_RULE_LOW)
    check("unlinked pair above the ceiling is flagged",
          kinds.get(("o2", "p2")) == KIND_UNLINKED_HIGH)
    check("rule-confirmed pair with a comfortable score is not flagged",
          ("o3", "p3") not in kinds)
    check("a pair already queued for review is not also flagged as unlinked",
          ("o4", "p4") not in kinds)
    check("an adjudicated pair is excluded even when it disagrees",
          ("o5", "p5") not in kinds)
    check("rule-confirmed rows sort ascending, unlinked rows descending",
          [r["kind"] for r in rows] ==
          [KIND_RULE_LOW, KIND_UNLINKED_HIGH])

    check("a pair with no score is never invented as a disagreement",
          disagreements({}, {("o9", "p9")}, set(), set()) == [])
    check("unparseable scores are dropped, not read as zero",
          load_scores([{"opp_id": "o", "project_id": "p",
                        "match_probability": ""}]) == {})
    check("a score exactly at the low floor is not flagged",
          disagreements({("a", "b"): LOW_SCORE}, {("a", "b")}, set(), set()) == [])
    check("a score exactly at the high ceiling is flagged",
          len(disagreements({("a", "b"): HIGH_SCORE}, set(), set(), set())) == 1)

    n_linked, n_unscored, _ = coverage({("a", "b"), ("c", "d")},
                                       {("a", "b"): 0.9})
    check("coverage counts links the scores cannot speak to",
          (n_linked, n_unscored) == (2, 1))

    n_ok = sum(1 for _, ok in checks if ok)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    return selftest() if args.selftest else build()


if __name__ == "__main__":
    raise SystemExit(main())
