// US2: OpenFreeMap vector basemap with a raster fallback chain and attribution.
'use strict';
const { test, expect } = require('./fixtures');

// A self-contained style: one background layer, no sources, so the vector
// path is exercised end to end with no network.
const STYLE = JSON.stringify({
  version: 8, name: 'test', sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#10151d' } }]
});

test('vector basemap renders when the OpenFreeMap style is reachable', async ({ page }) => {
  await page.route('https://tiles.openfreemap.org/**', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: STYLE }));
  await page.goto('/opposition-map.html');
  const map = page.locator('#map');
  await expect(map).toHaveAttribute('data-basemap', 'openfreemap');
  await expect(page.locator('#map .maplibregl-canvas, #map canvas.maplibregl-canvas')).toHaveCount(1, { timeout: 20000 });
  await page.waitForTimeout(500);
  await expect(map).toHaveAttribute('data-basemap', 'openfreemap');
  await expect(page.locator('.leaflet-control-attribution')).toContainText('OpenFreeMap');
  await expect(page.locator('.leaflet-control-attribution')).toContainText('OpenStreetMap');
});

test('blocked style host falls back to a raster provider with attribution', async ({ page }) => {
  // The fixture aborts every basemap host, so the chain runs to its end.
  await page.goto('/opposition-map.html');
  const map = page.locator('#map');
  await expect(map).toHaveAttribute('data-basemap', /^(esri_dark|osm)$/, { timeout: 20000 });
  await expect(page.locator('.leaflet-control-attribution')).toContainText(/Esri|OpenStreetMap/);
  await expect(page.locator('#error-banner')).toBeHidden();
});
