// US1 / SC-001: every scored county paints on both choropleth pages.
'use strict';
const { test, expect } = require('./fixtures');

const PAGES = [
  { file: 'restriction-model.html', map: '#scoreMap' },
  { file: 'opposition-map.html', map: '#map' }
];

for (const p of PAGES) {
  test(`${p.file}: 3,144 of 3,144 scored counties paint`, async ({ page }) => {
    await page.goto('/' + p.file);
    const map = page.locator(p.map);
    await expect(map).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
    const scored = Number(await map.getAttribute('data-scored-counties'));
    const painted = Number(await map.getAttribute('data-painted-counties'));
    expect(scored).toBeGreaterThanOrEqual(3144);
    expect(painted).toBe(scored);
    await expect(page.locator('#error-banner')).toBeHidden();
    if (p.file === 'restriction-model.html') {
      await expect(page.locator('#undrawable')).toBeHidden();
    }
  });
}

test('geometry fetch failure shows the error banner', async ({ page }) => {
  await page.route('**/counties_2024.topojson', route => route.fulfill({ status: 404, body: '' }));
  await page.goto('/restriction-model.html');
  await expect(page.locator('#error-banner')).toBeVisible({ timeout: 30000 });
  await expect(page.locator('#error-banner')).toContainText('County boundary data was not reachable');
});

// SC-003 negative case: a copy of the page with its geometry URL broken must
// trip the same banner check every page is held to in pages.spec.js.
test('a page copy with a broken fetch URL fails the banner check', async ({ page }) => {
  const fs = require('fs');
  const path = require('path');
  const { ROOT } = require('./fixtures');
  const html = fs.readFileSync(path.join(ROOT, 'restriction-model.html'), 'utf8')
    .split('/data/geo/counties_2024.topojson').join('/data/geo/no_such_file.topojson');
  await page.route('**/restriction-model-broken.html', route =>
    route.fulfill({ status: 200, contentType: 'text/html', body: html }));
  await page.goto('/restriction-model-broken.html');
  await expect(page.locator('#error-banner')).toBeVisible({ timeout: 30000 });
});
