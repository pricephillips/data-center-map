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
import difflib
import json
import os
import subprocess
import tempfile
from datetime import date
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
# Field audit: what the source actually sent
#
# flatten() reads every field with record.get(name, ''), so the scraper cannot
# tell a field the source renamed from a field the source left blank. The
# population guard below catches the consequence -- a column going empty -- but
# not the cause, and the cause is the part a person has to go and look up.
#
# It does not have to be looked up. A renamed field is still in the response,
# under its new name, in a key nothing reads. Comparing the keys the API sent
# against the keys flatten() asked for names both halves of the rename, and
# difflib pairs them. The answer arrives with the next scheduled run instead of
# requiring someone with a browser.
#
# The read-key set is probed from flatten() rather than listed here, so adding
# a field to the mapping cannot leave this audit describing the old one.
# ---------------------------------------------------------------------------


class _KeyProbe(dict):
    """A stand-in record that remembers which top-level keys were asked for."""

    def __init__(self, seen):
        super().__init__()
        self._seen = seen

    def get(self, key, default=None):
        self._seen.add(key)
        return default

    def __getitem__(self, key):
        self._seen.add(key)
        return ""


def api_keys_read():
    """Top-level response keys flatten() consults, probed from flatten itself."""
    seen = set()
    flatten(_KeyProbe(seen))
    return seen


def field_audit(records, keys_read=None):
    """Compare the keys the source sent against the keys the mapping wants.

    Returns a dict with:
      absent    keys flatten() reads that no record carries -- a field the
                source dropped or renamed away
      unmapped  keys the source sent that flatten() never reads -- where a
                renamed field will be sitting
      renames   a suggested pairing of the two by name similarity, which is a
                prompt for a human, never applied automatically
    """
    keys_read = api_keys_read() if keys_read is None else set(keys_read)
    observed = set()
    for r in records:
        observed |= set(r.keys())
    absent = sorted(keys_read - observed)
    unmapped = sorted(observed - keys_read)
    renames = {}
    for gone in absent:
        near = difflib.get_close_matches(gone, unmapped, n=3, cutoff=0.6)
        if near:
            renames[gone] = near
    return {"observed": sorted(observed), "absent": absent,
            "unmapped": unmapped, "renames": renames,
            "n_records": len(records)}


def write_field_audit(audit, out_dir="data"):
    lines = ["# Scraper field audit", "",
             f"Generated {date.today().isoformat()} by "
             "`scripts/scrape-trackdatacenters-proposals.py`.", "",
             f"{audit['n_records']} records; "
             f"{len(audit['observed'])} distinct top-level keys in the response.",
             ""]
    if not audit["absent"] and not audit["unmapped"]:
        lines.append("Every key the source sent is mapped, and every key the "
                     "mapping reads was present. Nothing to do.")
    if audit["absent"]:
        lines += ["## Read by the mapping, absent from the response", "",
                  "These are the fields that will arrive empty. A field here "
                  "was dropped or renamed by the source; it is not a data gap.",
                  ""]
        lines += [f"- `{k}`" for k in audit["absent"]] + [""]
    if audit["unmapped"]:
        lines += ["## Sent by the response, read by nothing", "",
                  "Candidate landing places for anything in the list above.", ""]
        lines += [f"- `{k}`" for k in audit["unmapped"]] + [""]
    if audit["renames"]:
        lines += ["## Suggested pairings", "",
                  "By name similarity only. A prompt to go and check the "
                  "response, never a mapping to apply unread: two fields can "
                  "have similar names and different meanings.", "",
                  "| absent | candidates |", "|---|---|"]
        lines += [f"| `{k}` | {', '.join('`' + c + '`' for c in v)} |"
                  for k, v in sorted(audit["renames"].items())]
        lines.append("")
    path = os.path.join(out_dir, "scraper_field_audit.md")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# Field-population guard
#
# Every field above is mapped with record.get(name, ''), so a field the source
# renames or drops is indistinguishable from a field it left blank: the scrape
# writes empty strings and reports success.
#
# That happened on 2026-09-10. Six fields emptied in one run -- date 316 -> 10,
# lastUpdated 331 -> 12, bringingOwnEnergy and moratoriumExempt 326 -> 0,
# size_acres 224 -> 7, capacity_mw 123 -> 3 -- while id, name, state, lat, lon,
# companies and phase held at their prior counts. Downstream, announced_date in
# data/project_lifecycles.csv fell 302 -> 10, and that column is the landmark
# model's anchor, so the gate could no longer open at any window. The run was
# recorded as a success. So was the next one, which left all six exactly where
# they were while adding a project.
#
# The rule here is the one assert_unique_project_ids already applies to ids:
# stop rather than publish. A field that was populated on the previous run and
# has collapsed on this one fails the scrape, so the emptied column is never
# committed and the next rename cannot land the same way.
#
# Only scraped rows are compared. Manual additions come from
# data/proposals_added.csv with their own values and never pass through the
# API mapping, so including them would dilute exactly the signal being watched
# -- it is why the six emptied fields still showed ten non-empty rows.
# ---------------------------------------------------------------------------

FIELD_LOSS_MIN_PRIOR = 20   # fields below this were always sparse; ignore them
FIELD_LOSS_RATIO = 0.5      # flag when more than half the population is gone


def field_population(rows, fields):
    """Non-empty count per field. Values are stripped; '' and None are empty."""
    return {
        f: sum(1 for r in rows if str(r.get(f) or "").strip())
        for f in fields
    }


def population_violations(prev_rows, new_rows, fields,
                          min_prior=FIELD_LOSS_MIN_PRIOR,
                          ratio=FIELD_LOSS_RATIO):
    """Fields that were populated before and have collapsed now.

    Returns a list of (field, prev_count, new_count), empty when nothing
    collapsed. A field that was already sparse is never reported, because a
    source that carries a field for fifteen rows tells us nothing when it
    carries it for seven.
    """
    prev = field_population(prev_rows, fields)
    new = field_population(new_rows, fields)
    hits = []
    for f in fields:
        if prev[f] >= min_prior and new[f] <= prev[f] * (1.0 - ratio):
            hits.append((f, prev[f], new[f]))
    return hits


def previous_scraped_rows(out_path):
    """The last run's scraped rows, or None when there is no previous run.

    Manual additions are filtered out by id so the comparison sees only what
    the API mapping produced.
    """
    if not out_path.exists():
        return None
    added_ids = set()
    added_path = out_path.parent / ADDED_CSV.name
    if added_path.exists():
        with open(added_path, newline="", encoding="utf-8-sig") as fh:
            added_ids = {str(r["id"]).strip() for r in csv.DictReader(fh)}
    with open(out_path, newline="", encoding="utf-8-sig") as fh:
        return [r for r in csv.DictReader(fh)
                if str(r.get("id") or "").strip() not in added_ids]


def assert_field_population(rows, out_path, allow_field_loss=False):
    """Stop the scrape when a populated field has collapsed to near-empty.

    A collapse is a source change, not a data change, and the only safe
    response is to leave the previous file in place until the mapping is
    repaired. Pass allow_field_loss once the drop has been confirmed as a
    real upstream removal rather than a rename.
    """
    prev_rows = previous_scraped_rows(out_path)
    if prev_rows is None:
        print("field-population guard: no previous file, nothing to compare")
        return
    hits = population_violations(prev_rows, rows, CSV_FIELDS)
    if not hits:
        print(f"field-population guard: clean ({len(CSV_FIELDS)} fields checked)")
        return
    detail = "\n".join(
        f"  {f}: {prev} -> {new} non-empty rows" for f, prev, new in hits)
    if allow_field_loss:
        print("field-population guard: OVERRIDDEN by --allow-field-loss\n" + detail)
        return
    raise SystemExit(
        "scrape-trackdatacenters-proposals: field population collapsed.\n"
        + detail
        + f"\n\nEach field above was populated on at least {FIELD_LOSS_MIN_PRIOR} "
          "rows in the previous run and has lost more than half of that now. "
          "The usual cause is the source renaming or dropping the field, which "
          "this scraper cannot see: every field is read with record.get(name, "
          "''), so a missing key writes an empty string.\n"
          "Check the current field names in one API response and update "
          "flatten(). If the removal is real and permanent, re-run with "
          "--allow-field-loss to accept it deliberately.\n"
          f"{out_path} was NOT written; the previous run's data is intact."
    )


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
            # Compare ids as text on both sides. The scraped rows carry
            # whatever type the API returned, which for this source is an int,
            # while csv.DictReader always yields str -- so `321 not in {"321"}`
            # was true and this guard could never actually skip a colliding
            # manual row. It silently appended one instead, producing a
            # duplicate id in the output and, downstream, two projects merged
            # under one project_id.
            existing_ids = {str(r["id"]).strip() for r in rows}
            added = [r for r in reader if str(r["id"]).strip() not in existing_ids]
        rows.extend(added)
        print(f"manual additions: {len(added)} project(s) appended")

    # 3) ensure every row has every field (outcome_detail etc.)
    for r in rows:
        for c in fieldnames:
            r.setdefault(c, "")
    return rows, fieldnames


def scrape(out_path: Path, allow_field_loss=False):
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
    # Before the guard, because the guard may stop the run and this is the
    # diagnostic that says why: the guard reports which columns emptied, this
    # reports which keys the source actually sent.
    fa = field_audit(all_records)
    fa_path = write_field_audit(fa, str(out_path.parent))
    print(f"field audit: {len(fa['observed'])} keys in the response, "
          f"{len(fa['absent'])} read-but-absent, "
          f"{len(fa['unmapped'])} sent-but-unread -> {fa_path}")
    for gone, near in sorted(fa["renames"].items()):
        print(f"  possible rename: {gone} -> {', '.join(near)}")

    rows = [flatten(r) for r in all_records]
    # Before the overlay, so the guard compares mapping output to mapping
    # output, and before the write, so a collapse leaves the file untouched.
    assert_field_population(rows, out_path, allow_field_loss=allow_field_loss)
    rows, fieldnames = apply_manual_preservation(rows, out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def selftest():
    """Exercises the field-population guard. No network, no files."""
    checks = []

    def check(label, ok):
        checks.append((label, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    fields = ["date", "name", "capacity_mw"]
    # 40 rows with everything populated, against the same shape emptied.
    full = [{"date": "2026-01-0%d" % (i % 9 + 1), "name": f"P{i}",
             "capacity_mw": "100"} for i in range(40)]
    emptied = [dict(r, date="") for r in full]

    pop = field_population(full, fields)
    check("population counts non-empty values", pop["date"] == 40)
    check("population treats None as empty",
          field_population([{"date": None}], ["date"])["date"] == 0)
    check("population strips whitespace",
          field_population([{"date": "   "}], ["date"])["date"] == 0)

    hits = population_violations(full, emptied, fields)
    check("total collapse is flagged",
          [h[0] for h in hits] == ["date"] and hits[0][1:] == (40, 0))
    check("unaffected fields are not flagged",
          "name" not in [h[0] for h in hits])

    check("the observed 316 -> 10 drop is flagged",
          population_violations(
              [{"date": "x"}] * 316, [{"date": "x"}] * 10, ["date"]) != [])
    check("an unchanged field is clean",
          population_violations(full, full, fields) == [])
    check("ordinary churn is not flagged",
          population_violations(
              [{"date": "x"}] * 100,
              [{"date": "x"}] * 95, ["date"]) == [])
    check("a half-empty field is not flagged at exactly the ratio",
          population_violations(
              [{"date": "x"}] * 100,
              [{"date": "x"}] * 51, ["date"]) == [])
    check("an always-sparse field is ignored",
          population_violations(
              [{"date": "x"}] * (FIELD_LOSS_MIN_PRIOR - 1), [], ["date"]) == [])

    # --- field audit -------------------------------------------------------
    keys = api_keys_read()
    check("the read-key set is probed from flatten, not listed",
          {"date", "capacity_mw", "municipality", "id"} <= keys)
    check("a key flatten never reads is not in the set", "nonsense" not in keys)

    # The real 2026-09-10 shape: six fields gone, six new names present.
    renamed = [{"id": 1, "name": "A", "announcedDate": "2026-01-01",
                "capacityMw": 100, "sizeAcres": 10, "updatedAtIso": "x",
                "ownEnergy": True, "exemptFromMoratorium": False}]
    fa = field_audit(renamed, keys)
    check("a field the source stopped sending is reported absent",
          "date" in fa["absent"] and "capacity_mw" in fa["absent"])
    check("a key the source sent that nothing reads is reported",
          "announcedDate" in fa["unmapped"] and "capacityMw" in fa["unmapped"])
    check("a mapped key that is present is not reported either way",
          "name" not in fa["absent"] and "name" not in fa["unmapped"])
    check("similar names are paired as a possible rename",
          "capacityMw" in fa["renames"].get("capacity_mw", []))
    check("pairing is a suggestion, never applied",
          isinstance(fa["renames"], dict) and "observed" in fa)

    clean = field_audit([{k: "" for k in keys}], keys)
    check("a response carrying every mapped key reports nothing absent",
          clean["absent"] == [])
    check("a response carrying only mapped keys reports nothing unread",
          clean["unmapped"] == [])
    check("an empty response does not invent unmapped keys",
          field_audit([], keys)["unmapped"] == [])
    check("an empty response reports every mapped key as absent",
          len(field_audit([], keys)["absent"]) == len(keys))

    n_ok = sum(1 for _, ok in checks if ok)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def main():
    parser = argparse.ArgumentParser(description='Scrape trackdatacenters.com -> CSV')
    parser.add_argument('--out', default='data/proposals.csv', help='Output CSV path')
    parser.add_argument('--allow-field-loss', action='store_true',
                        help='accept a collapsed field as a real upstream '
                             'removal rather than an unnoticed rename')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    scrape(Path(args.out), allow_field_loss=args.allow_field_loss)


if __name__ == '__main__':
    main()
