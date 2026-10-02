// Selftest for legend-filter.js. Runs under a DOM shim with plain node, no
// browser and no dependencies, so it can sit in CI beside permalink_selftest.
const fs = require('fs');
const SRC = fs.readFileSync('legend-filter.js', 'utf8');
let pass = 0, fail = 0;
function eq(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + '\n        got ' + g + '\n        want ' + w); }
}

function El(tag) {
  return { tagName: tag, _attrs: {}, _cls: [], _kids: [], _handlers: {}, hidden: false,
    textContent: '', type: '',
    classList: { add(c){ this._o._cls.includes(c)||this._o._cls.push(c); },
                 remove(c){ const i=this._o._cls.indexOf(c); if(i>=0) this._o._cls.splice(i,1); },
                 contains(c){ return this._o._cls.includes(c); } },
    setAttribute(k,v){ this._attrs[k]=String(v); },
    getAttribute(k){ return this._attrs[k]===undefined?null:this._attrs[k]; },
    appendChild(c){ this._kids.push(c); return c; },
    addEventListener(t,f){ (this._handlers[t]=this._handlers[t]||[]).push(f); },
    fire(t,ev){ (this._handlers[t]||[]).forEach(f=>f(ev||{preventDefault(){}})); },
    querySelectorAll(){ return []; } };
}
function mkRow(key){ const e=El('DIV'); e.classList._o=e; e._attrs['data-lf']=key; return e; }
function mkContainer(keys){
  const c = El('DIV'); c.classList._o = c;
  const rows = keys.map(mkRow);
  c.querySelectorAll = () => rows;
  c._rows = rows;
  return c;
}
function boot(){
  const head = El('HEAD'); head.classList._o = head;
  const win = { document: { getElementById: () => null, head,
    createElement: t => { const e = El(t); e.classList._o = e; return e; },
    querySelector: () => null }, console };
  new Function('window', SRC)(win);
  return win;
}

// ---- single mode ----
{ const w = boot(); const c = mkContainer(['pending','win','loss','mixed']);
  let seen = null;
  const lf = w.LegendFilter.attach(c, { mode:'single', onChange(a){ seen = a; } });
  eq('starts empty', lf.get(), []);
  c._rows[1].fire('click');
  eq('click selects one', lf.get(), ['win']);
  eq('onChange got the set', seen, ['win']);
  eq('selected row is pressed', c._rows[1].getAttribute('aria-pressed'), 'true');
  eq('unselected row dims', c._rows[0].classList.contains('lf-dim'), true);
  c._rows[2].fire('click');
  eq('single mode replaces', lf.get(), ['loss']);
  c._rows[2].fire('click');
  eq('clicking active clears', lf.get(), []);
  eq('nothing dims when cleared', c._rows[0].classList.contains('lf-dim'), false);
}

// ---- multi mode ----
{ const w = boot(); const c = mkContainer(['atlas','ai']);
  const lf = w.LegendFilter.attach(c, { mode:'multi' });
  c._rows[0].fire('click'); c._rows[1].fire('click');
  eq('multi accumulates', lf.get(), ['atlas','ai']);
  c._rows[0].fire('click');
  eq('multi removes', lf.get(), ['ai']);
}

// ---- keyboard ----
{ const w = boot(); const c = mkContainer(['a','b']);
  const lf = w.LegendFilter.attach(c, { mode:'multi' });
  c._rows[0].fire('keydown', { key:'Enter', preventDefault(){} });
  eq('Enter toggles', lf.get(), ['a']);
  c._rows[1].fire('keydown', { key:' ', preventDefault(){} });
  eq('Space toggles', lf.get(), ['a','b']);
  c._rows[0].fire('keydown', { key:'Tab', preventDefault(){} });
  eq('other keys ignored', lf.get(), ['a','b']);
  eq('rows are focusable', c._rows[0].getAttribute('tabindex'), '0');
  eq('rows announce as buttons', c._rows[0].getAttribute('role'), 'button');
}

// ---- set() and silent ----
{ const w = boot(); const c = mkContainer(['a','b']);
  let calls = 0;
  const lf = w.LegendFilter.attach(c, { mode:'multi', onChange(){ calls++; } });
  lf.set(['a']); eq('set applies', lf.get(), ['a']);
  eq('set fires onChange', calls, 1);
  lf.set(['b'], { silent:true });
  eq('silent set applies', lf.get(), ['b']);
  eq('silent set does not fire', calls, 1);
  lf.set(['a','zzz']);
  eq('unknown key rejected', lf.get(), ['a']);
}

// ---- permalink mirroring, including the value map ----
{ const w = boot(); const c = mkContainer(['win','loss']);
  const written = {};
  const ctl = { set(k,v){ written[k] = v; } };
  const MAP = { win:'blocked_confirmed', loss:'advanced_confirmed' };
  const lf = w.LegendFilter.attach(c, { mode:'multi',
    permalink:{ ctl, key:'outcome', map:MAP } });
  c._rows[0].fire('click');
  eq('hash gets the tier term', written.outcome, 'blocked_confirmed');
  eq('raw term never written', String(written.outcome).includes('win'), false);
  c._rows[1].fire('click');
  eq('multi joins tiers', written.outcome, 'blocked_confirmed,advanced_confirmed');
  lf.set([]);
  eq('cleared set nulls the key', written.outcome, null);
}

// ---- unmapped class drops rather than leaking ----
{ const w = boot(); const c = mkContainer(['win','secret']);
  const written = {};
  const ctl = { set(k,v){ written[k] = v; } };
  w.LegendFilter.attach(c, { mode:'multi',
    permalink:{ ctl, key:'outcome', map:{ win:'blocked_confirmed' } } });
  c._rows[1].fire('click');
  eq('unmapped class does not reach the hash', written.outcome, null);
}

// ---- fromHashValue round trip ----
{ const w = boot(); const L = w.LegendFilter;
  const MAP = { win:'blocked_confirmed', loss:'advanced_confirmed' };
  eq('reverses a single tier', L.fromHashValue('blocked_confirmed', MAP), ['win']);
  eq('reverses several', L.fromHashValue('blocked_confirmed,advanced_confirmed', MAP), ['win','loss']);
  eq('unknown tier dropped', L.fromHashValue('nope', MAP), []);
  eq('no map passes through', L.fromHashValue('1,2'), ['1','2']);
  eq('empty is empty', L.fromHashValue(''), []);
}

// ---- degenerate input ----
{ const w = boot();
  eq('missing container returns null', w.LegendFilter.attach(null, {}), null);
  const empty = mkContainer([]);
  eq('legend with no tagged rows returns null', w.LegendFilter.attach(empty, {}), null);
}

// ---- year range (spec 009, US4) ----
{ const LF = boot().LegendFilter;
  eq('yearOf full date', LF.yearOf('2025-03-14'), 2025);
  eq('yearOf year-month without padding', LF.yearOf('2026-1'), 2026);
  eq('yearOf bare year', LF.yearOf('2024'), 2024);
  eq('yearOf timestamp', LF.yearOf('2026-09-17T06:48:04.389Z'), 2026);
  eq('yearOf empty is null', LF.yearOf(''), null);
  eq('yearOf null is null', LF.yearOf(null), null);
  eq('yearOf non-date is null', LF.yearOf('unknown'), null);
  eq('yearOf five digits is null', LF.yearOf('20251'), null);
  eq('open range keeps undated', LF.inYearRange('', null, null), true);
  eq('bounded range drops undated', LF.inYearRange('', 2024, null), false);
  eq('lower bound inclusive', LF.inYearRange('2025-01', 2025, null), true);
  eq('below lower bound', LF.inYearRange('2024-12-31', 2025, null), false);
  eq('upper bound inclusive', LF.inYearRange('2025', null, 2025), true);
  eq('above upper bound', LF.inYearRange('2026-1', null, 2025), false);
  eq('inside both bounds', LF.inYearRange('2025-06', 2024, 2026), true);
}

// ---- legends from symbology (spec 012, US4) ----
// viz-palette.js loads into the same window first, as it does on the pages,
// so the entries are compared against the real scale and color functions.
function bootWithPalette(){
  const w = boot();
  new Function('window', fs.readFileSync('viz-palette.js', 'utf8'))(w);
  return w;
}
{ const w = bootWithPalette(); const LF = w.LegendFilter, VP = w.VizPalette;
  const scale = new VP.SequentialScale([0.01, 0.02, 0.05, 0.09, 0.2, 0.4, 0.9], { floor: 0.35 });
  const ent = LF.entriesFromScale(scale, { ticks: 4 });
  eq('scale entries are the layer class breaks', ent.map(e => e.value), scale.ticks(4));
  eq('scale entry colors are the layer colors', ent.map(e => e.color), scale.ticks(4).map(v => scale.color(v)));
  eq('scale entry colors match the painted style', ent.map(e => e.color),
     scale.ticks(4).map(v => scale.style(v, false).fillColor));
  eq('scale entry labels default to percent', ent[ent.length - 1].label,
     (scale.ceiling * 100).toFixed(0) + '%');
  const cnt = new VP.SequentialScale([1, 1, 2, 3, 5, 14], { floor: 1, minPosition: VP.MIN_POSITION_SEPARABLE });
  const ce = LF.entriesFromScale(cnt, { ticks: 4, format: v => String(Math.round(v)) });
  eq('floored scale entries carry the floored colors', ce.map(e => e.color), cnt.ticks(4).map(v => cnt.color(v)));
  eq('no scale, no entries', LF.entriesFromScale(null), []);

  const sym = LF.marginSymbology();
  const me = LF.entriesFromSymbology(sym);
  eq('margin entries are the margin breaks', me.map(e => e.value), sym.breaks);
  eq('margin entry colors are the layer colors', me.map(e => e.color), sym.breaks.map(v => sym.color(v)));
  eq('margin colors come from VizPalette.diverging', me.map(e => e.color),
     [VP.diverging(-1), VP.diverging(0), VP.diverging(1)]);
  eq('margin labels keep the page vocabulary', me.map(e => e.label), ['R +50', 'even', 'D +50']);
  eq('margin saturates past the span', sym.color(-0.9), VP.diverging(-1));
  eq('positive margin is the Democratic end', sym.color(0.5), VP.diverging(1));
  eq('missing margin is no data', [sym.color(null), sym.color(NaN), sym.color('')], [null, null, null]);

  const host = { _attrs: {}, innerHTML: '', setAttribute(k, v){ this._attrs[k] = String(v); } };
  const re = LF.renderRampLegend(host, sym, { title: '2024 presidential margin', note: 'Positive is a Democratic margin.' });
  eq('rendered legend records the layer breaks', JSON.parse(host._attrs['data-legend-breaks']), sym.breaks);
  eq('rendered legend returns its entries', re.map(e => e.color), me.map(e => e.color));
  eq('rendered ramp is sampled from the layer color', host.innerHTML.indexOf(sym.color(-0.5)) > 0
     && host.innerHTML.indexOf(sym.color(0.5)) > 0, true);
  eq('rendered ticks carry the labels', ['R +50', 'even', 'D +50'].every(t => host.innerHTML.includes(t)), true);

  const oe = LF.outcomeEntries(['blocked_confirmed', 'advanced_confirmed', 'pending']);
  eq('outcome entries keep order', oe.map(e => e.key), ['blocked_confirmed', 'advanced_confirmed', 'pending']);
  eq('outcome entry colors are VizPalette.OUTCOME_COLOR', oe.map(e => e.color),
     ['blocked_confirmed', 'advanced_confirmed', 'pending'].map(k => VP.OUTCOME_COLOR[k]));
  eq('every platform tier has a color', LF.OUTCOME_TERMS.every(k => !!VP.OUTCOME_COLOR[k]), true);
  eq('non-tier keys are skipped', LF.outcomeEntries(['win', 'pending']).map(e => e.key), ['pending']);
  eq('page labels override defaults', LF.outcomeEntries(['pending'], { pending: 'pending / undecided' })[0].label,
     'pending / undecided');
}

// ---- uniform pin tooltip (spec 012, US4) ----
{ const LF = boot().LegendFilter;
  eq('label has name, place, outcome',
     LF.pinLabel({ name: 'Project A', county: 'Loudoun County', state: 'VA', outcome: 'blocked_confirmed' }),
     '<b>Project A</b><br>Loudoun County, VA<br><span class="lf-pin-oc">Blocked (confirmed)</span>');
  eq('empty parts omitted', LF.pinLabel({ name: 'P' }), '<b>P</b>');
  eq('name is escaped', LF.pinLabel({ name: '<x>&' }), '<b>&lt;x&gt;&amp;</b>');
  eq('long name is shortened', LF.pinLabel({ name: 'x'.repeat(80) }).length < 80, true);
  eq('every tier renders a label', LF.OUTCOME_TERMS.every(t => LF.outcomeLabel(t) !== ''), true);
  eq('unknown outcome is dropped', LF.pinLabel({ name: 'P', outcome: 'win' }), '<b>P</b>');
  eq('page friendly label is used', LF.pinLabel({ name: 'P', outcome: 'pending', outcomeLabel: 'Contested' }),
     '<b>P</b><br><span class="lf-pin-oc">Contested</span>');
  const bad = ['win', 'Loss', 'won', 'lost', 'decided', 'confirmed_blocks', 'blocked_share'];
  eq('scorekeeping labels never render',
     bad.map(b => LF.pinLabel({ name: 'P', outcomeLabel: b })), bad.map(() => '<b>P</b>'));
  eq('refused label falls back to the tier term',
     LF.pinLabel({ name: 'P', outcome: 'pending', outcomeLabel: 'won' }),
     '<b>P</b><br><span class="lf-pin-oc">Pending / undecided</span>');
  eq('undecided is not refused', LF.outcomeLabel('pending'), 'Pending / undecided');
  const extra = LF.pinLabel({ name: 'P', screen_tier: 'A', blocked_share: 0.4, decided: 3 });
  eq('fields outside name, place, outcome never render', extra, '<b>P</b>');

  let bound = null;
  const layer = { bindTooltip(c, o){ bound = { c, o }; return this; } };
  LF.bindPinTooltip(layer, { name: 'P', outcome: 'mixed' }, { className: 'pin-tooltip' });
  eq('bind uses the shared label', bound.c, '<b>P</b><br><span class="lf-pin-oc">Mixed</span>');
  eq('bind adds the shared class beside the page class', bound.o.className, 'lf-pin-tip pin-tooltip');
  eq('bind hovers above the pin', bound.o.direction, 'top');
  let late = { name: 'Q' };
  LF.bindPinTooltip(layer, () => late);
  late = { name: 'Q', outcome: 'advanced_confirmed' };
  eq('a function source is read on hover', bound.c(), '<b>Q</b><br><span class="lf-pin-oc">Advanced (confirmed)</span>');
  eq('a layer without bindTooltip is returned untouched', LF.bindPinTooltip({}, {}), {});
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
