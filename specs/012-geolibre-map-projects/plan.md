# Implementation Plan: GeoLibre Map Projects and Leaflet Pattern Ports

**Branch**: `012-geolibre-map-projects` | **Date**: 2026-10-01 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/012-geolibre-map-projects/spec.md`

## Summary

Register GeoLibre and forge3d and enforce their hosting and licensing
boundaries in `integration_audit.py`; export four GeoLibre projects from
committed files with `scripts/export_geolibre.py`; document private, local
opening and the deferred self-hosting path; port swipe compare, legends from
symbology and uniform pin tooltips into the existing Leaflet pages without a
rewrite. Research: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.11 (exporter; geolibre requires 3.11+), browser JavaScript (ES5 style, as the pages)
**Primary Dependencies**: geolibre==3.2.0 (exporter only, local venv); Leaflet as already loaded; no new JS library
**Storage**: files. Outputs `data/geolibre/*.geolibre.json` (Layer E, derived)
**Testing**: `--selftest` on every new Python module; `*_selftest.js`; Playwright in `tests/ui`
**Target Platform**: Price's Mac (export), static pages in Notion embeds
**Performance Goals**: all four projects in under a minute (measured: about 2 s)
**Constraints**: no MapLibre rewrite, no heatmap or density, no 3D extrusion; nothing uploaded; LF; no em-dashes; raw.githubusercontent.com first
**Scale/Scope**: 3,144 counties, 1,121 pinnable cases

## Constitution Check

| Principle | How this plan complies |
|---|---|
| I Defensibility | Unverified outcome tiers are drawn in lighter tints and named as recorded; no density layer. |
| II Vocabulary | Only `OUTCOME_GRADES` terms; exporter fails on any other value; leak audit covers `data/geolibre/`. |
| IV Descriptive | Calibrated score defined as a resemblance measure, not a forecast, in each project description. |
| VI Reproducibility | Commit SHA in every project; inputs must be tracked files. |
| VII Additive | New files and new keys only (`sw=` permalink key, `OUTCOME_COLOR`, legend helpers). |
| VIII Layer ownership | `data/geolibre/*.geolibre.json` declared in Layer E with one writer. |
| IX Selftested | `export_geolibre.py --selftest`, `integration_audit.py --selftest`, JS selftests. |

## Project Structure

### Documentation (this feature)

```text
specs/012-geolibre-map-projects/
├── spec.md
├── plan.md
├── research.md
└── tasks.md
```

### Source Code (repository root)

```text
configs/integrations.json        registry entries and sessions 9, 10
configs/geolibre_export.json     popup and tooltip allowlist
configs/layers.json              Layer E: data/geolibre/*.geolibre.json
integration_audit.py             boundary scan + selftest fixtures
scripts/export_geolibre.py       exporter (+ --selftest)
data/geolibre/                   four projects
docs/geolibre.md                 opening, rebuilding, MCP setup
viz-palette.js                   OUTCOME_COLOR
legend-filter.js, map-permalink.js, restriction-model.html, opposition-map.html,
opposition-tracker.html, proposals-map.html, master_datacenter_map.html
legend_filter_selftest.js, permalink_selftest.js, tests/ui/swipe.spec.js
DATA_NOTICES.md                  credit row for the projects
```

**Structure Decision**: existing single-repo layout; scripts in `scripts/`, configs in `configs/`.

## Complexity Tracking

| Deviation | Why | Simpler alternative rejected because |
|---|---|---|
| Compact JSON instead of `save_project`'s indent=2 | 11.5 MB vs 2.9 MB | Committing 11.5 MB crosses the PMTiles trigger for no reader benefit. |
| Time slider bound in-app | No Python builder in 3.2.0 | A hand-written plugin blob ties the file to an undocumented schema. |
| Action date stands in for announced date | Column absent | Inventing an announced date would be fabrication. |
