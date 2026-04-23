# Data Center Proposal Tracker Mirror

This repo mirrors proposal data from `trackdatacenters.com` into a CSV and serves a static HTML viewer from GitHub Pages.

## Files in this repo

- `scrape.py` — scraper that fetches proposals and writes `proposals.csv`
- `proposals.csv` — generated flat-file dataset
- `index.html` — GitHub Pages frontend that reads `proposals.csv`
- `.github/workflows/scrape.yml` — daily GitHub Actions workflow

## Setup

1. Create a new GitHub repo.
2. Copy these files into the repo root.
3. Commit and push.
4. In GitHub, enable **Actions**.
5. In **Settings → Pages**, publish from your default branch root.
6. Optionally run the workflow manually once from the **Actions** tab.

## How it works

- The workflow runs daily at `06:00 UTC`.
- It executes `python scrape.py --out proposals.csv`.
- If the CSV changes, the workflow commits and pushes the updated file.
- `index.html` fetches `proposals.csv` in-browser and renders filters, KPIs, and a sortable table.

## Notes

- The scraper currently relies on the same consent-cookie flow used by the source site.
- If the source site changes its API or request requirements, `scrape.py` may need to be adjusted.
