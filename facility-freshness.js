/* facility-freshness.js
 *
 * Freshness signal for the Layer A facility snapshots.
 *
 * The Infrastructure surfaces render atlas.csv and ai_centers.csv with no date
 * and no row count, so a snapshot frozen two years ago looks exactly like one
 * refreshed this morning. This module reads data/facility_manifest.json and
 * says, in one line per source, how many rows there are, when the file last
 * changed in the repository, whether anyone has recorded the upstream vintage,
 * and whether a pipeline refreshes it.
 *
 * It never presents the repository commit date as the age of the data. Where
 * the vintage is undeclared the line says so, because an unknown age stated
 * plainly is worth more than a confident date that measures the wrong thing.
 *
 * Usage
 *   <script src="./facility-freshness.js"></script>
 *   FacilityFreshness.render('facility-freshness');
 *
 * Self-tests: node facility_freshness_selftest.js
 */
(function (global) {
  'use strict';

  var RAW = 'https://raw.githubusercontent.com/pricephillips/data-center-map/main';
  // raw first: GitHub Pages sends no CORS headers, so an embedded iframe
  // fails silently on the Pages origin. Same convention as every other page.
  var MANIFEST_URLS = [
    RAW + '/data/facility_manifest.json',
    './data/facility_manifest.json',
    'data/facility_manifest.json'
  ];

  function fmtInt(n) {
    if (n === null || n === undefined || isNaN(n)) return 'unknown';
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function plural(n, one, many) { return n === 1 ? one : many; }

  /* One sentence of provenance for one source. Pure: no DOM, no network. */
  function describe(entry) {
    if (!entry) return '';
    if (entry.present === false) {
      return entry.label + ': file absent from this build.';
    }
    var parts = [];
    var rows = fmtInt(entry.rows);
    var unit = plural(entry.rows, 'facility', 'facilities');
    if (entry.rows_us !== null && entry.rows_us !== undefined &&
        entry.rows_us !== entry.rows) {
      parts.push(rows + ' ' + unit + ' (' + fmtInt(entry.rows_us) +
                 ' in the United States)');
    } else {
      parts.push(rows + ' ' + unit);
    }

    if (entry.vintage_status === 'declared' && entry.upstream_vintage) {
      parts.push('upstream vintage ' + entry.upstream_vintage);
    } else {
      parts.push('upstream vintage not recorded, so the age of the data is unknown');
    }

    if (entry.repo_last_changed) {
      var age = entry.days_since_repo_change;
      var aged = (age === null || age === undefined) ? '' :
        ' (' + fmtInt(age) + ' ' + plural(age, 'day', 'days') + ' ago)';
      parts.push('last changed in this repository ' + entry.repo_last_changed + aged);
    }

    var refresh = entry.refresh || {};
    if (refresh.pipeline) {
      parts.push('refreshed ' + (refresh.cadence || 'on a pipeline'));
    } else {
      parts.push('no automated refresh');
    }
    return entry.label + ': ' + parts.join('; ') + '.';
  }

  /* Try each URL in order; resolve with the first that parses. */
  function fetchFirst(urls, fetchImpl) {
    var f = fetchImpl || (global.fetch && global.fetch.bind(global));
    if (!f) return Promise.reject(new Error('no fetch available'));
    var i = 0, lastErr = null;
    function attempt() {
      if (i >= urls.length) {
        return Promise.reject(lastErr || new Error('no manifest url resolved'));
      }
      var url = urls[i++];
      return f(url).then(function (resp) {
        if (!resp || !resp.ok) throw new Error('HTTP ' + (resp && resp.status));
        return resp.json();
      }).catch(function (err) { lastErr = err; return attempt(); });
    }
    return attempt();
  }

  function lines(manifest, only) {
    var sources = (manifest && manifest.sources) || [];
    if (only && only.length) {
      sources = sources.filter(function (s) {
        return only.indexOf(s.source_id) >= 0;
      });
    }
    return sources.map(describe).filter(Boolean);
  }

  function render(targetId, opts) {
    opts = opts || {};
    var el = global.document && global.document.getElementById(targetId);
    if (!el) return Promise.resolve(null);
    return fetchFirst(opts.urls || MANIFEST_URLS, opts.fetchImpl)
      .then(function (manifest) {
        var body = lines(manifest, opts.sources);
        if (!body.length) { el.textContent = ''; return manifest; }
        el.innerHTML =
          '<span class="freshness-label">Data provenance</span>' +
          body.map(function (t) {
            return '<span class="freshness-line"></span>';
          }).join('');
        // Text is set through textContent so a manifest value can never be
        // interpreted as markup.
        var slots = el.querySelectorAll('.freshness-line');
        for (var i = 0; i < body.length; i++) slots[i].textContent = body[i];
        return manifest;
      })
      .catch(function () {
        el.textContent = 'Data provenance unavailable: facility_manifest.json ' +
                         'did not load. Treat these counts as undated.';
        return null;
      });
  }

  var api = {
    MANIFEST_URLS: MANIFEST_URLS,
    describe: describe,
    fetchFirst: fetchFirst,
    lines: lines,
    render: render,
    fmtInt: fmtInt
  };

  global.FacilityFreshness = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
