# Research: Fix capacity_mw Regression

## No NEEDS CLARIFICATION markers were present in the spec.

All decisions below were resolved from direct code inspection.

---

## Decision 1: SOURCE_KEYS aliasing is already complete and correct

**Decision**: No change needed to `SOURCE_KEYS` or `flatten()`.

**Rationale**: `SOURCE_KEYS['capacity_mw'] = ('capacityMw', 'capacity_mw')` is present in the scraper. `flatten()` calls `pick(record, *SOURCE_KEYS['capacity_mw'])`, which tries `capacityMw` first then falls back to the legacy `capacity_mw`. The existing selftest already verifies this at line 875 (`check("capacity under the new name is read", f["capacity_mw"] == 250)`). Current `data/proposals.csv` shows 163 capacity-populated rows — the fix was applied after the incident.

**What remains**: The selftest has no assertion that exercises the 20% coverage threshold. The runtime guard (`assert_field_population`) uses the global `FIELD_LOSS_RATIO = 0.5` (50%) for all fields, which means a 30%-loss event for capacity_mw would not be caught at runtime.

---

## Decision 2: Add CAPACITY_LOSS_RATIO = 0.2 as a named constant; add a secondary per-field call in assert_field_population()

**Decision**: Introduce `CAPACITY_LOSS_RATIO = 0.2` alongside `FIELD_LOSS_RATIO = 0.5`. In `assert_field_population()`, after the existing 50%-threshold check for all watched fields, add a second call to `population_violations()` covering only `capacity_mw` with `ratio=CAPACITY_LOSS_RATIO`. Both checks can raise; the capacity check's error message identifies it specifically.

**Rationale**: The original incident was 123 → 3 (97% drop), well past both thresholds. The risk is a subtler future drop — e.g., 120 → 90 (25% loss) — that the 50% guard misses and that still empties most of `cost_translation_demo.csv`. A per-field check is the minimum-footprint approach: it reuses `population_violations()` with a custom ratio, touches only `assert_field_population()`, and does not change the global threshold for other fields.

**Alternatives considered**:
- Lower `FIELD_LOSS_RATIO` globally to 0.2 — rejected: too sensitive for sparse fields (e.g., `moratoriumExempt` has low coverage and ordinary scrape variation would trigger false positives).
- A separate `capacity_check()` function — rejected: over-engineering; `population_violations()` already accepts `ratio` as a parameter.
- Selftest-only (no runtime change) — rejected: the spec requires the guard to fire at 20% in production, not just in test assertions.

---

## Decision 3: Add selftest assertions for the 20% threshold

**Decision**: Add two named checks to `selftest()`:
1. `"capacity coverage drop of 20% is flagged"` — uses `population_violations()` with `ratio=0.2` and a fixture where capacity falls by exactly 21%.
2. `"capacity coverage drop of 19% is not flagged"` — same fixture family, drop is 19%.

**Rationale**: Pins the exact boundary. The existing selftest checks general population logic at the 50% ratio; the new checks are specifically for the 20% constant. They are adjacent to the existing population_violations checks in `selftest()`, not a separate section.

---

## Decision 4: Add --selftest step to the CI workflow before the scrape run

**Decision**: Add one step to `.github/workflows/scrape-trackdatacenters-proposals.yml` before the "Run scraper" step:

```yaml
- name: Selftest
  run: python scripts/scrape-trackdatacenters-proposals.py --selftest
```

**Rationale**: Every other scraper workflow (`fetch-permits.yml`, `acquire-geo-sources.yml`, `bill-sync.yml`, etc.) calls `--selftest` before the main run. The TrackDatacenters workflow is the only one that does not. This gap means selftest regressions in the scraper go undetected until a developer happens to run `--selftest` locally. Wiring it into CI closes the gap. Constitution Principle IX: "A new module is wired into the blocking selftest step of `pipeline.yml` before its outputs are relied on."

**Alternatives considered**: Adding a separate `selftest.yml` workflow — rejected: every other module uses an in-workflow step; consistency favors that pattern.

---

## Summary

Three code changes, one workflow change:

| Change | File | Lines (approx) |
|--------|------|---------------|
| Add `CAPACITY_LOSS_RATIO = 0.2` constant | `scripts/scrape-trackdatacenters-proposals.py` | 1 |
| Add secondary capacity check in `assert_field_population()` | same | ~10 |
| Add 2 selftest assertions for 20% threshold | same | ~10 |
| Add `--selftest` step before scrape run | `.github/workflows/scrape-trackdatacenters-proposals.yml` | 3 |
