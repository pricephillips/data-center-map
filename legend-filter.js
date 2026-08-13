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
 * Registration
 *   2026-08-12  Initial registration. Contract, modes, and empty-set
 *               semantics as above. Selection is applied through the host
 *               page's existing filter predicate; the module owns no
 *               filtering logic of its own.
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

  global.LegendFilter = { attach: attach, fromHashValue: fromHashValue };
})(window);
