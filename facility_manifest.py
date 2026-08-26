#!/usr/bin/env python3
"""
facility_manifest.py

Publishes a freshness signal for the Layer A facility snapshots.

The facility layer is the largest dataset in this repository and the only one
that cannot say when it was last true. atlas.csv and ai_centers.csv are static
files with no acquisition pipeline behind them, and the three surfaces that
read them show their contents with no date and no row count, so a snapshot
frozen two years ago looks exactly like one refreshed this morning. This
module makes the difference visible.

It deliberately does not infer a vintage from the repository. A file committed
today can be years old upstream, so upstream_vintage is whatever
configs/facility_sources.json declares and nothing else; where nobody has
recorded it, the manifest says undeclared and the surfaces say undeclared.
What the repository does know is when the file last changed here, and that is
reported separately under exactly that name.

Reads
  configs/facility_sources.json
  the snapshot files it names

Writes
  data/facility_manifest.json

Usage
  python facility_manifest.py
  python facility_manifest.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "configs", "facility_sources.json")
OUT_JSON = os.path.join(HERE, "data", "facility_manifest.json")

# Column names that carry a country, in the order they are tried.
COUNTRY_KEYS = ("Country", "country")
US_VALUES = {"united states", "usa", "us", "u.s.", "united states of america"}


def row_counts(path: str) -> tuple[int, int | None]:
    """(total data rows, rows in the United States or None if unknowable).

    Counted with a csv reader, not by lines: ai_centers.csv carries embedded
    newlines inside quoted note fields, and counting lines reports 171 rows
    for a 29-row file.
    """
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    total = len(rows)
    key = next((k for k in COUNTRY_KEYS if rows and k in rows[0]), None)
    if key is None:
        return total, None
    us = sum(1 for r in rows if (r.get(key) or "").strip().lower() in US_VALUES)
    return total, us


def content_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _is_shallow(root: str) -> bool:
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True, timeout=20)
        return out.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def repo_last_changed(path: str, root: str = HERE) -> str | None:
    """Date the file last changed in this repository, ISO day precision.

    Provenance about the repository, never about the data. Returns None where
    it cannot be known, and the surfaces then say nothing rather than
    something wrong.

    A shallow checkout is the case that matters. CI checks out at depth 1, so
    `git log -1 -- atlas.csv` returns the tip commit whether or not that
    commit touched the file, and every run would report the snapshot as having
    changed today. The filesystem timestamp is no better there: in a fresh
    checkout it is the checkout time. A freshness signal that reports "changed
    today" on a file frozen for months is worse than one that admits it does
    not know, which is the whole reason this module exists.
    """
    rel = os.path.relpath(path, root)
    if _is_shallow(root):
        return None
    try:
        out = subprocess.run(
            ["git", "-C", root, "log", "-1", "--format=%cI", "--", rel],
            capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            stamp = out.stdout.strip()
            return stamp[:10] if stamp else None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def days_since(day: str | None, today: str) -> int | None:
    if not day:
        return None
    try:
        a = dt.date.fromisoformat(day)
        b = dt.date.fromisoformat(today)
    except ValueError:
        return None
    return (b - a).days


def prior_entries(root: str) -> dict:
    """Last run's manifest, keyed by source_id.

    The committed manifest is how this module remembers a change date across
    runs. Git can only supply one on a full checkout, and CI does not have
    one, so carrying the previous answer forward is what keeps the date
    correct rather than merely available.
    """
    try:
        with open(os.path.join(root, "data", "facility_manifest.json"),
                  encoding="utf-8") as fh:
            return {e.get("source_id"): e
                    for e in (json.load(fh).get("sources") or [])}
    except (OSError, ValueError):
        return {}


def build(config: dict, root: str = HERE, today: str | None = None) -> dict:
    today = today or dt.date.today().isoformat()
    prior = prior_entries(root)
    entries = []
    for src in config["sources"]:
        # A planned source has no file yet. It still belongs in the manifest,
        # because "declared, not yet acquired" is a different state from
        # "nobody has thought about it", and only one of the two is progress.
        if not src.get("file"):
            entries.append({
                "source_id": src["source_id"],
                "label": src["label"],
                "short_label": src.get("short_label", src["label"]),
                "file": None,
                "layer": src.get("layer", "A"),
                "publisher": src.get("publisher"),
                "landing_urls": src.get("landing_urls", []),
                "license": src.get("license", "unconfirmed"),
                "geography": src.get("geography"),
                "upstream_vintage": src.get("upstream_vintage"),
                "vintage_status": src.get("vintage_status", "undeclared"),
                "refresh": src.get("refresh", {}),
                "acquisition": src.get("acquisition", {}),
                "notes": src.get("notes", ""),
                "present": False, "rows": None, "rows_us": None,
                "sha256_12": None, "repo_last_changed": None,
                "days_since_repo_change": None,
            })
            continue
        path = os.path.join(root, src["file"])
        entry = {
            "source_id": src["source_id"],
            "label": src["label"],
            "short_label": src.get("short_label", src["label"]),
            "file": src["file"],
            "layer": src.get("layer", "A"),
            "publisher": src.get("publisher"),
            "landing_urls": src.get("landing_urls", []),
            "license": src.get("license", "unconfirmed"),
            "geography": src.get("geography"),
            "upstream_vintage": src.get("upstream_vintage"),
            "vintage_status": src.get("vintage_status", "undeclared"),
            "refresh": src.get("refresh", {}),
            "acquisition": src.get("acquisition", {}),
            "notes": src.get("notes", ""),
        }
        if not os.path.exists(path):
            entry.update({"present": False, "rows": None, "rows_us": None,
                          "sha256_12": None, "repo_last_changed": None,
                          "days_since_repo_change": None})
            entries.append(entry)
            continue
        total, us = row_counts(path)
        digest = content_hash(path)
        # The content hash decides the date, not the checkout. If the file is
        # byte-identical to what the last manifest described, it did not
        # change and the previous date still stands. If it differs, it changed
        # since that run and today is the honest answer. Git is consulted only
        # to bootstrap a source with no prior entry, and only where the
        # checkout has the history to answer.
        was = prior.get(src["source_id"]) or {}
        if was.get("sha256_12") == digest and was.get("repo_last_changed"):
            changed = was["repo_last_changed"]
        elif was.get("sha256_12") and was["sha256_12"] != digest:
            changed = today
        else:
            changed = repo_last_changed(path, root)
        entry.update({
            "present": True,
            "rows": total,
            "rows_us": us,
            "sha256_12": digest,
            "repo_last_changed": changed,
            "days_since_repo_change": days_since(changed, today),
        })
        entries.append(entry)

    piped = [e for e in entries if e["refresh"].get("pipeline")]
    dated = [e for e in entries if e["vintage_status"] == "declared"]
    pinning = [e for e in entries
               if (e.get("acquisition") or {}).get("status") == "needs_manual_pin"]
    return {
        "generated": today,
        "sources": entries,
        "totals": {
            "sources": len(entries),
            "rows": sum(e["rows"] or 0 for e in entries),
            "sources_with_a_pipeline": len(piped),
            "sources_with_a_declared_vintage": len(dated),
            "sources_awaiting_a_manual_pin": len(pinning),
        },
        "note": ("repo_last_changed is when the file last changed in this "
                 "repository. It is not the age of the data: a snapshot "
                 "committed today can be years old upstream. Where "
                 "vintage_status is undeclared, the honest reading is that "
                 "the age of the data is unknown, and the surfaces say so."),
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "data"))
        multi = os.path.join(tmp, "multi.csv")
        with open(multi, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["Name", "Country", "Notes"])
            w.writerow(["A", "United States", "line one\nline two\nline three"])
            w.writerow(["B", "China", "plain"])
            w.writerow(["C", "USA", "plain"])
        total, us = row_counts(multi)
        check("embedded newlines do not inflate the row count", total == 3)
        check("country filter counts United States and USA", us == 2)
        check("line count would have been wrong",
              sum(1 for _ in open(multi)) - 1 != total)

        nocountry = os.path.join(tmp, "nocountry.csv")
        with open(nocountry, "w", encoding="utf-8", newline="") as fh:
            fh.write("name,state\nA,VA\nB,IA\n")
        t2, us2 = row_counts(nocountry)
        check("no country column yields no US count", t2 == 2 and us2 is None)

        check("hash is stable", content_hash(multi) == content_hash(multi))
        check("days_since counts forward",
              days_since("2026-08-20", "2026-08-26") == 6)
        check("days_since tolerates a missing date",
              days_since(None, "2026-08-26") is None)

        cfg = {"sources": [
            {"source_id": "s0", "label": "Planned source", "file": None,
             "vintage_status": "undeclared",
             "acquisition": {"status": "needs_manual_pin"},
             "refresh": {"cadence": "unknown", "pipeline": False}},
            {"source_id": "s1", "label": "Present source", "file": "multi.csv",
             "vintage_status": "undeclared",
             "refresh": {"cadence": "unknown", "pipeline": False}},
            {"source_id": "s2", "label": "Declared source", "file": "nocountry.csv",
             "upstream_vintage": "2026-01", "vintage_status": "declared",
             "refresh": {"cadence": "monthly", "pipeline": True}},
            {"source_id": "s3", "label": "Missing file", "file": "absent.csv",
             "vintage_status": "undeclared", "refresh": {}},
        ]}
        m = build(cfg, root=tmp, today="2026-08-26")
        by = {e["source_id"]: e for e in m["sources"]}
        check("a planned source is declared, not skipped",
              by["s0"]["present"] is False and by["s0"]["rows"] is None
              and by["s0"]["file"] is None)
        check("pinning work is counted",
              m["totals"]["sources_awaiting_a_manual_pin"] == 1)
        check("missing file reports absent, not zero rows",
              by["s3"]["present"] is False and by["s3"]["rows"] is None)
        check("totals count only present rows", m["totals"]["rows"] == 5)
        check("pipeline count is honest",
              m["totals"]["sources_with_a_pipeline"] == 1)
        check("declared vintage count is honest",
              m["totals"]["sources_with_a_declared_vintage"] == 1)
        check("vintage is never inferred from the repository",
              by["s1"]["upstream_vintage"] is None)

        # The change date has to survive a shallow checkout, which is what CI
        # uses. Git there answers with the tip commit whatever the file, so
        # the committed manifest is the memory and the content hash is what
        # decides whether that memory still applies.
        manifest_path = os.path.join(tmp, "data", "facility_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump({"sources": [{
                "source_id": "s1",
                "sha256_12": by["s1"]["sha256_12"],
                "repo_last_changed": "2026-01-15",
            }]}, fh)
        again = build(cfg, root=tmp, today="2026-08-26")
        by2 = {e["source_id"]: e for e in again["sources"]}
        check("an unchanged file keeps its recorded change date",
              by2["s1"]["repo_last_changed"] == "2026-01-15")

        with open(multi, "a", encoding="utf-8", newline="") as fh:
            fh.write("D,United States,new row\n")
        after = build(cfg, root=tmp, today="2026-08-26")
        by3 = {e["source_id"]: e for e in after["sources"]}
        check("a changed file takes today's date",
              by3["s1"]["repo_last_changed"] == "2026-08-26")
        check("and its hash moves with it",
              by3["s1"]["sha256_12"] != by2["s1"]["sha256_12"])

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    with open(SOURCES, encoding="utf-8") as fh:
        config = json.load(fh)
    manifest = build(config)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print(f"wrote {os.path.relpath(OUT_JSON, HERE)}")
    for e in manifest["sources"]:
        rows = e["rows"] if e["rows"] is not None else "absent"
        print(f"  {e['source_id']}: rows={rows} "
              f"repo_last_changed={e['repo_last_changed']} "
              f"vintage={e['vintage_status']} "
              f"pipeline={bool(e['refresh'].get('pipeline'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
