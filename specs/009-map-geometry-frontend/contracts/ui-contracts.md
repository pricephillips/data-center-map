# UI Contracts

## `Basemap` (basemap.js)

```js
Basemap.dark(opts?)  // -> layer-like { addTo(map), on(ev, fn), off, remove, provider }
Basemap.CHAIN        // ['openfreemap', 'esri_dark', 'osm']
Basemap.PROVIDERS    // name -> { kind, url|style, attribution, maxZoom }
Basemap.spec(name)   // provider record
Basemap.nextProvider(name) // next in CHAIN or null
```

- `opts.provider` pins a provider and disables the vector attempt when it is a
  raster provider (index.html uses this).
- Emits `basemapfallback` on the map with `{ from, to, reason }` when it
  switches.
- Sets `data-basemap` on the map container.

## `LegendFilter.dateRange` (legend-filter.js)

```js
LegendFilter.yearOf(value)          // -> int year or null
LegendFilter.inYearRange(value, lo, hi) // lo/hi null = unbounded
```

## Permalink extras (opposition-map.html)

`#...&from=2024&to=2025` restores the slider. Unknown values are ignored.

## Geometry fetch

`[RAW + '/data/geo/counties_2024.topojson', './data/geo/counties_2024.topojson']`,
decoded with `topojson.feature(topo, topo.objects.counties)`. On failure the
page shows its existing `#error-banner`.

## UI test harness (tests/ui)

`npm test` in `tests/ui` runs Playwright against a local static server on the
repo root. Per page it asserts: no visible `#error-banner`, no console
errors, no request escaped to a third-party host, zero critical axe
violations; pin pages have at least one marker; choropleth pages have
`data-painted-counties >= 3144`.
