"""
fetch_pudl.py — county generation features from PUDL (priority item 4).

CI-side acquisition of county-level electricity generation features for the
county policy model and the cost layer. Source: PUDL's published parquet
outputs (Catalyst Cooperative), which are the maintained free join between
EIA 860/923 and FERC Form 1 identifiers. Data license CC-BY-4.0, attributed
in the output file and in the report.

Output: data/county_pudl_features.csv, keyed on 5-digit FIPS, same shape and
role as data/county_census_features.csv. Reference data only. Additive: no
existing file is read for writing, and no downstream module consumes this
until county_aggregator.py explicitly joins it, which is a separate pass.

Prediction-moment discipline: every emitted feature is an as-of-report-year
county attribute. Operating capacity is a slow-moving stock and is safe
pre-announcement information. Planned capacity is deliberately kept in its
own columns and stamped with its report year, because it is forward-looking
and a model that uses it must respect the year boundary rather than treating
it as timeless.

Schema tolerance is the central design choice here. PUDL renames and
restructures tables between releases, so this module does NOT hardcode a
column layout. It resolves each concept it needs (capacity, county, state,
FIPS, fuel, status, year) through a candidate-name list, verifies the
resolution at runtime, and refuses to write anything if a required concept
cannot be found, printing the actual column list instead. A guessed layout
is worse than no data.

Modes:
  python3 fetch_pudl.py --list                 print the resolved download URL
                                               and exit, no download
  python3 fetch_pudl.py --fetch                download, aggregate, write
  python3 fetch_pudl.py --from-file PATH       aggregate an already-downloaded
                                               parquet, no network
  python3 fetch_pudl.py --validate             check an existing output file
                                               against data/county_aggregate.csv
  python3 fetch_pudl.py --selftest             synthetic-fixture tests, no
                                               network

Requires: pyarrow (parquet reader). Not added to pipeline.yml; only the new
fetch-pudl workflow installs it, so the clean feed takes on no dependency.

Release pinning: --release defaults to a stable versioned release rather than
the nightly build, because a nightly is not reproducible. Pass
--release nightly deliberately if you want the newest data and accept that
the run is not repeatable.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

OUT_PATH = os.path.join(DATA, "county_pudl_features.csv")
OUT_MANIFEST = os.path.join(DATA, "county_pudl_manifest.json")
COUNTY_AGGREGATE = os.path.join(DATA, "county_aggregate.csv")

PUDL_BASE = "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop"
DEFAULT_RELEASE = "v2025.2.0"
DEFAULT_TABLE = "out_eia__yearly_generators"

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Concept resolution
# ---------------------------------------------------------------------------

# Candidate column names per concept, most-preferred first. PUDL has used
# several of these across releases; resolution is checked at runtime and a
# missing required concept aborts the run.
CONCEPTS: dict[str, list[str]] = {
    "fips": ["county_id_fips", "county_fips", "fips_county", "county_id"],
    "county": ["county"],
    "state": ["state", "plant_state", "state_id_fips"],
    "capacity": ["capacity_mw", "summer_capacity_mw", "winter_capacity_mw"],
    "fuel": ["energy_source_code_1", "fuel_type_code_pudl", "energy_source_code",
             "technology_description"],
    "status": ["operational_status", "operational_status_code",
               "operational_status_pudl", "status"],
    "year": ["report_year", "report_date"],
    "plant": ["plant_id_eia", "plant_id_pudl", "plant_name_eia"],
    "retirement_year": ["planned_retirement_date", "planned_retirement_year",
                        "retirement_date"],
}
REQUIRED = ("capacity", "fuel", "year")
# Geography: at least one of fips, or county plus state.
NEED_GEO = "either county_id_fips, or county together with state"


def resolve_concepts(columns) -> tuple[dict, list[str]]:
    """Map concept -> actual column name. Returns (mapping, problems)."""
    cols = list(columns)
    lower = {c.lower(): c for c in cols}
    mapping = {}
    for concept, candidates in CONCEPTS.items():
        for cand in candidates:
            if cand in lower:
                mapping[concept] = lower[cand]
                break
    problems = [f"missing required concept '{c}'" for c in REQUIRED
                if c not in mapping]
    if "fips" not in mapping and not ("county" in mapping and "state" in mapping):
        problems.append(f"missing geography: need {NEED_GEO}")
    return mapping, problems


# ---------------------------------------------------------------------------
# Fuel grouping
# ---------------------------------------------------------------------------

# EIA energy_source_code and fuel_type_code_pudl values folded into the small
# groups the county model can actually use. Anything unrecognized lands in
# "other" and is counted, never silently dropped.
FUEL_GROUPS = {
    "gas": {"ng", "lfg", "obg", "og", "bfg", "pg", "sgc", "sgp", "gas",
            "natural_gas"},
    "coal": {"bit", "sub", "lig", "rc", "wc", "ant", "coal", "syc"},
    "nuclear": {"nuc", "nuclear"},
    "wind": {"wnd", "ws", "wind"},
    "solar": {"sun", "solar"},
    "hydro": {"wat", "hydro", "hps"},
    "oil": {"dfo", "rfo", "jf", "ker", "wo", "pc", "oil", "petroleum"},
    "storage": {"mwh", "battery", "storage"},
}
_FUEL_LOOKUP = {}
for _grp, _codes in FUEL_GROUPS.items():
    for _c in _codes:
        _FUEL_LOOKUP[_c] = _grp
FUEL_ORDER = ["gas", "coal", "nuclear", "wind", "solar", "hydro", "oil",
              "storage", "other"]


def fuel_group(value) -> str:
    v = str(value or "").strip().lower()
    if not v:
        return "other"
    if v in _FUEL_LOOKUP:
        return _FUEL_LOOKUP[v]
    # technology_description style strings, e.g. "Natural Gas Fired Combined
    # Cycle", "Onshore Wind Turbine", "Batteries".
    for grp in ("nuclear", "coal", "solar", "wind", "hydro"):
        if grp in v:
            return grp
    if "natural gas" in v or "gas" in v:
        return "gas"
    if "batter" in v or "storage" in v:
        return "storage"
    if "petroleum" in v or "oil" in v or "diesel" in v:
        return "oil"
    return "other"


# Operational status buckets. PUDL uses both words and single-letter EIA
# codes across releases, so both are handled.
_OPERATING = {"existing", "operating", "op", "sb", "on", "ol", "os", "sc"}
_PLANNED = {"proposed", "planned", "p", "l", "t", "u", "v", "tsc",
            "construction", "under construction"}
_RETIRED = {"retired", "re", "cn", "ip", "out_of_service"}


def status_bucket(value) -> str:
    v = str(value or "").strip().lower().replace("-", "_")
    if v in _OPERATING:
        return "operating"
    if v in _PLANNED:
        return "planned"
    if v in _RETIRED:
        return "retired"
    if "propos" in v or "construc" in v or "plan" in v:
        return "planned"
    if "retire" in v or "cancel" in v:
        return "retired"
    if "exist" in v or "operat" in v or "standby" in v:
        return "operating"
    return "unknown"


# ---------------------------------------------------------------------------
# FIPS normalization
# ---------------------------------------------------------------------------

_STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56", "PR": "72",
}


def norm_fips(value) -> str:
    """Return a 5-digit FIPS string, or '' if the value cannot be one."""
    v = str(value or "").strip()
    if not v or v.lower() in {"nan", "none", "<na>", "null"}:
        return ""
    v = v.split(".")[0]
    if not v.isdigit():
        return ""
    if len(v) > 5:
        return ""
    return v.zfill(5)


def norm_county_key(county, state) -> str:
    """Fallback join key when no FIPS column exists: 'ST|countyname'."""
    c = re.sub(r"\s+(county|parish|borough|census area|municipality|city and borough)$",
               "", str(county or "").strip().lower())
    c = re.sub(r"[^a-z0-9 ]", "", c).strip()
    s = str(state or "").strip().upper()
    if len(s) > 2 and s in _STATE_FIPS.values():
        s = next((k for k, v in _STATE_FIPS.items() if v == s), s)
    return f"{s}|{c}" if c and s else ""


def load_fips_by_county_key(path=COUNTY_AGGREGATE) -> dict:
    """Build 'ST|countyname' -> fips from the county frame already in the repo,
    so a PUDL release without a FIPS column can still be joined."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            key = norm_county_key(r.get("county_name", "").split(",")[0],
                                  r.get("state", ""))
            if key:
                out[key] = norm_fips(r.get("fips", ""))
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

FEATURE_COLS = (
    ["fips", "n_plants", "gen_capacity_mw"]
    + [f"gen_capacity_mw_{g}" for g in FUEL_ORDER]
    + ["planned_capacity_mw", "planned_capacity_mw_gas",
       "planned_capacity_mw_renewable", "retiring_capacity_mw",
       "report_year", "pudl_release", "source"]
)


def aggregate(rows, mapping, fips_by_key, report_year=None):
    """Aggregate generator rows to one record per county FIPS.

    rows: iterable of dicts (already column-selected).
    Returns (features_by_fips, stats).
    """
    m = mapping
    stats = {"rows_read": 0, "rows_no_geo": 0, "rows_no_capacity": 0,
             "rows_wrong_year": 0, "status_unknown": 0, "fuel_other": 0,
             "joined_via_fips": 0, "joined_via_county_name": 0,
             "unjoinable_county_names": set()}
    acc = defaultdict(lambda: {"plants": set(), "operating": 0.0,
                               "fuel": defaultdict(float), "planned": 0.0,
                               "planned_gas": 0.0, "planned_renewable": 0.0,
                               "retiring": 0.0, "years": set()})

    for r in rows:
        stats["rows_read"] += 1

        yr = r.get(m["year"])
        yr_s = str(yr or "")[:4]
        year_val = int(yr_s) if yr_s.isdigit() else None
        if report_year is not None and year_val != report_year:
            stats["rows_wrong_year"] += 1
            continue

        fips = ""
        if "fips" in m:
            fips = norm_fips(r.get(m["fips"]))
            if fips:
                stats["joined_via_fips"] += 1
        if not fips and "county" in m and "state" in m:
            key = norm_county_key(r.get(m["county"]), r.get(m["state"]))
            fips = fips_by_key.get(key, "")
            if fips:
                stats["joined_via_county_name"] += 1
            elif key:
                stats["unjoinable_county_names"].add(key)
        if not fips:
            stats["rows_no_geo"] += 1
            continue

        try:
            cap = float(r.get(m["capacity"]) or 0.0)
        except (TypeError, ValueError):
            cap = 0.0
        if cap <= 0:
            stats["rows_no_capacity"] += 1

        grp = fuel_group(r.get(m["fuel"]))
        if grp == "other":
            stats["fuel_other"] += 1
        bucket = status_bucket(r.get(m["status"])) if "status" in m else "operating"
        if bucket == "unknown":
            stats["status_unknown"] += 1

        a = acc[fips]
        if year_val:
            a["years"].add(year_val)
        if "plant" in m and r.get(m["plant"]) is not None:
            a["plants"].add(str(r.get(m["plant"])))
        if bucket in ("operating", "unknown"):
            a["operating"] += cap
            a["fuel"][grp] += cap
        elif bucket == "planned":
            a["planned"] += cap
            if grp == "gas":
                a["planned_gas"] += cap
            elif grp in ("wind", "solar", "hydro", "storage"):
                a["planned_renewable"] += cap
        if "retirement_year" in m and r.get(m["retirement_year"]):
            ry = str(r.get(m["retirement_year"]))[:4]
            if ry.isdigit() and year_val and 0 <= int(ry) - year_val <= 5:
                a["retiring"] += cap

    stats["unjoinable_county_names"] = sorted(stats["unjoinable_county_names"])[:40]
    return acc, stats


def to_feature_rows(acc, release, table):
    out = []
    src = (f"PUDL {release} {table} (EIA 860/923 via Catalyst Cooperative, "
           f"CC-BY-4.0)")
    for fips, a in sorted(acc.items()):
        row = {
            "fips": fips,
            "n_plants": len(a["plants"]),
            "gen_capacity_mw": round(a["operating"], 2),
            "planned_capacity_mw": round(a["planned"], 2),
            "planned_capacity_mw_gas": round(a["planned_gas"], 2),
            "planned_capacity_mw_renewable": round(a["planned_renewable"], 2),
            "retiring_capacity_mw": round(a["retiring"], 2),
            "report_year": max(a["years"]) if a["years"] else "",
            "pudl_release": release,
            "source": src,
        }
        for g in FUEL_ORDER:
            row[f"gen_capacity_mw_{g}"] = round(a["fuel"].get(g, 0.0), 2)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Download and read
# ---------------------------------------------------------------------------

def parquet_url(release: str, table: str) -> str:
    return f"{PUDL_BASE}/{release}/{table}.parquet"


def download(url: str, dest: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "hawthorn-dc-pipeline"})
    print(f"downloading {url}")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as fh:
            total = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
        print(f"  {total / 1e6:.1f} MB -> {dest}")
        return dest
    except urllib.error.HTTPError as exc:
        print(f"ERROR: HTTP {exc.code} for {url}")
        print("If the release or table name has changed, list current objects at "
              f"{PUDL_BASE}/ and pass --release / --table explicitly.")
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"ERROR: network failure for {url}: {exc}")
        sys.exit(1)


def read_parquet_rows(path: str):
    """Yield dicts for the resolved columns only, batch by batch, so a
    multi-gigabyte file never lands in memory at once. Returns
    (row_iterator, mapping, all_columns)."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("ERROR: pyarrow is required to read parquet. "
              "Install with: pip install pyarrow")
        sys.exit(1)

    pf = pq.ParquetFile(path)
    all_cols = list(pf.schema_arrow.names)
    mapping, problems = resolve_concepts(all_cols)
    if problems:
        print("ERROR: could not resolve the PUDL schema. Refusing to write on a "
              "guessed layout.")
        for p in problems:
            print(f"  {p}")
        print("Columns present in this file:")
        for c in all_cols:
            print(f"  {c}")
        sys.exit(1)

    wanted = sorted(set(mapping.values()))
    print("resolved schema:")
    for concept, col in sorted(mapping.items()):
        print(f"  {concept}: {col}")

    def gen():
        for batch in pf.iter_batches(batch_size=100_000, columns=wanted):
            for rec in batch.to_pylist():
                yield rec

    return gen(), mapping, all_cols


# ---------------------------------------------------------------------------
# Write and validate
# ---------------------------------------------------------------------------

def write_features(rows, path=OUT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FEATURE_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in FEATURE_COLS})


def validate(path=OUT_PATH, frame=COUNTY_AGGREGATE) -> int:
    """Check the output against the county frame already in the repo. Reports
    coverage and flags anything structurally wrong. Does not modify files."""
    if not os.path.exists(path):
        print(f"ERROR: {os.path.relpath(path, ROOT)} does not exist; run --fetch first")
        return 1
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    fips = [r["fips"] for r in rows]
    problems = []
    if len(set(fips)) != len(fips):
        problems.append("duplicate FIPS in output")
    if any(len(f) != 5 or not f.isdigit() for f in fips):
        problems.append("non-5-digit FIPS present")

    frame_fips = set()
    if os.path.exists(frame):
        with open(frame, newline="", encoding="utf-8-sig") as fh:
            frame_fips = {norm_fips(r["fips"]) for r in csv.DictReader(fh)}
    unmatched = sorted(set(fips) - frame_fips) if frame_fips else []

    def fnum(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0

    for r in rows:
        total = fnum(r, "gen_capacity_mw")
        parts = sum(fnum(r, f"gen_capacity_mw_{g}") for g in FUEL_ORDER)
        if total > 0 and abs(total - parts) > max(1.0, total * 0.01):
            problems.append(f"fuel columns do not sum to total for {r['fips']}")
            break

    print(f"counties in output: {len(rows)}")
    if frame_fips:
        print(f"coverage of county frame: {len(set(fips) & frame_fips)}/{len(frame_fips)}")
        print(f"FIPS not in county frame: {len(unmatched)}"
              + (f" (first 10: {', '.join(unmatched[:10])})" if unmatched else ""))
    tot = sum(fnum(r, "gen_capacity_mw") for r in rows)
    plan = sum(fnum(r, "planned_capacity_mw") for r in rows)
    print(f"total operating capacity: {tot:,.0f} MW")
    print(f"total planned capacity: {plan:,.0f} MW")
    if problems:
        for p in problems:
            print(f"  PROBLEM {p}")
        return 1
    print("validate: clean")
    return 0


def write_manifest(stats, mapping, release, table, n_counties, path=OUT_MANIFEST):
    payload = {
        "release": release,
        "table": table,
        "resolved_columns": mapping,
        "counties_written": n_counties,
        "stats": {k: v for k, v in stats.items()},
        "attribution": "PUDL data outputs, Catalyst Cooperative, CC-BY-4.0",
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  FAIL {label}")
        else:
            print(f"  pass {label}")

    check(norm_fips("1001") == "01001", "fips zero-pad")
    check(norm_fips("01001.0") == "01001", "fips float form")
    check(norm_fips("nan") == "", "fips nan")
    check(norm_fips("") == "", "fips empty")
    check(norm_fips("123456") == "", "fips too long rejected")

    check(norm_county_key("Autauga County", "AL") == "AL|autauga", "county key")
    check(norm_county_key("St. Louis city", "MO") == "MO|st louis city", "county key punctuation")
    check(norm_county_key("Bethel Census Area", "AK") == "AK|bethel", "census area suffix")

    check(fuel_group("NG") == "gas", "fuel code gas")
    check(fuel_group("BIT") == "coal", "fuel code coal")
    check(fuel_group("Natural Gas Fired Combined Cycle") == "gas", "fuel description gas")
    check(fuel_group("Onshore Wind Turbine") == "wind", "fuel description wind")
    check(fuel_group("Batteries") == "storage", "fuel description storage")
    check(fuel_group("") == "other", "fuel blank falls to other")
    check(fuel_group("Flywheels") == "other", "unrecognized falls to other")

    check(status_bucket("existing") == "operating", "status existing")
    check(status_bucket("OP") == "operating", "status code OP")
    check(status_bucket("proposed") == "planned", "status proposed")
    check(status_bucket("Under Construction") == "planned", "status construction")
    check(status_bucket("retired") == "retired", "status retired")
    check(status_bucket("zzz") == "unknown", "status unknown")

    m, probs = resolve_concepts(["county_id_fips", "capacity_mw",
                                 "energy_source_code_1", "report_year",
                                 "operational_status", "plant_id_eia"])
    check(not probs, "resolution succeeds on a known layout")
    check(m["fips"] == "county_id_fips", "resolution picks fips")

    m2, probs2 = resolve_concepts(["county", "state", "summer_capacity_mw",
                                   "technology_description", "report_date"])
    check(not probs2, "resolution falls back to county plus state")
    check(m2["capacity"] == "summer_capacity_mw", "capacity fallback")

    _, probs3 = resolve_concepts(["capacity_mw", "energy_source_code_1",
                                  "report_year"])
    check(any("geography" in p for p in probs3), "missing geography is a problem")
    _, probs4 = resolve_concepts(["county_id_fips", "report_year"])
    check(len(probs4) >= 2, "missing capacity and fuel are problems")

    # End-to-end aggregation on a synthetic frame.
    mapping = {"fips": "county_id_fips", "capacity": "capacity_mw",
               "fuel": "energy_source_code_1", "year": "report_year",
               "status": "operational_status", "plant": "plant_id_eia",
               "retirement_year": "planned_retirement_date"}
    rows = [
        {"county_id_fips": "01001", "capacity_mw": 100.0, "energy_source_code_1": "NG",
         "report_year": 2024, "operational_status": "existing", "plant_id_eia": 1,
         "planned_retirement_date": None},
        {"county_id_fips": "01001", "capacity_mw": 50.0, "energy_source_code_1": "NG",
         "report_year": 2024, "operational_status": "existing", "plant_id_eia": 1,
         "planned_retirement_date": "2027-01-01"},
        {"county_id_fips": "01001", "capacity_mw": 25.0, "energy_source_code_1": "SUN",
         "report_year": 2024, "operational_status": "proposed", "plant_id_eia": 2,
         "planned_retirement_date": None},
        {"county_id_fips": "1003", "capacity_mw": 200.0, "energy_source_code_1": "BIT",
         "report_year": 2024, "operational_status": "existing", "plant_id_eia": 3,
         "planned_retirement_date": None},
        {"county_id_fips": None, "capacity_mw": 10.0, "energy_source_code_1": "NG",
         "report_year": 2024, "operational_status": "existing", "plant_id_eia": 4,
         "planned_retirement_date": None},
        {"county_id_fips": "01001", "capacity_mw": 999.0, "energy_source_code_1": "NG",
         "report_year": 2019, "operational_status": "existing", "plant_id_eia": 9,
         "planned_retirement_date": None},
    ]
    acc, stats = aggregate(rows, mapping, {}, report_year=2024)
    feats = {r["fips"]: r for r in to_feature_rows(acc, "vtest", "ttest")}
    check(set(feats) == {"01001", "01003"}, "counties aggregated and padded")
    check(feats["01001"]["gen_capacity_mw"] == 150.0, "operating capacity summed")
    check(feats["01001"]["gen_capacity_mw_gas"] == 150.0, "gas capacity")
    check(feats["01001"]["planned_capacity_mw"] == 25.0, "planned kept separate")
    check(feats["01001"]["planned_capacity_mw_renewable"] == 25.0, "planned renewable")
    check(feats["01001"]["retiring_capacity_mw"] == 50.0, "retiring within 5 years")
    check(feats["01001"]["n_plants"] == 2, "distinct plants counted")
    check(stats["rows_no_geo"] == 1, "missing geography counted not crashed")
    check(stats["rows_wrong_year"] == 1, "off-year row excluded")
    check(feats["01003"]["gen_capacity_mw_coal"] == 200.0, "coal capacity")

    # County-name fallback path.
    mapping2 = {"county": "county", "state": "state", "capacity": "capacity_mw",
                "fuel": "energy_source_code_1", "year": "report_year"}
    rows2 = [{"county": "Autauga County", "state": "AL", "capacity_mw": 10.0,
              "energy_source_code_1": "NG", "report_year": 2024},
             {"county": "Nowhere County", "state": "ZZ", "capacity_mw": 5.0,
              "energy_source_code_1": "NG", "report_year": 2024}]
    acc2, stats2 = aggregate(rows2, mapping2, {"AL|autauga": "01001"},
                             report_year=2024)
    f2 = {r["fips"]: r for r in to_feature_rows(acc2, "vtest", "ttest")}
    check(list(f2) == ["01001"], "county-name join resolves")
    check(stats2["joined_via_county_name"] == 1, "county-name joins counted")
    check(stats2["rows_no_geo"] == 1, "unjoinable county counted")
    check(stats2["unjoinable_county_names"] == ["ZZ|nowhere"],
          "unjoinable names reported for review")

    # Fuel columns must sum to the total, which is what --validate checks.
    tot = feats["01001"]["gen_capacity_mw"]
    parts = sum(feats["01001"][f"gen_capacity_mw_{g}"] for g in FUEL_ORDER)
    check(abs(tot - parts) < 1e-6, "fuel columns sum to total")

    # Round trip through the real parquet reader if pyarrow is present.
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import tempfile
        tbl = pa.table({
            "county_id_fips": ["01001", "01001", "01003"],
            "capacity_mw": [100.0, 25.0, 200.0],
            "energy_source_code_1": ["NG", "SUN", "BIT"],
            "report_year": [2024, 2024, 2024],
            "operational_status": ["existing", "proposed", "existing"],
            "plant_id_eia": [1, 2, 3],
            "planned_retirement_date": [None, None, None],
        })
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "fixture.parquet")
            pq.write_table(tbl, p)
            it, mp, cols = read_parquet_rows(p)
            acc3, _ = aggregate(list(it), mp, {}, report_year=2024)
            f3 = {r["fips"]: r for r in to_feature_rows(acc3, "vtest", "ttest")}
            check(f3["01001"]["gen_capacity_mw"] == 100.0, "parquet round trip")
            check(f3["01003"]["gen_capacity_mw_coal"] == 200.0, "parquet coal")
    except ImportError:
        print("  skip parquet round trip (pyarrow not installed)")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_aggregate(path, release, table, report_year):
    rows, mapping, all_cols = read_parquet_rows(path)
    fips_by_key = load_fips_by_county_key()
    if "fips" not in mapping and not fips_by_key:
        print("ERROR: this release has no FIPS column and "
              "data/county_aggregate.csv is unavailable for a county-name join.")
        return 1
    acc, stats = aggregate(rows, mapping, fips_by_key, report_year=report_year)
    feats = to_feature_rows(acc, release, table)
    if not feats:
        print("ERROR: aggregation produced zero counties; refusing to write.")
        print(f"  rows read: {stats['rows_read']}, no geography: "
              f"{stats['rows_no_geo']}, off-year: {stats['rows_wrong_year']}")
        return 1
    write_features(feats)
    write_manifest(stats, mapping, release, table, len(feats))
    print(f"wrote {len(feats)} counties -> {os.path.relpath(OUT_PATH, ROOT)}")
    print(f"  rows read {stats['rows_read']}, joined via FIPS "
          f"{stats['joined_via_fips']}, via county name "
          f"{stats['joined_via_county_name']}, no geography {stats['rows_no_geo']}")
    if stats["unjoinable_county_names"]:
        print(f"  unjoinable county names (first few): "
              f"{', '.join(stats['unjoinable_county_names'][:8])}")
    if stats["status_unknown"]:
        print(f"  note: {stats['status_unknown']} rows had an unrecognized "
              f"operational status and were counted as operating")
    rc = validate()
    for p in (OUT_PATH, OUT_MANIFEST):
        hits = sum(1 for line in open(p, encoding="utf-8") if LEAK_RE.search(line))
        print(f"leak audit {os.path.relpath(p, ROOT)}: "
              + ("clean" if not hits else f"{hits} hits, inspect"))
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download and aggregate")
    ap.add_argument("--from-file", metavar="PATH",
                    help="aggregate a local parquet, no network")
    ap.add_argument("--list", action="store_true",
                    help="print the resolved URL and exit")
    ap.add_argument("--validate", action="store_true",
                    help="check the existing output file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--release", default=DEFAULT_RELEASE,
                    help=f"PUDL release (default {DEFAULT_RELEASE}; "
                         f"'nightly' is not reproducible)")
    ap.add_argument("--table", default=DEFAULT_TABLE,
                    help=f"PUDL table name (default {DEFAULT_TABLE})")
    ap.add_argument("--report-year", type=int, default=None,
                    help="keep only this report year (default: keep all, which "
                         "double counts if the table is multi-year)")
    ap.add_argument("--keep-download", action="store_true",
                    help="do not delete the downloaded parquet")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.list:
        print(parquet_url(args.release, args.table))
        return 0
    if args.validate:
        return validate()
    if args.from_file:
        if not os.path.exists(args.from_file):
            print(f"ERROR: {args.from_file} not found")
            return 1
        return run_aggregate(args.from_file, args.release, args.table,
                             args.report_year)
    if args.fetch:
        if args.report_year is None:
            print("note: --report-year not set. If the table is multi-year, "
                  "capacity will be summed across years. Pass --report-year to "
                  "pin a single vintage.")
        dest = os.path.join(DATA, f"_pudl_{args.table}.parquet")
        download(parquet_url(args.release, args.table), dest)
        try:
            return run_aggregate(dest, args.release, args.table, args.report_year)
        finally:
            if not args.keep_download and os.path.exists(dest):
                os.remove(dest)
                print(f"removed {os.path.relpath(dest, ROOT)}")

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
