# Research: Templated Deliverable Generation

**Feature**: `010-deliverable-generation` | **Date**: 2026-10-02

The session 7 sandbox ran Python 3.11.15 with `requirements/ci.txt` installed in
a venv. Pandoc 3.x and Vale were on the PATH. The docx skill's `validate.py` was
run from the skill directory. It is not vendored into the repo.

## R1. The base template is client-neutral and built from code

**Decision**: `templates/location_report.docx` is built from scratch by
`scripts/build_report_templates.py`. It carries no client name, logo or
framing. The Vantage template from 2026-09-23 is no longer the base. It becomes
a future client add-on at `templates/clients/vantage/location_report.docx`,
built against the same fields (R2). The spec's Assumptions now say so.

**Section order**: Price confirmed it on 2026-10-02, and it is recorded in
`templates/manifest.json` as tag `location_report-v1`:

1. Title block: county, state, FIPS, data as of, commit. The `--plate` slot
   sits directly under it.
2. Summary.
3. Enacted restriction history.
4. Restriction resemblance score: the calibrated score, the interval, the
   decile, the national and state percentiles, the peer median, and the score
   distribution chart.
5. Tracked cases in the county.
6. Neighboring counties.
7. Sources and method.

**Rationale**:

- The record comes before the model. A reader sees what the county enacted
  before seeing how much it resembles counties that enacted.
- A builder in Python is reviewable in a diff and reproducible. Its selftest
  proves the template contains no typed number.
- A template saved by hand in Word splits Jinja tags across runs, and it cannot
  be reviewed.

**Alternatives considered**: Waiting for the Vantage template was rejected,
because it would tie the base to one client's framing. Building the template by
hand in Word was rejected for the reasons above.

## R2. Client templates bind the same fields

**Decision**: `--client <name>` selects
`templates/clients/<name>/location_report.docx`. Before filling, the renderer
reads the template's variables with
`DocxTemplate.get_undeclared_template_variables()` and refuses the template in
two cases:

- It names a field outside the contract in
  [contracts/template_fields.md](./contracts/template_fields.md). An unknown
  field would need a bespoke code path.
- It omits a field listed under `required_fields` in `templates/manifest.json`.

Client names are restricted to `[a-z0-9_-]+`, so `--client` cannot reach
outside `templates/clients/`. With `--client` absent, the base template is used.
The output file name carries the client name.

**Rationale**:

- The spec's Assumptions say client framing enters as config or template text,
  never as a code path.
- The contract check is the edge case "hand edits break docxtpl tags".

**Alternatives considered**: Per-client context builders were rejected, because
they would change data binding per client.

## R3. Every number is read at run time, with provenance

**Decision**: `scripts/report_data.py` builds a `Facts` object. Each number in
it is a `Fact` that holds:

- the formatted string;
- the raw cell;
- the file, key column, key and column it came from.

The renderers put only formatted strings into the template context.

A per-file column allowlist means refused columns are never loaded:

- the group-level columns `decided`, `confirmed_blocks` and `blocked_share`;
- the screener composite columns;
- every county rate input: `blocked_share_of_decided*`,
  `peer_restriction_rate`, `n_decided`, `n_blocked_confirmed` and
  `n_advanced_confirmed`.

The refusal set is `export_geolibre.GROUP_LEVEL | SCREENER_COMPOSITE` plus
those rate columns.

Formatting is defined once:

- scores and intervals use two decimals, rounded half up from the CSV string
  with `decimal.Decimal`;
- percentiles round half up to an integer ordinal;
- counts print as integers.

"Data as of" is the latest commit date among the input files
(`git log -1 --format=%cs`). The commit is HEAD, with `-dirty` added if an input
is modified (the same rule as `export_geolibre.commit_sha`).

**Rationale**: Principle VI and SC-002. The sidecar makes the re-derivation
mechanical (R9).

## R4. Enacted restriction history

**Decision**: The history uses the label rule from `county_aggregator.py`, not
a copy of it. It imports `RESTRICTIVE_TYPES`, `ENACTED_STATUSES`,
`DIRECTION_AMBIGUOUS_STATUSES`, `_type_tokens`, `norm_county` and
`norm_state`, and its county resolver comes from `load_frame()`. Rows are:

- clean-feed rows that resolve to the county and meet the rule, with their
  date, place, jurisdiction level, type, status and source link;
- rows from `data/external_restriction_census.csv` matched on normalized
  county and state.

There are three cases:

- `has_enacted_restrictive` is 0: the section prints one sentence ("No enacted
  restriction is on record for X.") and nothing else. It prints no table, no
  count and no rate.
- The label is 1 but no row resolves: the section says the label rests on
  records not itemized here, as `county-profile.html` does.
- A row is city-level (Spalding's Griffin moratorium): the level column says
  "city". The county-level label is not overstated.

## R5. Score and interval

**Finding**: The calibrated score falls outside its Venn-Abers interval in
1,126 of 3,144 counties. Spalding is one of them (0.1717 against 0.175 to
0.264). The two come from different calibrations of the same out-of-fold
score. `data/county_policy_intervals.md` promises that the interval contains
the Venn-Abers point, not the calibrated score.

**Decision**:

- The report always prints the interval beside the score, never "score ±
  width".
- The interval definition in `configs/definitions.json` says the interval comes
  from a separate calibration and need not contain the score.
- The report quotes no county rate.
- The peer median (`calibrated_score_peer_median`) is a median score, not a
  rate, so it is printed.

## R6. Definitions on first use

**Decision**: `configs/definitions.json` holds each term's display text and a
parenthetical definition. The context provides `define(key)`:

- the first call in document order returns "term (definition)";
- every later call returns the term alone;
- Jinja renders the document XML in order, so first use follows the template,
  whatever a client template's section order.

After rendering, the text is checked for every defined term: its first
occurrence must carry its definition. Definitions contain no digits (selftest),
so every digit in a report traces to a fact. The Vale rule
`Hawthorn.Undefined` applies to the markdown twin.

The client-facing term for the interval is "interval", not "Venn-Abers
interval". Vale's rule wants a definition right after the method name
("Venn-Abers (...)"), and the method name is jargon a client does not need.
The definition says the interval comes from a separate calibration. Charts
call it "interval" too. The method name stays in the code, the specs and
`data/county_policy_intervals.md`.

## R7. The plate slot (spec 013 contract)

**Decision**: The plate is implemented in `report_data.load_plate()` and shared
by both renderers:

- **Slot**: an image paragraph under the title block. Its width is the
  template's text width, read from the template section (page width minus
  margins), so a client template with other margins still gets full width.
  Height follows the aspect ratio, and the image bytes are inserted unmodified.
- **Caption**: the sidecar's `title`, then its `subtitle`.
- **Credits**: the sidecar's `credits` lines, printed verbatim in the
  "Credit" style. The Elevation credit must begin with the 3DEP credit quoted
  in `DATA_NOTICES.md`.
- **Refusals**: the renderer refuses in four cases:
  - the sidecar is missing;
  - `git merge-base --is-ancestor <commit_sha> HEAD` fails (a `-dirty` SHA is
    not a commit, so it fails too);
  - `preview` is true and the report is not `--draft`;
  - the credits do not match `DATA_NOTICES.md`.
- **Absent flag**: the `{%p if plate %}` paragraphs drop out, so the layout is
  the same as a template with no slot. Both variants pass
  `validate.py --original` (R8).

## R8. Validation and timing

**Decision**: The docx skill's `validate.py` lives outside the repo, so it is
not a CI step. The renderer runs it when `DOCX_VALIDATE` points at it, with
`--original` set to the template the report was filled from. In-repo checks
cover FR-002 without it, in the renderer selftest and on every render:

- every `w14:paraId` is below `0x80000000`;
- every `<w:tbl>` has `<w:tblGrid>`;
- every `<w:hyperlink>` is a sibling of runs, not inside one.

RichText cells must use docxtpl's `{{r ...}}` form. A plain `{{ ... }}` puts a
run inside `<w:t>`, which `validate.py` rejects ("Element content is not
allowed"). This was found in the session 7 probe.

**Measured** (2026-10-02, details in [quickstart.md](./quickstart.md)):

- The Spalding report passes `validate.py --original templates/location_report.docx`
  both without a plate and with one.
- The location report renders in about 1.5 s with the chart. The county PDF
  renders in about 4 to 5 s.
- LibreOffice in the sandbox would not load any file, not even a plain `.txt`,
  so no page image of the Word report was made. The PDF pages were inspected
  as images instead.
- WeasyPrint laid out the chart SVGs' text with substitute font metrics and
  clipped the axis labels. The PDF therefore embeds vl-convert's PNG of the
  same spec, which keeps vl-convert's own text layout.
- Great Tables 1.0.0's `html()` cell wrapper cannot be stored in pandas 3
  string columns. Link cells are escaped first and formatted as plain strings,
  which Great Tables inserts as HTML.
- `as_raw_html(inline_css=True)` needs `css-inline`, which is not registered.
  The PDF uses the default `<style>` block instead.

## R9. Re-derivation check (SC-002)

**Decision**: The renderer writes `<stem>.facts.json`. Each fact in it holds:

- `file`, `sha256`, `key_column`, `key`, `column`;
- `raw`, `formatted` and `rule`.

`render_location_report.py --verify <facts.json>` checks each fact in three
steps:

1. Open the file and find the row.
2. Re-read the cell and re-apply the formatting rule.
3. Compare the result with `formatted`, and confirm that `formatted` appears
   in the DOCX text.

It reports the mismatches, and zero mismatches is the pass. Counts derived by
filtering are re-derived by the same filter (`rule: count`). Their provenance
lists the filter, not one cell.

## R10. Tools and where they run

| Tool | Where | Why there |
|---|---|---|
| docxtpl, python-docx | local (installed in CI for selftests) | Word reports are reviewed by Price before sending |
| Great Tables | local and the PDF workflow | PDF tables only. Word uses native tables, which stay editable and accessible. |
| WeasyPrint | CI (`render-county-pdf.yml`) and local | Needs Pango. The pipeline selftest job installs `libpango-1.0-0 libpangoft2-1.0-0`. |
| Altair, vl-convert | CI and local | No browser needed |
| Pandoc | local CLI | GPL-2.0-cli: subprocess only, never imported |

All Python packages are pinned in `requirements/ci.in`. `uv pip compile`
preserved every existing pin: the diff of `ci.txt` adds lines and moves no
version. The selftest step installs them with `-c requirements/ci.txt`.

## R11. Chart standard (US5)

**Decision**: `docs/chart_standard.md` adopts five Urban Institute guide rules:

- the title states the finding;
- series are labeled directly;
- every chart carries a source line;
- uncertainty is shown as a range;
- there is no chart junk.

The guide was used only to review `viz-palette.js`, which stays canonical. The
review found nothing to change:

- the outcome colors already keep unverified tiers as lighter tints;
- the sequential ramp is perceptually ordered.

Charts read colors from `viz-palette.json`. `charts.py --sync-palette` writes
that file from `viz-palette.js` with the regex reader in `export_geolibre`, and
the selftest fails on drift.

## R12. Hawthorn styles

**Decision**: One function, `hawthorn_styles(doc)`, styles both templates:

- **Fonts**: Georgia for the body and headings.
- **Text colors**: slate `#1f2937` for text and `#4b5563` for captions.
- **Margins**: US Letter with one-inch margins.
- **Named styles**: `Title`, `Subtitle`, `Heading 1` and `Heading 2`, `Body Text`,
  `Caption`, `Credit`, `Table Text` and `Hyperlink`.
- **Pandoc styles**: `First Paragraph`, `Compact`, `Image Caption`,
  `Table Caption`, `Block Text`, `Author`, `Date`, `Abstract`.

The reference document carries the full set, so Pandoc maps every heading and
paragraph to a Hawthorn style.
