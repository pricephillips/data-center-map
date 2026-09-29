---

description: "Task list for spec 006, Source Durability and Event-Level Dedupe"
---

# Tasks: Source Durability and Event-Level Dedupe

**Input**: Design documents from `specs/006-source-durability-dedupe/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: FR-005 requires every new or touched module to ship or extend `--selftest` with no network use. Each selftest is the test task for its story, and `tests/test_selftests.py` discovers it.

**Organization**: Tasks are grouped by user story, so each story can be implemented and tested on its own.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [x] T001 Add `trafilatura>=2.2,<3` and `datasketch>=2.0,<3` to `requirements/ci.in`. Regenerate `requirements/ci.txt` with `uv pip compile requirements/ci.in --python-version 3.11 -o requirements/ci.txt`. Confirm with `git diff requirements/ci.txt` that no existing pin line changed; only lines are added.
- [x] T002 [P] Create `configs/source_durability.json` exactly as in `contracts/config.md`: archive, extract, dedupe and measure sections, with a `_comment` saying it is hand-edited.
- [x] T003 [P] Create `tests/fixtures/source_durability/`:
  - `article_jsonld.html`, whose JSON-LD has `datePublished` 2026-03-14;
  - `article_meta.html`, with `article:published_time` 2025-11-02;
  - `article_time.html`, with a byline `<time datetime="2026-07-09">`;
  - `paywall_stub.html`, headline plus under 500 characters of text;
  - `dedupe_articles.json`: two syndicated copies with the same headline and lead on different domains on the same date, plus one unrelated article;
  - `cdx_*.json`: a 200 capture, a non-200-only result, and an empty result.

  Mark every file as synthetic in a comment or `_comment` field.

## Phase 2: Foundational

- [x] T004 Create `event_dedupe.py` per research D6 and `contracts/cli.md`.
  - Functions: `available()`, `load_config()`, `normalize_title()`, `shingles()`, `cluster(items, cfg=None)`, and `cluster_id(url)`, which returns `evt_` plus `sha1(normalize_url(url))[:10]`.
  - MinHash is 128 permutations with seed 1, over 5-word shingles. It runs on the title, and on title plus lead when a lead exists, each in its own MinHashLSH at threshold 0.7.
  - Title-only matches need at least `min_title_tokens` tokens.
  - A cluster member must be compatible with every existing member: dates at most `max_days_apart` apart, the same domain only when the date is the same, states equal or one blank.
  - Grouping is greedy in (date, id) order.
  - Without datasketch, return singletons and print one notice.
  - `--selftest`: the three fixture articles give 2 clusters; the threshold is pinned at 0.7 by passing its own config; recurring same-domain titles on different days stay apart; a 4-token title does not merge on title alone; the 3-day window holds; the result is deterministic. Print `SKIP (datasketch not installed)` and exit 0 when the package is absent.
- [x] T005 [P] Create `article_extract.py` per research D5.
  - `available()`, `load_config()`.
  - `fetch(url, opener=None, cfg=None)`: stdlib urllib, User-Agent, timeout, `max_bytes` cap, text/html only.
  - `extract(url, html=None, opener=None, cfg=None)` returns `url`, `http_status`, `date_hint`, `text_chars`, `thin_text` and `lead`. The date comes from trafilatura `bare_extraction` with `original_date=True` and `extensive_search=False`. It must be day precision and between `min_date` and today, or it is blank. `thin_text` is `yes` below `thin_text_chars`. `lead` is the first `lead_words` words and is kept in memory only.
  - `--selftest`: the three HTML fixtures give their published dates, the stub is `thin_text=yes`, the date bounds hold, a mocked opener works, and there is no network. Print `SKIP (trafilatura not installed)` for the extraction checks and exit 0 when the package is absent.

**Checkpoint**: The shared clustering and extraction modules pass their selftests with and without the packages.

## Phase 3: User Story 1 - Every cited source has an archived snapshot (P1)

**Goal**: Nightly, capped, resumable archiving of every clean-feed URL into `data/source_archive.csv`.

**Independent Test**: `python source_archive.py --selftest`. From the five-URL fixture with mocked CDX, 3 are found and 2 are requested, and the CSV has LF endings.

- [x] T006 [US1] Create `source_archive.py`, stdlib only (FR-001), per research D2 to D4 and data-model ArchiveRecord.
  - Collect URLs from the clean feed's `Source URL` and `Sources` columns: exact URL, fragment removed, deduplicated.
  - Hosts in `skip_hosts` get `status=unresolved_redirect`.
  - Build the queue in order: requested rows due for a recheck, then new URLs, then `not_archived` rows. Sort by URL within each group, and apply the `max_lookups` and `max_saves` caps.
  - Parse the CDX lookup (`limit=-10`, JSON). A 200 capture sets `archived`, `method=cdx` (or `spn` if the row was `requested`), `archived_url` = `https://web.archive.org/web/<ts>/<original>`, and `archived_at` as ISO UTC.
  - When there is no 200 capture, record the newest status in `http_status` and send a Save Page Now request: anonymous `GET /save/<url>`, or `POST /save` with `Authorization: LOW` when `IA_S3_ACCESS_KEY` and `IA_S3_SECRET_KEY` are set (FR-002). A 2xx sets `status=requested`, `requested_on`, and `attempts+1`. At `max_attempts`, set `status=failed`.
  - On 429 or 503, back off through `backoff_s`. If the error persists, stop, set `stop_reason=rate_limited` and `stop_at_url`, and still write the outputs.
  - Checkpoint the CSV every `checkpoint_every` URLs. Write the CSV sorted by URL with LF endings. Columns in order: `url, archived_url, archived_at, http_status, method, status, checked_on, requested_on, attempts`.
  - Write `data/source_archive_manifest.json` with `coverage` = archived / resolvable, and `history` capped at 60 entries.
  - Flags: `--dry-run`, `--max-lookups`, `--max-saves`.
  - The HTTP layer is an injectable callable.
- [x] T007 [US1] Add `--selftest` to `source_archive.py`, with mocked HTTP only:
  - five URLs, 3 found and 2 requested;
  - a URL with only non-200 captures is not archived and records its `http_status`;
  - a Google News URL is `unresolved_redirect`, with no call made;
  - a 429 run stops with `stop_at_url` recorded, and the next run resumes from the CSV state;
  - a requested row rechecks to `archived`, `method=spn`;
  - keys switch the method to POST and are never written;
  - no CRLF.
- [x] T008 [US1] Create `.github/workflows/source-archive.yml`.
  - Nightly cron plus `workflow_dispatch`; `permissions: contents: write`; a concurrency group; `timeout-minutes: 45`; pinned action SHAs as in `fetch-pudl.yml`.
  - Steps: selftest, then run with `IA_S3_ACCESS_KEY` and `IA_S3_SECRET_KEY` from secrets.
  - Commit with one `git add` per path inside `if [ -e "$f" ]`, a `[skip ci]` message, and `bash scripts/git_push_retry.sh`.

**Checkpoint**: US1 is testable offline. The first live coverage number comes from CI.

## Phase 4: User Story 2 - Harvest candidates carry article text and a date hint (P2)

**Goal**: `date_hint` and `thin_text` on harvest and triage worklists, never auto-applied.

**Independent Test**: `python article_extract.py --selftest` shows the fixture dates matching.

- [x] T009 [US2] Edit `signal_harvest.py`.
  - Append `date_hint`, `thin_text`, `cluster_id` and `cluster_members` to the end of `FIELDS`. The `cluster_*` columns are filled in T014.
  - Add an `extract` parameter to `harvest()`. The default is `article_extract.extract` when available, otherwise none.
  - Extract opposition rows only, up to `max_fetch_per_run`, and keep each lead in memory for T014.
  - Add a `--no-extract` flag.
  - Print the hint count.
  - Selftest: a fake extractor fills `date_hint` and `thin_text`, and the columns come last in their stated order.
- [x] T010 [US2] Edit `promote_signal_candidates.py`.
  - `build_master_row` must not read `date_hint`, and `rewrite_queue` keeps the appended columns.
  - Selftest: a candidate with a `date_hint` that differs from its `seen_date` promotes with `Date == seen_date`.
- [x] T011 [P] [US2] Edit `untagged_triage.py`.
  - Append `date_hint` and `thin_text` to `FIELDS`.
  - Add the `--date-hints` and `--hint-limit` flags. Fetch only rows with a `resolved_url` not yet in `data/untagged_date_hints.csv`, using an append-only cache with columns `row_key, url, date_hint, thin_text, http_status, fetched_on`.
  - A plain run fills hints from the cache only.
  - Selftest: a cached hint fills the column with no fetch, a mocked extractor appends to the cache, and a rerun makes no second fetch.
- [x] T012 [US2] Add `--measure` to `article_extract.py` per research D10.
  - Sample the first `sample_size` rows of `data/project_decision_dates.csv` by `project_id`.
  - Fetch only rows missing from `data/date_hint_agreement.csv`.
  - Write the CSV and the `.md` per `contracts/outputs.md`: exact, 1-day and 3-day agreement, no-hint count, and the verdict.
  - Selftest: a mocked opener and a temporary directory.
- [x] T013 [US2] Edit `.github/workflows/update-opposition-csv.yml`.
  - Add `trafilatura datasketch` to the install line.
  - Add a non-blocking `python article_extract.py --measure` step after promotion.
  - Stage `data/date_hint_agreement.csv` and `.md` with a guarded add per path.

**Checkpoint**: Hints reach the worklists. Master is untouched.

## Phase 5: User Story 3 - Syndicated coverage collapses to one candidate event (P2)

**Goal**: One worklist row per cluster, and syndicated copies in master proposed as `supersede` through `status_resolution.py`.

**Independent Test**: `python event_dedupe.py --selftest` shows three fixture articles giving 2 clusters.

- [x] T014 [US3] Edit `signal_harvest.py`: cluster the opposition rows after extraction.
  - Items: title, lead, `seen_date`, domain, state.
  - The representative is the highest priority, then the earliest `seen_date`, then the URL. It gets `cluster_id` and `cluster_members`: the other URLs, sorted and `; `-joined. Drop the members from the worklist.
  - Print the syndicated-copy count.
  - `known_urls()` also reads the `cluster_member` URLs in `data/signal_promotion_report.csv`.
  - Selftest: syndicated copies collapse to one row, and `known_urls` includes `cluster_member` URLs. Print SKIP for the clustering checks when datasketch is absent.
- [x] T015 [US3] Edit `promote_signal_candidates.py`. When a representative with `cluster_members` is promoted, append one report row per member: `action=cluster_member`, `url=member`, and `blocking_reasons` = `syndicated copy of <rep url> (<cluster_id>)`. Extend the selftest.
- [x] T016 [US3] Edit `status_resolution.py`.
  - Append `cluster_id` and `archived_url` to `WORKLIST_FIELDS`. `archived_url` is looked up in `data/source_archive.csv` when the file is present.
  - Add a syndicated pass to `scan()`: pending rows with a Source URL, Incident as the title, domain from the URL. The keeper is the earliest date, then file order. Every other member not already in `status_resolutions.csv` and not already proposed becomes `supersede`, with `signal="syndicated copy"`, `group_key="cluster|<id>"` and `group_final_url` = the keeper's URL.
  - Selftest: syndicated master rows are proposed and master is unchanged. Print SKIP when datasketch is absent.
- [x] T017 [US3] Edit `.github/workflows/pipeline.yml`.
  - Add an "Install harvest and triage extras (spec 006)" step, `uv pip install --system -c requirements/ci.txt trafilatura datasketch`, after the blocking selftest gate and before the verification step.
  - Change the triage call to `python untagged_triage.py --date-hints`.
  - Stage `data/untagged_date_hints.csv` in the verification block with an `if [ -e ]` add.

**Checkpoint**: SC-002 replay: the live queue goes from 82 to 48 rows, and the master scan proposes 64 syndicated `supersede` rows.

## Phase 6: Polish and cross-cutting

- [x] T018 [P] Declare the new outputs in `configs/layers.json`:
  - Layer E: `data/source_archive.csv`, `data/source_archive_manifest.json`, `data/date_hint_agreement.csv`, `data/date_hint_agreement.md`;
  - `not_regenerable` entry for `data/source_archive.csv`.

  Add writer lines to `ARCHITECTURE.md`, then run `python layer_audit.py --write-gitattributes`.
- [x] T019 [P] Deferred: add `"data/source_archive.csv": {"archived_url": 0.2, "url": 0.2}` to `coverage_delta.files` in `configs/data_quality.json` once the file exists on main, because `qc/coverage_delta.py` exits 2 on a missing declared file. In `configs/integrations.json`, set the trafilatura targets to `article_extract.py`, `signal_harvest.py` and `untagged_triage.py`, and the datasketch targets to `event_dedupe.py`, `signal_harvest.py` and `status_resolution.py`. Run `python integration_audit.py`.
- [x] T020 Run the generators on current data:
  - `python status_resolution.py` (record the syndicated proposal count);
  - `python untagged_triage.py`;
  - `python source_archive.py --dry-run`.

  Commit the regenerated worklists.
- [x] T021 Run the gates:
  - `pre-commit run --all-files`;
  - `python -m pytest tests/test_selftests.py -q`, with the packages installed and again in a venv without them;
  - `python leak_audit.py --tier blocking`;
  - `python layer_audit.py --strict --no-write`;
  - `actionlint` on the three workflows.
- [x] T022 Update the spec status and record the measured SC-002 and the pending SC-001 and SC-003 in `specs/006-source-durability-dedupe/plan.md`.

## Dependencies and execution order

- Setup (T001-T003), then Foundational (T004-T005), then the stories.
- US1 (T006-T008) depends only on T002 and T003 and can run in parallel with US2 and US3.
- US2: T009 depends on T005. T010 depends on T009. T011 depends on T005. T012 depends on T005. T013 depends on T012.
- US3: T014 depends on T004 and T009. T015 depends on T014. T016 depends on T004. T017 depends on T011.
- Polish depends on all stories.

## Parallel examples

- T002 and T003, then T004 and T005.
- T006 (US1) with T011 (US2) and T016 (US3): different files, and the shared modules are already done.

## Implementation strategy

MVP is US1: the archive gives every cited source a durable copy, and it has no
package dependency. US2 and US3 follow. They share the harvest edit, so T009
comes before T014.
