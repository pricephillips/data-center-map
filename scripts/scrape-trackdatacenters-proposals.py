#!/usr/bin/env python3
"""
trackdatacenters.com -> proposals.csv scraper

Uses curl subprocesses instead of urllib because the source site's consent
endpoint intermittently returns HTTP 500 under urllib in GitHub Actions, while
curl handles the cookie flow reliably.

Usage:
  python scripts/scrape-trackdatacenters-proposals.py
  python scripts/scrape-trackdatacenters-proposals.py --out data/proposals.csv
"""

import argparse
import csv
import json
import subprocess
import tempfile
from pathlib import Path

BASE_URL = "https://www.trackdatacenters.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
CSV_FIELDS = [
    "id", "name", "type", "phase", "status", "state",
    "towns", "counties", "address",
    "lat", "lon", "size_acres", "capacity_mw", "scale",
    "date", "lastUpdated", "yearOpened",
    "jobsConstruction", "jobsLongTerm", "jobsTotal",
    "companies",
    "zoningAllowance", "landSold", "bringingOwnEnergy",
    "approx", "locationTbd", "moratoriumExempt",
    "info",
    "createdAt", "updatedAt",
]


def run_curl(args):
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result.stdout


def consent(cookie_jar):
    run_curl([
        'curl', '-s', '-c', cookie_jar, '-b', cookie_jar,
        '-H', f'Referer: {BASE_URL}/',
        '-H', 'Content-Type: application/json',
        '-H', 'x-app-request: 1',
        '-H', f'User-Agent: {USER_AGENT}',
        '-X', 'POST', '-d', '{"choice":"all"}',
        f'{BASE_URL}/api/cookies/consent',
    ])


def fetch_page(cookie_jar, cursor, limit=100):
    url = f'{BASE_URL}/api/data/proposals?limit={limit}&cursor={cursor}&fields=complete'
    body = run_curl([
        'curl', '-s', '-b', cookie_jar,
        '-H', f'Referer: {BASE_URL}/',
        '-H', 'x-app-request: 1',
        '-H', f'User-Agent: {USER_AGENT}',
        url,
    ])
    return json.loads(body)


def flatten(record):
    muni = record.get('municipality') or {}
    return {
        'id': record['id'],
        'name': record.get('name', ''),
        'type': record.get('type', ''),
        'phase': record.get('phase', ''),
        'status': record.get('status', ''),
        'state': record.get('state', ''),
        'towns': '; '.join(muni.get('towns') or []),
        'counties': '; '.join(muni.get('counties') or []),
        'address': record.get('address', ''),
        'lat': record.get('lat', ''),
        'lon': record.get('lon', ''),
        'size_acres': record.get('size_acres', ''),
        'capacity_mw': record.get('capacity_mw', ''),
        'scale': record.get('scale', ''),
        'date': record.get('date', ''),
        'lastUpdated': record.get('lastUpdated', ''),
        'yearOpened': record.get('yearOpened', ''),
        'jobsConstruction': record.get('jobsConstruction', ''),
        'jobsLongTerm': record.get('jobsLongTerm', ''),
        'jobsTotal': record.get('jobsTotal', ''),
        'companies': '; '.join(record.get('companies') or []),
        'zoningAllowance': record.get('zoningAllowance', ''),
        'landSold': record.get('landSold', ''),
        'bringingOwnEnergy': record.get('bringingOwnEnergy', ''),
        'approx': record.get('approx', ''),
        'locationTbd': record.get('locationTbd', ''),
        'moratoriumExempt': record.get('moratoriumExempt', ''),
        'info': (record.get('info') or '').replace('\n', ' '),
        'createdAt': record.get('createdAt', ''),
        'updatedAt': record.get('updatedAt', ''),
    }



# ---------------------------------------------------------------------------
# Manual-work preservation (defensibility overlay)
#
# The scraper is authoritative for trackdatacenters fields, but the platform
# carries manual corrections and additions the source does not know about:
#   - data/proposals_manual_overlay.csv : field-level corrections to scraped
#     rows (id, field, value) — e.g. a voided approval that must not show as
#     approved, or a sourced announced-date the source lacks.
#   - data/proposals_added.csv : projects not on trackdatacenters at all
#     (appended verbatim).
#   - the outcome_detail column : additive, preserved if present.
# Without this, every nightly scrape silently destroys manual defensibility
# work. Files are optional; absent them, behavior is the plain scrape.
# ---------------------------------------------------------------------------

OVERLAY_CSV = Path("data/proposals_manual_overlay.csv")
ADDED_CSV = Path("data/proposals_added.csv")


def apply_manual_preservation(rows, out_path):
    """Apply field overlay + append manual projects. Returns (rows, fieldnames).
    out_path is used only to resolve sibling data/ files when --out differs."""
    base_dir = out_path.parent
    overlay_path = base_dir / OVERLAY_CSV.name
    added_path = base_dir / ADDED_CSV.name

    fieldnames = list(CSV_FIELDS)

    # 1) field-level overlay corrections, keyed by id
    if overlay_path.exists():
        by_id = {r["id"]: r for r in rows}
        applied = 0
        with open(overlay_path, newline="", encoding="utf-8-sig") as fh:
            for o in csv.DictReader(fh):
                tgt = by_id.get(o["id"])
                if tgt is not None and o["field"] in tgt:
                    tgt[o["field"]] = o["value"]
                    applied += 1
        print(f"manual overlay: {applied} field correction(s) applied")

    # 2) append manual-added projects (not on the source)
    if added_path.exists():
        with open(added_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for c in reader.fieldnames or []:
                if c not in fieldnames:
                    fieldnames.append(c)   # e.g. outcome_detail
            existing_ids = {r["id"] for r in rows}
            added = [r for r in reader if r["id"] not in existing_ids]
        rows.extend(added)
        print(f"manual additions: {len(added)} project(s) appended")

    # 3) ensure every row has every field (outcome_detail etc.)
    for r in rows:
        for c in fieldnames:
            r.setdefault(c, "")
    return rows, fieldnames


def scrape(out_path: Path):
    all_records = []
    cursor = 0
    total = None
    with tempfile.NamedTemporaryFile() as tmp:
        cookie_jar = tmp.name
        consent(cookie_jar)
        while True:
            page = fetch_page(cookie_jar, cursor)
            if total is None:
                total = page['total']
            all_records.extend(page['data'])
            if len(all_records) >= total:
                break
            cursor = page['nextCursor']
    rows = [flatten(r) for r in all_records]
    rows, fieldnames = apply_manual_preservation(rows, out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description='Scrape trackdatacenters.com -> CSV')
    parser.add_argument('--out', default='data/proposals.csv', help='Output CSV path')
    args = parser.parse_args()
    scrape(Path(args.out))


if __name__ == '__main__':
    main()
