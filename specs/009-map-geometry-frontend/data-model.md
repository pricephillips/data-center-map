# Data Model: County Geometry, Basemap, Tables, and UI Tests

## County geometry (`data/geo/counties_2024.topojson`)

TopoJSON Topology with one object, `counties` (GeometryCollection).

| Field | Type | Rule |
|---|---|---|
| geometry `id` | string, 5 digits | County FIPS, zero padded. Unique. |
| `properties.name` | string | Census NAME. |
| `transform` | quantization | Present (quantized arcs). |

Validation (enforced by `tests/ui/check_geometry.js`, run in the
`boundaries` job and by the UI tests):

- every `fips` in `data/county_policy_scores.csv` has a geometry;
- no duplicate ids;
- file size is at most 1,000,000 bytes.

## Geometry manifest (`data/geo/counties_2024_manifest.json`)

| Field | Type | Meaning |
|---|---|---|
| `file` | string | `data/geo/counties_2024.topojson` |
| `source_url` | string | Where the geometry came from. |
| `source_note` | string | How it was built (tool, parameters). |
| `vintage` | int | Census edition year (2023 for the seed, 2024 once the job runs). |
| `scale` | string | `1:10m` seed, `1:5m` job build. |
| `sha256` | string | Of the committed TopoJSON. |
| `bytes` | int | Size of the committed TopoJSON. |
| `feature_count` | int | Geometries in `counties`. |
| `scored_fips` / `scored_missing` | int / list | Coverage against the scores file at build time. |
| `built_at` | ISO date | Build date. |
| `writer` | string | `acquire-geo-sources.yml:boundaries` |

State transition: `seed` (vintage 2023, committed by session 6) to `built`
(vintage 2024, committed by the Actions job). The job fails without writing
if `scored_missing` is non-empty.

## Basemap provider (`basemap.js` `PROVIDERS`)

| Field | Type | Meaning |
|---|---|---|
| `kind` | `raster` or `vector` | Renderer. |
| `url` / `style` | string | Tile template or style JSON URL. |
| `attribution` | string | Always non-empty (FR-004). |
| `maxZoom` | int | |

`CHAIN = ['openfreemap', 'esri_dark', 'osm']`. The active provider is exposed
as `layer.provider` and on the map container as `data-basemap`.

## Map render summary (test-facing DOM attributes)

On the map container of choropleth pages:
`data-scored-counties` (scores loaded), `data-painted-counties` (scored FIPS
that received a polygon fill), `data-basemap` (active provider).

## Date range state (`opposition-map.html`)

| Key | Type | Meaning |
|---|---|---|
| `from`, `to` | int year | Inclusive. Absent means unbounded. Serialized in the permalink hash. |

A pin with no parsable date is shown only when the range is unbounded.
