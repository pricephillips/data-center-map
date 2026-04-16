#!/usr/bin/env python3
"""
geocode_ai_centers.py
---------------------
Run this whenever you get a new ai_centers CSV with new rows.
It caches previously geocoded addresses so only NEW rows hit the API.

Usage:
    python3 geocode_ai_centers.py new_ai_centers.csv
Output:
    ai_centers.csv  (ready to drop into your GitHub repo)
"""

import sys
import csv
import json
import time
import requests
from pathlib import Path

CACHE_FILE = Path(__file__).parent / 'geocode_cache.json'
OUTPUT_FILE = Path(__file__).parent / 'ai_centers.csv'

KEEP_COLS = [
    'Name', 'Owner', 'Users', 'Country', 'Address', 'lat', 'lon',
    'Current power (MW)', 'Current H100 equivalents',
    'Current total capital cost (2025 USD billions)',
    'Project', 'Energy companies'
]

def load_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

def geocode(address, cache):
    if address in cache:
        return cache[address]
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': address, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'DataCenterMapUpdater/1.0'},
            timeout=10
        )
        data = r.json()
        if data:
            result = (float(data[0]['lat']), float(data[0]['lon']))
            cache[address] = result
            save_cache(cache)
            time.sleep(1.2)
            return result
    except Exception as e:
        print(f"  ERROR geocoding '{address}': {e}")
    return (None, None)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 geocode_ai_centers.py <input_csv>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    cache = load_cache()

    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    results = []
    for row in rows:
        addr = row.get('Address', '').strip()
        if row.get('lat') and row.get('lon'):
            # Already has coordinates, keep them
            lat, lon = float(row['lat']), float(row['lon'])
            print(f"  [cached in file] {row.get('Name','?')}")
        elif addr in cache:
            lat, lon = cache[addr]
            print(f"  [cache hit]      {row.get('Name','?')}")
        else:
            print(f"  [geocoding]      {row.get('Name','?')} -> {addr}")
            lat, lon = geocode(addr, cache)

        out = {col: row.get(col, '') for col in KEEP_COLS if col not in ('lat','lon')}
        out['lat'] = lat if lat is not None else ''
        out['lon'] = lon if lon is not None else ''
        results.append(out)

    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=KEEP_COLS)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone. {len(results)} rows written to {OUTPUT_FILE}")
    missing = sum(1 for r in results if not r['lat'])
    if missing:
        print(f"WARNING: {missing} rows could not be geocoded — add coordinates manually in ai_centers.csv")

if __name__ == '__main__':
    main()
