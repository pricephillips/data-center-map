# Cost Layer Notes (Phase 4 scaffold)

This file summarizes how to use the new cost-translation layer safely.

## Scope

- Prices observed announcement-to-decision spans using anchors in `data/cost_anchors.csv`.
- Generates demo ranges in `data/cost_translation_demo.csv` for projects with both a verified decision date and known MW.
- Documents methodology in `data/cost_translation_methodology.md`.

## Use in analysis

- Treat all outputs as **internal, non-client-facing** until delay measurement has a matched-control estimate with adequate n.
- Interpret ranges as pricing the **span**, not opposition's marginal effect.
- Distinguish clearly between:
  - Destroyed value: construction escalation, sunk predevelopment capital.
  - Deferred revenue exposure: gross revenue that arrives later; the economic cost is the time value of deferral.

## Guardrails

- Do not impute MW for dollar outputs; if `capacity_mw` is missing, withhold cost figures.
- Do not label deferred revenue exposure as "loss"; keep the exposure framing.
- Re-verify anchors before any external publication, especially escalation and rent assumptions.

## Operational next steps

- Optionally add a CI step that runs `python3 cost_translation.py` after retrain to keep outputs fresh.
- Later, when a defensible matched delay estimate exists, add a client-facing cost view that:
  - Uses delay attributable to opposition, not raw spans.
  - Surfaces ranges, never point estimates.
  - Carries anchor sources and confidence flags through to the UI.
