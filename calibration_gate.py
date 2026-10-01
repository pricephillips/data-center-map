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
  models/cards/outcome_model_<date>.md  spec 008 US5: a skops model card,
                                   written only on PROMOTE, aggregates only

Metrics computed (all on out-of-fold predictions):
  - Brier score, and Brier skill score vs a base-rate baseline
  - Expected Calibration Error (ECE) over probability bins
  - Reliability table (predicted vs observed per bin)
  - Discrimination check (does the model separate classes at all)
  - Spec 008 US2 (additive history columns, gate criteria unchanged):
    maximum calibration error (MCE) over the same bins, ECE and MCE over
    equal-mass bins, and the reliability bins as JSON. Built in-house: the
    definitions match netcal's for binary labels (positive-class probability
    against observed frequency, equal-width bins, empty bins skipped), and
    netcal's metrics import torch (specs/008-model-robustness/research.md D3).
  - Report only: ECE and MCE of the production county model, read from
    data/county_policy_scores.csv (cross-fitted calibrated_score). No history
    row is written for it, so the verdict history stays the outcome model's.

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
Self-test:           python3 calibration_gate.py --selftest
Pure-Python (no sklearn needed). skops is needed only for the model card;
without it the card is skipped with a warning and the verdict is unchanged.
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
OUTCOME_METRICS = P("data", "outcome_model_metrics.json")
COUNTY_SCORES = P("data", "county_policy_scores.csv")
CARDS_DIR = P("models", "cards")

# ---- promotion thresholds (conservative) ----
ECE_MAX = 0.15           # expected calibration error ceiling
BSS_MIN = 0.05           # Brier skill score floor (must beat base rate)
MIN_SAMPLE = 60          # below this: INSUFFICIENT_DATA -> HOLD
MIN_POSITIVES = 20       # blocked cases needed for a stable estimate
N_BINS = 5               # reliability bins
# Proposed, NOT enforced (spec 008 US2): shown in the report for adoption.
# With n near 80 in 5 bins one sparse bin can carry a large gap, so a tighter
# ceiling would hold on noise.
MCE_PROPOSED = 0.30

LEGACY_COLS = ["timestamp", "model", "n", "n_positive", "base_rate",
               "brier", "brier_baseline", "brier_skill_score", "ece",
               "discrimination_ok", "verdict", "reason"]
# Appended by spec 008; the legacy columns keep their names and order.
ADDED_COLS = ["mce", "ece_equal_mass", "mce_equal_mass", "reliability_bins"]
HISTORY_COLS = LEGACY_COLS + ADDED_COLS
STANDING_STATEMENT = (
    "Associations in this model are predictive and descriptive, not causal. "
    "Scores describe how closely a decided project's recorded profile "
    "resembles blocked projects in the training frame; they are not a "
    "forecast for any single project and support no cost or effect-size "
    "claim.")


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


def width_bins(pairs, n_bins: int):
    """Equal-width bins: (lo, hi, count, mean_pred, observed), non-empty only."""
    edges = [i / n_bins for i in range(n_bins + 1)]
    out = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        hi_inc = hi + (1e-9 if i == n_bins - 1 else 0)
        m = [(y, p) for y, p in pairs if lo <= p < hi_inc]
        if m:
            out.append((lo, hi, len(m), sum(p for _, p in m) / len(m),
                        sum(y for y, _ in m) / len(m)))
    return out


def mass_bins(pairs, n_bins: int):
    """Equal-mass bins over predictions sorted ascending: (lo, hi, count,
    mean_pred, observed). Bin sizes differ by at most one."""
    srt = sorted(pairs, key=lambda t: t[1])
    n = len(srt)
    out = []
    for i in range(n_bins):
        m = srt[i * n // n_bins:(i + 1) * n // n_bins]
        if m:
            out.append((m[0][1], m[-1][1], len(m), sum(p for _, p in m) / len(m),
                        sum(y for y, _ in m) / len(m)))
    return out


def reliability_table(pairs, n_bins: int):
    """Return per-bin (label, count, mean_pred, observed_freq)."""
    return [(f"{lo:.1f}-{hi:.1f}", cnt, mp, obs)
            for lo, hi, cnt, mp, obs in width_bins(pairs, n_bins)]


def ece_of(bins) -> float:
    total = sum(b[2] for b in bins)
    return sum((b[2] / total) * abs(b[3] - b[4]) for b in bins) if total else 0.0


def mce_of(bins) -> float:
    return max((abs(b[3] - b[4]) for b in bins), default=0.0)


def expected_calibration_error(pairs, n_bins: int) -> float:
    return ece_of(width_bins(pairs, n_bins))


def max_calibration_error(pairs, n_bins: int) -> float:
    return mce_of(width_bins(pairs, n_bins))


def bins_json(bins) -> str:
    return json.dumps([[round(lo, 4), round(hi, 4), cnt, round(mp, 4), round(ob, 4)]
                       for lo, hi, cnt, mp, ob in bins], separators=(",", ":"))


def widen_history(path: str) -> bool:
    """Rewrite a pre-spec-008 history file once with the appended columns.

    Existing rows keep every value; the new columns are blank for them.
    Returns True when the file was widened.
    """
    if not os.path.exists(path):
        return False
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] == HISTORY_COLS:
        return False
    if rows[0] != LEGACY_COLS:
        raise ValueError(f"{os.path.relpath(path, ROOT)} has an unexpected "
                         f"header; refusing to rewrite it: {rows[0]}")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(HISTORY_COLS)
        for r in rows[1:]:
            w.writerow(r + [""] * len(ADDED_COLS))
    return True


def load_county_pairs(path: str):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append((int(r["has_enacted_restrictive"]), float(r["calibrated_score"])))
            except (ValueError, KeyError):
                continue
    return rows


def write_card(record: dict, metrics: dict | None, bins, out_dir: str,
               date_str: str) -> str | None:
    """Write a skops model card for a promoted outcome model.

    Aggregates only (Principle VI). Card(model=None) serializes no model
    object, so nothing is pickled. Returns the path, or None without skops.
    """
    try:
        from skops.card import Card
    except ImportError:
        print("WARNING: skops not installed; model card skipped "
              "(verdict unchanged)")
        return None
    m = metrics or {}
    auc, br = m.get("roc_auc", {}), m.get("brier", {})
    cv = m.get("cv", {})
    feats = m.get("feature_columns", [])
    rel = "\n".join(["| Predicted bin | Projects | Mean predicted | Observed share |",
                     "|---|---|---|---|"]
                    + [f"| {lo:.1f}-{hi:.1f} | {c} | {mp:.2f} | {ob:.2f} |"
                       for lo, hi, c, mp, ob in bins])
    card = Card(model=None, template=None)
    card.add(**{
        "Model description": (
            "Outcome model: L2-regularized logistic regression (C = 0.5, "
            "class-weighted) on decided and opposed data center projects, "
            "estimating the probability that a decided project's terminal "
            "disposition is blocked rather than advanced."),
        "Model description/Data window": (
            f"Training frame built {m.get('generated', 'unknown')} from the "
            f"project lifecycle layer. Decided cases only (terminal "
            f"dispositions); pending projects are never labels."),
        "Model description/Frame": (
            f"{record['n']} projects, {record['n_positive']} blocked "
            f"(base rate {float(record['base_rate']):.2f})."),
        "Model description/Specification": (
            f"Median imputation, standardized inputs, {len(feats)} features: "
            + (", ".join(f"`{f}`" for f in feats) if feats else "see "
               "data/outcome_model_metrics.json") + ". Estimator registered in "
            "outcome_model.py; challengers are reported in "
            "data/outcome_model_estimators.csv and do not ship."),
        "Evaluation": (
            f"Repeated stratified cross-validation, {cv.get('folds', '?')} "
            f"folds x {cv.get('repeats', '?')} repeats."),
        "Evaluation/Cross-validated metrics": (
            f"AUC (area under the ROC curve: the chance a random blocked "
            f"project scores above a random advanced one; 0.5 is chance) "
            f"median {auc.get('p50', 'n/a')}, p10 {auc.get('p10', 'n/a')}, "
            f"p90 {auc.get('p90', 'n/a')}. Brier (mean squared gap between "
            f"predicted probability and outcome; lower is better) median "
            f"{br.get('p50', 'n/a')}, p10 {br.get('p10', 'n/a')}, p90 "
            f"{br.get('p90', 'n/a')}; predicting the base rate scores "
            f"{br.get('base_rate_brier', 'n/a')}."),
        "Evaluation/Calibration": (
            f"On out-of-fold predictions: ECE {record['ece']}, MCE "
            f"{record['mce']}, equal-mass ECE {record['ece_equal_mass']}, "
            f"equal-mass MCE {record['mce_equal_mass']}, Brier skill "
            f"{record['brier_skill_score']}.\n\n" + rel),
        "Gate result": (
            f"{record['verdict']} on {record['timestamp']}: {record['reason']}. "
            f"Thresholds: ECE <= {ECE_MAX}, Brier skill >= {BSS_MIN}, "
            f"n >= {MIN_SAMPLE}, positives >= {MIN_POSITIVES}."),
        "Limitations": STANDING_STATEMENT,
    })
    os.makedirs(out_dir, exist_ok=True)
    card_md = os.path.join(out_dir, "outcome_model_" + date_str + ".md")
    card.save(card_md)
    with open(card_md, encoding="utf-8") as fh:
        text = fh.read().replace("\r\n", "\n").rstrip() + "\n"
    with open(card_md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return card_md


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
    wbins = width_bins(pairs, N_BINS)
    mbins = mass_bins(pairs, N_BINS)
    mce = mce_of(wbins)
    ece_m, mce_m = ece_of(mbins), mce_of(mbins)

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
        "mce": round(mce, 4), "ece_equal_mass": round(ece_m, 4),
        "mce_equal_mass": round(mce_m, 4), "reliability_bins": bins_json(wbins),
    }

    # append-only history (widened once, in place, for the spec 008 columns)
    if widen_history(HISTORY_CSV):
        print(f"widened {os.path.relpath(HISTORY_CSV, ROOT)} header with "
              f"{', '.join(ADDED_COLS)}")
    exists = os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_COLS, lineterminator="\n")
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
    w(f"- Maximum calibration error (MCE): **{mce:.3f}** (largest bin gap; "
      f"proposed ceiling {MCE_PROPOSED}, not enforced)")
    w(f"- Equal-mass bins ({N_BINS} bins of about {n // N_BINS if n else 0} "
      f"projects): ECE {ece_m:.3f}, MCE {mce_m:.3f}")
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
    w("## Proposed threshold (for adoption; not part of this verdict)")
    w("")
    w(f"MCE <= {MCE_PROPOSED} on the equal-width bins. This run: MCE "
      f"{mce:.3f}, which {'would meet' if mce <= MCE_PROPOSED else 'would NOT meet'} "
      f"the proposed ceiling. The gate criteria above are unchanged until the "
      f"ceiling is adopted and recorded in this module.")
    w("")
    cpairs = load_county_pairs(COUNTY_SCORES)
    w("## Production county model (report only)")
    w("")
    if cpairs:
        cw = width_bins(cpairs, N_BINS)
        cm = mass_bins(cpairs, N_BINS)
        cn = len(cpairs)
        cpos = sum(y for y, _ in cpairs)
        w(f"Read from data/county_policy_scores.csv (`calibrated_score`, "
          f"cross-fitted: no county's recalibration was fit on itself). "
          f"{cn} counties, {cpos} with an enacted restriction. No history "
          f"row is written for this model, so the verdict history above "
          f"stays the outcome model's.")
        w("")
        w(f"- ECE {ece_of(cw):.3f}, MCE {mce_of(cw):.3f} (equal-width, "
          f"{N_BINS} bins)")
        w(f"- ECE {ece_of(cm):.3f}, MCE {mce_of(cm):.3f} (equal-mass, "
          f"{N_BINS} bins of about {cn // N_BINS} counties)")
        w("")
        w("| Predicted bin | Counties | Mean predicted | Observed share |")
        w("|---|---|---|---|")
        for lo, hi, cnt, mp, ob in cm:
            w(f"| {lo:.3f}-{hi:.3f} | {cnt} | {mp:.3f} | {ob:.3f} |")
        w("")
        w("Equal-mass bins are shown because most county scores sit below "
          "0.2, where equal-width bins would put nearly every county in one "
          "row.")
    else:
        w("data/county_policy_scores.csv not found; nothing to report.")
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

    card_path = None
    if verdict == "PROMOTE":
        try:
            om = json.load(open(OUTCOME_METRICS, encoding="utf-8"))
        except (OSError, ValueError):
            om = None
        card_path = write_card(record, om, wbins, CARDS_DIR, ts[:10])
        if card_path:
            w(f"Model card: `{os.path.relpath(card_path, ROOT)}`.")
            w("")

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    print(f"verdict: {verdict} | {reason}")
    print(f"n={n} pos={n_pos} | Brier {b:.3f} | skill {bss:.3f} | ECE {ece:.3f}")
    print(f"logged to {os.path.relpath(HISTORY_CSV, ROOT)} "
          f"({len(history)} runs for this model)")

    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = [f"{f}:{i}" for f in (OUT_REPORT, HISTORY_CSV) + ((card_path,) if card_path else ())
            for i, l in enumerate(open(f, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:10])
        return 1
    print("leak audit: clean")

    # exit code communicates the gate result to CI:
    #   0 = PROMOTE, 10 = HOLD. CI can branch on this without parsing text.
    return 0 if verdict == "PROMOTE" else 10


def selftest() -> int:
    """Spec 008 US2/US5 checks on synthetic data and temp files only."""
    import random
    import tempfile
    fails = []

    def check(name, cond):
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    rnd = random.Random(11)
    ps = [rnd.random() for _ in range(20000)]
    calibrated = [(int(rnd.random() < p), p) for p in ps]
    shifted = [(y, min(1.0, p + 0.25)) for y, p in calibrated]
    e_cal = expected_calibration_error(calibrated, N_BINS)
    e_sh = expected_calibration_error(shifted, N_BINS)
    check(f"calibrated ECE near 0 ({e_cal:.4f})", e_cal < 0.02)
    check(f"shifted ECE larger ({e_sh:.4f})", e_sh > 0.15 and e_sh > 5 * e_cal)
    check("MCE >= ECE", max_calibration_error(shifted, N_BINS) >= e_sh)

    # Hand-computed: two bins, gaps 0.3 (n=2) and 0.4 (n=2).
    hand = [(0, 0.1), (1, 0.3), (1, 0.9), (0, 0.9)]
    wb = width_bins(hand, 2)
    check("hand ECE 0.35", abs(ece_of(wb) - 0.35) < 1e-9)
    check("hand MCE 0.40", abs(mce_of(wb) - 0.40) < 1e-9)
    mb = mass_bins(list(zip([0] * 7, [i / 10 for i in range(7)])), 3)
    check("equal-mass bins differ in size by at most one",
          sorted(b[2] for b in mb) == [2, 2, 3])
    check("legacy ECE definition unchanged",
          abs(expected_calibration_error(hand, 2)
              - sum((c / 4) * abs(m - o) for _, c, m, o in reliability_table(hand, 2))) < 1e-12)
    parsed = json.loads(bins_json(wb))
    check("reliability bins JSON round-trips", len(parsed) == 2 and parsed[0][2] == 2)

    with tempfile.TemporaryDirectory() as td:
        hp = os.path.join(td, "h.csv")
        with open(hp, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh, lineterminator="\n")
            wr.writerow(LEGACY_COLS)
            wr.writerow(["2026-07-15T16:44:29Z", "outcome_model", "78", "24", "0.3077",
                         "0.1913", "0.213", "0.1022", "0.1348", "True", "PROMOTE", "PASSED: x"])
        check("legacy header widened once", widen_history(hp) and not widen_history(hp))
        rows = list(csv.reader(open(hp, encoding="utf-8")))
        check("legacy columns keep names and order", rows[0][:len(LEGACY_COLS)] == LEGACY_COLS)
        check("legacy row values unchanged, new columns blank",
              rows[1][:12][-2:] == ["PROMOTE", "PASSED: x"] and rows[1][12:] == [""] * 4)
        bad = os.path.join(td, "bad.csv")
        open(bad, "w").write("a,b\n1,2\n")
        try:
            widen_history(bad)
            check("unexpected header refused", False)
        except ValueError:
            check("unexpected header refused", True)

        rec = {"timestamp": "2026-10-01T00:00:00Z", "n": 80, "n_positive": 30,
               "base_rate": 0.375, "ece": 0.06, "mce": 0.12, "ece_equal_mass": 0.07,
               "mce_equal_mass": 0.15, "brier_skill_score": 0.41,
               "verdict": "PROMOTE", "reason": "PASSED: test"}
        met = {"generated": "2026-10-01", "cv": {"folds": 5, "repeats": 10},
               "roc_auc": {"p10": 0.75, "p50": 0.85, "p90": 0.95},
               "brier": {"p10": 0.1, "p50": 0.16, "p90": 0.22, "base_rate_brier": 0.23},
               "feature_columns": ["n_opposition_events", "mech_lawsuit"]}
        try:
            import skops  # noqa: F401
        except ImportError:
            print("SKIP model card: skops not installed")
        else:
            cp = write_card(rec, met, wb, os.path.join(td, "cards"), "2026-10-01")
            text = open(cp, encoding="utf-8").read() if cp else ""
            check("card written at models/cards/<model>_<date>.md layout",
                  bool(cp) and cp.endswith("outcome_model_2026-10-01.md"))
            for need in ("Data window", "Frame", "Specification", "Cross-validated metrics",
                         "Calibration", "Gate result", "not causal"):
                check(f"card has {need}", need in text)
            check("card has no CRLF and no em-dash", "\r" not in text and "\u2014" not in text)
            check("card passes the vocabulary check",
                  not re.search(r"\b(win|wins|loss|losses|lost)\b", text, re.I))
            check("card carries no row-level project ids", "prj_" not in text)

    print(f"{'PASS' if not fails else 'FAIL'}: calibration_gate selftest")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
