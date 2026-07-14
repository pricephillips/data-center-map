"""
outcome_model.py — Phase 3: first outcome model.

Regularized classification of terminal outcome (blocked_confirmed vs
advanced_confirmed) on decided + opposed projects, with honest cross-
validation and predictive (NOT causal) feature importance.

Additive only. Reads Phase 1/2 outputs, writes three NEW files:

  data/outcome_model_report.md     internal report: calibrated ranges,
                                   fold-level metrics, importance, limitations
  data/outcome_model_features.csv  the exact per-project feature matrix used
                                   (full audit trail for every number)
  data/outcome_model_metrics.json  machine-readable metrics for future
                                   calibration tracking (Phase 5 gating)

Defensibility rules honored:
  - Training labels are decided cases ONLY (terminal dispositions); pending
    and mixed are excluded, consistent with the platform's decided-case rule.
  - All metrics are reported as ranges across repeated CV folds, never as
    single unexplained point estimates.
  - Feature importance is labeled predictive association; the report states
    plainly that nothing here supports causal or cost claims.
  - Sample size (n ~= 80) is stated up front; the model is a retrospective
    association analysis, not a deployable predictor. NOT client-facing.
  - This module is intentionally NOT wired into the CI pipeline. Promotion
    to automated retraining is Phase 5 and requires the calibration gate.

Run from repo root:  python3 outcome_model.py
Depends on project_resolution.py + control_group.py outputs.
Requires scikit-learn (pip install scikit-learn).
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import date

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

LIFECYCLES_CSV = P("data", "project_lifecycles.csv")
UNIVERSE_CSV = P("data", "baseline_universe.csv")
LINKS_CSV = P("data", "project_links.csv")
OPPOSITION_CSV = P("master_opposition.csv")

OUT_REPORT = P("data", "outcome_model_report.md")
OUT_FEATURES = P("data", "outcome_model_features.csv")
OUT_METRICS = P("data", "outcome_model_metrics.json")

RANDOM_STATE = 20260710
N_REPEATS = 10
N_FOLDS = 5

MECHANISMS = ["moratorium", "zoning_restriction", "lawsuit", "public_comment",
              "legislation", "ordinance", "regulatory_action", "petition"]

HYPERSCALERS = {"google", "meta", "microsoft", "amazon", "aws", "oracle",
                "openai", "xai", "apple"}


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def parse_float(v) -> float | None:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def build_features():
    life = load_csv(LIFECYCLES_CSV)
    uni = {r["universe_id"]: r for r in load_csv(UNIVERSE_CSV)}
    links = load_csv(LINKS_CSV)
    opp_by_id = {}
    for r in load_csv(OPPOSITION_CSV):
        # re-derive event id the same way project_resolution does
        import hashlib
        key = "|".join([(r.get("Incident") or "").strip(), (r.get("Date") or "").strip(),
                        (r.get("State") or "").strip(), (r.get("Source URL") or "").strip()])
        opp_by_id["opp_" + hashlib.sha1(key.encode()).hexdigest()[:12]] = r

    links_by_project: dict[str, list[dict]] = {}
    for l in links:
        links_by_project.setdefault(l["project_id"], []).append(l)

    rows = []
    for r in life:
        if r["decided"] != "yes" or int(r["n_opposition_events"] or 0) == 0:
            continue
        pid = r["project_id"]
        u = uni.get(pid, {})
        evs = links_by_project.get(pid, [])
        types = " ; ".join(l.get("opp_type", "") for l in evs).lower()
        petition_sigs = 0
        companies = set()
        for l in evs:
            raw = opp_by_id.get(l["opp_id"], {})
            s = parse_float(raw.get("Petition Signatures"))
            petition_sigs = max(petition_sigs, int(s) if s else 0)
            for c in re.split(r"[;,/]", (raw.get("Company", "") + ";" + raw.get("Hyperscaler", "")).lower()):
                companies.add(c.strip())
        feat = {
            "project_id": pid,
            "project_name": r["project_name"],
            "outcome": r["lifecycle_outcome"],          # full tier (3 values possible)
            # Binary target: blocked vs not-blocked. restricted_conditional is
            # a terminal ADVANCE, so it sits on the not-blocked (advanced) side
            # here; multi-class treatment of the conditional tier is deferred
            # until it has enough cases to model.
            "label_blocked": 1 if r["lifecycle_outcome"] == "blocked_confirmed" else 0,
            "n_opposition_events": int(r["n_opposition_events"]),
            "n_opposition_groups": int(r["n_opposition_groups"] or 0),
            "has_lawsuit": 1 if r["has_lawsuit"] == "yes" else 0,
            "opposition_span_days": parse_float(r["opposition_span_days"]) or 0.0,
            "county_margin_2024": parse_float(u.get("county_margin_2024")),
            "log10_capacity_mw": (math.log10(c) if (c := parse_float(r["capacity_mw"])) and c > 0 else None),
            "capacity_missing": 0 if parse_float(r["capacity_mw"]) else 1,
            "petition_signatures_log1p": math.log1p(petition_sigs),
            "hyperscaler_involved": 1 if companies & HYPERSCALERS else 0,
        }
        for m in MECHANISMS:
            feat[f"mech_{m}"] = 1 if m in types else 0
        rows.append(feat)
    return rows


def main() -> int:
    for f in (LIFECYCLES_CSV, UNIVERSE_CSV, LINKS_CSV):
        if not os.path.exists(f):
            print(f"ERROR: {os.path.relpath(f, ROOT)} missing — run the resolution/control chain first")
            return 1
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import RepeatedStratifiedKFold
        from sklearn.pipeline import Pipeline
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score, brier_score_loss
        from sklearn.inspection import permutation_importance
    except ImportError:
        print("ERROR: scikit-learn not installed (pip install scikit-learn). "
              "This module is not part of the CI pipeline by design.")
        return 1

    rows = build_features()
    feature_cols = [c for c in rows[0] if c not in
                    ("project_id", "project_name", "outcome", "label_blocked")]
    X = np.array([[np.nan if r[c] is None else float(r[c]) for c in feature_cols]
                  for r in rows])
    y = np.array([r["label_blocked"] for r in rows])
    n, n_blocked = len(y), int(y.sum())

    # audit trail: exact matrix used
    with open(OUT_FEATURES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    model = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(C=0.5, max_iter=2000,
                                   class_weight="balanced")),
    ])

    cv = RepeatedStratifiedKFold(n_splits=N_FOLDS, n_repeats=N_REPEATS,
                                 random_state=RANDOM_STATE)
    aucs, briers = [], []
    oof_pred = np.full(len(y), np.nan)  # from first repeat only, for calibration
    importances = np.zeros(len(feature_cols))
    coefs = np.zeros(len(feature_cols))
    n_imp = 0
    for rep_fold, (tr, te) in enumerate(cv.split(X, y)):
        model.fit(X[tr], y[tr])
        p = model.predict_proba(X[te])[:, 1]
        if len(set(y[te])) == 2:
            aucs.append(roc_auc_score(y[te], p))
        briers.append(brier_score_loss(y[te], p))
        if rep_fold < N_FOLDS:
            oof_pred[te] = p
        pi = permutation_importance(model, X[te], y[te], scoring="roc_auc",
                                    n_repeats=5, random_state=RANDOM_STATE)
        importances += pi.importances_mean
        coefs += model.named_steps["clf"].coef_[0]
        n_imp += 1
    importances /= n_imp
    coefs /= n_imp

    auc_lo, auc_med, auc_hi = np.percentile(aucs, [10, 50, 90])
    brier_lo, brier_med, brier_hi = np.percentile(briers, [10, 50, 90])
    base_rate = n_blocked / n
    base_brier = base_rate * (1 - base_rate)

    # coarse calibration on out-of-fold predictions (first repeat)
    bins = [(0.0, 1/3), (1/3, 2/3), (2/3, 1.0001)]
    calib = []
    for lo, hi in bins:
        m = (oof_pred >= lo) & (oof_pred < hi)
        if m.sum():
            calib.append((f"{lo:.2f}-{min(hi,1.0):.2f}", int(m.sum()),
                          float(oof_pred[m].mean()), float(y[m].mean())))

    # export per-project out-of-fold predictions so the calibration gate
    # (calibration_gate.py) can assess predicted-vs-observed without refitting.
    OUT_PRED = P("data", "outcome_model_predictions.csv")
    with open(OUT_PRED, "w", newline="", encoding="utf-8") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["project_id", "project_name", "label_blocked", "oof_pred_blocked"])
        for r, pred in zip(rows, oof_pred):
            if not np.isnan(pred):
                wtr.writerow([r["project_id"], r["project_name"],
                              r["label_blocked"], f"{pred:.6f}"])

    imp_ranked = sorted(zip(feature_cols, importances, coefs),
                        key=lambda t: -abs(t[1]))

    lines = []
    w = lines.append
    w("# Outcome Model — First Iteration (Phase 3)")
    w("")
    w(f"Generated {date.today().isoformat()} by `outcome_model.py`. All figures "
      "re-derived from current CSVs at generation time; the exact feature "
      "matrix is in `outcome_model_features.csv`.")
    w("")
    w("**Internal diagnostic only — NOT client-facing.** This is a "
      "retrospective association analysis on a small, selection-affected "
      "sample. Feature importance is predictive association, not causation. "
      "Nothing here supports effect-size or cost claims.")
    w("")
    w("## Sample")
    w("")
    n_conditional = sum(1 for r in rows if r["outcome"] == "restricted_conditional")
    adv_desc = f"{n - n_blocked} advanced"
    if n_conditional:
        adv_desc += f" (of which {n_conditional} `restricted_conditional`)"
    w(f"- Decided + opposed projects: **{n}** "
      f"({adv_desc}, {n_blocked} `blocked_confirmed`; "
      f"base rate of blocked = {base_rate:.2f})")
    w(f"- Labels are terminal dispositions only, per the decided-case rule.")
    w(f"- Features with missingness: county margin missing for "
      f"{int(np.isnan(X[:, feature_cols.index('county_margin_2024')]).sum())} projects; "
      f"capacity known for only {int((1 - X[:, feature_cols.index('capacity_missing')]).sum())} "
      "(median-imputed, with a missingness indicator retained as a feature).")
    w("")
    w("## Model and validation")
    w("")
    w(f"L2-regularized logistic regression (C=0.5, class-weighted), median "
      f"imputation, standardized inputs. {N_FOLDS}-fold stratified CV repeated "
      f"{N_REPEATS}× ({len(aucs)} evaluated folds).")
    w("")
    w(f"- ROC-AUC across folds: **{auc_med:.2f}** (10th–90th pct: "
      f"{auc_lo:.2f}–{auc_hi:.2f}). Chance = 0.50.")
    w(f"- Brier score across folds: **{brier_med:.3f}** (10th–90th pct: "
      f"{brier_lo:.3f}–{brier_hi:.3f}). Predicting the base rate for everyone "
      f"scores {base_brier:.3f}; lower is better.")
    w("")
    w("The wide fold-to-fold range is the honest picture at n="
      f"{n}: each test fold holds ~{n // N_FOLDS} projects and ~"
      f"{max(1, round(n_blocked / N_FOLDS))} blocked cases.")
    w("")
    w("## Coarse calibration (out-of-fold, first repeat)")
    w("")
    for label, cnt, pred, obs in calib:
        w(f"- Predicted {label}: {cnt} projects; mean predicted {pred:.2f}, "
          f"observed blocked share {obs:.2f}")
    w("")
    w("## Predictive associations (permutation importance, AUC drop, "
      "averaged over CV test folds)")
    w("")
    w("AUC drop when permuted; sign = direction of the fold-averaged "
      "standardized coefficient (+ associates with blocked_confirmed, "
      "- with advanced_confirmed).")
    w("")
    for name, imp, coef in imp_ranked[:8]:
        direction = "toward blocked" if coef > 0 else "toward advanced"
        w(f"- `{name}`: {imp:+.3f} (coef {coef:+.2f}, {direction})")
    w("")
    w("Read these as \"which features the model used,\" not \"what causes "
      "blocks.\" In particular, opposition intensity features "
      "(events, span, groups) are partially contemporaneous with the outcome "
      "process — they describe how contested fights unfolded, and are not "
      "ex-ante predictors for a new project.")
    w("")
    w("## Limitations (binding)")
    w("")
    w(f"- n={n} with {n_blocked} blocked cases; estimates are unstable by "
      "nature. Growing the seed via link triage and date recovery is the "
      "highest-leverage improvement.")
    w("- Sample is opposed projects only; this model says nothing about "
      "unopposed baselines (the matched-control work addresses that "
      "separately).")
    w("- No delay/survival modeling yet: only "
      "projects with verified decision dates can enter that model "
      "(see date-recovery worklist).")
    w("- Not wired into CI. Automated retraining requires the Phase 5 "
      "calibration gate; until then this is run manually.")
    w("")

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    metrics = {
        "generated": date.today().isoformat(),
        "n": n, "n_blocked": n_blocked, "base_rate": round(base_rate, 4),
        "cv": {"folds": N_FOLDS, "repeats": N_REPEATS},
        "roc_auc": {"p10": round(auc_lo, 4), "p50": round(auc_med, 4), "p90": round(auc_hi, 4)},
        "brier": {"p10": round(brier_lo, 4), "p50": round(brier_med, 4),
                  "p90": round(brier_hi, 4), "base_rate_brier": round(base_brier, 4)},
        "top_features": [{"name": k, "auc_drop": round(float(v), 4),
                          "mean_std_coef": round(float(c), 4)}
                         for k, v, c in imp_ranked[:8]],
        "feature_columns": feature_cols,
        "random_state": RANDOM_STATE,
    }
    with open(OUT_METRICS, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    print(f"n={n} ({n_blocked} blocked) | AUC {auc_med:.2f} "
          f"[{auc_lo:.2f}-{auc_hi:.2f}] | Brier {brier_med:.3f} "
          f"(base {base_brier:.3f})")
    print("top associations:", ", ".join(f"{k} {v:+.3f}/{c:+.2f}" for k, v, c in imp_ranked[:5]))
    print(f"wrote {os.path.relpath(OUT_REPORT, ROOT)}, features, metrics")

    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = [f"{f}:{i}" for f in (OUT_REPORT, OUT_FEATURES)
            for i, l in enumerate(open(f, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:10])
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
