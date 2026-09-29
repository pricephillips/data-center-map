// US3: dashboard tables sort, filter, and export the filtered view as LF CSV.
'use strict';
const fs = require('fs');
const { test, expect } = require('./fixtures');

async function exportCsv(page, scope) {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator(scope + ' .tab-export').first().click()
  ]);
  return fs.readFileSync(await download.path(), 'utf8');
}

test('developments: filter by state, export only the filtered rows with LF endings', async ({ page }) => {
  await page.goto('/developments-dashboard.html');
  await expect(page.locator('.table-wrap .tabulator-row').first()).toBeVisible({ timeout: 30000 });
  // Column titles are the page's own, unchanged.
  const titles = await page.locator('.table-wrap .tabulator-col-title').allTextContents();
  expect(titles.map(t => t.trim())).toEqual(
    ['Name', 'Layer', 'State / Country', 'Operator / Owner', 'Type', 'Sq ft', 'MW', 'Capex ($B)', 'Notes', 'Src']);
  await page.selectOption('#tf-state', 'Virginia');
  const csv = await exportCsv(page, '.table-wrap');
  expect(csv).not.toContain('\r');
  const lines = csv.trim().split('\n');
  expect(lines[0]).toContain('State / Country');
  expect(lines.length).toBeGreaterThan(2);
  const body = lines.slice(1);
  expect(body.every(l => l.includes('Virginia'))).toBe(true);
  expect(body.some(l => /\b(IM3|EFD)"?,/.test(l.split(',')[0] + ','))).toBe(false);

  // A header filter narrows the export further.
  const opFilter = page.locator('.table-wrap .tabulator-header-filter input').nth(3);
  const firstOp = (await page.locator('.table-wrap .tabulator-row .tabulator-cell[tabulator-field="c3"]').first().textContent()).trim();
  await opFilter.fill(firstOp);
  await opFilter.press('End'); // Tabulator's live filter listens for keyup
  await page.waitForTimeout(400);
  const csv2 = await exportCsv(page, '.table-wrap');
  const body2 = csv2.trim().split('\n').slice(1);
  expect(body2.length).toBeGreaterThan(0);
  expect(body2.length).toBeLessThanOrEqual(body.length);
  expect(body2.every(l => l.includes(firstOp))).toBe(true);
});

test('developments: header click sorts numerically', async ({ page }) => {
  await page.goto('/developments-dashboard.html');
  await expect(page.locator('.table-wrap .tabulator-row').first()).toBeVisible({ timeout: 30000 });
  await page.selectOption('#tf-layer', 'ai');
  await page.locator('.table-wrap .tabulator-col[tabulator-field="c6"] .tabulator-col-title').click();
  await page.locator('.table-wrap .tabulator-col[tabulator-field="c6"] .tabulator-col-title').click();
  const mw = await page.locator('.table-wrap .tabulator-row .tabulator-cell[tabulator-field="c6"]').allTextContents();
  const nums = mw.map(t => parseFloat(t.replace(/,/g, ''))).filter(Number.isFinite);
  expect(nums.length).toBeGreaterThan(3);
  const sortedDesc = [...nums].sort((a, b) => b - a);
  const sortedAsc = [...nums].sort((a, b) => a - b);
  expect(JSON.stringify(nums) === JSON.stringify(sortedDesc) || JSON.stringify(nums) === JSON.stringify(sortedAsc)).toBe(true);
});

for (const p of [
  { file: 'positions-dashboard.html', scope: '#body' },
  { file: 'data-operations.html', scope: 'body' }
]) {
  test(`${p.file}: tables are Tabulator tables and export LF CSV`, async ({ page }) => {
    await page.goto('/' + p.file);
    await expect(page.locator(p.scope + ' .tab-host.tabulator').first()).toBeVisible({ timeout: 30000 });
    const plainVisible = await page.locator(p.scope + ' table:visible').count();
    expect(plainVisible).toBe(0);
    const csv = await exportCsv(page, p.scope);
    expect(csv).not.toContain('\r');
    const lines = csv.trim().split('\n');
    expect(lines.length).toBeGreaterThan(1);
    expect(lines[1]).not.toMatch(/<[a-z]/i); // text, not the display HTML
  });
}
