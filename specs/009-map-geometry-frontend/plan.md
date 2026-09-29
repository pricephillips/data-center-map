# Implementation Plan: County Geometry, Basemap, Tables, and UI Tests

**Branch**: `009-map-geometry-frontend` (worked on `claude/admiring-fermat-xso491`) | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-map-geometry-frontend/spec.md`

## Summary

The two choropleth pages stop fetching plotly's 3.2 MB pre-2015 county
GeoJSON and load a committed `data/geo/counties_2024.topojson` through the
raw-first chain, decoded with topojson-client. The seed file is the Census
2023 cartographic boundary (all 3,144 scored FIPS present, about 0.8 MB); the
new `boundaries` job in `acquire-geo-sources.yml` rebuilds it from the Census
2024 1:5m file with mapshaper and fails if any scored FIPS is missing
(research R2, R3).

`basemap.js` gains an OpenFreeMap vector provider through MapLibre GL JS and
maplibre-gl-leaflet, loaded lazily, with a real fallback chain
(`openfreemap` then `esri_dark` then `osm`) and attribution for each (R5).

Then: Tabulator on the three dashboards (R6); a Playwright plus axe-core
harness in `tests/ui/` that runs fully offline by routing requests to the
working tree and `node_modules` (R7); an in-house year-range slider and
detail side panel on `opposition-map.html` (R10).

## Technical Context

**Language/Version**: Browser JavaScript (ES2019, no build step); Node 20+ for
selftests and Playwright; Python 3.11 for the existing gates only.

**Primary Dependencies** (pinned, per `configs/integrations.json`):
topojson-client 3.1.0, maplibre-gl 6.11.2, @maplibre/maplibre-gl-leaflet 0.1.4,
tabulator-tables 6.5.3, @playwright/test 1.63.0, @axe-core/playwright 4.13.0,
mapshaper 0.7.69 (CI build only). Existing: Leaflet 1.9.4,
leaflet.markercluster 1.5.3, PapaParse 5.4.1.

**Storage**: Files. New: `data/geo/counties_2024.topojson`,
`data/geo/counties_2024_manifest.json`. No change to any CSV.

**Testing**: `node *_selftest.js` (basemap, legend filter, permalink);
`tests/ui` Playwright specs with Chromium from `/opt/pw-browsers`;
`tests/ui/check_geometry.js` coverage check.

**Target Platform**: Static pages on GitHub Pages and raw.githubusercontent,
embedded in Notion and Simple.ink iframes.

**Project Type**: Static web front end over a script-based data pipeline.

**Performance Goals**: County geometry payload under 1 MB (SC-002).
Basemap fallback decision within 8 s of a dead style host.

**Constraints**:

- Raw-first fetch chain on every data file, including the TopoJSON.
- Edit only the HTML pages, `basemap.js`, `legend-filter.js`,
  `map-permalink.js`, `data/geo/`, `tests/ui/`, `acquire-geo-sources.yml`
  (plus `docs/pending_*` and this spec directory). Spec 005 runs in parallel
  and owns `pipeline.yml`, `requirements/`, `qc/`.
- No new workflow can be assumed pushable (R8).
- Census, OpenFreeMap and the CDNs are unreachable from the sandbox.

**Scale/Scope**: 13 HTML pages (2 choropleth, 5 pin maps, 3 dashboards
changed); 3,144 scored counties.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| 1. `leak_audit.py --tier blocking` = 0 | PASS (verify at end) | No new vocabulary; Tabulator reuses existing column titles and outcome ladder labels (FR-005). |
| 2. `layer_audit.py` = 0 undeclared | PASS, with declaration staged | `data/geo/*` is outside the audit inventory and has no Python writer. The Layer D declaration for FR-001 is in `docs/pending_map_geometry.patch` because `configs/layers.json` and ARCHITECTURE.md are outside this session's file set (R9). |
| 3. `--selftest` on touched modules | REQUIRES ACTION | No Python module touched. JS modules: extend `basemap_selftest.js` and `legend_filter_selftest.js`; add `tests/ui/check_geometry.js --selftest`. |
| 4. `node --check` on touched JS | REQUIRES ACTION | `scripts/check_inline_js.py` and the pre-commit `node-check` hook. |
| 5. No em-dashes, no CRLF | PASS (verify at end) | Tabulator CSV export uses LF. |
| 6. docx validation | N/A | |

Principles: I (defensibility) is why the manifest records the seed's true
vintage (2023) rather than the filename's; VII (additive) keeps `Basemap.dark()`
and the page-level crosswalk intact; VIII (layer ownership) gives the TopoJSON
exactly one writer, the `boundaries` job, after the seed commit.

**Post-design re-check**: no violations. The staged layer declaration is the
one item Price applies by hand.

## Project Structure

### Documentation (this feature)

```text
specs/009-map-geometry-frontend/
├── plan.md
├── research.md          # R1-R10
├── data-model.md        # geometry, manifest, providers, DOM summary, date state
├── quickstart.md
├── contracts/ui-contracts.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
data/geo/counties_2024.topojson           # NEW seed (Census 2023 cb); job rebuilds from 2024 1:5m
data/geo/counties_2024_manifest.json      # NEW
data/geo/README.md                        # NEW provenance and rebuild command
restriction-model.html                    # EDIT geometry source, topojson-client, painted-count attrs
opposition-map.html                       # EDIT same + date slider + side panel
basemap.js                                # EDIT vector provider, chain, fallback events
basemap_selftest.js                       # EDIT chain and attribution cases
legend-filter.js / legend_filter_selftest.js  # EDIT year-range helper
map-permalink.js                          # (only if extras need a helper)
developments-dashboard.html, positions-dashboard.html, data-operations.html  # EDIT Tabulator
tests/ui/package.json, playwright.config.js, *.spec.js, serve.js, check_geometry.js  # NEW
.github/workflows/acquire-geo-sources.yml # EDIT boundaries job
.github/workflows/ui-check.yml            # NEW
docs/pending_map_geometry.patch / .md     # NEW if workflows cannot be pushed; always carries layers.json + ARCHITECTURE.md
```

**Structure Decision**: No build step and no bundler, matching the existing
pages. Test tooling is isolated in `tests/ui/` with its own `package.json` so
the repo root gains no Node dependency.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Seed geometry from 2023 edition in a file named `_2024` | Census host unreachable from the sandbox; Story 1 is P1 | Waiting on the Actions run leaves 13 counties unpainted; the manifest states the true vintage |
| `tests/ui/package.json` (Node deps in repo) | Playwright and axe need npm packages | Global installs are not reproducible in CI |
