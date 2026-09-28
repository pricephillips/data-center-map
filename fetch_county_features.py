"""
fetch_county_features.py

CI-side acquisition of county profile variables from outside the platform's
own layers, for the adaptive feature search. Every output is a fips-keyed CSV
under data/features/ that configs/feature_plugins.json registers, so
county_features.py adds the columns to the candidate pool with no code change
and feature_search.py ranks them with everything else.

Sources (settings in configs/feature_sources.json):

  grid_generation  PUDL out_eia__yearly_generators (EIA-860). Operating,
                   planned and retiring nameplate capacity per county for the
                   latest report year, fuel shares. EIA-860 is a census of
                   plants of 1 MW and up, so a county with no plant is a true
                   zero, not a missing value.
  retail_price     PUDL core_eia861__yearly_sales and
                   core_eia861__yearly_service_territory (EIA-861). Commercial
                   and industrial average price (cents/kWh) of the bundled
                   utilities serving the county, customer weighted, and the
                   state average across all service types. Counties served
                   only by delivery-only utilities (retail choice) carry a
                   blank county price, never a borrowed one.
  water_use        USGS county water use, 2015. Freshwater withdrawals per
                   square mile, groundwater share, irrigation share.
  drought          U.S. Drought Monitor county statistics. Mean percent of
                   county area in D1 or worse over the window, and the share
                   of weeks with at least half the county in D2 or worse.
  farmland         USDA NASS Census of Agriculture 2022 (Quick Stats API,
                   free key in the NASS_API_KEY secret). Land in farms and
                   cropland as a share of county land area. Disclosure
                   suppressed values (D) stay blank.

Zoning regime is not built: there is no national county-level source, and
the partial ones would enter the search as detection-biased variables.

Every variable is an attribute of the county, not of any project or
opposition record, so every column is leakage class 'none'.

Failure discipline: sources are independent. A source that fails (network,
schema drift, implausible values, missing secret) writes nothing and is
recorded in data/features/features_manifest.json with the reason. An existing
file from an earlier successful run is left in place. The run exits nonzero
only when every enabled source failed.

Usage
  python fetch_county_features.py                 all enabled sources
  python fetch_county_features.py --only drought,water_use
  python fetch_county_features.py --selftest      synthetic fixtures, no network
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(ROOT, *parts)


SOURCES_CFG = P("configs", "feature_sources.json")
AGG_CSV = P("data", "county_aggregate.csv")
FEAT_DIR = P("data", "features")
OUT_GRID = P("data", "features", "grid_generation.csv")
OUT_PRICE = P("data", "features", "retail_price.csv")
OUT_WATER = P("data", "features", "water_use.csv")
OUT_DROUGHT = P("data", "features", "drought.csv")
OUT_FARM = P("data", "features", "farmland.csv")
OUT_MANIFEST = P("data", "features", "features_manifest.json")

UA = {"User-Agent": "hawthorn-dc-pipeline (county reference data)"}

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56",
}
FIPS_STATE = {v: k for k, v in STATE_FIPS.items()}


class SourceError(Exception):
    """A source could not be built; nothing is written for it."""


# --------------------------------------------------------------------------
# primitives (pure; covered by --selftest)
# --------------------------------------------------------------------------

def fnum(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s.startswith("(") or s.lower() in {"nan", "none", "na", "--", "-"}:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def norm_fips(v) -> str:
    s = str(v or "").strip().split(".")[0]
    if not s.isdigit() or len(s) > 5:
        return ""
    return s.zfill(5)


def rnd(x, k=4):
    return "" if x is None else round(x, k)


def share(num, den):
    if num is None or den is None or den <= 0:
        return None
    return max(0.0, min(1.0, num / den))


def load_frame(path=AGG_CSV) -> dict:
    """fips -> {'state': 'IA', 'land_sqmi': float|None} for the county frame."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            f = norm_fips(r.get("fips"))
            if not f or f.startswith("72"):
                continue
            out[f] = {"state": (r.get("state") or "").strip().upper(),
                      "land_sqmi": fnum(r.get("land_sqmi"))}
    return out


def write_csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])
    os.replace(tmp, path)


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def coverage(rows, cols) -> dict:
    return {c: sum(1 for r in rows if r.get(c) not in ("", None)) for c in cols}


# --------------------------------------------------------------------------
# network
# --------------------------------------------------------------------------

def http_get(url, headers=None, timeout=300, retries=3) -> bytes:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
        time.sleep(3 * (i + 1))
    raise SourceError(f"GET failed for {url.split('key=')[0]}: {last}")


def first_ok(urls, **kw) -> tuple[bytes, str]:
    errs = []
    for u in urls:
        try:
            return http_get(u, **kw), u
        except SourceError as exc:
            errs.append(str(exc))
    raise SourceError("; ".join(errs) or "no URL configured")


def download_to(url, dest):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=900) as resp, open(dest, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise SourceError(f"download failed for {url}: {exc}")


def pudl_parquet(cfg, table, tmpdir) -> str:
    errs = []
    for base in cfg.get("base_urls", []):
        url = f"{base}/{cfg['pudl_release']}/{table}.parquet"
        dest = os.path.join(tmpdir, f"{table}.parquet")
        try:
            print(f"  downloading {url}")
            download_to(url, dest)
            return dest
        except SourceError as exc:
            errs.append(str(exc))
    raise SourceError("; ".join(errs))


def resolve(columns, candidates: dict, required) -> dict:
    lower = {c.lower(): c for c in columns}
    m = {}
    for concept, cands in candidates.items():
        for c in cands:
            if c in lower:
                m[concept] = lower[c]
                break
    missing = [c for c in required if c not in m]
    if missing:
        raise SourceError(f"schema drift: missing {missing}; columns present: "
                          f"{sorted(columns)[:60]}")
    return m


def iter_parquet(path, candidates, required):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    m = resolve(pf.schema_arrow.names, candidates, required)
    cols = sorted(set(m.values()))

    def gen():
        for batch in pf.iter_batches(batch_size=200_000, columns=cols):
            for rec in batch.to_pylist():
                yield {k: rec.get(v) for k, v in m.items()}
    return gen, m


def year_of(v):
    s = str(v or "")[:4]
    return int(s) if s.isdigit() else None


# --------------------------------------------------------------------------
# grid_generation
# --------------------------------------------------------------------------

GEN_COLS = ["fips", "gen_capacity_mw", "gen_fossil_share", "gen_renewable_share",
            "gen_nuclear_mw", "planned_capacity_mw", "planned_renewable_mw",
            "planned_gas_mw", "retiring_capacity_mw", "report_year", "source"]
FOSSIL = {"gas", "coal", "oil"}
RENEW = {"wind", "solar", "hydro"}


def build_grid(rows, frame, report_year, source) -> list:
    """rows: dicts with fips, capacity, fuel, status, retirement (optional),
    already filtered to report_year. Densifies to the full frame: a county
    with no EIA-860 plant is zero capacity."""
    import fetch_pudl as FP
    acc = defaultdict(lambda: defaultdict(float))
    for r in rows:
        f = norm_fips(r.get("fips"))
        if f not in frame:
            continue
        cap = fnum(r.get("capacity")) or 0.0
        if cap <= 0:
            continue
        grp = FP.fuel_group(r.get("fuel"))
        st = FP.status_bucket(r.get("status"))
        a = acc[f]
        if st in ("operating", "unknown"):
            a["op"] += cap
            a["op_" + grp] += cap
            ry = year_of(r.get("retirement"))
            if ry and 0 <= ry - report_year <= 5:
                a["retiring"] += cap
        elif st == "planned":
            a["planned"] += cap
            if grp in RENEW or grp == "storage":
                a["planned_renew"] += cap
            if grp == "gas":
                a["planned_gas"] += cap
    out = []
    for f in sorted(frame):
        a = acc.get(f, {})
        op = a.get("op", 0.0)
        out.append({
            "fips": f,
            "gen_capacity_mw": round(op, 2),
            "gen_fossil_share": rnd(share(sum(a.get("op_" + g, 0.0) for g in FOSSIL), op)),
            "gen_renewable_share": rnd(share(sum(a.get("op_" + g, 0.0) for g in RENEW), op)),
            "gen_nuclear_mw": round(a.get("op_nuclear", 0.0), 2),
            "planned_capacity_mw": round(a.get("planned", 0.0), 2),
            "planned_renewable_mw": round(a.get("planned_renew", 0.0), 2),
            "planned_gas_mw": round(a.get("planned_gas", 0.0), 2),
            "retiring_capacity_mw": round(a.get("retiring", 0.0), 2),
            "report_year": report_year,
            "source": source,
        })
    return out


GEN_CANDS = {
    "fips": ["county_id_fips", "county_fips", "fips_county"],
    "capacity": ["capacity_mw", "summer_capacity_mw"],
    "fuel": ["energy_source_code_1", "fuel_type_code_pudl", "energy_source_code",
             "technology_description"],
    "status": ["operational_status", "operational_status_code",
               "operational_status_pudl"],
    "year": ["report_date", "report_year"],
    "retirement": ["planned_generator_retirement_date", "planned_retirement_date",
                   "planned_retirement_year"],
}


def src_grid(cfg, frame, tmpdir):
    path = pudl_parquet(cfg, cfg["table"], tmpdir)
    gen, m = iter_parquet(path, GEN_CANDS, ("fips", "capacity", "fuel", "status", "year"))
    yr = max((year_of(r["year"]) or 0) for r in gen())
    if yr < 2015:
        raise SourceError(f"implausible latest report year {yr}")
    rows = build_grid((r for r in gen() if year_of(r["year"]) == yr), frame, yr,
                      f"PUDL {cfg['pudl_release']} {cfg['table']} ({cfg['license']})")
    tot = sum(r["gen_capacity_mw"] for r in rows)
    if not 5e5 < tot < 3e6:
        raise SourceError(f"implausible national operating capacity {tot:.0f} MW")
    return OUT_GRID, GEN_COLS, rows, {"report_year": yr, "resolved": m}


# --------------------------------------------------------------------------
# retail_price
# --------------------------------------------------------------------------

PRICE_COLS = ["fips", "price_com_cents", "price_ind_cents", "state_price_com_cents",
              "state_price_ind_cents", "n_utilities", "n_bundled_utilities",
              "report_year", "source"]
CLASSES = {"commercial": "com", "industrial": "ind"}


def build_price(sales, territory, frame, report_year, source) -> list:
    """sales: dicts utility, state, cls, service, revenue_usd, mwh, customers.
    territory: dicts utility, state, fips. Both already at report_year."""
    util = defaultdict(lambda: [0.0, 0.0, 0.0])        # (u, st, c) bundled rev, mwh, cust
    st_rev = defaultdict(float)
    st_mwh = defaultdict(float)
    for r in sales:
        c = CLASSES.get(str(r.get("cls") or "").strip().lower())
        if not c:
            continue
        st = str(r.get("state") or "").strip().upper()
        svc = str(r.get("service") or "bundled").strip().lower()
        rev, mwh = fnum(r.get("revenue_usd")), fnum(r.get("mwh"))
        if rev is not None:
            st_rev[(st, c)] += rev
        if mwh is not None and svc in ("bundled", "energy"):
            st_mwh[(st, c)] += mwh
        if svc == "bundled" and rev is not None and mwh:
            u = util[(str(r.get("utility")), st, c)]
            u[0] += rev
            u[1] += mwh
            u[2] += fnum(r.get("customers")) or 0.0

    def cents(rev, mwh):
        return rev / (mwh * 10.0) if mwh and mwh > 0 else None

    serving = defaultdict(set)
    for r in territory:
        f = norm_fips(r.get("fips"))
        if f in frame:
            serving[f].add((str(r.get("utility")), str(r.get("state") or "").strip().upper()))

    out = []
    for f in sorted(frame):
        st = frame[f]["state"]
        row = {"fips": f, "report_year": report_year, "source": source,
               "n_utilities": len(serving.get(f, ())) if f in serving else ""}
        bundled = set()
        for c in CLASSES.values():
            num = den = 0.0
            for (u, ust) in serving.get(f, ()):
                rec = util.get((u, ust, c))
                if not rec or rec[1] <= 0:
                    continue
                bundled.add(u)
                w = rec[2] if rec[2] > 0 else 1.0
                num += w * cents(rec[0], rec[1])
                den += w
            row[f"price_{c}_cents"] = rnd(num / den if den else None, 3)
            row[f"state_price_{c}_cents"] = rnd(cents(st_rev.get((st, c)), st_mwh.get((st, c))), 3)
        row["n_bundled_utilities"] = len(bundled) if f in serving else ""
        out.append(row)
    return out


SALES_CANDS = {
    "utility": ["utility_id_eia"], "state": ["state"],
    "cls": ["customer_class"], "service": ["service_type"],
    "revenue_usd": ["sales_revenue", "revenue"], "mwh": ["sales_mwh"],
    "customers": ["customers"], "year": ["report_date", "report_year"],
}
TERR_CANDS = {
    "utility": ["utility_id_eia"], "state": ["state"],
    "fips": ["county_id_fips", "county_fips"], "year": ["report_date", "report_year"],
}


def src_price(cfg, frame, tmpdir):
    sp = pudl_parquet(cfg, cfg["sales_table"], tmpdir)
    tp = pudl_parquet(cfg, cfg["territory_table"], tmpdir)
    sgen, sm = iter_parquet(sp, SALES_CANDS, ("utility", "state", "cls", "revenue_usd", "mwh", "year"))
    tgen, tm = iter_parquet(tp, TERR_CANDS, ("utility", "state", "fips", "year"))
    sy = {year_of(r["year"]) for r in sgen()}
    ty = {year_of(r["year"]) for r in tgen()}
    common = sorted(y for y in (sy & ty) if y)
    if not common:
        raise SourceError("no report year common to sales and service territory")
    yr = common[-1]
    rows = build_price([r for r in sgen() if year_of(r["year"]) == yr],
                       [r for r in tgen() if year_of(r["year"]) == yr],
                       frame, yr,
                       f"PUDL {cfg['pudl_release']} {cfg['sales_table']} + "
                       f"{cfg['territory_table']} ({cfg['license']})")
    sp_vals = sorted(r["state_price_com_cents"] for r in rows if r["state_price_com_cents"] != "")
    if not sp_vals:
        raise SourceError("no state prices computed")
    med = sp_vals[len(sp_vals) // 2]
    if not 4.0 <= med <= 40.0:
        raise SourceError(f"implausible median commercial price {med} cents/kWh "
                          "(revenue unit may have changed)")
    return OUT_PRICE, PRICE_COLS, rows, {"report_year": yr, "resolved_sales": sm,
                                         "resolved_territory": tm}


# --------------------------------------------------------------------------
# water_use (USGS 2015 county file)
# --------------------------------------------------------------------------

WATER_COLS = ["fips", "fw_withdrawal_mgd", "fw_withdrawal_per_sqmi",
              "gw_share", "irrigation_share", "thermo_share", "vintage", "source"]


def parse_usgs(text: str) -> list:
    """The USGS file carries a title line above the header; find the header."""
    lines = text.splitlines()
    hdr = next((i for i, ln in enumerate(lines[:20])
                if "FIPS" in ln and "TO-WFrTo" in ln), None)
    if hdr is None:
        raise SourceError("USGS header row (FIPS, TO-WFrTo) not found")
    return list(csv.DictReader(io.StringIO("\n".join(lines[hdr:]))))


def build_water(recs, frame, vintage, source) -> list:
    by = {}
    for r in recs:
        f = norm_fips(r.get("FIPS"))
        if not f:
            f = norm_fips(str(r.get("STATEFIPS", "")).zfill(2) + str(r.get("COUNTYFIPS", "")).zfill(3))
        if f in frame:
            by[f] = r
    out = []
    for f in sorted(frame):
        r = by.get(f)
        land = frame[f]["land_sqmi"]
        tot = fnum(r.get("TO-WFrTo")) if r else None
        thermo = None
        if r:
            parts = [fnum(r.get(k)) for k in ("PT-WFrTo", "PC-WFrTo", "PO-WFrTo")]
            if any(p is not None for p in parts):
                thermo = sum(p or 0.0 for p in parts)
        out.append({
            "fips": f,
            "fw_withdrawal_mgd": rnd(tot, 3),
            "fw_withdrawal_per_sqmi": rnd(tot / land if tot is not None and land else None, 6),
            "gw_share": rnd(share(fnum(r.get("TO-WGWFr")) if r else None, tot)),
            "irrigation_share": rnd(share(fnum(r.get("IR-WFrTo")) if r else None, tot)),
            "thermo_share": rnd(share(thermo, tot)),
            "vintage": vintage if r else "",
            "source": source if r else "",
        })
    return out


def src_water(cfg, frame, tmpdir):
    raw, url = first_ok(cfg["urls"])
    rows = build_water(parse_usgs(raw.decode("utf-8-sig", "replace")), frame,
                       cfg["vintage"], f"{cfg['license']}; {url}")
    hit = sum(1 for r in rows if r["fw_withdrawal_mgd"] != "")
    if hit < 0.9 * len(frame):
        raise SourceError(f"only {hit} of {len(frame)} counties matched")
    return OUT_WATER, WATER_COLS, rows, {"url": url}


# --------------------------------------------------------------------------
# drought (U.S. Drought Monitor)
# --------------------------------------------------------------------------

DROUGHT_COLS = ["fips", "drought_d1_mean_pct", "drought_d2_weeks_share",
                "weeks", "window", "source"]


def build_drought(recs, frame, window, threshold, min_weeks, source) -> list:
    """recs: USDM cumulative statistics rows (D1 = pct area in D1 or worse)."""
    wk = defaultdict(dict)
    for r in recs:
        f = norm_fips(r.get("FIPS") or r.get("fips"))
        if f not in frame:
            continue
        d = str(r.get("MapDate") or r.get("mapDate") or r.get("ValidStart") or "")[:10]
        d1, d2 = fnum(r.get("D1")), fnum(r.get("D2"))
        if d and d1 is not None and d2 is not None:
            wk[f][d] = (d1, d2)
    out = []
    for f in sorted(frame):
        w = list(wk.get(f, {}).values())
        ok = len(w) >= min_weeks
        out.append({
            "fips": f,
            "drought_d1_mean_pct": rnd(sum(x[0] for x in w) / len(w), 3) if ok else "",
            "drought_d2_weeks_share": rnd(sum(1 for x in w if x[1] >= threshold) / len(w)) if ok else "",
            "weeks": len(w),
            "window": window,
            "source": source,
        })
    return out


def parse_usdm(raw: bytes) -> list:
    txt = raw.decode("utf-8-sig", "replace").strip()
    if txt.startswith("["):
        return json.loads(txt)
    if txt.startswith("{"):
        obj = json.loads(txt)
        for k in ("data", "Data", "results"):
            if isinstance(obj.get(k), list):
                return obj[k]
        raise SourceError("USDM JSON without a record list")
    return list(csv.DictReader(io.StringIO(txt)))


def src_drought(cfg, frame, tmpdir):
    recs, errs = [], []
    for st in sorted({v["state"] for v in frame.values() if v["state"]}):
        url = cfg["url_template"].format(aoi=st, start=urllib.parse.quote(cfg["start"], safe=""),
                                         end=urllib.parse.quote(cfg["end"], safe=""))
        try:
            recs.extend(parse_usdm(http_get(url, headers={"Accept": "application/json"}, timeout=180)))
        except (SourceError, ValueError) as exc:
            errs.append(f"{st}: {exc}")
    window = f"{cfg['start']} to {cfg['end']}"
    rows = build_drought(recs, frame, window, cfg["d2_week_threshold_pct"],
                         cfg["min_weeks"], f"{cfg['license']}")
    hit = sum(1 for r in rows if r["drought_d1_mean_pct"] != "")
    if hit < 0.8 * len(frame):
        raise SourceError(f"only {hit} of {len(frame)} counties with {cfg['min_weeks']}+ weeks; "
                          f"state errors: {errs[:5]}")
    return OUT_DROUGHT, DROUGHT_COLS, rows, {"state_errors": errs, "records": len(recs)}


# --------------------------------------------------------------------------
# farmland (NASS Census of Agriculture)
# --------------------------------------------------------------------------

FARM_COLS = ["fips", "farm_acres", "farmland_share", "cropland_acres",
             "cropland_share", "census_year", "source"]


def nass_by_fips(recs) -> dict:
    out = {}
    for r in recs:
        st = str(r.get("state_fips_code") or "").zfill(2)
        co = str(r.get("county_code") or "").zfill(3)
        if co in ("998", "999", "000") or not st.isdigit() or not co.isdigit():
            continue
        if str(r.get("domain_desc") or "TOTAL").upper() != "TOTAL":
            continue
        out[st + co] = fnum(r.get("Value"))
    return out


def build_farm(farm, crop, frame, year, source) -> list:
    out = []
    for f in sorted(frame):
        acres_land = (frame[f]["land_sqmi"] or 0) * 640.0
        fa, ca = farm.get(f), crop.get(f)
        out.append({
            "fips": f,
            "farm_acres": rnd(fa, 0),
            "farmland_share": rnd(share(fa, acres_land)),
            "cropland_acres": rnd(ca, 0),
            "cropland_share": rnd(share(ca, acres_land)),
            "census_year": year if (fa is not None or ca is not None) else "",
            "source": source if (fa is not None or ca is not None) else "",
        })
    return out


def src_farm(cfg, frame, tmpdir):
    key = os.environ.get(cfg.get("key_env", "NASS_API_KEY"), "").strip()
    if not key:
        raise SourceError(f"secret {cfg.get('key_env')} not set; farmland skipped")
    got = {}
    for name, short in cfg["series"].items():
        q = urllib.parse.urlencode({"key": key, "source_desc": "CENSUS",
                                    "year": cfg["year"], "agg_level_desc": "COUNTY",
                                    "short_desc": short, "domain_desc": "TOTAL",
                                    "format": "JSON"})
        obj = json.loads(http_get(f"{cfg['api']}?{q}", timeout=300))
        if "data" not in obj:
            raise SourceError(f"NASS returned no data for '{short}': {str(obj)[:200]}")
        got[name] = nass_by_fips(obj["data"])
    rows = build_farm(got["farm_acres"], got["cropland_acres"], frame, cfg["year"],
                      f"{cfg['license']}, Census of Agriculture {cfg['year']}")
    hit = sum(1 for r in rows if r["farm_acres"] != "")
    if hit < 0.8 * len(frame):
        raise SourceError(f"only {hit} of {len(frame)} counties populated")
    return OUT_FARM, FARM_COLS, rows, {"series": cfg["series"]}


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

BUILDERS = {"grid_generation": src_grid, "retail_price": src_price,
            "water_use": src_water, "drought": src_drought, "farmland": src_farm}


def read_manifest() -> dict:
    try:
        return json.load(open(OUT_MANIFEST, encoding="utf-8"))
    except (OSError, ValueError):
        return {"sources": {}}


def main(only=None) -> int:
    cfg = json.load(open(SOURCES_CFG, encoding="utf-8"))["sources"]
    frame = load_frame()
    man = read_manifest()
    man.setdefault("sources", {})
    ran = ok = 0
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in BUILDERS.items():
            sc = cfg.get(name, {})
            if not sc.get("enabled", False) or (only and name not in only):
                continue
            ran += 1
            print(f"[{name}]")
            prev = man["sources"].get(name, {})
            try:
                path, cols, rows, info = fn(sc, frame, tmp)
                write_csv(path, cols, rows)
                ok += 1
                man["sources"][name] = {
                    "status": "ok", "attempted": now, "built": now,
                    "file": os.path.relpath(path, ROOT), "rows": len(rows),
                    "sha256": sha256(path),
                    "coverage": coverage(rows, [c for c in cols if c not in ("fips", "source")]),
                    "license": sc.get("license", ""), "info": info}
                print(f"  wrote {len(rows)} rows -> {os.path.relpath(path, ROOT)}")
            except (SourceError, Exception, SystemExit) as exc:      # noqa: BLE001
                man["sources"][name] = {**prev, "status": "failed", "attempted": now,
                                        "error": f"{type(exc).__name__}: {exc}"[:800]}
                print(f"  FAILED: {exc}")
    man["frame_counties"] = len(frame)
    man["note"] = ("county profile variables for the adaptive feature search; "
                   "all leakage class none; zoning regime not built (no national source)")
    os.makedirs(FEAT_DIR, exist_ok=True)
    with open(OUT_MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(man, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    print(f"{ok} of {ran} sources built")
    return 0 if ran == 0 or ok > 0 else 1


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    frame = {"19001": {"state": "IA", "land_sqmi": 100.0},
             "19003": {"state": "IA", "land_sqmi": 200.0},
             "17001": {"state": "IL", "land_sqmi": 50.0}}

    check("fnum strips commas", fnum("1,234") == 1234.0)
    check("fnum suppression is None", fnum("(D)") is None and fnum(" (Z)") is None)
    check("fnum dashes are None", fnum("--") is None)
    check("norm_fips pads", norm_fips("1001") == "01001" and norm_fips("19001.0") == "19001")
    check("share clipped", share(3, 2) == 1.0 and share(1, 0) is None)

    # grid
    g = build_grid([
        {"fips": "19001", "capacity": 100, "fuel": "NG", "status": "existing", "retirement": "2026-01-01"},
        {"fips": "19001", "capacity": 50, "fuel": "WND", "status": "existing"},
        {"fips": "19001", "capacity": 200, "fuel": "SUN", "status": "proposed"},
        {"fips": "19003", "capacity": 10, "fuel": "NUC", "status": "retired"},
        {"fips": "99999", "capacity": 10, "fuel": "NG", "status": "existing"},
    ], frame, 2023, "t")
    gb = {r["fips"]: r for r in g}
    check("grid densified to frame", len(g) == 3)
    check("grid operating capacity", gb["19001"]["gen_capacity_mw"] == 150)
    check("grid fossil share", abs(gb["19001"]["gen_fossil_share"] - 0.6667) < 1e-3)
    check("grid planned renewable", gb["19001"]["planned_renewable_mw"] == 200)
    check("grid retiring within 5y", gb["19001"]["retiring_capacity_mw"] == 100)
    check("grid retired excluded, true zero", gb["19003"]["gen_capacity_mw"] == 0
          and gb["19003"]["gen_fossil_share"] == "")

    # price
    sales = [
        {"utility": "1", "state": "IA", "cls": "commercial", "service": "bundled",
         "revenue_usd": 1_000_000, "mwh": 10_000, "customers": 300},
        {"utility": "2", "state": "IA", "cls": "commercial", "service": "bundled",
         "revenue_usd": 1_200_000, "mwh": 10_000, "customers": 100},
        {"utility": "1", "state": "IA", "cls": "industrial", "service": "bundled",
         "revenue_usd": 700_000, "mwh": 10_000, "customers": 10},
        {"utility": "9", "state": "IL", "cls": "commercial", "service": "delivery",
         "revenue_usd": 400_000, "mwh": 10_000, "customers": 50},
        {"utility": "8", "state": "IL", "cls": "commercial", "service": "energy",
         "revenue_usd": 600_000, "mwh": 10_000, "customers": 50},
    ]
    terr = [{"utility": "1", "state": "IA", "fips": "19001"},
            {"utility": "2", "state": "IA", "fips": "19001"},
            {"utility": "9", "state": "IL", "fips": "17001"}]
    p = {r["fips"]: r for r in build_price(sales, terr, frame, 2023, "t")}
    check("price weighted by customers", abs(p["19001"]["price_com_cents"] - 10.5) < 1e-9)
    check("price industrial", p["19001"]["price_ind_cents"] == 7.0)
    check("state price", abs(p["19001"]["state_price_com_cents"] - 11.0) < 1e-9)
    check("delivery-only county blank, not borrowed", p["17001"]["price_com_cents"] == "")
    check("restructured state price sums delivery and energy",
          p["17001"]["state_price_com_cents"] == 10.0)
    check("unserved county blank counts", p["19003"]["n_utilities"] == "")

    # water
    txt = ("Estimated use of water in the United States county-level data for 2015\n"
           "STATE,STATEFIPS,COUNTY,COUNTYFIPS,FIPS,TO-WGWFr,TO-WFrTo,IR-WFrTo,PT-WFrTo\n"
           "IA,19,Adair County,001,19001,2,10,5,--\n")
    w = {r["fips"]: r for r in build_water(parse_usgs(txt), frame, "2015", "t")}
    check("usgs header found under title", w["19001"]["fw_withdrawal_mgd"] == 10)
    check("water per sqmi", w["19001"]["fw_withdrawal_per_sqmi"] == 0.1)
    check("water shares", w["19001"]["gw_share"] == 0.2 and w["19001"]["irrigation_share"] == 0.5)
    check("water thermo all missing is blank", w["19001"]["thermo_share"] == "")
    check("water unmatched county blank", w["19003"]["fw_withdrawal_mgd"] == "")

    # drought
    recs = [{"FIPS": "19001", "MapDate": f"2020{i:04d}", "D1": "40", "D2": "60" if i < 2 else "10"}
            for i in range(4)]
    recs += [{"FIPS": "19003", "MapDate": "20200101", "D1": "5", "D2": "0"}]
    d = {r["fips"]: r for r in build_drought(recs, frame, "w", 50, 3, "t")}
    check("drought mean", d["19001"]["drought_d1_mean_pct"] == 40)
    check("drought d2 week share", d["19001"]["drought_d2_weeks_share"] == 0.5)
    check("drought too few weeks blank", d["19003"]["drought_d1_mean_pct"] == "")
    check("usdm json list parsed", len(parse_usdm(b'[{"FIPS":"19001","D1":1,"D2":0}]')) == 1)
    check("usdm csv parsed", len(parse_usdm(b"FIPS,D1,D2\n19001,1,0\n")) == 1)

    # farmland
    farm = nass_by_fips([
        {"state_fips_code": "19", "county_code": "001", "Value": "32,000", "domain_desc": "TOTAL"},
        {"state_fips_code": "19", "county_code": "003", "Value": "(D)", "domain_desc": "TOTAL"},
        {"state_fips_code": "19", "county_code": "998", "Value": "5", "domain_desc": "TOTAL"}])
    fr = {r["fips"]: r for r in build_farm(farm, {}, frame, 2022, "t")}
    check("farmland share", fr["19001"]["farmland_share"] == 0.5)
    check("suppressed stays blank", fr["19003"]["farm_acres"] == "")
    check("combined-counties code dropped", "19998" not in farm)

    # plugin registration is consistent with the files this module writes
    try:
        plug = json.load(open(P("configs", "feature_plugins.json"), encoding="utf-8"))
        files = {pl["file"]: pl for pl in plug.get("plugins", [])}
        declared = {"grid_generation.csv": GEN_COLS, "retail_price.csv": PRICE_COLS,
                    "water_use.csv": WATER_COLS, "drought.csv": DROUGHT_COLS,
                    "farmland.csv": FARM_COLS}
        good = True
        for fn, cols in declared.items():
            pl = files.get(f"data/features/{fn}")
            if pl is None:
                continue
            for col, meta in pl.get("columns", {}).items():
                good &= col in cols and meta.get("leakage_class") == "none"
        check("registered plugin columns exist and are leakage none", good)
    except (OSError, ValueError):
        check("plugin config readable", False)

    # outputs are LF and carry no em-dash
    with tempfile.TemporaryDirectory() as tmp:
        pth = os.path.join(tmp, "x.csv")
        write_csv(pth, GEN_COLS, g)
        raw = open(pth, "rb").read()
        check("LF line endings", b"\r\n" not in raw)
        check("no em-dash", chr(0x2014).encode() not in raw)

    print(f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated source names")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    sys.exit(main({s.strip() for s in a.only.split(",") if s.strip()} or None))
