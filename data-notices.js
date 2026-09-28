/*
 * data-notices.js
 *
 * Third-party data notices, attribution and credit lines, in one place.
 *
 * Some sources the platform draws on require a notice to be shown in the
 * application itself (the NASS and Census Bureau APIs), and some licences
 * require attribution (PUDL, CC-BY-4.0; OpenStreetMap, ODbL; the U.S. Drought
 * Monitor's citation request). Keeping every notice in this one registry means
 * a new source is one entry here, and no page can carry a stale or divergent
 * copy of a required sentence.
 *
 * What it does
 *   1. Adds a floating (i) button to every page that loads it. Clicking it
 *      opens a panel listing every notice. It works inside the Notion and
 *      Simple.ink embeds, where #site-nav is hidden.
 *   2. Exposes window.DataNotices.inline(ids, label) -> HTML string: a small
 *      clickable (i) (a <details> element, so it works without JS handlers)
 *      listing only the notices for the given source ids. Pages place it
 *      next to the numbers that come from those sources.
 *
 * Wording of required notices is verbatim from the provider's terms. Do not
 * paraphrase them. Load with <script src="./data-notices.js"></script> in
 * <head>; the floating button is added on DOMContentLoaded.
 */
(function () {
  'use strict';

  var NOTICES = [
    { id: 'nass', kind: 'required', source: 'USDA NASS Quick Stats',
      text: 'This product uses the NASS API but is not endorsed or certified by NASS.',
      detail: 'County farmland and cropland variables: USDA National Agricultural ' +
              'Statistics Service, Census of Agriculture 2022.',
      url: 'https://quickstats.nass.usda.gov/api' },
    { id: 'census', kind: 'required', source: 'U.S. Census Bureau Data API',
      text: 'This product uses the Census Bureau Data API but is not endorsed or ' +
            'certified by the Census Bureau.',
      detail: 'County population, income, education and land area: American Community ' +
              'Survey 5-year estimates and the Census Gazetteer.',
      url: 'https://www.census.gov/data/developers/about/terms-of-service.html' },
    { id: 'usdm', kind: 'credit', source: 'U.S. Drought Monitor',
      text: 'Drought data: U.S. Drought Monitor, produced by the National Drought ' +
            'Mitigation Center (NDMC), the United States Department of Agriculture ' +
            '(USDA) and the National Oceanic and Atmospheric Administration (NOAA).',
      detail: 'County drought variables, 2020 to 2024.',
      url: 'https://droughtmonitor.unl.edu/' },
    { id: 'pudl', kind: 'required', source: 'PUDL (Catalyst Cooperative)',
      text: 'Electricity generation and retail price data: Public Utility Data Liberation ' +
            '(PUDL) project, Catalyst Cooperative, licensed CC-BY-4.0. Compiled from U.S. ' +
            'Energy Information Administration Forms 860 and 861. Use here does not imply ' +
            'endorsement by Catalyst Cooperative or EIA.',
      detail: 'County generating capacity and commercial and industrial electricity prices.',
      url: 'https://catalyst.coop/pudl/' },
    { id: 'osm', kind: 'required', source: 'OpenStreetMap',
      text: 'Map data © OpenStreetMap contributors, available under the Open Database ' +
            'License (ODbL).',
      detail: 'Existing data center footprints (via the IM3 Atlas), facility candidate ' +
              'locations, and the base map.',
      url: 'https://www.openstreetmap.org/copyright' },
    { id: 'esri', kind: 'required', source: 'Esri base map',
      text: 'Powered by Esri. Base map: Esri, HERE, Garmin, \u00a9 OpenStreetMap ' +
            'contributors, and the GIS user community.',
      detail: 'Map tiles on the Atlas and Opposition Tracker.',
      url: 'https://developers.arcgis.com/documentation/esri-and-data-attribution/' },
    { id: 'im3', kind: 'required', source: 'IM3 Open Source Data Center Atlas',
      text: 'Contains information from the IM3 Open Source Data Center Atlas (Pacific ' +
            'Northwest National Laboratory), made available under the Open Database ' +
            'License (ODbL). Derived from OpenStreetMap.',
      detail: 'Existing data center locations, footprints and county counts.',
      url: 'https://www.osti.gov/biblio/2550666' },
    { id: 'epoch', kind: 'required', source: 'Epoch AI',
      text: 'Epoch AI, \u2018AI data centers\u2019, published online at epoch.ai, licensed ' +
            'CC-BY.',
      detail: 'Frontier AI data center sites, power and capital estimates.',
      url: 'https://epoch.ai/data/data-centers-documentation' },
    { id: 'usgs', kind: 'credit', source: 'U.S. Geological Survey',
      text: 'Water use data: U.S. Geological Survey, Estimated Use of Water in the ' +
            'United States, county data 2015.',
      detail: 'County freshwater withdrawal variables.',
      url: 'https://www.usgs.gov/mission-areas/water-resources/science/water-use-united-states' },
    { id: 'elections', kind: 'credit', source: 'County presidential results',
      text: 'Compiled by tonmcg (US_County_Level_Election_Results_08-24, MIT License) ' +
            'from results published by Townhall.com (2016) and Fox News (2024). The ' +
            'compiler notes it is not an authoritative source.',
      detail: '2016 and 2024 county presidential margins.',
      url: 'https://github.com/tonmcg/US_County_Level_Election_Results_08-24' },
    { id: 'openstates', kind: 'credit', source: 'Open States',
      text: 'State legislative data from Open States (Plural).',
      detail: 'Bill histories, stages and roll-call votes.',
      url: 'https://openstates.org/' },
    { id: 'gdelt', kind: 'credit', source: 'The GDELT Project',
      text: 'Candidate opposition events are discovered through The GDELT Project.',
      detail: 'Used to find candidate opposition events; every recorded event carries ' +
              'its own source link.',
      url: 'https://www.gdeltproject.org/' }
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
      (n.url ? ' <a href="' + esc(n.url) + '" target="_blank" rel="noopener">source</a>' : '') +
      '</li>';
  }

  function list(ns) {
    return '<ul class="dn-list">' + ns.map(item).join('') + '</ul>';
  }

  var CSS =
    '.dn-fab{position:fixed;left:12px;bottom:12px;z-index:1100;width:26px;height:26px;' +
      'border-radius:50%;border:1px solid var(--border,#2c3544);background:var(--panel,#161c25);' +
      'color:var(--muted,#8a96a4);font:700 13px/24px Inter,system-ui,sans-serif;text-align:center;' +
      'cursor:pointer;padding:0;box-shadow:0 2px 8px rgba(0,0,0,.35)}' +
    '.dn-fab:hover,.dn-fab[aria-expanded=true]{color:var(--text,#eef2f7);' +
      'border-color:var(--atlas,#4fc3f7)}' +
    '.dn-fab:focus-visible{outline:2px solid var(--atlas,#4fc3f7);outline-offset:2px}' +
    '.dn-panel{position:fixed;left:12px;bottom:46px;z-index:1100;width:min(440px,calc(100vw - 24px));' +
      'max-height:min(70vh,560px);overflow:auto;background:var(--panel,#161c25);' +
      'color:var(--text,#eef2f7);border:1px solid var(--border,#2c3544);border-radius:10px;' +
      'padding:12px 14px;font:12px/1.55 Inter,system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.45)}' +
    '.dn-panel[hidden]{display:none}' +
    '.dn-panel h4{font-size:13px;font-weight:700;margin:0 0 6px}' +
    '.dn-panel h5{font-size:11px;font-weight:600;margin:10px 0 4px;color:var(--muted,#8a96a4);' +
      'text-transform:uppercase;letter-spacing:.04em}' +
    '.dn-list{margin:0;padding:0 0 0 16px}.dn-list li{margin:0 0 6px}' +
    '.dn-detail{color:var(--muted,#8a96a4)}' +
    '.dn-panel a,.dn-inline a{color:var(--atlas,#4fc3f7)}' +
    /* details.dn-inline outranks a page's own bare details/summary card styles */
    'details.dn-inline,details.dn-inline[open]{display:inline;margin:0 0 0 6px;padding:0;' +
      'background:none;border:0;border-radius:0;font-weight:400}' +
    'details.dn-inline>summary{display:inline-block;vertical-align:middle;list-style:none;cursor:pointer;' +
      'padding:0;margin:0;box-sizing:border-box;' +
      'width:15px;height:15px;border:1px solid var(--border,#2c3544);border-radius:50%;' +
      'color:var(--muted,#8a96a4);font:700 10px/13px Inter,system-ui,sans-serif;text-align:center}' +
    'details.dn-inline>summary::-webkit-details-marker{display:none}' +
    'details.dn-inline>summary::before{content:none}' +
    'details.dn-inline[open]>summary{background:var(--atlas,#4fc3f7);border-color:var(--atlas,#4fc3f7);' +
      'color:#08131a}' +
    'details.dn-inline>div{display:block;margin-top:8px;font:400 12px/1.55 Inter,system-ui,sans-serif;' +
      'color:var(--text,#eef2f7);text-transform:none;letter-spacing:0}';

  function injectCss() {
    if (document.getElementById('dn-style')) return;
    var st = document.createElement('style');
    st.id = 'dn-style';
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }

  function inline(ids, label) {
    injectCss();
    var ns = (ids || []).map(function (i) { return BY_ID[i]; }).filter(Boolean);
    if (!ns.length) return '';
    var lab = label || 'Data sources and notices';
    return '<details class="dn-inline"><summary aria-label="' + esc(lab) + '" title="' +
      esc(lab) + '">i</summary><div>' + list(ns) + '</div></details>';
  }

  function mountFab() {
    if (document.getElementById('dn-fab')) return;
    injectCss();
    var req = NOTICES.filter(function (n) { return n.kind === 'required'; });
    var cred = NOTICES.filter(function (n) { return n.kind !== 'required'; });

    var panel = document.createElement('div');
    panel.id = 'dn-panel';
    panel.className = 'dn-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Data sources and notices');
    panel.hidden = true;
    panel.innerHTML = '<h4>Data sources and notices</h4>' +
      '<h5>Required notices</h5>' + list(req) +
      '<h5>Other sources</h5>' + list(cred);

    var btn = document.createElement('button');
    btn.id = 'dn-fab';
    btn.className = 'dn-fab';
    btn.type = 'button';
    btn.textContent = 'i';
    btn.title = 'Data sources and notices';
    btn.setAttribute('aria-label', 'Data sources and notices');
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

  window.DataNotices = { list: NOTICES.slice(), inline: inline, mount: mountFab };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountFab);
  } else {
    mountFab();
  }
})();
