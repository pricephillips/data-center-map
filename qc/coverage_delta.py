#!/usr/bin/env python3
"""
qc/coverage_delta.py

A column's coverage cannot silently collapse (spec 005, US3).

On 2026-09-10 a scraper field rename took data/proposals.csv capacity_mw from
123 non-null rows to 3. It emptied the cost-translation demo, and no check
failed. This module profiles non-null counts per column and compares each
declared file with its last snapshot. It fails when a declared column drops
by more than its threshold. The default threshold is 20 percent, and every
threshold is declared per column in configs/data_quality.json (FR-004).

It also flags values beyond a robust z-score of 6 (modified z,
0.6745 * (x - median) / MAD) on Megawatts, Investment Million USD and Acreage.
Those flags are for review only and never fail the run.

Baselines (research D8)
  The snapshot CSVs themselves are not kept, and snapshots/manifest.csv holds
  only date, sha256 and row count, so this module keeps one profile per
  (file, sha256) in snapshots/coverage_profiles.csv.
  - The clean feed is compared with the most recent manifest snapshot whose
    sha differs from the current file and has a stored profile.
  - Other files are compared with their most recent stored profile of a
    different sha.
  - When a snapshot has no profile, the most recent available one is used
    and the report says which.

Reads   configs/data_quality.json, snapshots/manifest.csv, declared files
Writes  data/coverage_delta_report.md, snapshots/coverage_profiles.csv (append)

Usage
  python qc/coverage_delta.py                  compare every declared file, write report, append profiles
  python qc/coverage_delta.py --no-write       compare only
  python qc/coverage_delta.py --before A.csv --after B.csv [--file data/proposals.csv]
  python qc/coverage_delta.py --before-profile P.json [--after-profile Q.json] [--file ...]
  python qc/coverage_delta.py --selftest

Exit codes: 0 no declared column dropped past its threshold; 1 at least one
did (each line names the file, the column and both counts); 2 missing input.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import statistics as st
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "configs", "data_quality.json")
MANIFEST = os.path.join(ROOT, "snapshots", "manifest.csv")
STORE = os.path.join(ROOT, "snapshots", "coverage_profiles.csv")
REPORT = os.path.join(ROOT, "data", "coverage_delta_report.md")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "coverage_delta", "proposals_2026-09-10.json")

STORE_COLS = ["date", "sha256", "file", "rows", "column", "non_null"]
DEFAULTS = {"default_threshold": 0.20,
            "files": {"master_opposition_clean.csv": {"*": 0.20},
                      "data/proposals.csv": {"*": 0.20}},
            "robust_z": {"threshold": 6.0, "columns": {
                "master_opposition_clean.csv": ["Megawatts", "Investment Million USD", "Acreage"]}}}
_NUM_RE = re.compile(r"^\s*\$?\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z][A-Za-z .]*)?$")
MAX_OUTLIERS = 50


# --------------------------------------------------------------------------
# config and profiles
# --------------------------------------------------------------------------

def load_config(path=None) -> tuple[dict, list[str]]:
    notes = []
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        cfg.update(json.load(open(path or CONFIG, encoding="utf-8")).get("coverage_delta", {}))
    except (OSError, ValueError) as exc:
        notes.append(f"config unreadable ({type(exc).__name__}); built-in defaults used")
    return cfg, notes


def profile_bytes(raw: bytes, file: str, date: str = "") -> dict:
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    cols = list(rows[0].keys()) if rows else \
        next(csv.reader(io.StringIO(raw.decode("utf-8-sig"))), [])
    return {"file": file, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows),
            "date": date or dt.date.today().isoformat(),
            "columns": {c: sum(1 for r in rows if (r.get(c) or "").strip()) for c in cols}}


def profile_file(path: str, file: str) -> dict:
    with open(path, "rb") as fh:
        return profile_bytes(fh.read(), file)


def read_store() -> list[dict]:
    """Stored profiles, oldest first, one per (file, sha256)."""
    out, idx = [], {}
    try:
        with open(STORE, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                key = (r["file"], r["sha256"])
                if key not in idx:
                    idx[key] = {"file": r["file"], "sha256": r["sha256"], "date": r["date"],
                                "rows": int(r["rows"]), "columns": {}}
                    out.append(idx[key])
                idx[key]["columns"][r["column"]] = int(r["non_null"])
    except OSError:
        pass
    return out


def append_store(profiles: list[dict]):
    new = not os.path.exists(STORE)
    with open(STORE, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(STORE_COLS)
        for p in profiles:
            for col, n in p["columns"].items():
                w.writerow([p["date"], p["sha256"], p["file"], p["rows"], col, n])


def read_manifest() -> list[dict]:
    try:
        with open(MANIFEST, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def pick_baseline(current: dict, store: list[dict], manifest: list[dict]) -> tuple[dict | None, str]:
    """The profile to compare against, and where it came from."""
    mine = [p for p in store if p["file"] == current["file"] and p["sha256"] != current["sha256"]]
    by_sha = {p["sha256"]: p for p in mine}
    base = os.path.basename(current["file"])
    snaps = [m for m in manifest if m.get("file") == base and m.get("sha256") != current["sha256"]]
    if snaps:
        latest = snaps[-1]
        for m in reversed(snaps):
            if m["sha256"] in by_sha:
                src = f"manifest snapshot {m['date']}"
                if m is not latest:
                    src += (f"; the latest snapshot ({latest['date']} sha "
                            f"{latest['sha256'][:12]}) has no stored profile")
                return by_sha[m["sha256"]], src
        if mine:
            last = snaps[-1]
            return mine[-1], (f"profile store (manifest snapshot {last['date']} "
                              f"sha {last['sha256'][:12]} has no stored profile)")
    if mine:
        return mine[-1], "profile store"
    return None, "first profile of this file; recorded as the baseline"


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------

def compare(before: dict, after: dict, spec: dict, default: float) -> list[dict]:
    """Declared-column findings. '*' declares every column of the baseline."""
    declared = [c for c in spec if c != "*"]
    if "*" in spec:
        declared = list(dict.fromkeys(list(before["columns"]) + declared))
    out = []
    for col in declared:
        thr = float(spec.get(col, spec.get("*", default)))
        b = before["columns"].get(col)
        a = after["columns"].get(col)
        if b is None:
            continue                       # new column: nothing to compare with
        if a is None:
            status, drop, note = ("fail" if b > 0 else "ok"), (1.0 if b > 0 else 0.0), "column missing"
            a = 0
        else:
            drop = (b - a) / b if b > 0 else 0.0
            status, note = ("fail" if b > 0 and drop > thr else "ok"), ""
        out.append({"file": after["file"], "column": col, "before": b, "after": a,
                    "drop_share": round(drop, 4), "threshold": thr, "status": status, "note": note})
    out.sort(key=lambda f: (f["status"] != "fail", -f["drop_share"], f["column"]))
    return out


def parse_number(v):
    m = _NUM_RE.match(str(v or "").replace(",", ""))
    return float(m.group(1)) if m else None


def robust_z(values: list[float]) -> list[float]:
    if len(values) < 3:
        return [0.0] * len(values)
    med = st.median(values)
    mad = st.median(abs(x - med) for x in values)
    if mad > 0:
        return [0.6745 * (x - med) / mad for x in values]
    meanad = sum(abs(x - med) for x in values) / len(values)
    if meanad > 0:
        return [(x - med) / (1.2533 * meanad) for x in values]
    return [0.0] * len(values)


def outliers(raw: bytes, file: str, columns: list[str], threshold: float) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    out = []
    for col in columns:
        vals = [(i, parse_number(r.get(col))) for i, r in enumerate(rows)]
        vals = [(i, x) for i, x in vals if x is not None]
        for (i, x), z in zip(vals, robust_z([x for _, x in vals])):
            if abs(z) > threshold:
                out.append({"file": file, "column": col, "row_id": f"r{i + 2}",
                            "value": rows[i].get(col, ""), "robust_z": round(z, 1)})
    out.sort(key=lambda o: -abs(o["robust_z"]))
    return out


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def _md(v) -> str:
    return str(v).replace("|", "/").replace("\n", " ").replace("\r", " ")[:80]


def render(sections: list[dict], flags: list[dict], notes: list[str]) -> str:
    nfail = sum(1 for s in sections for f in s["findings"] if f["status"] == "fail")
    L = ["# Coverage Delta Report", "",
         "Generated by qc/coverage_delta.py (spec 005). Do not edit by hand. Thresholds: "
         "configs/data_quality.json.", "",
         f"- Run (UTC): {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
         f"- Result: {nfail} column(s) dropped past threshold; "
         f"{len(flags)} value(s) flagged for review (robust z)"]
    L += [f"- Note: {n}" for n in notes]
    for s in sections:
        cur, base = s["current"], s["baseline"]
        L += ["", f"## {cur['file']}", "",
              f"- Current: sha {cur['sha256'][:12]}, {cur['rows']} rows.",
              "- Baseline: " + (f"{base['date']} sha {base['sha256'][:12]}, {base['rows']} rows"
                                if base else "none") + f" ({s['source']})", ""]
        if not s["findings"]:
            L.append("No declared column to compare.")
            continue
        L += ["| Column | Before | After | Drop | Threshold | Status |", "|---|---:|---:|---:|---:|---|"]
        for f in s["findings"]:
            st_ = f["status"].upper() + (f" ({f['note']})" if f["note"] else "")
            L.append(f"| {_md(f['column'])} | {f['before']} | {f['after']} | "
                     f"{f['drop_share']:.1%} | {f['threshold']:.0%} | {st_} |")
    L += ["", "## Outliers (robust z above threshold, review only)", ""]
    if not flags:
        L.append("None.")
    else:
        L += ["| File | Column | Row | Value | z |", "|---|---|---|---|---:|"]
        L += [f"| {o['file']} | {o['column']} | {o['row_id']} | {_md(o['value'])} | {o['robust_z']} |"
              for o in flags[:MAX_OUTLIERS]]
        if len(flags) > MAX_OUTLIERS:
            L.append(f"| ... | {len(flags) - MAX_OUTLIERS} more | | | |")
    return "\n".join(L).rstrip() + "\n"


def write_report(text: str):
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def fail_lines(findings) -> list[str]:
    return [f"FAIL {f['file']} {f['column']} {f['before']} -> {f['after']} "
            f"(drop {f['drop_share']:.1%} > {f['threshold']:.0%})"
            + (f" [{f['note']}]" if f["note"] else "") for f in findings if f["status"] == "fail"]


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def run(write=True, config_path=None) -> tuple[int, str]:
    cfg, notes = load_config(config_path)
    store, manifest = read_store(), read_manifest()
    sections, flags, new_profiles = [], [], []
    for file, spec in cfg.get("files", {}).items():
        path = os.path.join(ROOT, file)
        if not os.path.exists(path):
            print(f"MISSING INPUT: {file}")
            return 2, ""
        with open(path, "rb") as fh:
            raw = fh.read()
        cur = profile_bytes(raw, file)
        base, source = pick_baseline(cur, store, manifest)
        findings = compare(base, cur, spec, cfg["default_threshold"]) if base else []
        sections.append({"current": cur, "baseline": base, "source": source, "findings": findings})
        if not any(p["file"] == file and p["sha256"] == cur["sha256"] for p in store):
            new_profiles.append(cur)
        rz = cfg.get("robust_z", {})
        flags += outliers(raw, file, rz.get("columns", {}).get(file, []), float(rz.get("threshold", 6.0)))
    text = render(sections, flags, notes)
    lines = [ln for s in sections for ln in fail_lines(s["findings"])]
    for s in sections:
        print(f"{s['current']['file']}: baseline {s['source']}; "
              f"{sum(1 for f in s['findings'] if f['status'] == 'fail')} fail")
    for ln in lines:
        print(ln)
    print(f"{len(flags)} outlier(s) flagged for review")
    if write:
        write_report(text)
        if new_profiles:
            append_store(new_profiles)
    return (1 if lines else 0), text


def compare_explicit(before: dict, after: dict, file: str, config_path=None) -> int:
    cfg, _ = load_config(config_path)
    spec = cfg.get("files", {}).get(file, {"*": cfg["default_threshold"]})
    after = dict(after, file=file)
    lines = fail_lines(compare(before, after, spec, cfg["default_threshold"]))
    print(f"{file}: {before['rows']} -> {after['rows']} rows; {len(lines)} fail")
    for ln in lines:
        print(ln)
    return 1 if lines else 0


def load_profile_json(path: str) -> dict:
    return json.load(open(path, encoding="utf-8"))


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    fx = load_profile_json(FIXTURE)
    f = compare(fx["before"], fx["after"], {"*": 0.2, "capacity_mw": 0.2}, 0.2)
    cap = [x for x in f if x["column"] == "capacity_mw"][0]
    check("2026-09-10 replay fails on capacity_mw 123 -> 3",
          cap["status"] == "fail" and cap["before"] == 123 and cap["after"] == 3)
    check("failure line names the column and both counts",
          any("capacity_mw 123 -> 3" in ln for ln in fail_lines(f)))
    check("unchanged columns pass", [x for x in f if x["column"] == "id"][0]["status"] == "ok")
    p = lambda cols, rows=100: {"file": "t.csv", "sha256": "s", "rows": rows, "date": "d", "columns": cols}
    check("15 percent drop passes at 20", compare(p({"a": 100}), p({"a": 85}), {"*": 0.2}, 0.2)[0]["status"] == "ok")
    check("21 percent drop fails at 20", compare(p({"a": 100}), p({"a": 79}), {"*": 0.2}, 0.2)[0]["status"] == "fail")
    check("per-column threshold overrides *",
          compare(p({"a": 100}), p({"a": 79}), {"*": 0.2, "a": 0.5}, 0.2)[0]["status"] == "ok")
    miss = compare(p({"a": 10}), p({}), {"*": 0.2}, 0.2)[0]
    check("a declared column that disappears fails", miss["status"] == "fail" and miss["note"])
    check("undeclared column is not gated without *", compare(p({"a": 10, "b": 9}), p({"a": 10, "b": 0}),
                                                               {"a": 0.2}, 0.2)[0]["column"] == "a")
    check("growth from zero is fine", compare(p({"a": 0}), p({"a": 5}), {"*": 0.2}, 0.2)[0]["status"] == "ok")

    # outliers: a planted 10,000 MW among values near 100; MAD 0 does not crash
    vals = [100, 110, 95, 120, 105, 98, 102, 101, 99, 97]
    raw = ("Megawatts,Acreage\n" + "".join(f"{v} MW,40\n" for v in vals) + "\"10,000\",40\n").encode()
    o = outliers(raw, "t.csv", ["Megawatts", "Acreage"], 6.0)
    check("planted 10,000 MW flagged", len(o) == 1 and o[0]["row_id"] == "r12")
    check("constant column (MAD 0) flags nothing", not [x for x in o if x["column"] == "Acreage"])
    check("parse tolerates units and commas", parse_number("1,200 acres") == 1200.0
          and parse_number("$50") == 50.0 and parse_number("n/a") is None)

    g = globals()
    saved = {k: g[k] for k in ("CONFIG", "MANIFEST", "STORE", "REPORT", "ROOT")}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path, man_path = os.path.join(tmp, "cfg.json"), os.path.join(tmp, "manifest.csv")
            g.update(ROOT=tmp, STORE=os.path.join(tmp, "profiles.csv"),
                     REPORT=os.path.join(tmp, "report.md"), MANIFEST=man_path, CONFIG=cfg_path)
            with open(cfg_path, "w") as fh:
                json.dump({"coverage_delta": {"default_threshold": 0.2,
                                              "files": {"feed.csv": {"*": 0.2}},
                                              "robust_z": {"threshold": 6, "columns": {}}}}, fh)
            feed = os.path.join(tmp, "feed.csv")

            def put_feed(n_mw):
                with open(feed, "w", newline="\n") as fh:
                    fh.write("id,mw\n" + "".join(f"{i},{'5' if i < n_mw else ''}\n" for i in range(10)))
                return profile_file(feed, "feed.csv")

            first = put_feed(10)
            code, text = run()
            check("first run: no baseline, recorded", code == 0 and "none (first profile" in text
                  and len(read_store()) == 1)
            with open(man_path, "w", newline="\n") as fh:
                fh.write(f"date,sha256,rows,file\n2026-09-01,{first['sha256']},10,feed.csv\n")
            put_feed(10)
            code, _ = run()
            check("same file again: no duplicate profile", len(read_store()) == 1)
            cur = put_feed(2)
            with open(man_path, "a", newline="\n") as fh:
                fh.write(f"2026-09-02,{cur['sha256']},10,feed.csv\n")
            code, text = run()
            check("collapse against the manifest snapshot fails (exit 1)",
                  code == 1 and "manifest snapshot 2026-09-01" in text)
            check("manifest selection skips the current sha", "FAIL (" not in text and "| mw | 10 | 2 |" in text)
            put_feed(9)
            with open(man_path, "a", newline="\n") as fh:
                fh.write("2026-09-03,ffff,10,feed.csv\n")
            code, text = run(write=False)
            check("snapshot without a profile falls back and says so", "has no stored profile" in text)
            raw_r = open(g["REPORT"], "rb").read()
            check("report LF only, no em-dash", b"\r" not in raw_r and chr(0x2014).encode() not in raw_r)
            os.remove(feed)
            check("missing input exits 2", run(write=False)[0] == 2)
    finally:
        g.update(saved)
    check("explicit fixture compare exits 1",
          compare_explicit(fx["before"], fx["after"], "data/proposals.csv") == 1)
    print(f"{len(fails)} failure(s)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Column coverage delta gate (spec 005 US3)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--before", help="CSV to use as the baseline")
    ap.add_argument("--after", help="CSV to compare with the baseline")
    ap.add_argument("--before-profile", help="JSON profile, or a fixture holding before and after")
    ap.add_argument("--after-profile", help="JSON profile")
    ap.add_argument("--file", default="data/proposals.csv",
                    help="declared file whose thresholds apply to an explicit compare")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.before or a.after:
        if not (a.before and a.after and os.path.exists(a.before) and os.path.exists(a.after)):
            print("MISSING INPUT: --before and --after must both name existing files")
            return 2
        return compare_explicit(profile_file(a.before, a.file), profile_file(a.after, a.file), a.file)
    if a.before_profile:
        bp = load_profile_json(a.before_profile)
        if a.after_profile:
            return compare_explicit(bp, load_profile_json(a.after_profile), a.file)
        return compare_explicit(bp["before"], bp["after"], a.file)
    return run(write=not a.no_write)[0]


if __name__ == "__main__":
    sys.exit(main())
