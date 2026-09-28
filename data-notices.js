/*
 * data-notices.js
 *
 * Legally required third-party notices, in one registry. Only notices that a
 * provider's terms or licence REQUIRE are listed; courtesy credits are not
 * shown in the app (see DATA_NOTICES.md for the full source record).
 *
 * Opt-in per page. A page shows the (i) button only when its script tag names
 * the notices that apply to the data that page displays:
 *
 *   <script src="./data-notices.js" data-notices="census,nass"></script>
 *
 * With no data-notices attribute, nothing is shown. Where a notice is better
 * carried by an existing attribution surface (map tiles in the Leaflet
 * attribution control via basemap.js, dataset credits in a page footer), the
 * page uses that surface instead and does not load this module.
 *
 * Required notice wording is verbatim from the provider's terms. Do not
 * paraphrase it.
 */
(function () {
  'use strict';

  var NOTICES = [
    { id: 'census', source: 'U.S. Census Bureau Data API',
      text: 'This product uses the Census Bureau Data API but is not endorsed or ' +
            'certified by the Census Bureau.',
      detail: 'County population, income, education and land area (American Community ' +
              'Survey 5-year estimates).',
      url: 'https://www.census.gov/data/developers/about/terms-of-service.html' },
    { id: 'nass', source: 'USDA NASS Quick Stats',
      text: 'This product uses the NASS API but is not endorsed or certified by NASS.',
      detail: 'County farmland and cropland variables (Census of Agriculture 2022).',
      url: 'https://quickstats.nass.usda.gov/api' },
    { id: 'pudl', source: 'PUDL (Catalyst Cooperative)',
      text: 'Electricity generation and retail price data from the Public Utility Data ' +
            'Liberation (PUDL) project, Catalyst Cooperative, licensed CC-BY-4.0, compiled ' +
            'from U.S. Energy Information Administration Forms 860 and 861. Use here does ' +
            'not imply endorsement.',
      detail: 'County generating capacity and electricity price variables.',
      url: 'https://catalyst.coop/pudl/' },
    { id: 'im3', source: 'IM3 Open Source Data Center Atlas',
      text: 'Contains information from the IM3 Open Source Data Center Atlas (Pacific ' +
            'Northwest National Laboratory), made available under the Open Database ' +
            'License (ODbL).',
      detail: 'Existing data center locations and footprints.',
      url: 'https://www.osti.gov/biblio/2550666' },
    { id: 'osm', source: 'OpenStreetMap',
      text: 'Map data © OpenStreetMap contributors, available under the Open ' +
            'Database License (ODbL).',
      detail: 'Source of the IM3 Atlas facility locations.',
      url: 'https://www.openstreetmap.org/copyright' },
    { id: 'epoch', source: 'Epoch AI',
      text: 'Epoch AI, ‘AI data centers’, published online at epoch.ai, ' +
            'licensed CC-BY.',
      detail: 'Frontier AI data center sites.',
      url: 'https://epoch.ai/data/data-centers-documentation' },
    { id: 'moratorium', source: 'Moratorium Nation',
      text: 'Bommarito, Michael J. (2026). Moratorium Nation: U.S. Infrastructure ' +
            'Moratorium Data [Data set], licensed CC-BY-4.0. Records are reviewed and ' +
            'may be reclassified before use here.',
      detail: 'Third-party restriction census rows and upstream records.',
      url: 'https://github.com/mjbommar/moratorium-data-2026' }
  ];

  var BY_ID = {};
  NOTICES.forEach(function (n) { BY_ID[n.id] = n; });

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function item(n) {
    return '<li><b>' + esc(n.source) + '.</b> ' + esc(n.text) +
      (n.detail ? ' <span class="dn-detail">' + esc(n.detail) + '</span>' : '') +
      (n.url ? ' <a href="' + esc(n.url) + '" target="_blank" rel="noopener">terms</a>' : '') +
      '</li>';
  }

  var CSS =
    '.dn-fab{position:fixed;left:12px;bottom:12px;z-index:1100;width:26px;height:26px;' +
      'border-radius:50%;border:1px solid var(--border,#2c3544);background:var(--panel,#161c25);' +
      'color:var(--muted,#8a96a4);font:700 13px/24px Inter,system-ui,sans-serif;text-align:center;' +
      'cursor:pointer;padding:0;box-shadow:0 2px 8px rgba(0,0,0,.35)}' +
    '.dn-fab:hover,.dn-fab[aria-expanded=true]{color:var(--text,#eef2f7);' +
      'border-color:var(--atlas,#4fc3f7)}' +
    '.dn-fab:focus-visible{outline:2px solid var(--atlas,#4fc3f7);outline-offset:2px}' +
    '.dn-panel{position:fixed;left:12px;bottom:46px;z-index:1100;width:min(420px,calc(100vw - 24px));' +
      'max-height:min(70vh,520px);overflow:auto;background:var(--panel,#161c25);' +
      'color:var(--text,#eef2f7);border:1px solid var(--border,#2c3544);border-radius:10px;' +
      'padding:12px 14px;font:12px/1.55 Inter,system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.45)}' +
    '.dn-panel[hidden]{display:none}' +
    '.dn-panel h4{font-size:13px;font-weight:700;margin:0 0 8px}' +
    '.dn-list{margin:0;padding:0 0 0 16px}.dn-list li{margin:0 0 6px}' +
    '.dn-detail{color:var(--muted,#8a96a4)}' +
    '.dn-panel a{color:var(--atlas,#4fc3f7)}';

  function requested() {
    var el = document.currentScript ||
      document.querySelector('script[src*="data-notices.js"]');
    var raw = el ? (el.getAttribute('data-notices') || '') : '';
    return raw.split(',').map(function (s) { return s.trim(); })
      .filter(function (id) { return BY_ID[id]; });
  }

  var IDS = requested();

  function mount() {
    if (!IDS.length || document.getElementById('dn-fab')) return;
    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);

    var panel = document.createElement('div');
    panel.id = 'dn-panel';
    panel.className = 'dn-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Data notices');
    panel.hidden = true;
    panel.innerHTML = '<h4>Data notices</h4><ul class="dn-list">' +
      IDS.map(function (id) { return item(BY_ID[id]); }).join('') + '</ul>';

    var btn = document.createElement('button');
    btn.id = 'dn-fab';
    btn.className = 'dn-fab';
    btn.type = 'button';
    btn.textContent = 'i';
    btn.title = 'Data notices';
    btn.setAttribute('aria-label', 'Data notices');
    btn.setAttribute('aria-controls', 'dn-panel');
    btn.setAttribute('aria-expanded', 'false');

    function set(open) {
      panel.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    btn.addEventListener('click', function (e) { e.stopPropagation(); set(panel.hidden); });
    panel.addEventListener('click', function (e) { e.stopPropagation(); });
    document.addEventListener('click', function () { if (!panel.hidden) set(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !panel.hidden) { set(false); btn.focus(); }
    });

    document.body.appendChild(panel);
    document.body.appendChild(btn);
  }

  window.DataNotices = { list: NOTICES.slice(), shown: IDS.slice() };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
