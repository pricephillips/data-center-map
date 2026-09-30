# Quickstart: validating spec 007

Prerequisites: Python 3.11 and `uv`. For the OCR scenario you also need
`tesseract-ocr` and `ghostscript`. The data flow is described in
[data-model.md](data-model.md), the commands in [contracts/cli.md](contracts/cli.md).

```bash
uv pip install --system -c requirements/ci.txt civic-scraper ocrmypdf pdfminer.six
```

## Offline gates (all stories)

```bash
python -m pytest tests/test_selftests.py -q
python leak_audit.py --tier blocking          # 0 blocking
python layer_audit.py --strict --no-write     # 0 undeclared
python integration_audit.py                   # exit 0
pre-commit run --all-files
```

Each touched module's selftest covers its story offline:

| Story | Selftest | Asserts |
|-------|----------|---------|
| US1 | `local_meeting_feed.py --selftest` | civic-scraper hit recorded as `adapter=civic_scraper:<platform>`; a raise falls back to native probes and is logged; no Legistar in the civic-scraper table; resolved entries are not re-probed; the ambiguous rule is relaxed for CivicPlus only |
| US2 | `agenda_text.py --selftest` | Scanned fixture is OCR'd and the text fixture is not; "data center" is found in both; a second run is a cache hit; the workflow guard (the OCR round trip is SKIP without tesseract) |
| US3 | `bill_sync.py --selftest` | A mocked one-chamber passage is Pending; a signature is terminal; federal ids are parsed; a missing key gives `skipped_no_key` and exit 0; the review file's `venue` column is set |
| US4 | `permit_ingest.py --selftest`, `fetch_permits.py --selftest` | The 10-row Epoch fixture conforms to the schema; `source` carries the attribution; a tracked project is held out with its `pk`; non-US rows are rejected |
| US5 | `scripts/agenda_extract.py --selftest` | Refuses when `CI` is set; the vote tally's offsets slice the source exactly; an ungrounded field is dropped and counted; a non-draft output path is refused |

## Live scenarios (CI or a networked machine)

1. **SC-001**: `python local_meeting_feed.py --compare 50`. Both counts are
   printed. Then run `--discover` and compare the resolved count in the cache
   with the 6 recorded in research.md.
2. **US2**: `python agenda_text.py --run --max-docs 5`. Then inspect
   `data/agenda_text_index.csv` for `text_source` values.
3. **SC-002**: `CONGRESS_API_KEY=... python bill_sync.py --federal`.
   `data/bill_sync_federal.csv` has 43 rows, each `matched` or carrying an
   `unmatched_reason`. Without the key, the same command exits 0 with every
   row `skipped_no_key`.
4. **SC-003**: `python fetch_permits.py --config configs/epoch_frontier_dc.json`,
   then `python permit_ingest.py --in data/permit_candidates_epoch_frontier_dc.csv --config configs/epoch_frontier_dc_ingest.json --out data/baseline_dated_external.csv --append`.
   The run prints the matched share, and the matches file lists the held
   rows.
5. **SC-004** (Price's Mac, Ollama running):
   `python scripts/agenda_extract.py --in agenda1.txt ... --in agenda20.txt`.
   The summary reports 100 percent of retained fields with valid offsets, by
   construction, and counts the dropped fields.
