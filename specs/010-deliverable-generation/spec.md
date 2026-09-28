# Feature Specification: Templated Deliverable Generation

**Feature Branch**: `010-deliverable-generation`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 7 of the tool integration plan: docxtpl, WeasyPrint, Great Tables, Altair with vl-convert, Pandoc (command-line only), with the Urban Institute chart guide as reference. Deferred entries that attach here once triggered: Quarto, osmnx, FEMA NRI, gridstatus and ERCOT context, National Zoning Atlas context, Docling (ordinance terms database), vis-timeline, BERTopic.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Location reports render from data into the Word template (Priority: P1)

`scripts/render_location_report.py` fills `templates/location_report.docx` (built from the 2026-09-23 Vantage location report template) using docxtpl, with every number read from current platform files at run time: county score, decile, interval, enacted-restriction history, nearby project cases. The output passes `validate.py --original` and the spec 004 Vale rules. The script runs locally; Price reviews before sending.

**Why this priority**: Location reports (Woodland CA, Morris IL, Spalding GA, Lackawanna PA) are the recurring client deliverable, and hand-built numbers are where drift enters (Principle VI).

**Independent Test**: Render a report for FIPS 13255 (Spalding GA) and confirm every numeric field matches a re-derivation from the CSVs.

**Acceptance Scenarios**:

1. **Given** a county with no enacted restriction, **When** rendered, **Then** the history section states that plainly and no rate is printed (Principle VI).
2. **Given** a statistical term (calibrated score, decile, interval), **When** rendered, **Then** its definition appears on first use from a shared definitions file.

### User Story 2 - County profile PDFs are generated (Priority: P2)

`scripts/render_county_pdf.py` renders a PDF per requested FIPS with WeasyPrint from an HTML template that shares data and styles with `county-profile.html`. Tables use Great Tables. The PDF footer records data vintage and commit SHA.

**Independent Test**: Render three FIPS; confirm the PDFs open, carry the footer, and contain no em-dashes.

### User Story 3 - One chart definition serves every product (Priority: P2)

`scripts/charts.py` defines report charts in Altair (score distribution with the county marked, enactment timeline, peer comparison with intervals). vl-convert renders each to PNG and SVG for Word and PDF, and the same spec renders interactively on web pages and client portals. Colors come from `viz-palette.js` values mirrored into a shared JSON file.

**Why this priority**: Static reports and active databases should show the same chart the same way. Today, web charts are Chart.js and report charts are built separately.

**Independent Test**: Render one chart spec to SVG and to an HTML embed from the same fixture; confirm identical data values in both.

### User Story 4 - Markdown briefs become branded Word files (Priority: P2)

`scripts/md_to_docx.sh` runs Pandoc with `templates/reference.docx` (Hawthorn styles) to turn markdown briefs, such as the county static reports in the project, into Word. Pandoc runs as a separate program and is never imported, so its GPL license does not attach to repo code. Output passes `validate.py --original` and Vale.

**Independent Test**: Convert the Lackawanna County static report markdown and confirm the heading styles map to the reference template and no em-dashes appear.

### User Story 5 - Charts follow one reviewed standard (Priority: P3)

A short `docs/chart_standard.md` records which Urban Institute guide rules the platform adopts (titles as findings, direct labels, source lines, uncertainty shown as ranges) as they map onto `viz-palette.js`. `viz-palette.js` remains the canonical color source.

### Edge Cases

- Template edits by hand break docxtpl tags: the renderer validates that all expected tags are present before filling.
- A figure would need a group-level outcome column: the renderer refuses; those columns are permanently internal.
- WeasyPrint system libraries are missing locally: the script prints the install line and exits non-zero.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Renderers MUST read only from committed platform files and MUST record the commit SHA used.
- **FR-002**: docxtpl output MUST follow the docx rules: `w14:paraId` below `0x80000000`, explicit `<w:tblGrid>`, hyperlinks as siblings of runs.
- **FR-003**: Every rendered document MUST pass Vale (spec 004) and the leak audit before it is considered final.
- **FR-004**: Statistical definitions MUST come from one shared file used by all renderers.
- **FR-005**: Chart specs MUST live in one module used by both static renderers and web pages.
- **FR-006**: Pandoc MUST be invoked only as a subprocess or shell command.

## Success Criteria *(mandatory)*

- **SC-001**: A location report renders end to end in under a minute, with zero manual number entry.
- **SC-002**: A re-derivation check reports zero mismatches between rendered numbers and CSV values.
- **SC-003**: Rendered DOCX files pass `validate.py --original`.

## Assumptions

- The Vantage location report template in the project docs is the base; Price confirms section order before the template is tagged.
- Client-specific framing enters as config or template text, never as a bespoke code path.
