// Spec 012, US4: swipe compare on restriction-model (dragged, keyboard,
// permalinked), legends built from the layer's symbology, and the shared
// hover tooltip on every pin map.
'use strict';
const { test, expect, offline } = require('./fixtures');

async function openModel(page, url) {
  await page.goto(url || '/restriction-model.html');
  await expect(page.locator('#scoreMap')).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
}
const handle = page => page.locator('#scoreMap .swipe-handle');

test('dragging the swipe divider is written to the permalink and restored on reload', async ({ page }) => {
  await openModel(page);
  await expect(page.locator('#swipeToggle')).toBeVisible();
  await page.click('#swipeToggle');
  await expect(page.locator('#swipeToggle')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#scoreMap .swipe')).toBeVisible({ timeout: 15000 });
  await expect(handle(page)).toHaveAttribute('aria-valuenow', '50');
  // The margin legend is the layer's symbology read back: same breaks.
  await expect(page.locator('#marginLegend')).toBeVisible();
  const breaks = await page.locator('#marginLegend').getAttribute('data-legend-breaks');
  expect(JSON.parse(breaks)).toEqual(await page.evaluate(() => LegendFilter.marginSymbology().breaks));
  await expect(page.locator('#marginLegend')).toContainText('R +50');
  await expect(page.locator('#marginLegend')).toContainText('D +50');

  // Drag the handle to about 30 percent of the map width.
  const box = await page.locator('#scoreMap').boundingBox();
  const hb = await handle(page).boundingBox();
  await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.42, hb.y + hb.height / 2, { steps: 4 });
  await page.mouse.move(box.x + box.width * 0.30, hb.y + hb.height / 2, { steps: 4 });
  await page.mouse.up();
  const pct = Number(await handle(page).getAttribute('aria-valuenow'));
  expect(pct).toBeGreaterThanOrEqual(27);
  expect(pct).toBeLessThanOrEqual(33);
  await expect.poll(() => page.evaluate(() => location.hash)).toContain('sw=' + pct);
  // The drag must not have panned the map underneath.
  expect(await page.evaluate(() => location.hash)).toMatch(/map=4\//);

  // Both panes are clipped at the divider, on complementary sides.
  const clips = await page.evaluate(() => ({
    margin: CURRENT_MAP.getPane('marginPane').style.clip,
    score: CURRENT_MAP.getPane('overlayPane').style.clip
  }));
  expect(clips.margin).toMatch(/^rect\(/);
  expect(clips.score).toMatch(/^rect\(/);

  // Keyboard: the handle is a slider.
  await handle(page).focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await expect(handle(page)).toHaveAttribute('aria-valuenow', String(pct + 2));
  await expect.poll(() => page.evaluate(() => location.hash)).toContain('sw=' + (pct + 2));
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  await expect(handle(page)).toHaveAttribute('aria-valuenow', String(pct));
  await expect.poll(() => page.evaluate(() => location.hash)).toContain('sw=' + pct);

  // Reload from the permalink in a fresh page: same position, swipe on.
  const link = await page.evaluate(() => location.pathname + location.hash);
  const fresh = await page.context().newPage();
  await offline(fresh);
  await openModel(fresh, link);
  await expect(fresh.locator('#scoreMap .swipe')).toBeVisible({ timeout: 15000 });
  await expect(handle(fresh)).toHaveAttribute('aria-valuenow', String(pct));
  await expect(fresh.locator('#swipeToggle')).toHaveAttribute('aria-pressed', 'true');
  expect(await fresh.evaluate(() => location.hash)).toContain('sw=' + pct);
  expect(fresh.consoleErrors, 'console errors').toEqual([]);

  // Off drops the key and leaves every other key alone.
  await fresh.click('#swipeToggle');
  await expect(fresh.locator('#scoreMap .swipe')).toBeHidden();
  await expect.poll(() => fresh.evaluate(() => location.hash)).not.toContain('sw=');
  expect(await fresh.evaluate(() => location.hash)).toMatch(/map=/);
  expect(page.consoleErrors, 'console errors').toEqual([]);
});

test('a link without sw opens with the swipe off', async ({ page }) => {
  await openModel(page, '/restriction-model.html#map=5/39.0000/-98.0000');
  await expect(page.locator('#swipeToggle')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#scoreMap .swipe')).toBeHidden();
  expect(await page.evaluate(() => location.hash)).not.toContain('sw=');
});

test('opposition-map legends are built from the layer symbology', async ({ page }) => {
  await page.goto('/opposition-map.html#metric=margin_2024');
  await expect(page.locator('#map')).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
  const legend = page.locator('#countyLegend');
  await expect(legend).toHaveAttribute('data-legend-breaks', /.+/);
  const ok = await page.evaluate(() => {
    const sym = LegendFilter.marginSymbology();
    const breaks = JSON.parse(document.getElementById('countyLegend').getAttribute('data-legend-breaks'));
    // Each legend break paints the color the layer's own function returns.
    return JSON.stringify(breaks) === JSON.stringify(sym.breaks) &&
      breaks.every(b => marginColor(b).color === sym.color(b));
  });
  expect(ok).toBe(true);
  // Outcome rows take the colors the pins are painted with.
  const rows = await page.evaluate(() =>
    Array.from(document.querySelectorAll('#outcomeLegendRows .row')).map(r => ({
      key: r.dataset.outcome,
      color: getComputedStyle(r.querySelector('.sw')).backgroundColor,
      text: r.textContent.trim()
    })));
  expect(rows.map(r => r.key)).toEqual(['blocked_confirmed', 'advanced_confirmed', 'pending']);
  expect(rows.map(r => r.text)).toEqual(['blocked (confirmed terminal)', 'advanced (confirmed)', 'pending / undecided']);
  const want = await page.evaluate(() => ['blocked_confirmed', 'advanced_confirmed', 'pending'].map(k => {
    const d = document.createElement('i'); d.style.background = OUTCOME_COLOR[k];
    document.body.appendChild(d); const c = getComputedStyle(d).backgroundColor; d.remove(); return c;
  }));
  expect(rows.map(r => r.color)).toEqual(want);
});

// Capture every Leaflet map a page builds, including maps held inside a
// closure (proposals-map), so the test can find the pins without a page hook.
async function captureMaps(page) {
  await page.addInitScript(() => {
    window.__maps = [];
    let real;
    Object.defineProperty(window, 'L', {
      configurable: true,
      get() { return real; },
      set(v) {
        real = v;
        if (v && v.Map && v.Map.addInitHook) v.Map.addInitHook(function () { window.__maps.push(this); });
      }
    });
  });
}

const REFUSED = /\b(win|wins|won|loss|losses|lost|decided|confirmed_blocks|blocked_share)\b/i;
const PIN_PAGES = ['opposition-map.html', 'opposition-tracker.html', 'proposals-map.html', 'master_datacenter_map.html'];

for (const file of PIN_PAGES) {
  test(`${file}: every pin carries the shared hover label`, async ({ page }) => {
    await captureMaps(page);
    await page.goto('/' + file);
    if (file === 'opposition-map.html') {
      await expect(page.locator('#map')).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
    }
    const read = () => page.evaluate(() => {
      const seen = new Set(), out = [];
      const visit = l => {
        if (!l || seen.has(l)) return;
        seen.add(l);
        if ((l instanceof L.Marker || l instanceof L.CircleMarker) && l.getTooltip && l.getTooltip()) {
          const t = l.getTooltip();
          const c = typeof t._content === 'function' ? t._content(l) : t._content;
          out.push({ cls: t.options.className || '', html: String(c) });
        }
        if (l.getLayers) l.getLayers().forEach(visit);
      };
      (window.__maps || []).forEach(m => m.eachLayer(visit));
      // Cluster groups not yet on a map (the tracker's state view).
      try { if (typeof clusterGroup !== 'undefined') visit(clusterGroup); } catch (e) { /* none */ }
      return out;
    });
    await expect.poll(async () => (await read()).length, { timeout: 30000 }).toBeGreaterThan(0);
    const tips = await read();
    expect(tips.every(t => t.cls.split(' ').includes('lf-pin-tip')), 'shared tooltip class').toBe(true);
    expect(tips.every(t => /^<b>[^<]*<\/b>/.test(t.html)), 'label opens with the name').toBe(true);
    expect(tips.filter(t => REFUSED.test(t.html.replace(/<[^>]+>/g, ' '))).map(t => t.html)).toEqual([]);
    // At most three lines: name, place, outcome.
    expect(tips.every(t => t.html.split('<br>').length <= 3)).toBe(true);
    expect(page.consoleErrors, 'console errors').toEqual([]);
  });
}

test('opposition-map: hovering a pin shows the label, clicking opens the side panel', async ({ page }) => {
  await page.goto('/opposition-map.html');
  await expect(page.locator('#map')).toHaveAttribute('data-painted-counties', /\d+/, { timeout: 30000 });
  if (await page.locator('#project-panel').isHidden()) await page.click('#mode-project');
  await expect(page.locator('#list-count')).not.toHaveText('', { timeout: 15000 });
  // Bring one pin out of its cluster, then hover its real icon.
  const pid = await page.evaluate(() => new Promise(res => {
    const ids = Object.keys(MARKERS);
    const id = ids.find(k => MARKERS[k]._icon) || ids[0];
    if (MARKERS[id]._icon) return res(id);
    pinCluster.zoomToShowLayer(MARKERS[id], () => res(id));
  }));
  const p = await page.evaluate(id => ({ name: BY_ID[id].name, county: BY_ID[id].county }), pid);
  await page.waitForFunction(id => !!MARKERS[id]._icon, pid);
  const icon = await page.evaluateHandle(id => MARKERS[id]._icon, pid);
  const b = await icon.asElement().boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  const tip = page.locator('.leaflet-tooltip.lf-pin-tip');
  await expect(tip).toBeVisible();
  await expect(tip.locator('b')).toHaveText(p.name.length > 60 ? p.name.slice(0, 57) + '...' : p.name);
  if (p.county) await expect(tip).toContainText(p.county);
  await expect(tip).toContainText(/Blocked \(confirmed\)|Advanced \(confirmed\)|Pending \/ undecided/);
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
  await expect(page.locator('#detail')).toBeVisible({ timeout: 15000 });
  expect(page.consoleErrors, 'console errors').toEqual([]);
});
