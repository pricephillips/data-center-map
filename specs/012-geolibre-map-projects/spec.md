# Feature Specification: GeoLibre Map Projects and Leaflet Pattern Ports

**Feature Branch**: `012-geolibre-map-projects`

**Created**: 2026-10-01

**Status**: Draft

**Input**: Review of GeoLibre (opengeos/GeoLibre, MIT, Python package `geolibre` 3.2.0, requires Python 3.11+) and its 100-map gallery. GeoLibre stores a whole map (layers, styles, popups, legend, swipe, time slider) as one `.geolibre.json` project file authored from Python. This spec adds GeoLibre as an export target for pipeline outputs and ports a small set of its interaction patterns into the existing Leaflet pages. It does not replace any page.

## Standing decisions this spec respects

- `configs/integrations.json` entry `maplibre-migration` (eliminated): no full MapLibre rewrite of the Leaflet pages. The maplibre-gl-leaflet binding stays the vector path.
- Entry `leaflet-heat` (eliminated, defensibility-risk): density and heatmap layers visually overstate detection-biased coverage. No heatmap or density layer of opposition records in any surface, GeoLibre projects included.
- Entry `frontend-stacks` (eliminated): public hosting of client data is not allowed. `share.geolibre.app` is public, so nothing is published there.
- Entry `pmtiles` (deferred): stays deferred unless a layer passes about 5 MB.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - GeoLibre and forge3d are registered and audited (Priority: P1)

`configs/integrations.json` gains entries for `geolibre` (python, MIT, status selected, spec 012, products `internal` and `active_db`, runs_in `local`) and `forge3d` (python, `Apache-2.0 OR MIT` open core, status selected, spec 013, products `static_report` and `internal`, runs_in `local`). Each entry pins an exact version and records risk notes: GeoLibre public sharing host is forbidden; forge3d Pro features are forbidden (see spec 013). `sessions` gains `"9": "012 GeoLibre map projects"` and `"10": "013 terrain plates"`. Two eliminated entries are added with reasons: `geolibre-share` (share.geolibre.app, public hosting of client data) and `forge3d-pro` (MapPlate, vector export, building import; commercial license key). `integration_audit.py` gains two checks: no repo code references `share.geolibre.app`, and no repo code calls `set_license_key`, `MapPlate`, or forge3d vector export.

**Why this priority**: Every tool enters through the registry. The audit makes the two hosting and licensing boundaries automatic rather than remembered.

**Independent Test**: `python integration_audit.py --selftest` passes, including new fixtures that plant each forbidden reference and confirm a failure.

### User Story 2 - The pipeline exports GeoLibre projects from committed files (Priority: P1)

New `scripts/export_geolibre.py` builds projects with the `geolibre` authoring API (`build_empty_project`, `geojson_layer`, `add_layer`, `build_choropleth_style`, `set_popup`, `add_legend`, `add_swipe`, `save_project` with credential redaction). Inputs are committed platform files only; the script records the commit SHA in each project's description. Outputs go to `data/geolibre/` and are declared in `configs/layers.json` with this script as the single writer.

Projects, each one file:

1. `national_restriction_model.geolibre.json`: county polygons from `data/geo/counties_2024.topojson` (converted to GeoJSON in memory) styled by `calibrated_score` deciles from `county_policy_scores.csv`, colors from the canonical palette (inferno truncated at t=0.10, fill opacity 0.88). Popup: county name, state, calibrated score, decile, `has_enacted_restrictive`. The project description defines "calibrated score" and "decile" in plain language on first use and states the score is a resemblance measure, not a forecast.
2. `national_opposition_cases.geolibre.json`: project pins from `master_opposition_clean.csv` with color by `outcome_defensible` using only the platform vocabulary. Popup columns come from an explicit allowlist in `configs/geolibre_export.json`; the script refuses any group-level outcome column (`decided`, `confirmed_blocks`, `blocked_share`) and any `site_screener.py` composite field. Pins are clustered at national zoom.
3. `score_vs_politics_swipe.geolibre.json`: the restriction-score choropleth on one side of a swipe control and 2024 presidential margin on the other, margin read from the political feature file named in `DATA_NOTICES.md` (MEDSL once promoted, legacy tonmcg until then), with that source credited.
4. `opposition_timeline.geolibre.json`: pins on a GeoLibre time slider by announced year. Only rows with a verified announced date enter; the project description states how many rows were excluded for missing dates.

**Why this priority**: One command turns the current data into an analyst-grade map without hand styling, and the same file can back a private client view later (spec 011).

**Independent Test**: Run the exporter against a fixture CSV; reload each project with `load_project` and `describe_project`; confirm layer counts, the allowlist, the refusal on a planted group-level column, and redaction.

**Acceptance Scenarios**:

1. **Given** a county FIPS in `county_policy_scores.csv` missing from the geometry, **When** the exporter runs, **Then** it fails with the FIPS listed.
2. **Given** a planted `win` or `loss` string in a fixture popup field, **When** the leak audit runs over `data/geolibre/`, **Then** it fails.
3. **Given** a row whose outcome is not one of the platform terms, **When** exported, **Then** the script fails rather than inventing a color class.

### User Story 3 - Projects open privately for analysts (Priority: P2)

`docs/geolibre.md` records how Price opens a project: GeoLibre desktop app or the Jupyter widget (`pip install "geolibre[vector]"`), loading the local file. Nothing is uploaded. A deferred registry entry `geolibre-selfhost` records the trigger "an active_db client engagement needs an interactive analyst workspace behind access control" and the research notes on GeoLibre self-hosting gathered during `/speckit-plan`.

**Independent Test**: Open each exported project in the Jupyter widget offline and confirm layers, legend, popups, swipe and slider render.

### User Story 4 - Three GeoLibre patterns come to the Leaflet pages (Priority: P2)

No rewrite; in-house code only, consistent with the earlier choice of in-house slider and side panel over stale plugins.

1. Swipe compare on `restriction-model.html`: a vertical divider that clips the political-margin choropleth over the score choropleth, state serialized by `map-permalink.js`. Implemented with Leaflet panes and CSS clip, no new library.
2. Legends generated from symbology: `legend-filter.js` builds legend entries from the same class breaks and colors the layer uses (read from `viz-palette.js`), so a legend can never disagree with the map.
3. Uniform hover tooltips on every pin map: the same short label (project name, county, outcome term) on hover; click keeps opening the detail side panel.

**Independent Test**: `ui-check.yml` passes; new Playwright cases drag the swipe divider and reload from the permalink to the same position; a selftest confirms legend entries equal the layer's class breaks.

### User Story 5 - GeoLibre MCP server for Claude Code sessions (Priority: P3)

Agent setup only, no repo change beyond `docs/geolibre.md`: install `geolibre[mcp]` locally and register its MCP server in Claude Code so sessions can inspect and edit exported projects. Record the install and config lines.

### Edge Cases

- `counties_2024.topojson` is TopoJSON; GeoLibre layers take GeoJSON. Convert in Python (topojson to GeoJSON) inside the exporter; do not commit a second geometry file unless it stays under 5 MB and is declared in `layers.json`.
- Alaska and Hawaii: keep geographic coordinates; GeoLibre renders Web Mercator. No insets in projects.
- Rows with no coordinates: excluded and counted in the project description, never geocoded on the fly.
- The GeoLibre package changes its project schema: the pinned version governs; the exporter validates with `describe_project` after writing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `scripts/export_geolibre.py` MUST read only committed files, record the commit SHA, and ship a `--selftest`.
- **FR-002**: Popup and tooltip fields MUST come from the allowlist in `configs/geolibre_export.json`; group-level outcome columns and composite screener fields MUST be refused.
- **FR-003**: No heatmap, density, or 3D extrusion of opposition records in any project or page.
- **FR-004**: No code path may upload to `share.geolibre.app` or any public host.
- **FR-005**: Outputs MUST pass `leak_audit.py --tier blocking`, the em-dash check, and LF line endings; `layer_audit.py` reports 0 undeclared findings.
- **FR-006**: Leaflet changes MUST pass `node --check`, all `*_selftest.js`, and `ui-check.yml`; `raw.githubusercontent.com` stays first in every fetch chain.
- **FR-007**: Data credits for every source in a project MUST appear in the project description and match `DATA_NOTICES.md`.

## Success Criteria *(mandatory)*

- **SC-001**: One command writes all four projects in under a minute on Price's Mac.
- **SC-002**: 3,144 of 3,144 scored counties appear in the choropleth project.
- **SC-003**: Integration, leak, layer and visibility audits are clean on main plus this change.

## Assumptions

- GeoLibre stays MIT. If its license changes, the pinned version remains usable and the entry moves to deferred.
- Exports are internal and analyst-facing until spec 011 provides access-controlled client hosting.
