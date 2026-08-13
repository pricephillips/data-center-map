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

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
