# Quickstart: validating 009

Prerequisites: Node 20+, Python 3.11+, Chromium at `/opt/pw-browsers`
(never `playwright install`).

## Story 1: every scored county paints

```bash
node tests/ui/check_geometry.js          # 3144/3144 scored FIPS present, <1 MB
cd tests/ui && npm ci && npx playwright test choropleth
```

Expected: `restriction-model.html` and `opposition-map.html` report
`data-painted-counties="3144"`; the "not drawn" card on the restriction model
page stays hidden. Break the geometry URL in a page copy: the error banner
shows.

## Story 2: vector basemap with fallback

```bash
node basemap_selftest.js
cd tests/ui && npx playwright test basemap
```

The test blocks `tiles.openfreemap.org` and asserts
`data-basemap` is a raster provider with non-empty attribution. In a normal
browser, the map shows the OpenFreeMap dark style and "OpenFreeMap" in the
attribution control.

## Stories 3 to 5

```bash
cd tests/ui && npx playwright test       # all pages: banner, console, axe, markers
node legend_filter_selftest.js           # year range helper
```

Tabulator: filter the State column on a dashboard, click "Export CSV", check
the file has only filtered rows and no `\r`.

Slider: set from=2025, copy the link, reload: same range and pin count.

## Repo gates before push

```bash
pre-commit run --all-files
python -m pytest tests/test_selftests.py
python scripts/check_inline_js.py
for f in *_selftest.js; do node "$f"; done
```
