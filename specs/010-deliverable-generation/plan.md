# Implementation Plan: Templated Deliverable Generation

**Branch**: `010-deliverable-generation` | **Date**: 2026-10-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/010-deliverable-generation/spec.md`, plus the
spec 013 plate-slot contract kept below, and the session 7 instructions: a
client-neutral base template built from scratch, optional per-client templates,
numbers read at run time only, one definitions file, `validate.py --original`.

## Summary

One shared loader, `scripts/report_data.py`, reads every number a deliverable
prints from committed platform files at run time and records where each one
came from (file, key, column, file hash, commit SHA). Four thin front ends use
it:

- `scripts/render_location_report.py` fills `templates/location_report.docx`
  (client-neutral, Hawthorn styles, built from code by
  `scripts/build_report_templates.py`) with docxtpl, or a client template from
  `templates/clients/<client>/location_report.docx` when `--client` is given.
  Every client template binds the same fields; the renderer refuses one that
  names an unknown field or drops a required one. It writes the DOCX, a facts
  sidecar for the re-derivation check, and a markdown twin under
  `deliverables/` so the spec 004 Vale rules apply.
- `scripts/render_county_pdf.py` renders the same facts through an HTML
  template with WeasyPrint, tables from Great Tables.
- `scripts/charts.py` holds the Altair chart definitions; vl-convert renders
  them to SVG and PNG for Word and PDF and to a Vega-Lite embed for web pages.
  Colors come from `viz-palette.json`, a mirror of `viz-palette.js` that the
  selftest keeps in sync.
- `scripts/md_to_docx.sh` runs the Pandoc command-line program against
  `templates/reference.docx`.

Statistical terms are defined on first use from `configs/definitions.json`.
A county with no enacted restriction gets one plain sentence and no rate.
Both renderers take the optional `--plate` flag exactly as the spec 013
contract below describes. Research: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.11; POSIX shell for `md_to_docx.sh`

**Primary Dependencies**: docxtpl 0.20.2 (LGPL-2.1, imported unmodified), python-docx 1.2.0 (via docxtpl; builds the templates), Great Tables 1.0.0, WeasyPrint 70.0, Altair 6.3.0 with vl-convert-python 1.9.0.post1, Jinja2 (via docxtpl). Pandoc 3.5 or later as a command-line program only. All pinned in `requirements/ci.in` and compiled into `ci.txt` with no existing pin moved.

**Storage**: Files only. Inputs are committed CSV and JSON files (data-model.md). Outputs go to `outputs/location_reports/`, `outputs/county_pdfs/`, `outputs/charts/` and `outputs/briefs/` (all gitignored deliverable artifacts), `deliverables/location_reports/*.md` (Vale-linted twin), `templates/*.docx` (built from code) and `viz-palette.json`. All are declared in Layer E of `configs/layers.json`.

**Testing**: `--selftest` on every new module (discovered by `tests/test_selftests.py`), with in-memory fixtures for a county with and one without an enacted restriction, plate refusals, client-template field checks, first-use definitions and the re-derivation check. The real Spalding render is checked with `validate.py --original` (docx skill) with and without a plate.

**Target Platform**: Price's Mac for the Word report (local, reviewed before sending); GitHub-hosted Ubuntu for PDFs and charts through a `workflow_dispatch` workflow that uploads artifacts and commits nothing.

**Project Type**: CLI scripts over a file-based data platform

**Performance Goals**: A location report renders end to end in under a minute (SC-001). Measured on the Spalding fixture in research R8.

**Constraints**: No number typed into a template or into code (Principle VI); group-level outcome columns and county rates refused; vocabulary and em-dash checks on rendered text; LF endings; Pandoc never imported; no Pro or paid service.

**Scale/Scope**: One report per engagement (four counties so far); the loader covers any of the 3,144 scored counties.

## Constitution Check

*GATE: passed before Phase 0 research; re-checked after Phase 1 design (no change).*

| Principle | How this plan complies |
|---|---|
| I Defensibility | Every case row prints its own source link. Unverified grades print as recorded ("Blocked (unverified)"). The interval is always printed beside the score, and the definition explains that the two come from different calibrations. |
| II Vocabulary | Rendered text passes `leak_audit.LEAK_RE` and the em-dash check before a file is written; `deliverables/*` joins the leak audit's blocking tier. Outcome labels come from `OUTCOME_LABEL`. |
| III Legislative | Not touched; bills are not part of the report. |
| IV Descriptive | The score definition says it is a resemblance measure, not a forecast; the sources section carries the descriptive-not-causal notice from `configs/definitions.json`. |
| V Calibrated | Scores print with their Venn-Abers interval, never alone. No new model. |
| VI Reproducibility | Numbers come only from the loader at run time; templates carry no digits (selftest). The facts sidecar lists file, key, column and SHA-256 for each number, and `--verify` re-derives them from the CSVs. No county rate and no group-level column is loaded (allowlist per file, refusal list from `export_geolibre`). A county with no enacted restriction prints no rate. |
| VII Additive | New files; registry and layer entries appended; `leak_audit.GENERATED` gains one pattern. |
| VIII Layer ownership | Each output directory has one writer, declared in Layer E; `ARCHITECTURE.md` updated in the same change. |
| IX Selftested | `--selftest` on `report_data.py`, `build_report_templates.py`, `render_location_report.py`, `render_county_pdf.py`, `charts.py`; the pipeline selftest step installs their packages. |
| Gates 1 to 6 | Leak audit 0 blocking, layer audit 0 undeclared, selftests, `node --check` (no JS touched), no em-dash or CRLF, DOCX passes `validate.py --original` (both plate variants). |

## Project Structure

### Documentation (this feature)

```text
specs/010-deliverable-generation/
├── spec.md
├── plan.md              # this file (keeps the spec 013 plate contract below)
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md               # the four commands, flags, exit codes, refusals
│   ├── template_fields.md   # the field contract every template binds to
│   └── facts_sidecar.md     # facts JSON and the re-derivation check
└── tasks.md
```

### Source Code (repository root)

```text
configs/definitions.json              shared statistical definitions (FR-004)
configs/integrations.json             docxtpl, great-tables targets; python-docx, jinja2 entries
configs/layers.json                   Layer E: every new output
templates/manifest.json               base template tag, section order, required fields
templates/location_report.docx        built by scripts/build_report_templates.py
templates/reference.docx              Pandoc reference doc, same Hawthorn styles
templates/county_profile.html.j2      HTML template for the PDF
templates/README.md                   client template rules
templates/clients/<client>/           optional client templates (none committed)
scripts/report_data.py                facts loader, definitions, plate contract, checks
scripts/build_report_templates.py     builds both .docx templates
scripts/render_location_report.py     Word report (US1)
scripts/render_county_pdf.py          PDF (US2)
scripts/charts.py                     Altair specs, vl-convert export, palette mirror (US3)
scripts/md_to_docx.sh                 Pandoc CLI wrapper (US4)
viz-palette.json                      mirror of viz-palette.js
docs/chart_standard.md                adopted Urban Institute rules (US5)
deliverables/README.md                what lives here and why Vale lints it
leak_audit.py                         deliverables/* in the blocking tier
ARCHITECTURE.md                       Layer E writers named
requirements/ci.in, ci.txt            new pins, no existing pin moved
.github/workflows/pipeline.yml        selftest step installs the new packages and Pango
.github/workflows/render-county-pdf.yml   workflow_dispatch, artifacts only
```

**Structure Decision**: Scripts live in `scripts/` beside `render_terrain_plate.py`,
which they share helpers with (`export_geolibre` for the commit SHA, palette and
refused columns). Templates get their own top-level `templates/` directory, as
the spec names it.

## Note from spec 013 (terrain plates): the plate image slot

`scripts/render_location_report.py` and `scripts/render_county_pdf.py` accept
an optional `--plate outputs/plates/<id>.png`. When it is given:

1. **Slot.** The plate goes in one fixed image slot: full text width, directly
   under the report title block, aspect ratio preserved (plates render at
   3:2 by default, 3000x2000 px at 300 dpi = 10x6.67 in). The renderer never
   crops, recolors or annotates the image (spec 013 SC-002: zero hand edits).
2. **Caption.** Read from the sibling sidecar `outputs/plates/<id>.json`:
   `title` is the caption's first sentence (it already states the finding);
   `subtitle` follows. No caption text is composed by the report renderer.
3. **Credit.** The sidecar's `credits` lines (USGS 3DEP with product and access
   date; the cases line with the commit SHA) print under the caption in the
   report's credit style. They must match `DATA_NOTICES.md`.
4. **Refusals.** The renderer fails if the sidecar is missing, if its
   `commit_sha` is not an ancestor of the report's own commit, or if
   `preview` is true and the report is not itself a draft.
5. **Absent flag.** Without `--plate` the layout is unchanged; both variants
   must pass `validate.py --original` (spec 013 US3 independent test).

## Complexity Tracking

No constitution violation. Choices that look heavier than needed:

| Choice | Why | Simpler alternative rejected because |
|---|---|---|
| Templates built from code, not by hand in Word | The builder is reviewable in a diff, reproducible and checked for typed numbers; a hand-saved .docx is an opaque binary. | Hand-built in Word: no review, and Word splits Jinja tags across runs. |
| A facts sidecar per report | SC-002 asks for a zero-mismatch re-derivation; the sidecar names the cell each number came from so the check is mechanical. | Re-parsing numbers out of the DOCX text: ambiguous (0.17 could be any of several fields). |
| `viz-palette.json` mirror | Python renderers and Altair need the palette; the regex reader stays the single parser and the selftest fails on drift. | Reading `viz-palette.js` in every renderer: three regex copies. |
