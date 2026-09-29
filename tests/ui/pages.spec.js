// US5: smoke and accessibility checks over every map and dashboard page.
//   - no visible #error-banner
//   - no console errors (network noise from aborted third-party hosts aside)
//   - zero critical axe-core violations (serious ones are reported)
//   - pin maps: at least one marker
//   - choropleth pages: at least 3,144 painted counties (choropleth.spec.js
//     asserts the exact equality)
'use strict';
const { test, expect } = require('./fixtures');
const AxeBuilder = require('@axe-core/playwright').default;

const PAGES = [
  { file: 'index.html', pins: true },
  { file: 'master_datacenter_map.html', pins: true },
  { file: 'opposition-map.html', counties: '#map' },
  // Opens on the state view with its markers held in the cluster group, not
  // on the map, so count the group itself.
  { file: 'opposition-tracker.html', pins: true, markers: 'clusterGroup' },
  { file: 'proposals-map.html', pins: true },
  { file: 'restriction-model.html', counties: '#scoreMap' },
  { file: 'developments-dashboard.html' },
  { file: 'positions-dashboard.html' },
  { file: 'data-operations.html' },
  { file: 'opposition-dashboard.html' },
  { file: 'project-lifecycles.html' },
  { file: 'county-profile.html' },
  { file: 'trackdatacenters-proposals.html' }
];

// Markers on any Leaflet map in the page: DOM icons plus vector markers drawn
// on a canvas renderer, which leave no DOM node to count.
async function markerCount(page, group) {
  return page.evaluate(group => {
    if (group) { try { return window.eval(group).getLayers().length; } catch (e) { return 0; } }
    let n = document.querySelectorAll('.leaflet-marker-icon').length;
    const maps = [];
    try { if (typeof map !== 'undefined' && map && map.eachLayer) maps.push(map); } catch (e) { /* none */ }
    maps.forEach(m => m.eachLayer(l => {
      if (window.L && (l instanceof L.CircleMarker || l instanceof L.Marker)) n++;
      if (l.getLayers && window.L && l instanceof L.MarkerClusterGroup) n += l.getLayers().length;
    }));
    return n;
  }, group || null);
}

for (const p of PAGES) {
  test(`${p.file}: loads clean`, async ({ page }) => {
    // axe over a page with thousands of table rows takes about a minute.
    test.setTimeout(180000);
    await page.goto('/' + p.file);
    await page.waitForLoadState('networkidle');
    if (p.counties) {
      await expect(page.locator(p.counties)).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
      const painted = Number(await page.locator(p.counties).getAttribute('data-painted-counties'));
      expect(painted).toBeGreaterThanOrEqual(3144);
    }
    if (p.pins) {
      await expect.poll(() => markerCount(page, p.markers), { timeout: 30000 }).toBeGreaterThan(0);
    }
    const bannerEl = page.locator('#error-banner');
    if (await bannerEl.count()) {
      await expect(bannerEl).toBeHidden();
    }
    expect(page.consoleErrors, 'console errors').toEqual([]);

    const axe = await new AxeBuilder({ page }).analyze();
    const critical = axe.violations.filter(v => v.impact === 'critical');
    const serious = axe.violations.filter(v => v.impact === 'serious');
    if (serious.length) {
      test.info().annotations.push({ type: 'axe-serious',
        description: serious.map(v => `${v.id} (${v.nodes.length})`).join(', ') });
    }
    expect(critical.map(v => `${v.id}: ${v.help} (${v.nodes.length} nodes, e.g. ${v.nodes[0].target})`),
      'critical axe violations').toEqual([]);
  });
}
