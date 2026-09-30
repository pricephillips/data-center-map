# CLI Contract

## local_meeting_feed.py (extended)

| Command | Behavior | Exit |
|---------|----------|------|
| `--discover [--state XX] [--redo] [--max-probes N]` | Probes missing entries, plus `none`/`ambiguous` entries that civic-scraper has not tried. civic-scraper runs first, then the native probes. `--max-probes` defaults to 400. | 0 |
| `--discover --no-civic-scraper` | Native probes only. Entries are marked `civic_scraper: unavailable`, so a later run with the library re-probes them. | 0 |
| `--compare N [--state XX]` | Takes the first N jurisdictions of the frame in sorted order, runs the discovery logic with and without civic-scraper, and prints both resolved counts. Writes nothing. | 0 |
| `--fetch`, `--selftest` | Unchanged. | 0 / 1 |

## agenda_text.py (new)

| Command | Behavior | Exit |
|---------|----------|------|
| `--run [--max-docs 40] [--max-mb 25]` | Processes PDF links from `data/local_meeting_feed.csv`, then updates `data/agenda_text_index.csv` and `.cache/agenda_text/`. | 0 (per-document errors are recorded, not raised) |
| `--file PATH` | Processes one local PDF and prints its index row. For checks by hand. | 0 |
| `--selftest` | Offline. The real OCR round trip is `SKIP` without ocrmypdf, tesseract or gs. | 0 / 1 |

## bill_sync.py (extended)

| Command | Behavior | Exit |
|---------|----------|------|
| `--extract` | As before. Federal rows keep `federal_skip` in the worklist, and it also writes `data/bill_sync_federal.csv` with `no_bill_id` or `pending_lookup`. | 0 |
| `--resolve` | As before, then the federal pass when `CONGRESS_API_KEY` is set (`skipped_no_key` otherwise). | 0, or 1 if `OPENSTATES_API_KEY` is missing (unchanged) |
| `--federal` | Federal pass only. Replaces only the `venue=federal` rows of the review file. With no key, it records the skip and exits 0. | 0 |

## fetch_permits.py, permit_ingest.py

The command lines are unchanged. The new behavior comes from config keys
(see `config.md`). `permit_ingest.py` gains `--selftest`.

## scripts/agenda_extract.py (new, local only)

| Command | Behavior | Exit |
|---------|----------|------|
| `--in FILE.txt [--in ...] [--model gemma2:2b] [--model-url http://localhost:11434] [--out data/agenda_extract_draft.csv]` | Extracts grounded candidate rows and prints a summary with drop counts by reason. | 0 |
| any extraction mode with `CI` set | Prints a refusal and does no work. | 2 |
| `--out` not ending `_draft.csv`, not under `data/`, or naming `master_opposition*.csv` | Refused. | 2 |
| `--selftest` | Stub model and fixture agenda. Allowed under `CI`. | 0 / 1 |
