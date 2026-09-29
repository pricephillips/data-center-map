# Tasks: County Geometry, Basemap, Tables, and UI Tests

**Input**: Design documents from `specs/009-map-geometry-frontend/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ui-contracts.md, quickstart.md

**Tests**: Requested by the spec (User Story 5 is the test harness), so each
story adds its checks to `tests/ui/` or to a node selftest.

**Order**: Session priority is US1, US2, then US5, US3, US4.

## Phase 1: Setup

- [ ] T001 Create `tests/ui/package.json` pinning `@playwright/test@1.63.0`, `@axe-core/playwright@4.13.0`, `leaflet@1.9.4`, `leaflet.markercluster@1.5.3`, `papaparse@5.4.1`, `chart.js@4.4.0`, `topojson-client@3.1.0`, `maplibre-gl@6.11.2`, `@maplibre/maplibre-gl-leaflet@0.1.4`, `tabulator-tables@6.5.3`, `mapshaper@0.7.69`; commit `package-lock.json`; add `tests/ui/.gitignore` for `node_modules/`, `test-results/`, `playwright-report/`
- [ ] T002 [P] Create `data/geo/README.md` stating provenance, the seed/built states, and the rebuild command

## Phase 2: Foundational

- [ ] T003 Write `tests/ui/check_geometry.js` (with `--selftest`): loads `data/geo/counties_2024.topojson`, fails unless "every `fips` in `data/county_policy_scores.csv` has a geometry", "no duplicate ids", and "file size is at most 1,000,000 bytes"; `--manifest` writes `data/geo/counties_2024_manifest.json` with the fields in data-model.md
- [ ] T004 Build the seed `data/geo/counties_2024.topojson` from `@severo_bo/us-atlas-2023@4.0.0` `counties-10m.json`: keep only the `counties` object, drop territories other than PR (state FIPS 60, 66, 69, 78), keep quantization; write the manifest with `vintage: 2023`, `scale: "1:10m"`, `sha256`, `bytes`, `feature_count`, `scored_missing: []`

## Phase 3: User Story 1 - Every scored county renders (P1) MVP

**Goal**: 3,144 of 3,144 scored counties paint on `restriction-model.html` and `opposition-map.html`.
**Independent test**: `node tests/ui/check_geometry.js`, then `data-painted-counties="3144"` on both pages.

- [ ] T005 [US1] In `restriction-model.html`: load `topojson-client@3.1.0` (jsdelivr then unpkg), replace `COUNTY_GEO_URLS` with `[RAW + '/data/geo/counties_2024.topojson', './data/geo/counties_2024.topojson']`, decode with `topojson.feature(topo, topo.objects.counties)`, update the vintage comments, keep the computed undrawable card, show `#error-banner` when geometry fails, set `data-scored-counties` / `data-painted-counties` on `#scoreMap`
- [ ] T006 [US1] Same change in `opposition-map.html` (geometry chain, decode, banner, count attributes on `#map`)
- [ ] T007 [US1] Remove every `plotly/datasets` geometry URL from all HTML pages (FR-003); `grep -r geojson-counties-fips *.html` returns nothing
- [ ] T008 [US1] Add the `boundaries` job to `.github/workflows/acquire-geo-sources.yml`: download `cb_2024_us_county_5m.zip`, `npx mapshaper@0.7.69 ... -simplify 12% keep-shapes -filter '...' -o format=topojson quantization=100000`, run `node tests/ui/check_geometry.js --manifest`, commit both files with the rebase-and-retry loop; `job` input gains `boundaries`

## Phase 4: User Story 2 - Licensed vector basemap (P1)

**Goal**: OpenFreeMap vector basemap under the Leaflet layers, raster fallback chain, attribution for every source.
**Independent test**: `node basemap_selftest.js`; UI test with the style host blocked shows a raster `data-basemap`.

- [ ] T009 [US2] In `basemap.js`: add `openfreemap` (`kind: 'vector'`, style `https://tiles.openfreemap.org/styles/dark`, attribution OpenFreeMap, OpenMapTiles, OpenStreetMap contributors), `CHAIN = ['openfreemap','esri_dark','osm']`, `nextProvider()`, lazy loading of maplibre-gl and maplibre-gl-leaflet (pinned, jsdelivr then unpkg), a layer wrapper with `addTo/on/off/remove/provider`, fallback on missing library, no WebGL, style error, 8 s load timeout, or raster `tileerror`; fires `basemapfallback`; sets `data-basemap` on the map container
- [ ] T010 [US2] Extend `basemap_selftest.js`: chain order, every provider has non-empty attribution, `nextProvider` ends at null, a pinned raster provider skips the vector attempt, fallback with a fake `L` when `maplibregl` is absent
- [ ] T011 [US2] Update page stubs and call sites that assume a raw tile layer (`opposition-tracker.html` hand-rolled OSM fallback, `index.html` provider pin) so they use the chain

## Phase 5: User Story 5 - UI changes tested in CI (P1)

**Goal**: Offline Playwright plus axe smoke tests over map and dashboard pages.
**Independent test**: break a fetch URL in a page copy and the banner check fails.

- [ ] T012 [US5] `tests/ui/playwright.config.js` (Chromium, `PLAYWRIGHT_BROWSERS_PATH` honored, `webServer` = `tests/ui/serve.js` on the repo root) and `tests/ui/serve.js` (static server, no deps)
- [ ] T013 [US5] `tests/ui/fixtures.js`: route raw.githubusercontent repo URLs to the working tree, pinned CDN URLs to `node_modules`, abort basemap hosts and all other third-party hosts while recording them
- [ ] T014 [US5] `tests/ui/pages.spec.js`: per page no visible `#error-banner`, no console errors, zero critical axe violations; pin pages at least one marker; choropleth pages `data-painted-counties >= 3144` and equal to `data-scored-counties`
- [ ] T015 [US5] `tests/ui/broken-fetch.spec.js`: serve a page copy with the geometry URL broken and assert the banner shows (SC-003 negative case)
- [ ] T016 [US5] `.github/workflows/ui-check.yml` on `*.html`, `*.js`, `data/geo/**`, `tests/ui/**`: node 20, `npm ci` in `tests/ui`, `npx playwright install --with-deps chromium` (CI only), `npx playwright test`

## Phase 6: User Story 3 - Dashboard tables (P2)

**Goal**: Tabulator with sort, header filters, CSV export of the filtered view, unchanged columns and vocabulary.
**Independent test**: filter by state, export, CSV has only filtered rows, LF endings.

- [ ] T017 [P] [US3] `developments-dashboard.html`: Tabulator on the existing table, same column titles, header filters, "Export CSV" of active rows; plain table if Tabulator fails to load
- [ ] T018 [P] [US3] `positions-dashboard.html`: same
- [ ] T019 [P] [US3] `data-operations.html`: same
- [ ] T020 [US3] `tests/ui/tables.spec.js`: filter, export, assert rows and no `\r`

## Phase 7: User Story 4 - Date slider and detail panel (P2)

**Goal**: year-range slider filtering pins through `legend-filter.js`, persisted by the permalink; side panel for county and project detail.
**Independent test**: set from=2025, reload the permalink, same range and pins.

- [ ] T021 [US4] `legend-filter.js`: `yearOf(value)` and `inYearRange(value, lo, hi)` ("A pin with no parsable date is shown only when the range is unbounded"); cases in `legend_filter_selftest.js`
- [ ] T022 [US4] `opposition-map.html`: two range inputs with labels, filter pins, `from`/`to` in permalink extras
- [ ] T023 [US4] `opposition-map.html`: `<aside>` detail panel replacing popups on click; close button and Escape; focus moves into the panel
- [ ] T024 [US4] `tests/ui/slider.spec.js`: permalink round trip

## Phase 8: Polish

- [ ] T025 `docs/pending_map_geometry.patch` and `.md`: any workflow change the push rejects, plus the FR-001 `configs/layers.json` Layer D declaration and ARCHITECTURE.md line
- [ ] T026 Run `pre-commit run --all-files`, `python -m pytest tests/test_selftests.py`, `python scripts/check_inline_js.py`, `node *_selftest.js`, `npx playwright test`; merge `origin/main`; push

## Dependencies

- T001 before T003, T004, T012.
- T003, T004 before US1. US1 before T014's choropleth assertion.
- US2 independent of US1. US5 depends on T001 and uses US1/US2 hooks.
- US3 and US4 independent of each other; each adds a spec to US5.

## Parallel examples

- T002 with T003.
- T017, T018, T019 (different files).
- T009 with T005/T006 (different files).

## Implementation strategy

MVP is US1 (T001 to T008). Ship US2 next, then the harness so later stories
land already tested.
