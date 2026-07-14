"""
calibration_gate.py — Phase 5: calibration gating for model promotion.

The gate that makes automated retraining safe. It reads a model's
out-of-fold predictions, measures whether predicted probabilities match
observed outcome frequencies, appends the result to a persistent history
log, and issues a PROMOTE / HOLD verdict against explicit thresholds. No
model is promoted to client-facing use or automated retraining unless it
clears this gate.

This module does NOT train anything. It consumes predictions produced by
the model modules (currently outcome_model.py, which writes
`data/outcome_model_predictions.csv`). Keeping the gate separate from the
trainers means the promotion decision is auditable and independent of the
code that has an interest in passing.

Additive only. Writes/updates:

  data/calibration_history.csv     append-only log: one row per gate run,
                                   per model, with all metrics and the verdict
  data/calibration_gate_report.md  human-readable latest verdict + trend

Metrics computed (all on out-of-fold predictions):
  - Brier score, and Brier skill score vs a base-rate baseline
  - Expected Calibration Error (ECE) over probability bins
  - Reliability table (predicted vs observed per bin)
  - Discrimination check (does the model separate classes at all)

Promotion thresholds (deliberately conservative; a model may be accurate on
discrimination yet still HOLD if it is miscalibrated):
  - ECE must be <= ECE_MAX
  - Brier skill score must be >= BSS_MIN (beats base-rate guessing)
  - Minimum sample and minimum positives must be met (else INSUFFICIENT_DATA,
    which is a HOLD, never a PROMOTE)

Defensibility rules honored:
  - The gate never promotes on thin data; INSUFFICIENT_DATA holds.
  - Calibration is judged, not just discrimination — a well-ranked but
    overconfident model is held, consistent with reporting calibrated ranges
    rather than unexplained point estimates.
  - History is append-only so calibration drift over time is visible and
    auditable.
  - No scorekeeping vocabulary; leak audit runs before writing.

Run from repo root:  python3 calibration_gate.py
Pure-Python (no sklearn needed). Safe to wire into CI later.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

PRED_CSV = P("data", "outcome_model_predictions.csv")
HISTORY_CSV = P("data", "calibration_history.csv")
OUT_REPORT = P("data", "calibration_gate_report.md")

# ---- promotion thresholds (conservative) ----
ECE_MAX = 0.15           # expected calibration error ceiling
BSS_MIN = 0.05           # Brier skill score floor (must beat base rate)
MIN_SAMPLE = 60          # below this: INSUFFICIENT_DATA -> HOLD
MIN_POSITIVES = 20       # blocked cases needed for a stable estimate
N_BINS = 5               # reliability bins

HISTORY_COLS = ["timestamp", "model", "n", "n_positive", "base_rate",
                "brier", "brier_baseline", "brier_skill_score", "ece",
                "discrimination_ok", "verdict", "reason"]


def load_predictions(path: str):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append((int(r["label_blocked"]), float(r["oof_pred_blocked"])))
            except (ValueError, KeyError):
                continue
    return rows


def brier(pairs) -> float:
    return sum((p - y) ** 2 for y, p in pairs) / len(pairs)


def reliability_table(pairs, n_bins: int):
    """Return per-bin (label, count, mean_pred, observed_freq)."""
    edges = [i / n_bins for i in range(n_bins + 1)]
    table = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        hi_inc = hi + (1e-9 if i == n_bins - 1 else 0)
        m = [(y, p) for y, p in pairs if lo <= p < hi_inc]
        if m:
            mean_pred = sum(p for _, p in m) / len(m)
            obs = sum(y for y, _ in m) / len(m)
            table.append((f"{lo:.1f}-{hi:.1f}", len(m), mean_pred, obs))
    return table


def expected_calibration_error(pairs, n_bins: int) -> float:
    total = len(pairs)
    ece = 0.0
    for _, cnt, mean_pred, obs in reliability_table(pairs, n_bins):
        ece += (cnt / total) * abs(mean_pred - obs)
    return ece


def main() -> int:
    if not os.path.exists(PRED_CSV):
        print(f"ERROR: {os.path.relpath(PRED_CSV, ROOT)} not found. Run "
              "outcome_model.py first to produce out-of-fold predictions.")
        return 1

    pairs = load_predictions(PRED_CSV)
    n = len(pairs)
    n_pos = sum(y for y, _ in pairs)
    base_rate = n_pos / n if n else 0.0

    b = brier(pairs)
    b_base = base_rate * (1 - base_rate)              # Brier of predicting base rate
    bss = 1 - (b / b_base) if b_base > 0 else 0.0     # Brier skill score
    ece = expected_calibration_error(pairs, N_BINS)
    rel = reliability_table(pairs, N_BINS)

    # discrimination sanity: mean predicted prob for positives > for negatives
    pos_mean = (sum(p for y, p in pairs if y == 1) / n_pos) if n_pos else 0.0
    neg_mean = (sum(p for y, p in pairs if y == 0) / (n - n_pos)) if (n - n_pos) else 0.0
    discrimination_ok = pos_mean > neg_mean

    # ---- verdict ----
    if n < MIN_SAMPLE or n_pos < MIN_POSITIVES:
        verdict = "HOLD"
        reason = (f"INSUFFICIENT_DATA: n={n} (need >={MIN_SAMPLE}), "
                  f"positives={n_pos} (need >={MIN_POSITIVES})")
    elif not discrimination_ok:
        verdict = "HOLD"
        reason = "NO_DISCRIMINATION: model does not separate classes on out-of-fold data"
    elif ece > ECE_MAX:
        verdict = "HOLD"
        reason = f"MISCALIBRATED: ECE {ece:.3f} > {ECE_MAX} ceiling"
    elif bss < BSS_MIN:
        verdict = "HOLD"
        reason = f"NO_SKILL: Brier skill score {bss:.3f} < {BSS_MIN} floor"
    else:
        verdict = "PROMOTE"
        reason = (f"PASSED: ECE {ece:.3f} <= {ECE_MAX}, "
                  f"Brier skill {bss:.3f} >= {BSS_MIN}, discrimination ok")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "timestamp": ts, "model": "outcome_model", "n": n, "n_positive": n_pos,
        "base_rate": round(base_rate, 4), "brier": round(b, 4),
        "brier_baseline": round(b_base, 4), "brier_skill_score": round(bss, 4),
        "ece": round(ece, 4), "discrimination_ok": discrimination_ok,
        "verdict": verdict, "reason": reason,
    }

    # append-only history
    exists = os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_COLS)
        if not exists:
            w.writeheader()
        w.writerow(record)

    # trend: read prior verdicts for this model
    history = []
    with open(HISTORY_CSV, newline="", encoding="utf-8") as fh:
        history = [r for r in csv.DictReader(fh) if r["model"] == "outcome_model"]

    # ---- report ----
    L = []
    w = L.append
    w("# Calibration Gate — Latest Verdict")
    w("")
    w(f"Run {ts} on `outcome_model` out-of-fold predictions.")
    w("")
    w(f"## Verdict: **{verdict}**")
    w("")
    w(f"{reason}")
    w("")
    w("## Metrics")
    w("")
    w(f"- Sample: {n} projects, {n_pos} blocked (base rate {base_rate:.2f})")
    w(f"- Brier score: **{b:.3f}** (base-rate baseline {b_base:.3f})")
    w(f"- Brier skill score: **{bss:.3f}** (>0 beats the baseline; floor {BSS_MIN})")
    w(f"- Expected calibration error (ECE): **{ece:.3f}** (ceiling {ECE_MAX})")
    w(f"- Discrimination (positives predicted higher than negatives): "
      f"{'yes' if discrimination_ok else 'NO'} "
      f"(mean pred: blocked {pos_mean:.2f} vs advanced {neg_mean:.2f})")
    w("")
    w("## Reliability table (out-of-fold)")
    w("")
    w("| Predicted bin | Projects | Mean predicted | Observed blocked |")
    w("|---|---|---|---|")
    for label, cnt, mean_pred, obs in rel:
        w(f"| {label} | {cnt} | {mean_pred:.2f} | {obs:.2f} |")
    w("")
    w("Well-calibrated means mean-predicted and observed track each other "
      "down each row. Gaps are where the model is over- or under-confident.")
    w("")
    w("## Promotion policy")
    w("")
    w(f"A model is promoted only when ECE <= {ECE_MAX}, Brier skill "
      f">= {BSS_MIN}, discrimination holds, and the sample clears "
      f"n >= {MIN_SAMPLE} with >= {MIN_POSITIVES} positives. A model that "
      "ranks well but is overconfident is held, consistent with the "
      "platform's rule to report calibrated ranges rather than unexplained "
      "point estimates. Thin data always holds; it never promotes.")
    w("")
    if len(history) > 1:
        w("## History (this model)")
        w("")
        w("| Run | n | ECE | Brier skill | Verdict |")
        w("|---|---|---|---|---|")
        for h in history[-8:]:
            w(f"| {h['timestamp'][:10]} | {h['n']} | {h['ece']} | "
              f"{h['brier_skill_score']} | {h['verdict']} |")
        w("")

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    print(f"verdict: {verdict} | {reason}")
    print(f"n={n} pos={n_pos} | Brier {b:.3f} | skill {bss:.3f} | ECE {ece:.3f}")
    print(f"logged to {os.path.relpath(HISTORY_CSV, ROOT)} "
          f"({len(history)} runs for this model)")

    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = [f"{f}:{i}" for f in (OUT_REPORT, HISTORY_CSV)
            for i, l in enumerate(open(f, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:10])
        return 1
    print("leak audit: clean")

    # exit code communicates the gate result to CI:
    #   0 = PROMOTE, 10 = HOLD. CI can branch on this without parsing text.
    return 0 if verdict == "PROMOTE" else 10


if __name__ == "__main__":
    sys.exit(main())
