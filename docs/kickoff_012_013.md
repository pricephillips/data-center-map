# Claude Code kickoff: specs 012 and 013 (GeoLibre projects, forge3d terrain plates)

Paste the block below into Claude Code from `~/data-center-map`. Run spec 012 first; 013 depends on its registry and audit changes.

```
Session goal: implement specs/012-geolibre-map-projects and specs/013-terrain-plates.

Setup
1. Pull fresh main. Verify against live files in the checkout, not memory or CDN copies.
2. Read: specs/012-geolibre-map-projects/spec.md, specs/013-terrain-plates/spec.md,
   specs/009-map-geometry-frontend/plan.md, specs/010-deliverable-generation/spec.md,
   configs/integrations.json, configs/layers.json, configs/surfaces.json,
   DATA_NOTICES.md, ARCHITECTURE.md, docs/tool_selection.md.
3. pip install geolibre==3.2.0 "geolibre[vector]" forge3d==1.40.1 in a local venv
   (Python 3.11). Read the installed sources for geolibre/authoring.py and
   geolibre/project.py and forge3d's public API before planning; use those, not
   recall, for every call signature.

Spec Kit flow, per spec
/speckit-plan, then /speckit-tasks, then /speckit-implement. Write research.md
findings for: GeoLibre self-hosting (012 US3), 3DEP access method and license of
any client library chosen (013 US1), and the four-leg render probe with its
decision rule and fallback (013 US4).

Hard rules
- No full MapLibre rewrite, no heatmap or density layers, no 3D extrusion of records.
- Nothing uploaded to share.geolibre.app or any public host.
- No forge3d Pro APIs: set_license_key, MapPlate, vector export, building import.
- Outcome vocabulary only: advanced_confirmed, restricted_conditional,
  blocked_confirmed, pending (plus the defensibility tiers as already used).
  No scorekeeping vocabulary (win, loss, won, lost) anywhere.
- Never expose decided, confirmed_blocks, blocked_share, or site_screener composite fields.
- calibrated_score is a resemblance measure, not a forecast; define statistical
  terms on first use in any client-readable text.
- Inputs from committed files only; record commit SHA in every output.
- Additive and backward compatible. No renames. raw.githubusercontent.com first in
  every fetch chain. LF endings. No em-dashes.
- Register every new file in configs/layers.json (data) or configs/surfaces.json (pages).
- Do not commit or push. Leave changes in the working tree for Price.

Gates before reporting done
python integration_audit.py --selftest
python leak_audit.py --tier blocking
python layer_audit.py
python visibility_audit.py
python scripts/export_geolibre.py --selftest
python scripts/render_terrain_plate.py --selftest
python scripts/plate_freshness.py --selftest
node --check on every touched .js, then every *_selftest.js
npx playwright test (tests/ui) for the Leaflet changes
em-dash and CRLF checks across changed files

- Do not set up a self-hosted runner. The repo is public; record it as an option only.

Report back
A short list: files added or changed, gate results, the probe leg chosen (or
the local-render fallback),
and any blocker. No narrative.
```
