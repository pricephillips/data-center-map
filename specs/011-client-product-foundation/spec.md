# Feature Specification: Client Product Foundation (Custom Databases, Exports, Alerts)

**Feature Branch**: `011-client-product-foundation`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 8 of the tool integration plan: Datasette, sqlite-utils, Frictionless Data Package, Apprise. It sets up the "actively updated custom database" product line alongside the static reports in spec 010. Deferred entries that attach here once triggered: Cloudflare Pages + Access (private hosting), Grist (editable records), dlt (client-supplied feeds), dbt-duckdb (many divergent client views), changedetection.io (pages with no API), Pagefind, Perspective, Observable Framework, Healthchecks.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A client database builds from a config, not from code (Priority: P1)

A client is defined by `clients/<client>/client.json`: jurisdictions (FIPS, states), project ids or site coordinates with a radius, which layers to include (events, projects, county scores, bills, meetings), and which fields are visible. `scripts/build_client_db.py` reads the clean feed and platform outputs, applies the config, and writes `clients/<client>/<client>.db` with sqlite-utils: typed columns, foreign keys between projects and events, and full-text search on summaries. Datasette serves it with faceting and CSV and JSON endpoints.

**Why this priority**: Client engagements enter the system as configs, frames or data rows, never as bespoke code paths. One builder and one config per client keeps every client database consistent with the national data.

**Independent Test**: Build a database from a fixture config covering two counties and confirm it contains only those counties' rows, the configured fields, and a working full-text index.

**Acceptance Scenarios**:

1. **Given** a config that lists FIPS 51145 and 08005, **When** the build runs, **Then** only events, projects and scores for those counties appear.
2. **Given** a config that requests a group-level outcome column (`decided`, `confirmed_blocks`, `blocked_share`), **When** the build runs, **Then** it refuses and names the column (Principle VI).
3. **Given** `ext_` records in the feed, **When** the build runs, **Then** they are excluded or labeled per the visibility matrix (`docs/visibility_matrix.md`).

### User Story 2 - Every export is a self-describing data package (Priority: P1)

Each build writes `clients/<client>/datapackage.json` (Frictionless): table schemas with field descriptions and the outcome ladder definitions, data vintage and commit SHA, sources, and every required attribution from `DATA_NOTICES.md` (Census sentence, CC-BY, ODbL share-alike where OSM-derived data is included). The build fails if `frictionless validate` fails.

**Why this priority**: Clients forward exports internally. A package that carries its own definitions and attributions keeps claims traceable (Principle I) and license notices intact outside our pages.

**Independent Test**: Validate the fixture build's package; remove one attribution and confirm the build fails.

### User Story 3 - Clients get alerts when their watchlist changes (Priority: P2)

`scripts/client_alerts.py` diffs each client's database against the previous build (daff output from spec 004) and sends a digest through Apprise to the client's configured channel (email, Slack or Teams webhook stored as a secret). Alert types: new verified event, outcome change, new hearing on a watched jurisdiction's agenda, new bill touching a watched state. Candidate items from harvest worklists appear only when labeled as unverified.

**Why this priority**: The value of an actively updated database is that the client hears about changes without checking it.

**Independent Test**: Build twice from fixtures with one outcome change and confirm exactly one alert payload, with activity-descriptive vocabulary, that passes the leak audit.

### User Story 4 - Builds run on a schedule and never block the pipeline (Priority: P2)

A new `client-builds.yml` runs after `pipeline.yml` succeeds (workflow_run), builds every client in `clients/`, and publishes artifacts. A failed client build fails only its own job. Client databases are never committed to the public repo; they are uploaded as workflow artifacts or pushed to the private host selected when Cloudflare Access (deferred) triggers.

### Edge Cases

- A client watchlist includes a county with no records: the database still builds, with county context and an explicit empty-events note.
- A config field name drifts from the feed: the build fails fast and lists unknown fields.
- A client sits in a public repo: client configs that name a client live in a private location or are encrypted; the public repo carries only a fixture client.
- Alert fatigue: digests are batched per client per day by default; the frequency is configurable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Client behavior MUST be expressed only in `client.json`; `build_client_db.py` contains no client names.
- **FR-002**: The builder MUST read platform outputs only and MUST NOT write any platform file (Principle VIII); client outputs are a new declared layer in `configs/layers.json`.
- **FR-003**: Every build MUST record commit SHA, data vintage and builder version inside the database and the data package.
- **FR-004**: The visibility matrix MUST be enforced at build time; internal-only columns never reach a client database.
- **FR-005**: Alert text MUST pass `leak_audit.py --tier blocking` and the spec 004 Vale rules before sending.
- **FR-006**: Secrets (webhooks, emails) MUST live in repository or environment secrets, never in `client.json`.
- **FR-007**: New modules MUST ship `--selftest` using fixture configs with no network.

### Key Entities

- **Client config**: jurisdictions, sites and radius, layers, visible fields, alert channel reference, cadence.
- **Client database**: a SQLite file per client, served by Datasette.
- **Data package**: the Frictionless descriptor that travels with every export.
- **Alert**: a digest of verified changes (and labeled candidates) since the last build.

## Success Criteria *(mandatory)*

- **SC-001**: A new client database goes from config to a browsable Datasette instance in under 10 minutes with no code change.
- **SC-002**: 100 percent of client exports pass `frictionless validate` with all attributions present.
- **SC-003**: Zero internal-only columns in any client database (checked by the builder's selftest and a post-build scan).
- **SC-004**: Alert payloads pass the leak audit in every run.

## Assumptions

- The first client pilot uses a fixture or internal config (for example the TVA 198-county frame) before any external client.
- Private hosting (Cloudflare Access or a small container host) is chosen when the first external client triggers it; until then databases are artifacts reviewed by Price.
- The repo has no LICENSE file and is all-rights-reserved; client products may therefore ship privately, which is why GPL and AGPL imports stay blocked.
