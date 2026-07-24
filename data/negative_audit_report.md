# Verified-Negative Audit

Generated 2026-07-24. Frame design registered 2026-07-23 (module docstring): census of proposals_unopposed + ai_centers rows in the baseline universe; atlas rows excluded on detectability grounds (1479 non-opposed atlas rows excluded; this is a detectability decision, not a claim about those facilities). Worklist order is seeded-shuffle (seed 20260723) with blocked_confirmed rows first, so any top-down batch is a random subset of the remaining frame. The blocked_confirmed rows themselves are a purposive cell, not a random draw: coding mixes from batches containing them must not be extrapolated to the frame.

## Coverage

- Frame size: 175
- Coded: 9 (5%)
- Remaining: 166

## Coding mix (coded rows)

| coding | n | share of coded |
|---|---|---|
| verified_opposition | 6 | 67% |
| verified_none | 1 | 11% |
| undeterminable | 2 | 22% |

Interpretation rules: emergence-rate statements use verified_opposition / (verified_opposition + verified_none) and must always report the undeterminable count alongside, since undeterminable rows are not missing at random (they skew toward low-footprint projects). No emergence model trains until coverage of the frame is complete; partial-coverage rates are interim descriptives only.

## Coding validation problems

- line 7: universe_id prj_76 not in audit frame; row ignored

