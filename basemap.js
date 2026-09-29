/* basemap.js
 *
 * One basemap provider for every map in this repository.
 *
 * Five pages each hard-coded their own tile URL, all of them CARTO's keyless
 * endpoints. CARTO began requiring an API key, so every map in the Notion hub
 * started rendering "API KEY REQUIRED" across the tiles at once, and fixing it
 * meant editing five files. This module makes the provider one line.
 *
 * The default is a vector basemap (spec 009, US2). The maps are client
 * facing, and the Esri World Gray Canvas terms and the OpenStreetMap tile
 * usage policy both restrict heavy or commercial use. OpenFreeMap serves
 * OpenStreetMap-derived vector tiles with no key and no usage cap; MapLibre GL
 * JS renders them and maplibre-gl-leaflet puts that canvas in Leaflet's tile
 * pane, under every county polygon and pin, so no page changes how it draws.
 *
 * Fallback chain: openfreemap -> esri_dark -> osm. A vector style fails in
 * ways a raster tile does not, and each one is caught:
 *   - MapLibre or the binding did not load (CDN blocked, old browser);
 *   - no WebGL (maplibregl.supported() is false, or the map constructor throws);
 *   - the style or its tiles error before the first render;
 *   - nothing has rendered after LOAD_TIMEOUT_MS.
 * Raster providers fall through on repeated tileerror before any tile loads.
 * Each switch fires 'basemapfallback' on the map with { from, to, reason }.
 *
 * What no chain can catch: a watermarked tile is still an HTTP 200, so the
 * CARTO "API KEY REQUIRED" failure never fires an error. Changing PROVIDER is
 * the fix for that class, and it is deliberately one word.
 *
 * The basemap is context, never data. Every map here draws its own geometry,
 * so a dead provider degrades the page to a dark background with the data
 * still on it. That is why this can fail without taking a surface down.
 *
 * Usage
 *   <script src="./basemap.js"></script>
 *   Basemap.dark().addTo(map);                       // the chain
 *   Basemap.dark({ provider: 'esri_dark' }).addTo(map); // one raster provider
 *
 * Self-tests: node basemap_selftest.js
 */
(function (global) {
  'use strict';

  var ESRI_ATTRIBUTION = 'Powered by <a href="https://www.esri.com">Esri</a> | ' +
    'Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community';

  var PROVIDERS = {
    // No key, no usage cap. The style JSON names its own tile and glyph
    // sources on the same host. OpenFreeMap asks for credit to itself,
    // OpenMapTiles (the schema) and OpenStreetMap (the data).
    openfreemap: {
      kind: 'vector',
      url: 'https://tiles.openfreemap.org/styles/dark',
      attribution: '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> ' +
        '&copy; <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> ' +
        'Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">' +
        'OpenStreetMap</a> contributors',
      maxZoom: 19,
      subdomains: []
    },
    // Keyless. Esri's dark canvas is the closest match to the palette the
    // pages were designed against. Note the {z}/{y}/{x} order, which is not
    // the {z}/{x}/{y} order every other provider here uses.
    esri_dark: {
      kind: 'raster',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/' +
           'World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      // Esri requires 'Powered by Esri' plus the service's own copyright text
      // (copyrightText on the MapServer endpoint).
      attribution: ESRI_ATTRIBUTION,
      maxZoom: 16,
      subdomains: []
    },
    esri_light: {
      kind: 'raster',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/' +
           'World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      attribution: ESRI_ATTRIBUTION,
      maxZoom: 16,
      subdomains: []
    },
    // Requires an API key as of 2026-08. Retained so the previous behaviour is
    // one word away, and so nobody re-adds it without seeing why it was left.
    carto_dark: {
      kind: 'raster',
      url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      attribution: '&copy; OpenStreetMap &copy; CARTO',
      maxZoom: 19,
      subdomains: ['a', 'b', 'c', 'd'],
      requiresKey: true
    },
    osm: {
      kind: 'raster',
      url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
      subdomains: []
    }
  };

  // Change this one word to change every map in the repository.
  var PROVIDER = 'openfreemap';
  // Tried in order, starting from PROVIDER.
  var CHAIN = ['openfreemap', 'esri_dark', 'osm'];

  // Pinned per configs/integrations.json. MapLibre 6 ships as ES modules only,
  // so it is loaded with import(); it creates its worker from a blob, which is
  // what lets it run from a CDN origin. The binding is a classic script that
  // reads the global maplibregl.
  var MAPLIBRE_URLS = [
    'https://cdn.jsdelivr.net/npm/maplibre-gl@6.11.2/dist/maplibre-gl.mjs',
    'https://unpkg.com/maplibre-gl@6.11.2/dist/maplibre-gl.mjs'
  ];
  var MAPLIBRE_CSS = 'https://cdn.jsdelivr.net/npm/maplibre-gl@6.11.2/dist/maplibre-gl.css';
  var BINDING_URLS = [
    'https://cdn.jsdelivr.net/npm/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js',
    'https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js'
  ];
  var LOAD_TIMEOUT_MS = 8000;
  // The chain's map zoom ceiling. A raster fallback with a lower native
  // ceiling (Esri, 16) upscales its last tiles past it rather than capping
  // the map differently depending on which provider happened to answer.
  var MAX_ZOOM = 18;
  // Raster tiles that must fail, with none loaded, before the chain moves on.
  var RASTER_ERROR_LIMIT = 3;

  function spec(name) {
    return PROVIDERS[name || PROVIDER] || PROVIDERS[PROVIDER];
  }

  function nextProvider(name) {
    var i = CHAIN.indexOf(name);
    return i >= 0 && i + 1 < CHAIN.length ? CHAIN[i + 1] : null;
  }

  // A plain Leaflet tile layer for one raster provider.
  function raster(name, opts) {
    opts = opts || {};
    var s = spec(name);
    var L = opts.L || global.L;
    if (!L || !L.tileLayer) return null;
    var config = {
      attribution: s.attribution,
      maxZoom: opts.maxZoom || s.maxZoom
    };
    if (s.subdomains && s.subdomains.length) config.subdomains = s.subdomains;
    if (opts.maxNativeZoom) config.maxNativeZoom = opts.maxNativeZoom;
    if (opts.opacity !== undefined) config.opacity = opts.opacity;
    if (opts.className) config.className = opts.className;
    return L.tileLayer(s.url, config);
  }

  // ---- lazy loading of the vector renderer ---------------------------------

  var vectorReady = null;

  function loadScript(url) {
    return new Promise(function (resolve, reject) {
      var doc = global.document;
      if (!doc) { reject(new Error('no document')); return; }
      var el = doc.createElement('script');
      el.src = url;
      el.onload = function () { resolve(); };
      el.onerror = function () { reject(new Error('failed ' + url)); };
      doc.head.appendChild(el);
    });
  }

  function firstOf(urls, load) {
    return urls.reduce(function (p, u) {
      return p.catch(function () { return load(u); });
    }, Promise.reject(new Error('start')));
  }

  function loadVector() {
    if (vectorReady) return vectorReady;
    var doc = global.document;
    if (doc && !doc.querySelector('link[data-basemap-css]')) {
      var link = doc.createElement('link');
      link.rel = 'stylesheet';
      link.href = MAPLIBRE_CSS;
      link.setAttribute('data-basemap-css', '');
      doc.head.appendChild(link);
    }
    vectorReady = (global.maplibregl ? Promise.resolve() : firstOf(MAPLIBRE_URLS, function (u) {
      // new Function keeps import() out of this file's parse, so the module
      // still loads in engines that would reject the syntax outright.
      return new Function('u', 'return import(u)')(u).then(function (mod) {
        global.maplibregl = mod;
      });
    })).then(function () {
      if (global.L && global.L.maplibreGL) return null;
      return firstOf(BINDING_URLS, loadScript);
    }).then(function () {
      if (!global.L || !global.L.maplibreGL) throw new Error('binding missing');
      var ml = global.maplibregl;
      if (typeof ml.supported === 'function' && !ml.supported()) throw new Error('no WebGL');
      return ml;
    });
    // A failed load is retried on the next page, not on the next map.
    vectorReady.catch(function () {});
    return vectorReady;
  }

  // ---- the chain layer -----------------------------------------------------

  function chainLayer(L, opts) {
    var Chain = L.Layer.extend({
      initialize: function () {
        // A tile layer is what gives a Leaflet map its maxZoom, and
        // leaflet.markercluster throws "Map has no maxZoom specified" without
        // one. The inner layer arrives asynchronously, so the chain itself
        // carries the zoom limit, registered the way GridLayer registers it.
        this.options = { pane: 'tilePane', attribution: null,
                         maxZoom: opts.maxZoom || MAX_ZOOM, minZoom: 0 };
        this.provider = null;
        this._inner = null;
        this._timer = null;
      },
      beforeAdd: function (map) {
        if (map._addZoomLimit) map._addZoomLimit(this);
      },
      onAdd: function (map) {
        this._map = map;
        this._use(PROVIDER, null);
        return this;
      },
      onRemove: function (map) {
        this._clear();
        if (map && map._removeZoomLimit) map._removeZoomLimit(this);
      },
      getAttribution: function () { return null; },
      _clear: function () {
        if (this._timer) { clearTimeout(this._timer); this._timer = null; }
        if (this._inner && this._map && this._map.hasLayer(this._inner)) {
          this._map.removeLayer(this._inner);
        }
        this._inner = null;
      },
      _fallback: function (reason) {
        var from = this.provider;
        var to = nextProvider(from);
        this._clear();
        if (this._map) this._map.fire('basemapfallback', { from: from, to: to, reason: reason });
        this.fire('basemapfallback', { from: from, to: to, reason: reason });
        if (to) this._use(to, reason);
      },
      _mark: function (name) {
        this.provider = name;
        var el = this._map && this._map.getContainer && this._map.getContainer();
        if (el && el.setAttribute) el.setAttribute('data-basemap', name);
      },
      _use: function (name, why) {
        var self = this;
        var s = spec(name);
        self._mark(name);
        if (s.kind === 'vector') {
          var settled = false;
          var done = function (ok, reason) {
            if (settled) return;
            settled = true;
            if (self._timer) { clearTimeout(self._timer); self._timer = null; }
            if (!ok && self.provider === name) self._fallback(reason);
          };
          self._timer = setTimeout(function () { done(false, 'timeout'); }, LOAD_TIMEOUT_MS);
          loadVector().then(function () {
            if (!self._map || self.provider !== name || settled) return;
            var layer;
            try {
              // The binding reads its Leaflet attribution from
              // attributionControl.customAttribution when given, and otherwise
              // from the style's sources. Setting it keeps the credit line
              // fixed whatever the style carries.
              layer = L.maplibreGL({
                style: s.url,
                attributionControl: { customAttribution: s.attribution },
                interactive: false
              });
              layer.addTo(self._map);
            } catch (e) {
              done(false, 'webgl');
              return;
            }
            self._inner = layer;
            var gl = layer.getMaplibreMap && layer.getMaplibreMap();
            if (!gl) { done(false, 'webgl'); return; }
            gl.once('load', function () { done(true); });
            gl.on('error', function () { done(false, 'style'); });
          }, function (e) {
            done(false, /WebGL/.test(String(e && e.message)) ? 'webgl' : 'library');
          });
          return;
        }
        var tiles = raster(name, { L: L, maxZoom: opts.maxZoom || MAX_ZOOM,
                                   maxNativeZoom: s.maxZoom, opacity: opts.opacity,
                                   className: opts.className });
        var errors = 0, loaded = false;
        tiles.on('tileload', function () { loaded = true; });
        tiles.on('tileerror', function (ev) {
          self.fire('tileerror', ev);
          errors++;
          if (!loaded && errors >= RASTER_ERROR_LIMIT && self.provider === name) {
            self._fallback('tiles');
          }
        });
        self._inner = tiles;
        tiles.addTo(self._map);
      }
    });
    return new Chain();
  }

  // The basemap for a page. With no provider, or with the vector one, it is
  // the fallback chain; naming a raster provider pins that provider and
  // returns a plain tile layer, as this function always did.
  function dark(opts) {
    opts = opts || {};
    var L = opts.L || global.L;
    if (!L || !L.tileLayer) return null;
    var name = opts.provider && PROVIDERS[opts.provider] ? opts.provider : PROVIDER;
    if (spec(name).kind === 'raster') return raster(name, opts);
    if (!L.Layer || !L.Layer.extend) return raster(nextProvider(name), opts);
    return chainLayer(L, opts);
  }

  var api = {
    PROVIDER: PROVIDER,
    PROVIDERS: PROVIDERS,
    CHAIN: CHAIN,
    LOAD_TIMEOUT_MS: LOAD_TIMEOUT_MS,
    MAX_ZOOM: MAX_ZOOM,
    spec: spec,
    nextProvider: nextProvider,
    raster: raster,
    dark: dark
  };

  global.Basemap = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
