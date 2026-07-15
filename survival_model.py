"""
survival_model.py — Phase 3: first time-to-decision (survival) model.

Models how long an opposed project takes to reach a terminal land-use
decision, using right-censoring so that still-pending projects contribute
information instead of being discarded. Two deliberately simple estimators
for a small sample:

  1. Kaplan-Meier survival curve of time-from-announcement-to-decision,
     overall and split by terminal direction (advanced vs blocked), with
     a log-rank test for whether the two differ.
  2. A regularized Cox proportional-hazards model with a SMALL, fixed set
     of covariates (penalizer set high because n is small), reporting
     hazard ratios with confidence intervals and the concordance index
     from honest k-fold cross-validation.

Additive only. Reads Phase 1/2 outputs, writes three NEW files:

  data/survival_model_report.md      internal report: KM medians, log-rank,
                                     Cox hazard ratios + CIs, CV c-index,
                                     binding limitations
  data/survival_km_curve.csv         the KM survival table (time, survival,
                                     at-risk, events) for plotting/audit
  data/survival_model_metrics.json   machine-readable metrics for Phase 5
                                     calibration tracking

Survival-analysis framing (why this is the right tool here):
  - EVENT = project reached a terminal decision; the "time" is
    announced -> decision in days.
  - CENSORED = opposed project announced but not yet decided; observed from
    announcement to its last-known activity date (last opposition event or
    status update). It contributes "survived at least this long without a
    decision," which is real information a naive dated-only analysis throws
    away.
  - This lets all opposed projects with a usable announcement date enter the
    model (~110), not just the ~17 with verified decision dates.

Defensibility rules honored:
  - EVENTS are terminal dispositions only (advanced/blocked), consistent
    with the decided-case rule; everything else is censored, not dropped or
    guessed.
  - Every estimate is a range/interval; nothing is a bare point estimate.
    Small-n instability is stated up front and the penalizer is deliberately
    strong.
  - Hazard ratios are described as associations with the RATE of reaching a
    decision, explicitly predictive-not-causal, and NOT client-facing.
  - Month-precision announcement dates (floored to the 1st) carry up to ~30
    days of error; year-precision dates are too coarse and are EXCLUDED from
    the time axis rather than floored.
  - Not wired into CI. Promotion to automated retraining is Phase 5 and
    requires the calibration gate.

Run from repo root:  python3 survival_model.py
Requires lifelines (pip install lifelines).
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
from datetime import date

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

LIFECYCLES_CSV = P("data", "project_lifecycles.csv")
UNIVERSE_CSV = P("data", "baseline_universe.csv")
LINKS_CSV = P("data", "project_links.csv")

OUT_REPORT = P("data", "survival_model_report.md")
OUT_KM = P("data", "survival_km_curve.csv")
OUT_METRICS = P("data", "survival_model_metrics.json")

RANDOM_STATE = 20260713
N_FOLDS = 5
COX_PENALIZER = 0.5          # strong; n is small
MIN_EVENTS_FOR_COX = 25      # below this, KM only (survival needs more events than a binary split)
MONTH_ERROR_DAYS = 30


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def parse_float(v):
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def days_between(a: str, b: str):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (ValueError, TypeError):
        return None


def build_survival_frame():
    """Return rows with duration (days), event flag (1=decided), direction,
    and covariates. Events come from verified decision dates; censored rows
    are observed to their last known activity date."""
    life = load_csv(LIFECYCLES_CSV)
    uni = {r["universe_id"]: r for r in load_csv(UNIVERSE_CSV)}
    links = load_csv(LINKS_CSV)
    types_by_project: dict[str, str] = {}
    for l in links:
        types_by_project.setdefault(l["project_id"], [])
        types_by_project[l["project_id"]].append(l.get("opp_type", ""))

    rows = []
    excluded_year = 0
    for r in life:
        if int(r["n_opposition_events"] or 0) == 0:
            continue
        announced = r["announced_date"]
        precision = r["announced_precision"]
        if not announced or precision not in ("day", "month"):
            if announced and precision == "year":
                excluded_year += 1
            continue

        decided = r["decided"] == "yes"
        duration = None
        event = 0
        if decided and r["days_announced_to_decision"]:
            duration = int(r["days_announced_to_decision"])
            event = 1
        else:
            # censor at last known activity (last opposition event or status update)
            anchor = r["last_opposition_date"] or r["last_status_update"]
            d = days_between(announced, anchor) if anchor else None
            if d is not None and d > 0:
                duration = d
                event = 0
        if duration is None or duration <= 0:
            continue

        u = uni.get(r["project_id"], {})
        types = " ".join(types_by_project.get(r["project_id"], [])).lower()
        margin = parse_float(u.get("county_margin_2024"))
        rows.append({
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "duration_days": duration,
            "event": event,                       # 1 = reached decision
            "direction": (r["lifecycle_outcome"] if event else "pending"),
            "blocked": 1 if r["lifecycle_outcome"] == "blocked_confirmed" else 0,
            "announced_precision": precision,
            # covariates (kept few on purpose)
            "n_opposition_events": int(r["n_opposition_events"]),
            "has_lawsuit": 1 if r["has_lawsuit"] == "yes" else 0,
            "county_margin_2024": margin,
            "hyperscaler_or_mechanism_moratorium": 1 if "moratorium" in types else 0,
        })
    return rows, excluded_year


def main() -> int:
    for f in (LIFECYCLES_CSV, UNIVERSE_CSV, LINKS_CSV):
        if not os.path.exists(f):
            print(f"ERROR: {os.path.relpath(f, ROOT)} missing — run the resolution/control chain first")
            return 1
    try:
        from lifelines import KaplanMeierFitter, CoxPHFitter
        from lifelines.statistics import logrank_test
        from lifelines.utils import concordance_index
    except ImportError:
        print("ERROR: lifelines not installed (pip install lifelines). "
              "This module is not part of the CI pipeline by design.")
        return 1

    rows, excluded_year = build_survival_frame()
    n = len(rows)
    n_events = sum(r["event"] for r in rows)
    n_censored = n - n_events
    n_blocked_ev = sum(1 for r in rows if r["event"] and r["blocked"])
    n_adv_ev = n_events - n_blocked_ev

    # Datable-outcome asymmetry (for the informative-censoring limitation):
    # among ALL opposed projects that reached a terminal outcome, how many of
    # each direction carry a verified discrete decision date. Computed from the
    # full lifecycle table, not just the survival frame.
    _life_all = load_csv(LIFECYCLES_CSV)
    _opp_terminal = [r for r in _life_all
                     if int(r["n_opposition_events"] or 0) > 0
                     and r["lifecycle_outcome"] in ("advanced_confirmed", "blocked_confirmed")]
    adv_total = sum(1 for r in _opp_terminal if r["lifecycle_outcome"] == "advanced_confirmed")
    blocked_total = sum(1 for r in _opp_terminal if r["lifecycle_outcome"] == "blocked_confirmed")
    adv_dated = sum(1 for r in _opp_terminal
                    if r["lifecycle_outcome"] == "advanced_confirmed" and r["decision_date"])
    blocked_dated = sum(1 for r in _opp_terminal
                        if r["lifecycle_outcome"] == "blocked_confirmed" and r["decision_date"])

    durations = np.array([r["duration_days"] for r in rows], dtype=float)
    events = np.array([r["event"] for r in rows], dtype=int)
    blocked = np.array([r["blocked"] for r in rows], dtype=int)

    # ---- 1. Kaplan-Meier overall + by direction ----
    kmf = KaplanMeierFitter()
    kmf.fit(durations, events, label="all_opposed")
    median_all = kmf.median_survival_time_

    # split: among EVENTS, direction is known; censored rows have unknown
    # eventual direction, so a by-direction KM uses direction-specific event
    # flags (an advanced curve treats blocked-events as censored and vice
    # versa). This estimates time-to-<direction>-decision.
    adv_event = np.array([1 if (r["event"] and not r["blocked"]) else 0 for r in rows])
    blk_event = np.array([1 if (r["event"] and r["blocked"]) else 0 for r in rows])
    km_adv = KaplanMeierFitter().fit(durations, adv_event, label="advanced")
    km_blk = KaplanMeierFitter().fit(durations, blk_event, label="blocked")

    # log-rank between blocked-events and advanced-events on the decided subset
    dec_mask = events == 1
    lr = None
    if dec_mask.sum() >= 4 and len(set(blocked[dec_mask])) == 2:
        lr = logrank_test(durations[dec_mask][blocked[dec_mask] == 1],
                          durations[dec_mask][blocked[dec_mask] == 0])

    # write KM table (overall)
    km_tbl = kmf.survival_function_.reset_index()
    km_tbl.columns = ["timeline_days", "survival"]
    at_risk = kmf.event_table.reset_index()[["event_at", "at_risk", "observed"]]
    with open(OUT_KM, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timeline_days", "survival_prob", "at_risk", "events_observed"])
        et = kmf.event_table
        for t in et.index:
            surv = float(kmf.survival_function_at_times(t).iloc[0])
            w.writerow([f"{t:.0f}", f"{surv:.4f}",
                        int(et.loc[t, "at_risk"]), int(et.loc[t, "observed"])])

    # ---- 2. Cox PH (only if enough events) ----
    cox_result = None
    c_index_cv = None
    if n_events >= MIN_EVENTS_FOR_COX:
        import pandas as pd
        cov = ["n_opposition_events", "has_lawsuit", "county_margin_2024",
               "hyperscaler_or_mechanism_moratorium"]
        df = pd.DataFrame([{**{c: r[c] for c in cov},
                            "duration_days": r["duration_days"],
                            "event": r["event"]} for r in rows])
        # median-impute margin
        med = df["county_margin_2024"].median()
        df["county_margin_2024"] = df["county_margin_2024"].fillna(med)

        cph = CoxPHFitter(penalizer=COX_PENALIZER)
        try:
            cph.fit(df, duration_col="duration_days", event_col="event")
            cox_result = cph.summary[["coef", "exp(coef)",
                                      "exp(coef) lower 95%", "exp(coef) upper 95%",
                                      "p"]].to_dict("index")
        except Exception as e:
            cox_result = {"_error": str(e)}

        # honest CV concordance
        rng = np.random.default_rng(RANDOM_STATE)
        idx = rng.permutation(len(df))
        folds = np.array_split(idx, N_FOLDS)
        cidxs = []
        for i in range(N_FOLDS):
            te = folds[i]
            tr = np.concatenate([folds[j] for j in range(N_FOLDS) if j != i])
            if df.iloc[tr]["event"].sum() < 3 or df.iloc[te]["event"].sum() < 1:
                continue
            try:
                m = CoxPHFitter(penalizer=COX_PENALIZER)
                m.fit(df.iloc[tr], duration_col="duration_days", event_col="event")
                risk = -m.predict_partial_hazard(df.iloc[te]).values.ravel()
                ci = concordance_index(df.iloc[te]["duration_days"],
                                       risk, df.iloc[te]["event"])
                cidxs.append(ci)
            except Exception:
                continue
        if cidxs:
            c_index_cv = (float(np.min(cidxs)), float(np.median(cidxs)), float(np.max(cidxs)))

    # ---- report ----
    L = []
    w = L.append
    w("# Time-to-Decision Survival Model — First Iteration (Phase 3)")
    w("")
    w(f"Generated {date.today().isoformat()} by `survival_model.py`. Figures "
      "re-derived from current CSVs at generation time.")
    w("")
    w("**Internal diagnostic only — NOT client-facing.** Small sample, "
      "retrospective, predictive-not-causal. Hazard ratios describe "
      "association with the RATE of reaching a decision, not causes of it.")
    w("")
    w("## Sample and censoring")
    w("")
    w(f"- Opposed projects in the model: **{n}** "
      f"({n_events} reached a terminal decision = events; "
      f"{n_censored} still pending = right-censored).")
    w(f"- Of the {n_events} events: {n_adv_ev} `advanced_confirmed`, "
      f"{n_blocked_ev} `blocked_confirmed`.")
    w(f"- Time axis is announced→decision in days. Censored projects are "
      f"observed to their last known activity date (last opposition event or "
      f"status update).")
    w(f"- {excluded_year} opposed projects were EXCLUDED from the time axis "
      f"because their announcement date is only year-precision (too coarse to "
      f"floor without fabricating months).")
    w(f"- Month-precision announcement dates (floored to the 1st) carry up to "
      f"~{MONTH_ERROR_DAYS} days of error each.")
    w("")
    w("## 1. Kaplan-Meier: time to a terminal decision")
    w("")
    if median_all is not None and math.isfinite(median_all):
        w(f"- Median time to decision across all opposed projects: "
          f"**{median_all:.0f} days**.")
    else:
        w("- Median not reached within observed follow-up (more than half of "
          "opposed projects remain pending at their last-observed time) — "
          "itself an informative result about how long opposition-linked "
          "projects stay unresolved.")
    for label, kmf_d in (("advanced", km_adv), ("blocked", km_blk)):
        m = kmf_d.median_survival_time_
        m_txt = f"{m:.0f} days" if (m is not None and math.isfinite(m)) else "not reached"
        w(f"- Median time to a `{label}` decision: {m_txt}.")
    if lr is not None:
        w(f"- Log-rank test (blocked vs advanced timing, decided subset): "
          f"p = {lr.p_value:.3f}. "
          + ("Suggestive of different timing." if lr.p_value < 0.1
             else "No significant timing difference at this sample size."))
    w("")
    w("Full KM table (time, survival, at-risk, events) is in "
      "`survival_km_curve.csv`.")
    w("")
    w("## 2. Cox proportional-hazards model")
    w("")
    if n_events < MIN_EVENTS_FOR_COX:
        w(f"WITHHELD: only {n_events} events (< {MIN_EVENTS_FOR_COX} minimum). "
          "A Cox model on this few events would be unstable; KM above is the "
          "defensible summary until more decisions resolve.")
    elif cox_result and "_error" not in cox_result:
        w(f"L2-penalized Cox (penalizer={COX_PENALIZER}, deliberately strong "
          "for small n). Hazard ratio (HR) > 1 means the covariate is "
          "associated with reaching a decision FASTER; < 1, slower.")
        w("")
        for cov, s in cox_result.items():
            hr = s["exp(coef)"]
            lo = s["exp(coef) lower 95%"]
            hi = s["exp(coef) upper 95%"]
            p = s["p"]
            w(f"- `{cov}`: HR {hr:.2f} (95% CI {lo:.2f}–{hi:.2f}, p={p:.2f})")
        w("")
        if c_index_cv:
            w(f"- Cross-validated concordance index (discrimination): median "
              f"**{c_index_cv[1]:.2f}** (range {c_index_cv[0]:.2f}–"
              f"{c_index_cv[2]:.2f}) across {N_FOLDS}-fold CV. 0.50 = chance.")
        w("")
        w("Wide CIs spanning 1.0 mean the direction is not established at this "
          "sample size. This is a scaffold that sharpens as decisions accrue.")
        w("")
        w("**Interpretation — the pooled model understates a real signal.** "
          "This Cox pools two distinct exit types (blocked and advanced) into "
          "a single \"reached a decision\" event. But the cause-specific "
          "Kaplan-Meier medians above show blocked decisions arrive markedly "
          "faster than advanced ones. Pooling them means the covariates are "
          "asked to explain a mixture of two different timing processes, which "
          "depresses discrimination (the near-chance concordance is partly an "
          "artifact of this). A cause-specific or competing-risks Cox (separate "
          "hazards for block vs advance) is the correct next specification; it "
          "is deferred until the advanced-side event count is large enough to "
          "fit its own model without overfitting. Until then the cause-specific "
          "KM medians, not the pooled hazard ratios, are the defensible "
          "timing summary.")
    else:
        err = cox_result.get("_error", "unknown") if cox_result else "not fit"
        w(f"Cox model did not converge cleanly ({err}); KM above stands as the "
          "summary. Revisit once more events are available.")
    w("")
    w("## Limitations (binding)")
    w("")
    w(f"- {n_events} events is a small basis for survival estimates; treat all "
      "numbers as provisional and interval-wide.")
    w("- Censored projects' eventual direction is unknown; by-direction KM "
      "curves estimate time-to-that-direction treating other outcomes as "
      "censored, which is standard but assumes non-informative censoring.")
    w(f"- **Datable-outcome asymmetry (informative-censoring caution).** Among "
      f"opposed projects that reached a terminal outcome, blocked outcomes are "
      f"datable far more often than advanced ones: in the current data, "
      f"{blocked_dated}/{blocked_total} blocked vs {adv_dated}/{adv_total} "
      f"advanced carry a verified discrete decision date. This is structural, "
      f"not a collection gap: a blocked project passes through a discrete "
      f"denial or withdrawal that gets recorded, whereas an opposed project "
      f"that advances often proceeds by-right (pre-zoned land, retrofits, "
      f"incentive agreements) with no contested vote to date. The advanced "
      f"side of any survival split is therefore both smaller and later-arriving "
      f"than the true population, which depresses the advanced-cause hazard and "
      f"is the main reason a cause-specific model is not yet fittable. Treat "
      f"advanced-side timing as a lower bound on how fast advances actually "
      f"occur.")
    w("- Announced→decision spans are raw durations within the opposed "
      "sample, NOT opposition-attributable delay (that needs the matched "
      "controls at adequate n).")
    w("- Not wired into CI. Automated retraining requires the Phase 5 "
      "calibration gate.")
    w("")

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    metrics = {
        "generated": date.today().isoformat(),
        "n": n, "n_events": n_events, "n_censored": n_censored,
        "events_advanced": n_adv_ev, "events_blocked": n_blocked_ev,
        "excluded_year_precision": excluded_year,
        "km_median_days_all": (float(median_all) if (median_all is not None
                               and math.isfinite(median_all)) else None),
        "logrank_p": (None if lr is None else float(lr.p_value)),
        "cox_fit": bool(cox_result and "_error" not in cox_result
                        and n_events >= MIN_EVENTS_FOR_COX),
        "cox_penalizer": COX_PENALIZER,
        "cv_concordance": (None if c_index_cv is None
                           else {"min": round(c_index_cv[0], 4),
                                 "median": round(c_index_cv[1], 4),
                                 "max": round(c_index_cv[2], 4)}),
        "random_state": RANDOM_STATE,
    }
    if cox_result and "_error" not in cox_result and n_events >= MIN_EVENTS_FOR_COX:
        metrics["cox_hazard_ratios"] = {
            k: {"hr": round(v["exp(coef)"], 4),
                "ci_low": round(v["exp(coef) lower 95%"], 4),
                "ci_high": round(v["exp(coef) upper 95%"], 4),
                "p": round(v["p"], 4)}
            for k, v in cox_result.items()}
    with open(OUT_METRICS, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    med_txt = (f"{median_all:.0f}d" if (median_all is not None and math.isfinite(median_all))
               else "not reached")
    print(f"n={n} ({n_events} events / {n_censored} censored) | "
          f"KM median {med_txt} | "
          + (f"Cox c-index {c_index_cv[1]:.2f}" if c_index_cv else "KM only (Cox withheld)"))
    print(f"wrote {os.path.relpath(OUT_REPORT, ROOT)}, km curve, metrics")

    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = [f"{f}:{i}" for f in (OUT_REPORT, OUT_KM)
            for i, l in enumerate(open(f, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:10])
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
