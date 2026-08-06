/* ===========================================================================
   viz-palette.js
   Canonical color scales and legend rendering for every choropleth in the
   platform. Loaded with a plain <script src> before each page's inline script.

   WHY THIS FILE EXISTS
   -------------------
   The same ramp was hand-copied into restriction-model.html and
   opposition-map.html and had already drifted: the restriction page derived
   its saturation ceiling from the current score distribution while the map
   page still froze it at 0.35, so one calibrated score painted two different
   colors depending on which page you opened. That is the same defect class the
   Python layer fixed with entity_split.py, and it gets the same fix: one
   canonical component, imported rather than retyped.

   WHAT WAS WRONG WITH THE OLD RAMP
   --------------------------------
   1. DOUBLE ENCODING. Fill opacity moved with the value (0.35 + 0.5t) at the
      same time as hue. A mid-value county's apparent color therefore depended
      on what sat behind it, and low-value counties faded into the page
      background instead of reading as low. Varying alpha and hue together is
      the single largest source of the muddy, jarring look. Fill opacity is now
      constant and the value is carried by color alone.

   2. NOT PERCEPTUALLY UNIFORM. The old stops ran slate (30,41,59) to dark
      red-brown (124,45,18) to orange to amber. The first leg is a large hue
      rotation with almost no lightness change, and the last is a fast
      lightness climb. Equal steps in probability produced visibly unequal
      steps in perceived difference, which reads as banding in the middle and
      a washed-out low end. The ramps here are inferno and a balanced
      diverging pair, both designed so perceived difference tracks data
      difference.

   3. NO NUMBERS. The legend said "Low" and "High". A reader could not map a
      color back to a probability, which for a calibrated model is the whole
      point of publishing calibrated probabilities. Legends now carry numeric
      ticks and a distribution strip showing where counties actually fall.

   CHOICE OF RAMP
   --------------
   Inferno, not viridis or magma. It is perceptually uniform and colorblind
   safe like the others, and its middle and upper range (orange through pale
   yellow) is where the platform's existing accent colors already live, so this
   reads as the corrected version of the ramp the pages already had rather than
   a rebrand. Its low end is truncated at t=0.10 because raw inferno starts at
   near-black, which is indistinguishable from a dark page background: a county
   scored near zero must still read as a scored county.

   ACCESSIBILITY
   -------------
   Enacted-restriction counties are marked with BOTH a distinct stroke color
   and a dash pattern, never color alone. The stroke is cyan, chosen because it
   stays legible against every value on a warm sequential ramp; white vanishes
   against the pale yellow top end.
   =========================================================================== */

(function (global) {
  'use strict';

  // --- ramp anchors --------------------------------------------------------
  // Inferno sampled at ten equal steps. Interpolation between adjacent
  // anchors in sRGB is visually indistinguishable from the full 256-entry
  // table at this density and costs nothing to ship.
  var INFERNO = [
    [0, 0, 4], [27, 12, 65], [74, 12, 107], [120, 28, 109],
    [165, 44, 96], [207, 68, 70], [237, 105, 37], [251, 154, 6],
    [247, 209, 61], [252, 255, 164]
  ];

  // Raw inferno below this point is too dark to separate from the page
  // background, so the usable ramp starts here.
  var SEQ_FLOOR = 0.10;

  // Diverging pair for margin_2024. Blue and orange rather than blue and red:
  // both remain distinguishable under deuteranopia and protanopia, where red
  // and the warm end of a sequential ramp collapse together. The midpoint is a
  // dark neutral rather than the usual white, because on a dark page a white
  // midpoint glows and reads as the most important value on the map, which is
  // the opposite of what a midpoint means.
  var DIV_NEG = [249, 115, 22];   // orange, Republican lean under this dataset
  var DIV_POS = [56, 132, 255];   // blue, Democratic lean under this dataset
  var DIV_MID = [40, 46, 58];

  // One fill opacity for every scored polygon on every page.
  var FILL_OPACITY = 0.88;

  var ENACTED_STROKE = { color: '#67e8f9', weight: 1.2, dash: '3,2' };
  var BASE_STROKE = { color: '#2c3544', weight: 0.35 };
  var HOVER_STROKE = { color: '#f8fafc', weight: 2 };
  var NODATA = { fillColor: '#161c25', fillOpacity: 0.45,
                 color: '#232b38', weight: 0.35 };

  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }

  function lerpRgb(a, b, f) {
    return [Math.round(a[0] + (b[0] - a[0]) * f),
            Math.round(a[1] + (b[1] - a[1]) * f),
            Math.round(a[2] + (b[2] - a[2]) * f)];
  }

  function sampleTable(table, t) {
    var x = clamp01(t) * (table.length - 1);
    var i = Math.min(table.length - 2, Math.floor(x));
    return lerpRgb(table[i], table[i + 1], x - i);
  }

  function rgbCss(c) { return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')'; }

  /** Sequential color for a normalized position in [0,1]. */
  function sequential(t) {
    return rgbCss(sampleTable(INFERNO, SEQ_FLOOR + (1 - SEQ_FLOOR) * clamp01(t)));
  }

  /** Diverging color for a signed normalized position in [-1,1]. */
  function diverging(t) {
    var v = t < -1 ? -1 : (t > 1 ? 1 : t);
    return rgbCss(lerpRgb(DIV_MID, v >= 0 ? DIV_POS : DIV_NEG, Math.abs(v)));
  }

  // --- scale ---------------------------------------------------------------
  /**
   * Builds a sequential scale from the values actually loaded.
   *
   * The ceiling is the 99th percentile of the current data, floored at a
   * caller-supplied minimum. A frozen ceiling silently degrades every time a
   * retrain moves the base rate: when the county base rate went from 6.1 to
   * 10.4 percent, the old fixed 0.35 pinned 200 counties and 61 percent of all
   * enacted-restriction counties at maximum color, so the map stopped
   * separating inside the exact band anyone reads it for.
   *
   * Square-root positioning is kept. These distributions are heavily massed
   * near zero, and a linear ramp leaves nine tenths of the country in the
   * bottom fifth of the color range.
   */
  function SequentialScale(values, opts) {
    opts = opts || {};
    var clean = (values || [])
      .map(Number).filter(function (v) { return isFinite(v) && v >= 0; })
      .sort(function (a, b) { return a - b; });
    this.values = clean;
    this.floor = opts.floor === undefined ? 0.35 : opts.floor;
    var p = opts.percentile === undefined ? 0.99 : opts.percentile;
    var ceil = clean.length
      ? clean[Math.min(clean.length - 1, Math.floor(clean.length * p))]
      : this.floor;
    this.ceiling = Math.max(this.floor, ceil);
  }

  SequentialScale.prototype.position = function (v) {
    if (v === null || !isFinite(v)) return null;
    return clamp01(Math.sqrt(Math.max(0, v) / this.ceiling));
  };

  SequentialScale.prototype.color = function (v) {
    var t = this.position(v);
    return t === null ? null : sequential(t);
  };

  /** Style object for a Leaflet path. Constant fill opacity by design. */
  SequentialScale.prototype.style = function (v, flagged) {
    var c = this.color(v);
    if (c === null) {
      return { fillColor: NODATA.fillColor, fillOpacity: NODATA.fillOpacity,
               color: NODATA.color, weight: NODATA.weight, dashArray: null,
               opacity: 1 };
    }
    return {
      fillColor: c, fillOpacity: FILL_OPACITY,
      color: flagged ? ENACTED_STROKE.color : BASE_STROKE.color,
      weight: flagged ? ENACTED_STROKE.weight : BASE_STROKE.weight,
      dashArray: flagged ? ENACTED_STROKE.dash : null,
      opacity: 1
    };
  };

  /**
   * Tick values for the legend, in data units.
   *
   * Ticks are placed at even intervals of the SQRT position rather than of the
   * value, so their spacing on the rendered ramp is even and the compression
   * near zero is visible instead of hidden.
   */
  SequentialScale.prototype.ticks = function (n) {
    n = n || 5;
    var out = [], i;
    for (i = 0; i <= n; i++) {
      out.push(this.ceiling * Math.pow(i / n, 2));
    }
    return out;
  };

  /** CSS gradient matching the rendered ramp, sampled from the same function. */
  function gradientCss(steps) {
    steps = steps || 12;
    var parts = [], i;
    for (i = 0; i <= steps; i++) {
      parts.push(sequential(i / steps) + ' ' + Math.round((i / steps) * 100) + '%');
    }
    return 'linear-gradient(to right,' + parts.join(',') + ')';
  }

  function divergingGradientCss(steps) {
    steps = steps || 12;
    var parts = [], i, t;
    for (i = 0; i <= steps; i++) {
      t = -1 + 2 * (i / steps);
      parts.push(diverging(t) + ' ' + Math.round((i / steps) * 100) + '%');
    }
    return 'linear-gradient(to right,' + parts.join(',') + ')';
  }

  // --- legend --------------------------------------------------------------
  /**
   * Renders a ramp, numeric ticks, and a distribution strip into a container.
   *
   * The distribution strip is the part that earns its space. A choropleth
   * shows where values are; it does not show how many places hold each value.
   * With a base rate near ten percent, most of the country sits in the bottom
   * two ticks, and without the strip a reader looking at a map dominated by
   * dark counties cannot tell whether that means low scores everywhere or a
   * ramp mis-scaled to a handful of outliers.
   */
  function renderLegend(el, scale, opts) {
    if (!el) return;
    opts = opts || {};
    var fmt = opts.format || function (v) { return (v * 100).toFixed(0) + '%'; };
    var ticks = scale.ticks(opts.ticks || 4);
    var bins = opts.bins || 40;
    var counts = new Array(bins).fill(0), i, t, max = 0;

    for (i = 0; i < scale.values.length; i++) {
      t = scale.position(scale.values[i]);
      if (t === null) continue;
      counts[Math.min(bins - 1, Math.floor(t * bins))]++;
    }
    for (i = 0; i < bins; i++) { if (counts[i] > max) max = counts[i]; }

    var barsHtml = '';
    for (i = 0; i < bins; i++) {
      // sqrt on the bar height too: one bin holds most of the country and a
      // linear height would flatten every other bin to nothing.
      var h = max ? Math.max(counts[i] ? 1 : 0, Math.round(Math.sqrt(counts[i] / max) * 18)) : 0;
      barsHtml += '<i style="height:' + h + 'px;background:' + sequential(i / (bins - 1)) + '"></i>';
    }

    var ticksHtml = ticks.map(function (v, ix) {
      var align = ix === 0 ? 'flex-start' : (ix === ticks.length - 1 ? 'flex-end' : 'center');
      return '<span style="justify-content:' + align + '">' + fmt(v) + '</span>';
    }).join('');

    el.innerHTML =
      '<div class="vp-title">' + (opts.title || '') + '</div>' +
      '<div class="vp-dist">' + barsHtml + '</div>' +
      '<div class="vp-ramp" style="background:' + gradientCss() + '"></div>' +
      '<div class="vp-ticks">' + ticksHtml + '</div>' +
      (opts.note ? '<div class="vp-note">' + opts.note + '</div>' : '');
  }

  /** Style block for the legend, injected once so pages stay self-contained. */
  function injectCss() {
    if (document.getElementById('vp-style')) return;
    var css =
      '.vp-title{font-size:11px;color:#8a96a4;margin-bottom:5px}' +
      '.vp-dist{display:flex;align-items:flex-end;gap:1px;height:18px;margin-bottom:2px;opacity:.75}' +
      '.vp-dist i{flex:1;min-width:0;border-radius:1px 1px 0 0}' +
      '.vp-ramp{height:10px;border-radius:3px;border:1px solid rgba(255,255,255,.07)}' +
      '.vp-ticks{display:flex;margin-top:3px;font-size:10.5px;color:#8a96a4}' +
      '.vp-ticks span{flex:1;display:flex}' +
      '.vp-note{margin-top:6px;font-size:10.5px;color:#6b7684;line-height:1.5}';
    var s = document.createElement('style');
    s.id = 'vp-style';
    s.textContent = css;
    document.head.appendChild(s);
  }

  // --- selftest ------------------------------------------------------------
  // Run from the console: VizPalette.selftest(). Kept in the shipped file for
  // the same reason the Python modules keep theirs, since this file now owns
  // a rule (the derived ceiling) that a page can no longer be trusted to
  // reimplement correctly on its own.
  function selftest() {
    var out = [], ok = 0;
    function ck(name, cond) { out.push((cond ? 'PASS  ' : 'FAIL  ') + name); if (cond) ok++; }

    var s = new SequentialScale([0.01, 0.02, 0.05, 0.09, 0.2, 0.4, 0.9], { floor: 0.35 });
    ck('ceiling respects the floor', s.ceiling >= 0.35);
    ck('position is null for non-numbers', s.position(null) === null);
    ck('position is monotone', s.position(0.02) < s.position(0.2));
    ck('position saturates at the ceiling', s.position(s.ceiling * 4) === 1);
    ck('zero maps to the ramp floor, not to transparent', s.position(0) === 0);

    var lo = s.style(0.01, false), hi = s.style(0.9, false);
    ck('fill opacity is constant across the range',
       lo.fillOpacity === hi.fillOpacity && lo.fillOpacity === FILL_OPACITY);
    ck('colors differ across the range', lo.fillColor !== hi.fillColor);
    ck('flagged counties get a dash pattern, not color alone',
       s.style(0.2, true).dashArray === ENACTED_STROKE.dash);
    ck('unscored counties are a category, not a hole',
       s.style(null, false).fillOpacity > 0);

    var t = s.ticks(4);
    ck('ticks start at zero and end at the ceiling',
       t[0] === 0 && Math.abs(t[t.length - 1] - s.ceiling) < 1e-9);
    ck('ticks are compressed near zero', (t[1] - t[0]) < (t[4] - t[3]));

    ck('diverging midpoint is neutral, not white',
       diverging(0) === rgbCss(DIV_MID));
    ck('diverging is symmetric in magnitude',
       diverging(-1) !== diverging(1) && diverging(0.5) !== diverging(-0.5));
    ck('sequential is truncated off pure black',
       sequential(0) !== 'rgb(0,0,4)');
    ck('sequential ends bright', sequential(1) === rgbCss(INFERNO[INFERNO.length - 1]));

    var empty = new SequentialScale([], { floor: 0.35 });
    ck('empty input falls back to the floor', empty.ceiling === 0.35);

    out.forEach(function (l) { console.log('  ' + l); });
    console.log('\n' + ok + '/' + out.length + ' checks passed');
    return ok === out.length;
  }

  global.VizPalette = {
    SequentialScale: SequentialScale,
    sequential: sequential,
    diverging: diverging,
    gradientCss: gradientCss,
    divergingGradientCss: divergingGradientCss,
    renderLegend: renderLegend,
    injectCss: injectCss,
    FILL_OPACITY: FILL_OPACITY,
    ENACTED_STROKE: ENACTED_STROKE,
    BASE_STROKE: BASE_STROKE,
    HOVER_STROKE: HOVER_STROKE,
    NODATA: NODATA,
    selftest: selftest
  };
})(window);
