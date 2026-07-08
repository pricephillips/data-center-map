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


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "master_opposition_clean.csv"
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
    print(apply_recovery(rows))
