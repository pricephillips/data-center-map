# Contract: command-line interfaces

Every Python script exits 0 on success, 1 on a check failure or refusal, and 2
on a usage error. Messages go to stderr. Paths are repo-relative.

## `scripts/render_location_report.py`

```text
render_location_report.py --fips 13255 [--client NAME] [--plate outputs/plates/<id>.png]
                          [--draft] [--no-charts] [--allow-uncommitted]
render_location_report.py --verify outputs/location_reports/<stem>.facts.json
render_location_report.py --selftest
```

- Writes `outputs/location_reports/<stem>.docx`, the matching `.facts.json`,
  and `deliverables/location_reports/<stem>.md`.
- Runs these checks on the rendered text before it writes:
  - vocabulary (`LEAK_RE`) and em-dash;
  - first-use definitions;
  - the no-rate rule when no restriction is enacted;
  - the FR-002 XML rules.
- If `DOCX_VALIDATE` names the docx skill's `validate.py`, it also runs that
  script with `--original <template>` and fails on a non-zero exit.
- `--verify` re-derives every fact. It prints the mismatches, and exit 0 means
  zero mismatches.
- It refuses in these cases:
  - an unknown FIPS;
  - a client name outside `[a-z0-9_-]+`, or a client template that is missing;
  - a template that is missing a required field or names an unknown one;
  - a refused column;
  - any of the plate refusals in the plan.

## `scripts/render_county_pdf.py`

```text
render_county_pdf.py --fips 13255 [--fips ...] [--plate outputs/plates/<id>.png] [--draft]
render_county_pdf.py --selftest
```

- Writes `outputs/county_pdfs/<fips>_<slug>.pdf`.
- The footer on every page reads "Data as of <date>. Commit <sha>." plus the
  page number.
- When the WeasyPrint system libraries are missing, it prints
  `sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0` (or
  `brew install pango` on macOS) and exits 1.
- The plate rules are the same as for the Word report.

## `scripts/charts.py`

```text
charts.py --fips 13255 [--chart score_distribution|enactment_timeline|peer_comparison]
charts.py --sync-palette
charts.py --selftest
```

- Writes `outputs/charts/<fips>_<chart>.svg`, `.png` and `.html` from one
  Vega-Lite spec.
- `--sync-palette` rewrites `viz-palette.json` from `viz-palette.js`.

## `scripts/build_report_templates.py`

```text
build_report_templates.py [--check]
build_report_templates.py --selftest
```

- Rebuilds `templates/location_report.docx` and `templates/reference.docx`
  from `templates/manifest.json` and the Hawthorn styles.
- `--check` builds both templates in memory and fails if they differ from the
  committed files in fields or section order.

## `scripts/md_to_docx.sh`

```text
scripts/md_to_docx.sh deliverables/<path>.md [outputs/briefs/<name>.docx]
```

- Runs `pandoc --reference-doc templates/reference.docx` as a separate
  program.
- Fails in three cases:
  - Pandoc is missing; it then prints the install hint;
  - the input contains U+2014;
  - `DOCX_VALIDATE` is set and validation fails.
