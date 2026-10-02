# Implementation Plan: Local, Federal, and Baseline Coverage Expansion

**Branch**: `007-coverage-expansion` (worked on `claude/epic-pascal-zdx2xy`) | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-coverage-expansion/spec.md`, including its "Changes on main since this spec (2026-09-29)" section, and the session 4 brief.

## Summary

Five additions. None writes to `master_opposition.csv` (Principle VII), and
every new output is declared in `configs/layers.json` (FR-006).

1. **civic-scraper in discovery (US1).** `local_meeting_feed.py` gains one
   discovery function, `probe_civic_scraper()`. It imports civic-scraper
   inside a `try`, and only for CivicPlus, Granicus and PrimeGov. It runs
   before the native probes. Any exception it raises is logged, and the native
   probes run as before (FR-001).
   - Legistar is never asked of civic-scraper. The native `probe_legistar()`
     and `legistar_probe.py` stay the only Legistar clients (research D1).
   - CivicPlus hosts carry the state (`va-powhatancounty.civicplus.com`), so
     CivicPlus is also tried for the 285 jurisdictions that discovery now
     skips as cross-state name-ambiguous. PrimeGov and Granicus slugs carry
     no state, so the ambiguity rule still applies to them (D2).
   - Each cache entry records `adapter` (`civic_scraper:<platform>` or
     `native:<platform>`) and `civic_scraper` (the library version, or
     `unavailable`). The `platform` value always names an existing fetcher,
     so `--fetch` is unchanged.
   - A `none` or `ambiguous` entry probed before this feature is re-probed
     once, when civic-scraper is importable. A resolved entry is never
     re-probed (D4).
   - `--compare N` runs discovery on a fixed N-jurisdiction sample with and
     without civic-scraper, prints both counts, and writes nothing. This is
     the spec's independent test.
2. **OCR before the keyword pass (US2).** A new module, `agenda_text.py`, is
   the only code that imports OCRmyPDF and pdfminer.six.
   - It reads PDF links from `data/local_meeting_feed.csv` and hashes each
     PDF with SHA-256.
   - It extracts the text layer with pdfminer.six. A PDF without a usable
     text layer goes through `ocrmypdf --skip-text -l eng --sidecar`.
   - The text is cached at `.cache/agenda_text/<sha256>.txt`, restored in CI
     through `actions/cache`, so each PDF is processed once.
   - It runs a keyword pass over the text and writes
     `data/agenda_text_index.csv`: one row per document, with the hash, text
     source (`text_layer`, `ocr`, `ocr_unavailable`, `fetch_error`) and
     keyword hits.
   - OCR runs only when `tesseract` and `gs` are on PATH. A selftest checks
     that every workflow invoking `agenda_text.py` installs `tesseract-ocr`
     and `ghostscript` (FR-002).
3. **Congress.gov federal pass (US3).** `bill_sync.py` gains a federal pass.
   - It extracts federal identifiers (`H.R. 8037`, `S. 4213`, `H.Res.`,
     `S.J.Res.`) from the 43 federal records, and derives the Congress number
     from the record date.
   - It pulls each bill's actions from Congress.gov v3 through the existing
     `Cache` class (keys `US:<congress>:<type>:<number>`).
   - It maps actions onto the existing `STAGES` ladder, terminal first: a
     bill that passed one chamber is `Passed one chamber`, which is Pending
     (Principle III).
   - Disagreements are written to `data/bill_status_review.csv` with the new
     appended column `venue=federal`. State rows get `venue=state`.
   - `data/bill_sync_federal.csv` lists every federal record with its match,
     or with the reason it is unmatched (SC-002).
   - `CONGRESS_API_KEY` is read from the environment. Without it the federal
     pass is skipped, recorded in the federal file and the report, and the
     run exits 0. State sync is unaffected.
4. **Epoch AI through permit_ingest config (US4).** No Epoch-specific code
   (FR-004). The work is two configs plus three generic, config-driven
   options on the existing fetch and ingest path, each with a selftest.
   - `configs/epoch_frontier_dc.json` is a `tabular` fetch config that
     `fetch-permits.yml` picks up automatically.
   - `configs/epoch_frontier_dc_ingest.json` is the column map.
   - `fetch_permits.py` gains a generic `earliest_date_from` option. It
     attaches the earliest dated observation from a second CSV, which here
     is Epoch's timelines file, because the campus file carries no date.
   - `permit_ingest.py` gains two generic options:
     - `state_from` pulls the two-letter state out of an address with a
       regex;
     - `match_projects` matches each row to `data/project_key_map.csv` with
       the `project_resolution.py` name and state rules.
   - Matched and review-tier rows are held out of
     `data/baseline_dated_external.csv`, so a tracked (and possibly opposed)
     project never enters the baseline as an unopposed comparable. They are
     written, with their `pk`, to `data/baseline_external_matches_<source>.csv`.
     Unmatched rows are appended as candidates.
   - The `source` value is the attribution string
     `Epoch AI Frontier Data Centers (CC-BY 4.0)`.
   - The CC-BY attribution and the very-large-campus sampling limit are
     recorded in the source manifest (`configs/facility_sources.json`, entry
     `epoch_frontier`).
5. **Grounded agenda extraction, local only (US5).**
   `scripts/agenda_extract.py` wraps LangExtract with a local Ollama model.
   - It refuses to run when `CI` is set, and exits 2. `--selftest` is the one
     mode allowed in CI, because it uses a stubbed model.
   - It keeps a field only when its character offsets are in bounds and
     slice the source text to exactly the extracted string.
   - It writes only a `*_draft.csv` worklist under `data/`. Any other output
     path is refused.
   - The run summary counts the fields it dropped, by reason.

Workflow edits go to `local-signals.yml`, `bill-sync.yml` and
`fetch-permits.yml`. Any edit that cannot be pushed from this sandbox is
staged as `docs/pending_coverage_expansion.patch` with a matching `.md`.

## Technical Context

**Language/Version**: Python 3.11 (CI 3.11.16; sandbox 3.11.15).

**Primary Dependencies**:

- New in `requirements/ci.in`:
  - `civic-scraper>=1.1,<2` (Apache-2.0), resolved at 1.1.0;
  - `ocrmypdf>=17,<18` (MPL-2.0), resolved at 17.13.0;
  - `pdfminer.six` (MIT), already a dependency of OCRmyPDF;
  - `langextract>=1.7,<2` (Apache-2.0), resolved at 1.7.0. It is pinned so
    local installs are reproducible, and no workflow installs it.
- Every tool is already registered in `configs/integrations.json` (session
  4). pdfminer.six is added as a registry entry targeting `agenda_text.py`.
- `requirements/ci.txt` is recompiled in place, so uv keeps every existing
  pin. A fresh compile would have moved fonttools 4.66.0 to 4.66.1; the
  in-place compile does not.
- Congress.gov API v3, called with the standard library only (bill_sync.py
  is stdlib-only).
- System packages `tesseract-ocr` and `ghostscript`, installed with apt, and
  only in `local-signals.yml`.

**Storage**: CSV, JSON and Markdown in the repo. OCR text lives in a
gitignored `.cache/agenda_text/` directory that CI persists with
`actions/cache`.

**Testing**:

- Every new or touched module gets a `--selftest`, which
  `tests/test_selftests.py` discovers. None touches the network.
- Third-party packages are replaced by fakes in `sys.modules`, so each
  selftest passes in the blocking discovery run, where the packages are not
  installed. The one exception is the real OCR round trip in
  `agenda_text.py`: it prints `SKIP` when ocrmypdf, tesseract or gs is
  absent.
- Fixtures live in `tests/fixtures/coverage_expansion/`:
  - a scanned PDF and a text PDF;
  - a 10-row Epoch fixture and a timelines fixture;
  - Congress.gov bill and action responses;
  - an agenda text with a known vote.

**Target Platform**: GitHub Actions `ubuntu-latest`, and Price's Mac for
`scripts/agenda_extract.py`.

**Project Type**: Script-based data pipeline: root-level modules and CI
workflows.

**Performance Goals**:

- Discovery re-probes about 870 cached `none`/`ambiguous` entries once. Most
  candidate hosts do not exist and fail at DNS or connect in under a second.
  Each civic-scraper call has a 20 s timeout. `--max-probes` caps a run
  (default 400) so the job stays well under the 6-hour runner limit. The rest
  carry over to the next weekly run, because the cache is saved every 10
  entries.
- The OCR pass is capped at 40 new PDFs per run, and skips any PDF over
  25 MB. Cached hashes cost nothing.
- The federal pass makes about 20 Congress.gov calls: the records that carry
  a bill id, each cached.

**Constraints**:

- `master_opposition.csv` is unchanged by every new path.
- Discovery never raises because of civic-scraper, including on import
  failure, API drift or a parser error. It falls back instead.
- There is only one Legistar client path.
- A missing `CONGRESS_API_KEY` means exit 0.
- Epoch rows never duplicate a tracked project.
- `agenda_extract.py` never runs in CI and never writes outside a draft
  file.

**Scale/Scope**:

- Two new modules: `agenda_text.py` and `scripts/agenda_extract.py`.
- Edits to `local_meeting_feed.py`, `bill_sync.py`, `fetch_permits.py`,
  `permit_ingest.py`, `configs/*`, `ARCHITECTURE.md`, `requirements/*`,
  `.gitignore` and three workflows.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| 1. `leak_audit.py --tier blocking` = 0 | **PASS (verify at end)** | New CSVs carry identifiers, dates, counts, matched terms from a fixed list, and verbatim bill titles or action text. No agenda text is committed: the index stores counts and terms, and the text stays in the gitignored cache. |
| 2. `layer_audit.py` = 0 undeclared | **REQUIRES ACTION** | New outputs are declared in the table below. ARCHITECTURE.md gets writer lines. No new crossing: `permit_ingest.py` writes only Layer E. |
| 3. `--selftest` on touched and new modules | **REQUIRES ACTION** | New: `agenda_text.py`, `scripts/agenda_extract.py`. Extended: `local_meeting_feed.py`, `bill_sync.py`, `fetch_permits.py`. `permit_ingest.py` gains its first selftest. |
| 4. `node --check` on touched JS | **N/A** | No JS is touched. |
| 5. No em-dashes, no CRLF | **PASS (verify at end)** | Every writer uses `lineterminator="\n"`. |
| 6. docx `--original` validation | **N/A** | No docx. |

New outputs and their layers:

| File | Writer | Layer |
|------|--------|-------|
| `data/agenda_text_index.csv` | `agenda_text.py` | E (explicit entry) |
| `data/bill_sync_federal.csv` | `bill_sync.py` | D (already covered by `data/bill_*.csv`; listed explicitly) |
| `data/bill_status_review.csv` gains a `venue` column | `bill_sync.py` | D (unchanged) |
| `data/baseline_external_matches_*.csv` | `permit_ingest.py` | E (covered by `data/baseline_*.csv`; listed explicitly) |
| `data/agenda_extract_draft.csv` | `scripts/agenda_extract.py` | E (explicit entry) |
| `configs/local_meeting_sources.json` gains two keys per entry | `local_meeting_feed.py` | `configs/*` (not a layer) |

Principle checks:

- **I.** Every federal stage cites its Congress.gov URL. Every extracted
  field cites its exact offsets. Epoch rows carry the attribution and the
  source URL.
- **II.** No generated prose other than counts. The keyword list is fixed
  and activity-descriptive.
- **III.** The federal mapping reuses `STAGES` and the terminal-first
  precedence. A one-chamber passage is Pending, and a selftest asserts it.
  An ended Congress with no terminal action is flagged
  `possible_sine_die`, never auto-coded.
- **VII.** Everything is additive. The review file gains an appended
  column. Epoch candidates enter only the external baseline, and only when
  unmatched. The extraction output is a draft.
- **VIII.** Each new file has exactly one writer.
- **IX.** Every touched module ships a selftest, and discovery wires it in.

**Post-design re-check**: every gate resolves within scope, and there are no
unjustified violations.

**Implementation status (2026-09-29)**: T001-T034 are done. All gates were
run after merging `origin/main`:

- `pre-commit run --all-files` is green;
- `python -m pytest tests/test_selftests.py` passes (92 modules);
- `leak_audit.py --tier blocking` reports 0;
- `layer_audit.py --strict --no-write` reports 0 undeclared;
- `integration_audit.py` passes;
- `ruff check .` and actionlint are clean.

The new selftests also pass with civic-scraper, pdfminer.six and OCRmyPDF
made unimportable, which is the state of the blocking discovery run.

Measured from the sandbox:

- **SC-001 (sample)**: `--compare 50` resolved 2 jurisdictions without
  civic-scraper and 5 with it. Two of the three new hits were
  cross-state-ambiguous names (MA Franklin, WA Douglas), reached through the
  state-qualified CivicPlus host. civic-scraper's Granicus parser raised on
  Mendocino's feed, and the native probe resolved it, which is FR-001's
  fallback working on real data. The full-frame number comes from the first
  `local-signals.yml` run, which re-probes the cached misses 400 at a time.
- **SC-002**: offline, all 43 federal records are listed. 9 carry 15 bill
  identifiers, and 34 have none (agency, oversight or letter records), each
  with its reason. The match count needs `CONGRESS_API_KEY` in
  `bill-sync.yml`.
- **SC-003**: a live run over Epoch's export gave 77 US campuses. 66 mapped;
  11 were rejected because their address names no state. 14 of the 66 (21%)
  match a tracked project: 10 confirmed, 4 for review. All 14 are held out of
  the baseline. 52 are candidates. Nothing was written to repo data from the
  sandbox: the weekly `fetch-permits.yml` run does that.
- **SC-004**: exact-slice grounding makes 100 percent valid offsets true by
  construction, and the selftest asserts it on the fixture. The 20-agenda
  run is Price's, on a machine with Ollama.

Owner actions: add the `CONGRESS_API_KEY` repository secret (free signup at
api.congress.gov).

## Project Structure

### Documentation (this feature)

```text
specs/007-coverage-expansion/
├── plan.md              # This file
├── research.md          # Phase 0: decisions D1-D12
├── data-model.md        # Phase 1: cache entry, text index, federal row, Epoch mapping, draft row
├── quickstart.md        # Phase 1: validation scenarios per user story and SC
├── contracts/
│   ├── cli.md           # command lines, flags, exit codes
│   ├── config.md        # new config keys (fetch, ingest, discovery)
│   └── outputs.md       # file formats and appended columns
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
local_meeting_feed.py                 # EDIT probe_civic_scraper, adapter/civic_scraper cache keys, re-probe rule, --compare, --max-probes
agenda_text.py                        # NEW  SHA-256 cache, text-layer test, OCRmyPDF --skip-text, keyword pass; --selftest
bill_sync.py                          # EDIT federal ids, Congress.gov client, action ladder, venue column, bill_sync_federal.csv, --federal
fetch_permits.py                      # EDIT tabular earliest_date_from option
permit_ingest.py                      # EDIT state_from, match_projects, --selftest
scripts/agenda_extract.py             # NEW  LangExtract + Ollama, CI refusal, offset grounding, draft-only; --selftest
configs/epoch_frontier_dc.json        # NEW  tabular fetch config (Epoch campuses + timelines)
configs/epoch_frontier_dc_ingest.json # NEW  permit_ingest column map, attribution, sampling note
configs/facility_sources.json         # EDIT epoch_frontier: license CC-BY-4.0, attribution, sampling limit, export URL
configs/layers.json                   # EDIT declare new outputs
configs/integrations.json             # EDIT pdfminer.six entry; OCR target is agenda_text.py
ARCHITECTURE.md                       # EDIT writer lines
requirements/ci.in, ci.txt            # EDIT civic-scraper, ocrmypdf, pdfminer.six, langextract
.gitignore                            # EDIT .cache/
tests/fixtures/coverage_expansion/    # NEW  fixtures
.github/workflows/local-signals.yml   # EDIT civic-scraper install, apt tesseract/ghostscript, OCR step, cache, commit index
.github/workflows/bill-sync.yml       # EDIT CONGRESS_API_KEY, federal pass, commit federal file
.github/workflows/fetch-permits.yml   # EDIT commit baseline_external_matches_*.csv
```

**Structure Decision**: Follow the flat layout. Each third-party import
lives in one module: civic-scraper in `local_meeting_feed.py`'s discovery
function, OCRmyPDF and pdfminer.six in `agenda_text.py`, LangExtract in
`scripts/agenda_extract.py`. That keeps the fallback and SKIP logic in one
place per package.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Three generic options added to `fetch_permits.py` and `permit_ingest.py` for an ingestion that FR-004 says is config-only | Epoch's campus CSV has no date and no state column, and its dates live in a second file. Without a join, a state extractor and a project matcher, the config maps zero valid rows. Matching is also needed so a tracked project never enters the baseline as an unopposed comparable. | An Epoch-specific ingest script is what FR-004 forbids. The options are source-agnostic, each is off unless a config names it, and each has a selftest, so the ingestion path stays one path. |
| `scripts/agenda_extract.py --selftest` runs in CI although the script refuses CI | Principle IX puts every module's selftest in the blocking discovery run. | Exempting the script would leave the offset rule untested in CI. The selftest uses a stub model and makes no network call, so it is not an extraction run. |

**Follow-up (2026-10-01, owner decision)**: US5 runs in CI. The sandbox
network policy blocks Ollama's hosts, so the local-only run had no machine to
run on. `agenda-extract.yml` (weekly, and on dispatch) installs Ollama inside
the runner, pulls `gemma2:2b`, restores the agenda text cache that
`local-signals.yml` writes, and runs
`scripts/agenda_extract.py --allow-ci --from-index 20`.

- The model is still local to the job, and no agenda text leaves it.
- Every retained field is still grounded by exact slice, and the output is
  still only `data/agenda_extract_draft.csv`, which the workflow commits for
  review.
- `--allow-ci` is the only way past the CI refusal, and only this workflow
  passes it. FR-005's refusal stays the default.
- `--from-index` reads only the passages around keyword hits, so a CPU runner
  finishes inside the job timeout. Window offsets are shifted back, so
  grounding is checked against the full document.

The federal pass needed no change: the `CONGRESS_API_KEY` repository secret
was already set. On 2026-10-01 `bill-sync.yml` matched 15 of 15 federal bill
identifiers, and 7 records went to review. The Epoch fetch added 49 candidates
to the dated baseline and held 17 that match tracked projects.
