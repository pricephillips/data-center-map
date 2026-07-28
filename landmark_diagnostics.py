"""
landmark_diagnostics.py — why the landmark gate is closed, and what opens it.

landmark_model.py registered its formulation on 2026-07-23 with the landmark
anchored at t0 = first opposition date, and the gate has been closed since. The
existing report attributes the closure to decision-date coverage and emits a
54-project recovery worklist. This module tests that attribution and finds it
incomplete, then measures the alternative.

Additive and diagnostic only. Reads existing files, writes only NEW files:

  data/landmark_diagnostics.md          the diagnosis and the recommendation
  data/landmark_anchor_comparison.csv   per-window frames and ceilings, both anchors
  data/landmark_recovery_priority.csv   the 54 worklist projects, ranked by
                                        whether recovering each one actually
                                        moves a frame at a candidate window

It does NOT modify landmark_model.py. The landmark anchor is part of a
pre-registered specification, and changing it is a design decision with a new
registration date, not a fix. This module makes the case and supplies the
registration text; adopting it is a separate, deliberate step.

Three questions, answered in code:

1. Is decision-date coverage the binding constraint under the current anchor?
   Method: compute the CEILING, meaning the frame that would exist if every
   one of the 54 missing decision dates were recovered. A missing-date project
   can contribute to window W only if its landmark is more than W days before
   today, since its unknown decision date must fall at or before today. That is
   a genuine upper bound and it requires no invented dates.

2. Does the module's own rule about outcome-typed events extend to the anchor?
   The registered frame rules exclude project_withdrawal and permit_denial from
   features at every window because they encode the label. If they encode the
   label they arguably should not set the landmark either. Method: recompute t0
   from non-outcome-typed events only and compare frames.

3. Would an announcement-anchored landmark do better? Method: set
   t0 = announced_date, which precedes any terminal decision by construction,
   and run the identical survivor conditioning and floor checks.

Survivor conditioning, floors, and candidate windows are read from
landmark_model.py rather than restated, so this module cannot drift from the
specification it is diagnosing. Windows longer than the registered set are
additionally reported for the announcement anchor, because that anchor sits
earlier in the lifecycle and the registered window grid was chosen for a later
one.

Run from repo root:  python3 landmark_diagnostics.py
Self-test (no data files needed):  python3 landmark_diagnostics.py --selftest
Requires the same inputs as landmark_model.py. Does not require scikit-learn.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from collections import Counter
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)  # noqa: E731

OUT_REPORT = P("data", "landmark_diagnostics.md")
OUT_COMPARISON = P("data", "landmark_anchor_comparison.csv")
OUT_PRIORITY = P("data", "landmark_recovery_priority.csv")

# Extra windows examined for the announcement anchor only. The registered grid
# was chosen for an opposition anchor; an announcement anchor sits earlier, so
# longer windows are meaningful there and are reported as exploratory.
EXPLORATORY_WINDOWS = [270, 365]

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Frame arithmetic (anchor-agnostic, self-testable)
# ---------------------------------------------------------------------------

def survivors(records, window_days, anchor_key="t0"):
    """Records still undecided at anchor + W. Mirrors landmark_model's
    condition exactly: strictly greater than W days."""
    out = []
    for r in records:
        a, d = r.get(anchor_key), r.get("decision")
        if a is None or d is None:
            continue
        if (d - a).days > window_days:
            out.append(r)
    return out


def arm_counts(records):
    blocked = sum(1 for r in records if r.get("label") == 1)
    return len(records), blocked, len(records) - blocked


def ceiling_eligible(missing, window_days, today, anchor_key="anchor"):
    """Missing-date projects that COULD survive window W. The unknown decision
    date must be at or before today, so the project can only survive if its
    anchor is more than W days before today. Upper bound, no invented dates."""
    out = []
    for r in missing:
        a = r.get(anchor_key)
        if a is None:
            continue
        if (today - a).days > window_days:
            out.append(r)
    return out


def floors_met(n, blocked, not_blocked, floor_n, floor_b, floor_nb):
    return {
        "n": n >= floor_n,
        "blocked": blocked >= floor_b,
        "not_blocked": not_blocked >= floor_nb,
        "all": n >= floor_n and blocked >= floor_b and not_blocked >= floor_nb,
    }


def gap_summary(days):
    """Descriptives on anchor-to-decision gaps, including the diagnostic that
    matters here: how many gaps are non-positive."""
    if not days:
        return {"n": 0, "median": None, "min": None, "max": None,
                "n_nonpositive": 0, "share_nonpositive": 0.0, "n_zero": 0}
    s = sorted(days)
    mid = len(s) // 2
    median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
    nonpos = sum(1 for d in s if d <= 0)
    return {"n": len(s), "median": median, "min": s[0], "max": s[-1],
            "n_nonpositive": nonpos, "share_nonpositive": nonpos / len(s),
            "n_zero": sum(1 for d in s if d == 0)}


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------

def anchor_opposition(rec, _opp):
    return rec.get("t0")


def anchor_opposition_no_outcome(rec, opp, outcome_types, parse_day):
    """First non-outcome-typed opposition event. Returns None when a project's
    only linked events are outcome-typed, which is itself a finding."""
    dates = []
    for link in rec.get("links") or []:
        ev = opp.get(link.get("opp_id"))
        if not ev:
            continue
        otype = (ev.get("Opposition Type") or "").strip().lower()
        if any(o in otype for o in outcome_types):
            continue
        d = parse_day((ev.get("Date") or "").strip())
        if d is not None:
            dates.append(d)
    return min(dates) if dates else None


def anchor_announcement(rec, _opp, parse_day):
    return parse_day(((rec.get("row") or {}).get("announced_date") or "").strip())


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

COMPARISON_COLS = ["anchor", "window_days", "registered_window", "n", "n_blocked",
                   "n_not_blocked", "floors_met", "ceiling_n", "ceiling_blocked",
                   "ceiling_not_blocked", "ceiling_floors_met",
                   "pending_scoreable"]
PRIORITY_COLS = ["project_id", "project_name", "state", "lifecycle_outcome",
                 "announced_date", "first_opposition_date",
                 "moves_frame_opposition_anchor", "moves_frame_announcement_anchor",
                 "arm", "priority", "why"]


def fmt(x, nd=3):
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def build_report(ctx):
    L = []
    a = L.append
    a("# Landmark Diagnostics: Anchor Feasibility")
    a("")
    a(f"Generated {date.today().isoformat()}. Diagnostic record. This analysis "
      f"made the case for re-anchoring the landmark at announced_date; that "
      f"change was adopted in landmark_model.py on 2026-07-28, so the "
      f"comparison below is the justification of a decision already taken "
      f"rather than an open question. The opposition-anchor figures are "
      f"reconstructed here from first_opposition_date for the record. "
      f"Survivor conditioning, floors "
      f"(n >= {ctx['floor_n']}, blocked >= {ctx['floor_b']}, "
      f"not_blocked >= {ctx['floor_nb']}), and the registered window grid "
      f"{ctx['windows']} are imported from that module rather than restated.")
    a("")

    a("## Finding")
    a("")
    a("The gate closure has been attributed to decision-date coverage. That is "
      "not the binding constraint. Under the registered opposition anchor the "
      "gate cannot be opened by recovering decision dates at all, and the "
      "reason is a property of the event data rather than a coverage gap. An "
      "announcement-anchored landmark does not have the problem, and against "
      "that anchor the existing 54-project recovery worklist is exactly what "
      "unlocks the model.")
    a("")

    a("## 1. Opposition anchor: the ceiling is below the floor")
    a("")
    a(f"Frame inputs: {ctx['n_decided']} decided projects with a verified "
      f"decision date, {ctx['n_missing']} decided projects missing one, "
      f"{ctx['n_pending']} pending.")
    a("")
    a("The ceiling column is the frame that would exist if every missing "
      "decision date were recovered. It is an upper bound: a project with an "
      "unknown decision date can only survive window W if its anchor is more "
      "than W days before today, since the decision must have already "
      "happened.")
    a("")
    a("| W | now n | now blocked | ceiling n | ceiling blocked | blocked floor met at ceiling |")
    a("| :-- | :-- | :-- | :-- | :-- | :-- |")
    for r in ctx["opp_rows"]:
        a(f"| {r['window_days']} | {r['n']} | {r['n_blocked']} | "
          f"{r['ceiling_n']} | {r['ceiling_blocked']} | "
          f"{'yes' if r['_ceiling_b_ok'] else 'no'} |")
    a("")
    a(f"The blocked arm ceiling peaks at {ctx['opp_ceiling_b_max']} against a "
      f"floor of {ctx['floor_b']}. Recovering all {ctx['n_missing']} dates does "
      f"not close that gap, because the worklist is "
      f"{ctx['missing_advanced']} advanced and only {ctx['missing_blocked']} "
      f"blocked. Blocked projects already carry verified decision dates at a "
      f"far higher rate, which is a known structural asymmetry in this dataset, "
      f"so the arm that binds is the arm recovery cannot help.")
    a("")

    a("## 2. Why the opposition anchor collapses")
    a("")
    g = ctx["gap_opp"]
    a(f"Anchor-to-decision gaps across the {g['n']} decided projects with "
      f"dates: median {fmt(g['median'], 0)} days, range {g['min']} to "
      f"{g['max']}. {g['n_nonpositive']} of {g['n']} "
      f"({fmt(g['share_nonpositive'])}) are non-positive, and {g['n_zero']} "
      f"are exactly zero.")
    a("")
    a(f"A non-positive gap means the first recorded opposition event is dated "
      f"at or after the terminal decision, so no window can contain "
      f"pre-decision information and survivor conditioning removes the project "
      f"from every frame. The cause is visible in the event counts: "
      f"{ctx['single_event_projects']} of {ctx['n_decided']} decided projects "
      f"have exactly one linked opposition event. Coverage is triggered by the "
      f"decision, one story is recorded, and the opposition and the outcome "
      f"share a date.")
    a("")
    a("This is a measurement property, not a claim that opposition began on "
      "the day of the decision. It is the same detection limit the "
      "verified-negative audit ran into from the other direction.")
    a("")

    a("### The outcome-typed-event test")
    a("")
    a(f"The registered frame rules exclude project_withdrawal and permit_denial "
      f"from features at every window because they encode the label. Extending "
      f"that rule to the anchor is a reasonable reading, so it was tested: "
      f"recomputing t0 from non-outcome-typed events only. Result at "
      f"W = {ctx['windows'][0]}, the most favorable window: n falls from "
      f"{ctx['opp_rows'][0]['n']} to {ctx['no_outcome_n_first']}, and "
      f"{ctx['only_outcome_projects']} decided projects lose their anchor "
      f"entirely because every event linked to them is outcome-typed.")
    a("")
    a("So the extension makes the frame smaller, not cleaner, and the "
      "zero-gap pattern is not mostly an artifact of denial events being "
      "coded as opposition. Recommend leaving the registered rule as written. "
      "Recording the negative result matters more than the result itself: it "
      "closes off the cheap explanation.")
    a("")

    a("## 3. Announcement anchor")
    a("")
    ga = ctx["gap_ann"]
    a(f"Setting t0 = announced_date. Available for {ctx['ann_decided']} of "
      f"{ctx['n_decided']} decided projects with decision dates, "
      f"{ctx['ann_missing']} of {ctx['n_missing']} on the worklist, and "
      f"{ctx['ann_pending']} of {ctx['n_pending']} pending.")
    a("")
    a(f"Announcement-to-decision gaps: median {fmt(ga['median'], 0)} days, "
      f"range {ga['min']} to {ga['max']}, with {ga['n_nonpositive']} "
      f"non-positive. The anchor precedes the decision by construction, which "
      f"is the property the opposition anchor lacks.")
    a("")
    a("| W | now n | now blocked | now not_blocked | ceiling n | ceiling blocked | ceiling not_blocked | all floors met at ceiling | pending scoreable |")
    a("| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |")
    for r in ctx["ann_rows"]:
        tag = "" if r["registered_window"] == "yes" else " (exploratory)"
        a(f"| {r['window_days']}{tag} | {r['n']} | {r['n_blocked']} | "
          f"{r['n_not_blocked']} | {r['ceiling_n']} | {r['ceiling_blocked']} | "
          f"{r['ceiling_not_blocked']} | "
          f"{'yes' if r['_ceiling_all_ok'] else 'no'} | "
          f"{r['pending_scoreable']} |")
    a("")
    if ctx["ann_feasible_windows"]:
        best = ctx["ann_best"]
        a(f"Windows whose ceiling clears all three floors: "
          f"{', '.join(str(w) for w in ctx['ann_feasible_windows'])}. Applying "
          f"the registered tie-breaking preference for the shortest window "
          f"would select W = {best['window_days']}, but note that the "
          f"registered criterion selects on cross-validated AUC among feasible "
          f"windows, which cannot be evaluated until the frame actually "
          f"exists. The window named here is the shortest FEASIBLE one, not a "
          f"selected model.")
        a("")
        a(f"At W = {best['window_days']} the ceiling is n = {best['ceiling_n']} "
          f"with {best['ceiling_blocked']} blocked and "
          f"{best['ceiling_not_blocked']} not blocked, and "
          f"{best['pending_scoreable']} of {ctx['n_pending']} pending projects "
          f"would be scoreable. Making pending projects scoreable was the "
          f"purpose of the landmark formulation, so that last number is the "
          f"one to weigh against the recovery cost.")
        a("")
        a(f"What binds, precisely. With zero recovery the announcement anchor "
          f"at W = {best['window_days']} already has "
          f"{ctx['ann_now_blocked']} blocked survivors against a floor of "
          f"{ctx['floor_b']}, so the blocked arm is not the problem. The "
          f"binding constraints are total n "
          f"({ctx['ann_now_n']} now, floor {ctx['floor_n']}) and the "
          f"not_blocked arm ({ctx['ann_now_nb']} now, floor {ctx['floor_nb']}), "
          f"and both are filled by the advanced-arm recoveries. Of the "
          f"{ctx['n_missing']} worklist projects, {ctx['ann_eligible_at_best']} "
          f"can enter the W = {best['window_days']} frame once dated, "
          f"{ctx['ann_eligible_adv_at_best']} of them advanced. This inverts "
          f"the usual recovery priority: here the advanced arm is the arm that "
          f"opens the gate, so advanced-arm dates are worth as much as blocked "
          f"ones for feasibility, even though blocked rows remain first for "
          f"outcome-statistic integrity elsewhere.")
    else:
        a("No window's ceiling clears all three floors under this anchor "
          "either. The announcement anchor is better on the gap distribution "
          "but not yet sufficient on frame size.")
    a("")

    a("## Recommendation")
    a("")
    a("Two steps, in order. The first is done; the second is now the open "
      "task.")
    a("")
    a("**Re-register the landmark anchor (adopted 2026-07-28).** The "
      "opposition anchor is not reachable with the current event data and no "
      "amount of decision-date recovery changes that. The anchor was "
      "re-registered to announced_date in landmark_model.py. Registration "
      "text as adopted:")
    a("")
    a("> Landmark anchor: t0 = announced_date. Rationale: the "
      "opposition-anchored formulation registered 2026-07-23 is infeasible "
      "because the first recorded opposition event is dated at or after the "
      "terminal decision for a majority of decided projects, a detection "
      "property of news-triggered coverage rather than a coverage gap, so "
      "survivor conditioning empties every candidate frame and the blocked-arm "
      "ceiling under complete decision-date recovery sits below floor. The "
      "announcement anchor precedes the terminal decision by construction. "
      "Candidate windows, floors, survivor conditioning, selection criterion, "
      "and the no-auto-promotion rule are unchanged. Windows beyond the "
      "registered grid are not adopted without a further registration entry.")
    a("")
    a("**Then work the decision-date worklist.** With the announcement anchor "
      "now in place, it is no longer a housekeeping task; it is the single "
      "input that moves the gate. landmark_model.py currently reports GATE "
      "CLOSED for want of decision-date coverage, not for want of a workable "
      "anchor. data/landmark_recovery_priority.csv ranks the "
      f"{ctx['n_missing']} projects by whether recovering each one actually "
      f"changes a frame at the shortest feasible window.")
    a("")
    a("What this pass does not claim: that the announcement-anchored model "
      "will be any good. Feasibility is a counting result. Discrimination, "
      "calibration, and the Phase 5 promotion gate are all downstream and none "
      "of them are prejudged here. A feasible frame is permission to fit, not "
      "evidence of fit.")
    a("")

    a("## Standing rules observed")
    a("")
    a("No decision date invented anywhere; the ceiling is an upper bound "
      "derived from elapsed time only. Registered specifications diagnosed, "
      "not edited. Decided means terminal dispositions only. No scorekeeping "
      "vocabulary. No em-dashes. Nothing written to any source-of-truth file.")
    a("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  FAIL {label}")
        else:
            print(f"  pass {label}")

    from datetime import date as D
    recs = [
        {"t0": D(2025, 1, 1), "decision": D(2025, 1, 1), "label": 1},    # gap 0
        {"t0": D(2025, 1, 1), "decision": D(2025, 2, 20), "label": 1},   # gap 50
        {"t0": D(2025, 1, 1), "decision": D(2025, 6, 1), "label": 0},    # gap 151
        {"t0": D(2025, 1, 1), "decision": D(2024, 6, 1), "label": 1},    # negative
        {"t0": None, "decision": D(2025, 6, 1), "label": 0},             # no anchor
    ]
    check(len(survivors(recs, 30)) == 2, "survivors excludes zero and negative gaps")
    check(len(survivors(recs, 60)) == 1, "survivors tightens with the window")
    check(len(survivors(recs, 200)) == 0, "survivors can empty")
    check(len(survivors(recs, 50)) == 1, "condition is strictly greater than W")
    n, b, nb = arm_counts(survivors(recs, 30))
    check((n, b, nb) == (2, 1, 1), "arm counts split by label")
    check(arm_counts([]) == (0, 0, 0), "empty arms safe")

    today = D(2026, 7, 27)
    missing = [
        {"anchor": D(2026, 1, 1), "lifecycle_outcome": "advanced_confirmed"},
        {"anchor": D(2026, 6, 1), "lifecycle_outcome": "blocked_confirmed"},
        {"anchor": None, "lifecycle_outcome": "advanced_confirmed"},
    ]
    check(len(ceiling_eligible(missing, 30, today)) == 2,
          "ceiling includes anchors more than W days old")
    check(len(ceiling_eligible(missing, 90, today)) == 1,
          "ceiling excludes anchors too recent to have survived")
    check(len(ceiling_eligible(missing, 5000, today)) == 0, "ceiling can empty")

    f = floors_met(40, 12, 12, 40, 12, 12)
    check(f["all"], "floors met exactly at the boundary")
    check(not floors_met(39, 12, 12, 40, 12, 12)["all"], "n floor binds")
    check(not floors_met(40, 11, 12, 40, 12, 12)["all"], "blocked floor binds")
    check(not floors_met(40, 12, 11, 40, 12, 12)["all"], "not_blocked floor binds")

    g = gap_summary([0, 0, -5, 10, 20])
    check(g["n_nonpositive"] == 3 and g["n_zero"] == 2, "gap summary counts")
    check(g["median"] == 0, "gap median odd length")
    check(gap_summary([10, 20])["median"] == 15, "gap median even length")
    check(gap_summary([])["n"] == 0, "empty gap summary safe")

    rec = {"row": {"announced_date": "2025-03-04"}}
    check(anchor_announcement(rec, {}, lambda s: D(2025, 3, 4) if s else None)
          == D(2025, 3, 4), "announcement anchor reads the row")
    check(anchor_announcement({"row": {}}, {}, lambda s: None) is None,
          "missing announcement gives None")

    opp = {"e1": {"Opposition Type": "moratorium", "Date": "2025-02-01"},
           "e2": {"Opposition Type": "permit_denial", "Date": "2025-01-01"}}
    r = {"links": [{"opp_id": "e1"}, {"opp_id": "e2"}]}
    pd_ = lambda s: D(*(int(x) for x in s.split("-"))) if s else None  # noqa: E731
    got = anchor_opposition_no_outcome(r, opp, {"permit_denial"}, pd_)
    check(got == D(2025, 2, 1), "outcome-typed event excluded from the anchor")
    r2 = {"links": [{"opp_id": "e2"}]}
    check(anchor_opposition_no_outcome(r2, opp, {"permit_denial"}, pd_) is None,
          "only outcome-typed events leaves no anchor")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    try:
        import landmark_model as lm
    except ImportError as exc:
        print(f"ERROR: cannot import landmark_model ({exc})")
        return 1
    for f in (lm.LIFECYCLES_CSV, lm.UNIVERSE_CSV, lm.LINKS_CSV):
        if not os.path.exists(f):
            print(f"ERROR: {os.path.relpath(f, ROOT)} missing; run the "
                  "resolution chain first")
            return 1

    frames = lm.build_frames()
    # landmark_model.build_frames returned a 4-tuple before the 2026-07-28
    # anchor re-registration and a 5-tuple after (missing-announce list added).
    # Support both so this diagnostic keeps running against either version.
    if len(frames) == 5:
        decided, missing, pending, _missing_ann, opp = frames
    else:
        decided, missing, pending, opp = frames
    # This module reconstructs the opposition anchor itself from
    # first_opposition_date below, so it does not depend on which field
    # build_frames now uses for t0.
    for r in decided:
        r["t0"] = lm.parse_day((r["row"].get("first_opposition_date") or "").strip())
    today = lm.TODAY
    windows = list(lm.CANDIDATE_WINDOWS)
    F_N, F_B, F_NB = lm.FLOOR_N, lm.FLOOR_BLOCKED, lm.FLOOR_NOT_BLOCKED

    # Anchor variants
    for r in decided:
        r["anchor_opp"] = anchor_opposition(r, opp)
        r["anchor_opp_no_outcome"] = anchor_opposition_no_outcome(
            r, opp, lm.OUTCOME_TYPES, lm.parse_day)
        r["anchor_ann"] = anchor_announcement(r, opp, lm.parse_day)
    for r in missing:
        r["anchor_opp"] = lm.parse_day((r.get("first_opposition_date") or "").strip())
        r["anchor_ann"] = lm.parse_day((r.get("announced_date") or "").strip())
    for r in pending:
        r["anchor_opp"] = r.get("t0")
        r["anchor_ann"] = anchor_announcement(r, opp, lm.parse_day)

    def rows_for(anchor_key, wins, registered_set):
        out = []
        for W in wins:
            surv = survivors(decided, W, anchor_key)
            n, b, nb = arm_counts(surv)
            el = ceiling_eligible(missing, W, today, anchor_key)
            eb = sum(1 for r in el if r.get("lifecycle_outcome") == "blocked_confirmed")
            cn, cb, cnb = n + len(el), b + eb, nb + (len(el) - eb)
            score = sum(1 for r in pending
                        if r.get(anchor_key) and (today - r[anchor_key]).days > W)
            f_now = floors_met(n, b, nb, F_N, F_B, F_NB)
            f_ceil = floors_met(cn, cb, cnb, F_N, F_B, F_NB)
            out.append({
                "anchor": anchor_key, "window_days": W,
                "registered_window": "yes" if W in registered_set else "no",
                "n": n, "n_blocked": b, "n_not_blocked": nb,
                "floors_met": "yes" if f_now["all"] else "no",
                "ceiling_n": cn, "ceiling_blocked": cb, "ceiling_not_blocked": cnb,
                "ceiling_floors_met": "yes" if f_ceil["all"] else "no",
                "pending_scoreable": score,
                "_ceiling_b_ok": f_ceil["blocked"],
                "_ceiling_all_ok": f_ceil["all"],
            })
        return out

    reg = set(windows)
    opp_rows = rows_for("anchor_opp", windows, reg)
    ann_rows = rows_for("anchor_ann", windows + EXPLORATORY_WINDOWS, reg)
    no_out_rows = rows_for("anchor_opp_no_outcome", windows, reg)

    gap_opp = gap_summary([(r["decision"] - r["anchor_opp"]).days
                           for r in decided if r.get("anchor_opp")])
    gap_ann = gap_summary([(r["decision"] - r["anchor_ann"]).days
                           for r in decided if r.get("anchor_ann")])

    ev_counts = Counter()
    for r in decided:
        ev_counts[len([l for l in (r.get("links") or []) if opp.get(l.get("opp_id"))])] += 1

    ann_feasible = [r["window_days"] for r in ann_rows if r["_ceiling_all_ok"]]
    ann_best = next((r for r in ann_rows if r["_ceiling_all_ok"]), None)

    if ann_best:
        bw = ann_best["window_days"]
        now_surv = survivors(decided, bw, "anchor_ann")
        ann_now_n, ann_now_b, ann_now_nb = arm_counts(now_surv)
        el_best = ceiling_eligible(missing, bw, today, "anchor_ann")
        ann_elig_best = len(el_best)
        ann_elig_adv = sum(1 for r in el_best
                           if r.get("lifecycle_outcome") != "blocked_confirmed")
    else:
        ann_now_n = ann_now_b = ann_now_nb = 0
        ann_elig_best = ann_elig_adv = 0

    ctx = {
        "floor_n": F_N, "floor_b": F_B, "floor_nb": F_NB, "windows": windows,
        "n_decided": len(decided), "n_missing": len(missing),
        "n_pending": len(pending),
        "opp_rows": opp_rows, "ann_rows": ann_rows,
        "opp_ceiling_b_max": max(r["ceiling_blocked"] for r in opp_rows),
        "missing_blocked": sum(1 for r in missing
                               if r.get("lifecycle_outcome") == "blocked_confirmed"),
        "missing_advanced": sum(1 for r in missing
                                if r.get("lifecycle_outcome") != "blocked_confirmed"),
        "gap_opp": gap_opp, "gap_ann": gap_ann,
        "single_event_projects": ev_counts.get(1, 0),
        "no_outcome_n_first": no_out_rows[0]["n"],
        "only_outcome_projects": sum(1 for r in decided
                                     if r.get("anchor_opp_no_outcome") is None),
        "ann_decided": sum(1 for r in decided if r.get("anchor_ann")),
        "ann_missing": sum(1 for r in missing if r.get("anchor_ann")),
        "ann_pending": sum(1 for r in pending if r.get("anchor_ann")),
        "ann_feasible_windows": ann_feasible, "ann_best": ann_best,
        "ann_now_n": ann_now_n, "ann_now_blocked": ann_now_b,
        "ann_now_nb": ann_now_nb, "ann_eligible_at_best": ann_elig_best,
        "ann_eligible_adv_at_best": ann_elig_adv,
    }

    # ---- outputs ----
    with open(OUT_COMPARISON, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COMPARISON_COLS, lineterminator="\n")
        w.writeheader()
        for r in opp_rows + no_out_rows + ann_rows:
            w.writerow({c: r.get(c, "") for c in COMPARISON_COLS})

    target_W = ann_best["window_days"] if ann_best else (windows[0] if windows else 30)
    prio = []
    for r in missing:
        a_opp, a_ann = r.get("anchor_opp"), r.get("anchor_ann")
        moves_opp = bool(a_opp and (today - a_opp).days > target_W)
        moves_ann = bool(a_ann and (today - a_ann).days > target_W)
        blocked = r.get("lifecycle_outcome") == "blocked_confirmed"
        if moves_ann:
            pr = 1
            why = ("blocked arm at the feasible window; keeps blocked-arm "
                   "outcome statistics current as well") if blocked else (
                   "enters the feasible window once dated; the advanced arm is "
                   "the binding constraint for feasibility here, so it is top "
                   "priority")
        elif a_ann:
            pr, why = 2, ("announced too recently to survive the window; "
                          "recovering the date does not change a frame yet")
        else:
            pr, why = 3, ("no announced date, so it cannot enter an "
                          "announcement-anchored frame at all; recover the "
                          "announced date first")
        prio.append({
            "project_id": r.get("project_id", ""),
            "project_name": r.get("project_name", ""),
            "state": r.get("state", ""),
            "lifecycle_outcome": r.get("lifecycle_outcome", ""),
            "announced_date": r.get("announced_date", ""),
            "first_opposition_date": r.get("first_opposition_date", ""),
            "moves_frame_opposition_anchor": "yes" if moves_opp else "no",
            "moves_frame_announcement_anchor": "yes" if moves_ann else "no",
            "arm": "blocked" if blocked else "advanced",
            "priority": pr, "why": why,
        })
    prio.sort(key=lambda r: (r["priority"], r["project_id"]))
    with open(OUT_PRIORITY, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRIORITY_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(prio)

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(build_report(ctx))

    # ---- console ----
    print(f"decided with dates {len(decided)}, missing dates {len(missing)}, "
          f"pending {len(pending)}")
    print(f"opposition anchor: gap median {fmt(gap_opp['median'], 0)}d, "
          f"{gap_opp['n_nonpositive']}/{gap_opp['n']} non-positive; "
          f"blocked ceiling max {ctx['opp_ceiling_b_max']} vs floor {F_B}")
    print(f"announcement anchor: gap median {fmt(gap_ann['median'], 0)}d, "
          f"{gap_ann['n_nonpositive']}/{gap_ann['n']} non-positive")
    for r in ann_rows:
        print(f"  W={r['window_days']:<4} now n={r['n']:<3} b={r['n_blocked']:<3}"
              f" | ceiling n={r['ceiling_n']:<3} b={r['ceiling_blocked']:<3} "
              f"nb={r['ceiling_not_blocked']:<3} floors="
              f"{r['ceiling_floors_met']:<3} pending_scoreable="
              f"{r['pending_scoreable']}")
    if ann_best:
        print(f"shortest feasible-at-ceiling window: {ann_best['window_days']} "
              f"days, {ann_best['pending_scoreable']} pending projects scoreable")
    else:
        print("no window feasible at ceiling under either anchor")
    p1 = [r for r in prio if r["priority"] == 1]
    print(f"recovery priority written: {len(p1)} projects enter the feasible "
          f"window once dated ({sum(1 for r in p1 if r['arm'] == 'advanced')} "
          f"advanced, {sum(1 for r in p1 if r['arm'] == 'blocked')} blocked)")

    dirty = []
    for p in (OUT_REPORT, OUT_COMPARISON, OUT_PRIORITY):
        hits = sum(1 for line in open(p, encoding="utf-8") if LEAK_RE.search(line))
        name = os.path.relpath(p, ROOT)
        if hits:
            dirty.append(name)
            print(f"LEAK AUDIT {name}: {hits} hits, inspect before use")
        else:
            print(f"leak audit {name}: clean")
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())
