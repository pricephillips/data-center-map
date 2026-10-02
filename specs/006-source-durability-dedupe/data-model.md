# Data Model: Source Durability and Event-Level Dedupe

## ArchiveRecord (`data/source_archive.csv`, one row per cited URL)

| Column | Type | Rule |
|---|---|---|
| url | string | Exact URL as cited (fragment removed, whitespace trimmed). Primary key. |
| archived_url | string | `https://web.archive.org/web/<ts>/<original>` for the newest capture with status 200; blank otherwise. |
| archived_at | string | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) of that capture; blank otherwise. |
| http_status | string | Status of the capture used. For a URL with only non-200 captures, the newest status. Blank when there is no capture. |
| method | enum | `cdx` (a capture existed before any request), `availability` (the same, found through the Wayback availability API because the CDX lookup failed; added 2026-10-02), `spn` (captured after our request), `none` |
| status | enum | `archived`, `requested`, `not_archived`, `unresolved_redirect`, `failed` |
| checked_on | date | Last CDX lookup. |
| requested_on | date | Last Save Page Now request. |
| attempts | int | Save requests sent. |

State transitions:

```text
(new) --google news host--> unresolved_redirect            (terminal)
(new) --CDX 200--> archived [method=cdx]                   (terminal)
(new) --no 200, save sent--> requested
(new) --no 200, save cap reached--> not_archived --next run--> (as new)
requested --recheck after recheck_after_days, CDX 200--> archived [method=spn]
requested --recheck, no 200, attempts < max--> requested (save re-sent)
requested --attempts == max_attempts--> failed             (terminal)
```

A non-200 capture never produces `archived`.

`method` records how the capture came to exist. `status` records where the URL
is in the lifecycle. The spec's five fields (`url`, `archived_url`,
`archived_at`, `http_status`, `method`) come first, in that order.

## ArchiveManifest (`data/source_archive_manifest.json`)

The manifest holds the outcome of the last run:

- `run_at`
- `credentials`: `keys` or `anonymous`
- `lookups`, `saves`, `availability_fallbacks` (CDX failures answered through `https://archive.org/wayback/available`; added 2026-10-02)
- `stop_reason`: `complete`, `cap_reached`, `rate_limited` or `time_budget` (the `max_runtime_s` wall-clock budget ran out; added 2026-10-02)
- `stop_at_url`
- `counts_by_status`
- `resolvable`: URLs that are not `unresolved_redirect`
- `archived`
- `coverage`: archived divided by resolvable. This is SC-001.

It also holds a `history` list of the last 60 runs as
`{run_at, archived, resolvable, coverage, stop_reason}`, so SC-001's 30-run
trend is in one file.

## Extraction (in memory; returned by `article_extract.extract`)

| Field | Rule |
|---|---|
| url | Fetched URL |
| http_status | Fetch status, or `error` |
| date_hint | ISO date or blank (research D5 bounds) |
| text_chars | Length of the extracted main text |
| thin_text | `yes` when `text_chars` < `thin_text_chars`, `no` otherwise, blank when not fetched |
| lead | First `lead_words` words of the main text. Kept in memory only and never written. |

## Event cluster (in memory; `event_dedupe.cluster`)

- **Input item**: `{id, title, lead?, date?, domain?, state?}`.
- **Output**: a list of clusters, each an ordered list of ids. The first id is
  the earliest item.
- **Id**: `cluster_id(url)` = `evt_` + `sha1(normalize_url(url))[:10]`, taken
  from the representative's URL.
- Matching and compatibility rules: research D6.

## Appended columns (FR-004: appended at the end, existing order unchanged)

| File | Appended columns | Writer |
|---|---|---|
| `data/signal_candidates.csv` | `date_hint`, `thin_text`, `cluster_id`, `cluster_members` | `signal_harvest.py`; `promote_signal_candidates.rewrite_queue` keeps them through `sh.FIELDS` |
| `data/untagged_triage.csv` | `date_hint`, `thin_text` | `untagged_triage.py` |
| `data/status_resolution_worklist.csv` | `cluster_id`, `archived_url` | `status_resolution.py` |

`data/signal_harvest_log.csv` is unchanged. It is append-only with a fixed
header, so adding a column would split old and new rows. The run prints its
syndicated-copy and date-hint counts instead.

`data/signal_promotion_report.csv` keeps its columns. It gains a new `action`
value, `cluster_member`, whose `blocking_reasons` field reads
`syndicated copy of <rep url> (<cluster_id>)`.

## TriageDateHint cache (`data/untagged_date_hints.csv`, append-only)

`row_key, url, date_hint, thin_text, http_status, fetched_on`. Keyed by
`row_key`. A later fetch for the same key is never made.

## DateHintAgreement (`data/date_hint_agreement.csv`, `.md`)

- **CSV**: `project_id, source_url, verified_date, date_hint, delta_days,
  agree, http_status, fetched_on`.
- **`agree`** is `yes` when `|delta_days| <= agree_days`, `no` when a hint
  differs by more, and blank when there is no hint.
- **The .md report** gives the sample size and the counts for exact, 1-day
  and 3-day agreement and for no hint, plus the SC-003 verdict (research
  D10).
