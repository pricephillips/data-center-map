const fs = require('fs');
const SRC = fs.readFileSync('facility-freshness.js', 'utf8');

let pass = 0, fail = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name); }
}
function eq(name, got, want) {
  if (got === want) { pass++; console.log('  PASS  ' + name); }
  else {
    fail++;
    console.log('  FAIL  ' + name + '\n        got  ' + JSON.stringify(got) +
                '\n        want ' + JSON.stringify(want));
  }
}

// Fresh module instance, mirroring a real page load.
function boot(doc) {
  const win = { document: doc || null, console, Promise };
  new Function('window', 'globalThis', 'module', SRC)(win, win, undefined);
  return win.FacilityFreshness;
}

const F = boot();

eq('thousands separator', F.fmtInt(1479), '1,479');
eq('small number unchanged', F.fmtInt(29), '29');
eq('missing count is not zero', F.fmtInt(null), 'unknown');

const undated = {
  label: 'IM3 Atlas', file: 'atlas.csv', present: true, rows: 1479, rows_us: 1479,
  repo_last_changed: '2026-08-20', days_since_repo_change: 6,
  vintage_status: 'undeclared', upstream_vintage: null,
  refresh: { cadence: 'unknown', pipeline: false }
};
const line = F.describe(undated);
ok('states the row count', line.indexOf('1,479 facilities') >= 0);
ok('says the age of the data is unknown',
   line.indexOf('age of the data is unknown') >= 0);
ok('reports the repository change date separately',
   line.indexOf('last changed in this repository 2026-08-20 (6 days ago)') >= 0);
ok('says there is no pipeline', line.indexOf('no automated refresh') >= 0);
ok('never calls the commit date a vintage',
   line.indexOf('upstream vintage 2026-08-20') < 0);

const dated = F.describe({
  label: 'Piped source', file: 'piped.csv', present: true, rows: 10, rows_us: 4,
  repo_last_changed: '2026-08-25', days_since_repo_change: 1,
  vintage_status: 'declared', upstream_vintage: '2026-07',
  refresh: { cadence: 'monthly', pipeline: true }
});
ok('declared vintage is shown', dated.indexOf('upstream vintage 2026-07') >= 0);
ok('US subset shown when it differs',
   dated.indexOf('10 facilities (4 in the United States)') >= 0);
ok('pipeline cadence is shown', dated.indexOf('refreshed monthly') >= 0);
ok('singular day', dated.indexOf('(1 day ago)') >= 0);

const planned = F.describe({
  label: 'Hyperscaler pages', file: null,
  acquisition: { status: 'needs_manual_pin' }
});
ok('a planned source reads as declared but unacquired',
   planned.indexOf('declared, not yet acquired') >= 0 &&
   planned.indexOf('needs manual pin') >= 0);
ok('planned sources are left out of a page provenance line by default',
   F.lines({ sources: [{ label: 'P', file: null }, undated] }).length === 1);
ok('and included when the caller asks',
   F.lines({ sources: [{ label: 'P', file: null }, undated] }, null, true).length === 2);

eq('absent file says so',
   F.describe({ label: 'Gone', file: 'gone.csv', present: false }),
   'Gone: file absent from this build.');

// Fallback chain: raw first, then the Pages origin.
ok('raw url comes first',
   F.MANIFEST_URLS[0].indexOf('raw.githubusercontent.com') >= 0);
ok('relative fallbacks follow',
   F.MANIFEST_URLS.slice(1).every(u => u.indexOf('http') !== 0));

// fetchFirst walks the chain and stops at the first success.
const tried = [];
function fakeFetch(map) {
  return function (url) {
    tried.push(url);
    if (map[url] === undefined) return Promise.reject(new Error('network'));
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(map[url]) });
  };
}
const manifest = { sources: [undated] };

let done = 0;
F.fetchFirst(['a', 'b', 'c'], fakeFetch({ b: manifest })).then(m => {
  eq('falls through to the first url that resolves', m, manifest);
  eq('stopped after the success', tried.join(','), 'a,b');
  done++;
}).then(() => F.fetchFirst(['x'], fakeFetch({})).then(
  () => { fail++; console.log('  FAIL  rejects when nothing resolves'); },
  () => { pass++; console.log('  PASS  rejects when nothing resolves'); }
)).then(() => {
  // render() writes through textContent, so a manifest value cannot be markup.
  const slots = [];
  const el = {
    textContent: '', innerHTML: '',
    querySelectorAll: () => slots
  };
  const doc = { getElementById: id => (id === 'target' ? el : null) };
  const G = boot(doc);
  const hostile = {
    sources: [{
      label: '<img src=x onerror=alert(1)>', file: 'h.csv', present: true, rows: 1,
      rows_us: 1, repo_last_changed: '2026-08-20', days_since_repo_change: 0,
      vintage_status: 'undeclared', refresh: {}
    }]
  };
  slots.push({ textContent: '' });
  return G.render('target', { urls: ['u'], fetchImpl: fakeFetch({ u: hostile }) })
    .then(() => {
      ok('manifest text is written as text, not markup',
         el.innerHTML.indexOf('onerror') < 0 &&
         slots[0].textContent.indexOf('onerror') >= 0);
    });
}).then(() => {
  const el = { textContent: '', innerHTML: '', querySelectorAll: () => [] };
  const doc = { getElementById: () => el };
  const G = boot(doc);
  return G.render('target', { urls: ['u'], fetchImpl: fakeFetch({}) }).then(() => {
    ok('a failed load says the counts are undated',
       el.textContent.indexOf('undated') >= 0);
  });
}).then(() => {
  const G = boot({ getElementById: () => null });
  return G.render('missing-element', { urls: ['u'], fetchImpl: fakeFetch({}) })
    .then(v => ok('missing element is a no-op', v === null));
}).then(() => {
  console.log('\n' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
});
