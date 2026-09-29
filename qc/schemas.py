#!/usr/bin/env python3
"""
qc/schemas.py

Declared, enforced schemas for three tables (spec 005, US1). This is the only
module in the repository that imports Pandera (FR-001).

  clean_feed     master_opposition_clean.csv      Layer C
  county_scores  data/county_policy_scores.csv    Layer D
  proposals      data/proposals.csv               Layer B, ids read through
                                                  data/project_key_map.csv

The known defects this exists for (mixed State values, a stray US, boolean
coercion, retired Connecticut FIPS) were each found by hand after they had
reached a deliverable. A schema finds them on the build that introduces them.

Nothing here edits an input (FR-007). The outputs are a report and a run log:

  qc/schema_report.md          every failure, with row ids; allowed exceptions
  data/schema_run_history.csv  one row per distinct input; counts clean runs

Single sources (FR-002): the outcome grades come from
outcome_defensibility.OUTCOME_GRADES and the state set and normalizer from
qc/schema_adapter.py. Nothing is copied here.

Mode (FR-003) is one flag in configs/data_quality.json, schema.mode:
  report_only  never blocks
  window       report-only until window_clean_runs consecutive clean runs,
               then blocking; the history records the switch date and keeps it
  blocking     blocks on any failure no allowed_exceptions rule covers

Usage
  python qc/schemas.py                 validate, write report and history
  python qc/schemas.py --no-write      validate and print only
  python qc/schemas.py --only clean_feed,proposals
  python qc/schemas.py --selftest

Exit codes: 0 clean or report-only; 1 effective blocking with failures;
2 an input file is missing or unreadable.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.append(_p)

import outcome_defensibility as _OD  # noqa: E402
import schema_adapter as _SA  # noqa: E402

try:
    import pandas as pd
    import pandera.pandas as pa
    HAVE_PANDERA = True
except ImportError:                      # CI before the pending patch lands
    pd = pa = None
    HAVE_PANDERA = False

CONFIG = os.path.join(ROOT, "configs", "data_quality.json")
CLEAN_CSV = os.path.join(ROOT, "master_opposition_clean.csv")
MASTER_CSV = os.path.join(ROOT, "master_opposition.csv")
SCORES_CSV = os.path.join(ROOT, "data", "county_policy_scores.csv")
FRAME_CSV = os.path.join(ROOT, "data", "county_census_features.csv")
PROPOSALS_CSV = os.path.join(ROOT, "data", "proposals.csv")
KEYMAP_CSV = os.path.join(ROOT, "data", "project_key_map.csv")
REPORT_MD = os.path.join(ROOT, "qc", "schema_report.md")
HISTORY_CSV = os.path.join(ROOT, "data", "schema_run_history.csv")

SCHEMAS = ("clean_feed", "county_scores", "proposals")
MODES = ("report_only", "window", "blocking")
HISTORY_COLS = ["run_utc", "input_sha256", "mode_config", "mode_effective",
                "failures", "allowed", "clean", "consecutive_clean", "switch_date"]
DEFAULTS = {"mode": "window", "window_clean_runs": 7, "switch_date": None,
            "proposals_columns": [], "allowed_exceptions": []}

# U.S. extent, the same box state_bounds.US_BOX uses, plus the Aleutians east
# of the antimeridian.
LAT_MIN, LAT_MAX = 17.0, 72.0
LON_RANGES = ((-180.0, -64.0), (172.0, 180.0))
BOOL_TOKENS = ("true", "false", "1", "0")
SHOW = 50                                  # rows listed per check in the report


# --------------------------------------------------------------------------
# config and inputs
# --------------------------------------------------------------------------

def load_config(path=None) -> tuple[dict, list[str]]:
    """schema block of configs/data_quality.json over DEFAULTS, plus notes."""
    notes = []
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(path or CONFIG, encoding="utf-8")).get("schema", {}))
    except (OSError, ValueError) as exc:
        notes.append(f"config unreadable ({type(exc).__name__}); built-in defaults used")
    if cfg.get("mode") not in MODES:
        notes.append(f"unknown schema.mode {cfg.get('mode')!r}; treated as report_only")
        cfg["mode"] = "report_only"
    return cfg, notes


def read_str(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def file_sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# element rules (vectorized over str Series; blank handled explicitly)
# --------------------------------------------------------------------------

def _num(s):
    return pd.to_numeric(s.str.replace(",", "", regex=False).str.strip(), errors="coerce")


def num_in(lo, hi, allow_blank=True):
    """Blank passes when allowed; a present value must parse and sit in range."""
    def f(s):
        blank = s.str.strip() == ""
        x = _num(s)
        ok = x.between(lo, hi)
        return (blank & allow_blank) | (~blank & ok.fillna(False))
    return f


def lon_ok(s):
    blank = s.str.strip() == ""
    x = _num(s)
    ok = pd.Series(False, index=s.index)
    for lo, hi in LON_RANGES:
        ok |= x.between(lo, hi).fillna(False)
    return blank | ok


def coords_paired(df):
    return (df["lat"].str.strip() == "") == (df["lon"].str.strip() == "")


def not_null_island(df):
    la, lo = _num(df["lat"]), _num(df["lon"])
    return ~((la.abs() < 0.001) & (lo.abs() < 0.001)).fillna(False)


def in_frame(frame_fips):
    """A malformed code is fips_format's failure, not a second one here."""
    return lambda s: ~s.str.fullmatch(r"\d{5}") | s.isin(frame_fips)


def _col_check(fn, name):
    return pa.Check(fn, name=name, error=name)


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------

def clean_feed_schema(frame_fips: set):
    states = sorted(_SA.STATE_ABBREV)
    return pa.DataFrameSchema(
        {
            "outcome_defensible": pa.Column(str, _col_check(
                lambda s: s.isin(_OD.OUTCOME_GRADES), "outcome_grade")),
            "State": pa.Column(str, _col_check(lambda s: s.isin(states), "state_normalized")),
            "lat": pa.Column(str, _col_check(num_in(LAT_MIN, LAT_MAX), "lat_bounds")),
            "lon": pa.Column(str, _col_check(lon_ok, "lon_bounds")),
            "^is_": pa.Column(str, _col_check(
                lambda s: s.str.strip().str.lower().isin(BOOL_TOKENS), "bool_token"),
                regex=True, required=False),
            # The feed carries no fips today (research D7); checked if it ever does.
            "fips": pa.Column(str, [
                _col_check(lambda s: s.str.fullmatch(r"\d{5}"), "fips_format"),
                _col_check(in_frame(frame_fips), "fips_frame")], required=False),
        },
        checks=[_col_check(coords_paired, "coords_paired"),
                _col_check(not_null_island, "not_null_island")],
        name="clean_feed")


def county_scores_schema(frame_fips: set):
    return pa.DataFrameSchema(
        {
            "fips": pa.Column(str, [
                _col_check(lambda s: s.str.fullmatch(r"\d{5}"), "fips_format"),
                _col_check(in_frame(frame_fips), "fips_frame"),
                _col_check(lambda s: ~s.duplicated(keep=False), "fips_unique")]),
            "calibrated_score": pa.Column(str, _col_check(num_in(0, 1, False), "score_unit")),
            "raw_oof_score": pa.Column(str, _col_check(num_in(0, 1, False), "score_unit")),
            "score_decile": pa.Column(str, _col_check(
                lambda s: s.isin([str(i) for i in range(1, 11)]), "decile_range")),
            "has_enacted_restrictive": pa.Column(str, _col_check(
                lambda s: s.isin(["0", "1"]), "label_binary")),
        },
        name="county_scores")


def proposals_schema(columns: list, active_ids: set):
    cols = {c: pa.Column(str) for c in columns}
    cols.update({
        "id": pa.Column(str, [
            _col_check(lambda s: (s.str.strip() != "") & ~s.duplicated(keep=False), "id_unique"),
            _col_check(lambda s: s.isin(active_ids), "id_keyed")]),
        "state": pa.Column(str, _col_check(
            lambda s: s.map(_SA.normalize_state) != "", "state_normalizable")),
        "lat": pa.Column(str, _col_check(num_in(LAT_MIN, LAT_MAX), "lat_bounds")),
        "lon": pa.Column(str, _col_check(lon_ok, "lon_bounds")),
        "capacity_mw": pa.Column(str, _col_check(num_in(0, float("inf")), "capacity_nonneg")),
    })
    return pa.DataFrameSchema(
        cols,
        checks=[_col_check(coords_paired, "coords_paired"),
                _col_check(not_null_island, "not_null_island")],
        name="proposals")


# --------------------------------------------------------------------------
# row ids
# --------------------------------------------------------------------------

def clean_row_ids(df):
    """r<line> locates a row; row_key survives reordering."""
    out = {}
    for i, r in df.iterrows():
        basis = "|".join(str(r.get(k, "")) for k in ("Source URL", "Incident", "Date", "State"))
        key = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
        pid = str(r.get("project_id", "") or "")
        out[i] = (f"r{i + 2}", key + (f" {pid}" if pid else ""))
    return out


def fips_row_ids(df):
    return {i: (str(r.get("fips", "")), "") for i, r in df.iterrows()}


def proposal_row_ids(df, pk_by_id: dict):
    return {i: (pk_by_id.get(str(r.get("id", "")), "no_pk"), f"id {r.get('id', '')}")
            for i, r in df.iterrows()}


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def _check_id(row) -> str:
    chk = str(row.get("check", ""))
    if chk == "column_in_dataframe":
        return "columns_present"
    return chk


def _matches(rule, check, row) -> bool:
    """A rule names one check or a list; each when-value is one value or a list."""
    checks = rule.get("check")
    if check not in (checks if isinstance(checks, list) else [checks]):
        return False
    for col, want in rule.get("when", {}).items():
        have = str(row.get(col, ""))
        if have not in ([str(w) for w in want] if isinstance(want, list) else [str(want)]):
            return False
    return True


def validate(name, schema, df, ids, rules) -> list[dict]:
    """Failure records for one table. Allowed exceptions are marked, not dropped."""
    try:
        schema.validate(df, lazy=True)
        return []
    except pa.errors.SchemaErrors as exc:
        fc = exc.failure_cases
    out, seen = [], set()
    for _, row in fc.iterrows():
        check = _check_id(row)
        col = row.get("column")
        col = "" if col is None or (isinstance(col, float) and col != col) else str(col)
        if row.get("schema_context") == "DataFrameSchema" and check != "columns_present":
            col = ""                       # frame-level check: one finding per row
        idx = row.get("index")
        if check == "columns_present":
            col = str(row.get("failure_case", col))
            rid, key, value = "(frame)", "", "missing column"
            rec_row = None
        else:
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = None
            rid, key = ids.get(idx, ("?", ""))
            value = "" if row.get("failure_case") is None else str(row.get("failure_case"))
            rec_row = df.loc[idx] if idx is not None and idx in df.index else None
            if rec_row is not None and not col:
                value = f"lat={rec_row.get('lat', '')} lon={rec_row.get('lon', '')}"
        dedupe = (check, col, rid, key)
        if dedupe in seen:                 # a frame-level check reports once per column
            continue
        seen.add(dedupe)
        allowed = ""
        if rec_row is not None:
            for rule in rules:
                if rule.get("schema") == name and _matches(rule, check, rec_row):
                    allowed = rule.get("reason", "declared exception")
                    break
        out.append({"schema": name, "check": check, "column": col, "row_id": rid,
                    "row_key": key, "value": value, "allowed": allowed})
    return out


def load_tables(only, cfg) -> tuple[dict, list[str]]:
    """name -> (path, schema, df, ids). Missing inputs are listed, not raised."""
    tables, missing = {}, []
    need = [n for n in SCHEMAS if not only or n in only]
    frame_fips = set()
    if {"clean_feed", "county_scores"} & set(need):
        try:
            frame_fips = {f.zfill(5) for f in read_str(FRAME_CSV)["fips"]}
        except (OSError, KeyError):
            missing.append(os.path.relpath(FRAME_CSV, ROOT))
    for name in need:
        try:
            if name == "clean_feed":
                df = read_str(CLEAN_CSV)
                tables[name] = (CLEAN_CSV, clean_feed_schema(frame_fips), df, clean_row_ids(df))
            elif name == "county_scores":
                df = read_str(SCORES_CSV)
                tables[name] = (SCORES_CSV, county_scores_schema(frame_fips), df, fips_row_ids(df))
            else:
                km = read_str(KEYMAP_CSV)
                km = km[km["status"] == "active"]
                pk_by_id = dict(zip(km["current_id"], km["pk"]))
                df = read_str(PROPOSALS_CSV)
                # the permanent key rides along so exception rules can name a
                # project without an id the source may renumber
                df["pk"] = df["id"].map(pk_by_id).fillna("")
                tables[name] = (PROPOSALS_CSV,
                                proposals_schema(cfg.get("proposals_columns") or [], set(pk_by_id)),
                                df, proposal_row_ids(df, pk_by_id))
        except (OSError, KeyError) as exc:
            missing.append(f"{name}: {exc}")
    return tables, missing


# --------------------------------------------------------------------------
# run history and mode
# --------------------------------------------------------------------------

def read_history() -> list[dict]:
    try:
        with open(HISTORY_CSV, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def decide_mode(cfg, history, input_sha, failures, today) -> tuple[dict, bool]:
    """The history row for this run, and whether to append it.

    An input identical to the last row's does not append: re-running on
    unchanged data cannot count toward the window.
    """
    prior = list(history)
    append = True
    if prior and prior[-1].get("input_sha256") == input_sha:
        prior = prior[:-1]
        append = False
    last = prior[-1] if prior else {}
    prev_consec = int(last.get("consecutive_clean") or 0)
    prev_switch = last.get("switch_date") or ""
    mode = cfg["mode"]
    if mode == "blocking":
        eff = "blocking"
        switch = str(cfg.get("switch_date") or prev_switch or today)
    elif mode == "window":
        eff = "blocking" if prev_switch or prev_consec >= int(cfg["window_clean_runs"]) \
            else "report_only"
        switch = prev_switch or (today if eff == "blocking" else "")
    else:
        eff, switch = "report_only", prev_switch
    clean = failures == 0
    row = {"run_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "input_sha256": input_sha, "mode_config": mode, "mode_effective": eff,
           "failures": failures, "allowed": 0, "clean": int(clean),
           "consecutive_clean": prev_consec + 1 if clean else 0, "switch_date": switch}
    return row, append


def append_history(row):
    new = not os.path.exists(HISTORY_CSV)
    os.makedirs(os.path.dirname(HISTORY_CSV), exist_ok=True)
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_COLS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(row)


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def master_duplicates() -> int | None:
    try:
        with open(MASTER_CSV, newline="", encoding="utf-8-sig") as fh:
            c = Counter(tuple(r) for r in csv.reader(fh))
    except OSError:
        return None
    return sum(n - 1 for n in c.values() if n > 1)


def _md(v) -> str:
    return str(v).replace("|", "/").replace("\n", " ").replace("\r", " ")[:120]


def render_report(results, row, cfg, notes, dupes) -> str:
    fails = [f for fs in results.values() for f in fs["failures"] if not f["allowed"]]
    allowed = [f for fs in results.values() for f in fs["failures"] if f["allowed"]]
    L = ["# Schema Report", "",
         "Generated by qc/schemas.py (spec 005). Do not edit by hand. Settings: "
         "configs/data_quality.json.", "",
         f"- Run (UTC): {row['run_utc']}",
         f"- Mode: config {row['mode_config']}, effective {row['mode_effective']}; "
         f"consecutive clean runs {row['consecutive_clean']} of {cfg['window_clean_runs']}; "
         f"switch date {row['switch_date'] or 'none'}",
         f"- Result: {len(fails)} failure(s), {len(allowed)} allowed exception(s)"]
    L += [f"- Note: {n}" for n in notes]
    L += ["", "## Summary", "", "| Schema | File | Rows | Failures | Allowed |",
          "|---|---|---:|---:|---:|"]
    for name, r in results.items():
        nf = sum(1 for f in r["failures"] if not f["allowed"])
        na = sum(1 for f in r["failures"] if f["allowed"])
        L.append(f"| {name} | `{r['file']}` | {r['rows']} | {nf} | {na} |")

    def groups(items):
        g = {}
        for f in items:
            g.setdefault((f["schema"], f["check"], f["column"], f["allowed"]), []).append(f)
        return g

    L += ["", "## Failures", ""]
    if not fails:
        L.append("None.")
    for (sch, chk, col, _), fs in sorted(groups(fails).items()):
        L += [f"### {sch}: {chk} ({col or 'row'}), {len(fs)} row(s)", "",
              "| Row | Key | Value |", "|---|---|---|"]
        L += [f"| {_md(f['row_id'])} | {_md(f['row_key'])} | {_md(f['value'])} |" for f in fs[:SHOW]]
        if len(fs) > SHOW:
            L.append(f"| ... | {len(fs) - SHOW} more | |")
        L.append("")
    L += ["", "## Allowed exceptions", ""]
    if not allowed:
        L.append("None.")
    for (sch, chk, col, reason), fs in sorted(groups(allowed).items()):
        L += [f"### {sch}: {chk} ({col or 'row'}), {len(fs)} row(s)", "", f"Reason: {reason}", "",
              ", ".join(_md(f["row_id"]) for f in fs[:SHOW])
              + (f", and {len(fs) - SHOW} more" if len(fs) > SHOW else ""), ""]
    L += ["", "## Information", ""]
    L.append("- master_opposition.csv exact duplicate rows: "
             + ("unreadable" if dupes is None else str(dupes))
             + ". No check reads this; removing them is a decision for Price "
               "(spec 005, Changes on main).")
    return "\n".join(L).rstrip() + "\n"


def write_report(text):
    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def run(only=None, write=True, config_path=None) -> tuple[int, dict]:
    cfg, notes = load_config(config_path)
    tables, missing = load_tables(only, cfg)
    if missing:
        for m in missing:
            print(f"MISSING INPUT: {m}")
        return 2, {"missing": missing}
    rules = cfg.get("allowed_exceptions") or []
    results, h = {}, hashlib.sha256()
    for name, (path, schema, df, ids) in tables.items():
        results[name] = {"file": os.path.relpath(path, ROOT).replace(os.sep, "/"),
                         "rows": len(df), "failures": validate(name, schema, df, ids, rules)}
        h.update(file_sha(path).encode())
    h.update(json.dumps(cfg, sort_keys=True, default=str).encode())
    n_fail = sum(1 for r in results.values() for f in r["failures"] if not f["allowed"])
    n_allow = sum(1 for r in results.values() for f in r["failures"] if f["allowed"])
    today = dt.date.today().isoformat()
    row, append = decide_mode(cfg, read_history(), h.hexdigest(), n_fail, today)
    row["allowed"] = n_allow
    text = render_report(results, row, cfg, notes, master_duplicates())
    for name, r in results.items():
        c = Counter((f["check"], f["column"]) for f in r["failures"] if not f["allowed"])
        print(f"{name}: {r['rows']} rows, "
              + (", ".join(f"{k[0]}({k[1] or 'row'})={v}" for k, v in sorted(c.items())) or "clean"))
    print(f"mode: config {row['mode_config']}, effective {row['mode_effective']}; "
          f"{n_fail} failure(s), {n_allow} allowed; consecutive clean {row['consecutive_clean']}")
    if write:
        write_report(text)
        if append:
            append_history(row)
    code = 1 if row["mode_effective"] == "blocking" and n_fail else 0
    return code, {"results": results, "row": row, "report": text, "appended": append}


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    if not HAVE_PANDERA:
        print("SKIP (pandera not installed): qc/schemas.py checks need pandera>=0.33,<0.34 "
              "from requirements/ci.txt")
        return 0
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    g = globals()
    saved = {k: g[k] for k in ("CONFIG", "CLEAN_CSV", "MASTER_CSV", "SCORES_CSV", "FRAME_CSV",
                               "PROPOSALS_CSV", "KEYMAP_CSV", "REPORT_MD", "HISTORY_CSV")}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            def put(name, text):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                return p

            base = "Source URL,Incident,Date,State,Scope,data_source,lat,lon,is_noise,outcome_defensible"
            g["FRAME_CSV"] = put("frame.csv", "fips,county_name\n19001,x\n09110,y\n")
            g["MASTER_CSV"] = put("master.csv", "a,b\n1,2\n1,2\n3,4\n")
            g["KEYMAP_CSV"] = put("km.csv", "pk,current_id,status\npk_00001,1,active\n")
            g["PROPOSALS_CSV"] = put("p.csv", "id,state,lat,lon,capacity_mw\n1,Iowa,41.5,-93.6,300\n")
            g["REPORT_MD"] = os.path.join(tmp, "report.md")
            g["HISTORY_CSV"] = os.path.join(tmp, "history.csv")
            cfg = {"schema": {"mode": "window", "window_clean_runs": 7,
                              "proposals_columns": ["id", "state", "lat", "lon", "capacity_mw"],
                              "allowed_exceptions": [
                                  {"schema": "clean_feed", "check": "state_normalized",
                                   "when": {"State": "US", "Scope": "federal"}, "reason": "national"}]}}
            g["CONFIG"] = put("cfg.json", json.dumps(cfg))

            # The spec's independent test: three named failures, no more.
            g["CLEAN_CSV"] = put("clean.csv", base + "\n"
                                 "u1,a,2026,US,,x,,,False,pending\n"
                                 "u2,b,2026,IA,,x,41.5,-93.6,True,won\n"
                                 "u3,c,2026,VA,,x,38.9,-77.4,false,advanced_confirmed\n")
            g["SCORES_CSV"] = put("s.csv", "fips,raw_oof_score,calibrated_score,score_decile,"
                                  "has_enacted_restrictive\n1900,0.1,0.1,1,0\n09110,0.5,0.5,5,1\n")
            code, res = run(write=False)
            got = sorted((f["schema"], f["check"]) for r in res["results"].values()
                         for f in r["failures"] if not f["allowed"])
            check("fixture gives exactly three named failures",
                  got == [("clean_feed", "outcome_grade"), ("clean_feed", "state_normalized"),
                          ("county_scores", "fips_format")])
            st = [f for f in res["results"]["clean_feed"]["failures"] if f["check"] == "state_normalized"]
            check("failure carries the row id", st and st[0]["row_id"] == "r2")
            check("report-only window does not block", code == 0
                  and res["row"]["mode_effective"] == "report_only")

            # federal US is a declared exception; a retired CT fips fails the frame
            g["CLEAN_CSV"] = put("clean.csv", base + "\n"
                                 "u1,a,2026,US,federal,x,,,False,pending\n"
                                 "u2,b,2026,IA,,x,0,0,True,pending\n"
                                 "u3,c,2026,VA,,x,38.9,,maybe,pending\n")
            g["SCORES_CSV"] = put("s.csv", "fips,raw_oof_score,calibrated_score,score_decile,"
                                  "has_enacted_restrictive\n09001,0.1,1.2,11,2\n")
            code, res = run(write=False)
            cf = res["results"]["clean_feed"]["failures"]
            check("federal US row is allowed, not failed",
                  any(f["allowed"] == "national" for f in cf)
                  and not any(f["check"] == "state_normalized" and not f["allowed"] for f in cf))
            checks = {f["check"] for f in cf if not f["allowed"]}
            check("null island caught", "not_null_island" in checks)
            check("unpaired coordinates caught", "coords_paired" in checks)
            check("bool token outside true/false/1/0 caught", "bool_token" in checks)
            sc = {f["check"] for f in res["results"]["county_scores"]["failures"]}
            check("retired CT fips fails the frame", "fips_frame" in sc)
            check("score, decile and label ranges caught",
                  {"score_unit", "decile_range", "label_binary"} <= sc)

            # proposals: unkeyed id, unplaceable state, negative capacity, missing column
            g["PROPOSALS_CSV"] = put("p.csv", "id,state,lat,lon\n1,Iowa,41.5,-93.6\n"
                                     "7,Freedonia,41,-93\n")
            code, res = run(only={"proposals"}, write=False)
            pc = {f["check"] for f in res["results"]["proposals"]["failures"]}
            check("proposals: missing column, unkeyed id, unplaceable state",
                  {"columns_present", "id_keyed", "state_normalizable"} <= pc)
            ok_row = [f for f in res["results"]["proposals"]["failures"] if f["check"] == "id_keyed"]
            check("proposals: rows are identified through the key map",
                  ok_row and ok_row[0]["row_id"] == "no_pk" and ok_row[0]["row_key"] == "id 7")
            cfg["schema"]["allowed_exceptions"].append(
                {"schema": "proposals", "check": ["lat_bounds", "lon_bounds", "not_null_island"],
                 "when": {"pk": ["pk_00001"]}, "reason": "source publishes 0,0"})
            put("cfg.json", json.dumps(cfg))
            g["PROPOSALS_CSV"] = put("p.csv", "id,state,lat,lon,capacity_mw\n1,Iowa,0,0,5\n")
            code, res = run(only={"proposals"}, write=False)
            pf = res["results"]["proposals"]["failures"]
            check("pk-keyed exception covers a list of checks",
                  len(pf) == 3 and all(f["allowed"] == "source publishes 0,0" for f in pf))
            g["PROPOSALS_CSV"] = put("p.csv", "id,state,lat,lon,capacity_mw\n1,Iowa,41.5,-93.6,-5\n")
            code, res = run(only={"proposals"}, write=False)
            check("proposals: negative capacity caught",
                  [f["check"] for f in res["results"]["proposals"]["failures"]] == ["capacity_nonneg"])
            check("proposals: pk is the row id",
                  res["results"]["proposals"]["failures"][0]["row_id"] == "pk_00001")

            # window: seven clean runs, then a failure blocks and the switch sticks
            g["CLEAN_CSV"] = put("clean.csv", base + "\nu1,a,2026,IA,,x,41.5,-93.6,True,pending\n")
            g["SCORES_CSV"] = put("s.csv", "fips,raw_oof_score,calibrated_score,score_decile,"
                                  "has_enacted_restrictive\n19001,0.1,0.1,1,0\n")
            g["PROPOSALS_CSV"] = put("p.csv", "id,state,lat,lon,capacity_mw\n1,Iowa,41.5,-93.6,300\n")
            codes = []
            for i in range(7):
                put("clean.csv", base + f"\nu{i},a,2026,IA,,x,41.5,-93.6,True,pending\n")
                codes.append(run()[0])
            hist = read_history()
            check("seven distinct clean runs recorded", len(hist) == 7
                  and hist[-1]["consecutive_clean"] == "7" and set(codes) == {0})
            code, res = run()
            check("identical input does not append", not res["appended"] and len(read_history()) == 7)
            put("clean.csv", base + "\nu9,a,2026,US,,x,41.5,-93.6,True,pending\n")
            code, res = run()
            check("after the window a failure blocks (exit 1)",
                  code == 1 and res["row"]["mode_effective"] == "blocking"
                  and res["row"]["switch_date"] != "")
            put("clean.csv", base + "\nu10,a,2026,IA,,x,41.5,-93.6,True,pending\n")
            code, res = run()
            check("switch date is sticky", res["row"]["mode_effective"] == "blocking" and code == 0)
            cfg["schema"]["mode"] = "report_only"
            put("cfg.json", json.dumps(cfg))
            put("clean.csv", base + "\nu11,a,2026,US,,x,41.5,-93.6,True,pending\n")
            code, res = run()
            check("report_only never blocks", code == 0)
            cfg["schema"]["mode"] = "sometimes"
            put("cfg.json", json.dumps(cfg))
            check("unknown mode reads as report_only", load_config()[0]["mode"] == "report_only")

            raw = open(REPORT_MD, "rb").read()
            check("report written, LF only", raw and b"\r" not in raw)
            check("report has no em-dash", "—".encode() not in raw)
            check("report shows master duplicates as information", b"exact duplicate rows: 1" in raw)
            os.remove(g["SCORES_CSV"])
            check("missing input exits 2", run(write=False)[0] == 2)
    finally:
        g.update(saved)
    print(f"{len(fails)} failure(s)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="validate and print; write nothing")
    ap.add_argument("--only", default="", help="comma-separated: " + ",".join(SCHEMAS))
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not HAVE_PANDERA:
        print("pandera is not installed (requirements/ci.txt pins it); cannot validate.")
        return 2
    only = {s.strip() for s in a.only.split(",") if s.strip()} or None
    code, _ = run(only=only, write=not a.no_write)
    return code


if __name__ == "__main__":
    sys.exit(main())
