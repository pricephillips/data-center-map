"""
verification_status.py

Classifies every row in master_opposition.csv by how well it is verified, and
holds the unverified rows out of the pipeline before they can reach the QC
gate, the project universe, the county layer, or any externally quotable
count.

Why this exists
---------------
The Google News RSS ingest that ran until 2026-07-24 appended 369 rows into
master_opposition.csv with blank Opposition Type, Entity "Unknown", blank
State, blank Date, and a news.google.com redirect in place of a publisher URL.
118 of those rows currently pass the QC gate. Once through, they receive a
project_id derived from a headline truncated at 50 characters, a qc_mechanism
inferred from that truncated headline (93 of the 118 come out as moratorium),
and a qc_is_block value of True in those 93 cases. None of them carries
coordinates, 90 of them carry no state, and none has a verified date.

That is inference from a headline standing in the source of truth alongside
sourced records. This module separates the two without deleting anything.

Statuses
--------
  sourced          Opposition Type present and a publisher URL present
  sourced_no_url   Opposition Type present, no Source URL (integrity flag,
                   retained; pre-dates this module)
  headline_only    Opposition Type blank and the Source URL is a Google News
                   redirect. The 369 rows above.
  incomplete       Opposition Type blank with some other URL or no URL

HOLDOUT (headline_only, incomplete) is removed from the feed build. COUNTABLE
(sourced, sourced_no_url) is what any external number may be derived from.

What this module does NOT do
----------------------------
It does not delete rows, it does not edit master_opposition.csv during a
pipeline run, and it does not promote anything. Held-out rows are written to
data/verification_holdout.csv with their status so they stay reviewable, and
untagged_triage.py builds the worklist for recovering them.

Optional local write-back (`--stamp`) adds the verification_status column to
master_opposition.csv so the status is visible in the file. That is a manual
operation on purpose: build_master_csv.py also writes that file, and the
pipeline computes status in memory instead to avoid racing it.

Usage
-----
  python verification_status.py                    classify and report, no writes
  python verification_status.py --report           write data/verification_status_report.md
  python verification_status.py --stamp            add the column to master_opposition.csv
  python verification_status.py --stamp --overwrite  restamp existing values
  python verification_status.py --write-countable /tmp/countable.csv
  python verification_status.py --selftest

Outputs
-------
  data/verification_status_report.md   counts by status, with the holdout named
  data/verification_holdout.csv        written by hold_out() during a feed build
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN = os.path.join(HERE, "master_opposition.csv")
DATA_DIR = os.path.join(HERE, "data")

COLUMN = "verification_status"

SOURCED = "sourced"
SOURCED_NO_URL = "sourced_no_url"
HEADLINE_ONLY = "headline_only"
INCOMPLETE = "incomplete"

ALL_STATUSES = (SOURCED, SOURCED_NO_URL, HEADLINE_ONLY, INCOMPLETE)

# Rows a count may be built from.
COUNTABLE = frozenset({SOURCED, SOURCED_NO_URL})

# Rows removed from the feed build. Held, not deleted.
HOLDOUT = frozenset({HEADLINE_ONLY, INCOMPLETE})

# Redirect hosts that stand in for a publisher URL rather than being one.
REDIRECT_HOSTS = ("news.google.com", "news.url.google.com")

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)


# Values that mean "empty" once a CSV has been through a loader. pandas turns a
# blank cell into float("nan") unless keep_default_na=False is passed, and a
# stringified NaN is truthy, which would silently classify every unverified row
# as sourced and let all of them back into the feed. This set closes that.
_EMPTYISH = {"", "nan", "none", "null", "na", "n/a", "<na>", "nat"}


def _s(row, key):
    v = row.get(key, "")
    if v is None:
        return ""
    if isinstance(v, float) and v != v:      # float("nan") is not equal to itself
        return ""
    out = str(v).strip()
    return "" if out.lower() in _EMPTYISH else out


def is_redirect_url(url):
    u = (url or "").strip().lower()
    if not u:
        return False
    return any(h in u for h in REDIRECT_HOSTS)


def classify(row):
    """Status for one row. Pure function of the row's own fields."""
    mech = _s(row, "Opposition Type")
    url = _s(row, "Source URL")
    if mech:
        # A curated row keeps its status even if its citation is a redirect.
        # Only untagged rows are held out.
        return SOURCED if url else SOURCED_NO_URL
    if is_redirect_url(url):
        return HEADLINE_ONLY
    return INCOMPLETE


def is_countable(row):
    """True if this row may contribute to an externally quotable number."""
    existing = _s(row, COLUMN)
    status = existing if existing in ALL_STATUSES else classify(row)
    return status in COUNTABLE


def countable_rows(rows):
    return [r for r in rows if is_countable(r)]


def stamp(rows, overwrite=False):
    """Set the verification_status column on a list of dicts, in place.
    Existing valid values are preserved unless overwrite is True. Returns a
    Counter of resulting statuses."""
    counts = Counter()
    for r in rows:
        existing = _s(r, COLUMN)
        if existing in ALL_STATUSES and not overwrite:
            counts[existing] += 1
            continue
        st = classify(r)
        r[COLUMN] = st
        counts[st] += 1
    return counts


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------

def hold_out(data, outdir="."):
    """Split rows into (kept, held) by verification status.

    Accepts a pandas DataFrame or a list of dicts and returns the same type it
    was given for `kept`, plus `held` as a list of dicts. Writes
    data/verification_holdout.csv so every removed row stays reviewable.
    Computes status in memory; never edits the input file.
    """
    is_df = hasattr(data, "to_dict") and hasattr(data, "columns")
    rows = data.to_dict("records") if is_df else list(data)

    keep_rows, held_rows = [], []
    for r in rows:
        existing = _s(r, COLUMN)
        st = existing if existing in ALL_STATUSES else classify(r)
        r[COLUMN] = st
        (held_rows if st in HOLDOUT else keep_rows).append(r)

    _write_holdout(held_rows, outdir)

    if is_df:
        import pandas as pd
        cols = list(data.columns)
        if COLUMN not in cols:
            cols = cols + [COLUMN]
        kept = pd.DataFrame(keep_rows, columns=cols) if keep_rows else \
            pd.DataFrame(columns=cols)
        return kept, held_rows
    return keep_rows, held_rows


def _write_holdout(held, outdir="."):
    target_dir = os.path.join(outdir, "data")
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, "verification_holdout.csv")
    fields = ["verification_status", "Incident", "Summary", "Source URL",
              "Entity", "State", "County", "Date", "Status"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in held:
            w.writerow({k: _s(r, k) for k in fields})
    return path


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report_markdown(counts, total, source_label):
    held = sum(counts.get(s, 0) for s in HOLDOUT)
    countable = sum(counts.get(s, 0) for s in COUNTABLE)
    L = ["# Verification status", "",
         f"Source: `{source_label}`", f"Rows: {total}", "",
         "| status | rows | counted externally | reaches the feed |",
         "| :-- | --: | :-- | :-- |"]
    for st in ALL_STATUSES:
        n = counts.get(st, 0)
        L.append(f"| `{st}` | {n} | "
                 f"{'yes' if st in COUNTABLE else 'no'} | "
                 f"{'no' if st in HOLDOUT else 'yes'} |")
    L += ["",
          f"Countable rows: {countable}",
          f"Held out of the feed build: {held}",
          "",
          "Held rows are preserved in `master_opposition.csv` and listed in "
          "`data/verification_holdout.csv`. They are recoverable through "
          "`untagged_triage.py`, which builds the per-row review worklist. "
          "Recovery requires a publisher URL, not a redirect.",
          ""]
    return "\n".join(L)


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        r = csv.DictReader(fh)
        return list(r), (r.fieldnames or [])


def write_stamped(path, rows, header):
    if COLUMN not in header:
        header = list(header) + [COLUMN]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in header})
    os.replace(tmp, path)
    return header


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            ok = False

    g = "https://news.google.com/rss/articles/CBMiabcdef?oc=5"
    check("google redirect + blank mechanism -> headline_only",
          classify({"Opposition Type": "", "Source URL": g}) == HEADLINE_ONLY)
    check("publisher url + mechanism -> sourced",
          classify({"Opposition Type": "moratorium",
                    "Source URL": "https://example.org/a"}) == SOURCED)
    check("mechanism, no url -> sourced_no_url",
          classify({"Opposition Type": "moratorium", "Source URL": ""}) == SOURCED_NO_URL)
    check("blank mechanism, no url -> incomplete",
          classify({"Opposition Type": "", "Source URL": ""}) == INCOMPLETE)
    check("redirect + mechanism stays countable",
          classify({"Opposition Type": "moratorium", "Source URL": g}) in COUNTABLE)

    rows = [{"Opposition Type": "", "Source URL": g},
            {"Opposition Type": "zoning", "Source URL": "https://x.org/1"}]
    c = stamp(rows)
    check("stamp assigns both", c[HEADLINE_ONLY] == 1 and c[SOURCED] == 1)
    check("stamp is idempotent", stamp(rows)[HEADLINE_ONLY] == 1)

    rows[0][COLUMN] = SOURCED
    check("stamp preserves a manual override",
          stamp(rows)[SOURCED] == 2)
    check("overwrite restamps", stamp(rows, overwrite=True)[HEADLINE_ONLY] == 1)

    check("is_countable respects the column",
          is_countable({COLUMN: SOURCED, "Opposition Type": "", "Source URL": g}))
    check("countable_rows filters", len(countable_rows(rows)) == 1)

    import tempfile
    td_nan = tempfile.mkdtemp()
    with tempfile.TemporaryDirectory() as td:
        kept, held = hold_out([dict(r) for r in rows], outdir=td)
        check("hold_out splits", len(kept) == 1 and len(held) == 1)
        check("holdout file written",
              os.path.exists(os.path.join(td, "data", "verification_holdout.csv")))
        try:
            import pandas as pd
            df = pd.DataFrame([dict(r) for r in rows])
            kdf, held2 = hold_out(df, outdir=td)
            check("hold_out returns a DataFrame", hasattr(kdf, "columns") and len(kdf) == 1)
            check("column added to DataFrame", COLUMN in list(kdf.columns))
        except ImportError:
            print("  SKIP  pandas path (pandas not installed)")

    # Regression guard: a loader that leaves NaN in place must not make the
    # holdout silently empty. This is the failure mode that would put every
    # unverified row back into the feed.
    nan_row = {"Opposition Type": float("nan"), "Source URL": g}
    check("float NaN mechanism is empty", classify(nan_row) == HEADLINE_ONLY)
    check("string 'nan' mechanism is empty",
          classify({"Opposition Type": "nan", "Source URL": g}) == HEADLINE_ONLY)
    check("None mechanism is empty",
          classify({"Opposition Type": None, "Source URL": g}) == HEADLINE_ONLY)
    try:
        import pandas as pd
        import io
        _csv = ('Opposition Type,Source URL,Incident\n'
                ',' + g + ',held\n'
                'moratorium,https://pub.example/a,kept\n')
        _df = pd.read_csv(io.StringIO(_csv))          # default NaN handling
        _kept, _held = hold_out(_df, outdir=td_nan)
        check("pandas default NaN loading still holds the row out",
              len(_held) == 1 and len(_kept) == 1)
    except ImportError:
        print("  SKIP  pandas NaN path (pandas not installed)")

    md = report_markdown(Counter({SOURCED: 5, HEADLINE_ONLY: 2}), 7, "test.csv")
    check("report has no scorekeeping language", not LEAK_RE.search(md))
    check("report has no em-dash", "\u2014" not in md)

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=DEFAULT_IN)
    ap.add_argument("--stamp", action="store_true",
                    help="write the verification_status column back into the input CSV")
    ap.add_argument("--overwrite", action="store_true",
                    help="with --stamp, replace existing status values")
    ap.add_argument("--report", action="store_true",
                    help="write data/verification_status_report.md")
    ap.add_argument("--write-countable", dest="write_countable", default=None,
                    metavar="PATH",
                    help="write a countable-rows-only copy of the input CSV to "
                         "PATH, for tools that read the raw file directly")
    ap.add_argument("--outdir", default=HERE)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if not os.path.exists(a.inp):
        print(f"verification_status: input not found: {a.inp}")
        return 1

    rows, header = load_csv(a.inp)
    counts = stamp(rows, overwrite=a.overwrite)
    total = len(rows)

    print(f"verification_status: {total} rows")
    for st in ALL_STATUSES:
        if counts.get(st):
            print(f"  {st:<16} {counts[st]}")
    print(f"  countable        {sum(counts.get(s, 0) for s in COUNTABLE)}")
    print(f"  held out         {sum(counts.get(s, 0) for s in HOLDOUT)}")

    if a.report:
        os.makedirs(os.path.join(a.outdir, "data"), exist_ok=True)
        p = os.path.join(a.outdir, "data", "verification_status_report.md")
        open(p, "w", encoding="utf-8").write(
            report_markdown(counts, total, os.path.basename(a.inp)))
        print(f"  wrote {p}")

    if a.write_countable:
        keep = [r for r in rows if r.get(COLUMN) in COUNTABLE]
        d = os.path.dirname(os.path.abspath(a.write_countable))
        if d:
            os.makedirs(d, exist_ok=True)
        write_stamped(a.write_countable, keep, header)
        print(f"  wrote {a.write_countable} ({len(keep)} countable rows)")

    if a.stamp:
        write_stamped(a.inp, rows, header)
        print(f"  stamped {a.inp}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
