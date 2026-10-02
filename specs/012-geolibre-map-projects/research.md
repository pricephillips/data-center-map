# Research: GeoLibre Map Projects and Leaflet Pattern Ports

All call signatures below were read from the installed `geolibre==3.2.0`
sources (`geolibre/authoring.py`, `geolibre/project.py`, `geolibre/_server.py`)
in a Python 3.11 venv on 2026-10-01, not from memory or documentation.

## R1. Authoring API actually used

| Need | Call (geolibre 3.2.0) | Note |
|---|---|---|
| Empty project | `project.build_empty_project(name, center=, zoom=)` | Returns a plain dict; `metadata` is free-form and carries `commit_sha`. |
| Layer | `project.geojson_layer(name, featurecollection, **style)` then `authoring.add_layer(p, layer)` | Style keys are camelCase (`fillOpacity`, `strokeColor`, ...). |
| Choropleth | `authoring.build_choropleth_style(values, column, class_count=10, colormap="inferno")` then `authoring.apply_style` | The builder supplies the graduated structure; the stop colors are then replaced from `viz-palette.js` (inferno floored at t=0.10), because GeoLibre's own `inferno` ramp starts at near-black `#000004`, which the platform palette rules out. |
| Categories | `apply_style` with `vectorStyleMode: "categorized"` | No Python builder exists for categorized stops; the app bundle reads `vectorStyleStops[].value/label/color` for this mode (confirmed in the shipped `App-*.js`). |
| Popup, tooltip | `authoring.set_popup(p, ref, fields, title=, hover=True, show_feature_id=False)` with `project.popup_field(field, label=, hover=)` | Fields come only from `configs/geolibre_export.json`. |
| Legend | `authoring.add_legend(p, title, labels=, colors=, shape=, position=)` | Entries are generated from the same stops the layer uses. |
| Swipe | `authoring.add_swipe(p, left_layers=[id], right_layers=[id])` | `describe_project` reports `swipe` in `mapControls`. |
| Clustering | style `pointRenderer: "cluster"`, `clusterRadius`, `clusterMaxZoom` | Same keys `Map.add_marker_cluster` sets. |
| Redaction | `project.redact_credentials(p)` | Keeps the swipe and legend plugin settings (`PUBLISHABLE_PLUGIN_SETTINGS`). |
| Validation | `authoring.load_project(path)` and `authoring.describe_project(p)` | Run on every file after it is written. |

**Deviation 1, save format.** `authoring.save_project` hard-codes
`json.dumps(indent=2)`, which puts every coordinate on its own lines. The swipe
project (two full county layers) came out at 11.5 MB that way versus 2.9 MB
compact, past the ~5 MB point at which registry entry `pmtiles` says a layer
needs vector tiles. The exporter keeps save_project's semantics (redacted
dict, temp file in the destination directory, atomic `os.replace`) but writes
compact JSON, then reloads with `load_project` and `describe_project`. Sizes:
1.5 MB, 2.9 MB, 0.3 MB, 0.3 MB. Coordinates are rounded to 4 decimals (about
11 m), under the 1:5m source's own generalization.

**Deviation 2, time slider.** geolibre 3.2.0 exposes no Python builder for
Time Slider state (only the plugin id `maplibre-gl-time-slider` in
`PUBLISHABLE_PLUGIN_SETTINGS`; its state shape lives in the minified app
bundle). Writing a reverse-engineered blob would bind the project to an
undocumented schema. The timeline project instead carries a numeric
`action_year` on every pin and `metadata.time_property = "action_year"`, and
its description tells the analyst to bind the slider to that property in the
app (Layers, Bind property).

**Deviation 3, "announced year".** `master_opposition_clean.csv` has no
announced-date column. The timeline uses the recorded action date (`Date`),
kept only where it parses as a full ISO date, `date_parseable` is True, and
its year equals `action_year`. 1,110 pins qualify; 11 pinnable rows are
excluded and counted in the description.

## R2. Inputs and vocabulary

- Geometry: `data/geo/counties_2024.topojson` is quantized TopoJSON
  (`transform` present, one object `counties`, `id` = FIPS). Decoded in
  Python (delta-decoded arcs, reversed arcs for negative indices); no second
  geometry file is committed. 3,144 of 3,144 scored FIPS have polygons.
- County and state names: the TopoJSON carries only `name`; state comes from
  `data/county_aggregate.csv` (`county_name`, `state`).
- Margin: `data/county_votes.json` (`{"<fips>": {"2024": share}}`).
  `data/features/features_manifest.json` has no `sources.political` block, so
  MEDSL is not promoted and the tonmcg credit (MIT, with its "not an
  authoritative source" caveat) is used, per `DATA_NOTICES.md`. 28 scored
  counties have no 2024 margin and are absent from the right side; the count
  is in the description.
- Outcome vocabulary: `outcome_defensibility.OUTCOME_GRADES` (seven grades,
  the four platform terms plus `blocked_unverified`, `advanced_unverified`,
  `mixed`). The exporter keeps a literal copy and its selftest checks the two
  agree. Colors: new `OUTCOME_COLOR` block in `viz-palette.js`, keeping the
  three colors `opposition-map.html` already shipped and adding lighter tints
  for the unverified tiers.
- Refused columns: `decided`, `confirmed_blocks`, `blocked_share`, and every
  column `site_screener.py --batch` writes into its composite (`tier`,
  `composite`, the five components, their `pct_*`, `events_within_50mi`).
- Pins: `map_pinnable == True` rows (1,121); the 834 others are statewide or
  jurisdiction-level records whose coordinates are not a site. Nothing is
  geocoded.

## R3. GeoLibre self-hosting (US3, deferred entry `geolibre-selfhost`)

What the 3.2.0 wheel shows:

- The whole web app ships inside the Python package as a Vite single-page
  build (`geolibre/static/app`, 202 MB with all plugin chunks). There is no
  application server: `_server.py` serves that directory from a
  `ThreadingHTTPServer` bound to 127.0.0.1 for the Jupyter widget, and
  `_extension.py` serves it through the Jupyter Server extension for
  JupyterHub. Self-hosting is therefore static hosting of `static/app`.
- Projects are plain JSON; the app loads one from a URL or file. GeoJSON is
  inlined, so a project needs no data backend.
- Outbound requests a self-hosted instance would still make: the basemap
  style and tiles (default OpenFreeMap) and any remote layer URLs. The
  platform's own projects contain only inlined GeoJSON.

What a client deployment would need (only when the trigger fires): serve
`static/app` from the spec 011 access-controlled host (not a public static
host); serve project files from the same private origin; restrict the app's
plugin list to first-party controls; pin the bundle to the registry version;
keep the sharing action unreachable (the code-level ban in
`integration_audit.py` covers repo code, not a third-party bundle, so this is
a deployment check). Until then projects open locally only (`docs/geolibre.md`).

## R4. Leaflet ports (US4)

In-house, no new libraries (entries `maplibre-migration` and `leaflet-heat`
stand). Swipe uses a dedicated Leaflet pane for the margin choropleth, with
both panes clipped by CSS `clip: rect()` on opposite sides of a keyboard-
accessible divider (`role="slider"`), redrawn on drag, move, zoom and
resize; state is the additive permalink key `sw=<int pct>` (absent = off, so
old links are unchanged). Legends are generated from the same scale objects
the layers use (`legend-filter.js` `entriesFromScale`, `marginSymbology`,
`outcomeEntries`), with selftests asserting entries equal the layer's breaks
and colors. One shared tooltip helper (`bindPinTooltip`) gives every pin map
the same name / place / outcome label; click still opens the side panel.

## R5. Registry and audit (US1)

`configs/integrations.json` gains sessions 9 and 10, selected entries
`geolibre` (==3.2.0, MIT) and `forge3d` (==1.40.1, `Apache-2.0 OR MIT`, added
to the allowlist since both components already are), deferred
`geolibre-selfhost`, and eliminated `geolibre-share` (principle-conflict) and
`forge3d-pro` (license-risk). `integration_audit.py` now scans repo code
(`*.py *.js *.html *.yml *.yaml *.sh`, skipping specs, docs, node_modules,
caches) for the sharing host, the forge3d license-key setter, `MapPlate`,
and, in files that use forge3d, SVG/PDF export, building import and scene
bundles. Generic names such as `export_pdf` only fire alongside forge3d so a
WeasyPrint renderer (spec 010) cannot trip them. Selftest plants each one.
