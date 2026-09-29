// US4: year-range slider (permalinked) and the detail side panel.
'use strict';
const { test, expect } = require('./fixtures');

async function openProjects(page, url) {
  await page.goto(url || '/opposition-map.html');
  await expect(page.locator('#map')).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
  if (await page.locator('#project-panel').isHidden()) await page.click('#mode-project');
  await expect(page.locator('#list-count')).not.toHaveText('', { timeout: 15000 });
}
const visible = page => page.evaluate(() => LAST_VISIBLE);

test('slider to 2025 filters pins and the permalink restores range and pins', async ({ page }) => {
  await openProjects(page);
  const all = await visible(page);
  const from = page.locator('#year-from');
  const max = Number(await from.getAttribute('max'));
  expect(max).toBeGreaterThanOrEqual(2025);
  await from.fill('2025');                       // range inputs accept fill(); fires input
  await expect(page.locator('#year-out')).toContainText('2025');
  const narrowed = await visible(page);
  expect(narrowed).toBeGreaterThan(0);
  expect(narrowed).toBeLessThan(all);
  // Every visible project was announced in 2025 or later.
  const years = await page.evaluate(() => visibleProjects().map(p => LegendFilter.yearOf(p.announced)));
  expect(years.every(y => y !== null && y >= 2025)).toBe(true);

  await expect.poll(() => page.evaluate(() => location.hash)).toContain('from=2025');
  const link = await page.evaluate(() => location.pathname + location.hash);
  const fresh = await page.context().newPage();
  const { offline } = require('./fixtures');
  await offline(fresh);
  await openProjects(fresh, link);
  await expect(fresh.locator('#year-from')).toHaveValue('2025');
  await expect(fresh.locator('#year-out')).toContainText('2025');
  expect(await visible(fresh)).toBe(narrowed);
});

test('an open range keeps undated projects and writes no from/to', async ({ page }) => {
  await openProjects(page);
  await expect(page.locator('#year-out')).toHaveText('All years, including undated');
  const hash = await page.evaluate(() => location.hash);
  expect(hash).not.toMatch(/[&#]from=|[&#]to=/);
  const undated = await page.evaluate(() =>
    visibleProjects().filter(p => LegendFilter.yearOf(p.announced) === null).length);
  expect(undated).toBeGreaterThan(0);
});

test('clicking a list row opens the detail panel; Escape closes it and returns focus', async ({ page }) => {
  await openProjects(page);
  const row = page.locator('#project-list .prow').first();
  const name = (await row.locator('.nm').textContent()).trim();
  await row.click();
  const panel = page.locator('#detail');
  await expect(panel).toBeVisible({ timeout: 15000 });
  await expect(panel.locator('h4')).toHaveText(name);
  await expect(page.locator('.leaflet-popup')).toHaveCount(0);
  const AxeBuilder = require('@axe-core/playwright').default;
  const axe = await new AxeBuilder({ page }).include('#detail').include('#year-range').analyze();
  expect(axe.violations.filter(v => v.impact === 'critical' || v.impact === 'serious').map(v => v.id)).toEqual([]);
  await page.screenshot({ path: test.info().outputPath('panel.png') });
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(row).toBeFocused();
  await expect(row).toHaveAttribute('aria-current', 'false');
});

test('clicking a county opens its detail in the panel', async ({ page }) => {
  await page.goto('/opposition-map.html#map=6/41.6/-72.7');
  await expect(page.locator('#map')).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
  // A real click on a county, at a point where nothing (a pin cluster, a
  // control) sits above the county canvas.
  const pt = await page.evaluate(() => {
    const rect = document.getElementById('map').getBoundingClientRect();
    for (const l of countyLayer.getLayers()) {
      const c = map.latLngToContainerPoint(l.getBounds().getCenter());
      const x = rect.left + c.x, y = rect.top + c.y;
      if (c.x < 40 || c.y < 40 || c.x > rect.width - 360 || c.y > rect.height - 60) continue;
      const el = document.elementFromPoint(x, y);
      if (el && el.tagName === 'CANVAS' && el.closest('.leaflet-countyPane-pane, .leaflet-pane')) return { x, y };
    }
    return null;
  });
  expect(pt).not.toBeNull();
  await page.mouse.click(pt.x, pt.y);
  const panel = page.locator('#detail');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText(/score|No score|outside the model frame/);
  await page.click('#detail-close');
  await expect(panel).toBeHidden();
});
