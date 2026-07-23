"""
county_policy_model.py

Enacted-restriction model on the county aggregate layer. Reads
data/county_aggregate.csv (produced by county_aggregator.py, which QC-gates
all inputs), fits and validates a regularized logistic model, and writes:

  - data/county_policy_metrics.json   (full validation metrics)
  - data/county_policy_scores.csv     (per-county cross-validated score,
                                       map-consumable)
  - data/county_policy_report.md      (auto-generated report; the report is
                                       produced by the model, not by hand)

Outcome: has_enacted_restrictive. A county has at least one enacted
restrictive policy (moratorium or zoning restriction with terminal enacted
status). Chosen because an enacted ordinance is a public record: the label
does not depend on press coverage the way opposition presence does.

Validation stack:
  - Repeated stratified k-fold (5 folds x 5 repeats, fixed seeds).
  - AUC and Brier reported as p10/p50/p90 across the 25 folds.
  - Calibration slope and intercept fit on pooled out-of-fold predictions
    (logistic recalibration); a well-calibrated model has slope near 1 and
    intercept near 0.
  - Coefficient stability: per-variable min/median/max across all fold fits.
    A variable whose coefficient changes sign across folds is flagged
    unstable and reported as such.
  - Per-county scores are out-of-fold (never scored by a model that saw the
    county in training).

Reporting rules: variable trust tiers accompany every table; associations
are predictive, not causal; no figure is a probability that a new project
draws opposition; leak audit runs on all generated outputs; no em-dashes.

Usage: python county_policy_model.py
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics as st
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(ROOT, *parts)


AGG_CSV = P("data", "county_aggregate.csv")
MANIFEST = P("data", "county_aggregate_manifest.json")
OUT_METRICS = P("data", "county_policy_metrics.json")
OUT_SCORES = P("data", "county_policy_scores.csv")
OUT_MD = P("data", "county_policy_report.md")

N_SPLITS = 5
N_REPEATS = 5
SEED = 7

# Specification selection. Tier 3 variables are strongly collinear
# (density x population +0.87, income x education +0.69, margin loads on
# all urbanicity variables), which makes coefficients sign-unstable under
# weak regularization. Rather than hand-pick a constant, the model searches
# a small grid of feature subsets and regularization strengths and selects
# by a fixed, documented criterion:
#   admissible: every coefficient sign-stable across all CV folds
#   among admissible: median AUC within AUC_TOLERANCE of the best grid
#   point, then fewest variables, then strongest regularization
# The full grid is recorded in the metrics JSON so the choice is auditable.
C_GRID = (1.0, 0.3, 0.1, 0.03, 0.01)
AUC_TOLERANCE = 0.01
SPECS = {
    "full": ["margin_2024", "margin_shift", "existing_dc_count",
             "n_projects_tracked", "state_legislation_events", "land_sqmi",
             "median_hh_income", "pop_density_sqmi", "pct_bachelors_plus",
             "population"],
    "no_shift": ["margin_2024", "existing_dc_count", "n_projects_tracked",
                 "state_legislation_events", "land_sqmi",
                 "median_hh_income", "pop_density_sqmi",
                 "pct_bachelors_plus", "population"],
    "drop_density": ["margin_2024", "margin_shift", "existing_dc_count",
                     "n_projects_tracked", "state_legislation_events",
                     "land_sqmi", "median_hh_income", "pct_bachelors_plus",
                     "population"],
    "core_six": ["margin_2024", "existing_dc_count", "median_hh_income",
                 "pop_density_sqmi", "pct_bachelors_plus", "population"],
    "tier1_plus_size": ["margin_2024", "existing_dc_count",
                        "n_projects_tracked", "land_sqmi", "population"],
}

# (column, report label, trust tier, transform)
# Tier 1: low detection-bias exposure. Tier 3: variable also predicts news
# coverage; on this outcome (public-record ordinances) exposure is reduced
# but tiering is still reported.
VARS = [
    ("margin_2024", "2024 presidential margin (positive = Democratic)", 1, "identity"),
    ("margin_shift", "margin shift 2016 to 2024 (positive = moved Democratic)", 1, "identity"),
    ("existing_dc_count", "existing data centers (atlas)", 1, "log1p"),
    ("n_projects_tracked", "tracked data center projects", 1, "log1p"),
    ("state_legislation_events", "state-level legislation activity (state-inherited)", 2, "log1p"),
    ("land_sqmi", "county land area", 1, "log1p"),
    ("median_hh_income", "median household income", 3, "log10"),
    ("pop_density_sqmi", "population density", 3, "log1p"),
    ("pct_bachelors_plus", "bachelors degree or higher, pct", 3, "identity"),
    ("population", "population", 3, "log1p"),
]

# margin_shift is derived at load time from margin_2024 - margin_2016.
# state_legislation_events is built exclusively from state-scope records
# with no county, which cannot contribute to the county outcome; it is
# leakage-free by construction and tiered 2 (state-inherited, shared by
# every county in a state).


def parse_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def transform(name, x):
    if x is None:
        return None
    if name == "log1p":
        return math.log1p(max(x, 0.0))
    if name == "log10":
        return math.log10(x) if x > 0 else None
    return x


def load():
    rows = list(csv.DictReader(open(AGG_CSV, encoding="utf-8-sig")))
    if not rows:
        print("ERROR: empty aggregate; run county_aggregator.py first",
              file=sys.stderr)
        sys.exit(1)
    # Exclude Puerto Rico municipios from the modeling frame: no
    # presidential margin exists, income sits far outside the mainland
    # distribution, and local land-use institutions differ. They remain in
    # the aggregate layer for the map.
    kept = [r for r in rows if not r["fips"].startswith("72")]
    for r in kept:
        m24, m16 = parse_float(r.get("margin_2024")), parse_float(r.get("margin_2016"))
        r["margin_shift"] = ("" if m24 is None or m16 is None
                             else m24 - m16)
    return kept, len(rows) - len(kept)


def main() -> int:
    try:
        import numpy as np
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import brier_score_loss, roc_auc_score
        from sklearn.model_selection import RepeatedStratifiedKFold
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("ERROR: scikit-learn required", file=sys.stderr)
        return 1

    rows, n_excluded_pr = load()
    all_feats = [v for v, _, _, _ in VARS]
    tr_by = {v: tr for v, _, _, tr in VARS}
    Xall = np.array(
        [[np.nan if (t := transform(tr, parse_float(r[f]))) is None else t
          for f, _, _, tr in VARS] for r in rows], dtype=float)
    y = np.array([int(r["has_enacted_restrictive"]) for r in rows])
    fips = [r["fips"] for r in rows]
    cv = RepeatedStratifiedKFold(n_splits=N_SPLITS, n_repeats=N_REPEATS,
                                 random_state=SEED)

    def run_cv(feat_list, c_reg):
        ix = [all_feats.index(f) for f in feat_list]
        X = Xall[:, ix]
        aucs, briers = [], []
        coef_folds = {f: [] for f in feat_list}
        oof_sum = np.zeros(len(y))
        oof_cnt = np.zeros(len(y))
        for tr_ix, te_ix in cv.split(X, y):
            pipe = make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                LogisticRegression(C=c_reg, max_iter=4000))
            pipe.fit(X[tr_ix], y[tr_ix])
            p = pipe.predict_proba(X[te_ix])[:, 1]
            aucs.append(roc_auc_score(y[te_ix], p))
            briers.append(brier_score_loss(y[te_ix], p))
            for f, c in zip(feat_list,
                            pipe.named_steps["logisticregression"].coef_[0]):
                coef_folds[f].append(float(c))
            oof_sum[te_ix] += p
            oof_cnt[te_ix] += 1
        stable = all(min(cs) > 0 or max(cs) < 0
                     for cs in coef_folds.values())
        return {"aucs": aucs, "briers": briers, "coef_folds": coef_folds,
                "oof": oof_sum / oof_cnt, "sign_stable": stable}

    # --- grid search over (spec x C) ---
    grid = []
    results = {}
    for sname, flist in SPECS.items():
        for c_reg in C_GRID:
            res = run_cv(flist, c_reg)
            results[(sname, c_reg)] = res
            grid.append({"spec": sname, "C": c_reg,
                         "n_vars": len(flist),
                         "auc_median": round(st.median(res["aucs"]), 4),
                         "sign_stable": res["sign_stable"]})
    best_auc = max(g["auc_median"] for g in grid)
    admissible = [g for g in grid
                  if g["sign_stable"] and
                  g["auc_median"] >= best_auc - AUC_TOLERANCE]
    if not admissible:
        print("ERROR: no sign-stable specification within AUC tolerance; "
              "widen the grid before shipping coefficients", file=sys.stderr)
        return 1
    admissible.sort(key=lambda g: (-g["auc_median"], -g["n_vars"], g["C"]))
    sel = admissible[0]
    feats = SPECS[sel["spec"]]
    C_REG = sel["C"]
    chosen = results[(sel["spec"], sel["C"])]
    aucs, briers = chosen["aucs"], chosen["briers"]
    coef_folds = chosen["coef_folds"]
    oof = chosen["oof"]

    # calibration: logistic recalibration on pooled out-of-fold predictions.
    # Under strong shrinkage the raw scores are compressed (underconfident),
    # so a recalibration layer is part of the model. To keep the reported
    # calibration honest, recalibrated scores are cross-fitted: each
    # county's calibrated score comes from a recalibration model fit on
    # folds that exclude it.
    eps = 1e-6
    logit_oof = np.log(np.clip(oof, eps, 1 - eps) /
                       np.clip(1 - oof, eps, 1 - eps)).reshape(-1, 1)
    recal = LogisticRegression(C=1e6, max_iter=4000).fit(logit_oof, y)
    cal_slope = float(recal.coef_[0][0])
    cal_intercept = float(recal.intercept_[0])

    from sklearn.model_selection import StratifiedKFold
    oof_cal = np.zeros(len(y))
    for tr_ix, te_ix in StratifiedKFold(n_splits=5, shuffle=True,
                                        random_state=SEED).split(logit_oof, y):
        rc = LogisticRegression(C=1e6, max_iter=4000)
        rc.fit(logit_oof[tr_ix], y[tr_ix])
        oof_cal[te_ix] = rc.predict_proba(logit_oof[te_ix])[:, 1]
    logit_cal = np.log(np.clip(oof_cal, eps, 1 - eps) /
                       np.clip(1 - oof_cal, eps, 1 - eps)).reshape(-1, 1)
    post = LogisticRegression(C=1e6, max_iter=4000).fit(logit_cal, y)
    post_slope = float(post.coef_[0][0])
    post_intercept = float(post.intercept_[0])
    brier_cal = float(np.mean((oof_cal - y) ** 2))

    def pctile(v, q):
        s = sorted(v)
        return s[min(len(s) - 1, int(q * len(s)))]

    base = float(y.mean())
    metrics = {
        "n": int(len(y)),
        "n_excluded_puerto_rico": int(n_excluded_pr),
        "n_positive": int(y.sum()),
        "base_rate": round(base, 4),
        "cv": {"folds": N_SPLITS, "repeats": N_REPEATS, "seed": SEED},
        "specification_selection": {
            "criterion": f"sign-stable on all folds, median AUC within "
                         f"{AUC_TOLERANCE} of grid best; then highest AUC, "
                         f"most variables retained, strongest "
                         f"regularization",
            "selected": {"spec": sel["spec"], "C": C_REG,
                         "variables": feats},
            "grid": grid},
        "roc_auc": {"p10": round(pctile(aucs, .10), 4),
                    "p50": round(st.median(aucs), 4),
                    "p90": round(pctile(aucs, .90), 4)},
        "brier": {"p10": round(pctile(briers, .10), 4),
                  "p50": round(st.median(briers), 4),
                  "p90": round(pctile(briers, .90), 4),
                  "base_rate_brier": round(base * (1 - base), 4)},
        "calibration": {
            "raw_slope": round(cal_slope, 3),
            "raw_intercept": round(cal_intercept, 3),
            "post_recalibration_slope": round(post_slope, 3),
            "post_recalibration_intercept": round(post_intercept, 3),
            "brier_calibrated": round(brier_cal, 4),
            "note": "raw scores are compressed by shrinkage; the model "
                    "includes a cross-fitted logistic recalibration layer. "
                    "calibrated_score in the scores file is cross-fitted: "
                    "no county's calibration was fit on itself. Post "
                    "slope near 1 and intercept near 0 indicate the "
                    "calibrated scores are honest probabilities."},
        "coefficients": {
            f: {"min": round(min(cs), 3), "median": round(st.median(cs), 3),
                "max": round(max(cs), 3),
                "sign_stable": bool(min(cs) > 0) or bool(max(cs) < 0)}
            for f, cs in coef_folds.items()},
        "aggregate_manifest": (json.load(open(MANIFEST))
                               if os.path.exists(MANIFEST) else None),
    }
    with open(OUT_METRICS, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    # per-county out-of-fold scores (map layer)
    dec = np.searchsorted(np.quantile(oof_cal, np.linspace(.1, .9, 9)),
                          oof_cal) + 1
    with open(OUT_SCORES, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fips", "raw_oof_score", "calibrated_score",
                    "score_decile", "has_enacted_restrictive"])
        for i in range(len(y)):
            w.writerow([fips[i], round(float(oof[i]), 4),
                        round(float(oof_cal[i]), 4), int(dec[i]),
                        int(y[i])])

    # auto-generated report
    L = []
    w = L.append
    w("# County Enacted-Restriction Model")
    w("")
    w("Auto-generated by county_policy_model.py. Do not edit by hand; rerun "
      "the module after data updates. Companion files: "
      "county_policy_metrics.json, county_policy_scores.csv.")
    w("")
    w("**Internal diagnostic. Associations are predictive, not causal. "
      "Scores are out-of-fold cross-validated probabilities of the county "
      "having an enacted restrictive policy given its profile; they are not "
      "probabilities that a new project draws opposition.**")
    w("")
    w("## Outcome and frame")
    w("")
    w(f"- Frame: {metrics['n']} counties. Puerto Rico municipios "
      f"(n = {n_excluded_pr}) are excluded from the model and retained in "
      f"the aggregate layer: no presidential margin exists and land-use "
      f"institutions differ.")
    w(f"- Positive: county has at least one enacted restrictive policy, "
      f"n = {metrics['n_positive']} "
      f"(base rate {100 * base:.1f} pct).")
    w("- The outcome is an enacted public record, which reduces but does "
      "not remove detection-bias exposure relative to opposition-presence "
      "outcomes.")
    w("")
    w("## Validation")
    w("")
    a, b = metrics["roc_auc"], metrics["brier"]
    w(f"- Repeated stratified CV ({N_SPLITS} folds x {N_REPEATS} repeats, "
      f"seed {SEED}).")
    w(f"- Specification selected by fixed criterion (see metrics JSON for "
      f"the full grid): spec '{sel['spec']}' with C = {C_REG}. Tier 3 "
      f"variables are strongly collinear (density x population +0.87, "
      f"income x education +0.69), so sign stability requires shrinkage; "
      f"only sign-stable specifications within {AUC_TOLERANCE} AUC of the "
      f"grid best are admissible, and among those the criterion retains "
      f"as many variables as possible. Under strong shrinkage, coefficient "
      f"magnitudes are compressed toward zero by design; read relative "
      f"ordering and sign, not absolute size.")
    w(f"- ROC AUC: {a['p50']:.2f} (p10 {a['p10']:.2f}, p90 {a['p90']:.2f}).")
    w(f"- Brier: {b['p50']:.3f} vs base-rate Brier "
      f"{b['base_rate_brier']:.3f}.")
    c = metrics["calibration"]
    w(f"- Raw-score calibration slope {c['raw_slope']:.2f} (shrinkage "
      f"compresses raw scores); the model therefore includes a "
      f"cross-fitted recalibration layer. Post-recalibration slope "
      f"{c['post_recalibration_slope']:.2f}, intercept "
      f"{c['post_recalibration_intercept']:.2f}. Use calibrated_score "
      f"from the scores file.")
    w("")
    w("## Coefficients (fold stability)")
    w("")
    w("| Variable | Tier | Median | Fold range | Sign stable |")
    w("|---|---|---|---|---|")
    tier = {v: t for v, _, t, _ in VARS}
    label = {v: l for v, l, _, _ in VARS}
    for f, cs in sorted(metrics["coefficients"].items(),
                        key=lambda kv: -abs(kv[1]["median"])):
        w(f"| {label[f]} | {tier[f]} | {cs['median']:+.2f} | "
          f"{cs['min']:+.2f} to {cs['max']:+.2f} | "
          f"{'yes' if cs['sign_stable'] else 'NO'} |")
    w("")
    w("Coefficients are on standardized, transformed inputs (logs on "
      "density, population, DC count, income). Variables whose coefficient "
      "changes sign across folds should not be interpreted directionally.")
    w("")
    w("## Trust tiers")
    w("")
    w("- Tier 1: political margin, existing DC count. Low detection-bias "
      "exposure; safest for external use.")
    w("- Tier 3: population, density, income, education. These variables "
      "also predict news coverage; on this public-record outcome the "
      "exposure is reduced, and it is still reported.")
    w("")
    w("## Standing caveats")
    w("")
    w("- Scores are out-of-fold: no county is scored by a model that saw "
      "it in training.")
    w("- The score is a resemblance measure over county profiles, "
      "calibrated to the observed enactment base rate. It is not a "
      "forecast of opposition to any specific project.")
    w("- Inputs are QC-gated upstream by county_aggregator.py; the input "
      "manifest is embedded in county_policy_metrics.json for "
      "reproducibility.")
    w("")

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    # leak audit over all generated outputs
    pat = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.I)
    leaks = []
    for path in (OUT_METRICS, OUT_SCORES, OUT_MD):
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if pat.search(line):
                leaks.append(f"{os.path.basename(path)}:{i}")

    print(f"n={metrics['n']} ({metrics['n_positive']} enacted) | "
          f"AUC {a['p50']:.2f} [{a['p10']:.2f}-{a['p90']:.2f}] | "
          f"Brier {b['p50']:.3f} (base {b['base_rate_brier']:.3f}) | "
          f"post-recal slope {c['post_recalibration_slope']:.2f}")
    unstable = [f for f, cs in metrics["coefficients"].items()
                if not cs["sign_stable"]]
    if unstable:
        print("sign-unstable coefficients:", ", ".join(unstable))
    print("wrote metrics, scores, report")
    print("leak audit:", "FAIL " + ", ".join(leaks) if leaks else "clean")
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
