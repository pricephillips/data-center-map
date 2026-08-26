#!/usr/bin/env python3
"""
operations_summary.py

Collapses the platform's self-maintenance record into one small artifact a
page can load.

The operations layer is the part of this repository that runs without being
asked: coverage is measured against an external census, gaps become ranked
work, candidates are built and promoted through a gate, harvested signals are
promoted or blocked with a reason, and models are retrained and either promoted
or held on calibration. Every one of those steps already writes an audit trail.
None of it was visible anywhere, so the platform's strongest claim, that it
maintains itself and records how, could only be made by assertion.

This module reads those trails and writes a single summary. It computes,
rather than the page, for two reasons: data/signal_promotion_report.csv is
two megabytes and has no business crossing a browser, and a number a client
reads should be derived once in a module with a self-test rather than in page
JavaScript that nothing checks.

Reads
  data/coverage_gap_summary.json     recall, per state and national
  data/gap_promotion_report.csv      census gap promotion decisions
  data/signal_promotion_report.csv   harvested signal promotion decisions
  data/calibration_history.csv       every retrain verdict
  data/national_restriction_worklist.csv
  data/stale_pending_summary.json
  data/decision_date_worklist.csv, data/announce_date_worklist.csv
  data/landmark_feasibility.csv      landmark gate, per window
  data/emergence_bounds.csv          emergence gate, per stratum
  data/facility_manifest.json        Layer A provenance

Writes
  data/operations_summary.json

Every input is optional. A missing file becomes a null section and the page
omits that panel, because an operations page that invents a number is worse
than one with a gap in it.

Usage
  python operations_summary.py
  python operations_summary.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT_JSON = os.path.join(DATA, "operations_summary.json")

RECENT = 8          # decisions shown per trail


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_csv(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def coverage_section(summary: dict | None) -> dict | None:
    if not summary:
        return None
    states = summary.get("states", {})
    rows = []
    confirmed = scope = 0
    for code, s in sorted(states.items()):
        if not s.get("census_in_scope"):
            continue                     # a state with nothing in scope is not a gap
        confirmed += s.get("covered_confirmed", 0)
        scope += s.get("census_in_scope", 0)
        rows.append({
            "state": code,
            "census_in_scope": s.get("census_in_scope"),
            "covered_confirmed": s.get("covered_confirmed"),
            "covered_unconfirmed": s.get("covered_unconfirmed"),
            "missing": s.get("missing"),
            "recall_any": s.get("recall_any"),
            "recall_confirmed": s.get("recall_confirmed"),
            "label_false_negatives": s.get("label_false_negatives"),
        })
    rows.sort(key=lambda r: ((r["recall_confirmed"] is None),
                             r["recall_confirmed"] or 0, -r["census_in_scope"]))
    nat = dict(summary.get("national", {}))
    nat["recall_confirmed"] = round(confirmed / scope, 3) if scope else None
    nat["missing"] = sum(r["missing"] or 0 for r in rows)
    return {"national": nat, "states": rows,
            "note": summary.get("note", "")}


def trail_section(rows: list[dict], date_field: str,
                  label_fields: list[str]) -> dict | None:
    if not rows:
        return None
    actions = Counter((r.get("action") or "unrecorded").strip() for r in rows)
    dates = sorted({(r.get(date_field) or "").strip() for r in rows} - {""})
    recent = []
    for r in rows[-RECENT:][::-1]:
        recent.append({
            "date": (r.get(date_field) or "").strip(),
            "action": (r.get("action") or "").strip(),
            "what": " ".join(str(r.get(f) or "").strip()
                             for f in label_fields).strip(),
            "reason": (r.get("reason") or r.get("blocking_reasons") or "").strip(),
        })
    return {
        "decisions": len(rows),
        "by_action": dict(actions.most_common()),
        "first_run": dates[0] if dates else None,
        "last_run": dates[-1] if dates else None,
        "recent": recent,
    }


def calibration_section(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    runs = []
    for r in rows:
        runs.append({
            "timestamp": (r.get("timestamp") or "")[:10],
            "model": r.get("model"),
            "n": r.get("n"),
            "ece": r.get("ece"),
            "brier_skill_score": r.get("brier_skill_score"),
            "verdict": (r.get("verdict") or "").strip(),
        })
    verdicts = [x["verdict"] for x in runs]
    streak = 0
    for v in reversed(verdicts):
        if v == verdicts[-1]:
            streak += 1
        else:
            break
    return {
        "runs": len(runs),
        "by_verdict": dict(Counter(verdicts).most_common()),
        "last_verdict": verdicts[-1],
        "last_run": runs[-1]["timestamp"],
        "current_streak": streak,
        "recent": runs[-RECENT:][::-1],
    }


def worklist_section(national: list[dict], stale: dict | None,
                     decision_rows: list[dict],
                     announce_rows: list[dict]) -> dict:
    out: dict = {}
    if national:
        out["national_restriction"] = {
            "rows": len(national),
            "by_task_class": dict(Counter(
                (r.get("task_class") or "unclassified").strip()
                for r in national).most_common()),
            "priority_1": sum(1 for r in national
                              if str(r.get("priority", "")).strip() == "1"),
        }
    if stale:
        out["stale_pending"] = {
            "scoped": stale.get("non_terminal_scoped"),
            "stale": stale.get("stale"),
            "priority_1_label_moving": stale.get("priority_1_label_moving"),
            "counties_in_priority_1": stale.get("counties_in_priority_1"),
            "as_of": stale.get("as_of"),
        }
    if decision_rows:
        out["decision_dates"] = {"open": len(decision_rows)}
    if announce_rows:
        out["announced_dates"] = {"open": len(announce_rows)}
    return out


def gates_section(landmark: list[dict], emergence: list[dict]) -> dict:
    out: dict = {}
    if landmark:
        windows = []
        for r in landmark:
            windows.append({
                "window_days": r.get("window_days"),
                "n": r.get("n"),
                "n_blocked": r.get("n_blocked"),
                "n_not_blocked": r.get("n_not_blocked"),
                "gate": (r.get("gate") or "").strip(),
            })
        open_windows = [w for w in windows if w["gate"] not in ("INFEASIBLE", "")]
        out["landmark"] = {
            "status": "OPEN" if open_windows else "CLOSED",
            "floors": (landmark[0].get("floors") or "").strip(),
            "windows": windows,
        }
    if emergence:
        strata = []
        for r in emergence:
            strata.append({
                "stratum": r.get("stratum"),
                "frame_n": r.get("frame_n"),
                "n_coded": r.get("n_coded"),
                "coverage": r.get("coverage"),
                "undet_share": r.get("undet_share"),
            })
        random = next((s for s in strata if s["stratum"] == "random"), None)
        cov = float(random["coverage"]) if random and random["coverage"] else 0.0
        out["emergence"] = {
            "status": "OPEN" if cov >= 0.60 else "LOCKED",
            "random_coverage": cov,
            "strata": strata,
        }
    return out


def build(root: str = HERE, today: str | None = None) -> dict:
    data = os.path.join(root, "data")
    J = lambda name: read_json(os.path.join(data, name))
    C = lambda name: read_csv(os.path.join(data, name))

    facility = J("facility_manifest.json")
    return {
        "generated": today or dt.date.today().isoformat(),
        "coverage": coverage_section(J("coverage_gap_summary.json")),
        "promotions": {
            "census_gaps": trail_section(
                C("gap_promotion_report.csv"), "date",
                ["state", "county", "instrument"]),
            "harvested_signals": trail_section(
                C("signal_promotion_report.csv"), "run_date",
                ["state", "county", "mechanism_hint"]),
        },
        "calibration": calibration_section(C("calibration_history.csv")),
        "worklists": worklist_section(
            C("national_restriction_worklist.csv"),
            J("stale_pending_summary.json"),
            C("decision_date_worklist.csv"),
            C("announce_date_worklist.csv")),
        "gates": gates_section(C("landmark_feasibility.csv"),
                               C("emergence_bounds.csv")),
        "facility_sources": (facility or {}).get("sources"),
        "note": ("Every figure here is read from an audit trail the pipeline "
                 "writes on its own runs. A missing input becomes a null "
                 "section rather than a zero: an operations page that invents "
                 "a number is worse than one with a gap in it."),
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    # coverage: a state with nothing in scope must not dilute the table
    cov = coverage_section({
        "states": {
            "AA": {"census_in_scope": 4, "covered_confirmed": 1,
                   "covered_unconfirmed": 2, "missing": 1, "recall_any": 0.75,
                   "recall_confirmed": 0.25, "label_false_negatives": 1},
            "BB": {"census_in_scope": 2, "covered_confirmed": 2,
                   "covered_unconfirmed": 0, "missing": 0, "recall_any": 1.0,
                   "recall_confirmed": 1.0, "label_false_negatives": 0},
            "ZZ": {"census_in_scope": 0, "covered_confirmed": 0,
                   "covered_unconfirmed": 0, "missing": 0,
                   "recall_any": None, "recall_confirmed": None,
                   "label_false_negatives": 0},
        },
        "national": {"census_in_scope": 6, "recall_any": 0.833},
        "note": "n",
    })
    check("empty state dropped", [r["state"] for r in cov["states"]] == ["AA", "BB"])
    check("weakest confirmed recall first", cov["states"][0]["state"] == "AA")
    check("national confirmed recall recomputed",
          cov["national"]["recall_confirmed"] == 0.5)
    check("national missing summed", cov["national"]["missing"] == 1)
    check("coverage is None when the input is missing",
          coverage_section(None) is None)

    trail = trail_section(
        [{"date": "2026-08-01", "action": "promoted", "state": "IA",
          "county": "Story", "instrument": "moratorium", "reason": ""},
         {"date": "2026-08-02", "action": "held", "state": "NC",
          "county": "Cherokee", "instrument": "moratorium",
          "reason": "no usable date"}],
        "date", ["state", "county", "instrument"])
    check("trail counts decisions", trail["decisions"] == 2)
    check("trail counts actions",
          trail["by_action"] == {"promoted": 1, "held": 1})
    check("trail reports the run span",
          trail["first_run"] == "2026-08-01" and trail["last_run"] == "2026-08-02")
    check("recent is newest first", trail["recent"][0]["action"] == "held")
    check("recent carries the reason",
          trail["recent"][0]["reason"] == "no usable date")
    check("empty trail is None", trail_section([], "date", ["state"]) is None)

    cal = calibration_section([
        {"timestamp": "2026-07-15T16:44:29Z", "model": "m", "n": "78",
         "ece": "0.13", "brier_skill_score": "0.10", "verdict": "HOLD"},
        {"timestamp": "2026-08-17T07:52:37Z", "model": "m", "n": "88",
         "ece": "0.13", "brier_skill_score": "0.16", "verdict": "PROMOTE"},
        {"timestamp": "2026-08-24T07:58:02Z", "model": "m", "n": "89",
         "ece": "0.07", "brier_skill_score": "0.20", "verdict": "PROMOTE"},
    ])
    check("verdict counted", cal["by_verdict"] == {"PROMOTE": 2, "HOLD": 1})
    check("streak counts only the trailing run", cal["current_streak"] == 2)
    check("last verdict is the latest row", cal["last_verdict"] == "PROMOTE")

    gates = gates_section(
        [{"window_days": "30", "n": "24", "n_blocked": "18",
          "n_not_blocked": "6", "gate": "INFEASIBLE",
          "floors": "n>=40, blocked>=12, not_blocked>=12"}],
        [{"stratum": "random", "frame_n": "159", "n_coded": "0",
          "coverage": "0.0", "undet_share": "0.0"},
         {"stratum": "purposive", "frame_n": "12", "n_coded": "9",
          "coverage": "0.75", "undet_share": "0.22"}])
    check("landmark closed on infeasible windows",
          gates["landmark"]["status"] == "CLOSED")
    check("emergence locked on random coverage, not pooled coverage",
          gates["emergence"]["status"] == "LOCKED"
          and gates["emergence"]["random_coverage"] == 0.0)

    open_gate = gates_section(
        [{"window_days": "30", "n": "44", "n_blocked": "18",
          "n_not_blocked": "20", "gate": "FEASIBLE", "floors": "f"}],
        [{"stratum": "random", "frame_n": "159", "n_coded": "100",
          "coverage": "0.63", "undet_share": "0.2"}])
    check("landmark opens on a feasible window",
          open_gate["landmark"]["status"] == "OPEN")
    check("emergence opens at the registered threshold",
          open_gate["emergence"]["status"] == "OPEN")

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "data"))
        out = build(root=tmp, today="2026-08-26")
        check("every section is null on an empty tree",
              out["coverage"] is None and out["calibration"] is None
              and out["promotions"]["census_gaps"] is None
              and out["worklists"] == {} and out["gates"] == {})
        check("generated date is stamped", out["generated"] == "2026-08-26")

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    summary = build()
    os.makedirs(DATA, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    print(f"wrote {os.path.relpath(OUT_JSON, HERE)}")
    cov = summary.get("coverage")
    if cov:
        n = cov["national"]
        print(f"  coverage: recall_any={n.get('recall_any')} "
              f"recall_confirmed={n.get('recall_confirmed')} "
              f"missing={n.get('missing')}")
    for name, trail in summary["promotions"].items():
        if trail:
            print(f"  {name}: {trail['decisions']} decisions, "
                  + ", ".join(f"{k}={v}" for k, v in trail["by_action"].items()))
    cal = summary.get("calibration")
    if cal:
        print(f"  calibration: {cal['runs']} runs, last {cal['last_verdict']} "
              f"({cal['current_streak']} in a row)")
    for gate, info in summary["gates"].items():
        print(f"  gate {gate}: {info['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
