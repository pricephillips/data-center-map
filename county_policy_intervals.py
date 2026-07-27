"""
county_policy_intervals.py

Conformal prediction sets for the county enacted-restriction model, via MAPIE
cross-conformal classification. Companion to county_policy_model.py; run after
it, never instead of it.

Two products, in order of usefulness here
-----------------------------------------
1. Venn-Abers probability intervals (MAPIE VennAbersCalibrator, cross mode):
   a per-county interval [p_lower, p_upper] on the enacted-restriction
   probability with a distribution-free validity guarantee. This is the
   informative object at a 6 pct base rate, and the direct counterpart of the
   calibrated_score already on the map.
2. LAC conformal prediction sets (MAPIE CrossConformalClassifier): kept and
   reported, with their limitation stated rather than hidden. On this frame
   the marginal guarantee is satisfied almost entirely by the majority class:
   class-conditional coverage of the enacted class is at or near zero, so the
   sets say little about exactly the counties that matter. That is a real
   finding about marginal conformal classification under imbalance, recorded
   in the report, not a bug in the computation.

What this adds and what it does not
-----------------------------------
county_policy_model.py already ships a cross-fitted calibrated probability per
county. What that number cannot say is which conclusions the evidence actually
supports for an individual county at a stated confidence level. Conformal
prediction sets say exactly that, with a distribution-free coverage guarantee:
at 90 pct confidence, the set contains the county's true class for at least
90 pct of counties, on average, under exchangeability.

Each county gets a set at each confidence level, reported as one of:

  none_supported      set is {no enacted restriction}: the profile is
                      consistent only with the absence of an enacted
                      restriction at this confidence
  enacted_supported   set is {enacted restriction}: consistent only with one
                      being on record
  indeterminate       set is both classes: the evidence cannot rule out either
  atypical            set is empty: the profile is atypical of BOTH classes at
                      this level. A statement about the county being unusual,
                      not a statement that neither outcome is possible.

Honesty constraints, stated up front because they will be asked about:

- The guarantee is MARGINAL, averaged over counties. It is not per-county and
  it is not per-class. With a base rate near 6 pct, coverage on the enacted
  class specifically can be materially below the nominal level, and the report
  prints that class-conditional number rather than hiding it.
- Sets are computed on the same counties the model trains on, using
  cross-conformal thresholds (MAPIE CrossConformalClassifier over the same
  stratified fold design and seed as the score model). That is the standard
  construction for reporting on a fixed frame; it is not a fresh holdout.
- The model predicts whether a county's profile matches counties that have
  enacted restrictions. It does not predict whether a new project will draw
  opposition, and neither do these sets.
- MAPIE supports only the LAC conformity score for binary targets, which is
  why empty sets can occur and are labeled rather than suppressed.

The estimator is rebuilt exactly from the metrics JSON that
county_policy_model.py writes: same selected variables, same C, same
transforms, same pipeline. If the metrics file is missing or the selected
variables are absent from the aggregate, this module refuses to run rather
than silently fitting a different specification.

Scope note from the roadmap correction: conformal intervals need a model with
a ground-truth target. This module therefore wraps county_policy_model.py
ONLY. The site screener composite is a percentile rank with no target and
cannot be conformalized; the outcome model can be, but near-chance
discrimination will produce mostly indeterminate sets, which is the honest
result to expect if it is ever wrapped.

Usage
-----
  python county_policy_intervals.py
  python county_policy_intervals.py --confidence 0.9 0.8
  python county_policy_intervals.py --selftest

Outputs
-------
  data/county_policy_intervals.csv   fips, calibrated_score, set_90, set_80,
                                     outcome, covered_90, covered_80
  data/county_policy_intervals.md    coverage table incl. class-conditional,
                                     set-size distribution, method notes
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(ROOT, *parts)


AGG_CSV = P("data", "county_aggregate.csv")
METRICS_JSON = P("data", "county_policy_metrics.json")
SCORES_CSV = P("data", "county_policy_scores.csv")
OUT_CSV = P("data", "county_policy_intervals.csv")
OUT_MD = P("data", "county_policy_intervals.md")

DEFAULT_CONFIDENCE = (0.9, 0.8)
SEED = 7          # matches county_policy_model.py
N_SPLITS = 5      # matches county_policy_model.py

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

SET_LABELS = {
    (True, False): "none_supported",
    (False, True): "enacted_supported",
    (True, True): "indeterminate",
    (False, False): "atypical",
}


def set_label(row):
    """row is the (n_classes,) boolean slice of a MAPIE prediction set for one
    county at one confidence level; class 0 = no enacted restriction."""
    return SET_LABELS[(bool(row[0]), bool(row[1]))]


# ---------------------------------------------------------------------------
# Frame construction: reuse the score model's own code, refuse to diverge
# ---------------------------------------------------------------------------

def load_spec():
    if not os.path.exists(METRICS_JSON):
        raise FileNotFoundError(
            f"{METRICS_JSON} not found. Run county_policy_model.py first; "
            f"this module conformalizes that model and must not select its "
            f"own specification.")
    m = json.load(open(METRICS_JSON, encoding="utf-8"))
    sel = m["specification_selection"]["selected"]
    return list(sel["variables"]), float(sel["C"]), m


def build_frame(variables):
    import numpy as np
    import county_policy_model as CPM

    rows, n_excluded_pr = CPM.load()
    fips = [r["fips"] for r in rows]
    y = np.array([1 if str(r.get("has_enacted_restrictive", "")).strip()
                  in ("1", "True", "true") else 0 for r in rows])
    tf = {name: t for (name, _label, _tier, t) in CPM.VARS}
    missing = [v for v in variables if v not in tf]
    if missing:
        raise ValueError(
            f"selected variables {missing} are not defined in "
            f"county_policy_model.VARS; the two modules have diverged.")
    X = np.full((len(rows), len(variables)), np.nan)
    for j, v in enumerate(variables):
        for i, r in enumerate(rows):
            val = CPM.parse_float(r.get(v))
            if val is not None:
                X[i, j] = CPM.transform(tf[v], val)
    return fips, X, y, n_excluded_pr


def _quiet():
    """MAPIE's isotonic internals emit benign divide-by-zero RuntimeWarnings
    while computing Venn-Abers slopes. Silence exactly those, nothing else."""
    import warnings
    warnings.filterwarnings(
        "ignore", category=RuntimeWarning, module=r"mapie\._venn_abers")


def build_estimator(c_reg):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(),
        LogisticRegression(C=c_reg, max_iter=4000, random_state=SEED))


def conformal_sets(X, y, c_reg, confidence):
    import numpy as np
    from sklearn.model_selection import StratifiedKFold
    from mapie.classification import CrossConformalClassifier

    cv = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=SEED)
    cc = CrossConformalClassifier(
        estimator=build_estimator(c_reg),
        confidence_level=list(confidence),
        conformity_score="lac",       # only valid score for binary targets
        cv=cv, random_state=SEED)
    cc.fit_conformalize(X, y)
    _pred, sets = cc.predict_set(X)   # (n, n_classes, n_confidence)
    return np.asarray(sets, dtype=bool)


def venn_abers_intervals(X, y, c_reg):
    """Cross Venn-Abers (CVAP) probability intervals per county.

    Returns (point, lower, upper). predict_proba(p0_p1_output=True) yields an
    array of shape (n, 2 * n_splits): the first n_splits columns are p0 per
    fold and the last n_splits are p1 per fold (verified against the invariant
    p0 <= p1 within every fold). The shipped interval is the conservative
    envelope [min p0, max p1] across folds, which always contains the CVAP
    point probability; tightness is traded for defensibility on purpose.
    """
    import numpy as np
    from mapie.calibration import VennAbersCalibrator

    _quiet()
    va = VennAbersCalibrator(estimator=build_estimator(c_reg),
                             inductive=False, n_splits=N_SPLITS,
                             random_state=SEED)
    va.fit(X, y)
    point, p0p1 = va.predict_proba(X, p0_p1_output=True)
    a = np.asarray(p0p1)[0]
    k = a.shape[1] // 2
    p0s, p1s = a[:, :k], a[:, k:]
    if not bool((p0s <= p1s + 1e-9).all()):
        raise RuntimeError(
            "Venn-Abers output layout is not p0-columns-then-p1-columns; "
            "MAPIE internals changed. Refusing to ship intervals built on a "
            "guessed layout.")
    lower = np.minimum(p0s.min(axis=1), point[:, 1])
    upper = np.maximum(p1s.max(axis=1), point[:, 1])
    return point[:, 1], lower, upper


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def coverage_stats(sets, y, confidence):
    """Marginal and class-conditional empirical coverage per level."""
    import numpy as np
    out = []
    for j, cl in enumerate(confidence):
        contains = np.array([sets[i, y[i], j] for i in range(len(y))])
        pos, neg = (y == 1), (y == 0)
        labels = Counter(set_label(sets[i, :, j]) for i in range(len(y)))
        out.append({
            "confidence": cl,
            "marginal": float(contains.mean()),
            "enacted_class": float(contains[pos].mean()) if pos.any() else None,
            "none_class": float(contains[neg].mean()) if neg.any() else None,
            "labels": labels,
        })
    return out


def load_calibrated_scores():
    out = {}
    if os.path.exists(SCORES_CSV):
        for r in csv.DictReader(open(SCORES_CSV, encoding="utf-8-sig")):
            out[r["fips"]] = r.get("calibrated_score", "")
    return out


def write_outputs(fips, y, sets, confidence, stats, meta, va=None):
    cal = load_calibrated_scores()
    lvl_cols = [f"set_{int(round(c * 100))}" for c in confidence]
    cov_cols = [f"covered_{int(round(c * 100))}" for c in confidence]
    va_cols = ["va_p_lower", "va_p_upper", "va_width"] if va else []

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fips", "calibrated_score", "has_enacted_restrictive"]
                   + va_cols + lvl_cols + cov_cols)
        for i, f in enumerate(fips):
            row = [f, cal.get(f, ""), int(y[i])]
            if va:
                _pt, lo, hi = va
                row += [round(float(lo[i]), 4), round(float(hi[i]), 4),
                        round(float(hi[i] - lo[i]), 4)]
            row += [set_label(sets[i, :, j]) for j in range(len(confidence))]
            row += [int(sets[i, y[i], j]) for j in range(len(confidence))]
            w.writerow(row)

    L = []
    w = L.append
    w("# County Enacted-Restriction Model: Conformal Prediction Sets")
    w("")
    w("Auto-generated by county_policy_intervals.py. Companion to "
      "county_policy_metrics.json and county_policy_scores.csv; rerun after "
      "county_policy_model.py.")
    w("")
    w("**Internal diagnostic. The guarantee is marginal coverage over "
      "counties, not per-county and not per-class. Sets describe which "
      "outcomes the evidence supports for a county's profile at a stated "
      "confidence; they are not probabilities that a new project draws "
      "opposition.**")
    w("")
    w("## Method")
    w("")
    w(f"- MAPIE {meta['mapie_version']} CrossConformalClassifier, LAC "
      f"conformity score (the only score MAPIE supports for a binary "
      f"target), {N_SPLITS}-fold stratified cross-conformal, seed {SEED}: "
      f"the same fold design and seed as the score model.")
    w(f"- Estimator rebuilt from the metrics JSON: spec "
      f"`{meta['spec_name']}`, C = {meta['c_reg']}, variables "
      f"{meta['n_vars']}. This module performs no specification selection "
      f"of its own.")
    w(f"- Frame: {len(fips)} counties, {int(y.sum())} with an enacted "
      f"restriction (base rate {y.mean():.3f}). Puerto Rico excluded, as in "
      f"the score model.")
    w("- Sets are computed on the model's own frame with cross-conformal "
      "thresholds. This is the standard construction for a fixed frame; it "
      "is not a fresh holdout.")
    w("")
    w("## Set interpretation")
    w("")
    w("| label | set contents | reading |")
    w("| :-- | :-- | :-- |")
    w("| `none_supported` | no-restriction only | profile consistent only "
      "with no enacted restriction at this confidence |")
    w("| `enacted_supported` | enacted only | profile consistent only with "
      "an enacted restriction on record |")
    w("| `indeterminate` | both classes | the evidence cannot rule out "
      "either outcome at this confidence |")
    w("| `atypical` | empty | profile atypical of both classes at this "
      "level; a statement of unusualness, not impossibility |")
    w("")
    if va:
        import numpy as np
        _pt, lo, hi = va
        width = hi - lo
        w("## Venn-Abers probability intervals (primary product)")
        w("")
        w("Per-county interval on the enacted-restriction probability, cross "
          "Venn-Abers over the same folds and seed as the score model. The "
          "interval is the conservative envelope across folds and always "
          "contains the Venn-Abers point probability. Validity is "
          "distribution-free; unlike the LAC sets below, informativeness "
          "does not collapse under the low base rate.")
        w("")
        w("| statistic | value |")
        w("| :-- | --: |")
        w(f"| median interval width | {float(np.median(width)):.4f} |")
        w(f"| p90 interval width | {float(np.quantile(width, .9)):.4f} |")
        w(f"| max interval width | {float(width.max()):.4f} |")
        w(f"| counties with width over 0.10 | {int((width > 0.10).sum())} |")
        _pos = (y == 1)
        w(f"| median width, counties with an enacted restriction | "
          f"{float(np.median(width[_pos])):.4f} |")
        w("")
        w("A wide interval is the model saying it does not know, which is "
          "information: those counties are where the enacted-restriction "
          "evidence is thinnest relative to profile. Any external use quotes "
          "the interval, never the point alone.")
        w("")
    w("## Results by confidence level")
    w("")
    for s in stats:
        pct = int(round(s["confidence"] * 100))
        w(f"### {pct} pct confidence")
        w("")
        w("| label | counties | share |")
        w("| :-- | --: | --: |")
        n = len(fips)
        for lab in ("none_supported", "enacted_supported", "indeterminate",
                    "atypical"):
            k = s["labels"].get(lab, 0)
            w(f"| `{lab}` | {k} | {k / n:.1%} |")
        w("")
        w(f"- Empirical marginal coverage: {s['marginal']:.3f} "
          f"(nominal {s['confidence']:.2f})")
        w(f"- Coverage on counties WITH an enacted restriction: "
          f"{s['enacted_class']:.3f}")
        w(f"- Coverage on counties without: {s['none_class']:.3f}")
        w("")
    w("The class-conditional gap is the expected behavior of marginal "
      "conformal prediction under a low base rate: the minority class is "
      "covered less often than the nominal level, and on this frame the "
      "enacted class is covered at or near zero. The LAC sets are retained "
      "as documentation of that limitation; the Venn-Abers intervals above "
      "are the object to use. Any external use of either must carry the "
      "marginal caveat.")
    w("")
    text = "\n".join(L)
    if LEAK_RE.search(text):
        raise RuntimeError("leak audit failed on generated report")
    if "\u2014" in text:
        raise RuntimeError("em-dash found in generated report")
    open(OUT_MD, "w", encoding="utf-8").write(text)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    import numpy as np
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            ok = False

    check("set labels cover all four shapes",
          set_label([True, False]) == "none_supported" and
          set_label([False, True]) == "enacted_supported" and
          set_label([True, True]) == "indeterminate" and
          set_label([False, False]) == "atypical")

    rng = np.random.default_rng(SEED)
    n = 600
    X = rng.normal(size=(n, 4))
    logits = 1.8 * X[:, 0] - 2.6
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-logits))).astype(int)
    check("synthetic frame has a low base rate", 0.02 < y.mean() < 0.25)

    sets = conformal_sets(X, y, c_reg=1.0, confidence=(0.9, 0.8))
    check("set tensor shape", sets.shape == (n, 2, 2))

    stats = coverage_stats(sets, y, (0.9, 0.8))
    check("marginal coverage holds at 90",
          stats[0]["marginal"] >= 0.9 - 0.03)
    check("marginal coverage holds at 80",
          stats[1]["marginal"] >= 0.8 - 0.04)
    check("80 pct sets are never wider than 90 pct sets",
          all(sets[i, :, 1].sum() <= sets[i, :, 0].sum() for i in range(n)))
    check("some sets are decisive at 90",
          stats[0]["labels"].get("none_supported", 0) > 0)

    # determinism
    sets2 = conformal_sets(X, y, c_reg=1.0, confidence=(0.9, 0.8))
    check("deterministic under the fixed seed", bool((sets == sets2).all()))

    pt, lo, hi = venn_abers_intervals(X, y, c_reg=1.0)
    check("venn-abers interval contains its point probability",
          bool(((pt >= lo - 1e-9) & (pt <= hi + 1e-9)).all()))
    check("venn-abers bounds are probabilities",
          bool((lo >= -1e-9).all() and (hi <= 1 + 1e-9).all()))
    check("venn-abers intervals are informative on the synthetic frame",
          float(np.median(hi - lo)) < 0.25)
    pt2, lo2, hi2 = venn_abers_intervals(X, y, c_reg=1.0)
    check("venn-abers deterministic under the fixed seed",
          bool((lo == lo2).all() and (hi == hi2).all()))
    # empirical validity: observed rate among high-upper counties exceeds
    # observed rate among low-upper counties (monotone usefulness check)
    import numpy as _np
    order = _np.argsort(hi)
    lo_half, hi_half = y[order[: n // 2]].mean(), y[order[n // 2:]].mean()
    check("higher intervals track higher observed rates", hi_half > lo_half)

    # report generation and audits on a temp copy of paths
    global OUT_CSV, OUT_MD
    import tempfile
    keep_csv, keep_md = OUT_CSV, OUT_MD
    td = tempfile.mkdtemp()
    OUT_CSV, OUT_MD = (os.path.join(td, "i.csv"), os.path.join(td, "i.md"))
    try:
        meta = {"mapie_version": "test", "spec_name": "test", "c_reg": 1.0,
                "n_vars": 4}
        write_outputs([f"f{i:05d}" for i in range(n)], y, sets, (0.9, 0.8),
                      stats, meta, va=(pt, lo, hi))
        rows = list(csv.DictReader(open(OUT_CSV)))
        check("interval CSV row per county", len(rows) == n)
        check("interval CSV carries both levels",
              "set_90" in rows[0] and "set_80" in rows[0])
        check("interval CSV carries venn-abers bounds",
              "va_p_lower" in rows[0] and "va_p_upper" in rows[0])
        check("va bounds ordered in CSV",
              all(float(r["va_p_lower"]) <= float(r["va_p_upper"]) + 1e-9
                  for r in rows))
        md = open(OUT_MD, encoding="utf-8").read()
        check("report passes leak audit", not LEAK_RE.search(md))
        check("report has no em-dash", "\u2014" not in md)
        check("report states the marginal caveat", "marginal" in md)
    finally:
        OUT_CSV, OUT_MD = keep_csv, keep_md

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confidence", nargs="+", type=float,
                    default=list(DEFAULT_CONFIDENCE))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    try:
        import mapie
    except ImportError:
        print("county_policy_intervals: mapie is not installed "
              "(pip install mapie); no outputs written.")
        return 1

    for c in a.confidence:
        if not 0.5 < c < 1.0:
            print(f"county_policy_intervals: confidence {c} outside (0.5, 1)")
            return 1
    confidence = tuple(sorted(a.confidence, reverse=True))

    variables, c_reg, metrics = load_spec()
    fips, X, y, _n_pr = build_frame(variables)
    va = venn_abers_intervals(X, y, c_reg)
    sets = conformal_sets(X, y, c_reg, confidence)
    stats = coverage_stats(sets, y, confidence)
    meta = {"mapie_version": mapie.__version__,
            "spec_name": metrics["specification_selection"]["selected"]["spec"],
            "c_reg": c_reg, "n_vars": len(variables)}
    write_outputs(fips, y, sets, confidence, stats, meta, va=va)

    import numpy as np
    _pt, _lo, _hi = va
    _w = _hi - _lo
    print(f"county_policy_intervals: {len(fips)} counties, "
          f"spec `{meta['spec_name']}` C={c_reg}")
    print(f"  venn-abers: median width {float(np.median(_w)):.4f} | "
          f"p90 {float(np.quantile(_w, .9)):.4f} | "
          f"over 0.10 wide: {int((_w > 0.10).sum())}")
    for s in stats:
        pct = int(round(s["confidence"] * 100))
        lab = s["labels"]
        print(f"  {pct} pct: none {lab.get('none_supported', 0)} | "
              f"enacted {lab.get('enacted_supported', 0)} | "
              f"indeterminate {lab.get('indeterminate', 0)} | "
              f"atypical {lab.get('atypical', 0)} | "
              f"marginal coverage {s['marginal']:.3f} | "
              f"enacted-class coverage {s['enacted_class']:.3f}")
    print(f"  wrote {OUT_CSV}")
    print(f"  wrote {OUT_MD}")
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
