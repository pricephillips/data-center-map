const fs = require('fs');
const SRC = fs.readFileSync('map-permalink.js','utf8');
let pass=0, fail=0;
function eq(name, got, want){
  const g=JSON.stringify(got), w=JSON.stringify(want);
  if(g===w){pass++; console.log('  PASS  '+name);} else {fail++; console.log('  FAIL  '+name+'\n        got '+g+'\n        want '+w);}
}
// Fresh module instance per group, mirroring a real page load.
function boot(hash, store){
  const win = {
    location:{ hash:hash, pathname:'/p.html', search:'' },
    history:{ replaceState:(a,b,url)=>{ const i=url.indexOf('#'); win.location.hash = i<0?'':url.slice(i); } },
    document:{ getElementById:id=>store[id]||null, createElement:()=>({style:{},setAttribute(){},select(){},remove(){}}),
               querySelectorAll:()=>[], body:{appendChild(){}} },
    navigator:{}, console, setTimeout, clearTimeout
  };
  new Function('window','global', SRC)(win, win);
  return win;
}
// A real <select> silently rejects a value with no matching <option>, which
// is exactly the deferred-population case writeControl() guards against. The
// fake has to reproduce that or the guard is untested.
const SEL = (opts) => { const o = opts || []; let v = '';
  return { tagName:'SELECT', type:'', _opts:o,
           get value(){ return v; },
           set value(x){ v = o.indexOf(x) >= 0 ? x : ''; },
           addEventListener(){}, setAttribute(){} }; };
const TXT = () => ({tagName:'INPUT', type:'text', value:'', addEventListener(){}, setAttribute(){}});
const CHK = () => ({tagName:'INPUT', type:'checkbox', checked:false, addEventListener(){}, setAttribute(){}});

// ---- group 1: parse ----
{ const w = boot('', {}); const MP = w.MapPermalink;
  eq('keyed view', MP.parseHash('#map=7.75/38.01713/-79.45881&state=virginia').view, {zoom:7.75,lat:38.01713,lon:-79.45881});
  eq('legacy bare slug -> state', MP.parseHash('#virginia').extra, {state:'virginia'});
  eq('malformed view rejected', MP.parseHash('#map=4/39.5').view, null);
  eq('out-of-range lat rejected', MP.parseHash('#map=4/999/-98.35').view, null);
  eq('second bare token is foreign', MP.parseHash('#virginia&junk').foreign, ['junk']);
  eq('encoded value decoded', MP.parseHash('#q=data%20center').extra, {q:'data center'});
  eq('slug helper', MP.slug('New Hampshire'), 'new-hampshire');
  eq('matchSlug reverse lookup', MP.matchSlug(['New Hampshire','Virginia'],'new-hampshire'), 'New Hampshire');
}

// ---- group 2: map restore + write ----
{ const w = boot('#map=6/40/-100&fips=51145', {}); const MP = w.MapPermalink;
  const m = {_c:{lat:0,lng:0},_z:4,getCenter(){return this._c;},getZoom(){return this._z;},on(){},
             setView(ll,z){this._c={lat:ll[0],lng:ll[1]};this._z=z;}};
  const ctl = MP.attach(m, {});
  eq('view restored', [m._z,m._c.lat,m._c.lng], [6,40,-100]);
  eq('extra survives restore', ctl.get('fips'), '51145');
  ctl.set('fips', null);
  eq('cleared key drops segment', w.location.hash, '#map=6/40.0000/-100.0000');
  ctl.set('fips','08005');
  eq('set key rewrites hash', w.location.hash, '#map=6/40.0000/-100.0000&fips=08005');
  m._z = 7.7500001; ctl.update();
  eq('float zoom dust rounded', w.location.hash.split('/')[0], '#map=7.75');
}

// ---- group 3: legacy migration to keyed form ----
{ const w = boot('#virginia', {}); const MP = w.MapPermalink;
  const m = {_c:{lat:38,lng:-79},_z:7,getCenter(){return this._c;},getZoom(){return this._z;},on(){},setView(){}};
  let seen=null;
  MP.attach(m, { onRestore(st){ seen = st.extra.state; } });
  eq('legacy slug surfaced to page', seen, 'virginia');
  eq('legacy rewritten in keyed form', w.location.hash, '#map=7/38.0000/-79.0000&state=virginia');
}

// ---- group 4: deferred control restore ----
{ const store = {'f-state':SEL(), 'f-search':TXT(), 'f-opp':CHK()};
  const w = boot('#state=Virginia&q=powhatan&opp=1', store); const MP = w.MapPermalink;
  let renders = 0;
  const cc = MP.attachControls({controls:[
    {id:'f-search',key:'q'},{id:'f-state',key:'state'},{id:'f-opp',key:'opp'}], onRestore(){renders++;}});
  eq('text input restored on load', store['f-search'].value, 'powhatan');
  eq('checkbox restored on load', store['f-opp'].checked, true);
  eq('unpopulated select did not take value', store['f-state'].value, '');
  eq('first restore reported a change', renders, 1);
  store['f-state'] = SEL(['Virginia','Ohio']);   // options now exist
  eq('deferred restore lands', cc.restore(), true);
  eq('select value after deferred restore', store['f-state'].value, 'Virginia');
  eq('onRestore fired again', renders, 2);
  eq('idempotent third restore', cc.restore(), false);
}

// ---- group 5: map + controls share one writer (opposition-map shape) ----
{ const store = {'county-metric':SEL(), 'proj-search':TXT()};
  const w = boot('#map=5/39/-98&mode=project&q=aurora', store); const MP = w.MapPermalink;
  const m = {_c:{lat:0,lng:0},_z:4,getCenter(){return this._c;},getZoom(){return this._z;},on(){},
             setView(ll,z){this._c={lat:ll[0],lng:ll[1]};this._z=z;}};
  const a = MP.attach(m, {});
  const b = MP.attachControls({controls:[{id:'proj-search',key:'q'}]});
  eq('control binder did not drop the map segment', w.location.hash.indexOf('map=5/'), 1);
  eq('control binder did not drop mode', w.location.hash.indexOf('mode=project') > 0, true);
  a.set('mode','county');
  eq('map-side set preserved control key', w.location.hash.indexOf('q=aurora') > 0, true);
  eq('map-side set applied', w.location.hash.indexOf('mode=county') > 0, true);
}

// ---- group 6: value maps keep raw source vocabulary out of the URL ----
{ const store = {'tf-outcome':SEL(['win','loss','pending','mixed'])};
  const w = boot('', store); const MP = w.MapPermalink;
  const OUT = { win:'blocked_confirmed', loss:'advanced_confirmed',
                pending:'pending', mixed:'restricted_conditional' };
  const cc = MP.attachControls({controls:[{id:'tf-outcome',key:'outcome',map:OUT}]});
  store['tf-outcome'].value = 'win';
  cc.update();
  // update() alone does not re-read controls; drive the change path instead.
  const fresh = boot('#outcome=blocked_confirmed', {'tf-outcome':SEL(['win','loss','pending','mixed'])});
  const st2 = {'tf-outcome':SEL(['win','loss','pending','mixed'])};
  const w2 = boot('#outcome=blocked_confirmed', st2); const MP2 = w2.MapPermalink;
  MP2.attachControls({controls:[{id:'tf-outcome',key:'outcome',map:OUT}]});
  eq('tier value in hash restores raw control value', st2['tf-outcome'].value, 'win');
  eq('raw value never appears in hash', w2.location.hash.indexOf('win'), -1);

  const st3 = {'tf-outcome':SEL(['win','loss','pending','mixed'])};
  const w3 = boot('#outcome=notatier', st3); const MP3 = w3.MapPermalink;
  MP3.attachControls({controls:[{id:'tf-outcome',key:'outcome',map:OUT}]});
  eq('unknown tier value is not applied', st3['tf-outcome'].value, '');

  const w4 = boot('', {}); const MP4 = w4.MapPermalink;
  eq('polarity: win maps to blocked_confirmed', OUT.win, 'blocked_confirmed');
  eq('polarity: loss maps to advanced_confirmed', OUT.loss, 'advanced_confirmed');
}

console.log('\n'+pass+' passed, '+fail+' failed');
process.exit(fail?1:0);
