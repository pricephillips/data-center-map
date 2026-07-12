"""
control_comparison.py — Phase 3, first artifact: descriptive comparison of
opposed projects vs. matched controls, with covariate balance diagnostics
and control-tier sensitivity.

Purpose: validate the Phase 2 matching BEFORE any modeling investment.
Everything in the output is descriptive. Nothing here is causal, and the
report says so wherever a reader could be tempted to read it otherwise.

Additive only. Reads the Phase 1/2 outputs, writes one NEW file:

  data/control_comparison.md   internal diagnostic report (regenerated per run;
                               all figures re-derived from current CSVs at
                               generation time — statistic reproducibility)

Report sections:
  1. Sample composition (opposed set, control pool by tier, exclusions)
  2. Covariate balance — standardized mean differences (SMD) between the
     opposed set and its matched controls, overall and per control tier.
     |SMD| < 0.10 well balanced, 0.10-0.25 moderate, > 0.25 imbalanced.
  3. Political-geography distribution, opposed vs. eligible pool (descriptive)
  4. Outcome distribution among decided opposed projects (ladder vocabulary)
  5. Match-quality flags to triage (no_shared_covariates, national fallback)
  6. Limitations (inherited from control_group_notes.md, restated)

Run from repo root:  python3 control_comparison.py
Depends on data/baseline_universe.csv and data/matched_controls.csv
(run project_resolution.py then control_group.py first).
"""

from __future__ import annotations

import csv
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

UNIVERSE_CSV = P("data", "baseline_universe.csv")
MATCHES_CSV = P("data", "matched_controls.csv")
LIFECYCLES_CSV = P("data", "project_lifecycles.csv")
OUT_REPORT = P("data", "control_comparison.md")

TIERS = ["proposals_unopposed", "ai_centers", "atlas"]


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def parse_float(v) -> float | None:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def sd(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def smd(a: list[float], b: list[float]) -> float | None:
    """Standardized mean difference with pooled SD."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb, sa, sb = mean(a), mean(b), sd(a), sd(b)
    pooled = math.sqrt(((sa or 0) ** 2 + (sb or 0) ** 2) / 2)
    if not pooled:
        return None
    return (ma - mb) / pooled


def fmt(x, nd=3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def smd_flag(s: float | None) -> str:
    if s is None:
        return "insufficient data"
    a = abs(s)
    if a < 0.10:
        return "well balanced"
    if a <= 0.25:
        return "moderate imbalance"
    return "IMBALANCED — down-weight or re-match"


def collect(matches: list[dict], key_op: str, key_ct: str,
            transform=lambda x: x) -> tuple[list[float], list[float]]:
    """Paired covariate values across match rows (rows lacking either side skipped)."""
    a, b = [], []
    for m in matches:
        x, y = parse_float(m[key_op]), parse_float(m[key_ct])
        if x is None or y is None:
            continue
        try:
            a.append(transform(x))
            b.append(transform(y))
        except ValueError:
            continue
    return a, b


def main() -> int:
    for f in (UNIVERSE_CSV, MATCHES_CSV, LIFECYCLES_CSV):
        if not os.path.exists(f):
            print(f"ERROR: {os.path.relpath(f, ROOT)} missing — run "
                  "project_resolution.py and control_group.py first")
            return 1

    universe = load_csv(UNIVERSE_CSV)
    matches = load_csv(MATCHES_CSV)
    life = load_csv(LIFECYCLES_CSV)

    opposed = [r for r in universe if r["opposed_flag"] == "yes"]
    pool = [r for r in universe if r["opposed_flag"] == "no"
            and not r["exclusion_reason"] and r["source"] != "proposals_opposed"]
    excluded = [r for r in universe if r["exclusion_reason"]]
    matched_projects = {m["opposed_project_id"] for m in matches}

    lines: list[str] = []
    w = lines.append

    w("# Opposed vs. Matched Controls — Descriptive Comparison")
    w("")
    w(f"Generated {date.today().isoformat()} by `control_comparison.py`. "
      "All figures re-derived from the current CSVs at generation time.")
    w("")
    w("**This report is descriptive and diagnostic only.** Differences shown "
      "here are associations in an observational, selection-affected sample. "
      "Nothing in this document quantifies the effect or cost of opposition, "
      "and no figure here should appear in a client-facing deliverable.")
    w("")

    # ---- 1. Sample composition ----
    w("## 1. Sample composition")
    w("")
    w(f"- Opposed projects (treatment side): **{len(opposed)}**, of which "
      f"{sum(1 for r in opposed if r['decided'] == 'yes')} decided / "
      f"{sum(1 for r in opposed if r['decided'] != 'yes')} pending")
    tier_counts = Counter(r["source"] for r in pool)
    w(f"- Eligible control pool: **{len(pool)}** — " +
      ", ".join(f"{t}: {tier_counts.get(t, 0)}" for t in TIERS))
    excl_counts = Counter(r["exclusion_reason"] for r in excluded)
    w(f"- Excluded from control pool: **{len(excluded)}** — " +
      ", ".join(f"{k}: {v}" for k, v in excl_counts.most_common()))
    w(f"- Matched: **{len(matched_projects)}** opposed projects × k=3 → "
      f"{len(matches)} match rows")
    w("")

    # ---- 2. Covariate balance ----
    w("## 2. Covariate balance (opposed vs. their matched controls)")
    w("")
    w("Standardized mean differences across match rows. |SMD| < 0.10 = well "
      "balanced; 0.10–0.25 = moderate; > 0.25 = imbalanced.")
    w("")
    segments = [("all tiers", matches)] + [
        (t, [m for m in matches if m["control_source"] == t]) for t in TIERS
    ]
    for label, seg in segments:
        if not seg:
            w(f"**{label}** — no matches in this tier.")
            w("")
            continue
        a_m, b_m = collect(seg, "opposed_county_margin_2024", "control_county_margin_2024")
        s_margin = smd(a_m, b_m)
        a_c, b_c = collect(seg, "opposed_capacity_mw", "control_capacity_mw",
                           transform=lambda x: math.log10(x) if x > 0 else float("nan"))
        a_c = [x for x in a_c if not math.isnan(x)]
        b_c = [x for x in b_c if not math.isnan(x)]
        s_cap = smd(a_c, b_c)
        w(f"**{label}** ({len(seg)} match rows)")
        w(f"- County 2024 margin: opposed mean {fmt(mean(a_m))}, control mean "
          f"{fmt(mean(b_m))}, SMD {fmt(s_margin)} — {smd_flag(s_margin)} "
          f"(n pairs: {len(a_m)})")
        w(f"- log10 capacity MW: opposed mean {fmt(mean(a_c))}, control mean "
          f"{fmt(mean(b_c))}, SMD {fmt(s_cap)} — {smd_flag(s_cap)} "
          f"(n pairs: {len(a_c)}; capacity is sparse outside the proposals tier)")
        w("")

    # ---- 3. Political geography, opposed vs eligible pool ----
    w("## 3. Political geography (descriptive)")
    w("")
    op_margins = [parse_float(r["county_margin_2024"]) for r in opposed]
    op_margins = [x for x in op_margins if x is not None]
    pool_margins = [parse_float(r["county_margin_2024"]) for r in pool]
    pool_margins = [x for x in pool_margins if x is not None]
    w(f"- Opposed projects sit in counties with mean 2024 margin "
      f"{fmt(mean(op_margins))} (n={len(op_margins)}); the eligible control "
      f"pool mean is {fmt(mean(pool_margins))} (n={len(pool_margins)}).")
    w("- This is a raw compositional difference between two differently-"
      "constructed samples. It describes where tracked opposition occurs; it "
      "does not measure any political driver of opposition.")
    w("")

    # ---- 4. Outcome distribution among decided opposed projects ----
    w("## 4. Outcomes among decided opposed projects")
    w("")
    decided = [r for r in life if r["decided"] == "yes"
               and int(r["n_opposition_events"] or 0) > 0]
    oc = Counter(r["lifecycle_outcome"] for r in decided)
    total = len(decided)
    w(f"Of **{total}** decided + opposed projects:")
    for k in ("advanced_confirmed", "blocked_confirmed"):
        n = oc.get(k, 0)
        pct = f"{100 * n / total:.0f}%" if total else "n/a"
        w(f"- `{k}`: {n} ({pct})")
    w("")
    w("Decided means terminal dispositions only; pending and mixed cases are "
      "excluded, consistent with the platform's decided-case rule. These "
      "shares describe the tracked opposed sample only — they are not block "
      "rates for data center projects in general.")
    w("")

    # ---- 5. Delay observables (gated: requires >=5 dated projects) ----
    dated = [r for r in life if r["decision_date"] and r["days_announced_to_decision"]]
    w("## 5. Delay observables (verified decision dates only)")
    w("")
    if len(dated) < 5:
        w(f"Only {len(dated)} projects have verified decision dates with computable "
          "delay; distributional statistics are withheld below n=5. Grow via the "
          "date-recovery worklist.")
    else:
        vals = sorted(int(r["days_announced_to_decision"]) for r in dated)
        med = vals[len(vals) // 2]
        precisions = Counter(r["announced_precision"] for r in dated)
        w(f"- {len(dated)} decided+opposed projects have verified decision dates: "
          f"announced-to-decision spans {vals[0]}–{vals[-1]} days, median {med} days.")
        w(f"- Announced-date precision of these rows: " +
          ", ".join(f"{k}: {v}" for k, v in precisions.items()) +
          ". Month-precision announced dates are floored to the 1st, so those "
          "delays carry up to ~30 days of error each.")
        by_outcome = {}
        for r in dated:
            by_outcome.setdefault(r["lifecycle_outcome"], []).append(int(r["days_announced_to_decision"]))
        for k, v in sorted(by_outcome.items()):
            v = sorted(v)
            w(f"- `{k}` (n={len(v)}): {v[0]}–{v[-1]} days, median {v[len(v)//2]}.")
        w("- These are raw spans within the opposed sample: NOT opposition-"
          "attributable delay (that requires the matched-control comparison at "
          "adequate n) and not client-facing.")
    w("")

    # ---- 6. Match-quality triage ----
    w("## 6. Match-quality flags")
    w("")
    weak = [m for m in matches if m["match_basis"] == "no_shared_covariates"]
    fallback = [m for m in matches if m["match_scope"] == "national_fallback"]
    w(f"- `no_shared_covariates` matches (state/tier only): **{len(weak)}** — "
      "down-weight or manually review before any use.")
    w(f"- `national_fallback` matches (no in-state pool): **{len(fallback)}**, "
      f"covering {len({m['opposed_project_id'] for m in fallback})} opposed "
      "projects. Growing the proposals_unopposed tier is the fix.")
    tier_use = Counter(m["control_source"] for m in matches)
    w(f"- Tier usage across all matches: " +
      ", ".join(f"{t}: {tier_use.get(t, 0)}" for t in TIERS) + ".")
    w("")

    # ---- 6. Limitations ----
    w("## 7. Limitations (binding)")
    w("")
    w("- \"Unopposed\" = no opposition recorded in the tracker; absence of "
      "evidence, not verified absence.")
    w("- The atlas tier is survivorship-biased (built facilities) and lacks "
      "capacity data; sensitivity across tiers in §2 exists for exactly this "
      "reason.")
    w("- Matching balances only observed covariates (political margin, "
      "capacity). Unobserved differences (land use context, utility posture, "
      "media environment) remain.")
    w("- No causal, effect-size, or cost interpretation is supported. See "
      "`data/control_group_notes.md`.")
    w("")

    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    # console summary
    print(f"wrote {os.path.relpath(OUT_REPORT, ROOT)}")
    for label, seg in segments:
        a_m, b_m = collect(seg, "opposed_county_margin_2024", "control_county_margin_2024")
        print(f"  balance[{label}]: margin SMD {fmt(smd(a_m, b_m))} "
              f"({smd_flag(smd(a_m, b_m))}, n={len(a_m)})")

    # leak audit
    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = [f"line {i}: {l.strip()[:80]}"
            for i, l in enumerate(open(OUT_REPORT, encoding="utf-8"), 1)
            if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:")
        for h in hits[:10]:
            print("  " + h)
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
