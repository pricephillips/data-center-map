"""
fetch_permits.py — pull data-center permit records from public open-data
portals (runs in GitHub Actions, where network access is unrestricted).

Writes CANDIDATE files for human review — never directly into
data/baseline_dated_external.csv. The review step is deliberate: a fetched
record enters the dated baseline only after Price confirms the mapping and
runs permit_ingest.py with a config.

Adapters:
  arcgis   — ArcGIS REST MapServer/FeatureServer layer query (JSON)
  socrata  — Socrata SODA resource endpoint (JSON)

Per-source JSON config:
  {
    "adapter": "arcgis",
    "source": "loudoun_lola",
    "url": "https://logis.loudoun.gov/gis/rest/services/Projects/LOLA_DATA/MapServer/0/query",
    "where": "UPPER(PlanName) LIKE '%DATA%' OR UPPER(PlanDescription) LIKE '%DATA CENTER%'",
    "out_fields": ["PlanNumber","PlanName","PlanApplicationDate","PlanType",
                   "PlanStatus","PlanDescription"],
    "date_fields": ["PlanApplicationDate"],       # epoch-ms -> ISO conversion
    "page_size": 1000
  }
  {
    "adapter": "socrata",
    "source": "example_county_permits",
    "url": "https://data.example.gov/resource/xxxx-yyyy.json",
    "where": "upper(description) like '%DATA CENTER%'",
    "page_size": 1000
  }

Output: data/permit_candidates_<source>.csv (raw portal columns, dates
normalized). Next step is manual: inspect, write a permit_ingest column map,
run permit_ingest.py.

No scorekeeping vocabulary is introduced; records are raw portal data.

Usage:
  python3 fetch_permits.py --config configs/loudoun_lola.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "hawthorn-baseline/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def epoch_ms_to_iso(v):
    try:
        return datetime.fromtimestamp(float(v) / 1000.0, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return v


def fetch_arcgis(cfg):
    rows, offset = [], 0
    page = int(cfg.get("page_size", 1000))
    date_fields = set(cfg.get("date_fields", []))
    while True:
        params = {
            "where": cfg.get("where", "1=1"),
            "outFields": ",".join(cfg.get("out_fields", ["*"])),
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page,
        }
        url = cfg["url"] + "?" + urllib.parse.urlencode(params)
        data = http_json(url)
        if "error" in data:
            raise RuntimeError(f"arcgis error: {data['error']}")
        feats = data.get("features", [])
        for f in feats:
            attrs = f.get("attributes", {})
            for df in date_fields:
                if df in attrs:
                    attrs[df] = epoch_ms_to_iso(attrs[df])
            rows.append(attrs)
        if len(feats) < page:
            break
        offset += page
    return rows


def fetch_socrata(cfg):
    rows, offset = [], 0
    page = int(cfg.get("page_size", 1000))
    while True:
        params = {"$limit": page, "$offset": offset}
        if cfg.get("where"):
            params["$where"] = cfg["where"]
        url = cfg["url"] + "?" + urllib.parse.urlencode(params)
        data = http_json(url)
        if not isinstance(data, list):
            raise RuntimeError(f"socrata unexpected response: {str(data)[:200]}")
        rows.extend(data)
        if len(data) < page:
            break
        offset += page
    return rows


ADAPTERS = {"arcgis": fetch_arcgis, "socrata": fetch_socrata}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--outdir", default=os.path.join(ROOT, "data"))
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    adapter = ADAPTERS.get(cfg.get("adapter"))
    if not adapter:
        print(f"ERROR: unknown adapter {cfg.get('adapter')!r}")
        return 1

    try:
        rows = adapter(cfg)
    except Exception as e:
        print(f"FETCH FAILED ({cfg.get('source')}): {e}")
        return 1

    out = os.path.join(args.outdir, f"permit_candidates_{cfg['source']}.csv")
    if not rows:
        print(f"{cfg['source']}: 0 records matched — nothing written")
        return 0
    cols = sorted({k for r in rows for k in r.keys()})
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"{cfg['source']}: {len(rows)} candidate records -> "
          f"{os.path.relpath(out, ROOT)}")
    print("Review the file, write a permit_ingest column map, then run "
          "permit_ingest.py to fold accepted rows into the dated baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
