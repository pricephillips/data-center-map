const fs = require('fs');
const SRC = fs.readFileSync('basemap.js', 'utf8');
let pass = 0, fail = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name); }
}

function boot(L) {
  const win = { L: L, console };
  new Function('window', 'globalThis', 'module', SRC)(win, win, undefined);
  return win.Basemap;
}

// Minimal Leaflet stand-in that records what it was handed.
const calls = [];
const fakeL = { tileLayer: (url, cfg) => { calls.push({ url, cfg }); return { url, cfg }; } };
const B = boot(fakeL);

ok('default provider is keyless',
   B.PROVIDERS[B.PROVIDER].requiresKey !== true);
ok('the keyed provider is marked, not merely removed',
   B.PROVIDERS.carto_dark.requiresKey === true);

// ---- spec 009 US2: the chain -------------------------------------------
ok('default provider is the OpenFreeMap vector style',
   B.PROVIDER === 'openfreemap' && B.PROVIDERS.openfreemap.kind === 'vector');
ok('chain is openfreemap, esri_dark, osm',
   B.CHAIN.join(',') === 'openfreemap,esri_dark,osm');
ok('chain starts at the default provider', B.CHAIN[0] === B.PROVIDER);
ok('nextProvider walks the chain and ends at null',
   B.nextProvider('openfreemap') === 'esri_dark' &&
   B.nextProvider('esri_dark') === 'osm' &&
   B.nextProvider('osm') === null);
ok('every provider in the chain is keyless',
   B.CHAIN.every(n => B.PROVIDERS[n] && B.PROVIDERS[n].requiresKey !== true));
ok('every raster fallback in the chain is a raster provider',
   B.CHAIN.slice(1).every(n => B.PROVIDERS[n].kind === 'raster'));
ok('OpenFreeMap attribution credits OpenFreeMap, OpenMapTiles and OpenStreetMap',
   /OpenFreeMap/.test(B.PROVIDERS.openfreemap.attribution) &&
   /OpenMapTiles/.test(B.PROVIDERS.openfreemap.attribution) &&
   /OpenStreetMap/.test(B.PROVIDERS.openfreemap.attribution));
ok('the vector style is served over https from openfreemap.org',
   /^https:\/\/tiles\.openfreemap\.org\/styles\//.test(B.PROVIDERS.openfreemap.url));
ok('every provider declares kind, url, attribution and maxZoom',
   Object.values(B.PROVIDERS).every(p => p.kind && p.url && p.attribution && p.maxZoom));
ok('the load timeout is bounded', B.LOAD_TIMEOUT_MS > 0 && B.LOAD_TIMEOUT_MS <= 15000);

// ---- raster layers, as before -------------------------------------------
const layer = B.dark({ provider: 'esri_dark' });
ok('a pinned raster provider returns a plain tile layer',
   layer && layer.url === B.PROVIDERS.esri_dark.url);
ok('esri tile order is z/y/x, not z/x/y',
   B.PROVIDERS.esri_dark.url.indexOf('{z}/{y}/{x}') > 0);
ok('attribution is always set', !!layer.cfg.attribution);
ok('maxZoom comes from the provider',
   layer.cfg.maxZoom === B.PROVIDERS.esri_dark.maxZoom);
ok('no subdomains key when the provider has none',
   !('subdomains' in layer.cfg));

const carto = B.dark({ provider: 'carto_dark' });
ok('a subdomained provider passes its subdomains',
   Array.isArray(carto.cfg.subdomains) && carto.cfg.subdomains.length === 4);

const capped = B.dark({ provider: 'osm', maxZoom: 9 });
ok('caller can cap maxZoom', capped.cfg.maxZoom === 9);
const styled = B.dark({ provider: 'osm', opacity: 0.5, className: 'muted' });
ok('caller can pass opacity and className',
   styled.cfg.opacity === 0.5 && styled.cfg.className === 'muted');

// A Leaflet without L.Layer (the fake above) cannot host the chain; the
// basemap degrades to the first raster fallback rather than throwing.
ok('no L.Layer: the chain degrades to the first raster fallback',
   B.dark().url === B.PROVIDERS.esri_dark.url);
ok('an unknown provider resolves to the default chain rather than throwing',
   B.dark({ provider: 'nope' }).url === B.PROVIDERS.esri_dark.url);

// A page loaded without Leaflet must degrade, not throw: the basemap is
// context and the data is drawn separately.
const noLeaflet = boot(undefined);
ok('missing Leaflet returns null instead of throwing',
   noLeaflet.dark({ L: undefined }) === null);

// ---- the chain layer with a fuller Leaflet stand-in ----------------------
// Evented enough to drive onAdd and the raster fallback: maplibre is absent,
// so the vector attempt must fail over to esri_dark, and three tile errors
// with no tile loaded must fail over again to osm.
function evented() {
  const h = {};
  return {
    on(ev, fn) { (h[ev] = h[ev] || []).push(fn); return this; },
    once(ev, fn) { return this.on(ev, fn); },
    fire(ev, data) { (h[ev] || []).forEach(fn => fn(Object.assign({ type: ev }, data))); return this; }
  };
}
const added = [];
const fullL = {
  tileLayer(url, cfg) {
    const t = Object.assign(evented(), { url, cfg,
      addTo(m) { m._layers.push(t); added.push(url); return t; } });
    return t;
  },
  Layer: { extend(proto) {
    return function () {
      Object.assign(this, evented(), proto);
      this.initialize();
      this.addTo = m => { this.onAdd(m); return this; };
    };
  } }
};
const attrs = {};
const fakeMap = Object.assign(evented(), {
  _layers: [],
  hasLayer(l) { return this._layers.includes(l); },
  removeLayer(l) { this._layers = this._layers.filter(x => x !== l); },
  getContainer() { return { setAttribute(k, v) { attrs[k] = v; } }; }
});
const events = [];
fakeMap.on('basemapfallback', e => events.push(e.from + '>' + e.to + ':' + e.reason));
const winFull = { L: fullL, console };
new Function('window', 'globalThis', 'module', SRC)(winFull, winFull, undefined);
const chain = winFull.Basemap.dark();
chain.addTo(fakeMap);
ok('chain layer starts on the vector provider', chain.provider === 'openfreemap');

setTimeout(() => {
  ok('missing MapLibre falls back to esri_dark', chain.provider === 'esri_dark' &&
     events[0] === 'openfreemap>esri_dark:library');
  ok('the container records the active provider', attrs['data-basemap'] === 'esri_dark');
  const esri = fakeMap._layers[0];
  let forwarded = 0;
  chain.on('tileerror', () => forwarded++);
  esri.fire('tileerror'); esri.fire('tileerror');
  ok('two tile errors do not switch', chain.provider === 'esri_dark');
  esri.fire('tileerror');
  ok('three tile errors with none loaded switch to osm', chain.provider === 'osm' &&
     events[1] === 'esri_dark>osm:tiles');
  ok('tile errors are forwarded to page listeners', forwarded === 3);
  ok('the failed layer is removed from the map',
     fakeMap._layers.length === 1 && fakeMap._layers[0].url === B.PROVIDERS.osm.url);
  const osm = fakeMap._layers[0];
  osm.fire('tileload'); osm.fire('tileerror'); osm.fire('tileerror'); osm.fire('tileerror');
  ok('errors after a tile has loaded do not switch, and the chain ends at osm',
     chain.provider === 'osm' && events.length === 2);
  console.log('\n' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
}, 50);
