"""
landmark_model.py — Landmark retrain of the project outcome model.

Purpose. The Phase 3 outcome model (outcome_model.py) computes features over
each project's FULL opposition history, so a decided project's feature vector
contains information that only existed after its decision. That model cannot
legitimately score a pending project. This module implements the landmark
formulation that can:

  t0 = first opposition date. For a fixed window W, features are computed
  ONLY from opposition events dated within [t0, t0 + W], the training frame
  is restricted to decided projects that were still undecided at t0 + W
  (survivor conditioning), and the model answers the only question a pending
  project can be asked: given a project still undecided W days after first
  opposition, what is P(blocked)?

Pre-registered window selection (LOCKED 2026-07-23, before the data existed
to fit it; do not modify without recording a new registration date):
  Candidate windows: W in {30, 60, 90, 120, 180} days.
  1. Feasibility floors, per window: n >= 40 AND n_blocked >= 12 AND
     n_not_blocked >= 12. Windows below floor are INFEASIBLE and are never
     fit beyond the counting stage.
  2. Among feasible windows: highest median CV AUC, admissible only if
     median Brier < base-rate Brier for that frame.
  3. Windows within 0.01 median AUC of the best are tied; the SHORTEST tied
     window is selected (earliest scoreability for pending projects).
  4. The selected model is NOT client-facing and NOT auto-promoted; promotion
     requires the Phase 5 calibration gate, same as every other model.

Frame rules (all recorded, all enforced in code):
  - Training labels are decided cases only (terminal dispositions).
  - decision_date is required and comes only from data/project_decision_dates.csv
    (sourced, day precision). Decided projects without a verified decision
    date are EXCLUDED from every landmark frame and listed in the
    decision-date worklist. No decision date is ever inferred.
  - Outcome-typed events (project_withdrawal, permit_denial) never enter
    features at any window: they encode the label.
  - Event dates at month precision are floored to the 1st (pipeline
    convention); window membership uses the floored date. Undated events are
    excluded from window features and counted in the coverage stats.
  - Petition signature counts and full-history span features are excluded:
    their values are as-of-scrape, not as-of-window.

Outputs (all NEW files; additive):
  data/landmark_feasibility.csv     per-window frame counts + gate status
  data/landmark_model_report.md     gate status, criterion, metrics if fit
  data/decision_date_worklist.csv   decided+opposed projects missing a
                                    verified decision date, blocked arm first
  and, only when a window passes the gate:
  data/landmark_model_features.csv  exact per-project matrix at selected W
  data/landmark_model_metrics.json  machine-readable metrics (Phase 5 input)
  data/landmark_model_predictions.csv  OOF predictions + pending-project
                                    scores with risk bands and scoreability
                                    status (scoreable once >= W days have
                                    elapsed since t0; provisional before)

Not wired into CI. Run from repo root:  python3 landmark_model.py
Depends on project_resolution.py outputs. Requires scikit-learn.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import date, timedelta

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

LIFECYCLES_CSV = P("data", "project_lifecycles.csv")
UNIVERSE_CSV = P("data", "baseline_universe.csv")
LINKS_CSV = P("data", "project_links.csv")
OPPOSITION_CSV = P("master_opposition.csv")
ATLAS_CSV = P("atlas.csv")

OUT_FEAS = P("data", "landmark_feasibility.csv")
OUT_REPORT = P("data", "landmark_model_report.md")
OUT_WORKLIST = P("data", "decision_date_worklist.csv")
OUT_FEATURES = P("data", "landmark_model_features.csv")
OUT_METRICS = P("data", "landmark_model_metrics.json")
OUT_PREDICTIONS = P("data", "landmark_model_predictions.csv")

RANDOM_STATE = 20260723
N_REPEATS = 10
N_FOLDS = 5

CANDIDATE_WINDOWS = [30, 60, 90, 120, 180]
FLOOR_N = 40
FLOOR_BLOCKED = 12
FLOOR_NOT_BLOCKED = 12
AUC_TIE_TOL = 0.01

OUTCOME_TYPES = {"project_withdrawal", "permit_denial"}
MECHANISMS = ["moratorium", "zoning_restriction", "lawsuit", "public_comment",
              "legislation", "ordinance", "regulatory_action", "petition"]
HYPERSCALERS = {"google", "meta", "microsoft", "amazon", "aws", "oracle",
                "openai", "xai", "apple"}

RISK_BANDS = [(0.0, 0.25, "lower"), (0.25, 0.50, "moderate"),
              (0.50, 0.75, "elevated"), (0.75, 1.01, "higher")]

TODAY = date.today()


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def parse_float(v):
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def parse_day(v):
    v = (v or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{1,2})$", v)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    return None


def band(p):
    for lo, hi, name in RISK_BANDS:
        if lo <= p < hi:
            return name
    return "higher"


def event_index():
    """opp_id -> raw master row, re-derived the way project_resolution does."""
    out = {}
    for r in load_csv(OPPOSITION_CSV):
        key = "|".join([(r.get("Incident") or "").strip(),
                        (r.get("Date") or "").strip(),
                        (r.get("State") or "").strip(),
                        (r.get("Source URL") or "").strip()])
        out["opp_" + hashlib.sha1(key.encode()).hexdigest()[:12]] = r
    return out


def static_features(r, uni_row, dc_density, state_events):
    """Features fixed at (or strictly before) announcement; no window logic."""
    cap = parse_float(r.get("capacity_mw"))
    ann = r.get("announced_date") or ""
    fo = r.get("first_opposition_date") or ""
    dtf = None
    if ann and fo:
        dtf = max((date.fromisoformat(fo) - date.fromisoformat(ann)).days, 0)
    return {
        "county_margin_2024": parse_float(uni_row.get("county_margin_2024")),
        "log10_capacity_mw": (math.log10(cap) if cap and cap > 0 else None),
        "capacity_missing": 0 if cap else 1,
        "hyperscaler_missing_placeholder": None,  # replaced below
        "log1p_existing_dc_in_county": math.log1p(
            dc_density.get((r.get("state", ""),
                            (r.get("county") or "").strip().lower()), 0)),
        "days_to_first_opposition": dtf,
        "log1p_prior_state_opposition": math.log1p(
            sum(1 for s, d in state_events
                if s == r.get("state", "") and ann and d < ann)),
    }


def window_features(links, opp_by_id, t0, w_end):
    """Dynamic features from events in [t0, t0+W], outcome types excluded."""
    n_in, undated, groups, lawsuit = 0, 0, set(), 0
    mech = {m: 0 for m in MECHANISMS}
    companies = set()
    for l in links:
        typ = (l.get("opp_type") or "").strip().lower()
        raw = opp_by_id.get(l["opp_id"], {})
        for c in re.split(r"[;,/]", (raw.get("Company", "") + ";" +
                                     raw.get("Hyperscaler", "")).lower()):
            companies.add(c.strip())
        if typ in OUTCOME_TYPES:
            continue
        d = parse_day(l.get("opp_date"))
        if d is None:
            undated += 1
            continue
        if not (t0 <= d <= w_end):
            continue
        n_in += 1
        g = (raw.get("Opposition Groups") or "").strip()
        if g:
            groups.add(g.lower())
        if typ == "lawsuit":
            lawsuit = 1
        for m in MECHANISMS:
            if m in typ:
                mech[m] = 1
    feat = {
        "n_events_in_window": n_in,
        "n_groups_in_window": len(groups),
        "lawsuit_in_window": lawsuit,
        "n_undated_events_excluded": undated,
        "hyperscaler_involved": 1 if companies & HYPERSCALERS else 0,
    }
    for m in MECHANISMS:
        feat[f"mech_{m}_in_window"] = mech[m]
    return feat


META_COLS = ("project_id", "project_name", "outcome", "label_blocked",
             "n_undated_events_excluded", "scoreability")


def build_frames():
    life = load_csv(LIFECYCLES_CSV)
    uni = {r["universe_id"]: r for r in load_csv(UNIVERSE_CSV)}
    links_by_project = {}
    for l in load_csv(LINKS_CSV):
        links_by_project.setdefault(l["project_id"], []).append(l)
    opp_by_id = event_index()

    atlas = load_csv(ATLAS_CSV)
    dc_density = Counter((a.get("state", ""), (a.get("county") or "").strip().lower())
                         for a in atlas)
    state_events = []
    for m in load_csv(OPPOSITION_CSV):
        d = (m.get("Date") or "").strip()
        s = (m.get("State") or "").strip()
        if s and re.match(r"^\d{4}-\d{2}", d):
            state_events.append((s, d[:10]))

    decided, missing_dd, pending = [], [], []
    for r in life:
        if int(r.get("n_opposition_events") or 0) == 0:
            continue
        t0 = parse_day(r.get("first_opposition_date"))
        if t0 is None:
            continue
        rec = {
            "row": r, "t0": t0,
            "links": links_by_project.get(r["project_id"], []),
            "static": None,
        }
        rec["static"] = static_features(r, uni.get(r["project_id"], {}),
                                        dc_density, state_events)
        if r["decided"] == "yes":
            dd = parse_day(r.get("decision_date"))
            if dd is None or not re.match(r"^\d{4}-\d{2}-\d{2}$",
                                          (r.get("decision_date") or "")):
                missing_dd.append(r)
                continue
            rec["decision"] = dd
            rec["label"] = 1 if r["lifecycle_outcome"] == "blocked_confirmed" else 0
            decided.append(rec)
        else:
            pending.append(rec)
    return decided, missing_dd, pending, opp_by_id


def assemble_matrix(records, opp_by_id, W, scoreability=None):
    rows = []
    for rec in records:
        r = rec["row"]
        w_end = rec["t0"] + timedelta(days=W)
        feat = {"project_id": r["project_id"], "project_name": r["project_name"],
                "outcome": r["lifecycle_outcome"],
                "label_blocked": rec.get("label", "")}
        feat.update({k: v for k, v in rec["static"].items()
                     if k != "hyperscaler_missing_placeholder"})
        feat.update(window_features(rec["links"], opp_by_id, rec["t0"], w_end))
        feat["scoreability"] = (scoreability(rec) if scoreability else "training")
        rows.append(feat)
    return rows


def write_worklist(missing_dd):
    missing_dd = sorted(missing_dd, key=lambda r: (
        0 if r["lifecycle_outcome"] == "blocked_confirmed" else 1,
        -int(r.get("n_opposition_events") or 0)))
    with open(OUT_WORKLIST, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["project_id", "project_name", "state", "county", "phase",
                    "lifecycle_outcome", "n_opposition_events",
                    "first_opposition_date", "what_to_recover"])
        for r in missing_dd:
            w.writerow([r["project_id"], r["project_name"], r.get("state", ""),
                        r.get("county", ""), r.get("phase", ""),
                        r["lifecycle_outcome"], r.get("n_opposition_events", ""),
                        r.get("first_opposition_date", ""),
                        "terminal disposition date (vote, withdrawal, or "
                        "signature), day precision, primary record preferred; "
                        "append to data/project_decision_dates.csv with source_url"])
    return missing_dd


def main() -> int:
    for f in (LIFECYCLES_CSV, UNIVERSE_CSV, LINKS_CSV):
        if not os.path.exists(f):
            print(f"ERROR: {os.path.relpath(f, ROOT)} missing — run the "
                  "resolution chain first")
            return 1
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import brier_score_loss, roc_auc_score
        from sklearn.model_selection import RepeatedStratifiedKFold
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("ERROR: scikit-learn not installed (pip install scikit-learn). "
              "This module is not part of the CI pipeline by design.")
        return 1

    decided, missing_dd, pending, opp_by_id = build_frames()
    missing_dd = write_worklist(missing_dd)

    # ---- per-window frames + gate ----
    feas_rows, frames = [], {}
    for W in CANDIDATE_WINDOWS:
        surv = [rec for rec in decided
                if (rec["decision"] - rec["t0"]).days > W]
        nb = sum(rec["label"] for rec in surv)
        na = len(surv) - nb
        ok = (len(surv) >= FLOOR_N and nb >= FLOOR_BLOCKED
              and na >= FLOOR_NOT_BLOCKED)
        feas_rows.append({"window_days": W, "n": len(surv), "n_blocked": nb,
                          "n_not_blocked": na,
                          "gate": "FEASIBLE" if ok else "INFEASIBLE",
                          "floors": f"n>={FLOOR_N}, blocked>={FLOOR_BLOCKED}, "
                                    f"not_blocked>={FLOOR_NOT_BLOCKED}"})
        frames[W] = surv
    with open(OUT_FEAS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(feas_rows[0].keys()),
                           lineterminator="\n")
        w.writeheader()
        w.writerows(feas_rows)

    feasible = [fr for fr in feas_rows if fr["gate"] == "FEASIBLE"]

    rep = []
    w = rep.append
    w("# Landmark Outcome Model")
    w("")
    w(f"Generated {TODAY.isoformat()}. Landmark t0 = first opposition date; "
      f"features from events in [t0, t0+W] only; training frame conditioned "
      f"on being undecided at t0+W. Selection criterion pre-registered "
      f"2026-07-23 (see module docstring); candidate windows "
      f"{CANDIDATE_WINDOWS}, floors n>={FLOOR_N}, blocked>={FLOOR_BLOCKED}, "
      f"not_blocked>={FLOOR_NOT_BLOCKED}.")
    w("")
    w("## Frame coverage")
    w("")
    w(f"- Decided + opposed projects with a dated first opposition: "
      f"{len(decided) + len(missing_dd)}")
    w(f"- With a verified, day-precision decision date (eligible for any "
      f"landmark frame): {len(decided)}")
    w(f"- Missing a verified decision date (excluded; see "
      f"decision_date_worklist.csv): {len(missing_dd)}, of which "
      f"{sum(1 for r in missing_dd if r['lifecycle_outcome'] == 'blocked_confirmed')} "
      f"blocked")
    w(f"- Pending projects with a dated first opposition (the scoring "
      f"population once a window is selected): {len(pending)}")
    w("")
    w("## Per-window gate status")
    w("")
    w("| W (days) | n | blocked | not blocked | gate |")
    w("|---|---|---|---|---|")
    for fr in feas_rows:
        w(f"| {fr['window_days']} | {fr['n']} | {fr['n_blocked']} | "
          f"{fr['n_not_blocked']} | {fr['gate']} |")
    w("")

    if not feasible:
        w("## Result: GATE CLOSED")
        w("")
        w("No candidate window meets the pre-registered floors. The model "
          "was not fit. The binding constraint is verified decision-date "
          "coverage, not modeling: every decided project lacking a "
          "day-precision decision date in data/project_decision_dates.csv "
          "is excluded from every frame. The worklist "
          "(data/decision_date_worklist.csv) is ordered blocked arm first, "
          "then by opposition event count. Recovering decision dates is the "
          "gate-opening path; the selection criterion above stays locked and "
          "will be applied unchanged when the floors are met.")
        w("")
        w("A second constraint will bind after coverage improves: survivor "
          "counts depend on the gap between first opposition and decision. "
          "In the current dated subset the median gap is near zero because "
          "many projects' only dated opposition event is the "
          "decision-adjacent record itself. Denser event dating (not just "
          "decision dating) raises survivor counts at every window. This is "
          "the same structural asymmetry recorded previously: blocked "
          "projects carry verified dates at higher rates, so coverage work "
          "must sample both arms to avoid steering the frame.")
        report_only = True
    else:
        # ---- fit feasible windows, apply pre-registered selection ----
        results = {}
        for fr in feasible:
            W = fr["window_days"]
            rows = assemble_matrix(frames[W], opp_by_id, W)
            cols = [c for c in rows[0] if c not in META_COLS]
            X = np.array([[np.nan if r[c] is None else float(r[c])
                           for c in cols] for r in rows])
            y = np.array([int(r["label_blocked"]) for r in rows])
            model = Pipeline([
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("clf", LogisticRegression(C=0.5, max_iter=2000,
                                           class_weight="balanced")),
            ])
            cv = RepeatedStratifiedKFold(n_splits=N_FOLDS, n_repeats=N_REPEATS,
                                         random_state=RANDOM_STATE)
            aucs, briers = [], []
            oof = np.full(len(y), np.nan)
            coefs = np.zeros(len(cols))
            k = 0
            for i, (tr, te) in enumerate(cv.split(X, y)):
                model.fit(X[tr], y[tr])
                p = model.predict_proba(X[te])[:, 1]
                if len(set(y[te])) == 2:
                    aucs.append(roc_auc_score(y[te], p))
                briers.append(brier_score_loss(y[te], p))
                if i < N_FOLDS:
                    oof[te] = p
                coefs += model.named_steps["clf"].coef_[0]
                k += 1
            coefs /= k
            base = y.mean()
            base_brier = float(np.mean((base - y) ** 2))
            results[W] = {
                "rows": rows, "cols": cols, "X": X, "y": y, "oof": oof,
                "coefs": coefs, "model": model,
                "auc": np.percentile(aucs, [10, 50, 90]).tolist(),
                "brier": np.percentile(briers, [10, 50, 90]).tolist(),
                "base_rate": float(base), "base_brier": base_brier,
            }
        admissible = {W: r for W, r in results.items()
                      if r["brier"][1] < r["base_brier"]}
        if not admissible:
            w("## Result: GATE CLOSED (admissibility)")
            w("")
            w("Feasible frames exist but no window produced median Brier "
              "below the base rate; per the pre-registered criterion no "
              "window is selected and no model is promoted.")
            report_only = True
        else:
            best_auc = max(r["auc"][1] for r in admissible.values())
            tied = sorted(W for W, r in admissible.items()
                          if best_auc - r["auc"][1] <= AUC_TIE_TOL)
            W_sel = tied[0]
            R = results[W_sel]
            report_only = False

            w("## Result: window selected by pre-registered criterion")
            w("")
            w("| W | median AUC [p10-p90] | median Brier | base Brier | admissible |")
            w("|---|---|---|---|---|")
            for W, r in sorted(results.items()):
                a, b = r["auc"], r["brier"]
                w(f"| {W} | {a[1]:.3f} [{a[0]:.3f}-{a[2]:.3f}] | {b[1]:.3f} "
                  f"| {r['base_brier']:.3f} | "
                  f"{'yes' if W in admissible else 'no'} |")
            w("")
            w(f"Selected W = {W_sel} days (shortest within {AUC_TIE_TOL} AUC "
              f"of best). n = {len(R['y'])}, blocked = {int(R['y'].sum())}, "
              f"base rate {R['base_rate']:.3f}.")
            w("")
            w("## Interpretation rules")
            w("")
            w("This is a retrospective predictive association on the "
              "survivor-conditioned frame. Scores are P(blocked | still "
              "undecided W days after first opposition). Nothing here is "
              "causal and nothing here is a cost estimate. Risk bands are "
              "reported instead of bare point estimates. Promotion to any "
              "client-facing surface requires the Phase 5 calibration gate.")

            # audit trail + metrics + predictions
            with open(OUT_FEATURES, "w", newline="", encoding="utf-8") as fh:
                wr = csv.DictWriter(fh, fieldnames=list(R["rows"][0].keys()),
                                    lineterminator="\n")
                wr.writeheader()
                wr.writerows(R["rows"])
            metrics = {
                "generated": TODAY.isoformat(),
                "landmark_window_days": W_sel,
                "criterion_registered": "2026-07-23",
                "n": int(len(R["y"])), "n_blocked": int(R["y"].sum()),
                "base_rate": R["base_rate"],
                "roc_auc": {"p10": R["auc"][0], "p50": R["auc"][1],
                            "p90": R["auc"][2]},
                "brier": {"p10": R["brier"][0], "p50": R["brier"][1],
                          "p90": R["brier"][2],
                          "base_rate_brier": R["base_brier"]},
                "windows_considered": {str(W): {
                    "median_auc": r["auc"][1], "median_brier": r["brier"][1],
                    "n": int(len(r["y"]))} for W, r in results.items()},
                "features": R["cols"],
            }
            with open(OUT_METRICS, "w", encoding="utf-8") as fh:
                json.dump(metrics, fh, indent=2)

            # score pending projects
            R["model"].fit(R["X"], R["y"])

            def scoreable(rec):
                elapsed = (TODAY - rec["t0"]).days
                return ("scoreable" if elapsed >= W_sel
                        else f"provisional_only_{elapsed}d_elapsed")

            pend_rows = assemble_matrix(pending, opp_by_id, W_sel,
                                        scoreability=scoreable)
            Xp = np.array([[np.nan if r[c] is None else float(r[c])
                            for c in R["cols"]] for r in pend_rows]) \
                if pend_rows else np.zeros((0, len(R["cols"])))
            pp = R["model"].predict_proba(Xp)[:, 1] if len(pend_rows) else []
            with open(OUT_PREDICTIONS, "w", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh, lineterminator="\n")
                wr.writerow(["project_id", "project_name", "frame",
                             "scoreability", "p_blocked", "risk_band",
                             "observed_label"])
                for r, p in zip(R["rows"], R["oof"]):
                    wr.writerow([r["project_id"], r["project_name"],
                                 "training_oof", "training",
                                 f"{p:.3f}" if not np.isnan(p) else "",
                                 band(p) if not np.isnan(p) else "",
                                 r["label_blocked"]])
                for r, p in zip(pend_rows, pp):
                    wr.writerow([r["project_id"], r["project_name"], "pending",
                                 r["scoreability"], f"{p:.3f}", band(p), ""])

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rep) + "\n")

    # leak audit on everything written this run
    audit_paths = [OUT_FEAS, OUT_REPORT, OUT_WORKLIST]
    if not report_only:
        audit_paths += [OUT_FEATURES, OUT_METRICS, OUT_PREDICTIONS]
    rx = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.I)
    dirty = [p for p in audit_paths
             if os.path.exists(p) and rx.search(open(p, encoding="utf-8").read())]
    if dirty:
        print("LEAK AUDIT FAILED: " + ", ".join(os.path.basename(p) for p in dirty))
        return 1

    gate_line = ("GATE CLOSED — no window feasible; worklist written"
                 if report_only else
                 f"window selected; see {os.path.relpath(OUT_REPORT, ROOT)}")
    print(f"landmark frames: " +
          ", ".join(f"W={fr['window_days']}:{fr['n']}({fr['n_blocked']}b)"
                    for fr in feas_rows))
    print(f"decision-date worklist: {len(missing_dd)} project(s)")
    print(gate_line)
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
