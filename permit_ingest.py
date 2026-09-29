"""
permit_ingest.py — normalize a permit-portal export into the dated-baseline
external schema (data/baseline_dated_external.csv).

Permit datasets (county/state portals, Accela/Tyler/eTRAKiT exports, open-data
CSVs) all carry the same essentials under different column names: a project
name, a filing/application date, sometimes a decision date, a status, and a
location. This module maps an arbitrary permit CSV to the schema
baseline_dated.py expects, using a per-source column-map config so each new
source is a few lines of mapping, not new code.

It does NOT scrape. It takes a CSV you already have (downloaded/exported) and
a mapping, and emits validated rows. Rows that fail validation are written to
a rejects file with reasons — never silently dropped.

Schema produced (matches baseline_dated.py EXTERNAL_REQUIRED/OPTIONAL):
  required: source, as_of, name, state, announced_date
  optional: county, capacity_mw, decision_date, status, source_url, operator

Defensibility rules honored:
  - A decision_date is emitted only when the mapped status indicates a TERMINAL
    disposition (approved/denied/withdrawn/issued) — an in-progress permit
    yields no decision date, so the record is later censored, not treated as
    decided. This mirrors the platform's decided-only rule.
  - decision_date counts as verified downstream only if a source_url is
    present; the mapping should supply a per-row or per-source URL.
  - Dates are normalized to YYYY-MM or YYYY-MM-DD; unparseable or year-only
    dates are rejected (never floored).
  - No opposition is asserted — permit records are presumed-unopposed
    comparables unless a name matches a tracked opposed project (flagged for
    manual review, not auto-merged).
  - No scorekeeping vocabulary; leak audit before exit.

Usage:
  python3 permit_ingest.py --in permits_raw.csv --config loudoun.json \\
      --append                      # append to existing external CSV
  python3 permit_ingest.py --in permits_raw.csv --config loudoun.json \\
      --out data/baseline_dated_external.csv

Config JSON (per source):
  {
    "source": "loudoun_permits",
    "state": "VA",                       # or "state_col": "State"
    "as_of": "2026-07-16",               # export date
    "default_source_url": "https://...", # applied when no per-row url_col
    "columns": {
      "name": "Project Name",
      "announced_date": "Application Date",
      "decision_date": "Decision Date",   # optional
      "status": "Status",                 # optional
      "county": "Jurisdiction",           # optional
      "capacity_mw": "Capacity_MW",       # optional
      "operator": "Applicant",            # optional
      "url": "Record URL"                 # optional per-row url
    },
    "terminal_statuses": ["approved","denied","withdrawn","issued","final"]
  }

Generic optional keys (spec 007; each is off unless a config names it, and
none is specific to one source):
    "state_from":  {"column": "Address", "regex": ",\\s*([A-Z]{2})\\s+\\d{5}"}
                   state from the first capture group, when neither "state"
                   nor columns.state gives one; no match -> reject
    "strip_regex": {"operator": "\\s*#\\w+"}   pattern removed from a field
    "match_projects": {"key_map": "data/project_key_map.csv",
                       "strong": 0.60, "soft": 0.34}
                   each row is matched to the permanent project keys with the
                   project_resolution.py name rule (name-token Jaccard, state
                   agreeing). A confirmed or review match is HELD OUT of --out,
                   so a tracked project never enters the baseline a second time
                   as an unopposed comparable, and is written with its pk to
                   baseline_external_matches_<config stem>.csv beside --out.
    "attribution", "sampling_note"   printed on every run; the record of a
                   source's terms of use and of what its rows can stand for.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DEFAULT = os.path.join("data", "baseline_dated_external.csv")
SCHEMA = ["source", "as_of", "name", "state", "announced_date",
          "county", "capacity_mw", "decision_date", "status",
          "source_url", "operator"]

MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def normalize_date(raw: str):
    """Return YYYY-MM or YYYY-MM-DD, or None. Year-only -> None (never floored)."""
    s = (raw or "").strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    if re.match(r"^\d{4}-\d{2}$", s):
        return s
    if re.match(r"^\d{4}$", s):
        return None                      # year-only excluded
    # M/D/YYYY or MM/DD/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        mo, dy, yr = m.groups()
        return f"{yr}-{int(mo):02d}-{int(dy):02d}"
    # "Mar 18, 2025" / "March 18 2025"
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mon, dy, yr = m.groups()
        mm = MONTHS.get(mon[:3].lower())
        if mm:
            return f"{yr}-{mm}-{int(dy):02d}"
    # "YYYY/MM/DD"
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if m:
        yr, mo, dy = m.groups()
        return f"{yr}-{int(mo):02d}-{int(dy):02d}"
    return None


def config_stem(config_path: str) -> str:
    stem = os.path.splitext(os.path.basename(config_path))[0]
    return stem[:-len("_ingest")] if stem.endswith("_ingest") else stem


def ingest(raw: list[dict], cfg: dict, as_of: str) -> tuple[list[dict], list[tuple]]:
    """Map raw rows to SCHEMA. Returns (rows, rejects [(line, reason)])."""
    colmap = cfg.get("columns", {})
    src = cfg.get("source")
    default_url = cfg.get("default_source_url", "")
    terminal = {s.lower() for s in cfg.get(
        "terminal_statuses", ["approved", "denied", "withdrawn", "issued", "final"])}
    state_from = cfg.get("state_from") or {}
    state_rx = re.compile(state_from["regex"]) if state_from.get("regex") else None
    strips = {k: re.compile(v) for k, v in (cfg.get("strip_regex") or {}).items()}

    def col(row, key):
        return (row.get(colmap.get(key, ""), "") or "").strip()

    out_rows, rejects = [], []
    for i, r in enumerate(raw, 2):
        name = col(r, "name")
        ann = normalize_date(col(r, "announced_date"))
        state = (cfg.get("state") or col(r, "state")).strip()
        if not state and state_rx:
            m = state_rx.search((r.get(state_from.get("column", ""), "") or "").strip())
            state = us_state(m.group(1)) if m else ""
        if not name:
            rejects.append((i, "missing name")); continue
        if not ann:
            rejects.append((i, f"unusable announced_date {col(r,'announced_date')!r}")); continue
        if not state:
            rejects.append((i, "missing state")); continue

        status = col(r, "status")
        dec = normalize_date(col(r, "decision_date"))
        # only keep a decision date when the status is terminal
        if dec and status and status.lower() not in terminal:
            dec = ""                     # in-progress: no decision date
        url = col(r, "url") or default_url

        row = {
            "source": src, "as_of": as_of, "name": name, "state": state,
            "announced_date": ann,
            "county": col(r, "county"),
            "capacity_mw": col(r, "capacity_mw"),
            "decision_date": dec or "",
            "status": status,
            "source_url": url,
            "operator": col(r, "operator"),
        }
        for field, rx in strips.items():
            if field in row:
                row[field] = rx.sub("", row[field]).strip()
        out_rows.append(row)
    return out_rows, rejects


def us_state(text: str) -> str:
    """Two-letter code for a captured state, a code or a full name; '' when it
    names no US state. A capture like "Papillion Nebraska" falls back to its
    trailing words, and "NW" (a street suffix) is refused rather than kept."""
    from project_resolution import norm_state  # noqa: PLC0415

    words = (text or "").split()
    for n in range(len(words), 0, -1):
        st = norm_state(" ".join(words[-n:]))
        if st:
            return st
    return ""


MATCH_COLS = ["source", "name", "state", "announced_date", "match_tier", "pk",
              "key_name", "name_jaccard", "note"]


def match_projects(rows: list[dict], key_rows: list[dict], strong: float = 0.60,
                   soft: float = 0.34) -> tuple[list[dict], list[dict]]:
    """Split rows into (unmatched, held). The rule is project_resolution.py's:
    name tokens (its stopwords) compared by Jaccard, only within the same
    state. One key at or above `strong` is confirmed; anything else at or
    above `soft`, or two strong keys, is review. Both are held out."""
    from project_resolution import jaccard, name_tokens, norm_state  # noqa: PLC0415

    by_state: dict[str, list[tuple[dict, frozenset]]] = {}
    for k in key_rows:
        by_state.setdefault(norm_state(k.get("state", "")), []).append(
            (k, name_tokens(k.get("name", ""))))
    unmatched, held = [], []
    for r in rows:
        toks = name_tokens(r["name"])
        scored = sorted(((jaccard(toks, kt), k) for k, kt in
                         by_state.get(norm_state(r["state"]), [])),
                        key=lambda x: (-x[0], x[1].get("pk", "")))
        strong_hits = [(j, k) for j, k in scored if j >= strong]
        soft_hits = [(j, k) for j, k in scored if j >= soft]
        if not soft_hits:
            unmatched.append(r)
            continue
        # One key at or above `strong` is the match even when weaker keys share
        # a word with it ("Google Cedar Rapids" 1.00 beside "QTS Cedar Rapids"
        # 0.50). Two strong keys, or only soft ones, go to a person.
        tier = "confirmed" if len(strong_hits) == 1 else "review"
        hits = strong_hits[:1] if tier == "confirmed" else soft_hits
        held.append({
            "source": r["source"], "name": r["name"], "state": r["state"],
            "announced_date": r["announced_date"], "match_tier": tier,
            "pk": "; ".join(k["pk"] for _, k in hits),
            "key_name": "; ".join(k.get("name", "") for _, k in hits),
            "name_jaccard": "; ".join(f"{j:.2f}" for j, _ in hits),
            "note": ("held out of baseline: tracked project" if tier == "confirmed"
                     else "held out of baseline: possible tracked project, needs review"),
        })
    return unmatched, held


def read_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile")
    ap.add_argument("--config")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--append", action="store_true",
                    help="append to --out instead of overwriting")
    ap.add_argument("--as-of", dest="as_of_override", default=None,
                    help="override the config's as_of (e.g. the run date, "
                         "so a scheduled fetch does not need a hand-edited "
                         "config every time)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.infile or not args.config:
        ap.error("--in and --config are required")

    if not os.path.exists(args.infile):
        print(f"ERROR: input {args.infile} not found")
        return 1
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)

    src = cfg.get("source")
    as_of = args.as_of_override or cfg.get("as_of")
    if not src or not as_of:
        print("ERROR: config must set 'source' and 'as_of'")
        return 1
    for k in ("attribution", "sampling_note"):
        if cfg.get(k):
            print(f"{k}: {cfg[k]}")

    out_rows, rejects = ingest(read_rows(args.infile), cfg, as_of)

    held = []
    mp = cfg.get("match_projects")
    if mp:
        key_map = mp.get("key_map", "data/project_key_map.csv")
        if not os.path.isabs(key_map):
            key_map = os.path.join(ROOT, key_map)
        total = len(out_rows)
        out_rows, held = match_projects(out_rows, read_rows(key_map),
                                        float(mp.get("strong", 0.60)),
                                        float(mp.get("soft", 0.34)))
        match_path = os.path.join(os.path.dirname(args.out) or ".",
                                  f"baseline_external_matches_{config_stem(args.config)}.csv")
        with open(match_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=MATCH_COLS, lineterminator="\n")
            w.writeheader()
            w.writerows(held)
        confirmed = sum(1 for h in held if h["match_tier"] == "confirmed")
        share = (len(held) / total * 100) if total else 0.0
        print(f"{src}: matched to tracked projects {len(held)} of {total} "
              f"({share:.0f}%): {confirmed} confirmed, {len(held) - confirmed} "
              f"for review; held out of the baseline -> {match_path}")

    # write / append
    mode = "a" if (args.append and os.path.exists(args.out)) else "w"
    existing_keys = set()
    if mode == "a":
        for r in read_rows(args.out):
            existing_keys.add((r["source"], r["name"], r["announced_date"]))
    deduped = [r for r in out_rows
               if (r["source"], r["name"], r["announced_date"]) not in existing_keys]

    with open(args.out, mode, newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SCHEMA, lineterminator="\n")
        if mode == "w":
            w.writeheader()
        w.writerows(deduped)

    if rejects:
        rej_path = args.out.replace(".csv", "_rejects.csv")
        with open(rej_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n"); w.writerow(["line", "reason"]); w.writerows(rejects)

    print(f"{src}: {len(deduped)} rows written ({mode}), "
          f"{len(out_rows)-len(deduped)} dedup-skipped, {len(rejects)} rejected")
    if rejects:
        print(f"  rejects -> {os.path.basename(args.out).replace('.csv','_rejects.csv')}")

    pat = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)
    hits = [i for i, l in enumerate(open(args.out, encoding="utf-8"), 1) if pat.search(l)]
    if hits:
        print("LEAK AUDIT: token in output rows", hits[:5],
              "(check source name/status fields)")
    else:
        print("leak audit: clean")
    print("Next: run `python3 baseline_dated.py` to fold these into the dated frame.")
    return 0


def selftest() -> int:
    """Offline. The Epoch fixture (spec 007, US4) through the real Epoch
    ingest config, plus the pre-existing date and decision-date rules."""
    import tempfile  # noqa: PLC0415

    ok = True

    def check(cond, label):
        nonlocal ok
        print(f"  {'pass' if cond else 'FAIL'} {label}")
        ok = ok and bool(cond)

    fx = os.path.join(ROOT, "tests", "fixtures", "coverage_expansion")
    with open(os.path.join(ROOT, "configs", "epoch_frontier_dc_ingest.json"),
              encoding="utf-8") as fh:
        cfg = json.load(fh)
    raw = read_rows(os.path.join(fx, "epoch_data_centers.csv"))
    # What fetch_permits.py's earliest_date_from adds before ingest.
    first = {}
    for t in read_rows(os.path.join(fx, "epoch_timelines.csv")):
        d = t["Date"][:10]
        if t["Data center"] not in first or d < first[t["Data center"]]:
            first[t["Data center"]] = d
    for r in raw:
        r["first_dated_observation"] = first.get(r["Name"], "")

    rows, rejects = ingest(raw, cfg, "2026-09-29")
    check(us_state("Papillion Nebraska") == "NE" and us_state("New Jersey") == "NJ"
          and us_state("TN") == "TN" and us_state("NW") == "",
          "state capture accepts codes and full names, refuses street suffixes")
    check(len(raw) == 10 and len(rows) == 9 and len(rejects) == 1,
          "10-row Epoch fixture: 9 US rows mapped, 1 rejected")
    check(rejects and rejects[0][1] == "missing state",
          "the non-US campus is rejected for having no US state, with a reason")
    check(all(list(r) == SCHEMA for r in rows), "every row has the external schema, in order")
    check(all(r["source"] == "Epoch AI Frontier Data Centers (CC-BY 4.0)" for r in rows),
          "the source field carries the Epoch attribution string")
    check("CC-BY" in cfg.get("attribution", "") and "frontier-scale" in cfg.get("sampling_note", ""),
          "the config records the CC-BY attribution and the very-large-campus limit")
    col2 = next(r for r in rows if r["name"] == "Colossus 2")
    check(col2["state"] == "TN" and col2["announced_date"] == "2025-02-28"
          and col2["operator"] == "SpaceXAI" and col2["capacity_mw"] == "946",
          "state from the address, earliest dated observation, confidence tag stripped")
    check(all(re.match(r"^\d{4}-\d{2}(-\d{2})?$", r["announced_date"]) for r in rows),
          "announced dates are normalized")

    keys = read_rows(os.path.join(fx, "epoch_key_map.csv"))
    unmatched, held = match_projects(rows, keys)
    by = {h["name"]: h for h in held}
    check(by.get("OpenAI Stargate Abilene", {}).get("match_tier") == "confirmed"
          and by["OpenAI Stargate Abilene"]["pk"] == "pk_90002",
          "a tracked project is matched to its permanent key and confirmed")
    check(by.get("Colossus 2", {}).get("match_tier") == "review"
          and by["Colossus 2"]["pk"] == "pk_90001",
          "a partial name match in the same state is held for review, not linked")
    check(len(unmatched) + len(held) == len(rows)
          and not ({h["name"] for h in held} & {r["name"] for r in unmatched}),
          "every row is either held or a candidate, never both")
    check(all(h["name"] != "Google Columbus" for h in held),
          "a campus that shares no name tokens with a same-state key stays a candidate")

    with tempfile.TemporaryDirectory() as td:
        cand = os.path.join(td, "cand.csv")
        with open(cand, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(raw[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(raw)
        cfg_path = os.path.join(td, "epoch_frontier_dc_ingest.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({**cfg, "match_projects": {**cfg["match_projects"],
                       "key_map": os.path.join(fx, "epoch_key_map.csv")}}, fh)
        out = os.path.join(td, "baseline_dated_external.csv")
        argv = sys.argv
        try:
            sys.argv = ["permit_ingest.py", "--in", cand, "--config", cfg_path,
                        "--out", out, "--append", "--as-of", "2026-09-29"]
            import contextlib  # noqa: PLC0415
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main()
                rc2 = main()
        finally:
            sys.argv = argv
        written = read_rows(out)
        matches = read_rows(os.path.join(td, "baseline_external_matches_epoch_frontier_dc.csv"))
        check(rc == 0 and rc2 == 0 and len(written) == 7 and len(matches) == 2,
              "end to end: 7 candidates appended, 2 held with their keys")
        check(not ({m["name"] for m in matches} & {r["name"] for r in written}),
              "a held project never reaches the baseline file")
        with open(out, "rb") as fh:
            check(b"\r\n" not in fh.read(), "output is LF")

    # Pre-existing rules, now under test.
    check(normalize_date("2024") is None, "a year-only date is rejected, never floored")
    check(normalize_date("3/18/2025") == "2025-03-18" and normalize_date("Mar 18, 2025") == "2025-03-18",
          "common date formats normalize")
    plain = {"source": "t", "state": "VA", "columns": {"name": "n", "announced_date": "a",
             "decision_date": "d", "status": "s"}, "terminal_statuses": ["approved"]}
    r2, _ = ingest([{"n": "X", "a": "2024-01-02", "d": "2024-05-01", "s": "Under review"},
                    {"n": "Y", "a": "2024-01-02", "d": "2024-05-01", "s": "Approved"}],
                   plain, "2026-09-29")
    check(r2[0]["decision_date"] == "" and r2[1]["decision_date"] == "2024-05-01",
          "a decision date is kept only for a terminal status")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
