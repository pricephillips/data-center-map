# Tasks: Local, Federal, and Baseline Coverage Expansion

**Input**: Design documents from `specs/007-coverage-expansion/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Every independent test in the spec becomes a `--selftest` check in
the touched module (Principle IX). `tests/test_selftests.py` discovers these
automatically, so no separate test files are written.

## Phase 1: Setup

- [X] T001 Append `civic-scraper>=1.1,<2`, `ocrmypdf>=17,<18`, `pdfminer.six` and `langextract>=1.7,<2` to requirements/ci.in, with a comment that langextract is pinned for local use only and that no workflow installs it. Then recompile in place with `uv pip compile requirements/ci.in --python-version 3.11 -o requirements/ci.txt` and confirm that no existing pin line changed (`git diff requirements/ci.txt` shows additions only).
- [X] T002 [P] Add `.cache/` to .gitignore.
- [X] T003 [P] Create the fixtures in tests/fixtures/coverage_expansion/:
  - `scanned.pdf`: image-only, with the words "data center rezoning";
  - `text.pdf`: text layer containing "data center";
  - `epoch_data_centers.csv`: 10 rows, including one non-US row and one row naming a project that is tracked in data/project_key_map.csv;
  - `epoch_timelines.csv`;
  - `congress_actions_one_chamber.json`, `congress_actions_signed.json`, `congress_bill.json`;
  - `agenda.txt`: contains a known vote tally, "5-2".

## Phase 2: Foundational

- [X] T004 Update configs/layers.json:
  - Layer E: `data/agenda_text_index.csv`, `data/agenda_extract_draft.csv`, `data/baseline_external_matches_*.csv`;
  - Layer D: `data/bill_sync_federal.csv`.
- [X] T005 [P] Update configs/integrations.json:
  - set the ocrmypdf target to `agenda_text.py`;
  - add a `pdfminer.six` entry (MIT, selected, spec 007, session 4, runs_in ci, target agenda_text.py).

  Then run `python integration_audit.py`.

**Checkpoint**: Declarations are in place, and each story can be built independently.

## Phase 3: User Story 1 - More jurisdictions resolve (P1) MVP

**Goal**: civic-scraper adapters for CivicPlus, Granicus and PrimeGov run before the native probes, with fallback, and the adapter is recorded in the cache.

**Independent Test**: `local_meeting_feed.py --selftest`; live: `--compare 50`.

- [X] T006 [US1] In local_meeting_feed.py, add `CIVIC_SCRAPER_PLATFORMS`: civicplus, primegov and granicus, with no Legistar entry. Each entry has candidate URL builders:
  - civicplus: `https://{st}-{bare}county.civicplus.com/AgendaCenter`, then `https://{st}-{bare}.civicplus.com/AgendaCenter`;
  - primegov: `https://{slug}.primegov.com/public/portal`;
  - granicus: `https://{slug}.granicus.com/ViewPublisherRSS.php?view_id=1&mode=agendas`.

  Each entry also names the existing fetcher, and the base URL that fetcher expects.
- [X] T007 [US1] Implement `probe_civic_scraper(state, county, ambiguous)` in local_meeting_feed.py:
  - import civic-scraper inside a `try`;
  - call `scrape()` with a 120-day window and a 20 s timeout;
  - accept a platform only when at least 1 asset is returned;
  - log and collect every exception without raising;
  - when `ambiguous`, try civicplus only;
  - return `(result or None, version or "unavailable", errors)`.
- [X] T008 [US1] Rework `discover_one()` in local_meeting_feed.py:
  - call civic-scraper first, then the native `PROBES`;
  - record `adapter` (`civic_scraper:<p>`, `native:<p>` or `none`), `civic_scraper` and `civic_scraper_errors`;
  - keep the ambiguous skip for the native probes.
- [X] T009 [US1] Update `discover()` in local_meeting_feed.py:
  - re-probe `none`/`ambiguous` entries whose `civic_scraper` value is missing or `unavailable`, when the library is importable;
  - never re-probe a resolved entry without `--redo`;
  - add `--max-probes` (default 400) and `--no-civic-scraper`;
  - print the resolved count split by adapter.
- [X] T010 [US1] Add `--compare N` to local_meeting_feed.py. It runs the first N frame jurisdictions with and without civic-scraper, in memory, and prints both resolved counts. It writes nothing.
- [X] T011 [US1] Extend `selftest()` in local_meeting_feed.py, using a fake `civic_scraper` package in `sys.modules`:
  - a hit records `adapter=civic_scraper:primegov` and is not re-probed;
  - a raise falls back to the native probes and the error is logged;
  - there is no Legistar platform in the civic-scraper table;
  - an ambiguous name tries civicplus only;
  - an ImportError gives `civic_scraper=unavailable`;
  - every civic-scraper platform maps to a key in `FETCHERS`.
- [X] T012 [US1] Update the local_meeting_feed.py docstring for the civic-scraper layer and the new flags.

**Checkpoint**: US1 is testable offline, and live via `--compare`.

## Phase 4: User Story 2 - Scanned agendas become searchable (P2)

**Goal**: PDFs without a text layer are OCR'd once, cached by SHA-256, and put through the keyword pass.

**Independent Test**: `agenda_text.py --selftest`. Only the scanned fixture is OCR'd, and "data center" is found in both fixtures.

- [X] T013 [P] [US2] Create agenda_text.py with the following:
  - `sha256_bytes`;
  - `extract_text_layer` (pdfminer.six, imported lazily);
  - `needs_ocr` (fewer than 50 non-space characters per page on average);
  - `run_ocr` (`ocrmypdf --skip-text -l eng --sidecar`, only if `tesseract` and `gs` are on PATH, else `ocr_unavailable`);
  - `keyword_hits` over the fixed term list;
  - `process_pdf(bytes)` returning an index row;
  - the text cache at `.cache/agenda_text/<sha>.txt`.
- [X] T014 [US2] Add `--run` to agenda_text.py:
  - read `data/local_meeting_feed.csv` PDF links;
  - download with caps `--max-docs 40` and `--max-mb 25`;
  - skip hashes whose index row is final;
  - upsert `data/agenda_text_index.csv`, sorted, LF.

  Also add `--file PATH`.
- [X] T015 [US2] Add `--selftest` to agenda_text.py:
  - the fixture PDFs: only the scanned one is OCR'd, keywords are found in both, and a rerun is a cache hit (SKIP the OCR round trip without ocrmypdf, tesseract or gs);
  - a pure-function check of `needs_ocr` and `keyword_hits`;
  - the workflow guard: any `.github/workflows/*.yml` that runs `agenda_text.py` must install `tesseract-ocr` and `ghostscript`.
- [X] T016 [US2] Edit .github/workflows/local-signals.yml:
  - install civic-scraper, ocrmypdf and pdfminer.six with `uv pip install --system -c requirements/ci.txt`;
  - `apt-get install tesseract-ocr ghostscript`;
  - add `actions/cache` for `.cache/agenda_text`;
  - run `agenda_text.py --selftest` in the self-test step and `agenda_text.py --run` after the fetch step (continue-on-error);
  - commit `data/agenda_text_index.csv`.

  If the edit cannot be pushed, stage it as docs/pending_coverage_expansion.patch with a matching `.md`.

## Phase 5: User Story 3 - Federal status from Congress.gov (P2)

**Goal**: The 43 federal records are matched to Congress.gov, or listed with a reason. Their stage follows the ladder, terminal first.

**Independent Test**: `bill_sync.py --selftest`. A mocked one-chamber passage codes as Pending.

- [X] T017 [P] [US3] In bill_sync.py, add `extract_federal_ids(text)`. It covers H.R., S. (not preceded by a letter or dot), H.Res., S.Res., H.J.Res., S.J.Res., H.Con.Res. and S.Con.Res., and returns `(label, type_code, number)`. Also add `congress_for_year(y)` and `congress_url()`.
- [X] T018 [US3] In bill_sync.py, add `classify_federal_actions(actions)`. It maps Congress.gov `type` and `text` onto `STAGES`, terminal first, counting chamber passage per distinct chamber (research D8).
- [X] T019 [US3] In bill_sync.py, add `congress_get()` (stdlib, `api_key` query param, throttled) and `lookup_federal_bill()`:
  - uses `Cache` keys `US:<congress>:<type>:<number>`;
  - fetches bill and actions;
  - falls back to the previous Congress on not-found.
- [X] T020 [US3] In bill_sync.py, add `resolve_federal()`. It builds one `data/bill_sync_federal.csv` row per federal worklist record, with `unmatched_reason` for every non-match, and review rows with `venue=federal`. A possible sine die is flagged when the Congress has ended with no terminal action.

  When `CONGRESS_API_KEY` is empty, it records `skipped_no_key` without failing.
- [X] T021 [US3] In bill_sync.py:
  - append `venue` to `REVIEW_COLS` and set `state` on state rows;
  - add `merge_review(existing, new, venue)`;
  - wire the federal pass into `--resolve`;
  - add a `--federal` mode;
  - write `bill_sync_federal.csv` in `--extract`;
  - add a federal section to `write_report()`.
- [X] T022 [US3] Extend `selftest()` in bill_sync.py:
  - federal id parsing, including the `U.S. 50` negative;
  - the one-chamber fixture is Pending;
  - the signed fixture is Signed into law;
  - a veto outranks passage;
  - the no-key path returns `skipped_no_key`;
  - `merge_review` replaces only one venue.
- [X] T023 [US3] Edit .github/workflows/bill-sync.yml: pass `CONGRESS_API_KEY` to the resolve step, run `bill_sync.py --federal` when the OpenStates key is absent, and commit `data/bill_sync_federal.csv`.

## Phase 6: User Story 4 - Epoch AI candidates (P2)

**Goal**: Epoch campuses enter the dated-baseline candidate file through config only. They carry the attribution, and are matched to project keys before any promotion.

**Independent Test**: `permit_ingest.py --selftest` on the 10-row fixture.

- [X] T024 [P] [US4] In fetch_permits.py, factor the CSV download into `_read_csv_url(url)`, add the generic `earliest_date_from` option to `fetch_tabular`, and extend the selftest with a mocked reader.
- [X] T025 [P] [US4] In permit_ingest.py:
  - refactor `main()` into `ingest(rows, cfg, as_of) -> (out_rows, rejects)` plus I/O;
  - add the generic `state_from`, `strip_regex` and `match_projects` options (the `project_resolution.name_tokens`/`jaccard` rule: strong 0.60 with the state agreeing is confirmed, soft 0.34 is review);
  - write `data/baseline_external_matches_<stem>.csv`;
  - print the attribution, the sampling note and the matched share.
- [X] T026 [US4] Add `--selftest` to permit_ingest.py, using the 10-row fixture and the real Epoch ingest config:
  - the output conforms to the schema;
  - `source` carries the attribution string;
  - the non-US row is rejected;
  - the tracked project is held with its `pk`;
  - a year-only date is rejected;
  - an in-progress status drops `decision_date`.
- [X] T027 [P] [US4] Create configs/epoch_frontier_dc.json (tabular fetch) and configs/epoch_frontier_dc_ingest.json (column map, attribution, sampling note).
- [X] T028 [P] [US4] Update the `epoch_frontier` entry in configs/facility_sources.json: license `CC-BY-4.0`, attribution, export URLs, the sampling-limit note and the acquisition status. Then run `facility_manifest.py --selftest`.
- [X] T029 [US4] Edit .github/workflows/fetch-permits.yml: add `permit_ingest.py --selftest` to the gate, and commit `data/baseline_external_matches_*.csv`.

## Phase 7: User Story 5 - Grounded agenda extraction, local only (P3)

**Goal**: Draft candidate rows where every retained field slices the source text exactly.

**Independent Test**: `scripts/agenda_extract.py --selftest` on the fixture agenda, with a stubbed model.

- [X] T030 [P] [US5] Create scripts/agenda_extract.py:
  - CI refusal (exit 2 unless `--selftest`);
  - an output-path guard (`data/*_draft.csv` only, never `master_opposition*`);
  - an injectable `extract_fn` (the default wraps `langextract.extract` with Ollama `--model`/`--model-url`);
  - `ground(text, extractions)` applying research D11, with drop counters;
  - `assemble_rows()`;
  - a draft CSV writer;
  - a run summary.
- [X] T031 [US5] Add `--selftest` to scripts/agenda_extract.py, with a stub model on `tests/fixtures/coverage_expansion/agenda.txt`:
  - the vote offsets slice to "5-2";
  - an ungrounded, an out-of-bounds and a mismatched field are each dropped and counted;
  - the CI refusal returns 2 via a subprocess;
  - a bad output path is refused;
  - 100 percent of retained fields are valid.

## Phase 8: Polish

- [X] T032 Add ARCHITECTURE.md writer lines for every new output.
- [X] T033 Merge `origin/main`. Then run:
  - `pre-commit run --all-files`;
  - `python -m pytest tests/test_selftests.py`;
  - `python leak_audit.py --tier blocking`;
  - `python layer_audit.py --strict --no-write`;
  - `python integration_audit.py`.

  Record the results in the plan's implementation status.
- [X] T034 Mark tasks done in this file, and add an implementation status note to plan.md.

## Dependencies

- Phase 1 comes before Phase 2, which comes before the stories. US1 to US5 are independent of each other. US5 consumes the text that US1 and US2 produce at runtime, but not at build time.
- Within US3: T017, T018 and T019, then T020, then T021, then T022.
- Within US4: T024, T025 and T027 are independent. T026 needs T025 and T027.

## Parallel examples

- US1 alone is the MVP. US2 (T013), US4 (T024, T025, T027, T028) and US5 (T030) touch disjoint files, so they can run alongside it.

## Implementation strategy

MVP is US1: it moves the largest coverage number (SC-001). Then US3 and US4
(pure config and stdlib), then US2 (it needs system packages in CI), then US5
(local only).
