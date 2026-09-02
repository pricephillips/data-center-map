// Selftest for the layer-mode logic in opposition-map.html. Extracts the pure
// functions from the page and exercises them directly, so the rules are
// verified rather than eyeballed. Plain node, no browser.
//
// This replaces zoom_tier_selftest.js, which tested an auto mode that
// crossfaded the county and pin layers by zoom. That mode was removed because
// it produced three defects, all measured in a browser:
//
//   * the pin pane sits above the county pane, and the crossfade changed only
//     opacity, never hit-testing, so at z7 (county 0.64, pin 0.50) the fainter
//     layer on top swallowed every county hover;
//   * the sidebar switched on `w.pin < 0.5`, false at exactly z7, so the UI
//     claimed "Project pins" at the one zoom where counties were dominant;
//   * export stayed enabled there, offering a county PNG in a mode the page
//     was calling project mode.
//
// The old file asserted the crossfade was smooth and monotonic, which it was.
// It could not assert the things that actually broke, because the invariants
// it needed -- exactly one interactive layer, and export tied to the mode
// rather than to a weight -- did not exist to be asserted. They do now, and
// this file asserts them.

const fs = require('fs');
const path = require('path');
const s = fs.readFileSync(path.join(__dirname, 'opposition-map.html'), 'utf8');

// Pull the block from the UNDERLAY_OPACITY constant through the end of
// layerState(), closing it by brace matching rather than by pattern. Anchoring
// the end on some line inside the function would mean that editing that line
// breaks the extraction instead of failing an assertion, which is the one way
// this test could report a green-to-red change as a harness error.
function extract() {
  const start = s.indexOf('const UNDERLAY_OPACITY');
  const fn = s.indexOf('function layerState(', start);
  if (start < 0 || fn < 0) return null;
  let i = s.indexOf('{', fn), depth = 0;
  if (i < 0) return null;
  for (; i < s.length; i++) {
    if (s[i] === '{') depth++;
    else if (s[i] === '}' && --depth === 0) return s.slice(start, i + 1);
  }
  return null;
}
const block = extract();
if (!block) {
  console.error('map_mode_selftest: could not find the layerState block in ' +
                'opposition-map.html. If the page was refactored, update this ' +
                'extraction rather than deleting the test.');
  process.exit(1);
}
const api = new Function(block + '; return {layerState, resolveMode, UNDERLAY_OPACITY};')();
const { layerState: ls, resolveMode: rm, UNDERLAY_OPACITY: CTX } = api;

let pass = 0, fail = 0;
const eq = (n, got, want) => {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a === b) { pass++; console.log('  PASS  ' + n); }
  else { fail++; console.log('  FAIL  ' + n + '\n        got ' + a + ' want ' + b); }
};

// ---- mode resolution -------------------------------------------------------
// Old permalinks carry mode=auto. A mode that no longer exists must land on
// the primary view, not on an undefined state.
eq('project resolves to project', rm('project'), 'project');
eq('county resolves to county', rm('county'), 'county');
eq('legacy auto resolves to county', rm('auto'), 'county');
eq('unknown value resolves to county', rm('banana'), 'county');
eq('undefined resolves to county', rm(undefined), 'county');
eq('empty string resolves to county', rm(''), 'county');
eq('null resolves to county', rm(null), 'county');

// ---- the two modes ---------------------------------------------------------
eq('county mode: counties opaque, pins gone',
   { c: ls('county', true).county, p: ls('county', true).pin }, { c: 1, p: 0 });
eq('county mode ignores the underlay checkbox',
   ls('county', false), ls('county', true));
eq('project mode with underlay: counties faint, pins full',
   { c: ls('project', true).county, p: ls('project', true).pin }, { c: CTX, p: 1 });
eq('project mode without underlay: counties gone, pins full',
   { c: ls('project', false).county, p: ls('project', false).pin }, { c: 0, p: 1 });
eq('the underlay is faint enough to read as ground', CTX > 0 && CTX <= 0.35, true);

// ---- the invariant the crossfade could not hold ----------------------------
// Exactly one layer is interactive, and it is the one the mode names. At no
// setting may both be hit-testable (the z7 bug) or neither (a dead map).
const settings = [['county', true], ['county', false], ['project', true],
                  ['project', false], ['auto', true], ['banana', false]];
let oneHit = true, hitMatchesMode = true, invisibleIsInert = true, ghost = false;
for (const [mode, under] of settings) {
  const w = ls(mode, under);
  if (w.countyHit === w.pinHit) oneHit = false;
  const countyMode = rm(mode) === 'county';
  if (w.countyHit !== countyMode || w.pinHit !== !countyMode) hitMatchesMode = false;
  // A pane at zero opacity must never accept pointer events: an invisible
  // layer that still captures hovers is exactly how the old bug felt.
  if ((w.county === 0 && w.countyHit) || (w.pin === 0 && w.pinHit)) invisibleIsInert = false;
  // The layer the reader is meant to be using must actually be visible.
  if ((w.countyHit && w.county <= 0) || (w.pinHit && w.pin <= 0)) ghost = true;
}
eq('exactly one layer is ever interactive', oneHit, true);
eq('the interactive layer is always the one the mode names', hitMatchesMode, true);
eq('an invisible layer never captures pointer events', invisibleIsInert, true);
eq('the interactive layer is always visible', ghost, false);

// ---- zoom is not an input --------------------------------------------------
// The whole class of bug came from layer visibility being a function of zoom.
// Assert the absence structurally: nothing in this block may consult the zoom.
eq('layerState takes no zoom parameter', /function layerState\(m, underlay\)/.test(block), true);
eq('the block never reads the map zoom', /getZoom|_zoom\b/.test(block), false);
// And behaviourally: the same mode gives the same answer every time.
eq('repeated calls are identical', JSON.stringify(ls('project', true)),
   JSON.stringify(ls('project', true)));

// The page must not have grown a zoom handler that moves layer opacity again.
eq('no zoomend handler touches layer visibility',
   /on\(['"]zoomend['"][^)]*applyLayerVisibility/.test(s), false);
eq('the removed crossfade is really gone',
   /tierWeights|Z_COUNTY|Z_PINS|applyZoomTier/.test(s), false);

// ---- export gate -----------------------------------------------------------
// Export renders the county canvas. Pins are DOM markers with no canvas
// representation, so the gate must be shut wherever pins are the subject.
eq('export enabled in county mode', ls('county', true).exportAllowed, true);
eq('export disabled in project mode', ls('project', true).exportAllowed, false);
eq('export disabled in project mode without underlay',
   ls('project', false).exportAllowed, false);
eq('legacy auto mode exports as county', ls('auto', true).exportAllowed, true);
let gateSane = true;
for (const [mode, under] of settings) {
  const w = ls(mode, under);
  // Whenever export is offered, counties must be the readable layer.
  if (w.exportAllowed && w.pin > w.county) gateSane = false;
  // And export must never be offered while pins are interactive.
  if (w.exportAllowed && w.pinHit) gateSane = false;
}
eq('export is only ever offered while counties read over pins', gateSane, true);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
