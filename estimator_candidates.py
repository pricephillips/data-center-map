"""
estimator_candidates.py: challenger estimators scored beside a registered one.

Spec 008, US1. The blocked class in the outcome and landmark frames is small
(about 30 cases), which is where maximum-likelihood logistic regression is
biased and can hit separation. Firth's penalized likelihood is the standard
remedy and keeps coefficients interpretable. This module scores a Firth
logistic regression (firthmodels) on exactly the splits the registered
estimator uses and reports both, one row per estimator per CV repeat.

What it does not do: choose. The registered rules in outcome_model.py and
landmark_model.py have no estimator dimension (the outcome model registers
one estimator; the landmark rule selects a window), so the registered
estimator is always the one that ships. Adopting a challenger is a new
registration entry, dated in the adopting module's docstring, followed by the
calibration gate (Constitution V; spec 008 FR-001).

Sign screen: a coefficient is sign-stable when its sign holds on every fold,
the same test as county_policy_model.is_sign_stable.

Reads nothing and writes only the path its caller passes to write_rows().

  python estimator_candidates.py --selftest
"""

from __future__ import annotations

import csv
import sys
import warnings

try:
    from sklearn.base import BaseEstimator, ClassifierMixin
except ImportError:          # importers' sklearn-free selftests still load
    BaseEstimator = ClassifierMixin = object

REGISTERED = "logistic_l2_registered"
FIRTH = "firth_logistic"
ROW_FIELDS = ["estimator", "role", "window_days", "repeat", "folds_scored",
              "auc_mean", "brier_mean", "max_abs_coef"]
FIRTH_MAX_ITER = 100
SELECTION_NOTE = ("Scored beside the registered estimator on identical "
                  "folds. The registered rule has no estimator dimension, so "
                  "the registered estimator ships; adopting a challenger "
                  "requires a new dated registration entry and the "
                  "calibration gate.")


def firth_available() -> bool:
    try:
        import firthmodels  # noqa: F401
    except ImportError:
        return False
    return True


def _front():
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    # keep_empty_features=True keeps coef_ aligned with the caller's columns
    # when a column is entirely missing in a training fold.
    return [("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler())]


def registered_pipeline(C: float = 0.5, class_weight="balanced", max_iter=2000):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    return Pipeline(_front() + [("clf", LogisticRegression(
        C=C, max_iter=max_iter, class_weight=class_weight))])


def independent_columns(X, tol: float = 1e-8) -> list[int]:
    """Indices of a linearly independent column subset, by pivoted QR.

    A column that is constant within a training fold (all missing, or a rare
    indicator with no positives in the fold) or an exact combination of
    others makes the Firth information matrix singular. Its coefficient is
    not identified by that fold, so it is fit at zero and reported that way.
    """
    import numpy as np
    from scipy.linalg import qr
    Xc = X - X.mean(axis=0)
    if Xc.size == 0:
        return []
    _, R, piv = qr(Xc, mode="economic", pivoting=True)
    d = np.abs(np.diag(R))
    if not d.size or d[0] == 0:
        return []
    rank = int((d > tol * d[0] * max(Xc.shape)).sum())
    return sorted(int(j) for j in piv[:rank])


class FirthOnIdentified(ClassifierMixin, BaseEstimator):
    """Firth logistic regression on the identified columns of each fit.

    Aliased columns get coefficient 0, so coef_ stays aligned with the
    caller's columns (the same contract the registered pipeline keeps with
    keep_empty_features=True).
    """

    def __init__(self, max_iter: int = FIRTH_MAX_ITER):
        self.max_iter = max_iter

    def fit(self, X, y):
        import numpy as np
        from firthmodels import FirthLogisticRegression
        X = np.asarray(X, dtype=float)
        self.cols_ = independent_columns(X)
        self.model_ = FirthLogisticRegression(max_iter=self.max_iter).fit(X[:, self.cols_], y)
        self.coef_ = np.zeros((1, X.shape[1]))
        self.coef_[0, self.cols_] = np.asarray(self.model_.coef_).ravel()
        self.intercept_ = np.atleast_1d(self.model_.intercept_)
        self.classes_ = self.model_.classes_
        self.n_aliased_ = X.shape[1] - len(self.cols_)
        return self

    def predict_proba(self, X):
        import numpy as np
        return self.model_.predict_proba(np.asarray(X, dtype=float)[:, self.cols_])


def firth_pipeline():
    from sklearn.pipeline import Pipeline
    return Pipeline(_front() + [("clf", FirthOnIdentified(max_iter=FIRTH_MAX_ITER))])


def _pct(vals, q):
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))] if s else None


def compare_estimators(X, y, cv, n_folds: int, estimators: dict,
                       window_days=None) -> tuple[list, dict]:
    """Score every estimator on the same cv.split(X, y) folds.

    estimators maps name -> (role, zero-arg factory returning a fresh
    pipeline whose last step is named "clf"). Returns (rows, summary): one
    row per estimator per repeat, and per-estimator AUC percentiles, median
    Brier and sign-stable coefficient counts over all folds.
    """
    import numpy as np
    from sklearn.metrics import brier_score_loss, roc_auc_score

    folds = list(cv.split(X, y))
    rows, summary = [], {}
    for name, (role, factory) in estimators.items():
        per_rep = {}
        aucs, briers, coef_folds = [], [], []
        for i, (tr, te) in enumerate(folds):
            model = factory()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model.fit(X[tr], y[tr])
            p = model.predict_proba(X[te])[:, 1]
            coefs = np.asarray(model.named_steps["clf"].coef_).ravel()
            if len(coefs) != X.shape[1]:
                raise RuntimeError(
                    f"{name} fold {i}: {len(coefs)} coefficients for "
                    f"{X.shape[1]} columns; a pipeline step dropped a column.")
            coef_folds.append(coefs)
            rep = per_rep.setdefault(i // n_folds, {"auc": [], "brier": [], "coef": 0.0})
            if len(set(y[te])) == 2:
                a = float(roc_auc_score(y[te], p))
                rep["auc"].append(a)
                aucs.append(a)
            b = float(brier_score_loss(y[te], p))
            rep["brier"].append(b)
            briers.append(b)
            rep["coef"] = max(rep["coef"], float(np.max(np.abs(coefs))) if len(coefs) else 0.0)
        for r, rep in sorted(per_rep.items()):
            rows.append({
                "estimator": name, "role": role,
                "window_days": "" if window_days is None else window_days,
                "repeat": r, "folds_scored": len(rep["auc"]),
                "auc_mean": round(sum(rep["auc"]) / len(rep["auc"]), 4) if rep["auc"] else "",
                "brier_mean": round(sum(rep["brier"]) / len(rep["brier"]), 4),
                "max_abs_coef": round(rep["coef"], 4)})
        C = np.array(coef_folds)
        stable = int(sum(1 for j in range(C.shape[1])
                         if (C[:, j] > 0).all() or (C[:, j] < 0).all()))
        summary[name] = {
            "role": role,
            "auc": {"p10": round(_pct(aucs, .1), 4), "p50": round(float(np.median(aucs)), 4),
                    "p90": round(_pct(aucs, .9), 4)} if aucs else None,
            "brier_p50": round(float(np.median(briers)), 4),
            "n_sign_stable": stable, "n_coef": int(C.shape[1]),
            "max_abs_coef": round(float(np.max(np.abs(C))), 4),
            "selected": role == "registered"}
    return rows, summary


def standard_estimators(C=0.5, class_weight="balanced", max_iter=2000) -> dict:
    """The registered estimator plus Firth, when firthmodels is importable."""
    est = {REGISTERED: ("registered", lambda: registered_pipeline(C, class_weight, max_iter))}
    if firth_available():
        est[FIRTH] = ("challenger", firth_pipeline)
    return est


def write_rows(path: str, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ROW_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def report_lines(summary: dict, n_repeats: int) -> list[str]:
    """Markdown table shared by the outcome and landmark reports."""
    L = ["| Estimator | Role | AUC median [p10-p90] | Brier median | "
         "Sign-stable coefficients | Largest coefficient | Ships |",
         "|---|---|---|---|---|---|---|"]
    for name, s in summary.items():
        a = s["auc"]
        auc = f"{a['p50']:.3f} [{a['p10']:.3f}-{a['p90']:.3f}]" if a else "n/a"
        L.append(f"| {name} | {s['role']} | {auc} | {s['brier_p50']:.3f} | "
                 f"{s['n_sign_stable']} of {s['n_coef']} | {s['max_abs_coef']:.2f} | "
                 f"{'yes' if s['selected'] else 'no'} |")
    L += ["", f"Per-repeat figures ({n_repeats} repeats per estimator) are in the "
          "estimators CSV. " + SELECTION_NOTE]
    if FIRTH not in summary:
        L += ["", "Firth challenger not scored this run: firthmodels is not installed."]
    return L


def selftest() -> int:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import RepeatedStratifiedKFold

    fails = []

    def check(name, cond):
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    rng = np.random.RandomState(0)
    # Separable fixture: the label is exactly x0 > 0.
    Xs = rng.randn(30, 2)
    ys = (Xs[:, 0] > 0).astype(int)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ml = LogisticRegression(C=1e10, max_iter=10000).fit(Xs, ys)
    check("unpenalized logistic diverges on a separable frame",
          float(np.max(np.abs(ml.coef_))) > 50)

    if firth_available():
        from firthmodels import FirthLogisticRegression
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fm = FirthLogisticRegression(max_iter=FIRTH_MAX_ITER).fit(Xs, ys)
        c = np.asarray(fm.coef_).ravel()
        check("Firth returns finite, bounded coefficients",
              bool(np.isfinite(c).all()) and float(np.max(np.abs(c))) < 20)
    else:
        print("SKIP Firth fit checks: firthmodels not installed")

    if firth_available():
        Xa = np.column_stack([Xs, Xs[:, 1], np.zeros(30)])   # duplicate + constant
        ya = (Xs[:, 1] + rng.randn(30) > 0).astype(int)
        fa = FirthOnIdentified().fit(Xa, ya)
        check("aliased and constant columns are fit at zero, coef_ stays aligned",
              fa.coef_.shape == (1, 4) and fa.n_aliased_ == 2
              and fa.coef_[0, 3] == 0.0 and np.isfinite(fa.coef_).all())
    check("independent_columns drops a duplicate and a constant",
          len(independent_columns(np.column_stack([Xs, Xs[:, 0] * 2, np.ones(30)]))) == 2)

    # Comparison returns one row per estimator per repeat, on shared folds.
    X = rng.randn(80, 3)
    X[rng.rand(80, 3) < 0.1] = np.nan
    y = (np.nan_to_num(X[:, 0]) + 0.8 * rng.randn(80) > 0.6).astype(int)
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=3, random_state=1)
    est = standard_estimators()
    rows, summary = compare_estimators(X, y, cv, 5, est)
    check("one row per estimator per repeat", len(rows) == 3 * len(est)
          and all(sum(1 for r in rows if r["estimator"] == e) == 3 for e in est))
    check("only the registered estimator ships",
          [n for n, s in summary.items() if s["selected"]] == [REGISTERED])
    check("registered AUC is informative on a signal frame",
          summary[REGISTERED]["auc"]["p50"] > 0.7)
    check("x0 coefficient is sign-stable", summary[REGISTERED]["n_sign_stable"] >= 1)
    rows2, _ = compare_estimators(X, y, cv, 5, est)
    check("deterministic across calls", rows == rows2)
    lines = report_lines(summary, 3)
    check("report table names every estimator",
          all(any(n in ln for ln in lines) for n in est))

    print(f"{'PASS' if not fails else 'FAIL'}: estimator_candidates selftest")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
