"""
cost_translation.py — Phase 4: cost-translation layer (first iteration).

Converts predicted/observed opposition outcomes (delay months, block events)
into DOLLAR RANGES using published industry anchors. Every anchor is sourced
and dated; every formula is documented; every output is a range with its
assumptions attached. This module contains NO estimation of opposition
effects — it only prices observables that other layers (delay measurement,
outcome model, matched controls) produce.

Additive only. Writes three NEW files:

  data/cost_anchors.csv                the anchor registry (value ranges,
                                       units, sources, dates, confidence)
  data/cost_translation_methodology.md the auditable methodology document
  data/cost_translation_demo.csv       illustrative application to projects
                                       with verified decision dates AND known
                                       MW (never imputed; labeled non-client-
                                       facing)

Defensibility rules honored:
  - Anchors come from published industry sources (JLL, Turner & Townsend,
    CBRE), each with source and as-of date in the registry. When an anchor is
    an assumption rather than a published figure, its confidence is marked
    'assumption' and the methodology says so.
  - Outputs are ranges built from anchor ranges, never point estimates.
  - Delay pricing distinguishes DEFERRED value (revenue that arrives later)
    from DESTROYED value (escalation, carrying). Gross deferred revenue is
    reported only as an upper-bound exposure, clearly labeled.
  - MW is never imputed for dollar output: no capacity, no dollar figure.
  - Nothing here is client-facing until the delay measurement reaches
    adequate n and the anchors are re-verified at publication time.

Run from repo root:  python3 cost_translation.py
"""

from __future__ import annotations

import csv
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

LIFECYCLES_CSV = P("data", "project_lifecycles.csv")
OUT_ANCHORS = P("data", "cost_anchors.csv")
OUT_METHOD = P("data", "cost_translation_methodology.md")
OUT_DEMO = P("data", "cost_translation_demo.csv")

# ---------------------------------------------------------------------------
# Anchor registry. value_low/value_high define the range used everywhere.
# confidence: 'published' = figure taken directly from the cited source;
#             'assumption' = parameter chosen by us, flagged in methodology.
# ---------------------------------------------------------------------------

ANCHORS = [
    dict(anchor_id="capex_per_mw",
         description="Shell-and-core construction cost per MW, global average, standard (non-AI-optimized) build",
         value_low=10_000_000, value_high=12_000_000, unit="USD per MW",
         source="JLL Global Data Center Market Outlook 2026 (global avg $11.3M/MW; screening range $10-12M/MW)",
         source_date="2026-01", confidence="published"),
    dict(anchor_id="capex_per_mw_ai",
         description="Construction cost per MW for AI-optimized (liquid-cooled, high-density) facilities, excluding IT fit-out",
         value_low=20_000_000, value_high=25_000_000, unit="USD per MW",
         source="Turner & Townsend Data Centre Cost Index 2025-2026; JLL 2026 Outlook (AI-optimized exceeds $20M/MW)",
         source_date="2025-11", confidence="published"),
    dict(anchor_id="construction_escalation",
         description="Annual construction cost escalation for air-cooled data center builds",
         value_low=0.055, value_high=0.06, unit="fraction per year",
         source="Turner & Townsend index 2025-2026 (5.5% YoY, 2025); JLL 2026 forecast (+6%)",
         source_date="2025-11", confidence="published"),
    dict(anchor_id="wholesale_rent_kw_month",
         description="Wholesale colocation asking rent, North America primary markets, 250-500 kW requirements",
         value_low=150, value_high=235, unit="USD per kW per month",
         source="CBRE North America Data Center Trends H2 2025 (avg $196.25/kW/mo, +6.6% YoY); CBRE Global Data Center Trends 2026 Q1 (NoVA $190-235, Chicago $200-230); low end reflects smaller/secondary markets",
         source_date="2026-06", confidence="published"),
    dict(anchor_id="carrying_rate",
         description="Annual carrying cost rate applied to capital already deployed while a project is delayed (financing/opportunity cost)",
         value_low=0.07, value_high=0.10, unit="fraction per year",
         source="ASSUMPTION. Reference floor: CBRE H1 2025 notes Class-A data center cap rates at 10-yr Treasury +100-150 bps; developer WACC typically higher. Set per engagement.",
         source_date="2025-09", confidence="assumption"),
    dict(anchor_id="predevelopment_share",
         description="Predevelopment spend at risk in a block (land control, engineering, legal, entitlement) as share of total capex",
         value_low=0.01, value_high=0.03, unit="fraction of capex",
         source="ASSUMPTION. No published benchmark identified; varies widely with land strategy. Set per engagement; replace with actuals when known.",
         source_date="2026-07", confidence="assumption"),
]


def anchor(aid: str) -> dict:
    return next(a for a in ANCHORS if a["anchor_id"] == aid)


# ---------------------------------------------------------------------------
# Translation functions (pure; all return (low, high) USD ranges)
# ---------------------------------------------------------------------------

def escalation_cost(mw: float, delay_months: float, ai_optimized: bool = False):
    """Destroyed value: construction cost drift while the project waits.
    capex(MW) x escalation_rate x (months/12). Applies to pre-construction
    delay of a project that ultimately proceeds."""
    cap = anchor("capex_per_mw_ai" if ai_optimized else "capex_per_mw")
    esc = anchor("construction_escalation")
    lo = mw * cap["value_low"] * esc["value_low"] * (delay_months / 12)
    hi = mw * cap["value_high"] * esc["value_high"] * (delay_months / 12)
    return lo, hi


def carrying_cost(deployed_capital: float, delay_months: float):
    """Destroyed value: financing/opportunity cost on capital already deployed
    (land, engineering, deposits) during the delay."""
    r = anchor("carrying_rate")
    return (deployed_capital * r["value_low"] * delay_months / 12,
            deployed_capital * r["value_high"] * delay_months / 12)


def deferred_revenue_exposure(mw: float, delay_months: float):
    """UPPER-BOUND EXPOSURE, not destroyed value: gross wholesale revenue that
    arrives later because delivery slips. Reported separately and labeled;
    the economic cost is the time value of this deferral, not its face
    amount."""
    r = anchor("wholesale_rent_kw_month")
    kw = mw * 1000
    return kw * r["value_low"] * delay_months, kw * r["value_high"] * delay_months


def block_sunk_cost(mw: float, ai_optimized: bool = False):
    """Predevelopment capital at risk when a project is blocked, as a share of
    intended capex. ASSUMPTION-based anchor; replace with actuals when
    known."""
    cap = anchor("capex_per_mw_ai" if ai_optimized else "capex_per_mw")
    sh = anchor("predevelopment_share")
    return (mw * cap["value_low"] * sh["value_low"],
            mw * cap["value_high"] * sh["value_high"])


def fmt_usd(x: float) -> str:
    if x >= 1_000_000:
        return f"${x/1_000_000:,.1f}M"
    return f"${x:,.0f}"


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

METHOD_TEMPLATE = """# Cost-Translation Layer — Methodology (Phase 4, first iteration)

Generated {today} by `cost_translation.py`. **Internal — NOT client-facing**
until (1) delay measurement reaches adequate n, and (2) anchors are
re-verified at publication time. Anchor values age; the registry records
as-of dates.

## What this layer does

It prices observables produced elsewhere in the platform. It does not
estimate opposition's effects. Inputs come from `project_lifecycles.csv`
(verified `days_announced_to_decision`) and, eventually, the outcome/delay
models. Every dollar figure is a range derived from the anchor registry
(`cost_anchors.csv`), where each anchor carries its source, date, and a
confidence flag (`published` vs `assumption`).

## Components

**1. Construction-cost escalation (destroyed value).**
`capex_per_mw x MW x escalation_rate x (delay_months / 12)`
A project delayed pre-construction faces higher build costs when it finally
starts. Anchors: JLL $10-12M/MW standard ($20-25M AI-optimized); T&T/JLL
escalation 5.5-6%/yr.

**2. Carrying cost on deployed capital (destroyed value).**
`deployed_capital x carrying_rate x (delay_months / 12)`
Financing/opportunity cost on capital already committed (land control,
engineering, deposits). `carrying_rate` (7-10%/yr) is an ASSUMPTION anchor —
reference floor is CBRE's Class-A cap rates at 10-yr Treasury +100-150 bps;
set per engagement. Requires a deployed-capital figure; never guessed.

**3. Deferred revenue exposure (upper bound, NOT destroyed value).**
`MW x 1000 kW x rent_per_kw_month x delay_months`
Gross wholesale revenue that arrives later. The economic cost is the time
value of the deferral, not this face amount; it is reported only as a
labeled exposure ceiling. Anchor: CBRE NA primary-market asking rents
$150-235/kW/mo.

**4. Block sunk cost (destroyed value, assumption-based).**
`capex x predevelopment_share`
Predevelopment capital at risk when a project is blocked. The 1-3% share is
an ASSUMPTION with no published benchmark identified; replace with project
actuals whenever available.

## Binding limitations

- Delay inputs currently come from {n_dated} projects with verified decision
  dates, all with month-precision announced dates (up to ~30 days error
  each). No opposition-attributable delay exists yet — that requires the
  matched-control comparison at adequate n. Applying this layer to raw
  announced-to-decision spans prices the SPAN, not opposition's effect.
- Anchors are national/global averages; market-level variation is 25-40%
  (Turner & Townsend). Market-specific anchors should replace these for any
  engagement-grade estimate.
- Two anchors are assumptions (`carrying_rate`, `predevelopment_share`) and
  are flagged as such everywhere they are used.
- Standard vs AI-optimized capex differs >2x; the demo uses the standard
  anchor unless a project is known AI-optimized. Ranges do not capture
  tenant IT fit-out (up to $25M/MW additional, T&T).

## Worked example

A 100 MW standard project delayed 6 months, with $15M deployed:
- Escalation: {ex_esc}
- Carrying on deployed capital: {ex_carry}
- Deferred revenue exposure (upper bound, labeled): {ex_defer}

## Anchor registry

See `data/cost_anchors.csv`. Re-verify all anchors before any external use.
"""


def main() -> int:
    # 1) anchor registry
    with open(OUT_ANCHORS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ANCHORS[0].keys()))
        w.writeheader()
        w.writerows(ANCHORS)

    # 2) demo application: dated projects with known MW only
    demo_rows = []
    if os.path.exists(LIFECYCLES_CSV):
        with open(LIFECYCLES_CSV, newline="", encoding="utf-8-sig") as fh:
            life = list(csv.DictReader(fh))
        for r in life:
            if not (r["decision_date"] and r["days_announced_to_decision"]):
                continue
            months = int(r["days_announced_to_decision"]) / 30.44
            try:
                mw = float(r["capacity_mw"])
            except (ValueError, TypeError):
                mw = None
            row = {
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "lifecycle_outcome": r["lifecycle_outcome"],
                "delay_days": r["days_announced_to_decision"],
                "announced_precision": r["announced_precision"],
                "capacity_mw": r["capacity_mw"],
            }
            if mw and mw > 0:
                e_lo, e_hi = escalation_cost(mw, months)
                d_lo, d_hi = deferred_revenue_exposure(mw, months)
                row.update({
                    "escalation_cost_low": f"{e_lo:.0f}",
                    "escalation_cost_high": f"{e_hi:.0f}",
                    "deferred_revenue_exposure_low": f"{d_lo:.0f}",
                    "deferred_revenue_exposure_high": f"{d_hi:.0f}",
                    "note": "ILLUSTRATIVE ONLY: prices the raw announced-to-decision span, not opposition-attributable delay; month-precision announced date",
                })
                if r["lifecycle_outcome"] == "blocked_confirmed":
                    b_lo, b_hi = block_sunk_cost(mw)
                    row.update({"block_sunk_cost_low": f"{b_lo:.0f}",
                                "block_sunk_cost_high": f"{b_hi:.0f}"})
                else:
                    row.update({"block_sunk_cost_low": "", "block_sunk_cost_high": ""})
            else:
                row.update({k: "" for k in
                            ("escalation_cost_low", "escalation_cost_high",
                             "deferred_revenue_exposure_low",
                             "deferred_revenue_exposure_high",
                             "block_sunk_cost_low", "block_sunk_cost_high")})
                row["note"] = "capacity_mw unknown: no dollar output (never imputed)"
            demo_rows.append(row)

    demo_cols = ["project_id", "project_name", "lifecycle_outcome", "delay_days",
                 "announced_precision", "capacity_mw",
                 "escalation_cost_low", "escalation_cost_high",
                 "deferred_revenue_exposure_low", "deferred_revenue_exposure_high",
                 "block_sunk_cost_low", "block_sunk_cost_high", "note"]
    with open(OUT_DEMO, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=demo_cols)
        w.writeheader()
        w.writerows(demo_rows)

    # 3) methodology doc with worked example
    ex_esc = escalation_cost(100, 6)
    ex_carry = carrying_cost(15_000_000, 6)
    ex_defer = deferred_revenue_exposure(100, 6)
    with open(OUT_METHOD, "w", encoding="utf-8") as fh:
        fh.write(METHOD_TEMPLATE.format(
            today=date.today().isoformat(),
            n_dated=len(demo_rows),
            ex_esc=f"{fmt_usd(ex_esc[0])} - {fmt_usd(ex_esc[1])}",
            ex_carry=f"{fmt_usd(ex_carry[0])} - {fmt_usd(ex_carry[1])} (assumption-based rate)",
            ex_defer=f"{fmt_usd(ex_defer[0])} - {fmt_usd(ex_defer[1])}",
        ))

    priced = sum(1 for r in demo_rows if r["escalation_cost_low"])
    print(f"anchors: {len(ANCHORS)} ({sum(1 for a in ANCHORS if a['confidence']=='published')} published, "
          f"{sum(1 for a in ANCHORS if a['confidence']=='assumption')} assumption)")
    print(f"demo: {len(demo_rows)} dated projects, {priced} priced (MW known), "
          f"{len(demo_rows) - priced} withheld (MW unknown)")

    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = [f"{f}:{i}" for f in (OUT_ANCHORS, OUT_METHOD, OUT_DEMO)
            for i, l in enumerate(open(f, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT FAILED:", hits[:10])
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
