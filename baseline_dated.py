"""
baseline_dated.py — dated baseline construction (control-side time axes).

Builds the dated control frame that the matched DELAY comparison requires:
control-side records with a time origin (announced date) and an event or
censor anchor. Until now the baseline universe carried no dates at all, so
opposition's effect on timing was unmeasurable. Two tiers:

  1. INTERNAL (automatic): proposals-derived baseline records already carry
     announced_date / announced_precision / decision_date /
     last_status_update after the control_group.py passthrough. This module
     assembles them into an analysis-ready frame.
  2. EXTERNAL (optional ingest): data/baseline_dated_external.csv — any
     externally sourced dated records (ISO large-load queues, permit
     portals, commercial trackers). Schema-validated; rows failing
     validation are rejected with reasons, never silently dropped.

Outputs (all additive, nothing existing is modified):
  data/baseline_dated.csv        analysis-ready dated frame, both tiers
  data/baseline_dated_notes.md   coverage, definitions, binding limitations

Defensibility rules honored:
  - A control's decision date is used ONLY if verified (present in the
    lifecycle passthrough, i.e. data/project_decision_dates.csv). Decided
    controls WITHOUT a verified decision date are censored at
    last_status_update and flagged `decided_undated` — their span is a
    lower bound, never an observed decision time.
  - Year-precision announced dates are excluded from the time axis (never
    floored to Jan 1).
  - External rows must carry source and as_of; rows without them are
    rejected. External decision dates are marked unverified unless a
    source_url is provided.
  - No scorekeeping vocabulary; leak audit before exit.

Run from repo root:  python3 baseline_dated.py
Depends on data/baseline_universe.csv (run control_group.py first).
"""

from __future__ import annotations

import csv
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

UNIVERSE_CSV = P("data", "baseline_universe.csv")
EXTERNAL_CSV = P("data", "baseline_dated_external.csv")   # optional
OUT_FRAME = P("data", "baseline_dated.csv")
OUT_NOTES = P("data", "baseline_dated_notes.md")

EXTERNAL_REQUIRED = ["source", "as_of", "name", "state", "announced_date"]
EXTERNAL_OPTIONAL = ["county", "capacity_mw", "decision_date", "status",
                     "source_url", "operator"]

DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None, ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s, "day"
    if re.match(r"^\d{4}-\d{2}$", s):
        return s + "-01", "month"
    if re.match(r"^\d{4}$", s):
        return None, "year"          # excluded from time axis
    return None, ""


def days_between(a, b):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (ValueError, TypeError):
        return None


def main() -> int:
    if not os.path.exists(UNIVERSE_CSV):
        print("ERROR: baseline_universe.csv missing — run control_group.py first")
        return 1
    uni = load_csv(UNIVERSE_CSV)
    if "announced_date" not in uni[0]:
        print("ERROR: baseline_universe.csv lacks date columns — "
              "update control_group.py (dated-baseline passthrough) and rerun")
        return 1

    frame = []
    tier_counts = {"internal": 0, "external": 0}
    rejected_external = []

    # ---- Tier 1: internal (proposals-derived) ----
    for r in uni:
        if r["source"] not in ("proposals_opposed", "proposals_unopposed"):
            continue
        origin, prec = parse_date(r["announced_date"][:10] if r["announced_date"] else "")
        # universe stores full ISO already; keep month/day only
        if r["announced_precision"] in ("day", "month") and r["announced_date"]:
            origin, prec = r["announced_date"], r["announced_precision"]
        if not origin or prec not in ("day", "month"):
            continue
        decided = r["decided"] == "yes"
        dec_date = (r["decision_date"] or "").strip()
        censor = (r["last_status_update"] or "").strip()

        if decided and dec_date:
            end, end_kind = dec_date, "decision_verified"
        elif decided:
            end, end_kind = censor, "decided_undated"   # lower bound only
        else:
            end, end_kind = censor, "censored_pending"
        span = days_between(origin, end) if end else None
        if span is None or span <= 0:
            continue
        frame.append({
            "record_id": r["universe_id"], "tier": "internal",
            "source": r["source"], "name": r["name"], "state": r["state"],
            "county": r["county"], "capacity_mw": r["capacity_mw"],
            "opposed_flag": r["opposed_flag"],
            "lifecycle_outcome": r["lifecycle_outcome"],
            "origin_date": origin, "origin_precision": prec,
            "end_date": end, "end_kind": end_kind,
            "span_days": span,
            "event_observed": 1 if end_kind == "decision_verified" else 0,
            "source_url": "",
        })
        tier_counts["internal"] += 1

    # ---- Tier 2: external ingest (optional) ----
    if os.path.exists(EXTERNAL_CSV):
        ext = load_csv(EXTERNAL_CSV)
        for i, r in enumerate(ext, 2):     # header = line 1
            missing = [c for c in EXTERNAL_REQUIRED if not (r.get(c) or "").strip()]
            if missing:
                rejected_external.append((i, f"missing required: {missing}"))
                continue
            origin, prec = parse_date(r["announced_date"])
            if not origin:
                rejected_external.append((i, f"unusable announced_date "
                                             f"{r['announced_date']!r} (year-only excluded)"))
                continue
            dec, dec_prec = parse_date(r.get("decision_date", ""))
            if dec:
                end, end_kind = dec, ("decision_verified" if (r.get("source_url") or "").strip()
                                      else "decision_unverified")
            else:
                end, end_kind = r["as_of"].strip(), "censored_asof"
            span = days_between(origin, end) if end else None
            if span is None or span <= 0:
                rejected_external.append((i, "non-positive or uncomputable span"))
                continue
            frame.append({
                "record_id": f"ext_{i:05d}", "tier": "external",
                "source": r["source"].strip(), "name": r["name"].strip(),
                "state": r["state"].strip(), "county": (r.get("county") or "").strip(),
                "capacity_mw": (r.get("capacity_mw") or "").strip(),
                "opposed_flag": "no",     # externals are presumed-unopposed comparables
                "lifecycle_outcome": (r.get("status") or "").strip(),
                "origin_date": origin, "origin_precision": prec,
                "end_date": end, "end_kind": end_kind,
                "span_days": span,
                "event_observed": 1 if end_kind == "decision_verified" else 0,
                "source_url": (r.get("source_url") or "").strip(),
            })
            tier_counts["external"] += 1

    cols = ["record_id", "tier", "source", "name", "state", "county",
            "capacity_mw", "opposed_flag", "lifecycle_outcome",
            "origin_date", "origin_precision", "end_date", "end_kind",
            "span_days", "event_observed", "source_url"]
    with open(OUT_FRAME, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(frame)

    # ---- coverage summary ----
    opp = [r for r in frame if r["opposed_flag"] == "yes"]
    ctl = [r for r in frame if r["opposed_flag"] == "no"]
    ctl_ev = sum(r["event_observed"] for r in ctl)
    opp_ev = sum(r["event_observed"] for r in opp)
    ctl_du = sum(1 for r in ctl if r["end_kind"] == "decided_undated")

    L = []
    w = L.append
    w("# Dated Baseline — Coverage and Definitions")
    w("")
    w(f"Generated {date.today().isoformat()} by `baseline_dated.py`.")
    w("")
    w("## Frame")
    w("")
    w(f"- Records with a usable time origin (day/month announced): **{len(frame)}** "
      f"({tier_counts['internal']} internal, {tier_counts['external']} external)")
    w(f"- Opposed: {len(opp)} ({opp_ev} with a verified decision date)")
    w(f"- Control (unopposed): {len(ctl)} ({ctl_ev} with a verified decision date; "
      f"{ctl_du} decided but undated → censored lower bounds)")
    w("")
    w("## End-anchor kinds")
    w("")
    w("- `decision_verified` — verified, sourced decision date; observed event.")
    w("- `decided_undated` — outcome is terminal but no verified date exists; "
      "span to last status update is a LOWER BOUND, treated as censored. "
      "Common on the control side (unopposed advances rarely produce a "
      "datable vote — same structural asymmetry documented in the survival "
      "model).")
    w("- `censored_pending` / `censored_asof` — no terminal outcome yet.")
    w("")
    w("## External ingest")
    w("")
    if os.path.exists(EXTERNAL_CSV):
        w(f"- `baseline_dated_external.csv` present: {tier_counts['external']} "
          f"accepted, {len(rejected_external)} rejected.")
        for line_no, reason in rejected_external[:15]:
            w(f"  - line {line_no}: {reason}")
    else:
        w("- `data/baseline_dated_external.csv` not present (optional). Schema "
          "when adding external dated sources (ISO large-load queues, permit "
          "portals, commercial trackers):")
        w(f"  - required: {', '.join(EXTERNAL_REQUIRED)}")
        w(f"  - optional: {', '.join(EXTERNAL_OPTIONAL)}")
        w("  - external decision dates count as verified only with a source_url; "
          "year-only announced dates are rejected.")
    w("")
    w("## Binding limitations")
    w("")
    w("- Control-side verified decision dates are currently scarce; most "
      "control spans are censored lower bounds. Time-to-decision comparisons "
      "must use survival methods (censoring-aware), never mean/median of raw "
      "spans across groups with different censoring rates.")
    w("- 'Unopposed' means no opposition recorded in the tracker — absence of "
      "evidence, not verified absence.")
    w("- This module constructs data only; inference belongs to the survival "
      "and comparison modules with their stated limitations.")
    w("")

    with open(OUT_NOTES, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    print(f"frame: {len(frame)} dated records ({len(opp)} opposed / {len(ctl)} control) | "
          f"events: {opp_ev} opposed, {ctl_ev} control | "
          f"external: {tier_counts['external']} accepted, {len(rejected_external)} rejected")
    print(f"wrote {os.path.relpath(OUT_FRAME, ROOT)}, notes")

    pat = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)
    hits = [f for f in (OUT_FRAME, OUT_NOTES)
            for i, l in enumerate(open(f, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:5])
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
