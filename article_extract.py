#!/usr/bin/env python3
"""
article_extract.py

Main text and publication date from a news page (spec 006, US2). The only
module that imports trafilatura.

Harvest and triage rows carry a headline and a URL but often no date: GDELT's
seendate is when GDELT crawled the page, not when it was published, and 369
held rows have no date at all. The publication date is usually in the page
(JSON-LD datePublished, article:published_time, a byline <time>), and
trafilatura reads it through htmldate.

What it returns is a HINT. date_hint lands in review worklists and never in
master_opposition.csv: promote_signal_candidates.build_master_row does not read
it, and a selftest there asserts so. A reviewer adopts a date; this module
only points at one.

  date_hint   ISO date, day precision, between extract.min_date and the fetch
              date; blank otherwise. original_date=True prefers the published
              date over the modified one. extensive_search=False stops htmldate
              guessing from dates in the body text, which in local coverage is
              usually the date of the meeting being previewed
  thin_text   "yes" when the main text is under extract.thin_text_chars
              (paywalls, consent walls); the row keeps its headline
  lead        the first extract.lead_words words, for event_dedupe. Returned
              in memory only and never written to disk

--measure writes the SC-003 check: the hint for each of the first 30 verified
decision dates in data/project_decision_dates.csv, compared under the rule
fixed in specs/006-source-durability-dedupe/research.md D10 (agree = within
one day). Rows already fetched are kept, so each URL is fetched once.

Without trafilatura, extract() returns an empty record (no hint, no lead)
and callers behave as they did before this module existed.

Outputs (--measure only)
  data/date_hint_agreement.csv
  data/date_hint_agreement.md

Usage
  python article_extract.py URL          print the extraction record
  python article_extract.py --measure
  python article_extract.py --selftest   offline; fixtures in
                                         tests/fixtures/source_durability/
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "configs", "source_durability.json")
FIXTURES = os.path.join(HERE, "tests", "fixtures", "source_durability")
DECISION_DATES = os.path.join(HERE, "data", "project_decision_dates.csv")
AGREE_CSV = os.path.join(HERE, "data", "date_hint_agreement.csv")
AGREE_MD = os.path.join(HERE, "data", "date_hint_agreement.md")

try:
    import trafilatura
    HAVE_TRAFILATURA = True
except ImportError:
    trafilatura = None
    HAVE_TRAFILATURA = False

USER_AGENT = ("Mozilla/5.0 (compatible; hawthorn-dc-tracker/1.0; "
              "opposition monitoring; contact repo owner)")
DEFAULTS = {"timeout_s": 10, "max_bytes": 2_000_000, "max_fetch_per_run": 150,
            "triage_hint_limit": 20, "triage_timeout_s": 8,
            "thin_text_chars": 500, "lead_words": 60, "min_date": "2010-01-01"}
MEASURE_DEFAULTS = {"sample_size": 30, "agree_days": 1, "target_rate": 0.8}

AGREE_FIELDS = ["project_id", "source_url", "verified_date", "date_hint",
                "delta_days", "agree", "http_status", "fetched_on"]


def available() -> bool:
    return HAVE_TRAFILATURA


def load_config(section: str = "extract", path: str = CONFIG) -> dict:
    cfg = dict(DEFAULTS if section == "extract" else MEASURE_DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh).get(section, {}))
    except (OSError, ValueError):
        pass
    return cfg


def empty_record(url: str, status: str = "") -> dict:
    return {"url": url, "http_status": status, "date_hint": "", "text_chars": 0,
            "thin_text": "", "lead": ""}


# ---------------------------------------------------------------------------
# fetch and extract
# ---------------------------------------------------------------------------

def fetch(url: str, opener=None, cfg: dict | None = None) -> tuple[str, str]:
    """(http_status, html). Standard library only; html is "" on any failure.
    opener(request, timeout) is injectable so the selftest never touches the
    network."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if not re.match(r"^https?://", url or "", re.I):
        return "bad_url", ""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html,*/*;q=0.5"})
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=float(cfg["timeout_s"])) as resp:
            status = str(getattr(resp, "status", None) or resp.getcode())
            ctype = (resp.headers.get("Content-Type") or "text/html").lower()
            if "html" not in ctype and "xml" not in ctype:
                return status, ""
            raw = resp.read(int(cfg["max_bytes"]) + 1)[: int(cfg["max_bytes"])]
            charset = resp.headers.get_content_charset() or "utf-8"
            return status, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        return str(exc.code), ""
    except Exception as exc:                      # network, timeout, TLS, decode
        return f"error:{type(exc).__name__}", ""


def _bounded_date(value, cfg: dict, today: dt.date) -> str:
    s = str(value or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return ""
    try:
        d = dt.date.fromisoformat(s)
    except ValueError:
        return ""
    lo = dt.date.fromisoformat(str(cfg["min_date"]))
    return d.isoformat() if lo <= d <= today else ""


def extract(url: str, html: str | None = None, opener=None,
            cfg: dict | None = None, today: dt.date | None = None) -> dict:
    """Extraction record for one page. Fetches when html is None."""
    cfg = {**DEFAULTS, **(cfg or {})}
    today = today or dt.date.today()
    status = "200"
    if html is None:
        status, html = fetch(url, opener=opener, cfg=cfg)
    rec = empty_record(url, status)
    if not HAVE_TRAFILATURA or not html:
        return rec
    try:
        doc = trafilatura.bare_extraction(
            html, url=url or None, with_metadata=True, include_comments=False,
            date_extraction_params={"original_date": True,
                                    "extensive_search": False,
                                    "max_date": today.isoformat()})
    except Exception as exc:                      # malformed markup
        print(f"article_extract: extraction failed for {url} ({type(exc).__name__})")
        return rec
    if doc is None:
        rec["thin_text"] = "yes"
        return rec
    text = (getattr(doc, "text", None) or "").strip()
    rec["date_hint"] = _bounded_date(getattr(doc, "date", None), cfg, today)
    rec["text_chars"] = len(text)
    rec["thin_text"] = "yes" if len(text) < int(cfg["thin_text_chars"]) else "no"
    rec["lead"] = " ".join(text.split()[: int(cfg["lead_words"])])
    return rec


# ---------------------------------------------------------------------------
# SC-003 measurement
# ---------------------------------------------------------------------------

def _read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _score(verified: str, hint: str, agree_days: int) -> tuple[str, str]:
    try:
        v = dt.date.fromisoformat(verified[:10])
        h = dt.date.fromisoformat(hint[:10])
    except ValueError:
        return "", ""
    delta = (h - v).days
    return str(delta), "yes" if abs(delta) <= agree_days else "no"


def measure(opener=None, decision_csv: str = DECISION_DATES,
            out_csv: str = AGREE_CSV, out_md: str = AGREE_MD,
            cfg: dict | None = None, mcfg: dict | None = None,
            today: dt.date | None = None) -> dict:
    cfg = {**DEFAULTS, **(cfg or load_config("extract"))}
    mcfg = {**MEASURE_DEFAULTS, **(mcfg or load_config("measure"))}
    today = today or dt.date.today()
    sample = sorted(_read(decision_csv), key=lambda r: r.get("project_id", ""))
    sample = sample[: int(mcfg["sample_size"])]
    done = {(r["project_id"], r["source_url"]): r for r in _read(out_csv)
            if r.get("fetched_on")}
    rows = []
    for r in sample:
        pid, url = r.get("project_id", ""), (r.get("source_url") or "").strip()
        verified = (r.get("decision_date") or "").strip()
        prior = done.get((pid, url))
        if prior:
            row = dict(prior)
        elif not HAVE_TRAFILATURA:
            row = {"project_id": pid, "source_url": url, "fetched_on": ""}
        else:
            rec = extract(url, opener=opener, cfg=cfg, today=today)
            row = {"project_id": pid, "source_url": url,
                   "date_hint": rec["date_hint"], "http_status": rec["http_status"],
                   "fetched_on": today.isoformat()}
        row["verified_date"] = verified
        row["delta_days"], row["agree"] = _score(verified, row.get("date_hint", ""),
                                                 int(mcfg["agree_days"]))
        rows.append({k: row.get(k, "") for k in AGREE_FIELDS})

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=AGREE_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    def within(n):
        return sum(1 for r in rows if r["delta_days"] != "" and abs(int(r["delta_days"])) <= n)

    fetched = sum(1 for r in rows if r["fetched_on"])
    hinted = sum(1 for r in rows if r["date_hint"])
    agree = within(int(mcfg["agree_days"]))
    stats = {"sampled": len(rows), "fetched": fetched, "hinted": hinted,
             "exact": within(0), "within_agree": agree, "within_3": within(3),
             "no_hint": fetched - hinted}
    need = int(round(float(mcfg["target_rate"]) * len(rows)))
    if fetched < len(rows):
        verdict = f"not yet measured ({fetched} of {len(rows)} fetched)"
    elif hinted and agree / hinted >= float(mcfg["target_rate"]) and agree >= need:
        verdict = f"met ({agree} of {hinted} hints agree; {agree} of {len(rows)} rows)"
    else:
        verdict = f"not met ({agree} of {hinted} hints agree; {agree} of {len(rows)} rows; needs {need})"
    stats["verdict"] = verdict
    lines = [
        "# Date hint agreement (SC-003)", "",
        f"Sample: first {len(rows)} rows of data/project_decision_dates.csv by project_id.",
        f"Rule: a hint agrees when it is within {int(mcfg['agree_days'])} day(s) of the "
        "verified decision date (fixed in specs/006-source-durability-dedupe/research.md D10).",
        f"Target: at least {int(float(mcfg['target_rate']) * 100)} percent of hints and "
        f"{need} of {len(rows)} rows.", "",
        "| Measure | Rows |", "|---|---|",
        f"| Sampled | {stats['sampled']} |",
        f"| Fetched | {stats['fetched']} |",
        f"| With a hint | {stats['hinted']} |",
        f"| Exact day | {stats['exact']} |",
        f"| Within {int(mcfg['agree_days'])} day(s) | {stats['within_agree']} |",
        f"| Within 3 days | {stats['within_3']} |",
        f"| No hint | {stats['no_hint']} |", "",
        f"SC-003: {verdict}", "",
    ]
    with open(out_md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    return stats


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        import email.message
        self._body, self.status = body, status
        self.headers = email.message.Message()
        self.headers["Content-Type"] = ctype

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def selftest() -> int:
    import tempfile
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    today = dt.date(2026, 9, 29)
    cfg = dict(DEFAULTS)
    check("bounded date keeps a day-precision date in range",
          _bounded_date("2026-03-14T18:05:00", cfg, today) == "2026-03-14")
    check("a future date is dropped", _bounded_date("2027-01-01", cfg, today) == "")
    check("a pre-2010 date is dropped", _bounded_date("2009-12-31", cfg, today) == "")
    check("a month-precision value is dropped", _bounded_date("2026-03", cfg, today) == "")
    check("score: one day after agrees", _score("2026-01-28", "2026-01-29", 1) == ("1", "yes"))
    check("score: three days off disagrees", _score("2026-01-28", "2026-01-31", 1) == ("3", "no"))

    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        if req.full_url.endswith("/pdf"):
            return _FakeResp(b"%PDF", ctype="application/pdf")
        if req.full_url.endswith("/missing"):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return _FakeResp(_fixture("article_jsonld.html").encode("utf-8"))

    st, html = fetch("https://example.com/a", opener=opener)
    check("fetch uses the injected opener", st == "200" and "moratorium" in html and calls)
    check("fetch refuses a non-html body", fetch("https://example.com/pdf", opener=opener) == ("200", ""))
    check("fetch records an HTTP error status", fetch("https://example.com/missing", opener=opener) == ("404", ""))
    check("fetch rejects a non-http url without a call",
          fetch("news:abc", opener=opener) == ("bad_url", "") and len(calls) == 3)

    if not HAVE_TRAFILATURA:
        rec = extract("https://example.com/a", html="<html></html>", today=today)
        check("without trafilatura the record is empty", rec["date_hint"] == "" and rec["lead"] == "")
        print("SKIP (trafilatura not installed): extraction checks need "
              "trafilatura>=2.2,<3 from requirements/ci.txt")
        print("ALL PASS" if not fails else "FAILURES PRESENT")
        return 1 if fails else 0

    expect = {"article_jsonld.html": "2026-03-14", "article_meta.html": "2025-11-02",
              "article_time.html": "2026-07-09"}
    for name, want in expect.items():
        rec = extract("https://example.com/" + name, html=_fixture(name), today=today)
        check(f"{name}: date_hint {rec['date_hint']} equals the published date {want}",
              rec["date_hint"] == want)
        check(f"{name}: full article is not thin_text", rec["thin_text"] == "no")
    stub = extract("https://example.com/stub", html=_fixture("paywall_stub.html"), today=today)
    check("paywall stub is thin_text and still dated",
          stub["thin_text"] == "yes" and stub["date_hint"] == "2026-05-20")
    rec = extract("https://example.com/a", html=_fixture("article_jsonld.html"), today=today)
    check("lead is capped at lead_words", 0 < len(rec["lead"].split()) <= DEFAULTS["lead_words"])
    early = extract("https://example.com/a", html=_fixture("article_jsonld.html"),
                    today=dt.date(2026, 1, 1))
    check("a published date after the fetch date is not a hint", early["date_hint"] == "")
    fetched = extract("https://example.com/a", opener=opener, today=today)
    check("extract fetches through the opener when no html is given",
          fetched["date_hint"] == "2026-03-14" and fetched["http_status"] == "200")

    td = tempfile.mkdtemp()
    dec = os.path.join(td, "decisions.csv")
    with open(dec, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["project_id", "decision_date", "decision_date_source", "source_url", "note"])
        w.writerow(["prj_2", "2026-03-13", "x", "https://example.com/a", ""])
        w.writerow(["prj_1", "2026-03-01", "x", "https://example.com/b", ""])
    out_csv, out_md = os.path.join(td, "agree.csv"), os.path.join(td, "agree.md")
    calls.clear()
    stats = measure(opener=opener, decision_csv=dec, out_csv=out_csv, out_md=out_md,
                    cfg=cfg, mcfg=dict(MEASURE_DEFAULTS, sample_size=2), today=today)
    check("measure scores the sample (one within a day, one 13 days off)",
          stats["within_agree"] == 1 and stats["hinted"] == 2 and len(calls) == 2)
    stats2 = measure(opener=opener, decision_csv=dec, out_csv=out_csv, out_md=out_md,
                     cfg=cfg, mcfg=dict(MEASURE_DEFAULTS, sample_size=2), today=today)
    check("a second measure run fetches nothing", len(calls) == 2 and stats2 == stats)
    raw = open(out_csv, "rb").read()
    md = open(out_md, encoding="utf-8").read()
    check("agreement csv is LF-only and sorted by project_id",
          b"\r\n" not in raw and raw.split(b"\n")[1].startswith(b"prj_1"))
    check("report carries the verdict and no em-dash", "SC-003: not met" in md and "\u2014" not in md)

    print("ALL PASS" if not fails else "FAILURES PRESENT")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?")
    ap.add_argument("--measure", action="store_true",
                    help="SC-003: score date hints against verified decision dates")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.measure:
        if not HAVE_TRAFILATURA:
            print("article_extract: trafilatura not installed; measurement skipped")
            return 0
        stats = measure()
        print(f"article_extract: SC-003 {stats['verdict']} -> "
              f"{os.path.relpath(AGREE_MD, HERE)}")
        return 0
    if args.url:
        if not HAVE_TRAFILATURA:
            print("article_extract: trafilatura not installed", file=sys.stderr)
            return 1
        rec = extract(args.url)
        rec.pop("lead", None)
        print(json.dumps(rec, indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
