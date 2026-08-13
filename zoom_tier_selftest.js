// Selftest for the auto-mode crossfade in opposition-map.html. Extracts the
// weighting function from the page and exercises it directly, so the zoom
// thresholds are verified rather than eyeballed. Plain node, no browser.
// thresholds are verified rather than eyeballed.
const fs=require('fs');
const s=fs.readFileSync(require('path').join(__dirname,'opposition-map.html'),'utf8');
const block=/const Z_COUNTY = 6;[\s\S]*?^}/m.exec(s)[0];
const f=new Function(block+'; return {tierWeights, Z_COUNTY, Z_PINS, COUNTY_CONTEXT_OPACITY};')();
const {tierWeights:tw, Z_COUNTY:ZC, Z_PINS:ZP, COUNTY_CONTEXT_OPACITY:CTX}=f;
let pass=0,fail=0;
const eq=(n,g,w)=>{const a=JSON.stringify(g),b=JSON.stringify(w);
  if(a===b){pass++;console.log('  PASS  '+n);}else{fail++;console.log('  FAIL  '+n+'\n        got '+a+' want '+b);}};
const near=(n,g,w)=>eq(n, Math.round(g*1000)/1000, Math.round(w*1000)/1000);

eq('national zoom: counties only',      tw(4),  {county:1, pin:0});
eq('at county threshold: still county', tw(ZC), {county:1, pin:0});
near('midpoint pin weight',             tw(7).pin, 0.5);
near('midpoint county weight',          tw(7).county, 1-(1-CTX)*0.5);
eq('at pin threshold: pins dominant',   tw(ZP), {county:CTX, pin:1});
eq('deep zoom: unchanged past threshold', tw(14), {county:CTX, pin:1});

// Monotonicity: pins must never dip while zooming in, counties never rise.
let ok=true, prevP=-1, prevC=99;
for(let z=3; z<=14; z+=0.25){const w=tw(z); if(w.pin<prevP-1e-9||w.county>prevC+1e-9) ok=false; prevP=w.pin; prevC=w.county;}
eq('monotonic across the whole range', ok, true);

// Counties never vanish entirely, so the geographic frame is always present.
let floorOk=true; for(let z=3;z<=18;z+=0.5) if(tw(z).county < CTX-1e-9) floorOk=false;
eq('county context never fully disappears', floorOk, true);

// No zoom leaves both panes below the pointer-events floor.
let gap=false; for(let z=3;z<=18;z+=0.25){const w=tw(z); if(w.county<0.15&&w.pin<0.15) gap=true;}
eq('no zoom where both layers are inert', gap, false);

// ---- export gate ----
// The gate must be closed wherever pins are the dominant layer, because they
// have no canvas representation and would be silently missing from the image.
const html2=fs.readFileSync(require('path').join(__dirname,'opposition-map.html'),'utf8');
const gateBlock=/const EXPORT_MIN_COUNTY_WEIGHT = [0-9.]+;[\s\S]*?^}/m.exec(html2)[0];
const gateFn=new Function(gateBlock+'; return exportAllowed;')();
const gate = z => gateFn(tw(z));
eq('export enabled at national zoom', gate(4), true);
eq('export enabled at county threshold', gate(ZC), true);
eq('export disabled once pins dominate', gate(ZP), false);
eq('export disabled deep in', gate(14), false);
// The gate must never reopen as you keep zooming in.
let reopened=false, wasOpen=true;
for(let z=3;z<=18;z+=0.25){const g=gate(z); if(!wasOpen&&g) reopened=true; wasOpen=g;}
eq('gate never reopens while zooming in', reopened, false);
// Whenever the gate is open, counties must actually be the readable layer.
let sane=true; for(let z=3;z<=18;z+=0.25) if(gate(z) && tw(z).pin > tw(z).county) sane=false;
eq('gate open implies counties read over pins', sane, true);

console.log('\n'+pass+' passed, '+fail+' failed');
process.exit(fail?1:0);
