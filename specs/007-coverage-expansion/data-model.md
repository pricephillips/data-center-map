# Data Model: Coverage Expansion

## 1. Discovery cache entry (`configs/local_meeting_sources.json`, value per `STATE::County`)

| Field | Type | Notes |
|-------|------|-------|
| platform | str | An existing fetcher key (`civicclerk`, `legistar`, `civicplus_rss`, `primegov`, `granicus`), or `none` or `ambiguous`. Unchanged. |
| base_url | str | Unchanged. For `civic_scraper:civicplus`, the value is `https://{st}-{slug}.civicplus.com`. |
| checked_at | ISO datetime | Unchanged. |
| adapter | str | NEW. `civic_scraper:<civicplus\|primegov\|granicus>`, `native:<platform>`, or `none`. |
| civic_scraper | str | NEW. The library version that was tried, or `unavailable`. Missing on entries written before this feature. |
| civic_scraper_errors | str | NEW, optional. `platform: ExceptionName` pairs, joined with `; `. Logged, never raised. |

State transitions:

- A missing entry is probed.
- A `none` or `ambiguous` entry whose `civic_scraper` value is missing or
  `unavailable` is re-probed once, when civic-scraper is importable.
- A resolved entry stays resolved. Only `--redo` probes it again.

## 2. Agenda text index row (`data/agenda_text_index.csv`)

| Column | Notes |
|--------|-------|
| document_url | The row key: one row per linked document. |
| sha256 | The content hash of the PDF bytes. It is the text-cache key, so two URLs serving the same PDF are read (and OCR'd) once. |
| jurisdiction, state | From the feed row. |
| text_source | `text_layer`, `ocr`, `ocr_unavailable`, `ocr_error`, `fetch_error`, `extractor_unavailable`, `too_large` or `not_pdf`. |
| pages | The page count, from pdfminer.six. |
| text_chars | Non-space characters of the cached text. |
| keyword_hits | The total number of matches of the fixed term list. |
| matched_terms | The distinct terms matched, joined with `; `. |
| processed_at | UTC ISO date. |

Rows with `ocr_unavailable`, `fetch_error` or `extractor_unavailable` are
retried on the next run. Every other status is final for that URL. The text itself is kept
at `.cache/agenda_text/<sha256>.txt`, which is gitignored and persisted by
`actions/cache`.

## 3. Federal bill record (`data/bill_sync_federal.csv`)

| Column | Notes |
|--------|-------|
| opp_id | As in the worklist. |
| identifier | For example `H.R. 8037`. Blank when none is found. |
| congress | For example `119`. |
| bill_type, bill_number | Congress.gov codes, for example `hr`, `8037`. |
| lookup_status | `matched`, `not_found`, `no_bill_id`, `skipped_no_key`, `http_<code>` or `network_error`. |
| unmatched_reason | Blank when matched. Otherwise, for example: `no federal bill identifier in record text; agency or oversight action`. |
| title | The bill title, up to 600 characters. |
| stage, stage_date, stage_evidence, correct_outcome | From the `STAGES` ladder. |
| latest_action_date | |
| congress_url | `https://www.congress.gov/bill/<n>th-congress/<chamber>-bill/<num>` |
| recorded_status, incident, date | From the worklist. |

`data/bill_status_review.csv` gains a trailing column, `venue`, with the
value `state` or `federal`. Federal rows use `congress_url` in the existing
`openstates_url` column, so the column set stays one schema.

## 4. Epoch candidate mapping

Fetch output: `data/permit_candidates_epoch_frontier_dc.csv`. It has Epoch's
own columns, plus `first_dated_observation`, which the generic
`earliest_date_from` option adds.

Ingest output (existing schema, `data/baseline_dated_external.csv`), for
unmatched rows only:

| Column | Value |
|--------|-------|
| source | `Epoch AI Frontier Data Centers (CC-BY 4.0)` |
| as_of | The run date (`--as-of`). |
| name | `Name` |
| state | From `Address`, by the `state_from` regex, then validated as a US state code or full name (`project_resolution.norm_state`). |
| announced_date | `first_dated_observation` |
| capacity_mw | `Current power (MW)` |
| operator | `Owner`, with ` #confident`, ` #likely` and ` #speculative` stripped. |
| source_url | `https://epoch.ai/data/data-centers` |

Matches file (`data/baseline_external_matches_<source_file_stem>.csv`):

| Column | Notes |
|--------|-------|
| source, name, state, announced_date | As they would have been written. |
| match_tier | `confirmed` or `review`. |
| pk | From `data/project_key_map.csv`. For a review row with several candidates, the keys joined with `; `. |
| key_name | The name in the key map. |
| name_jaccard | Two decimal places. |
| note | For example `held out of baseline: tracked project`. |

## 5. Agenda extraction draft row (`data/agenda_extract_draft.csv`)

| Column | Notes |
|--------|-------|
| source_path, source_sha256 | The input text file and the SHA-256 of its UTF-8 bytes. |
| row_n | The candidate index within the document. |
| jurisdiction, jurisdiction_start, jurisdiction_end | |
| body, body_start, body_end | |
| date, date_start, date_end | |
| item, item_start, item_end | Required. A row without an item is dropped. |
| action, action_start, action_end | |
| vote, vote_start, vote_end | Blank when no vote is stated. |
| model_id, extracted_at | |
| review_status | Always `draft`. |

Invariant: for every non-blank field `f`,
`source_text[f_start:f_end] == f`.
