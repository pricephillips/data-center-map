# GeoLibre map projects (spec 012)

`scripts/export_geolibre.py` writes four GeoLibre project files to
`data/geolibre/` from committed platform files. Each file is a whole map:
layers, symbology, popups, legend and (for the swipe project) the split-map
control. They are internal, analyst-facing files until spec 011 provides
access-controlled client hosting.

| File | What it shows |
|---|---|
| `national_restriction_model.geolibre.json` | County choropleth of calibrated score deciles (3,144 counties). |
| `national_opposition_cases.geolibre.json` | Opposition case pins by outcome tier, clustered below zoom 7. |
| `score_vs_politics_swipe.geolibre.json` | Score deciles on the left of a swipe divider, 2024 presidential margin on the right. |
| `opposition_timeline.geolibre.json` | Pins with a verified action year (`action_year` property) for the Time Slider. |

## Rebuilding

```
python3.11 -m venv .venv-geolibre
.venv-geolibre/bin/pip install "geolibre[vector]==3.2.0"
.venv-geolibre/bin/python scripts/export_geolibre.py
```

The export reads tracked files only and stamps the commit SHA into every
project's description and `metadata.commit_sha`. It fails on an untracked
input; `--allow-uncommitted` exists for a preview before commit and stamps the
SHA `-uncommitted`, so re-run it after committing. `--selftest` runs the
fixture checks (allowlist, refusals, vocabulary, redaction, reload).

Popup and tooltip fields come only from `configs/geolibre_export.json`.
Group-level outcome columns and every `site_screener.py` composite field are
refused even if that file names them.

## Opening a project (US3)

Nothing is uploaded. Open the local file in either place:

- **GeoLibre desktop app**: File, Open project, choose the `.geolibre.json`.
- **Jupyter widget** (`pip install "geolibre[vector]==3.2.0"`):

  ```python
  from geolibre import Map
  m = Map()
  m.load_project("data/geolibre/score_vs_politics_swipe.geolibre.json")
  m
  ```

Do not use any share or publish action in the app. Sharing goes to a public
host, which is forbidden for client data (`configs/integrations.json` entry
`geolibre-share`, enforced in code by `integration_audit.py`). The basemap is
the app default; no credentials are stored in the projects (they are passed
through GeoLibre's `redact_credentials` before writing).

The timeline project carries `action_year` on every pin. GeoLibre 3.2.0 has no
Python builder for Time Slider state, so bind it in the app: Layers, select
the layer, Bind property, `action_year`.

## Self-hosting (deferred)

Registry entry `geolibre-selfhost` (deferred) records the trigger: an
active_db client engagement needs an interactive analyst workspace behind
access control. Notes are in `specs/012-geolibre-map-projects/research.md`
(R3).

## MCP server for Claude Code sessions (US5)

Agent setup only; nothing in the repo depends on it. On Price's machine:

```
python3.11 -m venv ~/.venvs/geolibre-mcp
~/.venvs/geolibre-mcp/bin/pip install "geolibre[mcp]==3.2.0"
claude mcp add geolibre -- ~/.venvs/geolibre-mcp/bin/geolibre-mcp
```

`geolibre-mcp` is the console script geolibre 3.2.0 installs (entry point
`geolibre.mcp:main`; `python -m geolibre.mcp` is equivalent). The server
rewrites the whole project file on every edit, so point it at a copy
when experimenting and re-run the exporter to get back to the committed state.
