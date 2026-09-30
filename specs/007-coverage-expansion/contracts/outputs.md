# Output Contract

| File | Writer | Layer | Mode | Committed by |
|------|--------|-------|------|--------------|
| `configs/local_meeting_sources.json` | `local_meeting_feed.py --discover` | configs | Rewritten; adds the keys `adapter`, `civic_scraper`, `civic_scraper_errors` | `local-signals.yml` (unchanged) |
| `data/agenda_text_index.csv` | `agenda_text.py --run` | E | Upsert by `sha256`, sorted | `local-signals.yml` |
| `.cache/agenda_text/<sha>.txt` | `agenda_text.py` | (gitignored) | Write once | `actions/cache` |
| `data/bill_sync_federal.csv` | `bill_sync.py` | D | Rewritten | `bill-sync.yml` |
| `data/bill_status_review.csv` | `bill_sync.py` | D | Rewritten; `venue` column appended | `bill-sync.yml` (unchanged) |
| `data/bill_sync_report.md` | `bill_sync.py` | D | Gains a federal section | `bill-sync.yml` (unchanged) |
| `data/permit_candidates_epoch_frontier_dc.csv` | `fetch_permits.py` | B (glob) | Rewritten | `fetch-permits.yml` (glob) |
| `data/baseline_dated_external.csv` | `permit_ingest.py` | E | Append, unmatched Epoch rows only | `fetch-permits.yml` (unchanged) |
| `data/baseline_external_matches_<stem>.csv` | `permit_ingest.py` | E | Rewritten per run | `fetch-permits.yml` |
| `data/agenda_extract_draft.csv` | `scripts/agenda_extract.py` | E | Rewritten; never committed by CI | Price, after review |

Every CSV is UTF-8 with LF line endings and a header row. None of them is
`master_opposition.csv`, and no module added or edited here opens that file
for writing.
