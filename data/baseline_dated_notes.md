# Dated Baseline — Coverage and Definitions

Generated 2026-07-24 by `baseline_dated.py`.

## Frame

- Records with a usable time origin (day/month announced): **281** (281 internal, 0 external)
- Opposed: 161 (26 with a verified decision date)
- Control (unopposed): 120 (0 with a verified decision date; 45 decided but undated → censored lower bounds)

## End-anchor kinds

- `decision_verified` — verified, sourced decision date; observed event.
- `decided_undated` — outcome is terminal but no verified date exists; span to last status update is a LOWER BOUND, treated as censored. Common on the control side (unopposed advances rarely produce a datable vote — same structural asymmetry documented in the survival model).
- `censored_pending` / `censored_asof` — no terminal outcome yet.

## External ingest

- `data/baseline_dated_external.csv` not present (optional). Schema when adding external dated sources (ISO large-load queues, permit portals, commercial trackers):
  - required: source, as_of, name, state, announced_date
  - optional: county, capacity_mw, decision_date, status, source_url, operator
  - external decision dates count as verified only with a source_url; year-only announced dates are rejected.

## Binding limitations

- Control-side verified decision dates are currently scarce; most control spans are censored lower bounds. Time-to-decision comparisons must use survival methods (censoring-aware), never mean/median of raw spans across groups with different censoring rates.
- 'Unopposed' means no opposition recorded in the tracker — absence of evidence, not verified absence.
- This module constructs data only; inference belongs to the survival and comparison modules with their stated limitations.
