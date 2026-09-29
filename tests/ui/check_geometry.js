#!/usr/bin/env node
/* check_geometry.js
 *
 * Coverage gate for data/geo/counties_2024.topojson (spec 009, US1).
 *
 * The plotly county GeoJSON the maps used to draw on was missing 13 of the
 * 3,144 scored FIPS, and nothing noticed, because a county with a score and
 * no polygon simply never paints. This check makes that a failure instead:
 *
 *   - every fips in data/county_policy_scores.csv has a non-null geometry;
 *   - no geometry id appears twice;
 *   - the file is at most 1,000,000 bytes (SC-002).
 *
 * It runs in the boundaries job of acquire-geo-sources.yml before anything is
 * committed, and in the UI tests. No dependencies beyond Node.
 *
 * Usage
 *   node tests/ui/check_geometry.js                 check, exit 1 on failure
 *   node tests/ui/check_geometry.js --manifest \
 *     --source-url URL --source-note TEXT --vintage 2024 --scale 1:5m [--state seed]
 *                                                   check, then write the manifest
 *   node tests/ui/check_geometry.js --selftest
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = path.resolve(__dirname, '..', '..');
const TOPO = 'data/geo/counties_2024.topojson';
const MANIFEST = 'data/geo/counties_2024_manifest.json';
const SCORES = 'data/county_policy_scores.csv';
const MAX_BYTES = 1000000;

function scoredFips(csvText) {
  const lines = csvText.replace(/\r/g, '').trim().split('\n');
  const col = lines[0].split(',').indexOf('fips');
  if (col < 0) throw new Error('scores file has no fips column');
  const out = new Set();
  for (let i = 1; i < lines.length; i++) {
    const v = (lines[i].split(',')[col] || '').trim();
    if (v) out.add(v.padStart(5, '0'));
  }
  return out;
}

function check(topo, bytes, scored) {
  const errors = [];
  const obj = topo && topo.objects && topo.objects.counties;
  if (!obj || !Array.isArray(obj.geometries)) {
    return { errors: ['no counties object'], featureCount: 0, missing: [...scored] };
  }
  const seen = new Set();
  const drawable = new Set();
  obj.geometries.forEach(g => {
    const id = String(g.id == null ? '' : g.id).padStart(5, '0');
    if (seen.has(id)) errors.push('duplicate id ' + id);
    seen.add(id);
    if (g.type && g.type !== 'null' && Array.isArray(g.arcs) && g.arcs.length) drawable.add(id);
  });
  const missing = [...scored].filter(f => !drawable.has(f)).sort();
  if (missing.length) errors.push(missing.length + ' scored FIPS without a geometry: ' + missing.join(' '));
  if (bytes > MAX_BYTES) errors.push('file is ' + bytes + ' bytes, over ' + MAX_BYTES);
  return { errors, featureCount: obj.geometries.length, missing };
}

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith('--')) continue;
    const k = argv[i].slice(2);
    const next = argv[i + 1];
    out[k] = next && !next.startsWith('--') ? (i++, next) : true;
  }
  return out;
}

function main(argv) {
  const a = args(argv);
  const buf = fs.readFileSync(path.join(ROOT, TOPO));
  const topo = JSON.parse(buf.toString('utf8'));
  const scored = scoredFips(fs.readFileSync(path.join(ROOT, SCORES), 'utf8'));
  const r = check(topo, buf.length, scored);
  const covered = scored.size - r.missing.length;
  console.log(`${TOPO}: ${r.featureCount} features, ${buf.length} bytes, ` +
              `${covered} of ${scored.size} scored FIPS drawable`);
  if (r.errors.length) {
    r.errors.forEach(e => console.error('FAIL: ' + e));
    return 1;
  }
  if (a.manifest) {
    const manifest = {
      file: TOPO,
      source_url: a['source-url'] || '',
      source_note: a['source-note'] || '',
      vintage: Number(a.vintage) || null,
      scale: a.scale || '',
      sha256: crypto.createHash('sha256').update(buf).digest('hex'),
      bytes: buf.length,
      feature_count: r.featureCount,
      scored_fips: scored.size,
      scored_missing: r.missing,
      built_at: new Date().toISOString().slice(0, 10),
      writer: 'acquire-geo-sources.yml:boundaries',
      state: a.state || 'built'
    };
    fs.writeFileSync(path.join(ROOT, MANIFEST), JSON.stringify(manifest, null, 2) + '\n');
    console.log('wrote ' + MANIFEST);
  }
  return 0;
}

function selftest() {
  let fails = 0;
  const ok = (name, cond) => { if (!cond) { fails++; console.error('FAIL ' + name); } else console.log('ok   ' + name); };
  const topo = { objects: { counties: { geometries: [
    { id: '09110', type: 'Polygon', arcs: [[0]] },
    { id: '46102', type: 'Polygon', arcs: [[1]] },
    { id: '51610', type: null }
  ] } } };
  ok('scores parse pads fips', scoredFips('fips,x\n9110,1\r\n46102,2\n').has('09110'));
  ok('full coverage passes', check(topo, 10, new Set(['09110', '46102'])).errors.length === 0);
  ok('null geometry counts as missing', check(topo, 10, new Set(['51610'])).missing[0] === '51610');
  ok('absent fips fails', check(topo, 10, new Set(['02063'])).errors.length === 1);
  const dup = { objects: { counties: { geometries: [
    { id: '01001', type: 'Polygon', arcs: [[0]] }, { id: '01001', type: 'Polygon', arcs: [[0]] }] } } };
  ok('duplicate id fails', check(dup, 10, new Set()).errors.some(e => e.startsWith('duplicate')));
  ok('oversize fails', check(topo, MAX_BYTES + 1, new Set()).errors.length === 1);
  ok('missing object fails', check({ objects: {} }, 10, new Set()).errors.length === 1);
  console.log(fails ? fails + ' failed' : 'all passed');
  return fails ? 1 : 0;
}

if (require.main === module) {
  process.exit(process.argv.includes('--selftest') ? selftest() : main(process.argv.slice(2)));
}
module.exports = { check, scoredFips, MAX_BYTES };
