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
