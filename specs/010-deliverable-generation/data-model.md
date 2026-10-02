# Data Model: Templated Deliverable Generation

**Feature**: `010-deliverable-generation` | **Date**: 2026-10-02

Nothing here is a new dataset. Each entity is an in-memory structure, built at
run time from committed files, or an output written once per render.

## Inputs (read only; allowlisted columns)

| File | Key | Columns read | Used for |
|---|---|---|---|
| `data/county_policy_scores.csv` | `fips` | `calibrated_score`, `score_decile`, `has_enacted_restrictive` | Score, decile, enacted label; national score distribution (chart) |
| `data/county_policy_intervals.csv` | `fips` | `va_p_lower`, `va_p_upper` | Interval |
| `data/county_benchmarks.csv` | `fips` | `county_name`, `state`, `calibrated_score_pct_national`, `calibrated_score_pct_state`, `calibrated_score_peer_median`, `peer_n` | Percentiles, peer median |
| `data/county_adjacency.csv` | `fips` | `neighbor_fips` | Neighbors |
| `master_opposition_clean.csv` | row | `Date`, `City`, `County`, `State`, `Opposition Type`, `Status`, `Community Outcome` (label rule only, never printed), `outcome_defensible`, `qc_jurisdiction_key`, `Project Name`, `Source URL`, `verification_status` | History rows, case rows, case counts |
| `data/external_restriction_census.csv` | `state`, `county` | `instrument`, `census_status`, `date_enacted`, `source` | History rows filed in the census |
| `data/county_census_features.csv` | `fips` | `county_name` (through `county_aggregator.load_frame`) | FIPS resolver |
| `configs/definitions.json` | term key | all | Definitions, notices |
| `viz-palette.json` | n/a | all | Chart colors |
| `outputs/plates/<id>.json` + `.png` | n/a | `title`, `subtitle`, `credits`, `commit_sha`, `preview` | Plate slot (optional) |
| `DATA_NOTICES.md` | n/a | text | 3DEP credit check |

**Refused, never loaded**:

- `decided`, `confirmed_blocks`, `blocked_share`;
- every column in `export_geolibre.SCREENER_COMPOSITE`;
- the county rate inputs `blocked_share_of_decided*`, `peer_restriction_rate`,
  `n_decided`, `n_blocked_confirmed` and `n_advanced_confirmed`.

A template or chart that asks for one of them is refused.

## Fact

One printed number.

| Field | Type | Rule |
|---|---|---|
| `name` | str | Dotted context path, for example `score.calibrated` |
| `formatted` | str | What the template prints |
| `raw` | str | The cell as read, or the count |
| `rule` | enum | `score2` (two decimals, half up), `ordinal` (integer percentile, half up), `int`, `count` |
| `file` | str | Repo-relative path |
| `sha256` | str | Hash of the file at render time |
| `key_column`, `key`, `column` | str | Cell address. For `count`, `column` holds the filter name. |

## Facts (one county)

| Field | Content |
|---|---|
| `report` | `fips`, `county_name`, `state`, `state_name`, `as_of`, `commit`, `commit_short`, `draft`, `client`, `title` |
| `history` | `enacted` (bool), `rows` (tracker rows), `census_rows`, `unitemized` (bool: label 1 with no rows) |
| `score` | `calibrated`, `interval_low`, `interval_high`, `decile`, `pct_national`, `pct_state`, `peer_median`, `peer_n` |
| `cases` | `count`, `rows` (date, place, type, status, outcome label, project, source URL) |
| `neighbors` | `count`, `n_enacted`, `rows` (county, state, enacted, decile, cases) |
| `sources` | `files` (path, sha256), `notice` |
| `facts` | List of `Fact`, written to the sidecar |

**Validation**:

- The FIPS must be five digits and present in the scores file.
- `outcome_defensible` must be in `OUTCOME_LABEL`.
- No rendered text may match `LEAK_RE` or contain U+2014.
- When `history.enacted` is false, the history section's rendered text
  contains no digit.

## Plate

| Field | Content |
|---|---|
| `png`, `sidecar` | Paths |
| `title`, `subtitle` | Caption, verbatim from the sidecar |
| `credits` | List of lines, verbatim |
| `commit_sha`, `preview` | Checked by the refusals |

## Template manifest (`templates/manifest.json`)

| Field | Content |
|---|---|
| `tag` | `location_report-v1` |
| `confirmed` | Date and confirmer of the section order |
| `sections` | Ordered section ids and headings |
| `required_fields` | Top-level context fields every template must reference |
| `optional_fields` | Fields a template may omit, such as `charts` |

## Outputs

| Path | Writer | Notes |
|---|---|---|
| `outputs/location_reports/<stem>.docx` | `render_location_report.py` | `<stem>` = `<fips>_<county-slug>[-<client>][-draft]` |
| `outputs/location_reports/<stem>.facts.json` | `render_location_report.py` | Contract: [contracts/facts_sidecar.md](./contracts/facts_sidecar.md) |
| `deliverables/location_reports/<stem>.md` | `render_location_report.py` | Markdown twin for Vale |
| `outputs/county_pdfs/<fips>_<slug>.pdf` | `render_county_pdf.py` | Footer: data as of, commit SHA |
| `outputs/charts/<fips>_<chart>.{svg,png,html}` | `charts.py` | From one spec |
| `outputs/briefs/<name>.docx` | `md_to_docx.sh` | Shell, outside the Python layer audit, declared anyway |
| `templates/location_report.docx`, `templates/reference.docx` | `build_report_templates.py` | Rebuilt only when styles or sections change |
| `viz-palette.json` | `charts.py --sync-palette` | Mirror of `viz-palette.js` |

Every output is declared in Layer E. Each directory has exactly one writer.
