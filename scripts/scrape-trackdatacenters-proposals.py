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
import re
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


# ---------------------------------------------------------------------------
# Source key aliases
#
# On 2026-09-10 the source renamed four fields to a dateX/camelCase scheme
# without changing what they mean. flatten() reads every field with
# record.get(name, ''), so all four silently became empty strings and the run
# reported success. Downstream, announced_date in data/project_lifecycles.csv
# fell 302 -> 10 and the published coverage panel reported 3% of projects as
# carrying an announcement date -- a scraper bug rendered as a fact about the
# data, for four days, on the public site.
#
# Each column now names the response keys that may carry it, current name
# first and previous name after. Reading both means a rename costs one entry
# here instead of a silently emptied column, and a source that rolls one back
# keeps working. Old names are kept rather than replaced because nothing
# guarantees the source only moves forwards.
#
# The pairings come from data/scraper_field_audit.md, which lists each of
# these as sent-by-the-response-and-read-by-nothing while its old name is
# read-but-absent. They are applied with is_datelike() below standing behind
# the two date columns, so a pairing that turns out to be wrong writes empty
# and trips the population guard rather than feeding a wrong date into every
# downstream timeline.
# ---------------------------------------------------------------------------

SOURCE_KEYS = {
    'size_acres':  ('sizeAcres', 'size_acres'),
    'capacity_mw': ('capacityMw', 'capacity_mw'),
    'date':        ('dateAnnounced', 'date'),
    'lastUpdated': ('dateUpdated', 'lastUpdated'),
}

# The shapes project_resolution.parse_partial_date() accepts. The source sends
# genuinely partial dates -- "2026", "2026-1", "2026-1-15" -- so this cannot
# be a strict ISO test; before the rename only 16 of 302 announcement dates
# were day-precision. A full ISO timestamp is accepted because manual rows in
# data/proposals_added.csv carry that shape.
_DATELIKE = re.compile(r"^\d{4}(-\d{1,2}(-\d{1,2})?)?$|^\d{4}-\d{2}-\d{2}T")


def is_datelike(value):
    """True when parse_partial_date() downstream would get a date out of this."""
    return bool(_DATELIKE.match(str(value).strip())) if value is not None else False


def pick(record, *keys):
    """First of `keys` the record actually carries. Blank counts as absent."""
    for k in keys:
        v = record.get(k, '')
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == '':
            continue
        return v
    return ''


def pick_date(record, *keys):
    """pick(), but a value that is not a date at all is treated as absent.

    A wrong alias then empties the column, which the population guard stops
    the scrape over -- the loud failure -- instead of writing a plausible
    wrong value into the anchor every downstream timeline is measured from.
    """
    v = pick(record, *keys)
    return v if is_datelike(v) else ''


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
        'size_acres': pick(record, *SOURCE_KEYS['size_acres']),
        'capacity_mw': pick(record, *SOURCE_KEYS['capacity_mw']),
        'scale': record.get('scale', ''),
        'date': pick_date(record, *SOURCE_KEYS['date']),
        'lastUpdated': pick_date(record, *SOURCE_KEYS['lastUpdated']),
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


# A candidate's name is not enough to act on. On 2026-09-17 the source
# dropped approx and locationTbd and the audit paired locationTbd with
# locationConfidence -- a plausible name and an unanswerable question, because
# a boolean "location is to be determined" and a graded confidence are not the
# same field, and nothing in the report said which one had arrived. The
# pairing sat unresolvable for want of a single observed value.
#
# So the audit samples them. What decides a pairing is the shape of what the
# key holds -- a boolean, an enum, a number -- and that is cheap to show.
# Prose is described by its length rather than quoted: it answers the shape
# question no better, and quoting it would drip source text into a committed
# artifact run after run.

SAMPLE_LIMIT = 4        # distinct values shown per key
SAMPLE_MAX_CHARS = 40   # longer strings are described, not quoted


def _sample_value(value):
    """A compact rendering of one observed value, or None to skip it.

    None and blank are skipped rather than rendered: the question a sample
    answers is what the key holds when it holds something.
    """
    if value is None:
        return None
    if isinstance(value, bool):        # before int: bool is an int subclass
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if len(s) > SAMPLE_MAX_CHARS:
            return f"<str len={len(s)}>"
        return f'"{s}"'
    if isinstance(value, list):
        return f"<list n={len(value)}>"
    if isinstance(value, dict):
        shown = ", ".join(sorted(value)[:3])
        return f"<dict keys={shown}>" if shown else "<dict empty>"
    return f"<{type(value).__name__}>"


def field_samples(records, keys, limit=SAMPLE_LIMIT):
    """Up to `limit` distinct renderings per key, in first-seen order.

    A key present on every record but never populated yields no entry, which
    is itself worth reading: it is a landing place that would arrive as empty
    as the column it was meant to rescue.
    """
    out = {k: [] for k in keys}
    for r in records:
        for k in keys:
            bucket = out[k]
            if len(bucket) >= limit:
                continue
            s = _sample_value(r.get(k))
            if s is not None and s not in bucket:
                bucket.append(s)
    return {k: v for k, v in out.items() if v}


def field_audit(records, keys_read=None, source_keys=None):
    """Compare the keys the source sent against the keys the mapping wants.

    Returns a dict with:
      absent    keys flatten() reads that no record carries -- a field the
                source dropped or renamed away. A key whose SOURCE_KEYS alias
                group has a present member is not absent: the column is being
                read, just under a different name.
      unmapped  keys the source sent that flatten() never reads -- where a
                renamed field will be sitting
      renames   a suggested pairing of the two by name similarity, which is a
                prompt for a human, never applied automatically
      samples   observed values for each unmapped key, so a suggested pairing
                can be judged from the report instead of from its name
    """
    keys_read = api_keys_read() if keys_read is None else set(keys_read)
    source_keys = SOURCE_KEYS if source_keys is None else source_keys
    observed = set()
    for r in records:
        observed |= set(r.keys())
    # A key in an alias group is only absent when no member of its group is
    # present. Without this every legacy name in SOURCE_KEYS would be reported
    # absent forever -- true, and useless: the column is being read fine under
    # the current name, and an audit that cries about four healthy fields is
    # an audit nobody reads the week a fifth one actually breaks.
    covered = set()
    for group in source_keys.values():
        if set(group) & observed:
            covered |= set(group)
    absent = sorted(keys_read - observed - covered)
    unmapped = sorted(observed - keys_read)
    renames = {}
    for gone in absent:
        near = difflib.get_close_matches(gone, unmapped, n=3, cutoff=0.6)
        if near:
            renames[gone] = near
    return {"observed": sorted(observed), "absent": absent,
            "unmapped": unmapped, "renames": renames,
            "samples": field_samples(records, unmapped),
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
    samples = audit.get("samples", {})
    if audit["unmapped"]:
        lines += ["## Sent by the response, read by nothing", "",
                  "Candidate landing places for anything in the list above, "
                  "with up to "
                  f"{SAMPLE_LIMIT} distinct observed values each. Strings "
                  f"longer than {SAMPLE_MAX_CHARS} characters are described "
                  "by length rather than quoted.", ""]
        for k in audit["unmapped"]:
            seen = samples.get(k)
            lines.append(f"- `{k}` -- " + (", ".join(seen) if seen
                                           else "present, never populated"))
        lines.append("")
    if audit["renames"]:
        lines += ["## Suggested pairings", "",
                  "Paired by name similarity; the values are there so the "
                  "pairing can be judged rather than guessed. Two fields can "
                  "have similar names and different meanings, and the values "
                  "are usually what shows it.", "",
                  "| absent | candidate | values seen |", "|---|---|---|"]
        for k, cands in sorted(audit["renames"].items()):
            for c in cands:
                seen = samples.get(c)
                lines.append(f"| `{k}` | `{c}` | "
                             + (", ".join(seen) if seen
                                else "present, never populated") + " |")
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


def _present(value):
    """Absent means missing or blank. False and 0 are values, not absences.

    This matters because the two sides of the comparison arrive as different
    types. The previous run is read back from proposals.csv, where everything
    is a string, so a boolean field is "False" and a zero is "0" -- both
    non-empty. This run comes straight from flatten(), where they are the
    native False and 0. A truthiness test calls the same data populated on one
    side and empty on the other, which is exactly what blocked the scrape on
    2026-09-13 and 2026-09-14: approx, locationTbd and scale were reported
    collapsed while carrying identical values.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def field_population(rows, fields):
    """Count of rows carrying a value for each field."""
    return {
        f: sum(1 for r in rows if _present(r.get(f)))
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

    # The 2026-09-13 false positive. The previous run is read back from CSV
    # (all strings), this run comes from flatten() (native types). A
    # truthiness test called identical data populated on one side and empty
    # on the other, and stopped the scrape for two days.
    check("False is a value, not an absence", _present(False))
    check("zero is a value, not an absence", _present(0))
    check("an empty string is an absence", not _present(""))
    check("whitespace is an absence", not _present("   "))
    check("None is an absence", not _present(None))
    check('the string "False" is a value', _present("False"))
    check('the string "0" is a value', _present("0"))
    str_side = [{"approx": "False"}] * 40
    native_side = [{"approx": False}] * 40
    check("the same field does not collapse just by changing type",
          population_violations(str_side, native_side, ["approx"]) == [])
    check("a boolean flipping all-True to all-False is not a collapse",
          population_violations([{"a": True}] * 40, [{"a": False}] * 40,
                                ["a"]) == [])
    check("a field actually emptying is still a collapse",
          population_violations([{"a": "x"}] * 40, [{"a": ""}] * 40,
                                ["a"]) == [("a", 40, 0)])
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

    # The three fields the 2026-09-10 rename left unresolved. The other four
    # are aliased in SOURCE_KEYS and must no longer be reported; these have no
    # candidate anyone could confirm (moratoriumExempt has none at all, and
    # yearOpened -> dateOnline pairs a year against a date), so they are what
    # the absent/unmapped machinery still has to catch -- as it will have to
    # for whichever field the source renames next.
    renamed = [{"id": 1, "name": "A", "dateAnnounced": "2026-01-01",
                "capacityMw": 100, "sizeAcres": 10, "dateUpdated": "x",
                "btmPower": True, "dateOnline": "2027-01-01"}]
    fa = field_audit(renamed, keys)
    check("a field the source stopped sending is reported absent",
          "yearOpened" in fa["absent"] and "moratoriumExempt" in fa["absent"])
    check("a key the source sent that nothing reads is reported",
          "btmPower" in fa["unmapped"] and "dateOnline" in fa["unmapped"])
    check("a field now covered by an alias is reported neither way",
          not ({"date", "capacity_mw", "size_acres", "lastUpdated"}
               & set(fa["absent"]))
          and not ({"dateAnnounced", "capacityMw", "sizeAcres", "dateUpdated"}
                   & set(fa["unmapped"])))
    check("a mapped key that is present is not reported either way",
          "name" not in fa["absent"] and "name" not in fa["unmapped"])
    check("a rename with no similar name is listed but not paired",
          "yearOpened" not in fa["renames"]
          and "moratoriumExempt" not in fa["renames"])
    # Pairing only fires on name similarity, which is why the three above are
    # still open: difflib cannot pair yearOpened with dateOnline. It does pair
    # the easy shape, which is the common one.
    similar = field_audit([{"id": 1, "name": "A", "dateAnnounced": "2026-01-01",
                            "capacityMw": 1, "sizeAcres": 1, "dateUpdated": "x",
                            "moratoriumExemption": True}], keys)
    check("similar names are paired as a possible rename",
          "moratoriumExemption" in similar["renames"].get("moratoriumExempt", []))
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

    # --- value samples (the 2026-09-17 unanswerable pairing) --------------
    # locationTbd -> locationConfidence was paired on name and could not be
    # judged, because the report never said whether the candidate held a
    # boolean or a graded string. These checks pin the shapes that answer it.
    check("a boolean samples as a boolean, not as 1 or 0",
          _sample_value(True) == "True" and _sample_value(False) == "False")
    check("zero samples as a value", _sample_value(0) == "0")
    check("a short string is quoted", _sample_value(" high ") == '"high"')
    check("None and blank are skipped, not rendered",
          _sample_value(None) is None and _sample_value("") is None
          and _sample_value("   ") is None)
    long_prose = "x" * (SAMPLE_MAX_CHARS + 1)
    check("a long string is described by length, never quoted",
          _sample_value(long_prose) == f"<str len={SAMPLE_MAX_CHARS + 1}>")
    check("a string exactly at the cap is still quoted",
          _sample_value("y" * SAMPLE_MAX_CHARS) == '"' + "y" * SAMPLE_MAX_CHARS + '"')
    check("a list is described by length",
          _sample_value([1, 2, 3]) == "<list n=3>")
    check("a dict is described by its keys",
          _sample_value({"b": 1, "a": 2}) == "<dict keys=a, b>")

    samp = field_samples([{"k": "a"}, {"k": "a"}, {"k": "b"}], ["k"])
    check("repeated values are shown once", samp["k"] == ['"a"', '"b"'])
    many = field_samples([{"k": f"v{i}"} for i in range(20)], ["k"])
    check("samples are capped", len(many["k"]) == SAMPLE_LIMIT)
    check("a key present but never populated yields no sample",
          field_samples([{"k": ""}, {"k": None}], ["k"]) == {})

    fa_s = field_audit([{"id": 1, "name": "A", "locationConfidence": "exact",
                         "notes": long_prose}], keys)
    check("the audit samples the keys nothing reads",
          fa_s["samples"].get("locationConfidence") == ['"exact"'])
    check("the audit does not sample keys the mapping already reads",
          "name" not in fa_s["samples"])

    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:
        _p = write_field_audit(fa_s, _d)
        body = open(_p, encoding="utf-8").read()
    check("the report shows a candidate's observed values", '"exact"' in body)
    check("the report never quotes long source prose", long_prose not in body)
    check("the report describes long prose by length instead",
          f"<str len={len(long_prose)}>" in body)
    blank_fa = field_audit([{"id": 1, "name": "A", "emptyCandidate": ""}], keys)
    with _tf.TemporaryDirectory() as _d:
        blank_body = open(write_field_audit(blank_fa, _d), encoding="utf-8").read()
    check("an unpopulated candidate says so rather than looking absent",
          "`emptyCandidate` -- present, never populated" in blank_body)

    # --- source-key aliases (the 2026-09-10 rename) -----------------------
    renamed_rec = {"id": 7, "name": "N", "dateAnnounced": "2026-3",
                   "dateUpdated": "2026-4-2", "capacityMw": 250,
                   "sizeAcres": 80}
    f = flatten(renamed_rec)
    check("an announcement date under the new name is read",
          f["date"] == "2026-3")
    check("lastUpdated under the new name is read",
          f["lastUpdated"] == "2026-4-2")
    check("capacity under the new name is read", f["capacity_mw"] == 250)
    check("acreage under the new name is read", f["size_acres"] == 80)

    legacy_rec = {"id": 8, "name": "N", "date": "2025-1", "lastUpdated": "2025-2",
                  "capacity_mw": 10, "size_acres": 5}
    g = flatten(legacy_rec)
    check("the previous names still work if the source rolls back",
          (g["date"], g["lastUpdated"], g["capacity_mw"], g["size_acres"])
          == ("2025-1", "2025-2", 10, 5))

    both = flatten({"id": 9, "name": "N", "dateAnnounced": "2026-3",
                    "date": "1999-1"})
    check("the current name wins when the source sends both",
          both["date"] == "2026-3")
    check("a record carrying neither name yields empty, not a crash",
          flatten({"id": 10, "name": "N"})["date"] == "")

    # A pairing that is wrong must empty the column -- which the population
    # guard stops the scrape over -- not write a plausible non-date.
    check("a non-date under a date alias is discarded",
          flatten({"id": 11, "name": "N", "dateAnnounced": "Q2 next year"})
          ["date"] == "")
    check("a boolean under a date alias is discarded",
          flatten({"id": 12, "name": "N", "dateAnnounced": True})["date"] == "")
    check("partial dates the downstream parser accepts survive",
          all(is_datelike(v) for v in ("2026", "2026-1", "2026-1-15",
                                       "2026-01-15T05:00:00.000Z")))
    check("things it does not accept are rejected",
          not any(is_datelike(v) for v in ("", "soon", "26-1", "TBD", None)))
    check("a non-date alias is not date-filtered",
          flatten({"id": 13, "name": "N", "capacityMw": 0})["capacity_mw"] == 0)

    # The audit must not nag about legacy names that a live alias covers.
    alias_keys = api_keys_read()
    fa_alias = field_audit([renamed_rec], alias_keys)
    check("a legacy name covered by a live alias is not reported absent",
          "date" not in fa_alias["absent"]
          and "capacity_mw" not in fa_alias["absent"])
    check("the live alias is not reported as unread",
          "dateAnnounced" not in fa_alias["unmapped"])
    check("a field with no member present is still reported absent",
          "yearOpened" in fa_alias["absent"])

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
