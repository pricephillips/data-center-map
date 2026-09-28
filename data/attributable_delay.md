# Opposition-Attributable Delay (gated)

Generated 2026-09-28 by `attributable_delay.py`. **Internal — NOT client-facing.** This module emits an estimate only when its gates pass; a WITHHELD state is normal and correct until then.

## Gate status

- PASS: control-side verified decision events — 18 / 10 required
- SHORT: opposed verified events within matched sets — 8 / 15 required
- PASS: matched sets usable on both arms — 177 / 25 required

## Verdict: **WITHHELD**

The binding constraint is **opposed verified events within matched sets**. Control-side events come from the permit ingest (`permit_ingest.py` -> `data/baseline_dated_external.csv` with source URLs); each terminal permit decision added converts an interval-censored bound into an observed event and moves this gate. No estimate, preliminary or otherwise, is derivable from the current inputs without violating the platform's defensibility rules.

## Inputs and definitions

- Frame: `data/baseline_dated.csv` (announced-date origins; verified decision dates as events; status-update/as-of censoring).
- Matching: `data/matched_controls.csv` (state / capacity / 2024 margin).
- RMST (restricted mean survival time) is used because it is defined regardless of whether curves cross 0.5 and differences read directly as days.
