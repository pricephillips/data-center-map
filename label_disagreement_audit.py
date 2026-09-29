#!/usr/bin/env python3
"""
label_disagreement_audit.py

County labels that strongly disagree with the model (spec 005, US4).

Reads the cross-fitted out-of-fold scores that county_policy_model.py
already writes to data/county_policy_scores.csv. Every county's score there
comes from a fit that never saw that county, so a label the score strongly
contradicts is worth a second look. Two rules, with cutoffs in
configs/data_quality.json:

  positive_bottom_decile  labeled 1, calibrated_score at or below the 10th
                          percentile of all counties
  negative_top_1pct       labeled 0, calibrated_score at or above the 99th
                          percentile

Flagged counties are ranked by |label - calibrated_score|. Each positive
carries the master rows that produced its label, selected with
county_aggregator.py's own rule (imported, not copied). The output is a
review worklist only. Nothing here changes a label (FR-007), and the model
is not refit.

This is the confident-learning idea at the scale of one binary label.
cleanlab does the same job but is AGPL (configs/integrations.json).

Limits, measured (spec 005 research D11): replaying the labels from before
2026-08-21 surfaces 0 of the 28 counties removed that day. Those false
positives sat in high score deciles, because the model's predictors score
the same profile (a project approved over recorded opposition) that
produced the mislabels. Read an empty worklist as "no isolated flip", not as
"labels verified".

Reads   data/county_policy_scores.csv, data/county_aggregate.csv,
        master_opposition.csv, configs/data_quality.json
Writes  data/label_disagreement_worklist.csv

Usage
  python label_disagreement_audit.py
  python label_disagreement_audit.py --scores PATH [--out PATH] [--no-evidence]
  python label_disagreement_audit.py --scores PATH --no-write --check-recall FIPS.txt
  python label_disagreement_audit.py --selftest

Always exits 0 outside --selftest: missing scores print SKIP and write
nothing.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(ROOT, *parts)


SCORES_CSV = P("data", "county_policy_scores.csv")
AGG_CSV = P("data", "county_aggregate.csv")
MASTER_CSV = P("master_opposition.csv")
CONFIG = P("configs", "data_quality.json")
OUT_CSV = P("data", "label_disagreement_worklist.csv")

REQUIRED = ("fips", "calibrated_score", "raw_oof_score", "has_enacted_restrictive")
OUT_COLS = ["rank", "fips", "county_name", "state", "label", "calibrated_score",
            "raw_oof_score", "score_decile", "rule", "disagreement", "label_provenance",
            "n_evidence_rows", "evidence"]
DEFAULTS = {"positive_quantile": 0.10, "negative_quantile": 0.99, "max_evidence": 5}


def load_config(path=None) -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(path or CONFIG, encoding="utf-8")).get("label_disagreement", {}))
    except (OSError, ValueError):
        pass
    return cfg


def quantile(values: list[float], q: float) -> float:
    """Linear interpolation between order statistics (numpy's default)."""
    xs = sorted(values)
    if not xs:
        return float("nan")
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def read_scores(path) -> tuple[list[dict] | None, str]:
    if not os.path.exists(path):
        return None, f"scores file not found: {path}"
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    missing = [c for c in REQUIRED if not rows or c not in rows[0]]
    if missing:
        return None, f"scores file lacks out-of-fold columns {missing}"
    out = []
    for r in rows:
        try:
            out.append({"fips": r["fips"].zfill(5), "label": int(r["has_enacted_restrictive"]),
                        "cal": float(r["calibrated_score"]), "raw": float(r["raw_oof_score"]),
                        "decile": r.get("score_decile", "")})
        except (TypeError, ValueError):
            continue
    return out, ""


def flag(scores: list[dict], cfg: dict) -> list[dict]:
    lo = quantile([s["cal"] for s in scores], float(cfg["positive_quantile"]))
    hi = quantile([s["cal"] for s in scores], float(cfg["negative_quantile"]))
    out = []
    for s in scores:
        if s["label"] == 1 and s["cal"] <= lo:
            out.append(dict(s, rule="positive_bottom_decile"))
        elif s["label"] == 0 and s["cal"] >= hi:
            out.append(dict(s, rule="negative_top_1pct"))
    for s in out:
        s["disagreement"] = abs(s["label"] - s["cal"])
    out.sort(key=lambda s: (-s["disagreement"], s["fips"]))
    for i, s in enumerate(out, 1):
        s["rank"] = i
    return out


def evidence_rows(fipses: set) -> dict:
    """fips -> master rows county_aggregator counts toward enacted_restrictive."""
    import county_aggregator as CA
    _, resolver = CA.load_frame()
    out = defaultdict(list)
    with open(MASTER_CSV, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if CA._HAVE_VERIFICATION and not CA._VS.is_countable(r):
                continue
            cty, stt = r.get("County"), r.get("State")
            if not (cty or "").strip() or not (stt or "").strip():
                continue
            f = resolver.get((CA.norm_county(cty), CA.norm_state(stt)))
            if f not in fipses:
                continue
            if counts_toward_label(r, CA):
                out[f].append(r)
    return out


def counts_toward_label(r: dict, CA) -> bool:
    """county_aggregator's enacted-restrictive rule, including the approved guard."""
    status = (r.get("Status") or "").strip().lower()
    if not (CA._type_tokens(r.get("Opposition Type")) & CA.RESTRICTIVE_TYPES
            and status in CA.ENACTED_STATUSES):
        return False
    if status in CA.DIRECTION_AMBIGUOUS_STATUSES and \
            (r.get("Community Outcome") or "").strip().lower() != "win":
        return False
    return True


def read_aggregate() -> dict:
    try:
        with open(AGG_CSV, newline="", encoding="utf-8-sig") as fh:
            return {r["fips"].zfill(5): r for r in csv.DictReader(fh)}
    except OSError:
        return {}


def build_rows(flagged, agg, evidence, max_ev) -> list[dict]:
    rows = []
    for s in flagged:
        a = agg.get(s["fips"], {})
        ev = evidence.get(s["fips"], []) if evidence is not None else None
        if s["label"] == 0:
            ev_text = "no enacted restrictive row"
        elif ev is None:
            ev_text = "evidence not collected (--no-evidence)"
        else:
            ev_text = " ;; ".join(" | ".join((r.get(k) or "").strip() for k in
                                             ("Date", "Opposition Type", "Status", "Source URL"))
                                  for r in ev[:max_ev]) or "no countable row found"
        rows.append({"rank": s["rank"], "fips": s["fips"], "county_name": a.get("county_name", ""),
                     "state": a.get("state", ""), "label": s["label"],
                     "calibrated_score": round(s["cal"], 4), "raw_oof_score": round(s["raw"], 4),
                     "score_decile": s["decile"], "rule": s["rule"],
                     "disagreement": round(s["disagreement"], 4),
                     "label_provenance": a.get("label_provenance", ""),
                     "n_evidence_rows": "" if ev is None or s["label"] == 0 else len(ev),
                     "evidence": ev_text})
    return rows


def write_worklist(rows):
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def run(scores_path=None, write=True, with_evidence=True, recall_path=None) -> tuple[int, list]:
    cfg = load_config()
    scores, why = read_scores(scores_path or SCORES_CSV)
    if scores is None:
        print(f"SKIP: {why}. Out-of-fold probabilities are produced by county_policy_model.py; "
              "nothing written.")
        return 0, []
    flagged = flag(scores, cfg)
    evidence = evidence_rows({s["fips"] for s in flagged if s["label"] == 1}) \
        if with_evidence and flagged else ({} if with_evidence else None)
    rows = build_rows(flagged, read_aggregate(), evidence, int(cfg["max_evidence"]))
    npos = sum(1 for r in rows if r["label"] == 1)
    print(f"{len(scores)} counties; {len(rows)} flagged ({npos} positive_bottom_decile, "
          f"{len(rows) - npos} negative_top_1pct)")
    if recall_path:
        want = [ln.strip().zfill(5) for ln in open(recall_path, encoding="utf-8") if ln.strip()]
        got = {r["fips"] for r in rows}
        print(f"recall: {sum(1 for f in want if f in got)} of {len(want)}")
    if write:
        write_worklist(rows)
    return 0, rows


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold

    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # Synthetic frame: one informative feature, labels that follow it, one
    # clearly negative county flipped to positive. Probabilities are 5-fold
    # cross-fitted exactly as the county model's are.
    rng = np.random.default_rng(20260929)
    n = 400
    x = rng.normal(size=n)
    y = (x + 0.3 * rng.normal(size=n) > 0).astype(int)
    flip = int(np.argmin(np.where(y == 0, x, np.inf)))
    y[flip] = 1
    oof = np.zeros(n)
    for tr, te in StratifiedKFold(5, shuffle=True, random_state=0).split(x.reshape(-1, 1), y):
        m = LogisticRegression().fit(x[tr].reshape(-1, 1), y[tr])
        oof[te] = m.predict_proba(x[te].reshape(-1, 1))[:, 1]
    scores = [{"fips": f"{i:05d}", "label": int(y[i]), "cal": float(oof[i]), "raw": float(oof[i]),
               "decile": ""} for i in range(n)]
    flagged = flag(scores, DEFAULTS)
    check("injected flipped label ranks first", flagged and flagged[0]["fips"] == f"{flip:05d}")
    check("its rule is positive_bottom_decile", flagged and flagged[0]["rule"] == "positive_bottom_decile")
    check("flagged share stays small", len(flagged) < 0.05 * n)
    check("quantile interpolates", quantile([0, 1, 2, 3, 4], 0.1) == 0.4)

    class CA:                              # county_aggregator's rule surface
        RESTRICTIVE_TYPES = {"moratorium", "zoning_restriction", "ban"}
        ENACTED_STATUSES = {"passed", "approved", "enacted"}
        DIRECTION_AMBIGUOUS_STATUSES = {"approved"}

        @staticmethod
        def _type_tokens(cell):
            return {t.strip().lower() for t in str(cell or "").split(";") if t.strip()}
    check("enacted moratorium counts", counts_toward_label(
        {"Opposition Type": "moratorium; ordinance", "Status": "passed"}, CA))
    check("approved without win does not count (project approval)", not counts_toward_label(
        {"Opposition Type": "zoning_restriction", "Status": "approved", "Community Outcome": "loss"}, CA))
    check("approved with win counts", counts_toward_label(
        {"Opposition Type": "zoning_restriction", "Status": "approved", "Community Outcome": "win"}, CA))
    import county_aggregator as real
    check("the real aggregator exposes the rule this audit imports",
          all(hasattr(real, k) for k in ("load_frame", "norm_county", "norm_state", "_type_tokens",
                                         "RESTRICTIVE_TYPES", "ENACTED_STATUSES",
                                         "DIRECTION_AMBIGUOUS_STATUSES")))

    g = globals()
    saved = {k: g[k] for k in ("OUT_CSV", "CONFIG", "AGG_CSV")}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            g.update(OUT_CSV=os.path.join(tmp, "w.csv"), CONFIG=os.path.join(tmp, "none.json"),
                     AGG_CSV=os.path.join(tmp, "none.csv"))
            code, rows = run(scores_path=os.path.join(tmp, "missing.csv"))
            check("missing scores: SKIP, exit 0, nothing written",
                  code == 0 and rows == [] and not os.path.exists(OUT_CSV))
            bad = os.path.join(tmp, "bad.csv")
            with open(bad, "w", newline="\n") as fh:
                fh.write("fips,score\n01001,0.1\n")
            check("scores without OOF columns: SKIP", run(scores_path=bad)[1] == [])
            sp = os.path.join(tmp, "s.csv")
            with open(sp, "w", newline="\n") as fh:
                fh.write("fips,raw_oof_score,calibrated_score,score_decile,has_enacted_restrictive\n")
                for s in scores:
                    fh.write(f"{s['fips']},{s['raw']:.4f},{s['cal']:.4f},5,{s['label']}\n")
            rec = os.path.join(tmp, "r.txt")
            with open(rec, "w") as fh:
                fh.write(f"{flip:05d}\n")
            code, rows = run(scores_path=sp, with_evidence=False, recall_path=rec)
            raw = open(OUT_CSV, "rb").read()
            check("worklist written with the flip first", rows and rows[0]["fips"] == f"{flip:05d}")
            check("worklist header matches the contract", raw.split(b"\n")[0].decode() == ",".join(OUT_COLS))
            check("LF only, no em-dash", b"\r" not in raw and "—".encode() not in raw)
    finally:
        g.update(saved)
    print(f"{len(fails)} failure(s)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Out-of-fold label disagreement worklist (spec 005 US4)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scores", help="scores CSV (default data/county_policy_scores.csv)")
    ap.add_argument("--out", help="worklist path (default data/label_disagreement_worklist.csv)")
    ap.add_argument("--no-evidence", action="store_true", help="skip the master-row evidence lookup")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--check-recall", help="file of FIPS, one per line; prints how many are flagged")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.out:
        globals()["OUT_CSV"] = a.out
    return run(scores_path=a.scores, write=not a.no_write, with_evidence=not a.no_evidence,
               recall_path=a.check_recall)[0]


if __name__ == "__main__":
    sys.exit(main())
