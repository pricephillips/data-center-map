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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description='Scrape trackdatacenters.com -> CSV')
    parser.add_argument('--out', default='data/proposals.csv', help='Output CSV path')
    args = parser.parse_args()
    scrape(Path(args.out))


if __name__ == '__main__':
    main()
