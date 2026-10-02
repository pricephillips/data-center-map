# Tool Selection: Integration Plan

Decision record for the open-source scans of 2026-09-28 (Pass 1 about 70 tools, Pass 2 about 200), the selection pass, and the pass 4 re-review of every eliminated tool against the client product suite. The machine-readable version is `configs/integrations.json`, validated by `integration_audit.py`. When the two disagree, the registry governs and this file gets amended.

Result: 52 selected, 38 deferred with a trigger condition, 60 elimination entries (many cover several tools each). Every selected and deferred entry is tagged with the product lines it serves.

## Product lines

| Line | What it is | Examples |
|---|---|---|
| platform | Core pipeline, models, national map | clean feed, county model, two-mode map |
| static_report | Passive deliverables | location reports, county profile PDFs, briefs, newsletter content |
| active_db | Actively updated custom client databases | watchlist databases, alerts, filtered portals, exports |
| internal | Analyst tooling only | agent MCP servers, code search |

## Selection rules

A tool is selected only if it passes all of the following:

1. **License.** Permissive, weak copyleft used unmodified (MPL, LGPL), public domain, or a free public API. The repo has no LICENSE file, so it is all-rights-reserved, and client products may ship privately. GPL and AGPL code may therefore not be imported by repo code. GPL or AGPL programs may run as separate command-line tools (license tag `*-cli`, kind `cli`).
2. **Maintenance.** A release within the last 18 months. The only exception is a stable, tiny library (topojson-client).
3. **Fit.** It closes a named gap in the platform or a named client product need. It does not duplicate a module or a tool already selected, and it does not restructure the module-per-step pipeline. Product-layer tools sit downstream of the pipeline and only read from it.
4. **Principles.** No deep learning, no causal framing, no detection-biased variables entering a model unflagged, nothing that auto-writes `master_opposition.csv`. Context-only sources (zoning, hazards, grid) may appear in reports as labeled context and never as model features.
5. **Burden.** Installation effort is acceptable when it buys a product capability. A tool is ruled out only if it is unnecessarily burdensome: it needs a standing server where a library or static file does the same job, or it adds a heavy runtime (torch, a GPU) for a marginal gain.
6. **Make versus take.** If an in-house version is under about 150 lines and removes a dependency or a license risk, build it in-house.

## Selected, by session

| Session | Spec | Tools |
|---|---|---|
| 0 | agent setup | GitHub MCP, Census Bureau Data API MCP, ast-grep, DuckDB CLI |
| 1 | 004 CI hygiene and gates | uv, ruff (blocking on syntax only), actionlint, zizmor, pre-commit, pytest selftest runner, Vale (Hawthorn style), daff, Dependabot; wire `integration_audit.py` |
| 2 | 005 data quality | Pandera; in-house state normalizer, column coverage delta gate, out-of-fold label disagreement audit; MIT Election Lab county returns replacing `county_votes.json` |
| 3 | 006 source durability | Internet Archive SPN2 and CDX, trafilatura, datasketch MinHash |
| 4 | 007 coverage | civic-scraper, OCRmyPDF, Congress.gov API, Epoch AI Frontier Data Centers, LangExtract (local only) |
| 5 | 008 models | firthmodels, esda with libpysal (diagnostics), InterpretML EBM via interpret-core (challenger), skops model cards. netcal was eliminated in session 5 (torch dependency); the gate computes ECE and MCE in-house. |
| 6 | 009 frontend | Census 2024 county boundaries, mapshaper, topojson-client, MapLibre with maplibre-gl-leaflet, OpenFreeMap, Tabulator, Playwright, axe-core; in-house date slider and detail panel |
| 7 | 010 static reports | docxtpl, WeasyPrint, Great Tables, Altair with vl-convert, Pandoc (CLI); Urban Institute chart guide as reference |
| 8 | 011 client databases | Datasette, sqlite-utils, Frictionless Data Package, Apprise |

Order rationale: session 1 builds the gates every later session is checked against. Sessions 2 and 3 protect the data before new sources in session 4 add volume. Session 5 uses the cleaner labels. Session 6 fixes a live client-visible defect and can move ahead of 2 to 5 if a deliverable needs it. Sessions 7 and 8 build the two client product lines on top of the gates, the geometry and the cleaner data. Session 8 can run before 5 if a client database pilot is needed sooner, since it only reads platform outputs.

## Pass 4: tools reinstated after re-review

Re-reviewed against the client suite (passive static reports and actively updated custom databases). The bar was "not unnecessarily burdensome", not "easy to install".

**Moved to selected (6 reinstated, 1 new):**
- **Datasette + sqlite-utils** (session 8): per-client read-only databases with faceting, search and CSV/JSON endpoints, built from one builder plus a client config. This is the core of the active database product.
- **Frictionless Data Package** (session 8): every client export carries its schema, definitions, sources and required attributions.
- **Apprise** (session 8): per-client watchlist alerts to email, Slack or Teams, as a library with no server.
- **Altair + vl-convert** (session 7): one chart definition rendered in Word, PDF and web alike.
- **Pandoc** (session 7): markdown briefs to branded Word. Previously blocked as GPL; allowed now because it runs as a separate program.
- **MIT Election Lab county returns** (session 2): new, prompted by the notices audit finding that `county_votes.json` is scraped and non-authoritative.

**Moved to deferred with a client trigger (21), plus 3 newly registered:** Observable Framework (portal apps, chosen over Evidence), Quarto, MJML, Pagefind, Grist (editable client records), dlt (client-supplied feeds), dbt-duckdb (many divergent client views, downstream only), Docling (ordinance terms database), ArchiveBox with SingleFile CLI (evidentiary copies), LegiScan, Federal Register API (federal large-load policy tracking), gridstatus, ERCOT and National Zoning Atlas as site-report context only, FEMA NRI, BLS QCEW context, osmnx (site proximity), BERTopic (newsletter concerns), PMTiles, Perspective, Healthchecks. Newly registered: changedetection.io (client pages with no API), vis-timeline (lifecycle timelines), Cloudflare Pages + Access (private client hosting; GitHub Pages cannot restrict access).

**Confirmed eliminated on re-review:** imported GPL and AGPL libraries (cleanlab, deepchecks, scikit-survival, addfips); server-heavy BI and annotation stacks (Metabase, Superset, Argilla, Label Studio, Streamlit); stale front-end plugins (Leaflet.TimeDimension, leaflet-sidebar-v2, Pym.js; iframe-resizer v5 is GPL); defensibility risks (Bluesky, Leaflet.heat, us-atlas, generation queues as a baseline); pipeline restructures (dbt or SQLMesh as the core pipeline, Hamilton); principle conflicts (TabPFN, causal libraries); and tools covered by a selected choice.

## Product ideas the tools make possible

- **Ordinance terms database** (active_db, static_report): structured setbacks, noise limits, water and acreage caps from enacted ordinances. Docling plus LangExtract with character-offset grounding; deferred until scoped.
- **Watchlist databases with alerts** (active_db): spec 011.
- **Site report context sections** (static_report): proximity (osmnx), hazards (FEMA NRI), grid context (gridstatus), zoning context where mapped. All labeled as context and never model features.
- **Federal policy tracker for developer clients** (active_db): Federal Register API; outside the opposition tracker itself.

## Risk findings

- **13 scored counties never render.** The choropleth geometry (`plotly/datasets geojson-counties-fips.json`) predates 2022 and lacks the nine Connecticut planning regions, Alaska 02063, 02066 and 02158, and Oglala Lakota SD 46102. Fixed in spec 009, story 1.
- **Basemap terms.** `basemap.js` uses Esri World Gray Canvas and OSM raster tiles, whose terms are a risk for commercial client use. Replaced by OpenFreeMap with fallbacks (spec 009, story 2).
- **Political data source.** `county_votes.json` is scraped from news sites (notices audit). Replaced in spec 005, story 5.
- **No repo LICENSE.** The repo is all-rights-reserved by default. That keeps private client products possible, and it is why GPL and AGPL imports are blocked. Adding an explicit proprietary notice is Price's decision.
- **Pass 1 queue framing withdrawn.** Generation queues list power plants, not data centers (`docs/interconnection_queue_scoping.md`).
- **HIFLD Open** was discontinued in 2025.
- **Python version.** Decided in spec 008 (2026-10-01): stay on 3.11. esda 2.9.0 and libpysal 4.14.1 support 3.11, so no workflow moves. Bambi still needs 3.12. The Dependabot ignores on numpy >=2.5 and scipy >=1.18 come off with a later repo-wide 3.12 move.
- **Client data exposure.** Client configs and databases must never be committed to the public repo (spec 011, edge cases).

## Elimination categories

| Category | Examples |
|---|---|
| already built | NASS, EIA price, USGS water, Drought Monitor, PUDL generation (`fetch_county_features.py`) |
| redundant with existing | Polars, TOON, XGBoost/LightGBM, MLflow/DVC, obra/superpowers (Spec Kit governs) |
| redundant with a selected tool | Great Expectations, Scrapy, Crawl4AI, Instructor/DSPy, Repomix, Lighthouse CI, Evidence, Typst |
| license risk | cleanlab, deepchecks, scikit-survival, addfips (imported copyleft), relplot, Esri and OSM raster tiles for commercial use |
| maintenance risk | firthlogist, Leaflet.TimeDimension, leaflet-sidebar-v2, Pym.js, HIFLD Open |
| principle conflict | dbt/SQLMesh/Hamilton as the core pipeline, TabPFN, DoWhy/EconML |
| defensibility risk | gridstatus as a baseline, Bluesky, mgwr/spopt, us-atlas, Leaflet.heat |
| cost or infrastructure | Argilla, Label Studio, Metabase, Superset, torch NLP in CI, Council Data Project, Common Crawl |
| out of scope | GovInfo/Regulations.gov, forecasting libraries, LLM tracing |

## How each session starts

1. `python3 integration_audit.py --session N` lists that session's tools, pins, licenses and targets.
2. `/speckit-plan` on `specs/NNN-*/spec.md`, then `/speckit-tasks`, `/speckit-analyze`, `/speckit-implement`.
3. Constitution Check before shipping: leak audit, layer audit, selftests, `node --check`, em-dash and CRLF checks, and docx validation where relevant.
4. When a deferred trigger fires, move the entry to `selected`, add `version`, `spec`, `session` and `target`, and rerun `integration_audit.py`.
