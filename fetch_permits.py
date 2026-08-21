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
import re
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


def fetch_tabular(cfg):
    """Generic tabular-file adapter (added 2026-08-21): CSV or XLSX served at
    a plain URL, for agencies that publish file drops rather than APIs (state
    spreadsheets, RTO interconnection-queue reports). Keeps every column;
    optional row filter is a case-insensitive substring match against the
    concatenated row, mirroring the WHERE-style narrowing of the other
    adapters without pretending to be SQL.

    Config keys: url (required), format ("csv" default, or "xlsx"),
    sheet (xlsx only; name or 0-based index, default 0),
    contains (optional list of substrings; a row is kept when ANY matches),
    date_fields (epoch-ms or Excel-serial values converted to ISO dates).
    """
    fmt = (cfg.get("format") or "csv").lower()
    contains = [normalize_needle(s) for s in (cfg.get("contains") or [])]
    date_fields = set(cfg.get("date_fields", []))

    if fmt == "csv":
        req = urllib.request.Request(
            cfg["url"], headers={"User-Agent": "hawthorn-baseline/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8-sig", "replace")
        rows = list(csv.DictReader(text.splitlines()))
    elif fmt == "xlsx":
        try:
            import pandas as pd  # available in the Actions environment
        except ImportError as e:
            raise RuntimeError("xlsx format needs pandas in the runner") from e
        sheet = cfg.get("sheet", 0)
        df = pd.read_excel(cfg["url"], sheet_name=sheet, dtype=str)
        df = df.fillna("")
        rows = df.to_dict(orient="records")
    else:
        raise RuntimeError(f"tabular format {fmt!r} not supported")

    out = []
    for r in rows:
        if contains:
            hay = normalize_needle(" ".join(str(v) for v in r.values()))
            if not any(n in hay for n in contains):
                continue
        for df_field in date_fields:
            if df_field in r:
                r[df_field] = coerce_date(r[df_field])
        out.append(r)
    return out


def normalize_needle(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def coerce_date(v):
    """Epoch-ms, Excel serial, or already-a-date -> ISO where possible."""
    s = str(v or "").strip()
    if not s:
        return s
    if re.match(r"^\d{4}-\d{2}(-\d{2})?$", s):
        return s
    try:
        f = float(s)
    except ValueError:
        return s
    if f > 10**11:                      # epoch milliseconds
        return epoch_ms_to_iso(f)
    if 20000 < f < 80000:               # Excel serial (1954..2118)
        from datetime import date, timedelta
        return (date(1899, 12, 30) + timedelta(days=int(f))).isoformat()
    return s


ADAPTERS["tabular"] = fetch_tabular


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

    # Self-completing sources (added 2026-08-21): a config may register a
    # tracker whose service URL is not yet known, carrying "url": null plus
    # a "discovery" block for discover_arcgis_layer.py. When the url is
    # null, look for that tool's resolved output and use its query_url.
    # If discovery has not resolved yet, skip cleanly: an unresolved
    # source is a pending registration, not a fetch failure, and must not
    # break the scheduled workflow for sources that do work.
    if not cfg.get("url"):
        disc_path = os.path.join(
            args.outdir, f"arcgis_discovery_{cfg.get('source')}.json")
        resolved = None
        if os.path.exists(disc_path):
            try:
                with open(disc_path, encoding="utf-8") as fh:
                    resolved = (json.load(fh) or {}).get("resolved")
            except Exception:
                resolved = None
        if resolved and resolved.get("query_url"):
            cfg["url"] = resolved["query_url"]
            print(f"{cfg.get('source')}: url resolved by discovery -> "
                  f"{cfg['url']}")
        else:
            print(f"{cfg.get('source')}: no service url yet (discovery "
                  f"unresolved); skipping fetch. Run "
                  f"discover_arcgis_layer.py --config {args.config} in CI, "
                  f"or pin the url into the config by hand.")
            return 0

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
