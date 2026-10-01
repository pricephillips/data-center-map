"""
feature_search.py

Adaptive feature search and self-updating variable weights for the county
restriction models.

What it answers: across every county variable the platform holds, which ones
carry held-out predictive signal for (a) a county enacting a restriction,
(b) a county enacting a moratorium specifically, and (c) recorded local
opposition converting into an enacted restriction; how much each one
contributes; and how that contribution is moving as the data grows.

Pool: data/county_features_expanded.csv and its catalog, both written by
county_features.py. Adding a variable to the search is a change there (or a
plugin CSV), never a change here.

Evidence, per target, all out-of-sample:

  1. Held-out permutation importance under a regularized gradient-boosted
     model (shallow trees, repeated stratified CV). The drop in held-out AUC
     when a variable is scrambled. Nonlinear and interaction-aware, and the
     quantity the weights are built from.
  2. Cluster permutation importance. Variables correlated above
     cluster_threshold are scrambled together, because two collinear
     variables each look unimportant when scrambled alone (the other one
     covers for it). Credit is assigned to the cluster first, then split
     among its members by their individual importance.
  3. Complementary-pairs stability selection (Shah and Samworth 2013) on an
     L1 logistic model: how often a variable is selected across many
     half-samples, and whether it keeps its sign. This is the linear,
     direction-bearing evidence the production model's sign-stability rule
     needs, and it is the gate for promotion into that model.
  4. Marginal held-out AUC, reported for context only.
  5. Spec 008 US4, one target only (configs "ebm" block, restrict_profile):
     an Explainable Boosting Machine scored on the same CV folds, then refit
     once for per-variable shape tables a client chart can draw. It is a
     challenger. choose_promotions() never reads it, so no EBM-only
     variable reaches the production pool without the L1 evidence, the
     production model's sign-stability rule and the calibration gate.

Weights update themselves. Each variable's importance is tracked by a
local-level Kalman filter: the state is the smoothed importance, each run is
a noisy observation of it with a standard error taken from the fold spread.
A precise run moves the weight a lot; a noisy one moves it little; a variable
that stops carrying signal decays toward zero at the rate the evidence
supports rather than all at once. Weights are the filtered importances of
admitted variables, normalized to sum to one. A run on unchanged inputs does
not update anything, so the daily schedule cannot compound a single result.

Admission (a variable carries weight): its cluster improved held-out AUC in
at least 90 pct of folds this run (p10 above zero), or its filtered
importance is above zero at the lower 95 pct one-sided bound.

Promotion (a variable enters the production pool in county_policy_model.py):
target 'restrict' only; leakage class in the registered allow-list (default:
no outcome information of any kind); selection probability at or above the
registered threshold with a consistent sign; admitted; at most one variable
per correlation cluster; at most max_promoted. Promotion only makes a
variable a CANDIDATE: the production model's own sign-stability rule,
stability-cost alarm and calibration gate still decide what ships.

Reporting rules: associations are predictive, not causal. Importance is the
held-out AUC a variable contributes given the others, not an effect size.
No figure here is a probability that a project draws opposition. All
outputs are internal diagnostics.

Writes
  data/feature_search_metrics.json
  data/feature_search_ranking.csv
  data/feature_search_weights.json         current filtered state
  data/feature_search_weight_history.csv   append-only, one row per feature
                                           per update
  data/feature_search_promotions.json      read by county_policy_model.py
  data/feature_search_report.md
  data/feature_search_shapes.json          EBM shape tables (spec 008 US4);
                                           written when interpret is installed

Usage
  python feature_search.py
  python feature_search.py --target conversion
  python feature_search.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics as st
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(ROOT, *parts)


FEAT_CSV = P("data", "county_features_expanded.csv")
CATALOG = P("data", "county_features_catalog.json")
CONFIG = P("configs", "feature_search.json")
PROD_METRICS = P("data", "county_policy_metrics.json")

OUT_METRICS = P("data", "feature_search_metrics.json")
OUT_RANK = P("data", "feature_search_ranking.csv")
OUT_WEIGHTS = P("data", "feature_search_weights.json")
OUT_HISTORY = P("data", "feature_search_weight_history.csv")
OUT_PROMO = P("data", "feature_search_promotions.json")
OUT_MD = P("data", "feature_search_report.md")
OUT_SHAPES = P("data", "feature_search_shapes.json")

LABEL_COLS = {"fips", "state", "has_enacted_restrictive",
              "has_enacted_moratorium", "in_conversion_frame",
              "window_defined", "first_enacted_date"}

HISTORY_FIELDS = ["run_date", "target", "feature", "observed", "observed_se",
                  "filtered", "filtered_sd", "weight", "admitted", "drift_z",
                  "n_frame", "n_positive", "inputs_hash"]

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.I)
EM_DASH = chr(0x2014)


# --------------------------------------------------------------------------
# pure primitives (covered by --selftest without fitting anything)
# --------------------------------------------------------------------------

def pctile(vals, q):
    s = sorted(vals)
    if not s:
        return float("nan")
    return s[min(len(s) - 1, int(q * len(s)))]


def kalman_update(prior_mean, prior_var, obs, obs_var, process_var):
    """Local-level model, one step. Returns (mean, var, gain).

    Predict: the true importance may have moved by process_var since the last
    run. Update: blend in the observation in proportion to relative
    precision. A zero-variance observation is taken exactly; an infinitely
    noisy one changes nothing.
    """
    pred_var = prior_var + process_var
    if obs_var <= 0:
        return obs, 0.0, 1.0
    gain = pred_var / (pred_var + obs_var)
    mean = prior_mean + gain * (obs - prior_mean)
    return mean, (1 - gain) * pred_var, gain


def drift_z(prior_mean, prior_var, obs, obs_var, process_var):
    """Standardized surprise of an observation against the filter's forecast."""
    denom = math.sqrt(max(prior_var + process_var + obs_var, 1e-12))
    return (obs - prior_mean) / denom


def is_admitted(cluster_p10, filtered_mean, filtered_sd, z=1.645):
    return (cluster_p10 is not None and cluster_p10 > 0) or \
           (filtered_mean - z * filtered_sd > 0)


def normalize_weights(values: dict, active: set) -> dict:
    pos = {k: max(v, 0.0) for k, v in values.items() if k in active}
    tot = sum(pos.values())
    return {k: (round(pos.get(k, 0.0) / tot, 6) if tot > 0 and k in pos else 0.0)
            for k in values}


def split_cluster_credit(cluster_value: float, member_values: dict) -> dict:
    """Assign a cluster's importance to its members by their individual
    importance; equal shares if no member carries any alone."""
    if cluster_value <= 0:
        return {m: 0.0 for m in member_values}
    pos = {m: max(v, 0.0) for m, v in member_values.items()}
    tot = sum(pos.values())
    if tot <= 0:
        share = cluster_value / len(member_values)
        return {m: share for m in member_values}
    return {m: cluster_value * v / tot for m, v in pos.items()}


def mb_error_bound(q: float, p: int, threshold: float) -> float | None:
    """Expected number of falsely selected variables (Meinshausen and
    Buhlmann 2010; valid for complementary pairs under Shah and Samworth's
    weaker conditions)."""
    if p <= 0 or threshold <= 0.5:
        return None
    return round(q * q / ((2 * threshold - 1) * p), 3)


def admissible_penalties(q_mean: dict, p: int, threshold: float,
                         max_false: float) -> list:
    """Penalties whose mean selection count keeps the error bound at or below
    max_false. The sparsest penalty is always kept so selection is never
    empty of evidence; if even it exceeds the bound the report shows so."""
    q_max = math.sqrt(max_false * (2 * threshold - 1) * p) if threshold > 0.5 else 0
    ok = [C for C, q in q_mean.items() if q <= q_max]
    return ok or [min(q_mean)]


def direction_label(pos_frac: float, sel_prob: float, consistency: float) -> str:
    if sel_prob <= 0:
        return "not selected"
    if pos_frac >= consistency:
        return "positive"
    if pos_frac <= 1 - consistency:
        return "negative"
    return "mixed"


def choose_promotions(rows: list, cfg: dict, production_vars: set) -> list:
    """Deterministic promotion rule. rows carry: feature, family,
    leakage_class, sel_prob, direction, admitted, filtered, cluster."""
    pc = cfg["promotion"]
    thr = cfg["stability_selection"]["threshold"]
    allowed = set(pc["allowed_leakage_classes"])
    excluded_fam = set(pc.get("exclude_families", []))
    cands = [r for r in rows
             if r["feature"] not in production_vars
             and r["family"] not in excluded_fam
             and r["leakage_class"] in allowed
             and r["sel_prob"] >= thr
             and r["direction"] in ("positive", "negative")
             and r["admitted"]]
    cands.sort(key=lambda r: (-r["filtered"], -r["sel_prob"], r["feature"]))
    out, used = [], set()
    for r in cands:
        if r["cluster"] in used:
            continue
        used.add(r["cluster"])
        out.append(r)
        if len(out) >= pc["max_promoted"]:
            break
    return out


def config_hash(cfg: dict) -> str:
    body = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def read_history(path=None) -> list:
    try:
        with open(path or OUT_HISTORY, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def append_history(rows: list, path=None) -> None:
    target = path or OUT_HISTORY
    os.makedirs(os.path.dirname(target), exist_ok=True)
    exists = os.path.exists(target)
    with open(target, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS, lineterminator="\n")
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HISTORY_FIELDS})


# --------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------

def l1_logistic(C):
    """L1 logistic across scikit-learn versions. 1.8 deprecated `penalty` in
    favour of l1_ratio and 1.10 removes it; CI installs the latest release,
    so the call has to follow the installed API rather than pin one."""
    import sklearn
    from sklearn.linear_model import LogisticRegression
    major, minor = (int(x) for x in sklearn.__version__.split(".")[:2])
    if (major, minor) >= (1, 8):
        return LogisticRegression(l1_ratio=1.0, solver="liblinear", C=C,
                                  max_iter=2000)
    return LogisticRegression(penalty="l1", solver="liblinear", C=C,
                              max_iter=2000)


def production_variables() -> set:
    """Variables already in the production pool; never re-promoted."""
    try:
        import county_policy_model as cpm
        return {v for v, *_ in cpm.VARS}
    except Exception:
        return set()


def cluster_features(X, names, threshold):
    """Average-linkage clusters on 1 - |Spearman rho|."""
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    from scipy.stats import spearmanr
    if len(names) == 1:
        return {names[0]: 0}
    Xf = np.where(np.isnan(X), np.nanmedian(X, axis=0), X)
    rho = spearmanr(Xf).statistic
    rho = np.nan_to_num(np.atleast_2d(rho), nan=0.0)
    dist = 1 - np.abs(rho)
    np.fill_diagonal(dist, 0.0)
    dist = (dist + dist.T) / 2
    Z = linkage(squareform(dist, checks=False), method="average")
    lab = fcluster(Z, t=1 - threshold, criterion="distance")
    return {n: int(c) for n, c in zip(names, lab)}


def ebm_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("interpret") is not None


def ebm_model(ecfg: dict, names: list, seed: int):
    from interpret.glassbox import ExplainableBoostingClassifier
    return ExplainableBoostingClassifier(
        feature_names=list(names), interactions=ecfg.get("interactions", 0),
        outer_bags=ecfg.get("outer_bags", 8), max_bins=ecfg.get("max_bins", 32),
        random_state=seed, n_jobs=-1)


def ebm_shapes(ebm, names: list, labels: dict) -> dict:
    """Per-variable shape tables from a fitted main-effects EBM.

    Continuous variables: edges has one more entry than scores (bin bounds).
    Nominal variables: edges holds the category names. Scores are log-odds
    contributions; lower/upper are the EBM's bagged bounds.
    """
    glob = ebm.explain_global()
    imp = list(ebm.term_importances())
    out = {}
    for i, f in enumerate(names):
        d = glob.data(i)
        ftype = d.get("type", "univariate")
        edges = [x if isinstance(x, str) else round(float(x), 6) for x in d["names"]]
        out[f] = {"label": labels.get(f, f),
                  "type": "nominal" if len(edges) == len(d["scores"]) else "continuous",
                  "importance": round(float(imp[i]), 6),
                  "edges": edges,
                  "scores": [round(float(v), 6) for v in d["scores"]],
                  "lower": [round(float(v), 6) for v in d.get("lower_bounds", [])],
                  "upper": [round(float(v), 6) for v in d.get("upper_bounds", [])]}
        if ftype not in ("univariate",):
            out[f]["term_type"] = ftype
    return out


def run_ebm(X, y, names, folds, ecfg: dict, seed: int, labels: dict) -> dict:
    """Held-out AUC on the given folds, then one full-frame refit for shapes."""
    import warnings
    import numpy as np
    from sklearn.metrics import roc_auc_score
    aucs = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for tr, te in folds:
            m = ebm_model(ecfg, names, seed).fit(X[tr], y[tr])
            aucs.append(float(roc_auc_score(y[te], m.predict_proba(X[te])[:, 1])))
        full = ebm_model(ecfg, names, seed).fit(X, y)
    return {"auc": {"p10": round(pctile(aucs, .1), 4),
                    "p50": round(float(np.median(aucs)), 4),
                    "p90": round(pctile(aucs, .9), 4)},
            "shapes": ebm_shapes(full, names, labels)}


def search_target(tname, tcfg, cfg, data, catalog, rng_seed):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rows = data
    if tcfg["frame"] != "all":
        rows = [r for r in rows if str(r.get(tcfg["frame"])) == "1"]
    y = np.array([int(r[tcfg["label"]]) for r in rows])
    n, npos = len(y), int(y.sum())
    info = {"target": tname, "description": tcfg.get("description", ""),
            "label": tcfg["label"], "frame": tcfg["frame"],
            "n_frame": n, "n_positive": npos,
            "base_rate": round(npos / n, 4) if n else None}
    if npos < cfg["min_positives"] or n - npos < cfg["min_positives"]:
        info["status"] = (f"WITHHELD: {npos} positives in a frame of {n}; the "
                          f"registered minimum is {cfg['min_positives']}")
        return info, []

    excl = set(tcfg.get("exclude_leakage", []))
    cand = [f for f, m in catalog.items() if m["leakage_class"] not in excl]

    def col(f):
        out = []
        for r in rows:
            v = r.get(f, "")
            out.append(float(v) if v not in ("", None) else np.nan)
        return np.array(out)

    dropped = {}
    names, cols = [], []
    for f in cand:
        c = col(f)
        finite = c[~np.isnan(c)]
        if finite.size == 0 or np.unique(finite).size < 2:
            dropped[f] = "constant in frame"
            continue
        mode = st.mode(finite.tolist())
        nz_pos = int(((c != mode) & ~np.isnan(c) & (y == 1)).sum())
        if nz_pos < 5:
            dropped[f] = f"varies in only {nz_pos} positive counties"
            continue
        names.append(f)
        cols.append(c)
    X = np.column_stack(cols)
    p = len(names)
    info["n_candidates"] = p
    info["dropped"] = dropped
    clusters = cluster_features(X, names, cfg["cluster_threshold"])
    members = {}
    for f, c in clusters.items():
        members.setdefault(c, []).append(f)
    multi = {c: m for c, m in members.items() if len(m) > 1}

    # ---- CV: GBM permutation importance + linear benchmark + marginal AUC
    g = cfg["gbm"]
    cvc = cfg["cv"]
    cv = RepeatedStratifiedKFold(n_splits=cvc["splits"], n_repeats=cvc["repeats"],
                                 random_state=cvc["seed"])
    rng = np.random.default_rng(rng_seed)
    reps = cfg["permutation_repeats"]
    perm = {f: [] for f in names}
    cperm = {c: [] for c in multi}
    marg = {f: [] for f in names}
    auc_gbm, auc_lin = [], []
    oof_gbm = np.zeros(n)
    oof_cnt = np.zeros(n)
    for tr, te in cv.split(X, y):
        gbm = HistGradientBoostingClassifier(
            max_depth=g["max_depth"], learning_rate=g["learning_rate"],
            max_iter=g["max_iter"], min_samples_leaf=g["min_samples_leaf"],
            l2_regularization=g["l2_regularization"], early_stopping=False,
            random_state=cvc["seed"])
        gbm.fit(X[tr], y[tr])
        Xte = X[te]
        base = roc_auc_score(y[te], gbm.predict_proba(Xte)[:, 1])
        auc_gbm.append(base)
        oof_gbm[te] += gbm.predict_proba(Xte)[:, 1]
        oof_cnt[te] += 1

        def drop_for(idx):
            ds = []
            for _ in range(reps):
                Xp = Xte.copy()
                order = rng.permutation(len(te))
                Xp[:, idx] = Xte[order][:, idx]
                ds.append(base - roc_auc_score(y[te], gbm.predict_proba(Xp)[:, 1]))
            return float(np.mean(ds))

        for j, f in enumerate(names):
            perm[f].append(drop_for([j]))
        for c, m in multi.items():
            cperm[c].append(drop_for([names.index(f) for f in m]))

        lin = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                            StandardScaler(),
                            LogisticRegression(C=0.1, max_iter=4000))
        lin.fit(X[tr], y[tr])
        auc_lin.append(roc_auc_score(y[te], lin.predict_proba(Xte)[:, 1]))

        med = np.nanmedian(X[tr], axis=0)
        for j, f in enumerate(names):
            v = np.where(np.isnan(Xte[:, j]), med[j], Xte[:, j])
            marg[f].append(roc_auc_score(y[te], v))

    info["held_out_auc"] = {
        "gbm_all_candidates": {"p10": round(pctile(auc_gbm, .1), 4),
                               "p50": round(st.median(auc_gbm), 4),
                               "p90": round(pctile(auc_gbm, .9), 4)},
        "l2_logistic_all_candidates": {"p10": round(pctile(auc_lin, .1), 4),
                                       "p50": round(st.median(auc_lin), 4),
                                       "p90": round(pctile(auc_lin, .9), 4)}}
    info["cv"] = dict(cvc)

    # ---- EBM challenger (spec 008 US4), same folds as the GBM
    ecfg = cfg.get("ebm") or {}
    if ecfg.get("target") == tname:
        if ebm_available():
            res = run_ebm(X, y, names, list(cv.split(X, y)), ecfg, cvc["seed"],
                          {f: catalog[f]["label"] for f in names})
            info["held_out_auc"]["ebm_all_candidates"] = res["auc"]
            info["_ebm_shapes"] = res["shapes"]
        else:
            info["ebm"] = "not run: interpret-core is not installed"

    # ---- complementary-pairs stability selection (L1 logistic)
    ss = cfg["stability_selection"]
    sel_cnt = {C: np.zeros(p) for C in ss["C_grid"]}
    pos_cnt = {C: np.zeros(p) for C in ss["C_grid"]}
    n_fits = 0
    q_by_C = {C: [] for C in ss["C_grid"]}
    halves = StratifiedKFold(n_splits=2, shuffle=True)
    for k in range(ss["n_pairs"]):
        halves.random_state = cvc["seed"] + 1000 + k
        for idx, _ in halves.split(X, y):
            n_fits += 1
            prep = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                                 StandardScaler())
            Xs = prep.fit_transform(X[idx])
            for C in ss["C_grid"]:
                m = l1_logistic(C)
                m.fit(Xs, y[idx])
                coef = m.coef_[0]
                chosen = np.abs(coef) > 1e-8
                sel_cnt[C] += chosen
                pos_cnt[C] += chosen & (coef > 0)
                q_by_C[C].append(int(chosen.sum()))
    q_mean = {C: st.mean(v) for C, v in q_by_C.items()}
    grid_ok = admissible_penalties(q_mean, p, ss["threshold"],
                                   ss.get("max_expected_false", 2.0))
    sel_prob, pos_frac = {}, {}
    for j, f in enumerate(names):
        best_C = max(grid_ok, key=lambda C: (sel_cnt[C][j], -C))
        sel_prob[f] = float(sel_cnt[best_C][j] / n_fits)
        pos_frac[f] = float(pos_cnt[best_C][j] / sel_cnt[best_C][j]) if sel_cnt[best_C][j] else 0.5
    q = max(q_mean[C] for C in grid_ok)
    info["stability_selection"] = {
        "n_half_sample_fits": n_fits, "threshold": ss["threshold"],
        "penalties_used": sorted(grid_ok),
        "mean_selected_per_penalty": {str(C): round(v, 2) for C, v in sorted(q_mean.items())},
        "mean_selected_at_weakest_penalty_used": round(q, 2),
        "expected_false_selections_bound": mb_error_bound(q, p, ss["threshold"])}

    # ---- assemble per-feature evidence
    ns = cvc["splits"]
    out = []
    for f in names:
        c = clusters[f]
        fold = perm[f]
        cfold = cperm.get(c, fold)
        out.append({
            "target": tname, "feature": f,
            "family": catalog[f]["family"], "tier": catalog[f]["tier"],
            "leakage_class": catalog[f]["leakage_class"],
            "label": catalog[f]["label"], "cluster": c,
            "cluster_size": len(members[c]),
            "perm_mean": float(np.mean(fold)),
            "perm_se": float(np.std(fold, ddof=1) / math.sqrt(ns)) if len(fold) > 1 else 0.0,
            "perm_p10": pctile(fold, .1), "perm_p90": pctile(fold, .9),
            "cluster_perm_mean": float(np.mean(cfold)),
            "cluster_perm_se": float(np.std(cfold, ddof=1) / math.sqrt(ns)) if len(cfold) > 1 else 0.0,
            "cluster_perm_p10": pctile(cfold, .1),
            "marginal_auc": float(np.mean(marg[f])),
            "sel_prob": round(sel_prob[f], 4),
            "direction": direction_label(pos_frac[f], sel_prob[f], ss["sign_consistency"]),
        })
    # cluster credit -> attributed observation for the filter
    for c, m in members.items():
        rows_c = [r for r in out if r["cluster"] == c]
        if not rows_c:
            continue
        credit = split_cluster_credit(rows_c[0]["cluster_perm_mean"],
                                      {r["feature"]: r["perm_mean"] for r in rows_c})
        for r in rows_c:
            r["observed"] = credit[r["feature"]]
            share = (credit[r["feature"]] / rows_c[0]["cluster_perm_mean"]
                     if rows_c[0]["cluster_perm_mean"] > 0 else 1.0 / len(rows_c))
            r["observed_se"] = max(rows_c[0]["cluster_perm_se"] * share, 1e-4)
    info["status"] = "OK"
    return info, out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_inputs():
    with open(FEAT_CSV, encoding="utf-8-sig", newline="") as fh:
        data = list(csv.DictReader(fh))
    meta = json.load(open(CATALOG, encoding="utf-8"))
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    return data, meta, cfg


def main(only=None) -> int:
    import importlib.util
    if any(importlib.util.find_spec(m) is None for m in ("numpy", "scipy", "sklearn")):
        print("ERROR: numpy, scipy and scikit-learn are required", file=sys.stderr)
        return 1
    try:
        data, meta, cfg = load_inputs()
    except (OSError, ValueError) as e:
        print(f"ERROR: inputs unavailable ({e}); run county_features.py first",
              file=sys.stderr)
        return 1
    catalog = meta["features"]
    chash = config_hash(cfg)
    inputs_hash = f"{meta.get('content_hash', '')}-{chash}"
    today = dt.date.today().isoformat()

    try:
        state = json.load(open(OUT_WEIGHTS, encoding="utf-8"))
    except (OSError, ValueError):
        state = {"targets": {}}
    try:
        prior_metrics = json.load(open(OUT_METRICS, encoding="utf-8"))
    except (OSError, ValueError):
        prior_metrics = {"targets": {}}

    # The shapes file is required for a skip only where it can be produced;
    # otherwise a run without interpret could never skip.
    must_exist = (OUT_METRICS, OUT_RANK, OUT_PROMO, OUT_MD) + (
        (OUT_SHAPES,) if cfg.get("ebm", {}).get("target") and ebm_available() else ())
    if not only and all(
            state.get("targets", {}).get(t, {}).get("inputs_hash") == inputs_hash
            for t in cfg["targets"]) and all(
            os.path.exists(f) for f in must_exist):
        # Nothing the search reads has changed since the last update, and the
        # search is deterministic, so a rerun would reproduce every output
        # byte for byte. Skip it rather than spend the CI minutes.
        print(f"inputs unchanged since {state.get('generated', 'last run')} "
              f"(hash {inputs_hash}); nothing to update")
        return 0

    wf = cfg["weight_filter"]
    Q = wf["process_sd"] ** 2
    V0 = wf["initial_sd"] ** 2

    shapes_doc = None
    metrics = {"generated": today, "config_hash": chash,
               "features_hash": meta.get("content_hash", ""),
               "n_pool": len(catalog), "targets": {}}
    all_rows = []
    history_rows = []
    for i, (tname, tcfg) in enumerate(cfg["targets"].items()):
        if only and tname != only:
            metrics["targets"][tname] = prior_metrics.get("targets", {}).get(tname, {})
            all_rows += [r for r in _read_rank() if r.get("target") == tname]
            continue
        tstate = state["targets"].get(tname, {})
        info, rows = search_target(tname, tcfg, cfg, data, catalog, cfg["cv"]["seed"] + i)
        shapes = info.pop("_ebm_shapes", None)
        if shapes is not None:
            shapes_doc = {
                "generated": today, "target": tname,
                "role": "challenger; never read by choose_promotions()",
                "config": {k: v for k, v in cfg["ebm"].items() if not k.startswith("_")}
                          | {"seed": cfg["cv"]["seed"]},
                "inputs_hash": inputs_hash,
                "n_frame": info["n_frame"], "n_positive": info["n_positive"],
                "held_out_auc": info["held_out_auc"]["ebm_all_candidates"],
                "note": ("scores are log-odds contributions on the EBM's own "
                         "binning; predictive association, not an effect size"),
                "shapes": dict(sorted(shapes.items(),
                                      key=lambda kv: -kv[1]["importance"]))}
        metrics["targets"][tname] = info
        if not rows:
            print(f"[{tname}] {info['status']}")
            continue
        unchanged = tstate.get("inputs_hash") == inputs_hash
        fstate = tstate.get("features", {})
        new_state = {}
        for r in rows:
            f = r["feature"]
            seen_before = f in fstate
            prior = fstate.get(f, {"mean": 0.0, "var": V0})
            if unchanged:
                mean, var = prior["mean"], prior["var"]
                z = 0.0
            else:
                # No forecast exists for a variable's first observation, so
                # there is nothing for it to drift from.
                z = drift_z(prior["mean"], prior["var"], r["observed"],
                            r["observed_se"] ** 2, Q) if seen_before else 0.0
                mean, var, _ = kalman_update(prior["mean"], prior["var"],
                                             r["observed"], r["observed_se"] ** 2, Q)
            r["filtered"] = mean
            r["filtered_sd"] = math.sqrt(var)
            r["drift_z"] = round(z, 3)
            r["admitted"] = is_admitted(r["cluster_perm_p10"], mean, math.sqrt(var))
            new_state[f] = {"mean": mean, "var": var}
        active = {r["feature"] for r in rows if r["admitted"]}
        weights = normalize_weights({r["feature"]: r["filtered"] for r in rows}, active)
        for r in rows:
            r["weight"] = weights[r["feature"]]
        info["n_admitted"] = len(active)
        info["weights_updated"] = not unchanged
        info["drift_flags"] = sorted(r["feature"] for r in rows
                                     if abs(r["drift_z"]) >= wf["drift_z"])
        state["targets"][tname] = {
            "inputs_hash": inputs_hash, "updated": today if not unchanged
            else tstate.get("updated", today),
            "n_frame": info["n_frame"], "n_positive": info["n_positive"],
            "features": new_state,
            "weights": {f: w for f, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > 0}}
        if not unchanged:
            for r in rows:
                history_rows.append({
                    "run_date": today, "target": tname, "feature": r["feature"],
                    "observed": round(r["observed"], 6),
                    "observed_se": round(r["observed_se"], 6),
                    "filtered": round(r["filtered"], 6),
                    "filtered_sd": round(r["filtered_sd"], 6),
                    "weight": r["weight"], "admitted": int(r["admitted"]),
                    "drift_z": r["drift_z"], "n_frame": info["n_frame"],
                    "n_positive": info["n_positive"], "inputs_hash": inputs_hash})
        all_rows += rows
        a = info["held_out_auc"]["gbm_all_candidates"]
        print(f"[{tname}] n={info['n_frame']} ({info['n_positive']} positive) | "
              f"{info['n_candidates']} candidates, {len(active)} admitted | "
              f"GBM held-out AUC {a['p50']:.3f} | weights "
              f"{'updated' if not unchanged else 'unchanged (same inputs)'}")

    # ---- promotion into the production pool
    pc = cfg["promotion"]
    prod = production_variables()
    promo_rows = [r for r in all_rows if r.get("target") == pc["target"]
                  and isinstance(r.get("sel_prob"), float)]
    promoted = choose_promotions(promo_rows, cfg, prod) if pc["enabled"] else []
    keep_promotions = bool(only) and only != pc["target"]
    if keep_promotions:
        # A single-target run on another target must not rewrite the file the
        # production model reads; the last promotion decision stands.
        try:
            prev = json.load(open(OUT_PROMO, encoding="utf-8"))
            keep = {p["feature"] for p in prev.get("promoted", [])}
            promoted = [r for r in promo_rows if r["feature"] in keep]
        except (OSError, ValueError):
            promoted = []
    for r in all_rows:
        r["promoted"] = int(r.get("target") == pc["target"]
                            and r["feature"] in {p["feature"] for p in promoted})
    promo = {
        "generated": today, "enabled": bool(pc["enabled"]),
        "target": pc["target"], "inputs_hash": inputs_hash,
        "source_csv": "data/county_features_expanded.csv",
        "rule": ("selection probability >= threshold with consistent sign, "
                 "admitted, leakage class in allow-list, one per correlation "
                 "cluster, not already in the production pool"),
        "allowed_leakage_classes": pc["allowed_leakage_classes"],
        "promoted": [{"feature": r["feature"], "label": r["label"],
                      "tier": r["tier"], "family": r["family"],
                      "leakage_class": r["leakage_class"],
                      "direction": r["direction"],
                      "selection_probability": r["sel_prob"],
                      "filtered_importance": round(r["filtered"], 5)}
                     for r in promoted]}
    metrics["promotion"] = {"target": pc["target"],
                            "promoted": [p["feature"] for p in promoted]}

    if history_rows:
        append_history(history_rows)
    state["generated"] = today
    state["config_hash"] = chash
    state["note"] = ("filtered held-out importance per variable (AUC units) "
                     "and normalized weights over admitted variables. Internal.")
    _dump(OUT_WEIGHTS, state)
    if not keep_promotions:
        _dump(OUT_PROMO, promo)
    _dump(OUT_METRICS, metrics)
    if shapes_doc is not None:
        # Written after the promotion decision above, which never reads it.
        _dump(OUT_SHAPES, shapes_doc)
    _write_rank(all_rows)
    _write_report(metrics, all_rows, promo, cfg)

    leaks = []
    for path in (OUT_METRICS, OUT_RANK, OUT_WEIGHTS, OUT_PROMO, OUT_MD) + (
            (OUT_SHAPES,) if shapes_doc is not None else ()):
        for k, line in enumerate(open(path, encoding="utf-8"), 1):
            if LEAK_RE.search(line) or EM_DASH in line:
                leaks.append(f"{os.path.basename(path)}:{k}")
    print("promoted to production pool:", ", ".join(p["feature"] for p in promoted) or "none")
    print("leak audit:", "FAIL " + ", ".join(leaks) if leaks else "clean")
    return 1 if leaks else 0


RANK_FIELDS = ["target", "feature", "label", "family", "tier", "leakage_class",
               "cluster", "cluster_size", "weight", "admitted", "promoted",
               "filtered", "filtered_sd", "observed", "observed_se",
               "perm_mean", "perm_p10", "perm_p90", "cluster_perm_mean",
               "cluster_perm_p10", "sel_prob", "direction", "marginal_auc",
               "drift_z"]


def _read_rank():
    try:
        with open(OUT_RANK, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _write_rank(rows):
    def fmt(v):
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, float):
            return round(v, 6)
        return v
    rows = sorted(rows, key=lambda r: (r["target"], -float(r.get("weight") or 0),
                                       -float(r.get("filtered") or 0)))
    with open(OUT_RANK, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RANK_FIELDS, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: fmt(r.get(k, "")) for k in RANK_FIELDS})


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")


def _write_report(metrics, rows, promo, cfg):
    L = []
    w = L.append
    w("# Adaptive Feature Search")
    w("")
    w("Auto-generated by feature_search.py. Do not edit by hand. Companion "
      "files: feature_search_ranking.csv, feature_search_weights.json, "
      "feature_search_weight_history.csv, feature_search_promotions.json.")
    w("")
    w("**Internal diagnostic. Importance is the held-out AUC a variable "
      "contributes given the others (predictive, not causal). Weights are "
      "filtered across runs and move only as far as the evidence supports. "
      "Opposition variables use the pre-outcome window, which biases their "
      "associations toward zero.**")
    w("")
    w(f"- Candidate pool: {metrics['n_pool']} variables "
      f"(features hash {metrics['features_hash']}, config {metrics['config_hash']}).")
    w(f"- CV: {cfg['cv']['splits']} folds x {cfg['cv']['repeats']} repeats; "
      f"{cfg['permutation_repeats']} permutations per variable per fold; "
      f"clusters at |Spearman rho| >= {cfg['cluster_threshold']}.")
    w("")
    for tname, info in metrics["targets"].items():
        if not info:
            continue
        w(f"## Target: {tname}")
        w("")
        w(f"{info.get('description', '')}. Frame n = {info.get('n_frame')}, "
          f"positive n = {info.get('n_positive')} (base rate "
          f"{100 * (info.get('base_rate') or 0):.1f} pct).")
        w("")
        if info.get("status") != "OK":
            w(f"- {info.get('status')}")
            w("")
            continue
        a = info["held_out_auc"]
        ss = info["stability_selection"]
        w(f"- Held-out AUC, boosted trees on all candidates: "
          f"{a['gbm_all_candidates']['p50']:.3f} (p10 {a['gbm_all_candidates']['p10']:.3f}, "
          f"p90 {a['gbm_all_candidates']['p90']:.3f}); L2 logistic on all "
          f"candidates: {a['l2_logistic_all_candidates']['p50']:.3f}. Context "
          f"only; neither is the production model.")
        if "ebm_all_candidates" in a:
            e = a["ebm_all_candidates"]
            w(f"- Explainable boosting (glassbox challenger, main effects "
              f"only): held-out AUC {e['p50']:.3f} (p10 {e['p10']:.3f}, p90 "
              f"{e['p90']:.3f}). Per-variable shapes are in "
              f"data/feature_search_shapes.json for charting. A challenger: "
              f"promotion never reads it.")
        elif info.get("ebm"):
            w(f"- Explainable boosting: {info['ebm']}.")
        w(f"- {info['n_candidates']} candidates evaluated, {info['n_admitted']} "
          f"admitted. {len(info.get('dropped', {}))} not evaluable in this frame.")
        w(f"- Stability selection: {ss['n_half_sample_fits']} half-sample fits; "
          f"expected false selections at threshold {ss['threshold']}: at most "
          f"{ss['expected_false_selections_bound']}.")
        w(f"- Weights {'updated this run' if info.get('weights_updated') else 'unchanged (inputs identical to the last update)'}."
          + (f" Drift flags: {', '.join(info['drift_flags'])}." if info.get("drift_flags") else ""))
        w("")
        w("| Variable | Family | Tier | Leakage | Weight | Importance (AUC) | Selection prob | Direction |")
        w("|---|---|---|---|---|---|---|---|")
        trows = sorted([r for r in rows if r["target"] == tname],
                       key=lambda r: (-float(r["weight"] or 0), -float(r["filtered"] or 0)))
        for r in trows[:20]:
            w(f"| {r['label']} | {r['family']} | {r['tier']} | {r['leakage_class']} | "
              f"{float(r['weight']):.3f} | {float(r['filtered']):+.4f} | "
              f"{float(r['sel_prob']):.2f} | {r['direction']} |")
        w("")
    w("## Promotion into the production pool")
    w("")
    if promo["promoted"]:
        w(f"Target '{promo['target']}'. Promoted variables enter "
          f"county_policy_model.py as candidates only; its sign-stability rule, "
          f"stability-cost alarm and the calibration gate still decide what ships.")
        w("")
        for p in promo["promoted"]:
            w(f"- {p['label']} ({p['feature']}): {p['direction']}, selection "
              f"probability {p['selection_probability']:.2f}, tier {p['tier']}.")
    else:
        w("No variable met the promotion rule this run.")
    w("")
    w("## Reading this report")
    w("")
    w("- Leakage class 'none' carries no outcome information. "
      "'neighbor_outcome' uses other counties' enacted restrictions (diffusion). "
      "'pre_outcome' uses this county's opposition record truncated at its "
      "first enactment, with instrument records removed.")
    w("- Tier 3 variables also predict press coverage; detection-bias "
      "exposure is reduced on a public-record outcome and still reported.")
    w("- Weight is a share of the total filtered importance of admitted "
      "variables. It says which variables the data currently leans on, not "
      "how much any one of them changes a county's odds.")
    w("")
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    m, v, k = kalman_update(0.0, 1.0, 1.0, 1e-9, 0.0)
    check("a precise observation is taken almost exactly", abs(m - 1.0) < 1e-6)
    m, v, k = kalman_update(0.5, 1e-6, 1.0, 1.0, 0.0)
    check("a noisy observation barely moves a confident state", abs(m - 0.5) < 1e-3)
    m1, v1, _ = kalman_update(0.0, 0.01, 0.1, 0.01, 0.0)
    check("equal precision splits the difference", abs(m1 - 0.05) < 1e-9)
    check("an update never increases uncertainty beyond the prediction", v1 <= 0.01)
    _, _, g_lo = kalman_update(0.0, 0.01, 0.1, 0.01, 0.0)
    _, _, g_hi = kalman_update(0.0, 0.01, 0.1, 0.01, 0.05)
    check("process noise makes the filter adapt faster", g_hi > g_lo)
    m0, v0, _ = kalman_update(0.02, 1e-4, 0.02, 1e-4, 0.0)
    check("a repeated observation leaves the mean in place", abs(m0 - 0.02) < 1e-12)
    # decay: a variable that stops carrying signal shrinks gradually
    s, var = 0.03, 1e-5
    path = []
    for _ in range(4):
        s, var, _ = kalman_update(s, var, 0.0, 4e-5, 1e-5)
        path.append(s)
    check("a vanished signal decays monotonically, not in one step",
          path[0] > path[1] > path[2] > path[3] > 0 and path[0] < 0.03)

    check("drift z is zero for a forecast observation",
          drift_z(0.01, 1e-4, 0.01, 1e-4, 0.0) == 0.0)
    check("drift z flags a large surprise",
          abs(drift_z(0.0, 1e-6, 0.05, 1e-6, 0.0)) > 3)

    check("a cluster helping in 90 pct of folds is admitted",
          is_admitted(0.001, 0.0, 1.0))
    check("a confidently positive filtered state is admitted",
          is_admitted(-0.01, 0.02, 0.005))
    check("a noisy near-zero variable is not admitted",
          not is_admitted(-0.001, 0.002, 0.01))
    check("an absent cluster statistic falls back to the filter",
          is_admitted(None, 0.02, 0.001))

    w = normalize_weights({"a": 0.03, "b": 0.01, "c": 0.05, "d": -0.01}, {"a", "b", "d"})
    check("weights sum to one over admitted variables",
          abs(sum(w.values()) - 1.0) < 1e-6)
    check("an unadmitted variable carries no weight however large", w["c"] == 0.0)
    check("a negative importance carries no weight", w["d"] == 0.0)
    check("weights preserve order", w["a"] > w["b"])
    check("nothing admitted means no weight anywhere",
          all(x == 0 for x in normalize_weights({"a": 1.0}, set()).values()))

    cr = split_cluster_credit(0.04, {"x": 0.01, "y": 0.03})
    check("cluster credit splits by individual importance",
          abs(cr["x"] - 0.01) < 1e-9 and abs(cr["y"] - 0.03) < 1e-9)
    cr = split_cluster_credit(0.04, {"x": 0.0, "y": -0.01})
    check("collinear members that each look inert share the cluster credit",
          abs(cr["x"] - 0.02) < 1e-9 and abs(cr["y"] - 0.02) < 1e-9)
    check("a cluster that does not help gives no credit",
          all(v == 0 for v in split_cluster_credit(-0.01, {"x": 0.02}).values()))

    check("error bound follows the published formula",
          mb_error_bound(4, 40, 0.6) == round(16 / (0.2 * 40), 3))
    check("no bound below a 0.5 threshold", mb_error_bound(4, 40, 0.5) is None)

    ap = admissible_penalties({0.01: 2.0, 0.1: 6.0, 1.0: 30.0}, 50, 0.75, 2.0)
    check("only penalties within the error bound are used",
          sorted(ap) == [0.01, 0.1])
    check("the sparsest penalty survives when none meet the bound",
          admissible_penalties({0.5: 40.0, 1.0: 45.0}, 50, 0.75, 1.0) == [0.5])
    check("consistent positive selection reads positive",
          direction_label(0.95, 0.8, 0.9) == "positive")
    check("consistent negative selection reads negative",
          direction_label(0.02, 0.8, 0.9) == "negative")
    check("a flipping variable reads mixed", direction_label(0.6, 0.8, 0.9) == "mixed")
    check("an unselected variable has no direction",
          direction_label(0.5, 0.0, 0.9) == "not selected")

    cfg = {"stability_selection": {"threshold": 0.6},
           "promotion": {"allowed_leakage_classes": ["none"], "max_promoted": 2,
                         "exclude_families": ["base"]}}

    def row(f, **kw):
        base = {"feature": f, "family": "political", "leakage_class": "none",
                "sel_prob": 0.9, "direction": "positive", "admitted": True,
                "filtered": 0.01, "cluster": f}
        base.update(kw)
        return base

    pool = [row("good", filtered=0.03),
            row("leaky", leakage_class="neighbor_outcome", filtered=0.09),
            row("unstable", direction="mixed", filtered=0.08),
            row("rare", sel_prob=0.4, filtered=0.07),
            row("idle", admitted=False, filtered=0.06),
            row("already", filtered=0.05),
            row("twin_a", cluster="k", filtered=0.02),
            row("twin_b", cluster="k", filtered=0.019),
            row("basevar", family="base", filtered=0.1)]
    got = [r["feature"] for r in choose_promotions(pool, cfg, {"already"})]
    check("promotion excludes outcome-derived variables", "leaky" not in got)
    check("promotion excludes sign-unstable variables", "unstable" not in got)
    check("promotion excludes rarely selected variables", "rare" not in got)
    check("promotion excludes unadmitted variables", "idle" not in got)
    check("promotion never re-promotes a production variable", "already" not in got)
    check("promotion excludes registered families", "basevar" not in got)
    check("promotion takes one variable per cluster",
          not ("twin_a" in got and "twin_b" in got))
    check("promotion ranks by filtered importance and caps the count",
          got == ["good", "twin_a"])

    check("config hash ignores comments",
          config_hash({"a": 1, "_c": "x"}) == config_hash({"a": 1, "_c": "y"}))
    check("config hash sees a setting change",
          config_hash({"a": 1}) != config_hash({"a": 2}))
    check("percentile picks the lower tail", pctile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], .1) == 2)

    with tempfile.TemporaryDirectory() as tmp:
        h = os.path.join(tmp, "data", "h.csv")
        check("absent history reads empty", read_history(h) == [])
        append_history([{"run_date": "2026-09-28", "target": "t", "feature": "a"}], h)
        append_history([{"run_date": "2026-10-05", "target": "t", "feature": "a"}], h)
        rows = read_history(h)
        check("history appends in order", [r["run_date"] for r in rows]
              == ["2026-09-28", "2026-10-05"])
        check("history header written once",
              sum(1 for line in open(h) if line.startswith("run_date")) == 1)

    try:
        import numpy as np
        rng = np.random.default_rng(0)
        a = rng.normal(size=200)
        X = np.column_stack([a, a + rng.normal(scale=0.05, size=200),
                             rng.normal(size=200)])
        cl = cluster_features(X, ["a", "a2", "b"], 0.7)
        check("near-duplicate variables share a cluster", cl["a"] == cl["a2"])
        check("an independent variable sits alone", cl["b"] != cl["a"])
    except ImportError:
        pass

    # EBM challenger (spec 008 US4): one variable drives a logistic target
    # monotonically, two are noise. Its fitted shape must be monotone: scores
    # non-decreasing bin to bin within MONO_TOL (bagging noise between
    # adjacent bins), and rank-correlated with the bin midpoints above 0.95.
    if ebm_available():
        import numpy as np
        from sklearn.model_selection import StratifiedKFold
        MONO_TOL = 0.05
        rng = np.random.default_rng(1)
        n = 4000
        X = np.column_stack([rng.uniform(-3, 3, n), rng.normal(size=n),
                             rng.normal(size=n)])
        y = (rng.uniform(size=n) < 1 / (1 + np.exp(-1.5 * X[:, 0]))).astype(int)
        names = ["mono", "noise_a", "noise_b"]
        folds = list(StratifiedKFold(3, shuffle=True, random_state=0).split(X, y))
        ecfg = {k: v for k, v in json.load(open(CONFIG, encoding="utf-8"))["ebm"].items()
                if not k.startswith("_")}
        res = run_ebm(X, y, names, folds, ecfg, 7, {"mono": "monotone driver"})
        sh = res["shapes"]["mono"]
        sc = sh["scores"]
        mids = [(sh["edges"][k] + sh["edges"][k + 1]) / 2 for k in range(len(sc))]
        rk = lambda v: np.argsort(np.argsort(v))
        rho = float(np.corrcoef(rk(mids), rk(sc))[0, 1])
        check("EBM shape for the monotone driver is non-decreasing",
              all(b >= a - MONO_TOL for a, b in zip(sc, sc[1:])))
        check(f"EBM shape rank-tracks the driver (rho {rho:.3f})", rho > 0.95)
        check("continuous shape has one more edge than scores",
              sh["type"] == "continuous" and len(sh["edges"]) == len(sc) + 1)
        check("the driver outranks the noise variables",
              sh["importance"] > max(res["shapes"][k]["importance"]
                                     for k in ("noise_a", "noise_b")))
        check("EBM held-out AUC is informative", res["auc"]["p50"] > 0.75)
        check("labels carried into shapes", sh["label"] == "monotone driver")
    else:
        print("  SKIP  EBM shape checks: interpret-core not installed")

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--target", default=None,
                    help="run one target; the others keep their last results")
    a = ap.parse_args()
    raise SystemExit(selftest() if a.selftest else main(a.target))
