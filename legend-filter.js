/* legend-filter.js
 *
 * Turns a static map legend into a filter control. Clicking a legend row
 * narrows the map to that class; clicking it again clears. One canonical copy,
 * imported by script tag the same way viz-palette.js and map-permalink.js are,
 * so the three map pages behave identically.
 *
 * Why this exists: a legend already tells the reader what the classes are, so
 * it is where they look when they want only one of them. Sending them to a
 * separate dropdown to act on what the legend just told them is a detour. This
 * removes it.
 *
 * The module never filters anything itself. It manages selection state,
 * styling, keyboard access, and the URL key, then calls back with the active
 * set. Each page applies that set through its own existing predicate, so there
 * is exactly one filter path per page rather than a parallel one that can
 * disagree with the controls.
 *
 * Markup contract
 *   Rows opt in with data-lf on the row element:
 *     <div class="legend-row" data-lf="pending">...</div>
 *   Rows without the attribute are left alone, so a legend can mix filterable
 *   classes with explanatory notes and density keys.
 *
 * Modes
 *   'single'  one active class at a time; selecting another replaces it.
 *             Use where the page's own control is a single-select, so the two
 *             cannot express different things.
 *   'multi'   any subset active. An empty set means no filter, not an empty
 *             map, because a legend with nothing lit should show everything.
 *
 * Permalink
 *   Pass a MapPermalink controller and a key and the active set is written to
 *   the hash, so a filtered legend is linkable. A value map can be supplied
 *   for the same reason it exists in map-permalink.js: the class identifiers
 *   on opposition-tracker come from the raw source of record and are not
 *   publishable vocabulary.
 *
 * Year range (spec 009, US4)
 *   yearOf(value) and inYearRange(value, lo, hi) are the date predicate for the
 *   in-house range slider on opposition-map. They live here, beside the class
 *   filter, so a page's pin predicate reads its date bounds from the same
 *   module as its legend state. Pure functions; no DOM.
 *     yearOf('2025-03-14') -> 2025; yearOf('2026-1') -> 2026; yearOf('') -> null
 *     inYearRange(v, lo, hi): lo and hi are inclusive years, null = open.
 *     A value with no parsable year passes only when both ends are open, so
 *     narrowing the range never keeps an undated record it cannot place.
 *
 * Symbology legends and pin tooltips (spec 012, US4)
 *   entriesFromScale(scale), marginSymbology() with entriesFromSymbology()
 *   and renderRampLegend(), and outcomeEntries() build legend entries from
 *   the class breaks and colors the layer paints with (viz-palette.js), so a
 *   legend cannot disagree with its map. pinLabel() and bindPinTooltip() give
 *   every pin map the same hover label: name, place, outcome term. Outcome
 *   text is limited to the platform tiers; anything else is dropped.
 *
 * Registration
 *   2026-08-12  Initial registration. Contract, modes, and empty-set
 *               semantics as above. Selection is applied through the host
 *               page's existing filter predicate; the module owns no
 *               filtering logic of its own.
 *   2026-10-01  Added symbology legend builders and the shared pin tooltip.
 *               Additive; attach() and the year-range helpers are unchanged.
 */
(function (global) {
  'use strict';

  var CSS_ID = 'legend-filter-css';

  // Injected rather than added to three stylesheets, matching the pattern
  // viz-palette.js already uses for its legend styling.
  function injectCss() {
    if (global.document.getElementById(CSS_ID)) return;
    var s = global.document.createElement('style');
    s.id = CSS_ID;
    s.textContent = [
      '.lf-row{cursor:pointer;border-radius:5px;padding:1px 4px;margin-left:-4px;',
      '  transition:opacity .14s ease,background .14s ease;',
      '  outline-offset:2px}',
      '.lf-row:hover{background:rgba(255,255,255,.07)}',
      '.lf-row:focus-visible{outline:2px solid #7dd3fc}',
      '.lf-row[aria-pressed="true"]{background:rgba(255,255,255,.10)}',
      '.lf-dim{opacity:.34}',
      '.lf-dim:hover{opacity:.62}',
      '.lf-hint{font-size:10.5px;line-height:1.45;color:#8b97a6;margin-top:6px;',
      '  display:flex;align-items:center;gap:6px;flex-wrap:wrap}',
      '.lf-clear{cursor:pointer;color:#7dd3fc;text-decoration:underline;',
      '  background:none;border:0;padding:0;font:inherit}',
      '.lf-clear[hidden]{display:none}'
    ].join('\n');
    global.document.head.appendChild(s);
  }

  function rowsIn(container) {
    return Array.prototype.slice.call(container.querySelectorAll('[data-lf]'));
  }

  /* attach(container, opts) -> controller
   *
   *   opts.mode        'single' (default) or 'multi'
   *   opts.onChange    fn(activeKeysArray). Empty array means no filter.
   *   opts.hint        text shown under the rows. Defaults to a generic line.
   *   opts.permalink   { ctl, key, map } to mirror the active set into the URL
   *
   * Controller:
   *   .get()           current active keys
   *   .set(keys, opts) apply a set; opts.silent skips onChange
   */
  function attach(container, opts) {
    opts = opts || {};
    if (typeof container === 'string') {
      container = global.document.querySelector(container);
    }
    if (!container) return null;

    injectCss();

    var mode = opts.mode === 'multi' ? 'multi' : 'single';
    var rows = rowsIn(container);
    if (!rows.length) return null;

    var active = [];
    var pl = opts.permalink || null;

    var hint = global.document.createElement('div');
    hint.className = 'lf-hint';
    var hintText = global.document.createElement('span');
    hintText.textContent = opts.hint ||
      (mode === 'multi' ? 'Click to filter. Click again to clear.'
                        : 'Click a class to filter the map.');
    var clear = global.document.createElement('button');
    clear.type = 'button';
    clear.className = 'lf-clear';
    clear.textContent = 'Show all';
    clear.hidden = true;
    hint.appendChild(hintText);
    hint.appendChild(clear);
    container.appendChild(hint);

    function paint() {
      var filtering = active.length > 0;
      rows.forEach(function (r) {
        var on = active.indexOf(r.getAttribute('data-lf')) >= 0;
        r.setAttribute('aria-pressed', String(on));
        // Dim only while a filter is live. With nothing selected every class
        // is shown, so dimming everything would misreport the map state.
        if (filtering && !on) r.classList.add('lf-dim');
        else r.classList.remove('lf-dim');
      });
      clear.hidden = !filtering;
    }

    function toHash(keys) {
      if (!pl || !pl.ctl) return;
      if (!keys.length) { pl.ctl.set(pl.key, null); return; }
      var out = keys.map(function (k) {
        if (!pl.map) return k;
        return Object.prototype.hasOwnProperty.call(pl.map, k) ? pl.map[k] : null;
      });
      // A class with no published equivalent drops out rather than leaking its
      // raw identifier into the URL.
      out = out.filter(function (v) { return v !== null && v !== undefined; });
      pl.ctl.set(pl.key, out.length ? out.join(',') : null);
    }

    function commit(silent) {
      paint();
      toHash(active);
      if (!silent && typeof opts.onChange === 'function') {
        try { opts.onChange(active.slice()); }
        catch (e) {
          if (global.console && global.console.warn) {
            global.console.warn('LegendFilter onChange failed', e);
          }
        }
      }
    }

    function toggle(key) {
      var i = active.indexOf(key);
      if (mode === 'single') active = (i >= 0) ? [] : [key];
      else if (i >= 0) active.splice(i, 1);
      else active.push(key);
      commit(false);
    }

    rows.forEach(function (r) {
      r.classList.add('lf-row');
      r.setAttribute('role', 'button');
      r.setAttribute('tabindex', '0');
      r.setAttribute('aria-pressed', 'false');
      var key = r.getAttribute('data-lf');
      r.addEventListener('click', function () { toggle(key); });
      r.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
          ev.preventDefault();
          toggle(key);
        }
      });
    });

    clear.addEventListener('click', function () { active = []; commit(false); });

    paint();

    return {
      get: function () { return active.slice(); },
      set: function (keys, o) {
        var known = rows.map(function (r) { return r.getAttribute('data-lf'); });
        active = (keys || []).filter(function (k) { return known.indexOf(k) >= 0; });
        if (mode === 'single') active = active.slice(0, 1);
        commit(!!(o && o.silent));
      }
    };
  }

  /* Reverse a permalink value map, for restoring an active set from the hash. */
  function fromHashValue(v, map) {
    if (!v) return [];
    var parts = String(v).split(',');
    if (!map) return parts;
    var keys = Object.keys(map);
    var out = [];
    parts.forEach(function (p) {
      for (var i = 0; i < keys.length; i++) {
        if (map[keys[i]] === p) { out.push(keys[i]); return; }
      }
    });
    return out;
  }

  // Leading four-digit year of an ISO-ish date string ("2025", "2026-1",
  // "2025-03-14T..."), or null. Years outside 1900-2099 are not dates here.
  function yearOf(value) {
    var m = /^\s*((?:19|20)\d\d)(?:\D|$)/.exec(value == null ? '' : String(value));
    return m ? parseInt(m[1], 10) : null;
  }

  function inYearRange(value, lo, hi) {
    var open = (lo === null || lo === undefined) && (hi === null || hi === undefined);
    if (open) return true;
    var y = yearOf(value);
    if (y === null) return false;
    if (lo !== null && lo !== undefined && y < lo) return false;
    if (hi !== null && hi !== undefined && y > hi) return false;
    return true;
  }

  // ---------------------------------------------------------------------
  // Legends generated from symbology (spec 012, US4)
  // ---------------------------------------------------------------------
  // A hand-written legend is a second copy of the layer's class breaks and
  // colors, and a second copy drifts. These build legend entries from the
  // same objects the layer paints with, so the legend is the symbology read
  // back rather than a description of it. Colors come from viz-palette.js,
  // read at call time so a page that loads it later still gets them.

  function palette() { return global.VizPalette || null; }

  /* entriesFromScale(scale, opts) -> [{ value, label, color }]
   *   scale  a VizPalette.SequentialScale (or anything with ticks() and color())
   *   opts.ticks   number of intervals, as passed to scale.ticks(). Default 4.
   *   opts.format  fn(value) -> label. Default whole percent.
   * Each entry's value is a class break from scale.ticks() and its color is
   * scale.color() at that break, the call the layer's style() makes.
   */
  function entriesFromScale(scale, opts) {
    opts = opts || {};
    if (!scale || typeof scale.ticks !== 'function') return [];
    var fmt = opts.format || function (v) { return (v * 100).toFixed(0) + '%'; };
    return scale.ticks(opts.ticks || 4).map(function (v) {
      return { value: v, label: fmt(v), color: scale.color(v) };
    });
  }

  // margin_2024 symbology. Positive is a Democratic margin under the dataset
  // convention (naive re-derivation flips the sign). Colors saturate at a
  // 50-point margin, which is where both choropleth pages already saturated.
  var MARGIN_SPAN = 0.5;
  function marginLabel(v) {
    if (Math.abs(v) < 1e-9) return 'even';
    return (v < 0 ? 'R +' : 'D +') + Math.round(Math.abs(v) * 100);
  }
  /* marginSymbology() -> { breaks, color(m), label(m), span }
   *   The single definition the margin layer styles with and the margin
   *   legend is built from. color() returns null for a missing value so the
   *   caller can fall through to the no-data fill.
   */
  function marginSymbology() {
    var VP = palette();
    return {
      span: MARGIN_SPAN,
      breaks: [-MARGIN_SPAN, 0, MARGIN_SPAN],
      color: function (m) {
        if (m === null || m === undefined || m === '' || !isFinite(m) || !VP) return null;
        var t = Number(m) / MARGIN_SPAN;
        return VP.diverging(t < -1 ? -1 : (t > 1 ? 1 : t));
      },
      label: marginLabel
    };
  }
  /* entriesFromSymbology(sym) -> [{ value, label, color }] from sym.breaks. */
  function entriesFromSymbology(sym) {
    if (!sym || !sym.breaks) return [];
    return sym.breaks.map(function (v) {
      return { value: v, label: sym.label(v), color: sym.color(v) };
    });
  }
  /* rampCss(sym, steps) -> CSS gradient sampled from sym.color across its
   * breaks, so the legend ramp is the layer's own color function. */
  function rampCss(sym, steps) {
    steps = steps || 12;
    var lo = sym.breaks[0], hi = sym.breaks[sym.breaks.length - 1];
    var parts = [], i;
    for (i = 0; i <= steps; i++) {
      parts.push(sym.color(lo + (hi - lo) * (i / steps)) + ' ' +
                 Math.round((i / steps) * 100) + '%');
    }
    return 'linear-gradient(to right,' + parts.join(',') + ')';
  }
  /* renderRampLegend(el, sym, opts) -> entries
   *   Writes the viz-palette legend markup (vp-title, vp-ramp, vp-ticks,
   *   vp-note) from the symbology, and records the breaks on the element as
   *   data-legend-breaks so a test can compare them with the layer's.
   */
  function renderRampLegend(el, sym, opts) {
    opts = opts || {};
    var entries = entriesFromSymbology(sym);
    if (!el) return entries;
    var ticks = entries.map(function (e, ix) {
      var align = ix === 0 ? 'flex-start'
        : (ix === entries.length - 1 ? 'flex-end' : 'center');
      return '<span style="justify-content:' + align + '">' + escHtml(e.label) + '</span>';
    }).join('');
    el.innerHTML =
      '<div class="vp-title">' + escHtml(opts.title || '') + '</div>' +
      '<div class="vp-ramp" style="background:' + rampCss(sym) + '"></div>' +
      '<div class="vp-ticks">' + ticks + '</div>' +
      (opts.note ? '<div class="vp-note">' + escHtml(opts.note) + '</div>' : '');
    el.setAttribute('data-legend-breaks', JSON.stringify(entries.map(function (e) {
      return e.value;
    })));
    return entries;
  }

  // ---------------------------------------------------------------------
  // Outcome vocabulary and uniform pin tooltips (spec 012, US4)
  // ---------------------------------------------------------------------
  // The platform's outcome tiers, and the friendly labels already shown on
  // opposition-map. Nothing outside this set is ever rendered as an outcome.
  var OUTCOME_TERMS = ['advanced_confirmed', 'restricted_conditional',
    'blocked_confirmed', 'pending', 'blocked_unverified',
    'advanced_unverified', 'mixed'];
  var OUTCOME_TERM_LABEL = {
    advanced_confirmed: 'Advanced (confirmed)',
    restricted_conditional: 'Restricted (conditional)',
    blocked_confirmed: 'Blocked (confirmed)',
    pending: 'Pending / undecided',
    blocked_unverified: 'Blocked (unverified)',
    advanced_unverified: 'Advanced (unverified)',
    mixed: 'Mixed'
  };
  // Scorekeeping words and internal field names. A label containing any of
  // them is dropped, not rewritten, so a page passing a raw source value by
  // mistake shows no outcome rather than the wrong vocabulary.
  var REFUSED = /\b(win|wins|won|loss|losses|lost|decided|confirmed_blocks|blocked_share)\b/i;

  function escHtml(v) {
    return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* outcomeLabel(term, labels) -> friendly label, or '' for anything outside
   * the platform vocabulary. labels overrides the defaults per term. */
  function outcomeLabel(term, labels) {
    var k = String(term || '').trim().toLowerCase();
    if (OUTCOME_TERMS.indexOf(k) < 0) return '';
    var l = (labels && labels[k]) || OUTCOME_TERM_LABEL[k];
    return REFUSED.test(l) ? '' : l;
  }

  /* outcomeEntries(order, labels) -> [{ key, label, color }]
   *   Legend rows for outcome classes, colored from VizPalette.OUTCOME_COLOR,
   *   the table the pins are painted from. Keys outside the platform
   *   vocabulary are skipped.
   */
  function outcomeEntries(order, labels) {
    var VP = palette();
    var colors = (VP && VP.OUTCOME_COLOR) || {};
    return (order || OUTCOME_TERMS).filter(function (k) {
      return OUTCOME_TERMS.indexOf(k) >= 0;
    }).map(function (k) {
      return { key: k, label: outcomeLabel(k, labels), color: colors[k] || null };
    });
  }

  /* pinLabel(info) -> HTML for the hover tooltip on every pin map.
   *   info.name     project or facility name
   *   info.county   county text, as the source records it
   *   info.state    state name or abbreviation, appended after the county
   *   info.outcome  a platform outcome term, rendered through outcomeLabel()
   *   info.outcomeLabel  a page's existing friendly label, used in place of
   *                 the default when it passes the vocabulary screen
   * Three short lines: name, place, outcome. Empty parts are omitted. No other
   * field is read, so a composite or internal column cannot reach a tooltip.
   */
  function pinLabel(info) {
    info = info || {};
    var name = String(info.name || '').trim() || 'Unnamed';
    if (name.length > 60) name = name.slice(0, 57) + '...';
    var place = [info.county, info.state].map(function (v) {
      return String(v || '').trim();
    }).filter(Boolean).join(', ');
    var oc = '';
    if (info.outcomeLabel && !REFUSED.test(String(info.outcomeLabel))) {
      oc = String(info.outcomeLabel);
    } else if (info.outcome) {
      oc = outcomeLabel(info.outcome);
    }
    var out = '<b>' + escHtml(name) + '</b>';
    if (place) out += '<br>' + escHtml(place);
    if (oc) out += '<br><span class="lf-pin-oc">' + escHtml(oc) + '</span>';
    return out;
  }

  var PIN_CSS_ID = 'legend-filter-pin-css';
  function injectPinCss() {
    var d = global.document;
    if (!d || !d.getElementById || d.getElementById(PIN_CSS_ID)) return;
    var s = d.createElement('style');
    s.id = PIN_CSS_ID;
    s.textContent = [
      '.leaflet-tooltip.lf-pin-tip{background:#0f1722;color:#eef2f7;',
      '  border:1px solid #2c3544;border-radius:8px;font-size:12px;line-height:1.45;',
      '  box-shadow:0 6px 20px rgba(0,0,0,.35)}',
      '.leaflet-tooltip.lf-pin-tip b{color:#fff}',
      '.leaflet-tooltip.lf-pin-tip .lf-pin-oc{color:#c6cfda}'
    ].join('\n');
    d.head.appendChild(s);
  }

  /* bindPinTooltip(layer, info, opts) -> layer
   *   info is the object pinLabel() takes, or fn() returning one, evaluated
   *   on each hover so data joined after the pin was built still shows.
   *   opts.className is added beside the shared lf-pin-tip class. Hover only:
   *   the page's own click handler (detail panel, popup) is left untouched.
   */
  function bindPinTooltip(layer, info, opts) {
    if (!layer || typeof layer.bindTooltip !== 'function') return layer;
    opts = opts || {};
    injectPinCss();
    var content = typeof info === 'function'
      ? function () { return pinLabel(info()); }
      : pinLabel(info);
    layer.bindTooltip(content, {
      direction: 'top',
      className: 'lf-pin-tip' + (opts.className ? ' ' + opts.className : '')
    });
    return layer;
  }

  global.LegendFilter = { attach: attach, fromHashValue: fromHashValue,
                          yearOf: yearOf, inYearRange: inYearRange,
                          entriesFromScale: entriesFromScale,
                          marginSymbology: marginSymbology,
                          entriesFromSymbology: entriesFromSymbology,
                          renderRampLegend: renderRampLegend,
                          OUTCOME_TERMS: OUTCOME_TERMS,
                          outcomeLabel: outcomeLabel,
                          outcomeEntries: outcomeEntries,
                          pinLabel: pinLabel,
                          bindPinTooltip: bindPinTooltip };
})(window);
