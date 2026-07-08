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

# The QC gate modules live in qc/. Running from the repo root previously fell
# back to cleaner-only output because they were not importable; bootstrap the
# directory BEFORE importing the cleaner, whose legislative-outcome integration
# probes for these modules at import time. Root stays first on sys.path, so
# `import enrichment` inside the gate resolves to the single root classifier.
_QC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qc")
if os.path.isdir(_QC_DIR) and _QC_DIR not in sys.path:
    sys.path.append(_QC_DIR)

import clean_opposition_data as cleaner

try:
    import outcome_defensibility as _OD
    _HAVE_DEFENSIBILITY = True
except Exception as _e:
    print(f"  ! outcome_defensibility unavailable ({_e.__class__.__name__}: {_e}); "
          "graded-outcome columns will be skipped.")
    _HAVE_DEFENSIBILITY = False

try:
    import group_registry as _GR
    import date_recovery as _DR
    import review_worklists as _RW
    _HAVE_QUALITY_MODULES = True
except Exception as _e:
    print(f"  ! quality modules unavailable ({_e.__class__.__name__}: {_e}); "
          "registry/date-recovery/worklists skipped.")
    _HAVE_QUALITY_MODULES = False

import hashlib as _hashlib
from datetime import date as _date


def _quality_passes(records, outdir):
    """Group registry, date recovery, review worklists. Additive columns only."""
    if not _HAVE_QUALITY_MODULES:
        return
    reg, vmap = _GR.build_registry(records)
    _GR.annotate(records, vmap)
    n_groups = _GR.write_registry(records, reg,
                                  os.path.join(outdir, "group_registry.csv"))
    dr = _DR.apply_recovery(records, outdir)
    wl = _RW.write_worklists(records, outdir)
    vs = _RW.write_validation_sample(records, outdir)
    print(f"  Registry: {n_groups} canonical groups | dates recovered: "
          f"{dr['recovered']} (queued for redirect resolution: {dr.get('needs_redirect', 0)}; "
          f"still missing {dr['still_missing']})")
    print(f"  Worklists: {wl['conflicts']} conflicts, {wl['stale_pending']} stale pendings")
    print(f"  Validation sample: {vs['sampled']} rows across {vs['strata']} mechanism strata")
    _data_health_warnings(records)
    _write_codebook(outdir)
    try:
        import metrics as _M
        print(f"  Metrics: {_M.headline_report(records, outdir)}")
    except Exception as _e:
        print(f"  ! metrics report skipped ({_e.__class__.__name__}: {_e})")


def _data_health_warnings(records):
    """Interpretation hazards that belong in every build log, not in a
    footnote: non-random missing dates and a collapsed severity scale."""
    undated = [r for r in records
               if not str(r.get("Date", "")).strip()
               and not str(r.get("recovered_date", "")).strip()]
    gnews = sum(1 for r in undated if "news.google" in str(r.get("Source URL", "")))
    if undated:
        print(f"  ! Temporal coverage: {len(undated)} rows lack any date "
              f"({gnews} from Google News redirects); recent-period trend "
              "counts are floors, not totals.")
    sev = {}
    for r in records:
        v = str(r.get("Severity", "")).strip()
        if v:
            sev[v] = sev.get(v, 0) + 1
    if sev and len(sev) <= 2:
        print(f"  ! Severity scale collapsed to {sorted(sev)} "
              f"({sev}); treat as binary, not a 1-5 intensity measure.")


def _write_codebook(outdir):
    """CODEBOOK.md generated from the classifier tables themselves, so the
    documented definitions can never drift from the code that applies them."""
    try:
        import enrichment as _E
        import outcome_defensibility as _OD
    except Exception:
        return
    L = ["# Codebook (auto-generated - do not edit; regenerated on every build)",
         "", "## Mechanisms (priority order; the highest-priority match applies)", ""]
    for name, strength, is_block, pats in _E.MECHANISMS:
        L.append(f"**{name}** - strength {strength} "
                 f"({_E.STRENGTH_LABEL.get(strength, '?')}), "
                 f"{'BLOCK' if is_block else 'non-block'}. "
                 f"Triggers: {', '.join(pats[:8])}"
                 + (" ..." if len(pats) > 8 else ""))
    L += ["", "## Concerns (grievances; independent of mechanism)", ""]
    for name, pats in _E.CONCERNS:
        L.append(f"**{name}**: {', '.join(pats[:8])}" + (" ..." if len(pats) > 8 else ""))
    L += ["", "## Outcome ladder", "",
          "blocked_confirmed - the opposed project/measure was verifiably "
          "stopped (independent finality evidence: terminal status or bill stage)",
          "restricted_conditional - a conditional restriction was imposed; the "
          "project was NOT stopped",
          "blocked_unverified - recorded as stopped, without independent evidence",
          "advanced_confirmed / advanced_unverified - the project/measure "
          "advanced; verified / unverified",
          "",
          "These labels describe what happened to the opposed project or "
          "measure. Scorekeeping terms ('win'/'loss') appear only when quoting the "
          "raw Community Outcome field, always in quotes, and are never used "
          "in derived statistics: a denied permit, an enacted moratorium, and "
          "a defeated bill are different events - pair every grade with "
          "qc_mechanism.",
          "mixed / pending - as recorded / everything else",
          "", "Finality evidence codes: bill_stage > terminal_status > "
          "outcome_label_only (never sufficient for *_confirmed) > none."]
    open(os.path.join(outdir, "CODEBOOK.md"), "w").write("\n".join(L) + "\n")


def _snapshot_manifest(clean_path, n_rows, outdir):
    """Append (date, sha256, rows) so any externally quoted number is
    reproducible against the exact feed that produced it."""
    snapdir = os.path.join(outdir, "snapshots")
    os.makedirs(snapdir, exist_ok=True)
    h = _hashlib.sha256(open(clean_path, "rb").read()).hexdigest()
    line = f"{_date.today().isoformat()},{h},{n_rows},{os.path.basename(clean_path)}\n"
    mpath = os.path.join(snapdir, "manifest.csv")
    if not os.path.exists(mpath):
        open(mpath, "w").write("date,sha256,rows,file\n")
    # identical rebuilds (same hash) should not stack duplicate manifest rows
    last = ""
    with open(mpath) as fh:
        for last in fh:
            pass
    if h not in last:
        open(mpath, "a").write(line)
    print(f"  Snapshot: sha256 {h[:12]}... ({n_rows} rows) -> {mpath}")


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
        _quality_passes(result.clean, args.outdir)

        clean_path = os.path.join(args.outdir, os.path.basename(args.out))
        cols = records_to_csv(result.clean, cleaned_cols, clean_path)
        _snapshot_manifest(clean_path, len(result.clean), args.outdir)
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
            print(f"  Defensibility: {dsum['grades']}")
            print(f"  Block status : {dsum['block_status']}  |  conflicts flagged: {dsum['conflicts']}")
            _quality_passes(recs, args.outdir)
            cleaned = pd.DataFrame(recs)
        cleaned.to_csv(clean_path, index=False, quoting=csv.QUOTE_MINIMAL)
        _snapshot_manifest(clean_path, len(cleaned), args.outdir)
        print(f"\nCleaner-only output written: {clean_path} ({len(cleaned.columns)} columns)")
        print("  (gate skipped — see message above)")

    # Always write the cleaner's own change report alongside
    cleaner.write_report(report, changelog, n_raw,
                         path=os.path.join(args.outdir, "data_quality_report.md"))
    pd.DataFrame(changelog).to_csv(os.path.join(args.outdir, "change_log.csv"), index=False)
    print("  Wrote data_quality_report.md, change_log.csv")


if __name__ == "__main__":
    main()
