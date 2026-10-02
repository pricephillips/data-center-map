# Quickstart: Templated Deliverable Generation

**Feature**: `010-deliverable-generation`. Each step names the command and the
result to expect. Contracts: [contracts/cli.md](./contracts/cli.md),
[contracts/template_fields.md](./contracts/template_fields.md),
[contracts/facts_sidecar.md](./contracts/facts_sidecar.md).

## Setup

```bash
uv venv .venv && . .venv/bin/activate
uv pip install -r requirements/ci.txt         # docxtpl, great-tables, weasyprint, altair, vl-convert
# WeasyPrint needs Pango: brew install pango   |   sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0
# Pandoc (US4 only) is a command-line program: brew install pandoc
export DOCX_VALIDATE=/path/to/docx-skill/scripts/office/validate.py   # optional
```

## 1. Selftests (no network, no committed output)

```bash
python -m pytest tests/test_selftests.py -q -k "report_data or charts or build_report or render_"
```

Expected: `report_data.py` 38/38, `charts.py` 11/11, `build_report_templates.py` 13/13,
`render_location_report.py` 26/26, `render_county_pdf.py` 12/12.

## 2. Location report for Spalding County, GA (US1)

```bash
python scripts/render_location_report.py --fips 13255
python scripts/render_location_report.py --verify outputs/location_reports/13255_spalding.facts.json
vale deliverables/location_reports/13255_spalding.md
```

Expected:

- `outputs/location_reports/13255_spalding.docx`, `.facts.json` and the markdown twin
  `deliverables/location_reports/13255_spalding.md` are written.
- The re-derivation reports 0 mismatches, and Vale reports 0 errors and 0 warnings.
- The report says:
  - calibrated score 0.17;
  - interval 0.18 to 0.26;
  - decile 8;
  - 74th national and 79th state percentile;
  - peer median 0.20 across 10 peers;
  - one enacted restriction, the Griffin city moratorium of 2026-01-13, at level "city";
  - two tracked cases;
  - eight adjacent counties, seven of them with an enacted restriction.

These values are the data at commit `3a45260` and will move with the data.

**Measured 2026-10-02**: The render took about 1.5 s, chart included.
`validate.py ... --original templates/location_report.docx` printed "All
validations PASSED!" for both variants:

- without a plate: 153 → 117 paragraphs;
- with a plate: 153 → 121 paragraphs.

## 3. With a terrain plate (spec 013 contract)

```bash
python scripts/render_location_report.py --fips 13255 --plate outputs/plates/13255_spalding.png
```

Expected:

- The plate sits directly under the title block at full text width, with its
  aspect ratio kept.
- The caption is the sidecar's title followed by its subtitle.
- The two credit lines print verbatim.
- Without `--plate`, the paragraph sequence is unchanged.

The renderer refuses in four cases:

- the sidecar is missing;
- `commit_sha` is not an ancestor of HEAD (a `-dirty` SHA always fails);
- the plate is `"preview": true` and the report is not `--draft`;
- the Elevation credit does not begin with the `DATA_NOTICES.md` 3DEP credit.

Session 7 validated this with a stand-in plate of the same size and sidecar
keys, because forge3d is not installed in the sandbox. A real plate comes from
`render-plates.yml` (spec 013).

## 4. Client template

```bash
mkdir -p templates/clients/acme && cp templates/location_report.docx templates/clients/acme/
python scripts/render_location_report.py --fips 13255 --client acme   # 13255_spalding-acme.docx
```

Expected: the renderer refuses a client template that drops a required field,
names an unknown field, reads a refused column or types a number. The selftest
covers each case.

## 5. A county with no enacted restriction

```bash
python scripts/render_location_report.py --fips 13035   # Butts County, GA
```

Expected: the history section is the single sentence "No enacted restriction is
on record for Butts County, Georgia.", with no number and no rate.

## 6. County PDFs (US2)

```bash
python scripts/render_county_pdf.py --fips 13255 --fips 17063 --fips 42069
```

Expected:

- Three PDFs in `outputs/county_pdfs/`.
- Every page footer reads "... Data as of <date>. Commit <sha>." with a page
  number.
- The tables come from Great Tables, and the charts are vl-convert renders of
  the `charts.py` specs.
- No em-dash appears.

**Measured**: 3.6 to 5.0 s per county. In CI, use the `render-county-pdf.yml`
dispatch, which uploads the PDFs as artifacts.

## 7. Charts (US3)

```bash
python scripts/charts.py --fips 13255          # SVG, PNG and HTML per chart
python scripts/charts.py --sync-palette        # after any viz-palette.js change
```

Expected: the SVG and the HTML embed come from one spec. The selftest checks
that both carry the same data values, and that `viz-palette.json` still matches
`viz-palette.js`.

## 8. Markdown to Word (US4)

```bash
scripts/md_to_docx.sh deliverables/location_reports/13255_spalding.md
```

Expected:

- `outputs/briefs/13255_spalding.docx` is written, with headings in Georgia
  from `templates/reference.docx`.
- An input with an em-dash is refused.
- With `DOCX_VALIDATE` set, it passed `--original templates/reference.docx`
  on 2026-10-02.

## 9. Template rebuild

```bash
python scripts/build_report_templates.py --check   # exit 0: committed templates match the builder
```
