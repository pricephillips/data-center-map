#!/usr/bin/env python3
"""
source_archive.py

A durable snapshot for every cited source (spec 006, US1; Principle I).

Local news links rot. A client asking in a year for the source behind a record
gets a 404 today unless a copy exists somewhere that does not move. This job
reads every URL in the clean feed's `Source URL` and `Sources` columns, asks
the Internet Archive's CDX API whether a good snapshot already exists, asks
Save Page Now to capture the ones that have none, and records the outcome in
data/source_archive.csv. It runs nightly with a capped batch and resumes where
it stopped, because the state it needs lives in that CSV.

Rules
-----
  archived     only a capture with status 200. A snapshot of an error page is
               recorded (http_status) and is not treated as archived.
  requested    a Save Page Now request returned 2xx. The job never polls the
               capture: the next run due for a recheck asks CDX again, and a
               new 200 capture makes the row archived with method "spn".
               After max_attempts requests with no capture the row is failed.
  unresolved_redirect
               news.google.com links (skip_hosts). They name no publisher, so
               there is nothing durable to capture; triage resolves them.
  rate limits  HTTP 429 or 503 (or a save response naming a limit) backs off
               through backoff_s; if the limit persists the batch stops, the
               stop point and reason go to the manifest, and the CSV is written
               with everything done so far. A stop is expected behaviour, so it
               exits 0.

Queue order is deterministic: requested rows due for a recheck, then URLs not
yet in the CSV, then not_archived rows; by URL inside each group. max_lookups
caps CDX calls and max_saves caps save requests (configs/source_durability.json).

Credentials (FR-002): IA_S3_ACCESS_KEY and IA_S3_SECRET_KEY, mapped from
repository secrets by the workflow. With them a save is an authenticated
POST at the higher rate; without them it is an anonymous GET. They are never
printed or written.

Standard library HTTP only (FR-001). Writes only the two outputs below.

Outputs
  data/source_archive.csv            one row per cited URL
  data/source_archive_manifest.json  last run, coverage (SC-001), 60-run history

Usage
  python source_archive.py
  python source_archive.py --dry-run
  python source_archive.py --max-lookups 50 --max-saves 10
  python source_archive.py --selftest    mocked CDX and Save Page Now; no network
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "configs", "source_durability.json")
FEED_CSV = os.path.join(HERE, "master_opposition_clean.csv")
OUT_CSV = os.path.join(HERE, "data", "source_archive.csv")
MANIFEST = os.path.join(HERE, "data", "source_archive_manifest.json")
FIXTURES = os.path.join(HERE, "tests", "fixtures", "source_durability")

CDX_URL = "https://web.archive.org/cdx/search/cdx"
# Fallback when CDX fails. On the first run that reached its commit
# (2026-10-02), 44 of 55 CDX lookups returned no response (connection reset or
# timeout) at up to 30 s each, so the 30-minute budget covered 55 of 5,714
# URLs. The availability API answered the same question in about 4 s from the
# same kind of network.
AVAILABLE_URL = "https://archive.org/wayback/available"
SAVE_URL = "https://web.archive.org/save"
WAYBACK = "https://web.archive.org/web/{ts}/{original}"
USER_AGENT = "hawthorn-dc-tracker/1.0 (source archiving; contact repo owner)"

FIELDS = ["url", "archived_url", "archived_at", "http_status", "method",
          "status", "checked_on", "requested_on", "attempts"]
STATUSES = ("archived", "requested", "not_archived", "unresolved_redirect", "failed")
TERMINAL = {"archived", "unresolved_redirect", "failed"}
HISTORY_CAP = 60

DEFAULTS = {"max_lookups": 400, "max_saves": 150, "cdx_sleep_s": 1.0,
            "save_sleep_s": 6.0, "backoff_s": [10, 30, 90], "timeout_s": 30,
            "recheck_after_days": 1, "max_attempts": 3, "checkpoint_every": 25,
            "skip_hosts": ["news.google.com"], "max_runtime_s": 1800,
            "cdx_fail_streak": 3}

URL_RE = re.compile(r"https?://[^\s'\"}\],;|]+")
LIMIT_BODY = re.compile(r"(too many|rate limit|daily (capture )?limit|try again later)", re.I)


class RateLimited(Exception):
    pass


def load_config(path: str = CONFIG) -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh).get("archive", {}))
    except (OSError, ValueError):
        pass
    return cfg


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def clean_url(u: str) -> str:
    u = (u or "").strip().rstrip(".")
    return u.split("#")[0]


def cited_urls(feed_csv: str = FEED_CSV) -> list[str]:
    """Every URL in Source URL and Sources, exact form, fragment removed."""
    if not os.path.exists(feed_csv):
        raise FileNotFoundError(feed_csv)
    seen = set()
    with open(feed_csv, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            for f in ("Source URL", "Sources"):
                for m in URL_RE.findall(r.get(f) or ""):
                    u = clean_url(m)
                    if u:
                        seen.add(u)
    return sorted(seen)


def host_of(u: str) -> str:
    h = urllib.parse.urlsplit(u).hostname or ""
    return h[4:] if h.startswith("www.") else h


def load_rows(path: str = OUT_CSV) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["url"]: {k: r.get(k, "") for k in FIELDS} for r in csv.DictReader(fh)}


def write_rows(rows: dict[str, dict], path: str = OUT_CSV) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for u in sorted(rows):
            w.writerow({k: rows[u].get(k, "") for k in FIELDS})
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# HTTP (injectable)
# ---------------------------------------------------------------------------

def urllib_http(method: str, url: str, data: bytes | None = None,
                headers: dict | None = None, timeout: float = 30) -> tuple[int, str]:
    """(status, body). Network errors return status 0."""
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(20_000).decode("utf-8", "replace")
        except Exception:
            body = ""
        return int(exc.code), body
    except Exception as exc:
        return 0, f"{type(exc).__name__}"


class Client:
    """Wraps the HTTP callable with pacing and the backoff rule."""

    def __init__(self, http, cfg: dict, sleep=time.sleep, env=None):
        self.http, self.cfg, self.sleep = http, cfg, sleep
        env = os.environ if env is None else env
        self.access = (env.get("IA_S3_ACCESS_KEY") or "").strip()
        self.secret = (env.get("IA_S3_SECRET_KEY") or "").strip()
        self.lookups = self.saves = self.fallbacks = 0

    @property
    def credentials(self) -> str:
        return "keys" if self.access and self.secret else "anonymous"

    def _call(self, method, url, data=None, headers=None, pace=0.0):
        delays = [0] + list(self.cfg["backoff_s"])
        for i, wait in enumerate(delays):
            if wait:
                self.sleep(wait)
            status, body = self.http(method, url, data=data, headers=headers,
                                     timeout=float(self.cfg["timeout_s"]))
            is_save = method == "POST" or "/save/" in url
            limited = status in (429, 503) or (
                is_save and status >= 400 and bool(LIMIT_BODY.search(body or "")))
            if not limited:
                if pace:
                    self.sleep(pace)
                return status, body
            if i == len(delays) - 1:
                raise RateLimited(f"HTTP {status}")
        raise RateLimited("unreachable")

    def cdx(self, url: str) -> tuple[int, str]:
        self.lookups += 1
        q = urllib.parse.urlencode({"url": url, "output": "json",
                                    "fl": "timestamp,original,statuscode", "limit": "-10"})
        return self._call("GET", f"{CDX_URL}?{q}", pace=float(self.cfg["cdx_sleep_s"]))

    def available(self, url: str) -> tuple[int, str]:
        self.fallbacks += 1
        q = urllib.parse.urlencode({"url": url})
        return self._call("GET", f"{AVAILABLE_URL}?{q}", pace=float(self.cfg["cdx_sleep_s"]))

    def save(self, url: str) -> tuple[int, str]:
        self.saves += 1
        pace = float(self.cfg["save_sleep_s"])
        if self.credentials == "keys":
            data = urllib.parse.urlencode({"url": url, "skip_first_archive": "1"}).encode()
            headers = {"Accept": "application/json",
                       "Authorization": f"LOW {self.access}:{self.secret}",
                       "Content-Type": "application/x-www-form-urlencoded"}
            return self._call("POST", SAVE_URL, data=data, headers=headers, pace=pace)
        return self._call("GET", f"{SAVE_URL}/{url}", pace=pace)


def parse_cdx(body: str) -> tuple[dict | None, str]:
    """(newest 200 capture or None, newest status of any capture)."""
    try:
        data = json.loads(body or "[]")
    except ValueError:
        return None, ""
    caps = [r for r in data if isinstance(r, list) and len(r) >= 3
            and r[0] != "timestamp" and re.fullmatch(r"\d{14}", str(r[0]))]
    caps.sort(key=lambda r: r[0])
    newest_status = str(caps[-1][2]) if caps else ""
    good = [r for r in caps if str(r[2]) == "200"]
    if not good:
        return None, newest_status
    ts, original = good[-1][0], good[-1][1]
    at = dt.datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"archived_url": WAYBACK.format(ts=ts, original=original),
            "archived_at": at, "http_status": "200"}, newest_status


def parse_available(body: str) -> dict | None:
    """The availability API's closest 200 capture, or None. Returns None also
    for a malformed body, which the caller treats like a failed lookup."""
    try:
        snap = (json.loads(body or "{}").get("archived_snapshots") or {}).get("closest") or {}
    except (ValueError, AttributeError):
        return None
    ts = str(snap.get("timestamp") or "")
    if not (snap.get("available") and str(snap.get("status")) == "200"
            and re.fullmatch(r"\d{14}", ts)):
        return None
    at = dt.datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"archived_url": str(snap.get("url", "")).replace("http://web.archive.org",
                                                            "https://web.archive.org", 1),
            "archived_at": at, "http_status": "200"}


def is_available_body(body: str) -> bool:
    """True when the body is a well-formed availability answer (with or
    without a snapshot), so an empty answer can stand in for CDX's."""
    try:
        return isinstance(json.loads(body or "").get("archived_snapshots"), dict)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def plan_queue(urls: list[str], rows: dict[str, dict], cfg: dict,
               today: dt.date) -> tuple[list[str], list[str]]:
    """(queue in processing order, urls newly marked unresolved_redirect)."""
    skip = {h.lower() for h in cfg["skip_hosts"]}
    redirects, due, new, retry = [], [], [], []
    for u in urls:
        r = rows.get(u)
        if host_of(u).lower() in skip:
            if not r or r["status"] != "unresolved_redirect":
                redirects.append(u)
            continue
        if r is None:
            new.append(u)
        elif r["status"] == "requested":
            last = r.get("requested_on") or r.get("checked_on") or ""
            try:
                age = (today - dt.date.fromisoformat(last[:10])).days
            except ValueError:
                age = 10**6
            if age >= int(cfg["recheck_after_days"]):
                due.append(u)
        elif r["status"] == "not_archived":
            retry.append(u)
    return due + new + retry, redirects


def run(http=urllib_http, feed_csv: str = FEED_CSV, out_csv: str = OUT_CSV,
        manifest: str = MANIFEST, cfg: dict | None = None, sleep=time.sleep,
        env=None, today: dt.date | None = None, dry_run: bool = False,
        now: str | None = None, clock=time.monotonic) -> dict:
    cfg = {**DEFAULTS, **(cfg or load_config())}
    # Wall-clock budget. The lookup and save caps bound the number of calls,
    # not their duration: at 30 s timeouts and 10/30/90 s backoff a slow
    # Internet Archive day ran every scheduled job into source-archive.yml's
    # 45-minute timeout (four of four runs cancelled, 2026-09-29 to 10-02), and
    # a cancelled job never reaches its commit step. Stopping here, between
    # URLs, leaves time to write the state and commit it.
    deadline = clock() + float(cfg["max_runtime_s"])
    today = today or dt.date.today()
    urls = cited_urls(feed_csv)
    rows = load_rows(out_csv)
    queue, redirects = plan_queue(urls, rows, cfg, today)
    client = Client(http, cfg, sleep=sleep, env=env)
    if dry_run:
        return {"cited": len(urls), "in_csv": len(rows), "queue": len(queue),
                "batch": min(len(queue), int(cfg["max_lookups"])),
                "unresolved_redirect_new": len(redirects),
                "credentials": client.credentials}

    for u in redirects:
        rows[u] = {**{k: "" for k in FIELDS}, "url": u, "method": "none",
                   "status": "unresolved_redirect", "checked_on": today.isoformat(),
                   "attempts": "0"}

    stop_reason, stop_at, done = "complete", "", 0
    cdx_streak = 0
    try:
        for u in queue:
            # The cap bounds archive API calls of either kind, so skipping
            # CDX never lifts it.
            if client.lookups + client.fallbacks >= int(cfg["max_lookups"]):
                stop_reason, stop_at = "cap_reached", u
                break
            if clock() >= deadline:
                stop_reason, stop_at = "time_budget", u
                break
            prior = rows.get(u) or {**{k: "" for k in FIELDS}, "url": u,
                                    "method": "none", "attempts": "0"}
            row = dict(prior)
            # After cdx_fail_streak consecutive CDX failures, skip CDX for the
            # rest of the run. Each failure costs up to timeout_s before the
            # fallback runs: on 2026-10-02, with the fallback in place, 34 of
            # 53 lookups still failed CDX first, and the 30-minute budget
            # covered 53 URLs.
            if cdx_streak >= int(cfg["cdx_fail_streak"]):
                status, body = 0, "skipped: CDX failing this run"
            else:
                status, body = client.cdx(u)
                cdx_streak = 0 if status == 200 else cdx_streak + 1
            row["checked_on"] = today.isoformat()
            if status == 200:
                cap, newest = parse_cdx(body)
                found_by = "cdx"
            else:
                # CDX itself failed (not a rate limit): ask the availability
                # API instead. Only a well-formed answer counts; otherwise the
                # row stays as it was apart from checked_on, and the next run
                # tries again.
                a_status, a_body = client.available(u)
                if a_status != 200 or not is_available_body(a_body):
                    if not prior.get("status"):
                        row["status"] = "not_archived"
                    rows[u] = row
                    continue
                cap, newest = parse_available(a_body), ""
                found_by = "availability"
            if cap:
                row.update(cap)
                row["method"] = "spn" if prior.get("status") == "requested" else found_by
                row["status"] = "archived"
            else:
                row["http_status"] = newest
                attempts = int(row.get("attempts") or 0)
                if attempts >= int(cfg["max_attempts"]):
                    row["status"] = "failed"
                elif client.saves < int(cfg["max_saves"]):
                    s_status, _ = client.save(u)
                    if 200 <= s_status < 300:
                        row["status"] = "requested"
                        row["requested_on"] = today.isoformat()
                        row["attempts"] = str(attempts + 1)
                    else:
                        row["status"] = row.get("status") if row.get("status") == "requested" \
                            else "not_archived"
                else:
                    if row.get("status") != "requested":
                        row["status"] = "not_archived"
            rows[u] = row
            done += 1
            if done % int(cfg["checkpoint_every"]) == 0:
                write_rows(rows, out_csv)
    except RateLimited:
        stop_reason, stop_at = "rate_limited", u
    finally:
        write_rows(rows, out_csv)

    counts = {s: 0 for s in STATUSES}
    for r in rows.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    cited = set(urls)
    live = [r for u, r in rows.items() if u in cited]
    resolvable = sum(1 for r in live if r["status"] != "unresolved_redirect")
    archived = sum(1 for r in live if r["status"] == "archived")
    coverage = round(archived / resolvable, 4) if resolvable else 0.0
    prior_hist = []
    if os.path.exists(manifest):
        try:
            with open(manifest, encoding="utf-8") as fh:
                prior_hist = json.load(fh).get("history", [])
        except (OSError, ValueError):
            prior_hist = []
    run_at = now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"run_at": run_at, "archived": archived, "resolvable": resolvable,
             "coverage": coverage, "stop_reason": stop_reason}
    out = {"run_at": run_at, "credentials": client.credentials,
           "cited_urls": len(urls), "lookups": client.lookups, "saves": client.saves,
           "availability_fallbacks": client.fallbacks,
           "cdx_skipped": cdx_streak >= int(cfg["cdx_fail_streak"]),
           "stop_reason": stop_reason, "stop_at_url": stop_at,
           "counts_by_status": counts, "resolvable": resolvable,
           "archived": archived, "coverage": coverage,
           "history": (prior_hist + [entry])[-HISTORY_CAP:]}
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    with open(manifest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    return out


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _fx(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def selftest() -> int:
    import tempfile
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    found, non200, empty = _fx("cdx_found.json"), _fx("cdx_non200.json"), _fx("cdx_empty.json")
    cap, newest = parse_cdx(found)
    check("parse_cdx picks the newest 200 capture",
          cap and cap["archived_url"] == "https://web.archive.org/web/20260301083015/https://example.com/a"
          and cap["archived_at"] == "2026-03-01T08:30:15Z")
    check("a non-200-only result is not archived and keeps its status",
          parse_cdx(non200) == (None, "404"))
    check("an empty result is not archived", parse_cdx(empty) == (None, ""))
    check("a malformed body is not archived", parse_cdx("<html>") == (None, ""))

    td = tempfile.mkdtemp()
    feed = os.path.join(td, "feed.csv")
    with open(feed, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["Incident", "Source URL", "Sources"])
        w.writerow(["a", "https://ex1.com/a", "https://ex1.com/a; https://ex2.com/b#frag"])
        w.writerow(["b", "https://ex3.com/c", "https://ex4.com/d"])
        w.writerow(["c", "https://ex5.com/e", ""])
        w.writerow(["d", "https://news.google.com/rss/articles/CBMabc?oc=5", ""])
    out_csv, man = os.path.join(td, "archive.csv"), os.path.join(td, "manifest.json")
    check("cited urls: Source URL and Sources, fragment removed, deduped",
          cited_urls(feed) == ["https://ex1.com/a", "https://ex2.com/b", "https://ex3.com/c",
                               "https://ex4.com/d", "https://ex5.com/e",
                               "https://news.google.com/rss/articles/CBMabc?oc=5"])

    # Mocked archive. ex1, ex2, ex3 have 200 captures; ex4 and ex5 have none.
    state = {"captured": {"https://ex1.com/a", "https://ex2.com/b", "https://ex3.com/c"},
             "calls": [], "limit_after": None, "saves_seen": []}

    def http(method, url, data=None, headers=None, timeout=None):
        state["calls"].append((method, url, headers or {}))
        if state["limit_after"] is not None and len(state["calls"]) > state["limit_after"]:
            return 429, "Too Many Requests"
        if url.startswith(CDX_URL):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            u = q["url"][0]
            if u in state["captured"]:
                return 200, json.dumps([["timestamp", "original", "statuscode"],
                                        ["20260915101010", u, "200"]])
            if u == "https://ex4.com/d":
                return 200, non200
            return 200, empty
        if url.startswith(SAVE_URL):
            state["saves_seen"].append(url if method == "GET" else data.decode())
            return 200, "{}"
        return 404, ""

    slept = []
    cfg = dict(DEFAULTS, cdx_sleep_s=0, save_sleep_s=0, backoff_s=[1, 2])
    day1 = dt.date(2026, 9, 29)
    res = run(http=http, feed_csv=feed, out_csv=out_csv, manifest=man, cfg=cfg,
              sleep=slept.append, env={}, today=day1, now="2026-09-29T09:40:00Z")
    rows = load_rows(out_csv)
    check("five URLs: three found in CDX", sum(r["status"] == "archived" and r["method"] == "cdx"
                                               for r in rows.values()) == 3)
    check("five URLs: two save requests sent",
          res["saves"] == 2 and sum(r["status"] == "requested" for r in rows.values()) == 2)
    check("an existing snapshot makes no save request",
          not any("ex1.com" in s or "ex2.com" in s or "ex3.com" in s for s in state["saves_seen"]))
    check("the non-200 capture is not archived and records http_status 404",
          rows["https://ex4.com/d"]["http_status"] == "404"
          and rows["https://ex4.com/d"]["archived_url"] == "")
    gn = rows["https://news.google.com/rss/articles/CBMabc?oc=5"]
    check("a news.google.com URL is unresolved_redirect with no call",
          gn["status"] == "unresolved_redirect"
          and not any("news.google" in c[1] for c in state["calls"]))
    check("anonymous saves are GET /save/<url>",
          all(s.startswith(SAVE_URL + "/https://") for s in state["saves_seen"]))
    raw = open(out_csv, "rb").read()
    check("CSV has LF endings and the spec's five columns first",
          b"\r\n" not in raw and raw.startswith(b"url,archived_url,archived_at,http_status,method,"))
    check("manifest coverage is archived / resolvable (3 of 5)",
          res["resolvable"] == 5 and res["archived"] == 3 and res["coverage"] == 0.6)

    # Day 2: the requested rows are rechecked. ex5 is now captured by our save.
    state["captured"].add("https://ex5.com/e")
    state["calls"].clear()
    state["saves_seen"].clear()
    res2 = run(http=http, feed_csv=feed, out_csv=out_csv, manifest=man, cfg=cfg,
               sleep=slept.append, env={}, today=day1 + dt.timedelta(days=1),
               now="2026-09-30T09:40:00Z")
    rows = load_rows(out_csv)
    check("a requested row confirmed by CDX becomes archived with method spn",
          rows["https://ex5.com/e"]["status"] == "archived"
          and rows["https://ex5.com/e"]["method"] == "spn")
    check("archived rows are never looked up again",
          not any("ex1.com" in c[1] for c in state["calls"]))
    check("a still-missing row is requested again with attempts counted",
          rows["https://ex4.com/d"]["attempts"] == "2" and res2["saves"] == 1)
    check("manifest history keeps both runs", len(res2["history"]) == 2)

    # Day 3: a rate limit on the first call backs off, stops, records the stop point.
    state["calls"].clear()
    state["limit_after"] = 0
    slept.clear()
    before = open(out_csv, encoding="utf-8").read()
    res3 = run(http=http, feed_csv=feed, out_csv=out_csv, manifest=man, cfg=cfg,
               sleep=slept.append, env={}, today=day1 + dt.timedelta(days=2),
               now="2026-10-01T09:40:00Z")
    check("429 backs off through backoff_s before stopping", slept == [1, 2])
    check("the stop is recorded with the URL it stopped at",
          res3["stop_reason"] == "rate_limited" and res3["stop_at_url"] == "https://ex4.com/d")
    check("a stopped run keeps every row", open(out_csv, encoding="utf-8").read() == before)

    # Day 4: resume. The limit is gone; the row picks up where it stopped and
    # hits max_attempts.
    state["limit_after"] = None
    res4 = run(http=http, feed_csv=feed, out_csv=out_csv, manifest=man, cfg=cfg,
               sleep=slept.append, env={}, today=day1 + dt.timedelta(days=3),
               now="2026-10-02T09:40:00Z")
    rows = load_rows(out_csv)
    check("the next run resumes and completes", res4["stop_reason"] == "complete"
          and rows["https://ex4.com/d"]["attempts"] == "3")
    res5 = run(http=http, feed_csv=feed, out_csv=out_csv, manifest=man, cfg=cfg,
               sleep=slept.append, env={}, today=day1 + dt.timedelta(days=4),
               now="2026-10-03T09:40:00Z")
    rows = load_rows(out_csv)
    check("after max_attempts with no capture the row is failed",
          rows["https://ex4.com/d"]["status"] == "failed" and res5["saves"] == 0)

    # Keys: authenticated POST; the secret never lands in a file.
    td2 = tempfile.mkdtemp()
    state["calls"].clear()
    state["saves_seen"].clear()
    env = {"IA_S3_ACCESS_KEY": "AKEYVALUE", "IA_S3_SECRET_KEY": "SECRETVALUE"}
    res6 = run(http=http, feed_csv=feed, out_csv=os.path.join(td2, "a.csv"),
               manifest=os.path.join(td2, "m.json"), cfg=cfg, sleep=slept.append,
               env=env, today=day1, now="2026-09-29T09:40:00Z")
    posts = [c for c in state["calls"] if c[0] == "POST"]
    check("with keys a save is an authenticated POST",
          res6["credentials"] == "keys" and posts
          and posts[0][2].get("Authorization") == "LOW AKEYVALUE:SECRETVALUE")
    written = open(os.path.join(td2, "a.csv"), encoding="utf-8").read() + \
        open(os.path.join(td2, "m.json"), encoding="utf-8").read()
    check("credentials are never written", "SECRETVALUE" not in written and "AKEYVALUE" not in written)

    # Caps and dry run.
    td3 = tempfile.mkdtemp()
    state["calls"].clear()
    capped = run(http=http, feed_csv=feed, out_csv=os.path.join(td3, "a.csv"),
                 manifest=os.path.join(td3, "m.json"),
                 cfg=dict(cfg, max_lookups=2), sleep=slept.append, env={}, today=day1)
    check("max_lookups caps the batch and records where it stopped",
          capped["lookups"] == 2 and capped["stop_reason"] == "cap_reached"
          and capped["stop_at_url"] == "https://ex3.com/c")
    state["calls"].clear()
    ticks = iter(range(0, 10_000, 100))          # each clock() call advances 100 s
    timed = run(http=http, feed_csv=feed, out_csv=os.path.join(td3, "t.csv"),
                manifest=os.path.join(td3, "t.json"),
                cfg=dict(cfg, max_runtime_s=250), sleep=slept.append, env={}, today=day1,
                clock=lambda: next(ticks))
    check("max_runtime_s stops between URLs, records where, and still writes state",
          timed["stop_reason"] == "time_budget" and timed["lookups"] == 2
          and timed["stop_at_url"] == "https://ex3.com/c"
          and os.path.exists(os.path.join(td3, "t.csv")))
    # CDX down: the availability API answers instead.
    td4 = tempfile.mkdtemp()
    feed4 = os.path.join(td4, "feed.csv")
    with open(feed4, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["Incident", "Source URL", "Sources"])
        for u in ("https://a.example/1", "https://b.example/2", "https://c.example/3"):
            w.writerow(["x", u, ""])
    calls4 = []

    def http4(method, url, data=None, headers=None, timeout=None):
        calls4.append(url)
        if url.startswith(CDX_URL):
            return 0, "ConnectionResetError"
        if url.startswith(AVAILABLE_URL):
            u = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["url"][0]
            if u == "https://a.example/1":
                return 200, json.dumps({"archived_snapshots": {"closest": {
                    "status": "200", "available": True, "timestamp": "20260618165123",
                    "url": "http://web.archive.org/web/20260618165123/https://a.example/1"}}})
            if u == "https://b.example/2":
                return 200, json.dumps({"url": u, "archived_snapshots": {}})
            return 0, "TimeoutError"
        if url.startswith(SAVE_URL):
            return 200, "{}"
        return 404, ""

    fb = run(http=http4, feed_csv=feed4, out_csv=os.path.join(td4, "a.csv"),
             manifest=os.path.join(td4, "m.json"), cfg=cfg, sleep=slept.append,
             env={}, today=day1)
    got = load_rows(os.path.join(td4, "a.csv"))
    check("when CDX fails, an availability snapshot archives the row",
          got["https://a.example/1"]["status"] == "archived"
          and got["https://a.example/1"]["method"] == "availability"
          and got["https://a.example/1"]["archived_url"].startswith("https://web.archive.org/web/2026"))
    check("an availability answer with no snapshot goes on to a save request",
          got["https://b.example/2"]["status"] == "requested"
          and any(c.startswith(SAVE_URL) and "b.example" in c for c in calls4))
    check("when both lookups fail the row is left for the next run",
          got["https://c.example/3"]["status"] == "not_archived"
          and not any(c.startswith(SAVE_URL) and "c.example" in c for c in calls4))
    check("the manifest counts availability fallbacks", fb["availability_fallbacks"] == 3)
    calls4.clear()
    run(http=http4, feed_csv=feed4, out_csv=os.path.join(td4, "s.csv"),
        manifest=os.path.join(td4, "s.json"), cfg=dict(cfg, cdx_fail_streak=2),
        sleep=slept.append, env={}, today=day1)
    check("after cdx_fail_streak failures CDX is skipped for the rest of the run",
          sum(1 for c in calls4 if c.startswith(CDX_URL)) == 2
          and sum(1 for c in calls4 if c.startswith(AVAILABLE_URL)) == 3)
    check("parse_available rejects a non-200 snapshot and a malformed body",
          parse_available(json.dumps({"archived_snapshots": {"closest": {
              "status": "404", "available": True, "timestamp": "20260101000000"}}})) is None
          and parse_available("<html>") is None and not is_available_body("<html>"))

    state["calls"].clear()
    dry = run(http=http, feed_csv=feed, out_csv=os.path.join(td3, "none.csv"),
              manifest=os.path.join(td3, "none.json"), cfg=cfg, env={}, today=day1,
              dry_run=True)
    check("dry run makes no call and writes nothing",
          not state["calls"] and dry["queue"] == 5
          and not os.path.exists(os.path.join(td3, "none.csv")))

    print("ALL PASS" if not fails else "FAILURES PRESENT")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan the batch and print counts; no network, no writes")
    ap.add_argument("--max-lookups", type=int)
    ap.add_argument("--max-saves", type=int)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    cfg = load_config()
    if args.max_lookups is not None:
        cfg["max_lookups"] = args.max_lookups
    if args.max_saves is not None:
        cfg["max_saves"] = args.max_saves
    try:
        res = run(cfg=cfg, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(f"source_archive: input missing ({exc})", file=sys.stderr)
        return 1
    if args.dry_run:
        print("source_archive (dry run): " + json.dumps(res))
        return 0
    print(f"source_archive: {res['lookups']} lookups, {res['saves']} saves "
          f"({res['credentials']}); stop: {res['stop_reason']}"
          + (f" at {res['stop_at_url']}" if res["stop_at_url"] else ""))
    print(f"source_archive: {res['archived']} of {res['resolvable']} resolvable "
          f"URLs archived (coverage {res['coverage']:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
