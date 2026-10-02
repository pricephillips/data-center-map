# Tasks: Templated Deliverable Generation

**Input**: [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [spec.md](./spec.md)

**Status**: implemented 2026-10-02 (session 7). T026 runs before the push.

**Tests**: Each module ships `--selftest`, which `tests/test_selftests.py` discovers (Principle IX). No separate pytest files.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an open task)
- **[Story]**: US1 to US5 from spec.md

## Phase 1: Setup

- [x] T001 Pin docxtpl, great-tables, weasyprint, altair and vl-convert-python in `requirements/ci.in`. Recompile `requirements/ci.txt` and confirm no existing pin moved.
- [x] T002 [P] In `configs/integrations.json`:
  - update the docxtpl reason (client-neutral base, client templates as add-ons) and its targets;
  - narrow the great-tables target to the PDF renderer;
  - register python-docx and Jinja2, which are imported directly.
- [x] T003 [P] In `configs/layers.json`, declare every new output in Layer E: `outputs/location_reports/*`, `outputs/county_pdfs/*`, `outputs/charts/*`, `outputs/briefs/*`, `deliverables/location_reports/*.md`, `templates/location_report.docx`, `templates/reference.docx`, `viz-palette.json`.
- [x] T004 [P] Add `deliverables/*` to `leak_audit.GENERATED`, which is the blocking tier.
- [x] T005 Update spec 010 Assumptions: the base is client-neutral, and the Vantage template becomes a future client add-on under `templates/clients/`.

## Phase 2: Foundational (blocks US1 to US3)

- [x] T006 Write `configs/definitions.json`: calibrated score, Venn-Abers interval, decile, national and state percentile, peer median, enacted restriction, tracked case, outcome grade, and the descriptive notice. Definitions contain no digits.
- [x] T007 Write `templates/manifest.json`: tag `location_report-v1`, the section order Price confirmed on 2026-10-02, required and optional fields.
- [x] T008 Write `scripts/report_data.py`. It provides:
  - the Fact and Facts loader, with column allowlists and the refused-column set;
  - formatting rules;
  - history through the `county_aggregator` rule, cases, neighbors and score;
  - commit SHA and as-of date;
  - `define()` with first-use state;
  - `load_plate()` with the four refusals;
  - text checks (vocabulary, em-dash, first-use, no-rate);
  - DOCX XML checks (FR-002);
  - `--selftest`.
- [x] T009 [P] [US3] `scripts/charts.py --sync-palette` and `viz-palette.json`. The selftest fails on drift from `viz-palette.js`.
- [x] T010 Write `scripts/build_report_templates.py`. It applies Hawthorn styles and builds `templates/location_report.docx` (sections from the manifest, plate slot under the title block) and `templates/reference.docx` (Pandoc style set). It supports `--check` and `--selftest`, which fails on a typed digit or a missing required field.

## Phase 3: User Story 1, location reports (P1, MVP)

**Goal**: A Word report for any scored FIPS, with every number read at run time.
**Independent test**: Render FIPS 13255, then run `--verify` on its facts file and get zero mismatches. `validate.py --original templates/location_report.docx` passes.

- [x] T011 [US1] Write `scripts/render_location_report.py`:
  - context from `report_data`;
  - docxtpl fill;
  - RichText links in `{{r}}` form;
  - InlineImage for the plate and chart;
  - text and XML checks before writing;
  - the facts sidecar;
  - the markdown twin under `deliverables/location_reports/`.
- [x] T012 [US1] Add `--client`, which validates the name and checks the field contract against the manifest. Refuse unknown or missing fields.
- [x] T013 [US1] Add `--plate`, implementing the spec 013 contract: fixed slot at text width, sidecar caption and credits, four refusals, layout unchanged without it.
- [x] T014 [US1] Add `--verify`, the re-derivation check (contracts/facts_sidecar.md).
- [x] T015 [US1] Write the `--selftest`. It covers:
  - an enacted and a no-enacted fixture county, where the no-enacted history has no digits and no rate;
  - first-use definitions;
  - client template refusal;
  - plate present, absent, missing sidecar, non-ancestor SHA, and preview on a final report;
  - a refused column;
  - the FR-002 XML rules.
- [x] T016 [US1] Render Spalding (13255) with and without a fixture plate. Run `validate.py --original` on both, and run `--verify` and Vale on the markdown twin. Record the results and timing in quickstart.md.

## Phase 4: User Story 2, county PDFs (P2)

**Independent test**: Render three FIPS. Each PDF opens, carries the footer, and contains no em-dash.

- [x] T017 [US2] Write `templates/county_profile.html.j2` (print CSS, palette from `viz-palette.json`, `@page` footer with as-of date and SHA).
- [x] T018 [US2] Write `scripts/render_county_pdf.py`: Great Tables for the tables, chart SVG inline, `--plate`, and a WeasyPrint library check with the install line. Add a `--selftest`.
- [x] T019 [US2] Add `.github/workflows/render-county-pdf.yml`: `workflow_dispatch`, FIPS input validated through env, actions pinned to SHAs, uv install with `-c requirements/ci.txt`, Pango from apt, artifacts only.

## Phase 5: User Story 3, one chart definition (P2)

**Independent test**: One spec renders to SVG and to an HTML embed with identical data values.

- [x] T020 [US3] Add the Altair specs to `scripts/charts.py`: score distribution with the county marked, enactment timeline, peer comparison with intervals. Titles state the finding. Export SVG, PNG and HTML. The selftest checks that the SVG and HTML data values are equal.

## Phase 6: User Story 4, markdown to Word (P2)

**Independent test**: Convert a markdown brief. The headings map to the reference styles and no em-dash appears.

- [x] T021 [US4] Write `scripts/md_to_docx.sh`: Pandoc as a subprocess, `--reference-doc templates/reference.docx`, em-dash refusal, optional `DOCX_VALIDATE`.
- [x] T022 [US4] Write `deliverables/README.md`, linted by Vale. Convert the Spalding markdown twin as the worked example.

## Phase 7: User Story 5, chart standard (P3)

- [x] T023 [US5] Write `docs/chart_standard.md`: the adopted Urban Institute rules mapped to `viz-palette.js`, which was reviewed only.

## Phase 8: Polish and cross-cutting

- [x] T024 [P] `.github/workflows/pipeline.yml`: the selftest step installs the new packages and Pango; Vale lints `deliverables/`.
- [x] T025 [P] `ARCHITECTURE.md`: name the new Layer E writers. Add `templates/README.md`.
- [ ] T026 Merge `origin/main`. Run `pre-commit run --all-files`, `python -m pytest tests/test_selftests.py`, `python leak_audit.py --tier blocking`, `python layer_audit.py --strict --no-write` and `python integration_audit.py`.

## Dependencies

- Setup (T001 to T005) comes before Foundational (T006 to T010).
- Foundational comes before US1, US2 and US3.
- US1 needs T009 for the chart image. US2 reuses `report_data` and `charts`.
- US4 needs T010 for `reference.docx`. US5 is independent.

## Parallel examples

- T002, T003, T004 together.
- After T008: T009 and T010 together. Then T017 (US2) and T020 (US3) beside US1.

## Implementation strategy

The MVP is US1 on Spalding: a validated, verified Word report with and without
a plate. Then the PDF and charts, which share the loader, then Pandoc and the
standard.
