# Tasks: GeoLibre Map Projects and Leaflet Pattern Ports

**Input**: [plan.md](./plan.md), [research.md](./research.md), [spec.md](./spec.md)
**Status**: implemented 2026-10-01, uncommitted in the working tree for Price.

## Phase 1: Setup

- [x] T001 Local Python 3.11 venv with `geolibre==3.2.0` and `geolibre[vector]`; read `authoring.py`, `project.py`, `_server.py` (research R1, R3)
- [x] T002 [P] Add `OUTCOME_COLOR` to `viz-palette.js` and export it on `VizPalette`

## Phase 2: User Story 1, registry and audit (P1)

- [x] T003 [US1] `configs/integrations.json`: sessions 9 and 10; `geolibre`, `forge3d` selected; `geolibre-selfhost` deferred; `geolibre-share`, `forge3d-pro` eliminated; `Apache-2.0 OR MIT` on the allowlist
- [x] T004 [US1] `integration_audit.py`: boundary scan (sharing host, license-key setter, MapPlate, forge3d vector export, building import, scene bundles) wired into the default run
- [x] T005 [US1] Selftest fixtures planting each forbidden reference, plus a WeasyPrint `export_pdf` negative case

## Phase 3: User Story 2, exporter (P1)

- [x] T006 [US2] `configs/geolibre_export.json` allowlist
- [x] T007 [US2] `scripts/export_geolibre.py`: TopoJSON decode, palette read, four projects, refusals, SHA, redaction, reload validation
- [x] T008 [US2] `--selftest`: allowlist, planted group-level and screener columns, unknown outcome, missing FIPS, redaction, layer counts, swipe, heatmap refusal
- [x] T009 [US2] Write `data/geolibre/*.geolibre.json`; declare in `configs/layers.json` Layer E
- [x] T010 [US2] `DATA_NOTICES.md` credit row; leak audit clean over `data/geolibre/`

## Phase 4: User Story 3, private opening (P2)

- [x] T011 [US3] `docs/geolibre.md`: desktop app and Jupyter widget, nothing uploaded, time slider binding
- [ ] T012 [US3] Manual: open each project in the Jupyter widget offline and confirm layers, legend, popups, swipe (Price; needs a browser session)

## Phase 5: User Story 4, Leaflet ports (P2)

- [x] T013 [P] [US4] Swipe compare on `restriction-model.html`, panes plus CSS clip, keyboard divider
- [x] T014 [P] [US4] `map-permalink.js` `sw=` key; `permalink_selftest.js` round trip
- [x] T015 [P] [US4] `legend-filter.js` legends from symbology; `legend_filter_selftest.js` breaks equal layer breaks
- [x] T016 [P] [US4] Shared `bindPinTooltip` on opposition-map, opposition-tracker, proposals-map, master_datacenter_map
- [x] T017 [US4] `tests/ui/swipe.spec.js`: drag, permalink reload, legend breaks, tooltips

## Phase 6: User Story 5, MCP (P3)

- [x] T018 [US5] Install and `claude mcp add` lines in `docs/geolibre.md` (console script confirmed from the wheel's entry points)

## Phase 7: Polish

- [x] T019 Gates: integration, leak (blocking), layer, visibility audits; selftests; node --check; Playwright; em-dash and CRLF checks
- [ ] T020 After commit: re-run `scripts/export_geolibre.py` without `--allow-uncommitted` so the projects carry a clean SHA (Price)
