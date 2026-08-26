/* basemap.js
 *
 * One basemap provider for every map in this repository.
 *
 * Five pages each hard-coded their own tile URL, all of them CARTO's keyless
 * endpoints. CARTO began requiring an API key, so every map in the Notion hub
 * started rendering "API KEY REQUIRED" across the tiles at once, and fixing it
 * meant editing five files. This module makes the provider one line.
 *
 * Why there is no automatic fallback: a watermarked tile is still an HTTP 200.
 * Leaflet's tileerror never fires, so no error-driven chain can detect this
 * failure mode. Changing PROVIDER is the fix, and it is deliberately the only
 * thing that needs changing.
 *
 * The basemap is context, never data. Every map here draws its own geometry
 * (county polygons from GeoJSON, project pins from the feed), so a dead tile
 * provider degrades the page to a dark background with the data still on it.
 * That is why this can fail without taking a surface down.
 *
 * Usage
 *   <script src="./basemap.js"></script>
 *   Basemap.dark().addTo(map);
 *
 * Self-tests: node basemap_selftest.js
 */
(function (global) {
  'use strict';

  var PROVIDERS = {
    // Keyless. Esri's dark canvas is the closest match to the palette the
    // pages were designed against. Note the {z}/{y}/{x} order, which is not
    // the {z}/{x}/{y} order every other provider here uses.
    esri_dark: {
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/' +
           'World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri',
      maxZoom: 16,
      subdomains: []
    },
    esri_light: {
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/' +
           'World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri',
      maxZoom: 16,
      subdomains: []
    },
    // Requires an API key as of 2026-08. Retained so the previous behaviour is
    // one word away, and so nobody re-adds it without seeing why it was left.
    carto_dark: {
      url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      attribution: '&copy; OpenStreetMap &copy; CARTO',
      maxZoom: 19,
      subdomains: ['a', 'b', 'c', 'd'],
      requiresKey: true
    },
    osm: {
      url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
      subdomains: []
    }
  };

  // Change this one word to change every map in the repository.
  var PROVIDER = 'esri_dark';

  function spec(name) {
    return PROVIDERS[name || PROVIDER] || PROVIDERS[PROVIDER];
  }

  function dark(opts) {
    opts = opts || {};
    var s = spec(opts.provider);
    var L = opts.L || global.L;
    if (!L || !L.tileLayer) return null;
    var config = {
      attribution: s.attribution,
      maxZoom: opts.maxZoom || s.maxZoom
    };
    if (s.subdomains && s.subdomains.length) config.subdomains = s.subdomains;
    if (opts.opacity !== undefined) config.opacity = opts.opacity;
    if (opts.className) config.className = opts.className;
    return L.tileLayer(s.url, config);
  }

  var api = {
    PROVIDER: PROVIDER,
    PROVIDERS: PROVIDERS,
    spec: spec,
    dark: dark
  };

  global.Basemap = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
