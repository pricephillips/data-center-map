
#!/usr/bin/env python3
"""refresh_external_census.py

Refreshes the external restriction census against the current
Moratorium Nation dataset and writes a delta file for review.

It never overwrites data/external_restriction_census.csv; the seeded
census remains the source of record, and this module only surfaces new
or changed rows for review and ingest.

Correction, 2026-09-09: this docstring previously said the delta was
written "for coverage_audit.py and restriction_worklist.py to consume".
Neither module references it -- both read the seeded census directly --
so the delta has no automated consumer and is a worklist for a person,
in the same sense as data/permit_candidates_*.csv. That is the intended
shape, not a gap to close by wiring it into an audit: promoting an
upstream row into the census is a review decision.

The workflow that runs this had never succeeded before that date. It
invoked a --merge flag this module has never accepted, and its commit
step staged the census (read-only here) rather than the delta.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
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


COUNTY_JURISDICTION_TYPES = {"county", "parish"}
DATA_CENTER_SECTOR = "data_center"


def _norm_join(name: str) -> str:
    """Normalization used ONLY for delta comparison, never for output.

    Both sides spell a county differently ("Carroll County" upstream,
    "Carroll County" locally, but "Athens-Clarke County" against "Clarke
    County"), so comparing raw strings reported every upstream row as new.
    """
    s = (name or "").split(",")[0].strip().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(county|parish|borough|municipio)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def normalize_upstream_rows(upstream_rows: list[dict]) -> list[dict]:
    """Map Moratorium Nation's schema into the tracker census schema.

    Target schema:
      state, county, instrument, census_status, date_enacted, source

    Repaired 2026-09-15. This function shipped as a placeholder and still
    carried its own "TODO: adjust these field names to match the actual
    Moratorium Nation CSV" marker. Every one of its field reads was wrong
    against the real upstream header, and each error pushed in the same
    direction: the delta was unusable, which is why the module's docstring
    records that the delta "has no automated consumer". The delta was not
    lacking a consumer because promoting a census row is a review decision.
    It was lacking one because nothing could have consumed it.

      state          read row["state"], which upstream spells in full
                     ("Alabama"), while the seeded census and every joiner in
                     the repo use the abbreviation. state_abbrev was in the
                     fallback position and therefore never reached.
      jurisdiction   taken with no jurisdiction_type filter, so 173 City, 71
                     Township, 47 Town and 16 Village rows entered a census
                     the coverage audit reads as county-level. Those rows
                     cannot join the county frame and would have been counted
                     as unjoined census defects.
      status         read row["status"], a column upstream does not have. The
                     fallback row["census_status"] does not exist either, so
                     census_status came out empty on all 533 rows, and an
                     empty status cannot be told apart from a pending one.
      date_enacted   read the free-text column ("On or about 2026-06-25, per
                     WBRC reporting of the city council vote") rather than
                     date_enacted_iso, which upstream publishes and populates
                     on 199 of 206 county rows.
      sectors        no filter, so solar, wind and crypto-only moratoria
                     entered a data-center census.

    Net effect: 533 rows of the wrong shape, against 206 genuinely relevant
    ones. The seeded census holds 94 rows across 17 states while upstream
    carries 206 data-center county rows across 32 states, so the repair makes
    roughly 112 county-level restriction records in 15 additional states
    reviewable for the first time.

    instrument stays "moratorium". legal_basis is free-text prose upstream
    ("Board of Supervisors resolution/ordinance (exact instrument number not
    confirmed in this pass)") and cannot be mapped to the census vocabulary
    without inventing a classification, and 91 of the 94 seeded rows already
    carry "moratorium".

    This function still writes ONLY the delta. The seeded census remains the
    source of record and promoting a row into it remains a review decision.
    """
    normalized: list[dict] = []

    for row in upstream_rows:
        jtype = (row.get("jurisdiction_type") or "").strip().lower()
        if jtype not in COUNTY_JURISDICTION_TYPES:
            continue

        sectors = (row.get("sectors") or "").lower()
        if DATA_CENTER_SECTOR not in sectors:
            continue

        state = (row.get("state_abbrev") or "").strip().upper()
        county = (row.get("jurisdiction") or "").strip()
        if not state or not county:
            continue  # skip rows that cannot be keyed cleanly

        # enacted_status carries the census vocabulary (active, extended,
        # replaced, expired, rescinded, pending). current_status is prose.
        status = (row.get("enacted_status") or "").strip().lower()

        # ISO only. A free-text date would be carried into the census as if
        # it were a date and would fail every downstream parse.
        date = (row.get("date_enacted_iso") or "").strip()

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
    """Key rows at the county level for delta comparison.

    Was keyed on (state, county, instrument, date_enacted) with raw strings.
    Because the two sides disagreed on state spelling and date format, no
    upstream row ever matched a local one and all 533 were reported as new.
    The date is also the wrong key component: an upstream row whose date was
    later corrected would read as a new county rather than as a changed
    episode. Recall is a property of counties, the same unit coverage_audit.py
    settled on, so the key is the county.
    """
    return (
        (row.get("state") or "").strip().upper(),
        _norm_join(row.get("county") or ""),
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


def write_refresh_report(local_rows: list[dict], upstream_rows: list[dict],
                         delta_rows: list[dict]) -> None:
    counts = Counter(r.get("census_status") or "(blank)" for r in upstream_rows)
    local_states = {(r.get("state") or "").strip().upper() for r in local_rows}
    local_states.discard("")
    up_states = {(r.get("state") or "").strip().upper() for r in upstream_rows}
    up_states.discard("")
    new_states = sorted(up_states - local_states)

    with open(REPORT_MD, "w", encoding="utf-8", newline="\n") as f:
        # String literals repaired 2026-08-21: raw newlines had been
        # written inside these strings, a double-escaping casualty that made
        # the whole module a SyntaxError, so the refresh workflow failed at
        # parse time on every scheduled run.
        f.write("# External restriction census refresh\n\n")
        f.write("Upstream rows are filtered to county-level, data-center-sector "
                "rows before anything below is counted. The delta is keyed on "
                "the county, not the episode.\n\n")
        f.write(f"- Local seeded census rows: {len(local_rows)} "
                f"across {len(local_states)} states\n")
        f.write(f"- Upstream rows (county-level, data-center sector): "
                f"{len(upstream_rows)} across {len(up_states)} states\n")
        f.write(f"- Delta rows (counties absent from the local census): "
                f"{len(delta_rows)}\n\n")
        if new_states:
            f.write(f"## States upstream covers and the local census does not "
                    f"({len(new_states)})\n\n")
            f.write(", ".join(new_states) + "\n\n")
        if delta_rows:
            f.write("## Status distribution in upstream\n\n")
            for status, n in sorted(counts.items()):
                f.write(f"- {status}: {n}\n")
            f.write("\n")
        f.write("Promoting a delta row into "
                "data/external_restriction_census.csv remains a review "
                "decision. This module never writes the census.\n")


def run_selftest() -> None:
    """Offline invariants.

    Rewritten 2026-09-15. This made a live network call to the upstream repo,
    so it could not join the blocking self-test gate in pipeline.yml, and it
    reported an upstream outage as a code failure. The fixtures below carry the
    exact upstream header spellings the normalizer reads, which is precisely
    what the placeholder version got wrong.
    """
    fixture = [
        # County, data-center sector: kept.
        {"state": "Indiana", "state_abbrev": "IN", "jurisdiction": "DeKalb County",
         "jurisdiction_type": "County", "sectors": '["data_center"]',
         "enacted_status": "active", "date_enacted_iso": "2026-04-13",
         "date_enacted": "On or about 2026-04-13, per local reporting",
         "moratorium_id": "in-dekalb-county-2026"},
        # City: dropped, the census is county-level.
        {"state": "Alabama", "state_abbrev": "AL", "jurisdiction": "Birmingham",
         "jurisdiction_type": "City", "sectors": '["data_center"]',
         "enacted_status": "active", "date_enacted_iso": "2026-03-03",
         "moratorium_id": "al-birmingham-2026"},
        # Township: dropped.
        {"state": "Michigan", "state_abbrev": "MI", "jurisdiction": "Scio Township",
         "jurisdiction_type": "Township", "sectors": '["data_center"]',
         "enacted_status": "active", "date_enacted_iso": "2026-02-02",
         "moratorium_id": "mi-scio-2026"},
        # County but not a data-center instrument: dropped.
        {"state": "Kansas", "state_abbrev": "KS", "jurisdiction": "Reno County",
         "jurisdiction_type": "County", "sectors": '["solar"]',
         "enacted_status": "active", "date_enacted_iso": "2026-01-01",
         "moratorium_id": "ks-reno-2026"},
        # Parish counts as county-level.
        {"state": "Louisiana", "state_abbrev": "LA", "jurisdiction": "Caddo Parish",
         "jurisdiction_type": "Parish", "sectors": '["data_center","solar"]',
         "enacted_status": "pending", "date_enacted_iso": "",
         "moratorium_id": "la-caddo-2026"},
    ]
    norm = normalize_upstream_rows(fixture)
    failures = []

    def ck(name, got, want):
        if got != want:
            failures.append(f"{name}: expected {want!r}, got {got!r}")

    ck("county and sector filters", len(norm), 2)
    ck("state is abbreviated", norm[0]["state"], "IN")
    ck("status is mapped", norm[0]["census_status"], "active")
    ck("date is ISO, not prose", norm[0]["date_enacted"], "2026-04-13")
    ck("parish is county-level", norm[1]["state"], "LA")
    ck("pending status survives", norm[1]["census_status"], "pending")
    ck("blank iso date stays blank", norm[1]["date_enacted"], "")
    ck("source cites the upstream id",
       norm[0]["source"].startswith("moratorium-nation:in-dekalb-county-2026"), True)

    # A county the local census already holds must not read as new.
    local = [{"state": "IN", "county": "DeKalb County", "instrument": "moratorium",
              "census_status": "active", "date_enacted": "2026-04-13"}]
    ck("known county is not delta", len(compute_delta(local, norm)), 1)

    # The key normalizes, so a suffix difference is not a new county.
    local_suffix = [{"state": "LA", "county": "Caddo Parish (Phase 1)"}]
    ck("normalized key matches across suffixes",
       len(compute_delta(local_suffix, norm)), 1)

    # A corrected date is a changed episode, not a newly covered county.
    local_date = [{"state": "IN", "county": "DeKalb County",
                   "date_enacted": "2026-04-14"},
                  {"state": "LA", "county": "Caddo Parish"}]
    ck("date change is not a new county",
       len(compute_delta(local_date, norm)), 0)

    # State spelling must not resurrect the original defect.
    local_fullname = [{"state": "Indiana", "county": "DeKalb County"}]
    ck("full state name does not match an abbreviated key",
       len(compute_delta(local_fullname, norm)), 2)

    if failures:
        for f in failures:
            print("  " + f)
        raise SystemExit("Selftest FAILED")
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
