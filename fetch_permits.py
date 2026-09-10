"""
fetch_permits.py — pull data-center permit records from public open-data
portals (runs in GitHub Actions, where network access is unrestricted).

Writes CANDIDATE files for human review — never directly into
data/baseline_dated_external.csv. The review step is deliberate: a fetched
record enters the dated baseline only after Price confirms the mapping and
runs permit_ingest.py with a config.

Adapters:
  arcgis   — ArcGIS REST MapServer/FeatureServer layer query (JSON)
  socrata  — Socrata SODA resource endpoint (JSON)

Per-source JSON config:
  {
    "adapter": "arcgis",
    "source": "loudoun_lola",
    "url": "https://logis.loudoun.gov/gis/rest/services/Projects/LOLA_DATA/MapServer/0/query",
    "where": "UPPER(PlanName) LIKE '%DATA%' OR UPPER(PlanDescription) LIKE '%DATA CENTER%'",
    "out_fields": ["PlanNumber","PlanName","PlanApplicationDate","PlanType",
                   "PlanStatus","PlanDescription"],
    "date_fields": ["PlanApplicationDate"],       # epoch-ms -> ISO conversion
    "page_size": 1000
  }
  {
    "adapter": "socrata",
    "source": "example_county_permits",
    "url": "https://data.example.gov/resource/xxxx-yyyy.json",
    "where": "upper(description) like '%DATA CENTER%'",
    "page_size": 1000
  }

Output: data/permit_candidates_<source>.csv (raw portal columns, dates
normalized). Next step is manual: inspect, write a permit_ingest column map,
run permit_ingest.py.

No scorekeeping vocabulary is introduced; records are raw portal data.

Usage:
  python3 fetch_permits.py --config configs/loudoun_lola.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "hawthorn-baseline/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def epoch_ms_to_iso(v):
    try:
        return datetime.fromtimestamp(float(v) / 1000.0, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return v


def fetch_arcgis(cfg):
    rows, offset = [], 0
    page = int(cfg.get("page_size", 1000))
    date_fields = set(cfg.get("date_fields", []))
    while True:
        params = {
            "where": cfg.get("where", "1=1"),
            "outFields": ",".join(cfg.get("out_fields", ["*"])),
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page,
        }
        url = cfg["url"] + "?" + urllib.parse.urlencode(params)
        data = http_json(url)
        if "error" in data:
            raise RuntimeError(f"arcgis error: {data['error']}")
        feats = data.get("features", [])
        for f in feats:
            attrs = f.get("attributes", {})
            for df in date_fields:
                if df in attrs:
                    attrs[df] = epoch_ms_to_iso(attrs[df])
            rows.append(attrs)
        if len(feats) < page:
            break
        offset += page
    return rows


def fetch_socrata(cfg):
    rows, offset = [], 0
    page = int(cfg.get("page_size", 1000))
    while True:
        params = {"$limit": page, "$offset": offset}
        if cfg.get("where"):
            params["$where"] = cfg["where"]
        url = cfg["url"] + "?" + urllib.parse.urlencode(params)
        data = http_json(url)
        if not isinstance(data, list):
            raise RuntimeError(f"socrata unexpected response: {str(data)[:200]}")
        rows.extend(data)
        if len(data) < page:
            break
        offset += page
    return rows


WHERE_IDENT = re.compile(r"\b([a-z_][a-z0-9_]*)\b")

# SoQL words that appear in a $where but are not column names. Anything left
# after these is treated as a column reference.
SOQL_WORDS = frozenset({
    "and", "or", "not", "like", "in", "is", "null", "between", "true", "false",
    "upper", "lower", "trim", "length", "starts_with", "contains", "within_box",
    "within_circle", "date_trunc_y", "date_trunc_ym", "date_trunc_ymd", "case",
})


def where_columns(where: str) -> set:
    """Column names a $where clause references.

    Deliberately blunt: every bare identifier that is not a known SoQL word,
    with quoted string literals stripped first so a value like 'DATA CENTER'
    cannot be mistaken for a column. Over-reporting a column is safe -- the
    caller only ever compares against a schema it actually has -- while
    under-reporting would put back the failure this exists to catch.
    """
    if not where:
        return set()
    stripped = re.sub(r"'[^']*'", " ", str(where))
    return {m for m in WHERE_IDENT.findall(stripped.lower())
            if m not in SOQL_WORDS}


def unknown_where_columns(where: str, columns) -> list:
    """Columns the $where names that the dataset does not have.

    Empty when the schema is unknown: discovery may not have reported columns,
    and refusing to fetch on no evidence would be worse than trying.
    """
    known = {str(c).lower() for c in (columns or [])}
    if not known:
        return []
    return sorted(where_columns(where) - known)


ADAPTERS = {"arcgis": fetch_arcgis, "socrata": fetch_socrata}


def fetch_tabular(cfg):
    """Generic tabular-file adapter (added 2026-08-21): CSV or XLSX served at
    a plain URL, for agencies that publish file drops rather than APIs (state
    spreadsheets, RTO interconnection-queue reports). Keeps every column;
    optional row filter is a case-insensitive substring match against the
    concatenated row, mirroring the WHERE-style narrowing of the other
    adapters without pretending to be SQL.

    Config keys: url (required), format ("csv" default, or "xlsx"),
    sheet (xlsx only; name or 0-based index, default 0),
    contains (optional list of substrings; a row is kept when ANY matches),
    date_fields (epoch-ms or Excel-serial values converted to ISO dates).
    """
    fmt = (cfg.get("format") or "csv").lower()
    contains = [normalize_needle(s) for s in (cfg.get("contains") or [])]
    date_fields = set(cfg.get("date_fields", []))

    if fmt == "csv":
        req = urllib.request.Request(
            cfg["url"], headers={"User-Agent": "hawthorn-baseline/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8-sig", "replace")
        rows = list(csv.DictReader(text.splitlines()))
    elif fmt == "xlsx":
        try:
            import pandas as pd  # available in the Actions environment
        except ImportError as e:
            raise RuntimeError("xlsx format needs pandas in the runner") from e
        sheet = cfg.get("sheet", 0)
        df = pd.read_excel(cfg["url"], sheet_name=sheet, dtype=str)
        df = df.fillna("")
        rows = df.to_dict(orient="records")
    else:
        raise RuntimeError(f"tabular format {fmt!r} not supported")

    out = []
    for r in rows:
        if contains:
            hay = normalize_needle(" ".join(str(v) for v in r.values()))
            if not any(n in hay for n in contains):
                continue
        for df_field in date_fields:
            if df_field in r:
                r[df_field] = coerce_date(r[df_field])
        out.append(r)
    return out


def normalize_needle(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def coerce_date(v):
    """Epoch-ms, Excel serial, or already-a-date -> ISO where possible."""
    s = str(v or "").strip()
    if not s:
        return s
    if re.match(r"^\d{4}-\d{2}(-\d{2})?$", s):
        return s
    try:
        f = float(s)
    except ValueError:
        return s
    if f > 10**11:                      # epoch milliseconds
        return epoch_ms_to_iso(f)
    if 20000 < f < 80000:               # Excel serial (1954..2118)
        from datetime import date, timedelta
        return (date(1899, 12, 30) + timedelta(days=int(f))).isoformat()
    return s


ADAPTERS["tabular"] = fetch_tabular


def list_sources() -> list[str]:
    """Config filenames under configs/ that register a fetchable source,
    i.e. carry an "adapter" key. Added 2026-08-21 so the scheduled workflow
    can iterate every registered source instead of hardcoding one: adding a
    jurisdiction is then a config drop, never a workflow edit. Non-source
    JSON in configs/ (frame registries, ingest column maps) has no adapter
    key and is skipped; unparseable files are skipped rather than fatal so
    one bad file cannot take down the whole scheduled run."""
    out = []
    cfg_dir = os.path.join(ROOT, "configs")
    for name in sorted(os.listdir(cfg_dir) if os.path.isdir(cfg_dir) else []):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(cfg_dir, name), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            continue
        if isinstance(cfg, dict) and cfg.get("adapter"):
            out.append(name)
    return out


def selftest() -> int:
    """Offline checks for the $where/schema reconciliation.

    fetch_permits.py had no self-test until 2026-09-09, which is not a
    coincidence: the wa_sepa where clause was registered as an explicit guess
    at column names, and there was nothing that could have checked a guess.
    """
    checks: list[tuple[str, bool]] = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    W = "upper(title) like '%DATA CENTER%' OR upper(description) like '%X%'"

    ck("column references are extracted from a where clause",
       where_columns(W) == {"title", "description"})
    ck("string literals are not mistaken for columns",
       "data" not in where_columns(W) and "center" not in where_columns(W))
    ck("SoQL keywords are not mistaken for columns",
       not ({"upper", "like", "or"} & where_columns(W)))
    ck("an empty where names no columns", where_columns("") == set())
    ck("a where naming one column reports it",
       where_columns("status = 'OPEN'") == {"status"})

    # The wa_sepa case exactly: the guess names title/description, the real
    # dataset has neither.
    real = ["sepa_number", "project_name", "project_description", "county"]
    ck("the wa_sepa guess is caught against a real schema",
       unknown_where_columns(W, real) == ["description", "title"])
    ck("a where matching the schema is accepted",
       unknown_where_columns("upper(project_name) like '%DATA CENTER%'",
                             real) == [])
    ck("column comparison is case-insensitive",
       unknown_where_columns("upper(COUNTY) like '%KING%'", real) == [])

    # Refusing to fetch on no evidence would be worse than trying, so an
    # unknown schema must never block a source that works today.
    ck("no reported schema blocks nothing", unknown_where_columns(W, []) == [])
    ck("a null schema blocks nothing", unknown_where_columns(W, None) == [])
    ck("no where clause is never a mismatch",
       unknown_where_columns("", real) == [])

    # list_sources is the discriminator the acquisition workflow iterates, and
    # probe sources must stay out of it.
    srcs = list_sources()
    ck("list_sources returns adapter-bearing configs only",
       srcs and all(s.endswith(".json") for s in srcs))
    probe_cfgs = []
    cfg_dir = os.path.join(ROOT, "configs")
    for name in sorted(os.listdir(cfg_dir) if os.path.isdir(cfg_dir) else []):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(cfg_dir, name), encoding="utf-8") as fh:
                c = json.load(fh)
        except Exception:
            continue
        if isinstance(c, dict) and (c.get("discovery") or {}).get("kind") == "probe":
            probe_cfgs.append(name)
    ck("probe sources are never enumerated as fetchable",
       not (set(srcs) & set(probe_cfgs)))

    ok = sum(1 for _, c in checks if c)
    for name, cond in checks:
        if not cond:
            print(f"  FAIL {name}")
    print(f"fetch_permits selftest: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "data"))
    ap.add_argument("--list-sources", action="store_true",
                    help="print adapter-bearing configs under configs/, "
                         "one per line, and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.list_sources:
        for name in list_sources():
            print(name)
        return 0
    if not args.config:
        print("ERROR: --config required (or --list-sources)")
        return 1

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    adapter = ADAPTERS.get(cfg.get("adapter"))
    if not adapter:
        print(f"ERROR: unknown adapter {cfg.get('adapter')!r}")
        return 1

    # Self-completing sources (added 2026-08-21): a config may register a
    # tracker whose service URL is not yet known, carrying "url": null plus
    # a "discovery" block for discover_arcgis_layer.py. When the url is
    # null, look for that tool's resolved output and use its query_url.
    # If discovery has not resolved yet, skip cleanly: an unresolved
    # source is a pending registration, not a fetch failure, and must not
    # break the scheduled workflow for sources that do work.
    if not cfg.get("url"):
        # Two discoverers now write this block, and which one ran is a property
        # of the source rather than of this code, so both output paths are
        # checked rather than the ArcGIS one being assumed. A Socrata source
        # registers exactly like an ArcGIS one — "url": null plus a discovery
        # block — and self-completes the same way.
        resolved = None
        for kind in ("arcgis", "socrata"):
            disc_path = os.path.join(
                args.outdir, f"{kind}_discovery_{cfg.get('source')}.json")
            if not os.path.exists(disc_path):
                continue
            try:
                with open(disc_path, encoding="utf-8") as fh:
                    found = (json.load(fh) or {}).get("resolved")
            except Exception:
                found = None
            if found and found.get("query_url"):
                resolved = found
                break
        if resolved and resolved.get("query_url"):
            cfg["url"] = resolved["query_url"]
            print(f"{cfg.get('source')}: url resolved by discovery -> "
                  f"{cfg['url']}")
            # A resolved url is not the same as a usable query. A config
            # registered before anyone could see the portal carries a $where
            # guessing at column names, and Socrata answers a $where naming a
            # column it does not have with a 400 -- which is how wa_sepa took
            # the whole weekly acquisition job red on 2026-09-08 while its
            # discovery had in fact worked perfectly.
            #
            # Now that discovery records the dataset's columns, that guess is
            # checkable before the request. A mismatch is the same class of
            # thing as unresolved discovery above -- a registration still
            # pending, not a source that broke -- so it skips cleanly for the
            # same reason: it must not fail the scheduled run for the sources
            # that do work. The message carries the real column names, so the
            # fix is one edit to the config.
            missing = unknown_where_columns(cfg.get("where"),
                                            resolved.get("columns"))
            if missing:
                print(f"{cfg.get('source')}: the configured where clause "
                      f"names {len(missing)} column(s) this dataset does not "
                      f"have ({', '.join(missing)}); skipping fetch rather "
                      f"than sending a query the portal will reject.")
                print(f"{cfg.get('source')}: available columns are "
                      f"{', '.join(resolved['columns'])}")
                print(f"{cfg.get('source')}: edit \"where\" in "
                      f"{args.config} to use them.")
                return 0
        else:
            tool = ("discover_socrata_dataset.py"
                    if (cfg.get("discovery") or {}).get("kind") == "socrata"
                    else "discover_arcgis_layer.py")
            print(f"{cfg.get('source')}: no service url yet (discovery "
                  f"unresolved); skipping fetch. Run "
                  f"{tool} --config {args.config} in CI, "
                  f"or pin the url into the config by hand.")
            return 0

    try:
        rows = adapter(cfg)
    except Exception as e:
        print(f"FETCH FAILED ({cfg.get('source')}): {e}")
        return 1

    out = os.path.join(args.outdir, f"permit_candidates_{cfg['source']}.csv")
    if not rows:
        print(f"{cfg['source']}: 0 records matched — nothing written")
        return 0
    cols = sorted({k for r in rows for k in r.keys()})
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"{cfg['source']}: {len(rows)} candidate records -> "
          f"{os.path.relpath(out, ROOT)}")
    print("Review the file, write a permit_ingest column map, then run "
          "permit_ingest.py to fold accepted rows into the dated baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
