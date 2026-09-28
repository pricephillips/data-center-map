# Feature Specification: County Geometry, Basemap, Tables, and UI Tests

**Feature Branch**: `009-map-geometry-frontend`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 6 of the tool integration plan: Census 2024 cartographic boundaries, mapshaper, topojson-client, MapLibre GL JS with maplibre-gl-leaflet, OpenFreeMap, Tabulator, Playwright, axe-core, plus an in-house date slider and detail panel.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every scored county renders on the choropleth (Priority: P1)

A new `boundaries` job in `acquire-geo-sources.yml` downloads the Census 2024 1:5,000,000 county cartographic boundary file, simplifies it with mapshaper, and commits `data/geo/counties_2024.topojson` with a manifest (source URL, vintage, SHA-256, feature count). Map pages load it through the `raw.githubusercontent.com`-first chain and decode it with topojson-client. The plotly `geojson-counties-fips.json` fetch is removed.

**Why this priority**: Verified 2026-09-28: the plotly geometry has 3,221 features but lacks 13 of the 3,144 scored FIPS, namely all nine Connecticut planning regions (09110 to 09190), Alaska 02063, 02066 and 02158, and Oglala Lakota County SD (46102). Those counties receive scores and never paint. The file is also a 3.2 MB fetch from a third-party repo.

**Independent Test**: Load the new TopoJSON and confirm every FIPS in `county_policy_scores.csv` has a feature.

**Acceptance Scenarios**:

1. **Given** the new geometry, **When** `restriction-model.html` loads, **Then** the Connecticut planning regions paint with their scores.
2. **Given** the TopoJSON fetch fails, **When** a page loads, **Then** the existing visible error banner appears.
3. **Given** the manifest, **When** a FIPS in the scores is missing from the geometry, **Then** the `boundaries` job fails.

### User Story 2 - The basemap uses tiles licensed for commercial use (Priority: P1)

`basemap.js` gains a vector basemap from OpenFreeMap rendered through MapLibre GL JS and the maplibre-gl-leaflet binding, placed under the existing Leaflet layers. The current Esri and OSM raster options remain as fallbacks in the chain. Attribution for OpenFreeMap and OpenStreetMap is shown.

**Why this priority**: Esri World Gray Canvas terms and the OpenStreetMap tile usage policy both restrict heavy or commercial use. The maps are client-facing.

**Independent Test**: Load each map page with the OpenFreeMap style reachable and then blocked; confirm the vector basemap in the first case and the raster fallback in the second.

### User Story 3 - Dashboard tables sort, filter, and export (Priority: P2)

The tables on `developments-dashboard.html`, `positions-dashboard.html`, and `data-operations.html` render with Tabulator: column sort, header filters, and CSV export of the filtered view. The outcome ladder vocabulary and column names are unchanged.

**Independent Test**: Filter a table by state, export, and confirm the CSV has only the filtered rows with LF endings.

### User Story 4 - The two-mode map gets a date slider and a detail panel (Priority: P2)

`opposition-map.html` adds an in-house date range slider that filters pins through `legend-filter.js`, with state serialized by `map-permalink.js`, and an in-house side panel that shows county or project detail on click in place of popups. No stale plugins are added (Leaflet.TimeDimension and leaflet-sidebar-v2 were rejected for maintenance risk).

**Independent Test**: Set the slider to 2025, reload with the permalink, and confirm the same pins and range are restored.

### User Story 5 - UI changes are tested in CI (Priority: P1)

A new `ui-check.yml` runs on changes to `*.html` and `*.js`. Playwright loads each map and dashboard page from a local static server with the fetch chain pointed at repo files, then checks: no error banner, at least one marker on pin maps, at least 3,144 county paths on choropleth pages, no console errors, and no critical axe-core violations.

**Why this priority**: Neither `pipeline.yml` nor `gate-check.yml` triggers on HTML or JS, so UI pushes are CI-inert today.

**Independent Test**: Break a fetch URL in a page copy and confirm the job fails on the error-banner check.

### Edge Cases

- The Census host is unreachable from the sandbox: the job runs in Actions only; the committed TopoJSON is what pages use.
- MapLibre fails on a browser without WebGL: the raster fallback is used.
- Notion or Simple.ink embeds: `raw.githubusercontent.com` stays first in every fetch chain, including the new TopoJSON.
- Alaska and Hawaii insets: projection handling stays as it is today; this spec changes geometry source, not projection.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `data/geo/counties_2024.topojson` and its manifest MUST be declared in `configs/layers.json` with the `boundaries` job as the single writer.
- **FR-002**: Front-end libraries MUST be loaded from pinned CDN versions per `configs/integrations.json`; `node --check` MUST pass on all touched JS.
- **FR-003**: The plotly geometry URL MUST be removed from every page once the new file is live.
- **FR-004**: `basemap.js` MUST keep a fallback chain and show attribution for every tile source.
- **FR-005**: Tabulator adoption MUST NOT rename columns or alter vocabulary; `leak_audit.py` MUST stay at 0.
- **FR-006**: `ui-check.yml` MUST run without network access to third-party data hosts (fixtures served locally).

## Success Criteria *(mandatory)*

- **SC-001**: 3,144 of 3,144 scored counties render on `restriction-model.html` and `opposition-map.html`.
- **SC-002**: Initial county geometry payload drops below 1 MB (from 3.2 MB).
- **SC-003**: `ui-check.yml` passes on `main` and fails on a deliberately broken fetch.
- **SC-004**: Zero critical axe-core violations on the tested pages.

## Assumptions

- OpenFreeMap remains free with no key; if it changes terms, the raster fallback covers until a replacement is chosen.
- The 1:5m generalization is detailed enough at national and state zoom; county-profile pages may later use 1:500k.
