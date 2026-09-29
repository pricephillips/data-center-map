# Feature Specification: Local, Federal, and Baseline Coverage Expansion

**Feature Branch**: `007-coverage-expansion`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 4 of the tool integration plan: civic-scraper, OCRmyPDF, Congress.gov API, Epoch AI Frontier Data Centers, LangExtract (local only).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - More jurisdictions resolve to a machine-readable agenda source (Priority: P1)

`local_meeting_feed.py` discovery tries civic-scraper's platform adapters (CivicPlus, Legistar, Granicus, PrimeGov) before its own probes, and records which adapter resolved each jurisdiction in the discovery cache. Its own adapters stay as fallbacks.

**Why this priority**: Discovery resolved 3 of 808 jurisdictions before the 2026-09-03 additions. Local boards are where most restrictions are enacted.

**Independent Test**: Run discovery on a fixed 50-jurisdiction sample, with and without civic-scraper, and compare resolved counts.

**Acceptance Scenarios**:

1. **Given** a jurisdiction that civic-scraper resolves, **When** discovery runs, **Then** the cache records `adapter=civic_scraper:<platform>` and is not re-probed on later runs.
2. **Given** civic-scraper raises for a site, **When** discovery runs, **Then** the existing adapters are tried and the error is logged, not raised.

### User Story 2 - Scanned agendas become searchable (Priority: P2)

Before the keyword pass, agenda PDFs with no text layer go through OCRmyPDF (`--skip-text`, English). The OCR output is cached by content hash, so each PDF is processed once.

**Independent Test**: A fixture of one scanned PDF and one text PDF; only the scanned one is OCR'd, and the keyword pass finds "data center" in both.

### User Story 3 - Federal legislative records get status from Congress.gov (Priority: P2)

`bill_sync.py` matches the 43 federal legislative records it now skips to Congress.gov bill IDs, pulls actions, and writes status disagreements to `data/bill_status_review.csv` with `venue=federal`. The federal stage mapping follows the same terminal-over-milestone rule as the state ladder (Principle III).

**Independent Test**: A mocked Congress.gov response for a bill that passed one chamber codes as pending, not enacted.

### User Story 4 - Large campuses from Epoch AI enter the dated baseline as candidates (Priority: P2)

A `permit_ingest.py` config maps the Epoch AI Frontier Data Centers CSV into the dated-baseline candidate file, with attribution recorded in the source manifest. Candidates are matched to existing projects by `project_resolution.py` rules before promotion.

**Why this priority**: Phase 2 needs unopposed comparables, and announced-date recovery needs independent dated sources. Epoch covers only very large campuses, so the plan records that sampling limit wherever the data is used.

**Independent Test**: Map a 10-row fixture and confirm schema conformity and that `data_source` carries the Epoch attribution string.

### User Story 5 - Agenda text is extracted into grounded candidate rows, locally (Priority: P3)

`scripts/agenda_extract.py` runs LangExtract with a local Ollama model on agenda or minutes text and emits candidate rows (jurisdiction, body, date, item, action, vote if stated) where every field carries its character offsets into the source text. A field without an offset is dropped. Output goes to a worklist that Price reviews and commits. The script never runs in CI.

**Why this priority**: It is the highest-effort item and depends on User Stories 1 and 2 producing text. Grounding makes each extracted field auditable to the exact passage.

**Independent Test**: Run on a fixture agenda with a known vote; confirm the vote tally's offsets slice the source text exactly.

### Edge Cases

- civic-scraper changes its API: pinned below the next major version; the fallback path keeps discovery working.
- Congress.gov key missing: the federal pass is skipped and recorded, and state sync is unaffected.
- Epoch rows duplicate existing proposals: matched and linked, never duplicated; unmatched rows stay candidates.
- The LLM returns a plausible field with no grounding: dropped by rule, counted in the run summary.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: civic-scraper MUST be imported only inside `local_meeting_feed.py`'s discovery layer, behind a try/except that falls back to the existing adapters.
- **FR-002**: OCRmyPDF MUST run only in workflows that install `tesseract-ocr` and `ghostscript`, and outputs MUST be cached by SHA-256.
- **FR-003**: Congress.gov access MUST use the `CONGRESS_API_KEY` secret and the existing bill-sync cache pattern.
- **FR-004**: Epoch ingestion MUST go through `permit_ingest.py` config, with no new ingestion code path (config-not-code convention).
- **FR-005**: `agenda_extract.py` MUST refuse to run when `CI` is set and MUST write only a draft worklist.
- **FR-006**: Every new output MUST be declared in `configs/layers.json`; nothing writes to `master_opposition.csv`.

## Success Criteria *(mandatory)*

- **SC-001**: Resolved jurisdictions in `local_meeting_feed.py` discovery at least double on the 808-jurisdiction frame.
- **SC-002**: All 43 federal legislative records either match a Congress.gov bill or are listed as unmatched with a reason.
- **SC-003**: Epoch candidates appear in the baseline candidate file with attribution, and the matched share is reported.
- **SC-004**: On a 20-agenda sample, 100 percent of retained extracted fields have valid offsets.

## Assumptions

- Price adds the `CONGRESS_API_KEY` secret (free signup at api.congress.gov).
- Price's Mac already runs Ollama for the weekly report and can run the extraction script.
- Epoch's CC-BY attribution is satisfied by the manifest entry plus a methodology footnote in any deliverable that uses the data.

## Changes on main since this spec (2026-09-29)

- **Legistar is covered.** `legistar_probe.py` (with `configs/legistar_clients.json` and `data/legistar_discovery.json`) finds county Legistar clients and records adopted moratorium matters as evidence. The civic-scraper story must use civic-scraper only for the platforms Legistar does not cover (CivicPlus, Granicus, PrimeGov), or wrap `legistar_probe.py`. It must not add a second Legistar client.
- **Proposal discovery exists.** `proposal_discovery.py` (news), `fetch_air_permits.py` (EPA ECHO), `fetch_planned_generation.py` (EIA-860M) and `fetch_grid_territory.py` (EIA-861 via PUDL) run in `proposals-intel.yml`. Matching new proposal rows, including the Epoch rows, must use the permanent keys in `data/project_key_map.csv`, because the source renumbers `prj_` ids (190 hand rows were re-keyed on 2026-09-28).
- These sources are registered as already-built in `configs/integrations.json`.
