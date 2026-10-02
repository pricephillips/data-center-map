# Tool integration audit: sessions 0 to 4 and 6 (2026-10-02)

This is a check of every tool `configs/integrations.json` assigns to sessions 0 to
4 and 6, on `main` at `a36d756`. Each tool was checked on six points:

- its targets exist;
- the code actually uses it;
- it is pinned;
- a workflow installs and runs it;
- its outputs exist and are fresh;
- the spec's success criteria are met.

All six specs (004 to 009) have every task checked off. A checked task list is
not the same as a tool working at full capacity; the gaps below are what the
task lists did not show.

## Summary

| Session | Tools | At full capacity | Gaps |
|---|---|---|---|
| 0 Setup | GitHub MCP, ast-grep, DuckDB CLI (Census MCP deferred) | yes | Census MCP superseded by the network allowlist plus `CENSUS_API_KEY`; the registry now says so |
| 1 CI and gates (004) | uv, ruff, actionlint, zizmor, pre-commit, pytest, Vale, daff, Dependabot | after this change | zizmor was report-only, which let 2 unpinned actions back in. `master_diff.py` mostly reported "No changes". 2 plate workflows (3 install steps) bypass the constraints |
| 2 Data quality (005) | Pandera, state normalizer, coverage delta, label disagreement, MEDSL | after the dispatched run | MEDSL `political` features had never been fetched |
| 3 Durability and dedupe (006) | Internet Archive, trafilatura, datasketch | no | archive coverage is 10 of 5,714 cited URLs |
| 4 Coverage (007) | civic-scraper, OCRmyPDF, pdfminer.six, Congress.gov, Epoch, LangExtract | mostly | agenda text throughput is 40 documents per run; LangExtract has not run yet (first run scheduled 2026-10-05) |
| 6 Map and frontend (009) | Census 2024 geometry, mapshaper, topojson-client, MapLibre, OpenFreeMap, Tabulator, Playwright, axe, slider, side panel | yes | none found |

## Evidence by tool

| Tool | Wired where | Evidence on main |
|---|---|---|
| uv + `requirements/ci.txt` | every workflow that installs, except below | `fetch-census-geo-ref.yml` used bare `pip install pandas` (fixed here). `plate-probe.yml` and `render-plates.yml` install `requirements/plates.in` unpinned (spec 013, not changed here) |
| ruff | `pipeline.yml` gate | blocking set clean |
| actionlint | `workflow-lint.yml` | 0 findings on all workflows |
| zizmor | `workflow-lint.yml` | was report-only; 2 high `unpinned-uses` in `fetch-census-geo-ref.yml`. Pinned here, and high severity now blocks |
| pre-commit | local only | all hooks pass |
| pytest selftests | `pipeline.yml` | 98 modules discovered |
| Vale | `pipeline.yml` | lints `headline_metrics.md`; nothing writes under `deliverables/` yet (spec 010) |
| daff / `master_diff.py` | `pipeline.yml` | compared to `HEAD~1`, usually an auto-build, so 4 of the last 8 summaries read "No changes". Fixed here: the default `--base auto` compares to the last real source change, and checkout depth goes from 2 to 50 |
| Dependabot | weekly | numpy and scipy are held for Python 3.12 |
| Pandera | `pipeline.yml` | 7 of 7 clean report-only runs, so the next clean run switches to blocking automatically. Allowed exceptions grew from 207 to 253 in eight runs; review the harvest no-state exception before it absorbs real defects |
| coverage delta, label disagreement | `pipeline.yml` | reports fresh (2026-10-01 and 2026-10-02) |
| MEDSL county returns | `fetch-features.yml` (quarterly) | `data/features/political.csv` absent: the workflow last ran 2026-09-28, before spec 005 added the source. Dispatched with `only=political` on 2026-10-02. The dispatch description now lists `political` |
| Internet Archive | `source-archive.yml` | 55 lookups, 1 save and 10 archived out of 5,714 cited URLs (0.2%). Each 30-minute run stops on its time budget at about 33 s per URL, anonymously (`IA_S3_*` secrets not set) |
| trafilatura | `article_extract.py`, imported by `signal_harvest.py` and `untagged_triage.py` | wired as designed |
| datasketch | `event_dedupe.py`, imported by `signal_harvest.py` and `status_resolution.py` | wired as designed |
| civic-scraper | `local-signals.yml` | 179 jurisdictions resolved through civic-scraper (SC-001: 3 before) |
| OCRmyPDF + pdfminer.six | `local-signals.yml` (installs tesseract and ghostscript) | 82 documents indexed: 24 with a text layer, 53 not PDFs, 5 fetch errors, 0 OCR'd so far. 40 documents per run |
| Congress.gov | `bill-sync.yml` | 49 federal records: 15 matched, 34 unmatched with a reason (SC-002 met). 5 of the unmatched say only "no federal bill identifier in record text"; a title search could recover some |
| Epoch AI | `fetch-permits.yml` config | 49 rows in the dated baseline, 332 candidates, 17 matches recorded |
| LangExtract | `agenda-extract.yml` | runs in CI by owner decision (`4a49585`); the registry said `runs_in: local` (fixed here). 0 runs so far; the first scheduled run is Monday 2026-10-05 |
| Census 2024 geometry + mapshaper | `acquire-geo-sources.yml` | all 3,222 counties, including the 13 previously missing. The registry target path is fixed here |
| Tabulator, MapLibre, OpenFreeMap, Playwright, axe, slider, panel | pages and `ui-check.yml` | present on all target pages; axe runs in `pages.spec.js` and `slider.spec.js`; latest UI check green |

## Changed in this pass

- `master_diff.py`:
  - `--base auto` is the new default (rules in the module docstring);
  - `at_rev` decodes bytes without newline translation, which a CRLF file needs;
  - the selftest has a new case, now 11/11.
- `pipeline.yml`: checkout `fetch-depth: 50`.
- `workflow-lint.yml`: zizmor blocks on high severity and still reports everything.
- `fetch-census-geo-ref.yml`: actions pinned to the repo's releases; pandas
  installed through uv with the constraints file.
- `fetch-features.yml`: the dispatch description lists `political`.
- `configs/integrations.json`:
  - LangExtract `runs_in: ci`, with the decision recorded;
  - geometry and mapshaper target paths corrected;
  - Census MCP moved to deferred, with a trigger.
- `specs/004-ci-hygiene-gates/contracts/cli.md`: the `--base` contract.

## Needs a decision or credentials (not changed here)

1. **Archive throughput.** Add `IA_S3_ACCESS_KEY` and `IA_S3_SECRET_KEY` as
   Actions secrets (a free archive.org account, then S3 keys) for the
   authenticated Save Page Now rate. Then raise `max_runtime_s`, or run
   `source-archive.yml` more than once a day. At today's rate a full pass takes
   over 100 nights.
2. **Agenda throughput.** `agenda_text.py --max-docs 40` per run limits both OCR
   and LangExtract input. Raise it once a week of runs shows the run time.
3. **Plate workflows (spec 013).** Compile `requirements/plates.in` into a
   constraints file and install with uv, as every other workflow does.
4. **Federal title search.** For the 5 federal records with no bill identifier
   that are not agency actions, try Congress.gov's search by title.
5. **Pandera exceptions.** Look at the growth from 207 to 253 allowed rows before
   the gate turns blocking on its own.
