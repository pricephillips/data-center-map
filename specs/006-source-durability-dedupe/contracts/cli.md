# CLI Contracts

Exit code 0 means success. Selftests exit 0 when a required package is missing
and print `SKIP (<package> not installed): ...` for the checks they skipped.
No selftest opens a network connection.

## source_archive.py (stdlib only)

| Invocation | Behavior | Exit |
|---|---|---|
| `python source_archive.py` | Runs one capped batch against the clean feed. Writes `data/source_archive.csv` and `data/source_archive_manifest.json`. | 0. A rate-limit stop is also 0, recorded in the manifest. It is 1 only if the input file is missing or an output cannot be written. |
| `--max-lookups N`, `--max-saves N` | Override the config caps for this run. | |
| `--dry-run` | Plans the queue and prints the counts. Makes no network call and writes nothing. | 0 |
| `--selftest` | Mocked CDX and SPN fixtures (three URLs found, two requested, one Google News redirect, one non-200, rate-limit stop and resume). | 0 or 1 |

Environment: `IA_S3_ACCESS_KEY` and `IA_S3_SECRET_KEY` are optional (FR-002).
They are never printed or written.

## article_extract.py (trafilatura)

| Invocation | Behavior | Exit |
|---|---|---|
| `python article_extract.py URL` | Prints the extraction record as JSON (debugging). | 0, or 1 when trafilatura is missing |
| `--measure` | SC-003. Fetches the sample rows missing from `data/date_hint_agreement.csv`, then rewrites the CSV and `.md`. | 0; 0 with a notice when trafilatura is missing |
| `--selftest` | Three HTML fixtures with known dates, date bounds, `thin_text`, and a fetch with a mocked opener. | 0 or 1; SKIP lines when trafilatura is missing |

Library API:

- `extract(url, html=None, opener=None, cfg=None) -> dict`
- `available() -> bool`
- `fetch(url, opener=None, cfg=None) -> (status, html)`

## event_dedupe.py (datasketch)

| Invocation | Behavior | Exit |
|---|---|---|
| `--selftest` | Three fixture articles: two syndicated copies and one unrelated article produce 2 clusters. The threshold is pinned at 0.7, and the guards are checked. | 0 or 1; SKIP when datasketch is missing |

Library API:

- `available() -> bool`
- `cluster(items, cfg=None) -> list[list[id]]`. Without datasketch it returns
  singletons.
- `cluster_id(url) -> str`
- `normalize_title(t) -> list[str]`

## Touched modules

| Invocation | Change |
|---|---|
| `signal_harvest.py [--days N] [--no-extract]` | Extracts candidates and clusters them by default when the packages are present. `--no-extract` skips the page fetches. |
| `promote_signal_candidates.py` | Unchanged CLI. It writes `cluster_member` report rows. |
| `untagged_triage.py --date-hints [--hint-limit N]` | New flags. Without them, hints come from the cache only. |
| `status_resolution.py` | Unchanged CLI. The scan adds syndicated `supersede` proposals when datasketch is present. |
