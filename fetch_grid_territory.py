#!/usr/bin/env python3
"""
fetch_grid_territory.py -- which utilities and grid regions serve each county.

Reference-data acquisition for the proposals intelligence layer. The single
most decision-relevant fact about a proposed data center after its size is
who has to serve it: which utility files the large-load tariff, which RTO
runs the interconnection queue and the capacity auction that the load moves.
The repository had no grid geography at all.

Source: EIA Form 861 service territories and balancing-authority
associations, as published by PUDL (Catalyst Cooperative) in the same parquet
release fetch_pudl.py already reads. Data license CC-BY-4.0, attributed in
the manifest. EIA-861 lists, per utility and state, the counties the utility
serves, and per utility and state, the balancing authority it sits in. The
join gives county -> utilities -> balancing authorities -> region.

Schema tolerance follows fetch_pudl.py exactly: PUDL renames tables and
columns between releases, so each table and each concept is resolved through
a candidate list and verified at runtime. A required concept that cannot be
found aborts the run and prints the columns actually present. A guessed
layout is worse than no data.

Output
  data/county_grid_territory.csv       one row per county FIPS
  data/county_grid_territory_manifest.json

The file is reference data. proposal_enrichment.py prefers it over the
hand-written fallback in configs/grid_iso_crosswalk.json and records which
one it used on every project.

Modes
  python fetch_grid_territory.py --fetch       download, join, write
  python fetch_grid_territory.py --from-dir D  join already-downloaded parquet
  python fetch_grid_territory.py --selftest    synthetic rows, no network

Requires pyarrow for --fetch / --from-dir only. The selftest is stdlib.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import urllib.request
from collections import Counter, defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(ROOT, "data", "county_grid_territory.csv")
OUT_MANIFEST = os.path.join(ROOT, "data", "county_grid_territory_manifest.json")

PUDL_BASE = "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop"
DEFAULT_RELEASE = "v2025.2.0"

TABLES = {
    "territory": ["core_eia861__yearly_service_territory",
                  "out_eia861__yearly_service_territory",
                  "service_territory_eia861"],
    "ba_assn": ["core_eia861__assn_balancing_authority",
                "core_eia861__yearly_balancing_authority_assn",
                "balancing_authority_assn_eia861"],
    "ba": ["core_eia861__yearly_balancing_authority",
           "balancing_authority_eia861"],
}

CONCEPTS = {
    "territory": {
        "utility_id": ["utility_id_eia"],
        "utility_name": ["utility_name_eia", "utility_name"],
        "state": ["state"],
        "county": ["county"],
        "fips": ["county_id_fips", "county_fips"],
        "year": ["report_date", "report_year"],
    },
    "ba_assn": {
        "ba_id": ["balancing_authority_id_eia"],
        "utility_id": ["utility_id_eia"],
        "state": ["state"],
        "year": ["report_date", "report_year"],
    },
    "ba": {
        "ba_id": ["balancing_authority_id_eia"],
        "ba_code": ["balancing_authority_code_eia", "balancing_authority_code"],
        "ba_name": ["balancing_authority_name_eia", "balancing_authority_name"],
        "year": ["report_date", "report_year"],
    },
}
REQUIRED = {
    "territory": ("utility_id", "state", "fips", "year"),
    "ba_assn": ("ba_id", "utility_id", "state", "year"),
    "ba": ("ba_id", "ba_code"),
}

# Balancing-authority code -> region. Anything not listed is non-RTO and is
# placed Southeast or West by state; the code itself is always kept.
BA_REGION = {"PJM": "PJM", "MISO": "MISO", "SWPP": "SPP", "ERCO": "ERCOT",
             "NYIS": "NYISO", "ISNE": "ISO-NE", "CISO": "CAISO"}
WEST_STATES = {"AZ", "CA", "CO", "ID", "MT", "NV", "NM", "OR", "UT", "WA", "WY", "TX"}


def region_for(ba_code: str, state: str) -> str:
    code = (ba_code or "").strip().upper()
    if code in BA_REGION:
        return BA_REGION[code]
    if not code:
        return ""
    return "West (non-RTO)" if state in WEST_STATES else "Southeast (non-RTO)"


def resolve(table: str, columns) -> tuple[dict, list]:
    lower = {c.lower(): c for c in columns}
    m = {}
    for concept, cands in CONCEPTS[table].items():
        for c in cands:
            if c in lower:
                m[concept] = lower[c]
                break
    problems = [f"{table}: missing required concept '{c}'"
                for c in REQUIRED[table] if c not in m]
    return m, problems


def _year(v) -> int | None:
    s = str(v or "")[:4]
    return int(s) if s.isdigit() else None


def _latest(rows, ycol):
    ys = [y for y in (_year(r.get(ycol)) for r in rows) if y]
    if not ys:
        return rows, None
    y = max(ys)
    return [r for r in rows if _year(r.get(ycol)) == y], y


def build(territory, ba_assn, ba, report_year=None):
    """Join three row lists (dicts) into one row per county FIPS."""
    mt, p1 = resolve("territory", territory[0].keys() if territory else [])
    ma, p2 = resolve("ba_assn", ba_assn[0].keys() if ba_assn else [])
    mb, p3 = resolve("ba", ba[0].keys() if ba else [])
    problems = p1 + p2 + p3
    if problems:
        cols = {"territory": sorted(territory[0]) if territory else [],
                "ba_assn": sorted(ba_assn[0]) if ba_assn else [],
                "ba": sorted(ba[0]) if ba else []}
        raise SystemExit("fetch_grid_territory: schema did not resolve.\n  "
                         + "\n  ".join(problems)
                         + "\nColumns present:\n" + json.dumps(cols, indent=1))
    if report_year:
        pick = lambda rows, col: [r for r in rows if _year(r.get(col)) == report_year]
        territory, ba_assn = pick(territory, mt["year"]), pick(ba_assn, ma["year"])
        y = report_year
    else:
        territory, y = _latest(territory, mt["year"])
        ba_assn, _ = _latest(ba_assn, ma["year"])
    code_of = {}
    for r in ba:
        code_of[str(r[mb["ba_id"]])] = (r.get(mb["ba_code"]) or "",
                                         r.get(mb.get("ba_name", ""), "") or "")
    bas_of = defaultdict(set)          # (utility, state) -> {ba code}
    for r in ba_assn:
        code = code_of.get(str(r[ma["ba_id"]]), ("", ""))[0]
        if code:
            bas_of[(str(r[ma["utility_id"]]), r[ma["state"]])].add(code)
    counties = defaultdict(lambda: {"utilities": {}, "bas": Counter(), "regions": Counter(),
                                    "state": "", "county": ""})
    for r in territory:
        fips = str(r.get(mt["fips"]) or "").strip()
        if not fips or not fips.isdigit():
            continue
        fips = fips.zfill(5)
        c = counties[fips]
        st = r[mt["state"]]
        c["state"], c["county"] = st, r.get(mt.get("county", ""), "") or c["county"]
        uid = str(r[mt["utility_id"]])
        c["utilities"][uid] = r.get(mt.get("utility_name", ""), "") or uid
        for code in bas_of.get((uid, st), ()):
            c["bas"][code] += 1
            c["regions"][region_for(code, st)] += 1
    out = []
    for fips, c in sorted(counties.items()):
        regions = [k for k, _ in c["regions"].most_common() if k]
        out.append({
            "fips": fips, "state": c["state"], "county": c["county"],
            "report_year": y or "",
            "n_utilities": len(c["utilities"]),
            "utilities": "; ".join(sorted(c["utilities"].values())[:8]),
            "ba_codes": "; ".join(k for k, _ in c["bas"].most_common()),
            "regions": "; ".join(regions),
            "primary_region": regions[0] if regions else "",
            "region_split": "yes" if len(regions) > 1 else "no",
        })
    return out, y


FIELDS = ["fips", "state", "county", "report_year", "n_utilities", "utilities",
          "ba_codes", "regions", "primary_region", "region_split"]


def write(rows, year, release, tables):
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with open(OUT_MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"generated": date.today().isoformat(), "source": "EIA Form 861 via PUDL",
                   "publisher": "Catalyst Cooperative", "license": "CC-BY-4.0",
                   "release": release, "tables": tables, "report_year": year,
                   "counties": len(rows),
                   "split_counties": sum(1 for r in rows if r["region_split"] == "yes")},
                  fh, indent=1)
        fh.write("\n")


def _read_parquet(path):
    import pyarrow.parquet as pq                     # CI-only dependency
    return pq.read_table(path).to_pylist()


def fetch(release, workdir):
    got = {}
    for key, cands in TABLES.items():
        last = None
        for t in cands:
            url = f"{PUDL_BASE}/{release}/{t}.parquet"
            dest = os.path.join(workdir, f"{t}.parquet")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "hawthorn-dc-tracker/1.0"})
                with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as fh:
                    fh.write(r.read())
                got[key] = (t, dest)
                break
            except Exception as e:                   # try the next candidate name
                last = e
        if key not in got:
            raise SystemExit(f"fetch_grid_territory: no candidate table for {key} "
                             f"in release {release}: tried {cands} ({last})")
    return got


def selftest():
    checks = []

    def check(label, ok):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    terr = [
        {"utility_id_eia": 1, "utility_name_eia": "ComEd", "state": "IL", "county": "Cook",
         "county_id_fips": "17031", "report_date": "2023-01-01"},
        {"utility_id_eia": 2, "utility_name_eia": "Ameren Illinois", "state": "IL",
         "county": "Sangamon", "county_id_fips": "17167", "report_date": "2023-01-01"},
        {"utility_id_eia": 3, "utility_name_eia": "Co-op", "state": "IL",
         "county": "Cook", "county_id_fips": "17031", "report_date": "2023-01-01"},
        {"utility_id_eia": 4, "utility_name_eia": "TVA dist", "state": "TN",
         "county": "Shelby", "county_id_fips": "47157", "report_date": "2023-01-01"},
        {"utility_id_eia": 1, "utility_name_eia": "ComEd", "state": "IL", "county": "Cook",
         "county_id_fips": "17031", "report_date": "2019-01-01"},
    ]
    assn = [{"balancing_authority_id_eia": 10, "utility_id_eia": 1, "state": "IL", "report_date": "2023-01-01"},
            {"balancing_authority_id_eia": 11, "utility_id_eia": 2, "state": "IL", "report_date": "2023-01-01"},
            {"balancing_authority_id_eia": 11, "utility_id_eia": 3, "state": "IL", "report_date": "2023-01-01"},
            {"balancing_authority_id_eia": 12, "utility_id_eia": 4, "state": "TN", "report_date": "2023-01-01"}]
    bas = [{"balancing_authority_id_eia": 10, "balancing_authority_code_eia": "PJM"},
           {"balancing_authority_id_eia": 11, "balancing_authority_code_eia": "MISO"},
           {"balancing_authority_id_eia": 12, "balancing_authority_code_eia": "TVA"}]
    rows, y = build(terr, assn, bas)
    by = {r["fips"]: r for r in rows}
    check("the latest report year is used", y == 2023)
    check("a county served in two RTOs is marked split", by["17031"]["region_split"] == "yes")
    check("its regions are both named", set(by["17031"]["regions"].split("; ")) == {"PJM", "MISO"})
    check("a single-RTO county is not split", by["17167"]["primary_region"] == "MISO"
          and by["17167"]["region_split"] == "no")
    check("a non-RTO BA in the Southeast is labeled Southeast with its code kept",
          by["47157"]["primary_region"] == "Southeast (non-RTO)" and by["47157"]["ba_codes"] == "TVA")
    check("utilities are named", "ComEd" in by["17031"]["utilities"])
    check("region_for: SWPP is SPP", region_for("SWPP", "KS") == "SPP")
    check("region_for: a western non-RTO BA is West", region_for("PACE", "UT") == "West (non-RTO)")
    try:
        build([{"foo": 1}], assn, bas)
        check("an unresolvable schema aborts", False)
    except SystemExit as e:
        check("an unresolvable schema aborts and names the columns", "Columns present" in str(e))
    n = sum(checks)
    print(f"\n{n}/{len(checks)} checks passed")
    return 0 if n == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="EIA-861 county grid territory via PUDL")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--from-dir")
    ap.add_argument("--release", default=DEFAULT_RELEASE)
    ap.add_argument("--report-year", type=int)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.fetch:
        with tempfile.TemporaryDirectory() as td:
            got = fetch(a.release, td)
            data = {k: _read_parquet(p) for k, (_, p) in got.items()}
            names = {k: t for k, (t, _) in got.items()}
    elif a.from_dir:
        data, names = {}, {}
        for key, cands in TABLES.items():
            for t in cands:
                p = os.path.join(a.from_dir, f"{t}.parquet")
                if os.path.exists(p):
                    data[key], names[key] = _read_parquet(p), t
                    break
            if key not in data:
                raise SystemExit(f"no parquet for {key} in {a.from_dir}")
    else:
        ap.print_help()
        return
    rows, y = build(data["territory"], data["ba_assn"], data["ba"], a.report_year)
    write(rows, y, a.release, names)
    print(f"fetch_grid_territory: {len(rows)} counties, report year {y}, "
          f"{sum(1 for r in rows if r['region_split'] == 'yes')} split -> {OUT_CSV}")


if __name__ == "__main__":
    main()
