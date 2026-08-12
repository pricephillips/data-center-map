/* map-permalink.js
 *
 * Shared view- and filter-state serializer for the pages in this repo. Writes
 * map center and zoom, plus any page-supplied selection or filter keys, into
 * location.hash, and restores them on load. One canonical copy, imported by
 * script tag the same way viz-palette.js is, so the pages cannot drift apart.
 *
 * Why this exists: a claim in a client brief needs a citation target.
 * "Powhatan scores 26.2" is checkable only if the reader can open the map at
 * Powhatan. "Twelve blocked cases in Virginia" is checkable only if the
 * reader can open the dashboard already filtered that way. A screenshot plus
 * written instructions is not a citation. This turns the URL into the
 * citation format.
 *
 * Hash grammar
 *   #map=<zoom>/<lat>/<lon>[&<key>=<value>...]
 *
 * The map segment appears only on pages that call attach(). Dashboards carry
 * filter keys alone.
 *
 * Legacy form
 *   opposition-tracker.html and master_datacenter_map.html previously wrote a
 *   bare state slug (#virginia) with no key. Those links are already in
 *   circulation, so a bare segment is read as state=<slug> and rewritten in
 *   keyed form on the first write. Old links keep resolving; new links are
 *   parseable. Segments that are neither keyed nor the first bare token are
 *   preserved verbatim and ignored.
 *
 * One writer per page
 *   attach() and attachControls() both register into a single module-level
 *   writer. opposition-map.html carries a map and five filter controls; two
 *   independent writers would each rebuild the hash from its own partial
 *   view and overwrite the other's segments on every interaction.
 *
 * Restore is deliberately non-navigating for selection keys. A page that
 * restores state=virginia should re-apply the highlight and the status line
 * but must not re-run its flyToBounds, because the animation would land on
 * the state envelope and discard the exact center and zoom the link carried.
 *
 * Deferred control restore
 *   Dashboard <select> options are built from loaded data, so assigning a
 *   value before the options exist is a silent no-op. attachControls()
 *   returns a controller with restore(); pages call it again once their
 *   options are populated, then re-render. Restore never dispatches
 *   synthetic change events, because the pages wire onchange to their own
 *   render functions and a synthetic dispatch would re-enter them.
 *
 * Embedding note: these pages are served standalone from Pages and also
 * embedded in Notion through Simple.ink. Inside an iframe, replaceState
 * rewrites the frame's own URL and never the parent's, so a permalink is
 * only shareable from the standalone Pages URL. Some sandbox configurations
 * throw SecurityError on replaceState outright, which is why every history
 * call here is wrapped. A page that cannot write its hash still works; it
 * just does not produce links.
 *
 * Registration
 *   2026-08-12  Initial registration. Grammar as above. Restore applies once
 *               on load, before first user interaction, and is not
 *               re-applied on later hash changes within a session. Legacy
 *               bare-slug reads map to state. Selection restore does not
 *               navigate.
 *   2026-08-12  Added attachControls() for pages with no Leaflet map, and
 *               collapsed both entry points onto a single shared writer.
 *               Control restore is deferred and does not dispatch events.
 */
(function (global) {
  'use strict';

  // ---------------------------------------------------------------------
  // Single per-page writer
  // ---------------------------------------------------------------------
  var S = {
    map: null,
    precision: 4,
    extra: {},        // insertion-ordered selection and filter keys
    foreign: [],      // segments this module did not author
    lastWritten: null,
    suspended: false,
    parsed: null      // hash as read at first registration
  };

  function parseHash(raw) {
    var out = { view: null, extra: {}, foreign: [], legacy: false };
    var h = String(raw || '').replace(/^#/, '');
    if (!h) return out;

    var sawBare = false;

    h.split('&').forEach(function (seg) {
      if (!seg) return;
      var eq = seg.indexOf('=');

      if (eq < 0) {
        // First bare token is the legacy state slug. Anything after that is
        // not ours and rides along untouched.
        if (!sawBare) {
          sawBare = true;
          out.extra.state = decodeURIComponent(seg);
          out.legacy = true;
        } else {
          out.foreign.push(seg);
        }
        return;
      }

      var k = seg.slice(0, eq);
      var v = seg.slice(eq + 1);

      if (k === 'map') {
        var p = v.split('/');
        var z = parseFloat(p[0]), lat = parseFloat(p[1]), lon = parseFloat(p[2]);
        // Reject partial or out-of-range triples rather than restoring a
        // half-parsed view. A malformed hash falls through to the page
        // default; it does not drop the reader somewhere arbitrary.
        if (p.length === 3 &&
            isFinite(z) && isFinite(lat) && isFinite(lon) &&
            lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
          out.view = { zoom: z, lat: lat, lon: lon };
        }
        return;
      }

      out.extra[k] = decodeURIComponent(v);
    });

    return out;
  }

  // Read the incoming hash once, on whichever entry point registers first.
  function ensureParsed() {
    if (S.parsed) return S.parsed;
    S.parsed = parseHash(global.location.hash);
    S.foreign = S.parsed.foreign;
    Object.keys(S.parsed.extra).forEach(function (k) {
      S.extra[k] = S.parsed.extra[k];
    });
    return S.parsed;
  }

  function buildHash() {
    var parts = [];

    if (S.map) {
      var c = S.map.getCenter();
      // Zoom is rounded because zoomSnap 0.25 pages settle on clean quarters
      // but flyTo can leave float dust that churns the hash on every move.
      var z = Math.round(S.map.getZoom() * 100) / 100;
      parts.push('map=' + z + '/' +
                 c.lat.toFixed(S.precision) + '/' + c.lng.toFixed(S.precision));
    }

    Object.keys(S.extra).forEach(function (k) {
      var v = S.extra[k];
      if (v === null || v === undefined || v === '') return;
      parts.push(k + '=' + encodeURIComponent(String(v)));
    });

    S.foreign.forEach(function (seg) { parts.push(seg); });

    return parts.length ? '#' + parts.join('&') : '';
  }

  function flush() {
    if (S.suspended) return;
    var hash;
    try { hash = buildHash(); } catch (e) { return; }
    if (hash === S.lastWritten) return;
    S.lastWritten = hash;
    try {
      global.history.replaceState(null, '', global.location.pathname +
                                  global.location.search + hash);
    } catch (e) {
      // Sandboxed iframe. The page is fully functional without a shareable
      // URL, so this stays silent by design.
    }
  }

  function assign(k, v) {
    if (v === null || v === undefined || v === '' || v === false) delete S.extra[k];
    else S.extra[k] = (v === true) ? '1' : v;
  }

  function setKey(k, v) { assign(k, v); flush(); }

  var CONTROLLER = {
    set: setKey,
    get: function (k) { return S.extra[k]; },
    update: flush,
    suspend: function () { S.suspended = true; },
    resume: function () { S.suspended = false; flush(); }
  };

  // ---------------------------------------------------------------------
  // Map binding
  // ---------------------------------------------------------------------
  /* attach(map, opts) -> controller
   *
   *   opts.precision   decimal places on lat/lon. Default 4, roughly 11 m,
   *                    well past what a county-scale view needs and stable
   *                    under small pans.
   *   opts.onRestore   fn({ view, extra, legacy }) called once, after the
   *                    view is applied. Use it to park a selection for the
   *                    page to re-apply once its async layers exist. Runs
   *                    even when no view segment was present, so a selection
   *                    key alone still deep-links.
   */
  function attach(map, opts) {
    opts = opts || {};
    var parsed = ensureParsed();

    S.map = map;
    if (opts.precision != null) S.precision = opts.precision;

    if (parsed.view) {
      map.setView([parsed.view.lat, parsed.view.lon], parsed.view.zoom, {
        animate: false
      });
    }

    map.on('moveend zoomend', flush);

    if (typeof opts.onRestore === 'function') {
      try {
        opts.onRestore({
          view: parsed.view, extra: parsed.extra, legacy: parsed.legacy
        });
      } catch (e) {
        // A page-side restore that throws must not prevent the writer from
        // binding, or the page ends up with a URL that never updates.
        if (global.console && global.console.warn) {
          global.console.warn('MapPermalink restore handler failed', e);
        }
      }
    }

    flush();
    return CONTROLLER;
  }

  // ---------------------------------------------------------------------
  // Control binding, for pages with no map
  // ---------------------------------------------------------------------
  function readControl(el) {
    if (!el) return null;
    if (el.type === 'checkbox') return el.checked ? '1' : null;
    return el.value || null;
  }

  function writeControl(el, v) {
    if (!el) return false;
    if (el.type === 'checkbox') {
      var want = (v === '1' || v === 'true');
      if (el.checked === want) return false;
      el.checked = want;
      return true;
    }
    if (el.value === v) return false;
    el.value = v;
    // A select silently keeps its old value when the option is absent, which
    // is the deferred-population case. Report no change so the caller can
    // retry after its options are built.
    if (el.tagName === 'SELECT' && el.value !== v) return false;
    return true;
  }

  /* attachControls({ controls, onRestore }) -> controller
   *
   *   controls   [{ id: 'f-state', key: 'state' }, ...]
   *   onRestore  fn() called after any restore() that changed something, so
   *              the page can re-render.
   *
   * Controller adds:
   *   .restore()  re-apply parked values; returns true if anything landed
   */
  function attachControls(spec) {
    spec = spec || {};
    var controls = spec.controls || [];
    var parsed = ensureParsed();
    var debounce = null;

    function sync() {
      controls.forEach(function (c) {
        assign(c.key, readControl(global.document.getElementById(c.id)));
      });
      flush();
    }

    controls.forEach(function (c) {
      var el = global.document.getElementById(c.id);
      if (!el) return;
      el.addEventListener('change', sync);
      if (el.tagName === 'INPUT' && el.type !== 'checkbox') {
        el.addEventListener('input', function () {
          global.clearTimeout(debounce);
          debounce = global.setTimeout(sync, 250);
        });
      }
    });

    function restore() {
      var applied = false;
      controls.forEach(function (c) {
        var v = parsed.extra[c.key];
        if (v === undefined || v === null || v === '') return;
        if (writeControl(global.document.getElementById(c.id), v)) applied = true;
      });
      if (applied && typeof spec.onRestore === 'function') {
        try { spec.onRestore(); }
        catch (e) {
          if (global.console && global.console.warn) {
            global.console.warn('MapPermalink control restore failed', e);
          }
        }
      }
      return applied;
    }

    restore();
    flush();

    return {
      set: CONTROLLER.set,
      get: CONTROLLER.get,
      update: flush,
      suspend: CONTROLLER.suspend,
      resume: CONTROLLER.resume,
      restore: restore
    };
  }

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------
  /* Copies the current URL to the clipboard and reports through a button
   * label. Falls back to a hidden textarea where the async clipboard API is
   * unavailable, which covers older embedded webviews.
   */
  function copyLink(btn, restoreLabel) {
    var url = global.location.href;
    var done = function (ok) {
      if (!btn) return;
      var original = restoreLabel || 'Copy link';
      btn.textContent = ok ? 'Copied' : 'Copy blocked';
      global.setTimeout(function () { btn.textContent = original; }, 1800);
    };

    if (global.navigator && global.navigator.clipboard &&
        global.navigator.clipboard.writeText) {
      global.navigator.clipboard.writeText(url)
        .then(function () { done(true); })
        .catch(function () { done(false); });
      return;
    }

    try {
      var ta = global.document.createElement('textarea');
      ta.value = url;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      global.document.body.appendChild(ta);
      ta.select();
      var ok = global.document.execCommand('copy');
      ta.remove();
      done(ok);
    } catch (e) { done(false); }
  }

  /* Slug helpers, shared so the tracker pages encode identically. */
  function slug(name) {
    return String(name || '').toLowerCase().replace(/ /g, '-');
  }
  function matchSlug(candidates, wanted) {
    var w = slug(wanted);
    for (var i = 0; i < candidates.length; i++) {
      if (slug(candidates[i]) === w) return candidates[i];
    }
    return null;
  }

  global.MapPermalink = {
    attach: attach,
    attachControls: attachControls,
    copyLink: copyLink,
    parseHash: parseHash,
    slug: slug,
    matchSlug: matchSlug
  };
})(window);
