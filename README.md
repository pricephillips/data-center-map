# Renewable Opposition Pipeline README

This repository contains two related workflows: an **opposition pipeline** for tracking community opposition to data center projects, and a **developments / map pipeline** for maintaining a master map of data center developments and location matches.[file:100][file:101][file:109][file:112]

The opposition side combines historical records, new event discovery, PDF backfill, CSV merge logic, queue-based review, and Notion import scripts. The developments side geocodes, reconciles Aterio location matches, updates Notion location fields, and rebuilds the public HTML map.[file:102][file:103][file:104][file:105][file:106][file:108][file:113][file:114][file:115]

## What this pipeline does

- Maintains a master opposition dataset with historical incidents plus newly discovered opposition events.[file:100][file:101][file:102][file:103]
- Supports backfilling historical opposition entries from the attached PDF source into CSV form before merge/import.[file:106][file:107]
- Pushes reviewed opposition records into Notion, either in bulk from CSV or from a processing queue.[file:104][file:105]
- Maintains a separate developments master map workflow for project locations, coordinates, matches, and map publishing artifacts.[file:108][file:109][file:110][file:112][file:113][file:114][file:115]

## Main files

| File | Role |
|---|---|
| `discover_new_opposition_events.py` | Discovers or stages newly identified opposition events for review/import.[file:102] |
| `merge_datacentertracker_into_master.py` | Merges source opposition data into the master opposition CSV.[file:103] |
| `backfill_historical_opposition_from_pdf.py` | Extracts historical opposition items from the PDF source into structured rows.[file:106][file:107] |
| `import_opposition_csv_to_notion.py` | Bulk-imports opposition CSV rows into Notion.[file:104] |
| `process_opposition_queue_to_notion.py` | Processes a queue of opposition records into Notion, likely after review/triage.[file:105] |
| `master_opposition_backup.csv` | Backup of the master opposition dataset with 1009 data rows.[file:100] |
| `data-center-tracker.csv` | Source opposition-style CSV used as an input to merge/discovery steps, with 959 data rows.[file:101] |
| `developments_master.csv` | Master developments dataset used to build the large HTML map, with 29 data rows.[file:112] |
| `build_developments_master_map.py` | Builds or refreshes the master developments HTML map artifact.[file:108][file:109] |
| `Generate_Full_Map.py` | Generates a full map artifact from developments/location inputs.[file:111] |
| `Find-Coords-Script.py` | Finds or fills coordinates for developments/location records.[file:114] |
| `update_developments_location_in_notion.py` | Updates development location fields in Notion.[file:113] |
| `Aterio-Uploader.py` | Uploads matched development/location data into Aterio or an adjacent system.[file:115] |
| `Aterio_Location_Matches.csv` | Location reconciliation table used by the developments workflow.[file:110] |
| `master_datacenter_map.html` | Generated HTML output for the developments master map.[file:109] |

## Recommended run order

### Opposition workflow

1. Backfill historical records from the PDF when needed: `python backfill_historical_opposition_from_pdf.py`.[file:106][file:107]
2. Discover or stage newly found events: `python discover_new_opposition_events.py`.[file:102]
3. Merge source data into the master opposition CSV: `python merge_datacentertracker_into_master.py`.[file:103]
4. Review the resulting CSV before upload, especially dates, state/county fields, duplicates, source URLs, and outcome/status fields.[file:100][file:101][file:103]
5. Push to Notion either as a bulk CSV import with `python import_opposition_csv_to_notion.py` or through the queue processor with `python process_opposition_queue_to_notion.py`.[file:104][file:105]

### Developments workflow

1. Refresh coordinate data with `python Find-Coords-Script.py` as needed.[file:114]
2. Update location matches and the developments master CSV, including `Aterio_Location_Matches.csv` and `developments_master.csv` where applicable.[file:110][file:112]
3. Sync location changes into Notion with `python update_developments_location_in_notion.py`.[file:113]
4. Upload or reconcile downstream location data with `python Aterio-Uploader.py` if that system is part of the run.[file:115]
5. Rebuild the map output with `python build_developments_master_map.py` or `python Generate_Full_Map.py`, then review `master_datacenter_map.html`.[file:108][file:109][file:111]

## Setup

The scripts are Python-based, and several of them clearly integrate with Notion and CSV/PDF workflows, so the minimum setup should include Python 3.10+ plus the environment variables or secrets needed for Notion access before any write operations are attempted.[file:104][file:105][file:106][file:113]

Suggested setup:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If there is no `requirements.txt`, install dependencies script-by-script after checking imports. The attached scripts strongly suggest dependencies in the CSV, PDF, requests, and Notion/API categories, so build that list from actual imports in the repo before standardizing the environment.[file:102][file:104][file:105][file:106][file:113]

## Required inputs and outputs

| Workflow | Key inputs | Key outputs |
|---|---|---|
| Opposition | `master_opposition_backup.csv`, `data-center-tracker.csv`, PDF source, queue/review data.[file:100][file:101][file:107] | Updated master opposition CSV and Notion records.[file:103][file:104][file:105] |
| Developments | `developments_master.csv`, `Aterio_Location_Matches.csv`, geocoded locations.[file:110][file:112][file:114] | Updated Notion location data and rebuilt map HTML such as `master_datacenter_map.html`.[file:108][file:109][file:113] |

## Context and data model

Based on the attached datasets and the earlier dashboard work in this space, the opposition dataset is intended to support an interactive public-facing tracker that groups incidents by state, county, outcome, severity, source, and related campaign metadata.[file:100][file:101]

The discussions in this space also establish an important editorial rule for that tracker: visible UI labels should avoid naming specific upstream source brands directly, and instead use neutral wording such as “Historical archive” and “Ongoing monitoring” in the published interface.[conversation_history:1]

Practically, that means the pipeline can preserve raw source identifiers internally for merge logic if needed, but the public-facing layer should map them to neutral display labels before rendering or publishing.[conversation_history:1]

## Common bugs and fixes

### 1. Old source names still appear in the public tracker
Cause: the published HTML, raw GitHub asset, or GitHub Pages site may still be serving cached content, or the uploaded file may not actually be the cleaned artifact that was meant for publication.[conversation_history:1]

Fix: verify the exact file committed to GitHub, hard-refresh the Pages site, and use cache-busted URLs during verification. Also confirm the public HTML maps source identifiers like `bryce_tracker` and `datacentertracker.org` to neutral display strings before render.[conversation_history:1]

### 2. Notion imports create partial or malformed records
Cause: CSV rows may be missing required fields or may use inconsistent categorical values across historical and newly discovered records.[file:100][file:101][file:104][file:105]

Fix: validate required columns before upload, normalize enums such as state, outcome, status, and scope, and run queue processing on a small sample first before bulk import.[file:104][file:105]

### 3. Duplicate opposition rows after merging
Cause: historical backfill, source CSV merges, and event discovery can all create near-duplicate incidents that differ only by summary text, URLs, or formatting.[file:102][file:103][file:106]

Fix: dedupe on a stable composite key such as incident name + state + date + entity, then manually review borderline matches before import.[file:102][file:103]

### 4. County/state map behavior does not match the dataset
Cause: inconsistent county naming, missing coordinates, or a mismatch between state abbreviations, full state names, and county normalization rules can break joins in the published map.[file:100][file:109][conversation_history:1]

Fix: normalize county strings, keep both state abbreviation and display name available, and verify county-level behavior with a few known incidents after each map rebuild.[file:100][file:109][conversation_history:1]

### 5. GitHub Pages or raw file changes do not show immediately
Cause: GitHub caching can delay the appearance of updated HTML even after an overwrite or commit.[conversation_history:1]

Fix: use a cache-busting query string during checks, verify the GitHub file view and latest commit, and wait several minutes before assuming the deployment is wrong.[conversation_history:1]

## Operational guidance

- Keep a backup copy of the opposition master before every merge or bulk import step.[file:100]
- Treat PDF backfill and event discovery as staging steps, not auto-publish steps; a human review pass should sit between discovery and Notion import.[file:102][file:105][file:106]
- Rebuild public HTML outputs only after data cleanup is complete, because map/UI issues are often downstream symptoms of upstream data normalization problems.[file:108][file:109][conversation_history:1]
- When debugging publication issues, separate three questions: did the data change, did the HTML change, and did the hosted URL refresh.[file:100][file:109][conversation_history:1]

## Suggested next improvements

- Add a single `requirements.txt` or `pyproject.toml` so environment setup is repeatable across all scripts.[file:102][file:104][file:105][file:106][file:113]
- Add one canonical `README` command block for opposition and one for developments, then keep every script name and expected output updated there.[file:102][file:103][file:108][file:111]
- Add a validation script that checks required columns, duplicate keys, invalid states/counties, and missing coordinates before import or map generation.[file:100][file:112][file:114]
- Add a publish checklist for the public tracker so label normalization, cache-busting checks, and county-layer behavior are verified every time.[conversation_history:1]
