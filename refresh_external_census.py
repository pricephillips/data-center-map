
#!/usr/bin/env python3
"""refresh_external_census.py

Refreshes the external restriction census against the current
Moratorium Nation dataset and writes a delta file for
coverage_audit.py and restriction_worklist.py to consume.

It never overwrites data/external_restriction_census.csv; the seeded
census remains the source of record, and this module only surfaces new
or changed rows for review and ingest.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))

EXTERNAL_CSV = os.path.join(HERE, "data", "external_restriction_census.csv")
DELTA_CSV = os.path.join(HERE, "data", "external_restriction_census_delta.csv")
REPORT_MD = os.path.join(HERE, "data", "external_restriction_census_refresh_report.md")
FIPS_LOOKUP_JSON = os.path.join(HERE, "data", "county_fips_lookup.json")

# NOTE: adjust this URL if Moratorium Nation changes its repo or path.
# Path corrected 2026-08-21: the inventory lives under data/ in the
# upstream repo; the bare-root path 404s.
UPSTREAM_URL = (
    "https://raw.githubusercontent.com/mjbommar/moratorium-data-2026/"
    "main/data/moratorium_inventory.csv"
)


def load_local_census() -> list[dict]:
    if not os.path.exists(EXTERNAL_CSV):
        raise FileNotFoundError(EXTERNAL_CSV)
    with open(EXTERNAL_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_moratorium_csv() -> list[dict]:
    resp = urllib.request.urlopen(UPSTREAM_URL)
    text = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def normalize_upstream_rows(upstream_rows: list[dict]) -> list[dict]:
    """Map Moratorium Nation's schema into the tracker census schema.

    Target schema:
      state, county, instrument, census_status, date_enacted, source

    This function will need to be updated once the upstream headers are
    confirmed. For now, we assume Moratorium Nation exposes state and
    a county-like jurisdiction name plus a status and date.
    """
    normalized: list[dict] = []

    for row in upstream_rows:
        # TODO: adjust these field names to match the actual Moratorium Nation CSV.
        state = row.get("state") or row.get("state_abbrev")
        county = row.get("county") or row.get("jurisdiction")
        status = row.get("status") or row.get("census_status")
        date = row.get("date_enacted") or row.get("date") or ""

        if not state or not county:
            continue  # skip rows that cannot be keyed cleanly

        source_id = row.get("moratorium_id") or ""
        if source_id:
            source = f"moratorium-nation:{source_id} (CC-BY-4.0, github.com/mjbommar/moratorium-data-2026)"
        else:
            source = "moratorium-nation:unknown-id (CC-BY-4.0, github.com/mjbommar/moratorium-data-2026)"

        normalized.append(
            {
                "state": state,
                "county": county,
                "instrument": "moratorium",
                "census_status": status,
                "date_enacted": date,
                "source": source,
            }
        )

    return normalized


def make_row_key(row: dict) -> tuple:
    """Key rows at the county/instrument/date level to avoid duplicating
    episodes that are already present in the seeded census.
    """
    return (
        (row.get("state") or "").strip(),
        (row.get("county") or "").strip(),
        (row.get("instrument") or "").strip(),
        (row.get("date_enacted") or "").strip(),
    )


def compute_delta(local_rows: list[dict], upstream_rows: list[dict]) -> list[dict]:
    local_keys = {make_row_key(r) for r in local_rows}
    delta: list[dict] = []

    for row in upstream_rows:
        key = make_row_key(row)
        if key in local_keys:
            continue
        delta.append(row)

    return delta


def write_delta_csv(delta_rows: list[dict]) -> None:
    if not delta_rows:
        # If there is no delta, remove any stale file so CI can see a clean no-op.
        if os.path.exists(DELTA_CSV):
            os.remove(DELTA_CSV)
        return

    fieldnames = [
        "state",
        "county",
        "instrument",
        "census_status",
        "date_enacted",
        "source",
    ]
    with open(DELTA_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in delta_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_refresh_report(local_rows: list[dict], upstream_rows: list[dict], delta_rows: list[dict]) -> None:
    counts = Counter(r.get("census_status") or "" for r in upstream_rows)
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        # String literals repaired 2026-08-21: raw newlines had been
        # written inside these strings, a double-escaping casualty that made
        # the whole module a SyntaxError, so the refresh workflow failed at
        # parse time on every scheduled run.
        f.write("# External restriction census refresh\n\n")
        f.write(f"- Local seeded census rows: {len(local_rows)}\n")
        f.write(f"- Upstream rows (normalized): {len(upstream_rows)}\n")
        f.write(f"- Delta rows (new relative to local census): "
                f"{len(delta_rows)}\n\n")
        if delta_rows:
            f.write("## Status distribution in upstream\n\n")
            for status, n in sorted(counts.items()):
                if not status:
                    continue
                f.write(f"- {status}: {n}\n")


def run_selftest() -> None:
    # Minimal invariants: local census exists, upstream CSV is readable,
    # normalization produces at least one row, and delta computation does not crash.
    local = load_local_census()
    upstream_raw = fetch_moratorium_csv()
    upstream_norm = normalize_upstream_rows(upstream_raw)
    _ = compute_delta(local, upstream_norm)
    if not upstream_norm:
        raise SystemExit("Selftest: upstream normalization produced no rows")
    print("Selftest passed.")


def run_refresh() -> None:
    local = load_local_census()
    upstream_raw = fetch_moratorium_csv()
    upstream_norm = normalize_upstream_rows(upstream_raw)
    delta = compute_delta(local, upstream_norm)
    write_delta_csv(delta)
    write_refresh_report(local, upstream_norm, delta)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="Run invariants only")
    parser.add_argument("--refresh", action="store_true", help="Fetch upstream census and write delta")
    args = parser.parse_args()

    if args.selftest:
        run_selftest()
        return
    if args.refresh:
        run_refresh()
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
