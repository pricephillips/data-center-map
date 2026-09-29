# Research: County Geometry, Basemap, Tables, and UI Tests

**Feature**: `009-map-geometry-frontend` | **Date**: 2026-09-29

Measurements below were taken in the session 6 sandbox on 2026-09-29.

## R1. Where the county geometry comes from today

- `restriction-model.html` and `opposition-map.html` fetch plotly's
  `geojson-counties-fips.json` (3.2 MB, 3,221 features, pre-2015 vintage) from
  jsdelivr, then from `raw.githubusercontent.com/plotly/...`.
- Both pages carry a crosswalk for the old vintage: `BOUNDARY_RENAMES`
  (02270 to 02158, 46113 to 46102) and `BOUNDARY_RETIRED` (8 old CT counties,
  02261, 51515). `restriction-model.html` also lists the undrawable scored
  FIPS below the map.
- `data/county_policy_scores.csv` has 3,144 unique FIPS. Against the plotly
  file, 13 of them have no polygon (nine CT planning regions 09110 to 09190,
  02063, 02066) and two paint only through the rename crosswalk (02158,
  46102).
- `master_datacenter_map.html` and `opposition-tracker.html` draw state
  outlines from a different third-party file (PublicaMundi `us-states.json`).
  That is state geometry and is out of scope for this spec.

## R2. Census host reachability, and the seed file

**Decision**: Commit a seed `data/geo/counties_2024.topojson` built from the
Census 2023 cartographic boundary (1:10m, redistributed by the ISC-licensed
npm package `@severo_bo/us-atlas-2023@4.0.0`), reduced to the `counties`
object. The `boundaries` job in `acquire-geo-sources.yml` replaces it with the
Census 2024 1:5m build on its first Actions run. The manifest records the true
vintage, scale and source of whatever is committed, so the seed is never
presented as 2024 data.

**Rationale**:

- `www2.census.gov`, `unpkg.com`, `cdn.jsdelivr.net` and
  `tiles.openfreemap.org` all return a proxy 403 from the sandbox;
  `registry.npmjs.org` and `pypi.org` are reachable.
- The spec's edge case already says the Census download runs in Actions only.
- The 2023 edition has every one of the 3,144 scored FIPS (verified: 0
  missing; the 89 extra features are PR and the island areas). County
  geography did not change between the 2023 and 2024 editions for any scored
  FIPS; the CT, Valdez-Cordova and Oglala Lakota changes all predate 2023.
- Stable filename: pages point at `counties_2024.topojson` now and the Actions
  build overwrites it in place with no page edit.

**Alternatives considered**:

- `us-atlas@3` (2017 edition): lacks the CT planning regions. Rejected.
- Fetching Census from a third-party GitHub mirror: out of this session's
  repository scope, and no better provenance than the npm package.
- Waiting for the Actions job before touching pages: blocks Story 1 on a
  workflow change that may not be pushable from the sandbox (R8).

## R3. Payload size (SC-002)

**Decision**: Drop `states` and `nation` objects and territories other than
PR, keep quantization. Seed measures about 0.8 MB, under the 1 MB target.
The Actions build uses `mapshaper -simplify 12% keep-shapes -o
quantization=1e5 format=topojson`, and the job fails if the output exceeds
1,000,000 bytes.

## R4. Decoding in the browser

**Decision**: `topojson-client@3.1.0` from a pinned CDN URL
(`https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js`,
with unpkg as the second source). `topojson.feature(topo, topo.objects.counties)`
yields a FeatureCollection whose `id` is the 5-digit FIPS, the same contract
the plotly file had, so the Leaflet layer code is unchanged.

The geometry fetch chain becomes `RAW + '/data/geo/counties_2024.topojson'`
then `./data/geo/counties_2024.topojson`, same as every other data file
(Constitution: raw first).

**Crosswalk**: The retired-boundary and rename tables are kept but become
inert once every scored FIPS has its own polygon. The undrawable list on
`restriction-model.html` empties itself (it is computed, not hard-coded), and
stays as a guard should a future scores file carry a FIPS the geometry lacks.

## R5. Vector basemap

**Decision**: `basemap.js` gains an `openfreemap` provider rendered with
`maplibre-gl@6.11.2` through `@maplibre/maplibre-gl-leaflet@0.1.4`
(`L.maplibreGL({ style })`). Style: OpenFreeMap `dark`
(`https://tiles.openfreemap.org/styles/dark`), closest to the existing palette.

The chain is `openfreemap` then `esri_dark` then `osm`. Fallback triggers,
because a vector style fails differently from a raster tile:

1. MapLibre or the binding not loaded, or `maplibregl.supported()` false /
   WebGL context creation throws: fall back synchronously.
2. The style fetch errors (MapLibre `error` event before `load`), or no
   `load` within a timeout (8 s): swap to the next raster provider.
3. Raster `tileerror` on the first few tiles: swap to the next raster
   provider (the existing `opposition-tracker.html` does this by hand).

`Basemap.dark()` keeps its signature and returns a Leaflet layer-like object
with `addTo`, `on`, `remove`, so every page call site stays valid. The
MapLibre and binding scripts are loaded lazily by `basemap.js` itself, so
pages need no new `<script>` tags. Attribution: OpenFreeMap,
OpenMapTiles and OpenStreetMap contributors on the vector layer; each raster
provider keeps its own.

**Alternatives**: protomaps (needs self-hosted PMTiles); MapTiler (key).

## R6. Tables

**Decision**: `tabulator-tables@6.5.3` from jsdelivr (pinned) with unpkg
fallback, loaded only on the three dashboard pages. The existing HTML table
builders stay as the fallback when Tabulator does not load. Column titles and
cell text are taken from the current table code verbatim. CSV export uses
Tabulator's `download('csv', ..., { })` on the `active` row range; Tabulator
joins rows with `\n`, which satisfies the LF requirement.

## R7. UI tests

**Decision**: `tests/ui/` holds a small npm project (`package.json` pinning
`@playwright/test@1.63.0`, `@axe-core/playwright@4.13.0`, and the browser
libraries the pages load). Tests run Chromium from `/opt/pw-browsers` locally
(`PLAYWRIGHT_BROWSERS_PATH`), never `playwright install`.

FR-006 is met by request routing, not by editing pages: every request is
intercepted. `raw.githubusercontent.com/pricephillips/data-center-map/main/*`
is served from the working tree; pinned CDN library URLs are served from
`tests/ui/node_modules`; basemap hosts are aborted (which also exercises the
fallback path); any other third-party request is aborted and counted.

Choropleth pages render on canvas (`preferCanvas`), so there are no SVG
`<path>` elements to count. Pages publish a small, test-facing summary on the
map container: `data-scored-counties` and `data-painted-counties`. The test
asserts `painted >= 3144` and `painted == scored`.

axe: `critical` impact violations fail; `serious` are reported.

## R8. Workflow changes

The session token may lack the `workflows` permission. The `boundaries` job
and `ui-check.yml` are written as real files, a push is attempted once, and
if rejected they move to `docs/pending_map_geometry.patch` with
`docs/pending_map_geometry.md`, per the constitution.

## R9. Layer declaration (FR-001)

`configs/layers.json` and `ARCHITECTURE.md` are outside the file set this
session may edit (spec 005 runs in parallel). `layer_audit.py` inventories
only top-level `data/*` files and Python writers, so `data/geo/*` raises no
finding. The declaration (Layer D reference geometry, writer: the
`boundaries` job) ships in the same pending patch so it lands with the job.

## R10. Date slider and side panel

**Decision**: In-house. `legend-filter.js` gains a pure `dateRange` helper
(`inRange(dateStr, lo, hi)`, year bounds) with a node selftest;
`opposition-map.html` adds two `<input type="range">` year thumbs whose state
serializes via `map-permalink.js` extras (`from`, `to`). The side panel is an
`<aside>` that opens on county or pin click, closable by button and Escape,
with focus moved into it. Rejected: Leaflet.TimeDimension (last release
2019), leaflet-sidebar-v2 (2020).
