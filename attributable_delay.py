"""
attributable_delay.py — opposition-attributable delay (gated scaffold).

The estimator the cost layer is waiting for: the difference in time-to-
decision between opposed projects and their matched unopposed controls.
This is the number that, multiplied through the cost anchors, becomes a
defensible cost-of-opposition range — which is why it is gated hard and
refuses to emit an estimate until its inputs can support one.

Method (when unlocked):
  Within matched sets (data/matched_controls.csv), compare restricted mean
  survival time (RMST) of announced->decision between the opposed project
  and its matched controls, both arms censoring-aware. RMST is chosen over
  median because it is defined even when curves do not cross 0.5, uses the
  whole curve, and differences in RMST have a direct "days of delay"
  reading. The attributable-delay estimate is the matched RMST difference
  with a bootstrap interval over matched sets.

Gates (ALL must pass or the module reports WITHHELD with reasons):
  - >= MIN_CTRL_EVENTS verified control-side decision events (currently the
    binding constraint; permit ingest supplies these)
  - >= MIN_OPP_EVENTS verified opposed-side events within matched sets
  - >= MIN_MATCHED_SETS matched sets with usable spans on both arms

Output: data/attributable_delay.md (+ metrics JSON when unlocked).

Defensibility rules honored:
  - No estimate on thin data: WITHHELD is the normal state until control
    events accrue; the report says exactly which gate failed and what input
    unlocks it.
  - When unlocked, the estimate is a range (bootstrap percentile interval),
    never a point; the horizon tau is stated; censoring structure of both
    arms is stated.
  - Association framing only: matched difference, not a causal claim.
  - No scorekeeping vocabulary; leak audit before exit.

Run from repo root:  python3 attributable_delay.py
Depends on data/baseline_dated.csv + data/matched_controls.csv.
Requires lifelines + numpy. Manual-run / retrain-chain, not client-facing.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

FRAME_CSV = P("data", "baseline_dated.csv")
MATCHES_CSV = P("data", "matched_controls.csv")
OUT_MD = P("data", "attributable_delay.md")
OUT_JSON = P("data", "attributable_delay_metrics.json")

MIN_CTRL_EVENTS = 10     # verified control-side decision events
MIN_OPP_EVENTS = 15      # opposed events inside matched sets
MIN_MATCHED_SETS = 25    # matched sets usable on both arms
N_BOOT = 2000
RANDOM_STATE = 20260716


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def rmst(durations, events, tau):
    """Restricted mean survival time via KM, integrated to tau."""
    from lifelines import KaplanMeierFitter
    import numpy as np
    km = KaplanMeierFitter().fit(np.asarray(durations, dtype=float),
                                 np.asarray(events, dtype=int))
    from lifelines.utils import restricted_mean_survival_time
    return float(restricted_mean_survival_time(km, t=tau))


def main() -> int:
    for f in (FRAME_CSV, MATCHES_CSV):
        if not os.path.exists(f):
            print(f"ERROR: {os.path.relpath(f, ROOT)} missing")
            return 1
    try:
        import numpy as np  # noqa: F401
        import lifelines    # noqa: F401
    except ImportError:
        print("ERROR: lifelines + numpy required. Manual-run module.")
        return 1

    frame = load_csv(FRAME_CSV)
    by_id = {r["record_id"]: r for r in frame}

    pair_map = defaultdict(list)
    for m in load_csv(MATCHES_CSV):
        pair_map[m["opposed_project_id"]].append(m["control_universe_id"])

    # assemble matched sets usable on both arms
    sets = []
    for oid, ctl_ids in pair_map.items():
        o = by_id.get(oid)
        cs = [by_id[c] for c in ctl_ids if c in by_id]
        if o and cs:
            sets.append((o, cs))

    opp_events = sum(int(o["event_observed"]) for o, _ in sets)
    ctrl_events = sum(int(c["event_observed"]) for _, cs in sets for c in cs)
    n_sets = len(sets)

    gates = [
        ("control-side verified decision events",
         ctrl_events, MIN_CTRL_EVENTS),
        ("opposed verified events within matched sets",
         opp_events, MIN_OPP_EVENTS),
        ("matched sets usable on both arms",
         n_sets, MIN_MATCHED_SETS),
    ]
    failed = [(name, have, need) for name, have, need in gates if have < need]

    today = date.today().isoformat()
    L = []
    w = L.append
    w("# Opposition-Attributable Delay (gated)")
    w("")
    w(f"Generated {today} by `attributable_delay.py`. **Internal — NOT "
      "client-facing.** This module emits an estimate only when its gates "
      "pass; a WITHHELD state is normal and correct until then.")
    w("")
    w("## Gate status")
    w("")
    for name, have, need in gates:
        mark = "PASS" if have >= need else "SHORT"
        w(f"- {mark}: {name} — {have} / {need} required")
    w("")

    metrics = {
        "generated": today,
        "matched_sets": n_sets,
        "opposed_events_in_sets": opp_events,
        "control_events_in_sets": ctrl_events,
        "gates_passed": not failed,
        "estimate": None,
    }

    if failed:
        w("## Verdict: **WITHHELD**")
        w("")
        binding = failed[0][0]
        w(f"The binding constraint is **{binding}**. "
          "Control-side events come from the permit ingest "
          "(`permit_ingest.py` -> `data/baseline_dated_external.csv` with "
          "source URLs); each terminal permit decision added converts an "
          "interval-censored bound into an observed event and moves this "
          "gate. No estimate, preliminary or otherwise, is derivable from "
          "the current inputs without violating the platform's "
          "defensibility rules.")
        w("")
    else:
        # ---- unlocked path ----
        import numpy as np
        rng = np.random.default_rng(RANDOM_STATE)
        # common horizon: 90th percentile of all spans, rounded to 30d
        spans = [float(o["span_days"]) for o, _ in sets] + \
                [float(c["span_days"]) for _, cs in sets for c in cs]
        tau = float(int(np.percentile(spans, 90) / 30) * 30)

        def set_diff(idx):
            o_d, o_e, c_d, c_e = [], [], [], []
            for i in idx:
                o, cs = sets[i]
                o_d.append(float(o["span_days"]))
                o_e.append(int(o["event_observed"]))
                for c in cs:
                    c_d.append(float(c["span_days"]))
                    c_e.append(int(c["event_observed"]))
            # RMST here is mean time WITHOUT a decision up to tau; a LARGER
            # opposed RMST means opposed projects stay undecided longer.
            return rmst(o_d, o_e, tau) - rmst(c_d, c_e, tau)

        point = set_diff(range(n_sets))
        boots = []
        for _ in range(N_BOOT):
            idx = rng.integers(0, n_sets, n_sets)
            try:
                boots.append(set_diff(idx))
            except Exception:
                continue
        lo, hi = (float(np.percentile(boots, 2.5)),
                  float(np.percentile(boots, 97.5))) if boots else (math.nan,) * 2

        w("## Verdict: estimate available")
        w("")
        w(f"- Horizon tau = {tau:.0f} days (90th percentile of matched spans).")
        w(f"- Matched RMST difference (opposed − control) in days undecided "
          f"by tau: **{point:.0f}** (95% bootstrap interval "
          f"**{lo:.0f} to {hi:.0f}**, {len(boots)} resamples over matched "
          "sets).")
        w("")
        w("Positive values mean opposed projects remain undecided longer "
          "than their matched controls within the horizon. This is a "
          "matched association, not a causal effect; residual confounding "
          "and the arms' different censoring structures remain. Interval "
          "spanning zero means no attributable delay is demonstrated.")
        w("")
        metrics["estimate"] = {"tau_days": tau, "rmst_diff_days": round(point, 1),
                               "ci_low": round(lo, 1), "ci_high": round(hi, 1),
                               "n_boot": len(boots)}

    w("## Inputs and definitions")
    w("")
    w("- Frame: `data/baseline_dated.csv` (announced-date origins; verified "
      "decision dates as events; status-update/as-of censoring).")
    w("- Matching: `data/matched_controls.csv` (state / capacity / 2024 "
      "margin).")
    w("- RMST (restricted mean survival time) is used because it is defined "
      "regardless of whether curves cross 0.5 and differences read directly "
      "as days.")
    w("")

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    state = "WITHHELD" if failed else "ESTIMATE"
    print(f"{state} | sets={n_sets} opp_ev={opp_events} ctrl_ev={ctrl_events}")
    print(f"wrote {os.path.relpath(OUT_MD, ROOT)}, metrics")

    pat = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)
    hits = [i for i, l in enumerate(open(OUT_MD, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:5])
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
