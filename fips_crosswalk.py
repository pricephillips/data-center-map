#!/usr/bin/env python3
"""
fips_crosswalk.py — vintage-aware county FIPS harmonization.

Source of truth: U.S. Census Bureau Gazetteer files (counties + county
subdivisions), fetched by .github/workflows/fetch-census-geo-ref.yml into
data/census_geo/. Pre-2020 changes come from a small seed table transcribed
from Census "Substantial Changes to Counties and County Equivalent Entities".

Outputs
  data/fips_crosswalk.csv    from_fips -> to_fips, weight, relation, source
  data/county_reference.csv  current-vintage county list (fips, usps, name, aland)
  data/ct_town_region.csv    CT town -> legacy county + planning region

Rules (defensibility)
  * 1:1 changes (rename / merge) remap silently, status=remapped.
  * 1:many changes (CT realignment, AK splits) are NEVER fractionally
    allocated at record level. Records resolve only via a town/place match;
    otherwise status=ambiguous_split and the record is flagged, not guessed.
  * Weights (ALAND share) exist only for area-level reallocation of
    aggregate values and are labeled weight_basis=aland.

CLI
  python fips_crosswalk.py build   [--geo-dir data/census_geo] [--out-dir data]
  python fips_crosswalk.py audit   --csv FILE [--col fips] [--place-col place]
  python fips_crosswalk.py --selftest
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
import sys
import zipfile
from functools import lru_cache

import pandas as pd

DATA_DIR = os.environ.get("DATA_DIR", "data")
GEO_DIR = os.path.join(DATA_DIR, "census_geo")
XWALK_PATH = os.path.join(DATA_DIR, "fips_crosswalk.csv")
REF_PATH = os.path.join(DATA_DIR, "county_reference.csv")
CT_TOWN_PATH = os.path.join(DATA_DIR, "ct_town_region.csv")
LEGACY_VINTAGE = 2020

XWALK_COLS = ["from_fips", "from_name", "to_fips", "to_name", "relation",
              "weight", "weight_basis", "effective_year", "source"]

# Pre-2020 seed (Census "Substantial Changes to Counties"). Splits carry no
# weight: they must resolve by place or stay flagged.
SEED_CHANGES = [
    # from, to, relation, year
    ("46113", "46102", "rename", 2015),   # Shannon SD -> Oglala Lakota
    ("02270", "02158", "rename", 2015),   # Wade Hampton AK -> Kusilvak
    ("51515", "51019", "merge", 2013),    # Bedford city VA -> Bedford County
    ("51560", "51005", "merge", 2001),    # Clifton Forge city VA -> Alleghany
    ("02261", "02063", "split", 2019),    # Valdez-Cordova -> Chugach
    ("02261", "02066", "split", 2019),    # Valdez-Cordova -> Copper River
    ("02232", "02105", "split", 2007),    # Skagway-Hoonah-Angoon -> Hoonah-Angoon
    ("02232", "02230", "split", 2007),    # Skagway-Hoonah-Angoon -> Skagway
    ("02280", "02275", "split", 2008),    # Wrangell-Petersburg -> Wrangell
    ("02280", "02195", "split", 2008),    # Wrangell-Petersburg -> Petersburg
    ("02201", "02198", "split", 2008),    # PoW-Outer Ketchikan -> PoW-Hyder
    ("02201", "02130", "split", 2008),    # PoW-Outer Ketchikan -> Ketchikan Gateway
]
SEED_SOURCE = "census_substantial_changes_seed"


# ---------------------------------------------------------------- helpers
def norm_fips(v) -> str | None:
    """Zero-pad to 5 digits. Returns None for blanks / non-numeric."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = re.sub(r"\D", "", s)
    if not s or len(s) > 5:
        return None
    return s.zfill(5)


def norm_place(v) -> str:
    s = str(v or "").lower().strip()
    s = re.sub(r"\b(town|city|borough|township)( of)?\b", "", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _read_gaz(path: str) -> pd.DataFrame:
    """Read a Gazetteer .txt or .zip. 2020 files are tab-delimited with padded
    headers; 2024+ files are pipe-delimited, so the separator is sniffed."""
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".txt"))
            raw = z.read(name).decode("latin-1")
    else:
        with open(path, encoding="latin-1") as fh:
            raw = fh.read()
    sep = "|" if "|" in raw.split("\n", 1)[0] else "\t"
    df = pd.read_csv(io.StringIO(raw), sep=sep, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df.apply(lambda c: c.str.strip() if pd.api.types.is_string_dtype(c) else c)


def _find(geo_dir: str, kind: str, vintage: int | None = None) -> tuple[str, int]:
    pats = glob.glob(os.path.join(geo_dir, f"*_Gaz_{kind}_national.*"))
    found = {}
    for p in pats:
        m = re.match(r"(\d{4})_Gaz_", os.path.basename(p))
        if m:
            found[int(m.group(1))] = p
    if not found:
        raise FileNotFoundError(f"no {kind} Gazetteer file in {geo_dir}")
    if vintage is not None:
        if vintage not in found:
            raise FileNotFoundError(f"{vintage} {kind} Gazetteer missing in {geo_dir}")
        return found[vintage], vintage
    v = max(found)
    return found[v], v


# ---------------------------------------------------------------- build
def build_crosswalk(legacy_cty: pd.DataFrame, cur_cty: pd.DataFrame,
                    legacy_cs: pd.DataFrame, cur_cs: pd.DataFrame,
                    cur_vintage: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Pure function: Gazetteer frames in, (crosswalk, reference, ct_towns) out."""
    for df in (legacy_cty, cur_cty):
        df["fips"] = df["GEOID"].map(norm_fips)
        df["ALAND"] = pd.to_numeric(df["ALAND"], errors="coerce").fillna(0)
    cur_names = dict(zip(cur_cty["fips"], cur_cty["NAME"]))
    leg_names = dict(zip(legacy_cty["fips"], legacy_cty["NAME"]))
    cur_set, leg_set = set(cur_names), set(leg_names)

    rows = []
    # identity rows for every current county
    for f, n in cur_names.items():
        rows.append([f, n, f, n, "identity", 1.0, "identity", None,
                     f"census_gazetteer_{cur_vintage}"])

    # legacy -> current via county-subdivision membership (handles CT 2022)
    def cs_frame(df):
        d = df.copy()
        d["county"] = d["GEOID"].str[:5]
        d["cousubfp"] = d["GEOID"].str[5:10]
        # "County subdivisions not defined" (cousubfp 00000, water-only) all
        # share ANSICODE 00000000 nationwide; keyed on it they fan every legacy
        # county out to hundreds of unrelated counties. Drop them, treat an
        # all-zero ANSICODE as missing, and scope keys to the state.
        d = d[d["cousubfp"] != "00000"].copy()
        ans = d["ANSICODE"].fillna("").str.strip()
        ans = ans.where(~ans.str.fullmatch(r"0*"), "")
        d["key"] = d["GEOID"].str[:2] + ":" + ans
        d.loc[ans == "", "key"] = "fp:" + d["GEOID"].str[:2] + d["cousubfp"]
        d["ALAND"] = pd.to_numeric(d["ALAND"], errors="coerce").fillna(0)
        return d

    lcs, ccs = cs_frame(legacy_cs), cs_frame(cur_cs)
    dropped = sorted(leg_set - cur_set)
    ct_towns = pd.DataFrame(columns=["town", "town_norm", "cousub_geoid",
                                     "legacy_fips", "legacy_name",
                                     "region_fips", "region_name"])
    if dropped:
        lsub = lcs[lcs["county"].isin(dropped)][["key", "county", "NAME", "ALAND"]]
        m = lsub.merge(ccs[["key", "county", "GEOID"]], on="key",
                       suffixes=("_leg", "_cur"), how="left")
        unmatched = m["county_cur"].isna().sum()
        if unmatched:
            print(f"[fips_crosswalk] WARN {unmatched} legacy subdivisions "
                  "unmatched in current vintage", file=sys.stderr)
        m = m.dropna(subset=["county_cur"])
        g = m.groupby(["county_leg", "county_cur"])["ALAND"].sum().reset_index()
        tot = g.groupby("county_leg")["ALAND"].transform("sum")
        g["weight"] = (g["ALAND"] / tot).round(6)
        fanout = g.groupby("county_leg")["county_cur"].transform("nunique")
        for (_, r), k in zip(g.iterrows(), fanout):
            rel = "rename" if k == 1 else "split"
            if r["county_leg"].startswith("09"):
                rel = "ct_realign"
            rows.append([r["county_leg"], leg_names.get(r["county_leg"]),
                         r["county_cur"], cur_names.get(r["county_cur"]),
                         rel, float(r["weight"]), "aland", None,
                         f"census_gazetteer_{LEGACY_VINTAGE}_{cur_vintage}_cousub"])
        ct = m[m["county_leg"].str.startswith("09")].copy()
        if len(ct):
            ct_towns = pd.DataFrame({
                "town": ct["NAME"],
                "town_norm": ct["NAME"].map(norm_place),
                "cousub_geoid": ct["GEOID"],
                "legacy_fips": ct["county_leg"],
                "legacy_name": ct["county_leg"].map(leg_names),
                "region_fips": ct["county_cur"],
                "region_name": ct["county_cur"].map(cur_names),
            }).sort_values(["region_fips", "town"])

    # pre-2020 seed (only where target still exists)
    for frm, to, rel, yr in SEED_CHANGES:
        if to in cur_set and frm not in cur_set:
            rows.append([frm, None, to, cur_names[to], rel,
                         1.0 if rel != "split" else None,
                         "identity" if rel != "split" else "none",
                         yr, SEED_SOURCE])

    xw = pd.DataFrame(rows, columns=XWALK_COLS)
    xw = xw.drop_duplicates(["from_fips", "to_fips"], keep="first")
    xw = xw.sort_values(["from_fips", "to_fips"]).reset_index(drop=True)
    ref = cur_cty[["fips", "USPS", "NAME", "ALAND"]].rename(
        columns={"USPS": "usps", "NAME": "name", "ALAND": "aland"})
    ref["vintage"] = cur_vintage
    return xw, ref.sort_values("fips").reset_index(drop=True), ct_towns


def build(geo_dir: str = GEO_DIR, out_dir: str = DATA_DIR) -> dict:
    lc, _ = _find(geo_dir, "counties", LEGACY_VINTAGE)
    cc, cv = _find(geo_dir, "counties")
    ls, _ = _find(geo_dir, "cousubs", LEGACY_VINTAGE)
    cs, csv_ = _find(geo_dir, "cousubs", cv)
    xw, ref, ct = build_crosswalk(_read_gaz(lc), _read_gaz(cc),
                                  _read_gaz(ls), _read_gaz(cs), cv)
    problems = validate(xw)
    if problems:
        raise SystemExit("[fips_crosswalk] QC FAIL:\n  " + "\n  ".join(problems))
    os.makedirs(out_dir, exist_ok=True)
    xw.to_csv(os.path.join(out_dir, "fips_crosswalk.csv"), index=False)
    ref.to_csv(os.path.join(out_dir, "county_reference.csv"), index=False)
    ct.to_csv(os.path.join(out_dir, "ct_town_region.csv"), index=False)
    summary = {"current_vintage": cv, "counties": len(ref),
               "remap_rows": int((xw["relation"] != "identity").sum()),
               "ct_towns": len(ct)}
    print(f"[fips_crosswalk] built {summary}")
    return summary


def validate(xw: pd.DataFrame, strict_ct: bool = True) -> list[str]:
    p = []
    if xw["from_fips"].isna().any() or xw["to_fips"].isna().any():
        p.append("null fips in crosswalk")
    w = xw[xw["weight_basis"] == "aland"].groupby("from_fips")["weight"].sum()
    bad = w[(w - 1).abs() > 1e-4]
    if len(bad):
        p.append(f"aland weights do not sum to 1: {bad.to_dict()}")
    ct_leg = [f"09{c:03d}" for c in range(1, 16, 2)]
    present = set(xw["from_fips"])
    if strict_ct and any(f in present for f in ct_leg):
        miss = [f for f in ct_leg if f not in present]
        if miss:
            p.append(f"CT legacy counties missing: {miss}")
    return p


# ---------------------------------------------------------------- runtime API
@lru_cache(maxsize=1)
def _load(data_dir: str = DATA_DIR):
    xw = pd.read_csv(os.path.join(data_dir, "fips_crosswalk.csv"), dtype=str)
    ref = pd.read_csv(os.path.join(data_dir, "county_reference.csv"), dtype=str)
    ctp = os.path.join(data_dir, "ct_town_region.csv")
    ct = pd.read_csv(ctp, dtype=str) if os.path.exists(ctp) else pd.DataFrame()
    fwd: dict[str, list[tuple[str, str, float | None]]] = {}
    for r in xw.itertuples(index=False):
        w = float(r.weight) if isinstance(r.weight, str) and r.weight else None
        fwd.setdefault(r.from_fips, []).append((r.to_fips, r.relation, w))
    towns = {}
    if len(ct):
        for r in ct.itertuples(index=False):
            towns[r.town_norm] = r.region_fips
    return fwd, set(ref["fips"]), towns


def available(data_dir: str = DATA_DIR) -> bool:
    return os.path.exists(os.path.join(data_dir, "fips_crosswalk.csv"))


def resolve(fips, place=None, data_dir: str = DATA_DIR) -> tuple[str | None, str]:
    """-> (current_fips | None, status). status in:
    current | remapped | resolved_by_place | ambiguous_split | unknown | missing"""
    f = norm_fips(fips)
    fwd, current, towns = _load(data_dir)
    if f is None:
        return None, "missing"
    if f in current:
        return f, "current"
    targets = fwd.get(f)
    if not targets:
        return None, "unknown"
    if len(targets) == 1:
        return targets[0][0], "remapped"
    if place:
        t = towns.get(norm_place(place))
        cand = {t_[0] for t_ in targets}
        if t and t in cand:
            return t, "resolved_by_place"
    return None, "ambiguous_split"


def apply_to_frame(df: pd.DataFrame, fips_col: str = "fips",
                   place_col: str | None = None,
                   data_dir: str = DATA_DIR) -> pd.DataFrame:
    """Additive: keeps original in `<col>_original`, writes current code to
    `<col>`, adds `fips_status`. Ambiguous/unknown rows keep original code so
    nothing is dropped; downstream QC decides."""
    if not available(data_dir):
        print("[fips_crosswalk] crosswalk not built; skipping", file=sys.stderr)
        return df
    out = df.copy()
    orig = f"{fips_col}_original"
    if orig not in out.columns:
        out[orig] = out[fips_col]
    places = out[place_col] if place_col and place_col in out.columns else [None] * len(out)
    res = [resolve(f, p, data_dir) for f, p in zip(out[orig], places)]
    new = [r[0] if r[0] else norm_fips(o) for r, o in zip(res, out[orig])]
    out[fips_col] = new
    out["fips_status"] = [r[1] for r in res]
    return out


def reallocate(df: pd.DataFrame, value_cols: list[str], fips_col: str = "fips",
               data_dir: str = DATA_DIR) -> pd.DataFrame:
    """Area-level only: move additive county values (e.g. legacy CT vote
    counts) onto current geography using ALAND weights. Never use on labels."""
    xw = pd.read_csv(os.path.join(data_dir, "fips_crosswalk.csv"), dtype=str)
    xw = xw[xw["weight"].notna()].copy()
    xw["weight"] = xw["weight"].astype(float)
    d = df.copy()
    d[fips_col] = d[fips_col].map(norm_fips)
    m = d.merge(xw[["from_fips", "to_fips", "weight"]],
                left_on=fips_col, right_on="from_fips", how="left")
    m["to_fips"] = m["to_fips"].fillna(m[fips_col])
    m["weight"] = m["weight"].fillna(1.0)
    for c in value_cols:
        m[c] = pd.to_numeric(m[c], errors="coerce") * m["weight"]
    return (m.groupby("to_fips")[value_cols].sum().reset_index()
             .rename(columns={"to_fips": fips_col}))


def audit(path: str, col: str = "fips", place_col: str | None = None) -> pd.Series:
    df = pd.read_csv(path, dtype=str)
    counts = apply_to_frame(df, col, place_col)["fips_status"].value_counts()
    print(counts.to_string())
    return counts


# ---------------------------------------------------------------- selftest
def _selftest() -> None:
    import tempfile

    def gaz(rows, cols):
        return pd.DataFrame(rows, columns=cols)

    C = ["USPS", "GEOID", "ANSICODE", "NAME", "ALAND"]
    legacy_cty = gaz([["CT", "09001", "1", "Fairfield County", "100"],
                      ["CT", "09009", "2", "New Haven County", "100"],
                      ["IA", "19153", "3", "Polk County", "50"]], C)
    cur_cty = gaz([["CT", "09190", "4", "Western Connecticut Planning Region", "60"],
                   ["CT", "09120", "5", "Greater Bridgeport Planning Region", "40"],
                   ["CT", "09170", "6", "South Central Connecticut Planning Region", "100"],
                   ["SD", "46102", "7", "Oglala Lakota County", "10"],
                   ["IA", "19153", "3", "Polk County", "50"]], C)
    S = ["USPS", "GEOID", "ANSICODE", "NAME", "ALAND"]
    legacy_cs = gaz([["CT", "0900108070", "a", "Bridgeport town", "40"],
                     ["CT", "0900150580", "b", "Norwalk town", "60"],
                     ["CT", "0900952000", "c", "New Haven town", "100"],
                     ["CT", "0900100000", "00000000", "County subdivisions not defined", "0"]], S)
    cur_cs = gaz([["CT", "0912008070", "a", "Bridgeport town", "40"],
                  ["CT", "0919050580", "b", "Norwalk town", "60"],
                  ["CT", "0917052000", "c", "New Haven town", "100"],
                  ["IA", "1915300000", "00000000", "County subdivisions not defined", "0"],
                  ["CT", "0917000000", "00000000", "County subdivisions not defined", "0"]], S)
    xw, ref, ct = build_crosswalk(legacy_cty, cur_cty, legacy_cs, cur_cs, 2025)
    assert not validate(xw, strict_ct=False), validate(xw, False)
    # undefined water-only subdivisions must not fan legacy counties out
    assert set(xw[xw.from_fips == "09001"].to_fips) == {"09120", "09190"}, \
        xw[xw.from_fips == "09001"]
    fair = xw[xw.from_fips == "09001"].set_index("to_fips")["weight"]
    assert abs(fair["09190"] - 0.6) < 1e-9 and abs(fair["09120"] - 0.4) < 1e-9
    assert xw[(xw.from_fips == "09009")].relation.iloc[0] == "ct_realign"
    assert ((xw.from_fips == "46113") & (xw.to_fips == "46102")).any()
    assert len(ct) == 3

    with tempfile.TemporaryDirectory() as d:
        # both Gazetteer layouts: 2020 tab + padded header, 2024+ pipe
        for sep, name in (("\t", "tab.txt"), ("|", "pipe.txt")):
            p = os.path.join(d, name)
            with open(p, "w", encoding="latin-1") as fh:
                fh.write(sep.join(["USPS", "GEOID", "NAME   "]) + "   \n"
                         + sep.join(["IA", "19153", "Polk County"]) + "\n")
            g = _read_gaz(p)
            assert list(g.columns) == ["USPS", "GEOID", "NAME"], list(g.columns)
            assert g.loc[0, "GEOID"] == "19153"

    with tempfile.TemporaryDirectory() as d:
        xw.to_csv(os.path.join(d, "fips_crosswalk.csv"), index=False)
        ref.to_csv(os.path.join(d, "county_reference.csv"), index=False)
        ct.to_csv(os.path.join(d, "ct_town_region.csv"), index=False)
        _load.cache_clear()
        assert resolve("19153", data_dir=d) == ("19153", "current")
        assert resolve(19153.0, data_dir=d) == ("19153", "current")
        assert resolve("46113", data_dir=d) == ("46102", "remapped")
        assert resolve("09009", data_dir=d) == ("09170", "remapped")
        assert resolve("09001", data_dir=d) == (None, "ambiguous_split")
        assert resolve("09001", "Town of Norwalk", d) == ("09190", "resolved_by_place")
        assert resolve("99999", data_dir=d) == (None, "unknown")
        assert resolve("", data_dir=d) == (None, "missing")
        f = apply_to_frame(pd.DataFrame({"fips": ["9001", "46113"],
                                         "place": ["Bridgeport", None]}),
                           place_col="place", data_dir=d)
        assert list(f.fips) == ["09120", "46102"], list(f.fips)
        assert list(f.fips_status) == ["resolved_by_place", "remapped"]
        r = reallocate(pd.DataFrame({"fips": ["09001"], "votes": [1000]}),
                       ["votes"], data_dir=d).set_index("fips")["votes"]
        assert r["09190"] == 600 and r["09120"] == 400
        _load.cache_clear()
    print("[fips_crosswalk] selftest OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", choices=["build", "audit"])
    ap.add_argument("--geo-dir", default=GEO_DIR)
    ap.add_argument("--out-dir", default=DATA_DIR)
    ap.add_argument("--csv")
    ap.add_argument("--col", default="fips")
    ap.add_argument("--place-col")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        _selftest()
    elif a.cmd == "build":
        build(a.geo_dir, a.out_dir)
    elif a.cmd == "audit":
        if not a.csv:
            ap.error("audit needs --csv")
        audit(a.csv, a.col, a.place_col)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
