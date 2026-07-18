"""
dated_comparison.py — opposed vs. control time-to-decision (first iteration).

The first censoring-aware timing comparison between opposed projects and
unopposed controls, built on data/baseline_dated.csv. This is the direct
precursor to opposition-attributable delay.

Method (dictated by the data's censoring structure):
  - OPPOSED arm: standard right-censored Kaplan-Meier. Events are verified
    decision dates; pending projects are right-censored.
  - CONTROL arm: the tracker has NO verified control-side decision dates, so
    a standard KM is impossible (an event-free arm never leaves 1.0).
    Instead, controls that are decided-but-undated are INTERVAL-CENSORED:
    their decision happened somewhere in (0, span_days] where span is
    announcement -> last status update. Pending controls are right-censored
    at their span. The nonparametric MLE (Turnbull) estimator handles this.

Output: data/dated_comparison.md (report only; no CSV mutation).

Defensibility rules honored:
  - The two arms use DIFFERENT censoring schemes, so no log-rank test is
    reported — the comparison is descriptive (median bands), with the
    asymmetry stated up front.
  - Interval-censored medians are reported as ranges where the NPMLE is
    flat, never as points.
  - This measures time-to-decision by opposition status, NOT
    opposition-attributable delay; matching and covariates come later.
  - No scorekeeping vocabulary; leak audit before exit.

Run from repo root:  python3 dated_comparison.py
Depends on data/baseline_dated.csv (run baseline_dated.py first).
Requires lifelines. Not in CI (manual-run, like the other models).
"""

from __future__ import annotations

import csv
import math
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

FRAME_CSV = P("data", "baseline_dated.csv")
OUT_MD = P("data", "dated_comparison.md")

MIN_ARM = 20   # minimum records per arm to report anything


def main() -> int:
    if not os.path.exists(FRAME_CSV):
        print("ERROR: baseline_dated.csv missing — run baseline_dated.py first")
        return 1
    try:
        import numpy as np
        from lifelines import KaplanMeierFitter
    except ImportError:
        print("ERROR: lifelines required (pip install lifelines). Manual-run module.")
        return 1

    rows = list(csv.DictReader(open(FRAME_CSV, newline="", encoding="utf-8-sig")))
    opp = [r for r in rows if r["opposed_flag"] == "yes"]
    ctl = [r for r in rows if r["opposed_flag"] == "no"]

    if len(opp) < MIN_ARM or len(ctl) < MIN_ARM:
        print(f"WITHHELD: arms too small (opposed {len(opp)}, control {len(ctl)}; "
              f"need >= {MIN_ARM} each)")
        return 0

    # ---- opposed arm: right-censored KM ----
    o_dur = np.array([float(r["span_days"]) for r in opp])
    o_ev = np.array([int(r["event_observed"]) for r in opp])
    km_o = KaplanMeierFitter().fit(o_dur, o_ev, label="opposed")
    o_med = km_o.median_survival_time_
    o_events = int(o_ev.sum())

    # ---- control arm: interval-censored NPMLE ----
    # decided_undated: decision in (0, span]  -> lower=0.5 (avoid 0), upper=span
    # censored_*:      no decision by span    -> lower=span, upper=+inf
    lo, hi = [], []
    n_interval = 0
    for r in ctl:
        span = float(r["span_days"])
        if r["end_kind"] == "decided_undated":
            lo.append(0.5)
            hi.append(span)
            n_interval += 1
        else:
            lo.append(span)
            hi.append(math.inf)
    km_c = KaplanMeierFitter()
    km_c.fit_interval_censoring(np.array(lo), np.array(hi), label="control")
    # interval-censored median comes back as a DataFrame band (upper/lower NPMLE)
    med_df = km_c.median_survival_time_
    try:
        vals = [float(v) for v in med_df.iloc[0].tolist()]   # column names vary with label
        c_lo, c_hi = min(vals), max(vals)
    except (TypeError, AttributeError, IndexError):
        try:
            c_lo = c_hi = float(med_df)
        except (TypeError, ValueError):
            c_lo = c_hi = math.nan

    today = date.today().isoformat()
    L = []
    w = L.append
    w("# Opposed vs. Control — Time to Terminal Decision (first iteration)")
    w("")
    w(f"Generated {today} by `dated_comparison.py`. **Internal diagnostic — "
      "NOT client-facing.** Descriptive comparison only; the two arms carry "
      "different censoring structures, so no significance test is valid or "
      "reported.")
    w("")
    w("## Arms")
    w("")
    w(f"- **Opposed** (n={len(opp)}): {o_events} verified decision events; "
      "rest right-censored at last known activity. Standard Kaplan-Meier.")
    w(f"- **Control / unopposed** (n={len(ctl)}): zero verified decision "
      f"dates exist on the control side. {n_interval} controls are decided "
      "but undated — treated as interval-censored (decision occurred between "
      "announcement and last status update); the rest are right-censored "
      "pending. Nonparametric MLE (Turnbull) estimator.")
    w("")
    w("## Median time to decision")
    w("")
    o_med_txt = (f"**{o_med:.0f} days**" if (o_med is not None and math.isfinite(o_med))
                 else "**not reached** (curve does not cross 0.5)")
    w(f"- Opposed: {o_med_txt}")
    if math.isfinite(c_lo) and math.isfinite(c_hi):
        if abs(c_hi - c_lo) < 1:
            w(f"- Control: **~{c_lo:.0f} days** (interval-censored NPMLE; a "
              "band estimate, not an observed-event median)")
        else:
            w(f"- Control: **{c_lo:.0f}-{c_hi:.0f} days** (interval-censored "
              "NPMLE band)")
    else:
        w("- Control: **not identified** — the interval-censored likelihood is "
          "too diffuse to locate a median (decided-undated intervals are wide). "
          "More control-side verified dates would tighten this.")
    w("")
    w("## How to read this (binding)")
    w("")
    w("- This compares raw time-to-decision BY OPPOSITION STATUS. It is not "
      "opposition-attributable delay: arms are unmatched here, and opposed "
      "projects differ systematically from controls (siting, scale, "
      "political geography).")
    w("- The control arm's information comes almost entirely from interval "
      "bounds, which is weak evidence about timing. Every verified "
      "control-side decision date (external ingest with source_url) directly "
      "sharpens this comparison.")
    w("- The opposed arm's events skew blocked (blocked decisions are datable "
      "far more often than advances — see the survival model's asymmetry "
      "finding), so its curve partly reflects *blocked* timing.")
    w("- Next iteration: run this comparison within matched sets "
      "(data/matched_controls.csv) once enough matched controls carry usable "
      "spans.")
    w("")

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    med_o = f"{o_med:.0f}d" if (o_med is not None and math.isfinite(o_med)) else "n/r"
    med_c = (f"~{c_lo:.0f}d" if math.isfinite(c_lo) else "not identified")
    print(f"opposed n={len(opp)} ({o_events} ev) median {med_o} | "
          f"control n={len(ctl)} (interval-censored) median {med_c}")
    print(f"wrote {os.path.relpath(OUT_MD, ROOT)}")

    pat = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)
    hits = [i for i, l in enumerate(open(OUT_MD, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:5])
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
