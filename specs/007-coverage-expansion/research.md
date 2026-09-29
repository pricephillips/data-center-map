# Research: Local, Federal, and Baseline Coverage Expansion

All measurements were taken on 2026-09-29 from this sandbox, against `main`
at `99d86b6`.

## Baselines measured

- **Discovery cache** (`configs/local_meeting_sources.json`): 880 entries.
  `none` 589, `ambiguous` 285, `legistar` 4, `granicus` 2. That is 6
  resolved, plus 2 manual overrides (Wyandotte civicclerk, Powhatan
  civicplus_rss).
- **Federal legislative records**: `bill_sync.py --extract` marks 43 records
  `federal_skip`. About 8 carry an explicit bill id in their text (H.R. 8037,
  S.4213, H.R. 7977, H.R. 8033, H.R. 9442 and others). The rest are agency
  or oversight actions (EPA, FERC, DOE, NERC, BLM, Senate letters) with no
  bill to match.
- **Epoch AI**: `https://epoch.ai/data/data_centers/data_centers.csv` (93
  campuses, 16 columns, no date and no state column; the state is inside
  `Address`) and `https://epoch.ai/data/data_centers/data_center_timelines.csv`
  (545 dated observations, keyed by `Data center`). Both return 200 from the
  sandbox. The documentation page says the data is "free to use, distribute,
  and reproduce provided the source and authors are credited under the
  Creative Commons Attribution license".
- **Reachability from the sandbox**: `webapi.legistar.com`, `*.primegov.com`,
  `*.granicus.com` and existing `*.civicplus.com` hosts answer. A
  non-existent CivicPlus host fails at the proxy with 502. `api.congress.gov`
  answers; `DEMO_KEY` is rate limited.
- **Existing gates**:
  - `leak_audit.py --tier blocking` reports 0;
  - `layer_audit.py --strict --no-write` reports 0 undeclared;
  - `integration_audit.py` passes.

## D1. civic-scraper scope: CivicPlus, Granicus and PrimeGov only

- **Decision**: Discovery asks civic-scraper about three platforms only:
  CivicPlus (`CivicPlusSite`), PrimeGov (`PrimeGovSite`) and Granicus
  (`GranicusSite`). Legistar stays with the native `probe_legistar()` for
  discovery and `legistar_probe.py` for matters. A selftest asserts that the
  civic-scraper platform table has no Legistar entry.
- **Rationale**: The "Changes on main" note forbids a second Legistar client.
  civic-scraper 1.1.0 depends on `scraper-legistar`, and its
  `civic_scraper.platforms` package imports that on load. This code never
  instantiates `LegistarSite`, so the dependency stays transitive and
  unused. CivicClerk is left to the native probe, which already works and
  has a confirmed override.
- **Alternatives considered**: Wrapping `legistar_probe.py` from discovery.
  Rejected because discovery already has a Legistar probe with a stricter
  county-name rule, and the task is coverage for the other platforms.

## D2. CivicPlus is state-qualified, so it can cover the ambiguous 285

- **Decision**: For CivicPlus, try
  `https://{st}-{bare}county.civicplus.com/AgendaCenter`, then
  `https://{st}-{bare}.civicplus.com/AgendaCenter`, with `st` the lowercase
  state code. Try these even for jurisdictions whose bare name is
  cross-state ambiguous. PrimeGov (`{slug}.primegov.com`) and Granicus
  (`{slug}.granicus.com`) keep the ambiguity skip.
- **Rationale**: The ambiguity rule exists because a slug without a state
  can land on the wrong state's client. A CivicPlus subdomain carries the
  state (`va-powhatancounty`, `tx-hayscounty`, both live), so a hit cannot
  be the wrong state. This is where most of the SC-001 headroom is: the
  cache holds 285 ambiguous entries, and CivicPlus has been override-only
  until now.
- **Alternatives considered**: Guessing custom government domains. Rejected
  because there is no rule for them, as the existing CivicPlus probe
  docstring already says.

## D3. What counts as "civic-scraper resolved"

- **Decision**: A platform resolves when `site.scrape()` returns at least
  one asset in a 120-day lookback window ending today. An exception, a
  zero-asset result or a timeout (20 s) is a miss for that platform, and the
  next candidate or the native probes run.
- **Rationale**: A provisioned-but-empty instance is indistinguishable from
  a placeholder page, so requiring an asset keeps the shape test at least as
  strict as the native probes' JSON-shape tests. In the sandbox, civic-scraper's
  Granicus parser raised `ValueError` on the LA County RSS feed because its
  titles do not split into three parts. That is exactly the drift FR-001
  guards against, and the fallback handles it.
- **Alternatives considered**: Accepting HTTP 200. Rejected because Granicus
  and CivicPlus serve generic pages for unknown clients.

## D4. Re-probe rule and cache keys

- **Decision**: Every entry that `discover_one()` writes carries two new
  keys:
  - `adapter`: `civic_scraper:<platform>`, `native:<platform>`, or `none`;
  - `civic_scraper`: the library version, or `unavailable`.

  During discovery, an entry is re-probed when it is `none` or `ambiguous`
  and its `civic_scraper` value is missing or `unavailable`, and
  civic-scraper is importable now. Resolved entries are never re-probed
  without `--redo`. `--max-probes N` (default 400) caps a run.
- **Rationale**: Spec scenario 1 says a resolved jurisdiction "is not
  re-probed on later runs". Existing `none` entries were probed before
  civic-scraper existed and must get one chance, or SC-001 cannot move. The
  version marker makes that chance happen exactly once per jurisdiction.
- **Alternatives considered**: A one-off `--redo`. Rejected because it would
  also re-probe the 6 resolved entries and repeat every native probe.

## D5. Cached platform names map to existing fetchers

- **Decision**: A civic-scraper hit records the existing platform name
  (`civicplus_rss`, `primegov` or `granicus`) and a base URL that the
  existing fetcher understands. `--fetch` is unchanged.
- **Rationale**: FR-001 limits civic-scraper to the discovery layer. The
  native fetchers already parse these platforms, and the CivicPlus RSS path
  (`/RSSFeed.aspx?ModID=65&CID=All-agendacenter`) exists on every
  `*.civicplus.com` host.

## D6. OCR module and text-layer test

- **Decision**: `agenda_text.py` does the following:
  1. Downloads each PDF linked from `data/local_meeting_feed.csv`
     (`document_url` ending in `.pdf`, or served as `application/pdf`).
  2. Hashes the bytes with SHA-256, and looks up
     `.cache/agenda_text/<sha>.txt` and `data/agenda_text_index.csv`.
  3. On a miss, extracts text with pdfminer.six. If the text has fewer than
     50 non-space characters per page on average, the PDF has no usable
     text layer. It then runs
     `ocrmypdf --skip-text -l eng --sidecar <txt> --output-type pdf <in> <tmp.pdf>`.
  4. Runs the keyword pass over the text with the fixed term list
     `data center(s)`, `datacenter`, `hyperscale`, `moratorium`,
     `rezoning`, `special use permit`, `conditional use`, and writes one
     index row per document.
- **Rationale**:
  - OCRmyPDF's `--skip-text` leaves pages with text alone. The per-document
    test decides whether OCRmyPDF is invoked at all, which is what "only the
    scanned one is OCR'd" measures.
  - pdfminer.six is already installed with OCRmyPDF, and it is the engine
    pdfplumber uses. The registry's "existing pdfplumber keyword pass" does
    not exist in the code (no module reads agenda PDFs), so this module is
    that pass.
  - The cache key is the content hash, not the URL, so a re-posted or
    renamed agenda is not processed twice.
- **Guard (FR-002)**: OCR runs only when `shutil.which("tesseract")` and
  `shutil.which("gs")` both succeed. Otherwise the row is `ocr_unavailable`,
  and the next run retries it. A selftest parses
  `.github/workflows/*.yml` and fails if a workflow runs `agenda_text.py`
  without installing both `tesseract-ocr` and `ghostscript`.
- **Alternatives considered**:
  - Committing the text itself. Rejected because agenda text is large and
    unreviewed, and it would enter the leak audit's scope.
  - Docling. Already eliminated in the registry.

## D7. Federal identifiers and the Congress number

- **Decision**: The federal regex accepts `H.R.`, `HR`, `S.`, `H.Res.`,
  `S.Res.`, `H.J.Res.`, `S.J.Res.`, `H.Con.Res.` and `S.Con.Res.`, with
  optional dots and spaces, followed by 1 to 5 digits. `S.` must not be
  preceded by a letter or a dot, so `U.S. 50` is not a Senate bill.
- **Congress number**: `(year - 1789) // 2 + 1`, from the record date. When
  the bill is not found there, the previous Congress is tried once.
- **Congress.gov type codes**: `hr`, `s`, `hres`, `sres`, `hjres`, `sjres`,
  `hconres`, `sconres`.

## D8. Federal stage mapping (Principle III)

- **Decision**: Congress.gov actions are mapped onto the existing `STAGES`
  ladder, terminal first:

  | Action evidence | Stage |
  |-----------------|-------|
  | `type` `BecameLaw`, or text `Became Public Law` / `Became Private Law` | Signed into law |
  | text `Passed over veto` / `veto overridden` in both chambers | Signed into law |
  | `type` `Veto`, or text `Vetoed by President` | Vetoed |
  | text `Failed of passage` / `not agreed to` | Failed floor vote |
  | text `Passed/agreed to in House` or `in Senate`, counted per distinct chamber | Passed one chamber / Passed both chambers |
  | text `Ordered to be Reported` / `Reported by` | Passed committee only |
  | anything else (`IntroReferral` and the like) | Introduced |

  A bill in a Congress that has ended, with no terminal action, is flagged
  `possible_sine_die_unconfirmed` (LOW), exactly as the state pass does for
  stale bills. It is never auto-coded.
- **Rationale**: The same precedence as `classify_actions()`, so a milestone
  can never outrank a terminal action. The one-chamber case is Pending by
  construction, and a selftest asserts it on a mocked response.

## D9. Federal output and missing key

- **Decision**:
  - `data/bill_sync_federal.csv` gets one row per federal record, whatever
    happens to it.
  - Review rows go to `data/bill_status_review.csv` with the new appended
    column `venue` (`state` or `federal`).
  - A federal-only run (`--federal`) replaces only the `venue=federal` rows
    of the existing review file. Rows written before the column existed
    count as state.
  - When `CONGRESS_API_KEY` is empty, every row is `skipped_no_key`, the
    report says so, and the exit code is 0.
- **Rationale**: SC-002 needs a per-record answer, and the review file is
  the one place a reviewer already works. Replacing by venue keeps the state
  and federal passes independent.

## D10. Epoch mapping without new ingestion code (FR-004)

- **Decision**: The fetch and ingest configs carry all Epoch knowledge:

  | Field | Mapping |
  |-------|---------|
  | name | `Name` |
  | announced_date | earliest `Date` in the timelines file for that `Data center` |
  | state | regex `,\s*([A-Z]{2})\s+\d{5}` on `Address` |
  | capacity_mw | `Current power (MW)` |
  | operator | `Owner` (the `#confident` tags are stripped by a `strip_regex`) |
  | status | none |
  | source_url | `https://epoch.ai/data/data-centers` |
  | source | `Epoch AI Frontier Data Centers (CC-BY 4.0)` |

  Non-US rows fail the state regex and go to rejects with a reason.
- **Matching**: `match_projects` runs before any row is appended. Each row is
  compared with every `data/project_key_map.csv` row in the same state,
  using `project_resolution.name_tokens` and `project_resolution.jaccard`:
  - name Jaccard of 0.60 or more with the state agreeing is `confirmed`;
  - 0.34 or more is `review`;
  - two confirmed keys make the row `review`.

  Confirmed and review rows are held out of the baseline and written to the
  matches file with the `pk`. Unmatched rows are appended. The run prints
  the matched share (SC-003).
- **Rationale**:
  - The external tier is "presumed-unopposed comparables". A tracked project
    (Colossus 2 is in the key map, and heavily opposed) must never be added
    to it a second time. The `pk` is the permanent key, so a link survives
    the source renumbering.
  - The earliest timeline date is the first dated observation of the
    campus, usually "land clearing begins". It is a bound on the
    announcement, never the announcement itself. The ingest config and the
    manifest say so.
- **Sampling limit**: Epoch covers frontier-scale campuses only, 93 worldwide.
  Recorded in the manifest `notes`, the ingest config `sampling_note`, and
  the matches file header comment in the run output. Any use in a
  comparison must state it.
- **Alternatives considered**: An `epoch_ingest.py`. Forbidden by FR-004.

## D11. LangExtract grounding rule

- **Decision**: A field is kept only when all of these hold:
  - its `char_interval` exists;
  - `0 <= start < end <= len(text)`;
  - `text[start:end]` equals the extracted string exactly.

  Anything else is dropped and counted as `no_offset`, `out_of_bounds` or
  `offset_mismatch`. Rows are assembled in offset order:
  - `jurisdiction`, `body` and `date` are meeting-level and apply to every
    row;
  - each `item` starts a new row;
  - `action` and `vote` attach to the most recent `item`.

  A row with no retained `item` is dropped.
- **Rationale**: SC-004 needs 100 percent valid offsets on retained fields.
  An exact-slice test makes that true by construction. Fuzzy alignments are
  dropped rather than trusted.
- **Model**: The default is `gemma2:2b` on `http://localhost:11434`, and both
  can be overridden. The model call sits behind an injectable `extract_fn`,
  which the selftest stubs.

## D12. Requirements pins

- **Decision**: Append the four packages to `requirements/ci.in`, and
  compile in place with
  `uv pip compile requirements/ci.in --python-version 3.11 -o requirements/ci.txt`.
  uv prefers pins already in the output file, so every existing line is
  kept.
- **Rationale**: `ci.txt` is used only as a constraints file (`-c`).
  Pinning LangExtract there installs nothing in CI. civic-scraper pulls
  `demjson3` and `esprima`, which ship as sdists. `uv pip install` builds
  both; plain `pip` failed to build them in this sandbox, which is one more
  reason every workflow installs with `uv`.
