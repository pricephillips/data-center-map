#!/usr/bin/env python3
"""
build_clean_feed.py
========================================================================
End-to-end pipeline for the Data Center Opposition Tracker.

    raw master_opposition.csv
        │
        ▼  clean_opposition_data.clean()      ← fixes fixable issues, adds
        │                                        analytical columns
        ▼  qc_pipeline.run()                   ← validity GATE: quarantines
        │                                        genuinely-bad records,
        │                                        attaches qc_* enrichment
        ▼
    master_opposition_clean.csv   ← the file the map + Notion sync read
    quarantine.json               ← records held out, with reasons
    qc_report.md                  ← human-readable gate report

WHY THIS ORDER
  The gate treats mechanically-fixable problems (stringified-dict URLs,
  statewide capital pins) as HIGH/blocking and quarantines them. Running the
  cleaner first repairs those, so on the current data the baseline-blocking
  quarantine count drops from 281 to 0 — the records are recovered into the
  feed instead of dropped, while the gate still removes the genuinely invalid
  ones (duplicates, unplaceable, bad coordinates, etc.).

GRACEFUL DEGRADATION
  If the QC modules (qc_pipeline, schema_adapter, enrichment,
  legislative_outcome) are not importable, this still runs the cleaner and
  writes the cleaned CSV, skipping the gate with a warning. That makes it
  usable today and full-strength once the modules are on the path.

USAGE
    python build_clean_feed.py                       # fetch raw from GitHub
    python build_clean_feed.py --in master_opposition.csv
    python build_clean_feed.py --in raw.csv --out master_opposition_clean.csv
"""

import argparse
import csv
import json
import os
import sys

import pandas as pd

import clean_opposition_data as cleaner

try:
    import outcome_defensibility as _OD
    _HAVE_DEFENSIBILITY = True
except Exception as _e:
    print(f"  ! outcome_defensibility unavailable ({_e.__class__.__name__}: {_e}); "
          "graded-outcome columns will be skipped.")
    _HAVE_DEFENSIBILITY = False


def gate_available():
    try:
        import qc_pipeline  # noqa: F401
        return True
    except Exception as e:
        print(f"  ! QC gate unavailable ({e.__class__.__name__}: {e}).")
        print("    Falling back to cleaner-only output. Place qc_pipeline.py and its")
        print("    modules (schema_adapter, enrichment, legislative_outcome) on the path")
        print("    to enable the validity gate.")
        return False


def records_to_csv(records, column_order, path):
    """Write a list of dicts to CSV, preserving column_order then appending
    any extra keys (e.g. qc_* enrichment) in first-seen order."""
    cols = list(column_order)
    seen = set(cols)
    for r in records:
        for k in r.keys():
            if k not in seen:
                cols.append(k)
                seen.add(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return cols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", help="raw input CSV (default: fetch from GitHub)")
    ap.add_argument("--out", dest="out", default="master_opposition_clean.csv")
    ap.add_argument("--outdir", dest="outdir", default=".", help="directory for reports/quarantine")
    ap.add_argument("--overrides", dest="overrides", default="project_overrides.csv",
                    help="optional CSV of cross-venue project_id override rules")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    # Load any curated cross-venue project overrides so they apply in the
    # production pipeline (not only when running the cleaner standalone).
    n_ov = cleaner.load_project_overrides(args.overrides)
    if n_ov:
        print(f"Loaded {n_ov} project override rule(s) from {args.overrides}.")

    # 1. Load raw
    raw = cleaner.load(args.inp)
    n_raw = len(raw)
    n_raw_cols = len(raw.columns)
    print(f"Loaded {n_raw} raw rows, {n_raw_cols} columns.")

    # 2. Clean (pre-process)
    cleaned, report, changelog = cleaner.clean(raw)
    cleaned_cols = list(cleaned.columns)
    print(f"Cleaner applied. {len(changelog)} in-place value fixes; "
          f"{len(cleaned_cols) - n_raw_cols} analytical columns added.")

    # 3. Gate (validate + enrich), if available
    if gate_available():
        import qc_pipeline
        records = cleaned.to_dict("records")
        result = qc_pipeline.run(records)

        # Add map_pinnable: a single authoritative flag for point-map / heatmap
        # consumers. True only for the primary record of a project that has real
        # coordinates and is NOT a state/federal jurisdiction — so dashboards
        # never plot duplicate rows or pile statewide actions onto capitals.
        for r in result.clean:
            jl = str(r.get("qc_jurisdiction_level", "")).strip().lower()
            prim = str(r.get("is_primary_record", "")).strip().lower() == "true"
            try:
                la, lo = float(r.get("lat", "")), float(r.get("lon", ""))
                has_coords = not (la == 0 and lo == 0)
            except (TypeError, ValueError):
                has_coords = False
            r["map_pinnable"] = bool(prim and has_coords and jl not in ("state", "federal"))

        # Defensibility layer: graded outcome ladder + enactment-gated blocks
        if _HAVE_DEFENSIBILITY:
            dsum = _OD.apply_defensibility(result.clean)
            print(f"  Defensibility: {dsum['grades']}")
            print(f"  Block status : {dsum['block_status']}  |  conflicts flagged: {dsum['conflicts']}")

        clean_path = os.path.join(args.outdir, os.path.basename(args.out))
        cols = records_to_csv(result.clean, cleaned_cols, clean_path)
        json.dump(result.quarantine,
                  open(os.path.join(args.outdir, "quarantine.json"), "w", encoding="utf-8"),
                  indent=2)
        open(os.path.join(args.outdir, "qc_report.md"), "w", encoding="utf-8").write(
            qc_pipeline.render_markdown(result))

        print(f"\nGate complete:")
        print(f"  Passed to feed : {len(result.clean)} / {n_raw}")
        print(f"  Quarantined    : {result.n_blocked}")
        print(f"  Wrote {clean_path} ({len(cols)} columns incl. qc_* enrichment)")
        print(f"  Wrote quarantine.json, qc_report.md")
    else:
        clean_path = os.path.join(args.outdir, os.path.basename(args.out))
        if _HAVE_DEFENSIBILITY:
            recs = cleaned.to_dict("records")
            dsum = _OD.apply_defensibility(recs)   # computes enrichment itself
            cleaned = pd.DataFrame(recs)
            print(f"  Defensibility: {dsum['grades']}")
            print(f"  Block status : {dsum['block_status']}  |  conflicts flagged: {dsum['conflicts']}")
        cleaned.to_csv(clean_path, index=False, quoting=csv.QUOTE_MINIMAL)
        print(f"\nCleaner-only output written: {clean_path} ({len(cleaned.columns)} columns)")
        print("  (gate skipped — see message above)")

    # Always write the cleaner's own change report alongside
    cleaner.write_report(report, changelog, n_raw,
                         path=os.path.join(args.outdir, "data_quality_report.md"))
    pd.DataFrame(changelog).to_csv(os.path.join(args.outdir, "change_log.csv"), index=False)
    print("  Wrote data_quality_report.md, change_log.csv")


if __name__ == "__main__":
    main()
