# Dated Baseline — Coverage and Definitions

Generated 2026-08-24 by `baseline_dated.py`.

## Frame

- Records with a usable time origin (day/month announced): **803** (286 internal, 517 external)
- Opposed: 169 (29 with a verified decision date)
- Control (unopposed): 634 (0 with a verified decision date; 47 decided but undated → censored lower bounds)

## End-anchor kinds

- `decision_verified` — verified, sourced decision date; observed event.
- `decided_undated` — outcome is terminal but no verified date exists; span to last status update is a LOWER BOUND, treated as censored. Common on the control side (unopposed advances rarely produce a datable vote — same structural asymmetry documented in the survival model).
- `censored_pending` / `censored_asof` — no terminal outcome yet.

## External ingest

- `baseline_dated_external.csv` present: 517 accepted, 0 rejected.

## Binding limitations

- Control-side verified decision dates are currently scarce; most control spans are censored lower bounds. Time-to-decision comparisons must use survival methods (censoring-aware), never mean/median of raw spans across groups with different censoring rates.
- 'Unopposed' means no opposition recorded in the tracker — absence of evidence, not verified absence.
- This module constructs data only; inference belongs to the survival and comparison modules with their stated limitations.
