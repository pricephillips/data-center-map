#!/usr/bin/env python3
"""
stale_pending_audit.py

Mechanism-aware staleness pass over NON-TERMINAL records, ranked by whether
re-verifying one would move a county label.

Why this module exists. Hamilton County TN sat in the tracker as a county
"proposing a pause" while the commission enacted a one-year moratorium on
2026-07-15. Nothing was wrong with the record when it was written; it went
wrong by sitting still. Two existing mechanisms both miss this class:

  review_stale_pending.csv (review_worklists.py) lists `pending` rows older
  than a flat 365 days, unranked. A moratorium proposal is stale long
  before a year is out, and the list gives a reviewer no way to tell which
  of its rows could change a model label if confirmed.

  gate-check.yml watches MODEL and GATE staleness (worklists going stale
  the moment a date is recovered). It does not look at record age at all.

Registered thresholds. Staleness is a property of the instrument, not of
the calendar, so the threshold is per mechanism:

  moratorium, ban, project_denial   120 days   a vote follows a proposal in
                                               weeks to a few months; the
                                               Hamilton gap was 4 months
  conditional_zoning                180 days   ordinance drafting plus
                                               hearing cycles run longer
  legislation                       270 days   bound by session calendars;
                                               a bill pending mid-session
                                               is correctly pending, and
                                               calling it stale earlier
                                               would push against the
                                               approved-is-not-law rule
  litigation                        365 days   dockets move slowly
  everything else                   365 days   matches the existing flat
                                               STALE_DAYS, so this module
                                               never calls a row stale that
                                               review_worklists calls fresh

Ordering criterion, pre-registered here so the ranking cannot be tuned
after seeing the output:

  1  label-moving and overdue. Restrictive mechanism, county currently
     labelled has_enacted_restrictive == 0, past threshold. Confirming one
     of these moves a county from the negative class to the positive class.
     This is the Hamilton class and it is the only tier that changes what
     the county model trains on.
  2  overdue, restrictive mechanism, county already labelled enacted. The
     record grade may be wrong; the county label does not move.
  3  overdue, non-restrictive mechanism. Record accuracy only.
  4  not yet overdue but inside the early-warning band (30 days). Listed so
     a reviewer can act before the row goes stale, never counted as stale.

Scope. Statewide rows are excluded: legislative staleness is owned by
bill_sync.py and data/bill_status_review.csv, which reads machine-coded
action histories and is a better instrument than record age. The count of
excluded statewide rows is reported so the exclusion stays visible.

This module measures and ranks. It does not change a status, a grade, or a
label; every row it emits needs a primary source before anything moves.

Outputs
  data/stale_pending_worklist.csv   ranked re-verification queue
  data/stale_pending_summary.json   counts for CI and the gate check
  data/stale_pending_report.md      reviewer-facing summary

Standing rules honored: four-tier vocabulary, additive, writes only new
files, LF line endings, no em-dashes, --selftest with no data or network
dependency.

Usage
  python stale_pending_audit.py
  python stale_pending_audit.py --state TN,GA
  python stale_pending_audit.py --as-of 2026-08-18
  python stale_pending_audit.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_CLEAN = os.path.join(HERE, "master_opposition_clean.csv")
AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
OUT_CSV = os.path.join(HERE, "data", "stale_pending_worklist.csv")
OUT_JSON = os.path.join(HERE, "data", "stale_pending_summary.json")
OUT_MD = os.path.join(HERE, "data", "stale_pending_report.md")

# Grades that mean the action has reached a disposition. restricted_conditional
# counts as terminal here because a conditional restriction is an action taken,
# not an action awaited.
TERMINAL_OUTCOMES = {"blocked_confirmed", "advanced_confirmed",
                     "restricted_conditional"}

# Everything else is awaiting something: `pending`, the two unverified
# grades, `mixed`, and a blank grade. Exported because adjacency_scan.py
# needs the same definition and two copies would drift.
NON_TERMINAL_OUTCOMES = {"pending", "blocked_unverified",
                         "advanced_unverified", "mixed", ""}

# Mechanisms that can carry a county into has_enacted_restrictive.
RESTRICTIVE_MECHS = {"moratorium", "ban", "conditional_zoning"}

# Registered per-mechanism thresholds, in days. See the docstring for the
# reasoning behind each one.
THRESHOLDS = {
    "moratorium": 120,
    "ban": 120,
    "project_denial": 120,
    "conditional_zoning": 180,
    "legislation": 270,
    "litigation": 365,
}
DEFAULT_THRESHOLD = 365

# How far ahead of the threshold a row enters the early-warning tier.
EARLY_WARNING_DAYS = 30

FIELDS = ["recheck_priority", "priority_reason", "State", "County", "fips",
          "Incident", "Date", "days_stale", "threshold_days", "overdue_ratio",
          "qc_mechanism", "status_clean", "outcome_defensible",
          "county_label", "label_moving", "project_id", "Source URL"]


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def parse_date(value: str) -> date | None:
    v = str(value or "").strip()[:10]
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def truthy(v) -> bool:
    return str(v or "").strip().lower() in {"true", "1", "yes"}


def threshold_for(mech: str) -> int:
    return THRESHOLDS.get((mech or "").strip().lower(), DEFAULT_THRESHOLD)


def county_labels(agg_rows: list[dict]) -> dict[tuple, dict]:
    """Maps (state, normalized county) -> label and fips from the aggregate.

    Normalization is intentionally local and minimal (lowercase, drop the
    County/Parish/Borough word, drop punctuation) rather than imported: this
    module joins tracker rows to the aggregate, which is a same-vocabulary
    join, not the census join that coverage_audit.py has to make.
    """
    out = {}
    for r in agg_rows:
        st = (r.get("state") or "").strip().upper()
        name = (r.get("county_name") or "").split(",")[0]
        key = (st, norm_county(name))
        if st and key[1]:
            out[key] = {"fips": (r.get("fips") or "").strip(),
                        "label": (r.get("has_enacted_restrictive")
                                  or "").strip()}
    return out


def norm_county(name: str) -> str:
    n = str(name or "").lower()
    for word in ("county", "parish", "borough", "municipality"):
        n = n.replace(word, " ")
    n = "".join(ch if ch.isalpha() or ch == " " else " " for ch in n)
    return " ".join(n.split())


def classify(row: dict, as_of: date,
             labels: dict[tuple, dict]) -> dict | None:
    """Returns a worklist row, or None when the record is out of scope."""
    outcome = (row.get("outcome_defensible") or "").strip()
    if outcome not in NON_TERMINAL_OUTCOMES:
        return None
    if truthy(row.get("is_statewide")):
        return None
    county = (row.get("County") or "").strip()
    state = (row.get("State") or "").strip().upper()
    if not county or not state:
        return None
    rec_date = parse_date(row.get("Date"))
    if rec_date is None:
        # An undated non-terminal record cannot be aged. date_recovery.py and
        # data/decision_date_worklist.csv own that problem; double-listing it
        # here would put the same row in two queues with two owners.
        return None

    mech = (row.get("qc_mechanism") or "").strip().lower()
    thresh = threshold_for(mech)
    days = (as_of - rec_date).days
    overdue = days >= thresh
    ratio = round(days / thresh, 2) if thresh else None

    ref = labels.get((state, norm_county(county)), {})
    label = ref.get("label", "")
    restrictive = mech in RESTRICTIVE_MECHS
    label_moving = restrictive and label == "0"

    if overdue and label_moving:
        pri, why = 1, ("restrictive instrument past its threshold in a county "
                       "not yet labelled enacted; a confirmed enactment moves "
                       "the county label")
    elif overdue and restrictive:
        pri, why = 2, ("restrictive instrument past its threshold; the county "
                       "is already labelled enacted, so only the record grade "
                       "is at stake")
    elif overdue:
        pri, why = 3, "non-restrictive instrument past its threshold"
    elif days >= thresh - EARLY_WARNING_DAYS:
        pri, why = 4, (f"inside the {EARLY_WARNING_DAYS}-day early-warning "
                       f"band, not yet stale")
    else:
        return None

    return {
        "recheck_priority": pri,
        "priority_reason": why,
        "State": state,
        "County": county,
        "fips": ref.get("fips", ""),
        "Incident": (row.get("Incident") or "").strip(),
        "Date": rec_date.isoformat(),
        "days_stale": days,
        "threshold_days": thresh,
        "overdue_ratio": ratio,
        "qc_mechanism": mech,
        "status_clean": (row.get("status_clean") or "").strip(),
        "outcome_defensible": outcome,
        "county_label": label,
        "label_moving": "1" if label_moving else "0",
        "project_id": (row.get("project_id") or "").strip(),
        "Source URL": (row.get("Source URL") or "").strip(),
    }


def audit(records: list[dict], agg_rows: list[dict], as_of: date,
          states: set[str] | None = None) -> tuple[list[dict], dict]:
    labels = county_labels(agg_rows)
    rows, excluded_statewide, undated = [], 0, 0
    for r in records:
        if truthy(r.get("is_statewide")):
            if (r.get("outcome_defensible") or "").strip() \
                    in NON_TERMINAL_OUTCOMES:
                excluded_statewide += 1
            continue
        if (r.get("outcome_defensible") or "").strip() in \
                NON_TERMINAL_OUTCOMES and parse_date(r.get("Date")) is None:
            undated += 1
        out = classify(r, as_of, labels)
        if out is None:
            continue
        if states and out["State"] not in states:
            continue
        rows.append(out)

    rows.sort(key=lambda r: (r["recheck_priority"],
                             -(r["overdue_ratio"] or 0), r["State"],
                             r["County"]))
    by_pri = Counter(r["recheck_priority"] for r in rows)
    stale = [r for r in rows if r["recheck_priority"] in (1, 2, 3)]
    summary = {
        "as_of": as_of.isoformat(),
        "non_terminal_scoped": len(rows),
        "stale": len(stale),
        "priority_1_label_moving": by_pri.get(1, 0),
        "priority_2_grade_only": by_pri.get(2, 0),
        "priority_3_non_restrictive": by_pri.get(3, 0),
        "priority_4_early_warning": by_pri.get(4, 0),
        "label_moving_total": sum(1 for r in rows if r["label_moving"] == "1"),
        "counties_in_priority_1":
            len({(r["State"], r["County"]) for r in rows
                 if r["recheck_priority"] == 1}),
        "counties_with_a_label_moving_row":
            len({(r["State"], r["County"]) for r in rows
                 if r["label_moving"] == "1"}),
        "excluded_statewide_non_terminal": excluded_statewide,
        "excluded_undated_non_terminal": undated,
        "thresholds_days": dict(THRESHOLDS,
                                default=DEFAULT_THRESHOLD),
        "note": ("Age is a prompt to re-verify, never evidence that a status "
                 "changed. Priority 1 rows are the only ones whose "
                 "confirmation moves a county label. Statewide rows are out "
                 "of scope here and belong to bill_sync.py; undated "
                 "non-terminal rows belong to the date worklists."),
    }
    return rows, summary


def write_rows(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_report(rows: list[dict], summary: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    top = [r for r in rows if r["recheck_priority"] == 1][:25]
    by_state = Counter(r["State"] for r in rows
                       if r["recheck_priority"] in (1, 2, 3))
    lines = [
        "# Stale non-terminal records: re-verification queue",
        "",
        f"As of {summary['as_of']}. Generated by stale_pending_audit.py.",
        "",
        "Age is a prompt to re-verify, not evidence that anything changed. "
        "Nothing in this queue moves a status, a grade, or a label without a "
        "primary source.",
        "",
        "## Counts",
        "",
        f"- Non-terminal county records in scope: "
        f"{summary['non_terminal_scoped']}",
        f"- Past threshold (priorities 1 to 3): {summary['stale']}",
        f"- Priority 1, label-moving: {summary['priority_1_label_moving']} "
        f"across {summary['counties_in_priority_1']} counties",
        f"- Label-moving rows at any priority, early warning included: "
        f"{summary['label_moving_total']} across "
        f"{summary['counties_with_a_label_moving_row']} counties",
        f"- Priority 2, grade only: {summary['priority_2_grade_only']}",
        f"- Priority 3, non-restrictive: "
        f"{summary['priority_3_non_restrictive']}",
        f"- Priority 4, early warning: "
        f"{summary['priority_4_early_warning']}",
        f"- Excluded, statewide (owned by bill_sync.py): "
        f"{summary['excluded_statewide_non_terminal']}",
        f"- Excluded, undated (owned by the date worklists): "
        f"{summary['excluded_undated_non_terminal']}",
        "",
        "## Thresholds, in days",
        "",
        "| Mechanism | Threshold |",
        "|---|---|",
    ]
    for mech, days in sorted(THRESHOLDS.items()):
        lines.append(f"| {mech} | {days} |")
    lines.append(f"| everything else | {DEFAULT_THRESHOLD} |")
    lines += [
        "",
        "## Priority 1: confirming one of these moves a county label",
        "",
        "| State | County | Date | Days | Mechanism | Grade | Source |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in top:
        src = r["Source URL"] or "no source on record"
        lines.append(f"| {r['State']} | {r['County']} | {r['Date']} | "
                     f"{r['days_stale']} | {r['qc_mechanism']} | "
                     f"{r['outcome_defensible']} | {src} |")
    if not top:
        lines.append("| none | | | | | | |")
    lines += [
        "",
        "## Stale rows by state (priorities 1 to 3)",
        "",
        "| State | Rows |",
        "|---|---|",
    ]
    for st, n in sorted(by_state.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {st} | {n} |")
    lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def ck(label, cond):
        checks.append((label, cond))

    as_of = date(2026, 8, 18)
    agg = [
        {"fips": "47065", "county_name": "Hamilton County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "0"},
        {"fips": "47037", "county_name": "Davidson County, Tennessee",
         "state": "TN", "has_enacted_restrictive": "1"},
        {"fips": "19153", "county_name": "Polk County, Iowa",
         "state": "IA", "has_enacted_restrictive": "0"},
    ]
    records = [
        # The Hamilton class: proposed pause, four months old, county not
        # yet labelled enacted.
        {"Incident": "Hamilton County pause", "State": "TN",
         "County": "Hamilton County", "Date": "2026-03-01",
         "qc_mechanism": "moratorium", "status_clean": "active",
         "outcome_defensible": "pending", "is_statewide": "False",
         "Source URL": "https://example.org/hamilton"},
        # Restrictive and overdue, but the county is already labelled.
        {"Incident": "Davidson zoning", "State": "TN",
         "County": "Davidson County", "Date": "2026-01-01",
         "qc_mechanism": "moratorium", "status_clean": "active",
         "outcome_defensible": "pending", "is_statewide": "False",
         "Source URL": "https://example.org/davidson"},
        # Non-restrictive mechanism, well past the default threshold.
        {"Incident": "Polk ratepayer fight", "State": "IA",
         "County": "Polk County", "Date": "2024-01-01",
         "qc_mechanism": "cost_allocation", "status_clean": "active",
         "outcome_defensible": "pending", "is_statewide": "False",
         "Source URL": "https://example.org/polk"},
        # Terminal: out of scope entirely.
        {"Incident": "Terminal block", "State": "TN",
         "County": "Hamilton County", "Date": "2020-01-01",
         "qc_mechanism": "moratorium", "status_clean": "passed",
         "outcome_defensible": "blocked_confirmed", "is_statewide": "False"},
        # restricted_conditional is treated as terminal.
        {"Incident": "Conditional approval", "State": "TN",
         "County": "Hamilton County", "Date": "2020-01-01",
         "qc_mechanism": "conditional_zoning", "status_clean": "passed",
         "outcome_defensible": "restricted_conditional",
         "is_statewide": "False"},
        # Statewide: excluded, owned by bill_sync.
        {"Incident": "State bill", "State": "TN", "County": "Davidson County",
         "Date": "2024-01-01", "qc_mechanism": "legislation",
         "status_clean": "active", "outcome_defensible": "pending",
         "is_statewide": "True"},
        # Undated non-terminal: excluded, owned by the date worklists.
        {"Incident": "Undated", "State": "IA", "County": "Polk County",
         "Date": "", "qc_mechanism": "moratorium", "status_clean": "active",
         "outcome_defensible": "pending", "is_statewide": "False"},
        # Fresh: 10 days old, nowhere near any threshold.
        {"Incident": "Fresh proposal", "State": "IA",
         "County": "Polk County", "Date": "2026-08-08",
         "qc_mechanism": "moratorium", "status_clean": "active",
         "outcome_defensible": "pending", "is_statewide": "False"},
        # Early warning: 100 days against a 120-day threshold.
        {"Incident": "Nearly stale", "State": "IA", "County": "Polk County",
         "Date": "2026-05-10", "qc_mechanism": "moratorium",
         "status_clean": "active", "outcome_defensible": "pending",
         "is_statewide": "False"},
        # A `mixed` grade is non-terminal.
        {"Incident": "Mixed outcome", "State": "IA", "County": "Polk County",
         "Date": "2025-01-01", "qc_mechanism": "conditional_zoning",
         "status_clean": "active", "outcome_defensible": "mixed",
         "is_statewide": "False"},
        # Legislation at the county level: 270-day threshold, not 120. At
        # 198 days this row is NOT stale, which is the legislative-discipline
        # point: a shorter clock would push against approved-is-not-law.
        {"Incident": "County resolution", "State": "IA",
         "County": "Polk County", "Date": "2026-02-01",
         "qc_mechanism": "legislation", "status_clean": "active",
         "outcome_defensible": "blocked_unverified", "is_statewide": "False"},
        # Same mechanism past 270 days: listed, and non-restrictive, so it
        # ranks below anything that could move a label.
        {"Incident": "County resolution overdue", "State": "IA",
         "County": "Polk County", "Date": "2025-08-01",
         "qc_mechanism": "legislation", "status_clean": "active",
         "outcome_defensible": "blocked_unverified", "is_statewide": "False"},
    ]

    rows, summary = audit(records, agg, as_of)
    by = {r["Incident"]: r for r in rows}

    ck("Hamilton class is priority 1",
       by["Hamilton County pause"]["recheck_priority"] == 1)
    ck("Hamilton class is label-moving",
       by["Hamilton County pause"]["label_moving"] == "1")
    ck("Hamilton class carries the county fips",
       by["Hamilton County pause"]["fips"] == "47065")
    ck("already-labelled county is priority 2",
       by["Davidson zoning"]["recheck_priority"] == 2)
    ck("already-labelled county is not label-moving",
       by["Davidson zoning"]["label_moving"] == "0")
    ck("non-restrictive mechanism is priority 3",
       by["Polk ratepayer fight"]["recheck_priority"] == 3)
    ck("terminal grades are out of scope", "Terminal block" not in by)
    ck("restricted_conditional counts as terminal",
       "Conditional approval" not in by)
    ck("statewide rows are excluded", "State bill" not in by)
    ck("statewide exclusion is counted",
       summary["excluded_statewide_non_terminal"] == 1)
    ck("undated rows are excluded", "Undated" not in by)
    ck("undated exclusion is counted",
       summary["excluded_undated_non_terminal"] == 1)
    ck("fresh rows are not listed", "Fresh proposal" not in by)
    ck("early-warning row is priority 4",
       by["Nearly stale"]["recheck_priority"] == 4)
    ck("early-warning row is not counted as stale",
       summary["stale"] == 5 and summary["priority_4_early_warning"] == 1)
    ck("mixed grade is non-terminal", "Mixed outcome" in by)
    ck("mixed grade at 180 days is priority 1",
       by["Mixed outcome"]["recheck_priority"] == 1)

    ck("county legislation uses the 270-day threshold, not 120",
       threshold_for("legislation") == 270)
    ck("county legislation 198 days old is not stale at all",
       "County resolution" not in by)
    leg = by["County resolution overdue"]
    ck("county legislation past 270 days is listed",
       leg["threshold_days"] == 270 and leg["days_stale"] == 382)
    ck("legislation is not a label-moving mechanism",
       leg["recheck_priority"] == 3 and leg["label_moving"] == "0")

    ck("threshold lookup falls back to the default",
       threshold_for("public_pressure") == DEFAULT_THRESHOLD)
    ck("threshold lookup is case-insensitive",
       threshold_for("Moratorium") == 120)
    ck("default threshold matches the flat rule it replaces",
       DEFAULT_THRESHOLD == 365)

    ck("overdue ratio is reported",
       by["Hamilton County pause"]["overdue_ratio"] == round(170 / 120, 2))
    ck("days_stale is measured from the record date",
       by["Hamilton County pause"]["days_stale"] == 170)

    ck("priority 1 sorts before priority 2",
       rows[0]["recheck_priority"] == 1)
    ck("summary counts every label-moving row, early warning included",
       summary["label_moving_total"] == 3)
    ck("priority-1 county count never exceeds the priority-1 row count",
       summary["counties_in_priority_1"]
       <= summary["priority_1_label_moving"])
    ck("only overdue label-moving rows land in priority 1",
       summary["priority_1_label_moving"] == 2)

    st_rows, _ = audit(records, agg, as_of, states={"IA"})
    ck("state filter applies",
       all(r["State"] == "IA" for r in st_rows) and st_rows)

    ck("norm_county drops the county word",
       norm_county("Hamilton County") == "hamilton")
    ck("norm_county drops punctuation",
       norm_county("St. Joseph County") == "st joseph")
    ck("date parser handles slashes",
       parse_date("03/01/2026") == date(2026, 3, 1))
    ck("date parser rejects junk", parse_date("not a date") is None)
    ck("empty aggregate degrades to unknown label",
       audit(records, [], as_of)[0][0]["county_label"] == "")

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--state", default=None,
                    help="comma-separated state codes to restrict the queue")
    ap.add_argument("--as-of", default=None,
                    help="YYYY-MM-DD; defaults to today")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    for path in (MASTER_CLEAN, AGG_CSV):
        if not os.path.exists(path):
            print(f"ERROR: {os.path.relpath(path, HERE)} not found; "
                  f"the staleness audit needs it")
            return 1

    as_of = parse_date(args.as_of) if args.as_of else date.today()
    if as_of is None:
        print(f"ERROR: could not parse --as-of {args.as_of!r}")
        return 1
    states = ({s.strip().upper() for s in args.state.split(",") if s.strip()}
              if args.state else None)

    records = read_csv(MASTER_CLEAN)
    agg = read_csv(AGG_CSV)
    rows, summary = audit(records, agg, as_of, states)
    if states:
        summary["state_filter"] = sorted(states)

    write_rows(rows, OUT_CSV)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    write_report(rows, summary, OUT_MD)

    print(f"as of {summary['as_of']}")
    print(f"non-terminal county records in scope: "
          f"{summary['non_terminal_scoped']}")
    print(f"past threshold: {summary['stale']}")
    print(f"  priority 1 label-moving:   "
          f"{summary['priority_1_label_moving']} "
          f"({summary['counties_in_priority_1']} counties)")
    print(f"  priority 2 grade only:     "
          f"{summary['priority_2_grade_only']}")
    print(f"  priority 3 non-restrictive:"
          f" {summary['priority_3_non_restrictive']}")
    print(f"  priority 4 early warning:  "
          f"{summary['priority_4_early_warning']}")
    print(f"excluded statewide: {summary['excluded_statewide_non_terminal']}, "
          f"excluded undated: {summary['excluded_undated_non_terminal']}")
    print(f"\nwrote {OUT_CSV}")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
