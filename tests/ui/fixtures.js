// Shared test fixture: an offline page (spec 009, FR-006).
//
// Every request the page makes is intercepted:
//   - the local static server passes through;
//   - raw.githubusercontent.com/pricephillips/data-center-map/main/<path> is
//     served from the working tree, so the raw-first fetch chain is exercised
//     exactly as deployed but against the files under test;
//   - pinned CDN library URLs (unpkg, jsdelivr npm, cdnjs PapaParse) are served
//     from tests/ui/node_modules, whose versions package.json pins to match;
//   - anything else (basemap tiles and styles, third-party data) is aborted and
//     recorded in page.blockedHosts.
// Console errors are collected in page.consoleErrors, minus the network noise
// those aborts produce by design.
'use strict';
const fs = require('fs');
const path = require('path');
const base = require('@playwright/test');

const ROOT = path.resolve(__dirname, '..', '..');
const NM = path.join(__dirname, 'node_modules');
const RAW_PREFIX = 'https://raw.githubusercontent.com/pricephillips/data-center-map/main/';

const TYPES = {
  '.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css',
  '.json': 'application/json', '.topojson': 'application/json', '.csv': 'text/csv',
  '.md': 'text/plain', '.png': 'image/png', '.svg': 'image/svg+xml'
};

// Map a CDN URL to a file inside node_modules, or null.
function cdnToLocal(u) {
  let m = /^https:\/\/(?:unpkg\.com|cdn\.jsdelivr\.net\/npm)\/((?:@[^/]+\/)?[^@/]+)(?:@[^/]+)?\/(.+)$/.exec(u);
  if (m) return path.join(NM, m[1], m[2]);
  m = /^https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/PapaParse\/[^/]+\/(.+)$/.exec(u);
  if (m) return path.join(NM, 'papaparse', m[1]);
  return null;
}

function localFor(u) {
  if (u.startsWith(RAW_PREFIX)) return path.join(ROOT, u.slice(RAW_PREFIX.length));
  return cdnToLocal(u);
}

async function fulfillFile(route, file) {
  if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    return route.fulfill({ status: 404, body: 'not found' });
  }
  return route.fulfill({
    status: 200,
    body: fs.readFileSync(file),
    headers: {
      'content-type': TYPES[path.extname(file)] || 'application/octet-stream',
      'access-control-allow-origin': '*'
    }
  });
}

// Extra per-test routes: { urlSubstring: 'abort' | filePath | (route) => ... }.
async function offline(page, overrides) {
  page.blockedHosts = new Set();
  page.consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    // Aborted third-party requests and 404s for optional data files are the
    // harness working as intended, not page errors.
    if (/Failed to load resource|net::ERR_/.test(text)) return;
    page.consoleErrors.push(text);
  });
  page.on('pageerror', err => page.consoleErrors.push('pageerror: ' + err.message));
  await page.route('**/*', async route => {
    const u = route.request().url();
    for (const [needle, action] of Object.entries(overrides || {})) {
      if (!u.includes(needle)) continue;
      if (action === 'abort') return route.abort();
      if (typeof action === 'function') return action(route);
      return fulfillFile(route, action);
    }
    if (u.startsWith('http://127.0.0.1') || u.startsWith('http://localhost') ||
        u.startsWith('data:') || u.startsWith('blob:')) {
      return route.continue();
    }
    const local = localFor(u);
    if (local) return fulfillFile(route, local);
    try { page.blockedHosts.add(new URL(u).host); } catch (e) { /* ignore */ }
    return route.abort();
  });
}

const test = base.test.extend({
  page: async ({ page }, use) => {
    await offline(page);
    await use(page);
  }
});

module.exports = { test, expect: base.expect, offline, cdnToLocal, ROOT };
