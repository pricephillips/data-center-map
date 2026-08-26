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

const layer = B.dark();
ok('a layer is built from the default provider',
   layer && layer.url === B.PROVIDERS[B.PROVIDER].url);
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

const capped = B.dark({ maxZoom: 9 });
ok('caller can cap maxZoom', capped.cfg.maxZoom === 9);
const styled = B.dark({ opacity: 0.5, className: 'muted' });
ok('caller can pass opacity and className',
   styled.cfg.opacity === 0.5 && styled.cfg.className === 'muted');

ok('an unknown provider falls back to the default rather than throwing',
   B.dark({ provider: 'nope' }).url === B.PROVIDERS[B.PROVIDER].url);

// A page loaded without Leaflet must degrade, not throw: the basemap is
// context and the data is drawn separately.
const noLeaflet = boot(undefined);
ok('missing Leaflet returns null instead of throwing',
   noLeaflet.dark({ L: undefined }) === null);

ok('every provider declares url, attribution and maxZoom',
   Object.values(B.PROVIDERS).every(p => p.url && p.attribution && p.maxZoom));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
