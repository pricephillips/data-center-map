#!/usr/bin/env python3
"""
trackdatacenters.com → proposals.csv scraper
Designed to run as a GitHub Actions cron job.

Usage:
  python scripts/scrape-trackdatacenters-proposals.py
  python scripts/scrape-trackdatacenters-proposals.py --out data/proposals.csv
"""

import urllib.request
import urllib.error
import http.cookiejar
import json
import csv
import argparse
import sys
from pathlib import Path

BASE_URL = "https://www.trackdatacenters.com"
CONSENT_URL = f"{BASE_URL}/api/cookies/consent"
DATA_URL = f"{BASE_URL}/api/data/proposals"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
    "x-app-request": "1",
}

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


def make_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def set_consent(opener):
    payload = json.dumps({"choice": "all"}).encode()
    req = urllib.request.Request(
        CONSENT_URL,
        data=payload,
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    resp = opener.open(req, timeout=30)
    result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"Consent endpoint returned: {result}")


def fetch_page(opener, cursor, limit=100):
    url = f"{DATA_URL}?limit={limit}&cursor={cursor}&fields=complete"
    req = urllib.request.Request(url, headers=HEADERS)
    resp = opener.open(req, timeout=30)
    return json.loads(resp.read())


def flatten(record):
    muni = record.get("municipality") or {}
    return {
        "id": record["id"],
        "name": record.get("name", ""),
        "type": record.get("type", ""),
        "phase": record.get("phase", ""),
        "status": record.get("status", ""),
        "state": record.get("state", ""),
        "towns": "; ".join(muni.get("towns") or []),
        "counties": "; ".join(muni.get("counties") or []),
        "address": record.get("address", ""),
        "lat": record.get("lat", ""),
        "lon": record.get("lon", ""),
        "size_acres": record.get("size_acres", ""),
        "capacity_mw": record.get("capacity_mw", ""),
        "scale": record.get("scale", ""),
        "date": record.get("date", ""),
        "lastUpdated": record.get("lastUpdated", ""),
        "yearOpened": record.get("yearOpened", ""),
        "jobsConstruction": record.get("jobsConstruction", ""),
        "jobsLongTerm": record.get("jobsLongTerm", ""),
        "jobsTotal": record.get("jobsTotal", ""),
        "companies": "; ".join(record.get("companies") or []),
        "zoningAllowance": record.get("zoningAllowance", ""),
        "landSold": record.get("landSold", ""),
        "bringingOwnEnergy": record.get("bringingOwnEnergy", ""),
        "approx": record.get("approx", ""),
        "locationTbd": record.get("locationTbd", ""),
        "moratoriumExempt": record.get("moratoriumExempt", ""),
        "info": (record.get("info") or "").replace("\n", " "),
        "createdAt": record.get("createdAt", ""),
        "updatedAt": record.get("updatedAt", ""),
    }


def scrape(out_path: Path):
    opener = make_opener()
    set_consent(opener)

    all_records = []
    cursor = 0
    total = None

    while True:
        page = fetch_page(opener, cursor)
        if total is None:
            total = page["total"]
        all_records.extend(page["data"])
        print(f"  fetched {len(all_records)} / {total}", file=sys.stderr)
        if len(all_records) >= total:
            break
        cursor = page["nextCursor"]

    rows = [flatten(r) for r in all_records]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows → {out_path}", file=sys.stderr)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Scrape trackdatacenters.com → CSV")
    parser.add_argument("--out", default="data/proposals.csv", help="Output CSV path")
    args = parser.parse_args()
    scrape(Path(args.out))


if __name__ == "__main__":
    main()
