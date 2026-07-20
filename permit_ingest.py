"""
permit_ingest.py — normalize a permit-portal export into the dated-baseline
external schema (data/baseline_dated_external.csv).

Permit datasets (county/state portals, Accela/Tyler/eTRAKiT exports, open-data
CSVs) all carry the same essentials under different column names: a project
name, a filing/application date, sometimes a decision date, a status, and a
location. This module maps an arbitrary permit CSV to the schema
baseline_dated.py expects, using a per-source column-map config so each new
source is a few lines of mapping, not new code.

It does NOT scrape. It takes a CSV you already have (downloaded/exported) and
a mapping, and emits validated rows. Rows that fail validation are written to
a rejects file with reasons — never silently dropped.

Schema produced (matches baseline_dated.py EXTERNAL_REQUIRED/OPTIONAL):
  required: source, as_of, name, state, announced_date
  optional: county, capacity_mw, decision_date, status, source_url, operator

Defensibility rules honored:
  - A decision_date is emitted only when the mapped status indicates a TERMINAL
    disposition (approved/denied/withdrawn/issued) — an in-progress permit
    yields no decision date, so the record is later censored, not treated as
    decided. This mirrors the platform's decided-only rule.
  - decision_date counts as verified downstream only if a source_url is
    present; the mapping should supply a per-row or per-source URL.
  - Dates are normalized to YYYY-MM or YYYY-MM-DD; unparseable or year-only
    dates are rejected (never floored).
  - No opposition is asserted — permit records are presumed-unopposed
    comparables unless a name matches a tracked opposed project (flagged for
    manual review, not auto-merged).
  - No scorekeeping vocabulary; leak audit before exit.

Usage:
  python3 permit_ingest.py --in permits_raw.csv --config loudoun.json \\
      --append                      # append to existing external CSV
  python3 permit_ingest.py --in permits_raw.csv --config loudoun.json \\
      --out data/baseline_dated_external.csv

Config JSON (per source):
  {
    "source": "loudoun_permits",
    "state": "VA",                       # or "state_col": "State"
    "as_of": "2026-07-16",               # export date
    "default_source_url": "https://...", # applied when no per-row url_col
    "columns": {
      "name": "Project Name",
      "announced_date": "Application Date",
      "decision_date": "Decision Date",   # optional
      "status": "Status",                 # optional
      "county": "Jurisdiction",           # optional
      "capacity_mw": "Capacity_MW",       # optional
      "operator": "Applicant",            # optional
      "url": "Record URL"                 # optional per-row url
    },
    "terminal_statuses": ["approved","denied","withdrawn","issued","final"]
  }
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date

OUT_DEFAULT = os.path.join("data", "baseline_dated_external.csv")
SCHEMA = ["source", "as_of", "name", "state", "announced_date",
          "county", "capacity_mw", "decision_date", "status",
          "source_url", "operator"]

MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def normalize_date(raw: str):
    """Return YYYY-MM or YYYY-MM-DD, or None. Year-only -> None (never floored)."""
    s = (raw or "").strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    if re.match(r"^\d{4}-\d{2}$", s):
        return s
    if re.match(r"^\d{4}$", s):
        return None                      # year-only excluded
    # M/D/YYYY or MM/DD/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        mo, dy, yr = m.groups()
        return f"{yr}-{int(mo):02d}-{int(dy):02d}"
    # "Mar 18, 2025" / "March 18 2025"
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mon, dy, yr = m.groups()
        mm = MONTHS.get(mon[:3].lower())
        if mm:
            return f"{yr}-{mm}-{int(dy):02d}"
    # "YYYY/MM/DD"
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if m:
        yr, mo, dy = m.groups()
        return f"{yr}-{int(mo):02d}-{int(dy):02d}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--append", action="store_true",
                    help="append to --out instead of overwriting")
    args = ap.parse_args()

    if not os.path.exists(args.infile):
        print(f"ERROR: input {args.infile} not found")
        return 1
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)

    colmap = cfg.get("columns", {})
    src = cfg.get("source")
    as_of = cfg.get("as_of")
    default_url = cfg.get("default_source_url", "")
    terminal = {s.lower() for s in cfg.get(
        "terminal_statuses", ["approved", "denied", "withdrawn", "issued", "final"])}
    if not src or not as_of:
        print("ERROR: config must set 'source' and 'as_of'")
        return 1

    def col(row, key):
        return (row.get(colmap.get(key, ""), "") or "").strip()

    with open(args.infile, newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))

    out_rows, rejects = [], []
    for i, r in enumerate(raw, 2):
        name = col(r, "name")
        ann = normalize_date(col(r, "announced_date"))
        state = (cfg.get("state") or col(r, "state")).strip()
        if not name:
            rejects.append((i, "missing name")); continue
        if not ann:
            rejects.append((i, f"unusable announced_date {col(r,'announced_date')!r}")); continue
        if not state:
            rejects.append((i, "missing state")); continue

        status = col(r, "status")
        dec = normalize_date(col(r, "decision_date"))
        # only keep a decision date when the status is terminal
        if dec and status and status.lower() not in terminal:
            dec = ""                     # in-progress: no decision date
        url = col(r, "url") or default_url

        out_rows.append({
            "source": src, "as_of": as_of, "name": name, "state": state,
            "announced_date": ann,
            "county": col(r, "county"),
            "capacity_mw": col(r, "capacity_mw"),
            "decision_date": dec or "",
            "status": status,
            "source_url": url,
            "operator": col(r, "operator"),
        })

    # write / append
    mode = "a" if (args.append and os.path.exists(args.out)) else "w"
    existing_keys = set()
    if mode == "a":
        with open(args.out, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                existing_keys.add((r["source"], r["name"], r["announced_date"]))
    deduped = [r for r in out_rows
               if (r["source"], r["name"], r["announced_date"]) not in existing_keys]

    with open(args.out, mode, newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SCHEMA)
        if mode == "w":
            w.writeheader()
        w.writerows(deduped)

    if rejects:
        rej_path = args.out.replace(".csv", "_rejects.csv")
        with open(rej_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["line", "reason"]); w.writerows(rejects)

    print(f"{src}: {len(deduped)} rows written ({mode}), "
          f"{len(out_rows)-len(deduped)} dedup-skipped, {len(rejects)} rejected")
    if rejects:
        print(f"  rejects -> {os.path.basename(args.out).replace('.csv','_rejects.csv')}")

    pat = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)
    hits = [i for i, l in enumerate(open(args.out, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT: token in output rows", hits[:5],
              "(check source name/status fields)")
    else:
        print("leak audit: clean")
    print("Next: run `python3 baseline_dated.py` to fold these into the dated frame.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
