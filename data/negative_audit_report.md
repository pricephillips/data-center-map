# Verified-Negative Audit

Generated 2026-09-18. Frame design registered 2026-07-23 (module docstring): census of proposals_unopposed + ai_centers rows in the baseline universe; atlas rows excluded on detectability grounds (1479 non-opposed atlas rows excluded; this is a detectability decision, not a claim about those facilities). Worklist order is seeded-shuffle (seed 20260723) with blocked_confirmed rows first, so any top-down batch is a random subset of the remaining frame. The blocked_confirmed rows themselves are a purposive cell, not a random draw: coding mixes from batches containing them must not be extrapolated to the frame.

## Coverage

- Frame size: 171
- Coded: 4 (2%)
- Remaining: 167

## Coding mix (coded rows)

| coding | n | share of coded |
|---|---|---|
| verified_opposition | 3 | 75% |
| verified_none | 0 | 0% |
| undeterminable | 1 | 25% |

Interpretation rules: emergence-rate statements use verified_opposition / (verified_opposition + verified_none) and must always report the undeterminable count alongside, since undeterminable rows are not missing at random (they skew toward low-footprint projects). No emergence model trains until coverage of the frame is complete; partial-coverage rates are interim descriptives only.

## Coding validation problems

- line 3: universe_id prj_279 not in audit frame; row ignored
- line 4: universe_id prj_201 not in audit frame; row ignored
- line 6: universe_id prj_64 not in audit frame; row ignored
- line 7: universe_id prj_76 not in audit frame; row ignored
- line 8: universe_id prj_266 not in audit frame; row ignored
- line 10: universe_id prj_16 not in audit frame; row ignored

