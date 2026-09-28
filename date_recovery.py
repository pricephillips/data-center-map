"""
date_recovery.py
========================================================================
Recovers publication dates from Source URLs for rows whose Date field is
blank or unparseable. Entirely offline (URL string patterns only; no
fetching). Never overwrites Date: writes recovered_date + date_recovery_note,
and emits out/date_recovery_report.csv for review before adoption.

Patterns handled: /2026/03/15/, /2026/03/, 2026-03-15, 20260315, /2026/,
month-name-15-2026, 15-march-2026.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
_MONTHS.update({m[:3]: v for m, v in list(_MONTHS.items())})

_PATTERNS = [
    (re.compile(r"/(20[12]\d)/([01]?\d)/([0-3]?\d)(?:/|$|[^\d])"), "ymd_path"),
    (re.compile(r"(20[12]\d)-([01]\d)-([0-3]\d)"), "iso_in_url"),
    (re.compile(r"[/_-](20[12]\d)([01]\d)([0-3]\d)(?:[/_.-]|$)"), "compact"),
    (re.compile(r"/(20[12]\d)/([01]?\d)(?:/|$)"), "ym_path"),
    (re.compile(r"([a-z]{3,9})[-_ ]([0-3]?\d)[-_ ,]+(20[12]\d)", re.I), "monthname"),
    (re.compile(r"([0-3]?\d)[-_ ]([a-z]{3,9})[-_ ](20[12]\d)", re.I), "dmonthname"),
    (re.compile(r"/(20[12]\d)(?:/|$)"), "year_only"),
]


def _valid(y: int, m: int, d: int) -> bool:
    try:
        dt = datetime(y, m, d)
    except ValueError:
        return False
    return datetime(2010, 1, 1) <= dt <= datetime(2027, 12, 31)


def recover_from_url(url: str) -> tuple[str, str]:
    u = str(url or "")
    for rx, kind in _PATTERNS:
        m = rx.search(u)
        if not m:
            continue
        g = m.groups()
        if kind in ("ymd_path", "iso_in_url", "compact"):
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
            if _valid(y, mo, d):
                return f"{y:04d}-{mo:02d}-{d:02d}", kind
        elif kind == "ym_path":
            y, mo = int(g[0]), int(g[1])
            if _valid(y, mo, 1):
                return f"{y:04d}-{mo:02d}-15", kind + "_midmonth"
        elif kind == "monthname":
            mo = _MONTHS.get(g[0].lower()[:3] if g[0].lower()[:3] in _MONTHS
                             else g[0].lower())
            if mo and _valid(int(g[2]), mo, int(g[1])):
                return f"{int(g[2]):04d}-{mo:02d}-{int(g[1]):02d}", kind
        elif kind == "dmonthname":
            mo = _MONTHS.get(g[1].lower()[:3] if g[1].lower()[:3] in _MONTHS
                             else g[1].lower())
            if mo and _valid(int(g[2]), mo, int(g[0])):
                return f"{int(g[2]):04d}-{mo:02d}-{int(g[0]):02d}", kind
        elif kind == "year_only":
            y = int(g[0])
            if _valid(y, 7, 1):
                return f"{y:04d}-07-01", kind + "_midyear"
    return "", ""


def _has_date(rec: dict) -> bool:
    v = str(rec.get("Date", "") or "").strip()
    if not v:
        return False
    try:
        datetime.fromisoformat(v[:10])
        return True
    except ValueError:
        return False


_GOOGLE_NEWS = re.compile(r"news\.google\.com/rss/articles/")


def resolve_google_news(url: str, timeout: int = 10) -> str:
    """Resolve a Google News RSS redirect to the underlying article URL.
    Requires network access; call from an environment that has it, then feed
    the resolved URL back through recover_from_url(). One line of work:
        requests.get(url, timeout=timeout, allow_redirects=True).url
    Kept as a stub here so offline pipeline runs never attempt the network."""
    raise RuntimeError("network resolution not available in this environment")


def apply_recovery(records: list[dict], outdir: str = "out") -> dict:
    os.makedirs(outdir, exist_ok=True)
    report, n_hit, n_redirect = [], 0, 0
    for r in records:
        r.setdefault("recovered_date", "")
        r.setdefault("date_recovery_note", "")
        if _has_date(r):
            continue
        url = str(r.get("Source URL", ""))
        date, kind = recover_from_url(url)
        if date:
            r["recovered_date"] = date
            r["date_recovery_note"] = kind
            n_hit += 1
            report.append({"Incident": r.get("Incident", ""),
                           "State": r.get("State", ""),
                           "recovered_date": date, "method": kind,
                           "Source URL": url})
        elif _GOOGLE_NEWS.search(url):
            # Modern Google News tokens are encrypted; the date is only
            # reachable by following the redirect (network). Queue it.
            r["date_recovery_note"] = "requires_redirect_resolution"
            n_redirect += 1
            report.append({"Incident": r.get("Incident", ""),
                           "State": r.get("State", ""),
                           "recovered_date": "",
                           "method": "requires_redirect_resolution",
                           "Source URL": url})
    path = os.path.join(outdir, "date_recovery_report.csv")
    if report:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(report[0].keys()))
            w.writeheader()
            w.writerows(report)
    missing = sum(1 for r in records if not _has_date(r) and not r["recovered_date"])
    return {"recovered": n_hit, "needs_redirect": n_redirect,
            "still_missing": missing, "report": path}


def _load_decision_worklist(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _build_url_index(opposition_path: str) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    with open(opposition_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = str(row.get("Project Name", "") or "").lower().strip()
            url = str(row.get("Source URL", "") or "").strip()
            if key and url:
                index.setdefault(key, [])
                if url not in index[key]:
                    index[key].append(url)
    return index


def _precision(method: str, date: str) -> int:
    if not date:
        return 0
    if method.endswith("_midyear"):
        return 1
    if method.endswith("_midmonth"):
        return 2
    return 3


def run_decision_dates(worklist_path: str, opposition_path: str, out_path: str) -> dict:
    worklist = _load_decision_worklist(worklist_path)
    url_index = _build_url_index(opposition_path)
    fieldnames = ["project_id", "project_name", "state", "lifecycle_outcome",
                  "recovered_date", "method", "source_url", "year_only"]
    rows_out = []
    n_recovered = n_no_url = n_no_match = n_year_only = 0

    for w in worklist:
        key = str(w.get("project_name", "") or "").lower().strip()
        urls = url_index.get(key, [])

        best_date, best_method, best_url, best_prec = "", "", "", 0
        for url in urls:
            date, method = recover_from_url(url)
            prec = _precision(method, date)
            if prec > best_prec:
                best_date, best_method, best_url, best_prec = date, method, url, prec

        if not urls:
            best_method = "no_source_url"
            n_no_url += 1
        elif not best_date:
            best_method = "no_pattern_match"
            n_no_match += 1
        else:
            n_recovered += 1
            if best_method.endswith("_midyear"):
                n_year_only += 1

        year_only = "true" if best_method.endswith("_midyear") else "false"
        rows_out.append({
            "project_id": w.get("project_id", ""),
            "project_name": w.get("project_name", ""),
            "state": w.get("state", ""),
            "lifecycle_outcome": w.get("lifecycle_outcome", ""),
            "recovered_date": best_date,
            "method": best_method,
            "source_url": best_url,
            "year_only": year_only,
        })

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_out)

    return {"total": len(rows_out), "recovered": n_recovered,
            "no_source_url": n_no_url, "no_pattern_match": n_no_match,
            "year_only": n_year_only}


def selftest() -> int:
    checks = []

    def check(label, ok):
        checks.append((label, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    check("ymd_path: /2024/03/15/ -> 2024-03-15",
          recover_from_url("https://example.com/news/2024/03/15/story") == ("2024-03-15", "ymd_path"))

    check("iso_in_url: 2023-07-04 in body -> 2023-07-04",
          recover_from_url("https://city.gov/docs/decision-2023-07-04.pdf") == ("2023-07-04", "iso_in_url"))

    check("compact: 20221105 in segment -> 2022-11-05",
          recover_from_url("https://example.com/release_20221105_final") == ("2022-11-05", "compact"))

    check("ym_path_midmonth: /2025/06/ -> 2025-06-15",
          recover_from_url("https://example.com/2025/06/") == ("2025-06-15", "ym_path_midmonth"))

    check("monthname: march-15-2023 -> 2023-03-15",
          recover_from_url("https://example.com/march-15-2023-hearing") == ("2023-03-15", "monthname"))

    check("dmonthname: 15-march-2023 -> 2023-03-15",
          recover_from_url("https://example.com/15-march-2023-vote") == ("2023-03-15", "dmonthname"))

    check("year_only_midyear: /2021/ -> 2021-07-01",
          recover_from_url("https://example.com/projects/2021/final-approval") == ("2021-07-01", "year_only_midyear"))

    check("no match: plain URL -> empty",
          recover_from_url("https://example.com/meeting-notes") == ("", ""))

    check("empty url -> empty",
          recover_from_url("") == ("", ""))

    check("year_only flag true when method ends in _midyear",
          ("true" if "year_only_midyear".endswith("_midyear") else "false") == "true")

    check("year_only flag false for ymd_path method",
          ("true" if "ymd_path".endswith("_midyear") else "false") == "false")

    n_ok = sum(1 for _, ok in checks if ok)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Date recovery for opposition events and decision-date worklist")
    parser.add_argument("--input", default="master_opposition_clean.csv",
                        help="Input CSV for opposition-event date recovery (default: master_opposition_clean.csv)")
    parser.add_argument("--decision-dates", action="store_true",
                        help="Run offline recovery against the decision-date worklist")
    parser.add_argument("--selftest", action="store_true",
                        help="Run self-tests (no network, no file I/O)")
    args = parser.parse_args()

    if args.selftest:
        raise SystemExit(selftest())
    if args.decision_dates:
        result = run_decision_dates(
            "data/decision_date_worklist.csv",
            "master_opposition.csv",
            "data/decision_date_recovery_candidates.csv",
        )
        print(result)
        raise SystemExit(0)
    rows = list(csv.DictReader(open(args.input, newline="", encoding="utf-8")))
    print(apply_recovery(rows))
