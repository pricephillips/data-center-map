#!/usr/bin/env python3
"""dispute_watch.py — surface court dockets that may involve a challenge to
a data center incentive or development agreement, per jurisdiction, across
every state/county in the dashboard.

Uses the CourtListener (Free Law Project) REST v4 search endpoint
(https://www.courtlistener.com/api/rest/v4/search/?type=r). This module never
asserts that a hit IS a dispute over a data center incentive; it surfaces
candidate dockets by keyword co-occurrence for an analyst to confirm. No
score, stance, or outcome is computed here, and downstream scoring modules
are required not to move a number on the strength of a hit alone.

Two known limits, both of which must be resolved before these hits could
ever support a score:
  1. Quota. Unauthenticated access is capped at 125 requests/day. A
     dashboard-wide pass is 5 terms x 611 counties = 3,055 requests, so set
     COURTLISTENER_API_TOKEN (free registration at courtlistener.com) to
     raise the ceiling. Without it, a full pass would take about 25 days.
  2. Relevance. The query is a nationwide full-text match, so a common
     jurisdiction name co-occurs with incentive vocabulary in dockets that
     have nothing to do with that county: an "Anchorage" query returned
     Deepwater Horizon, Katrina canal-breach, and Motors Liquidation
     dockets. Results still need scoping to in-state courts before the
     output is more than a research lead.

Query construction: for each (state, county) in the feed, search for the
county/jurisdiction name plus a rotating set of incentive-dispute
vocabulary terms ("tax increment", "abatement", "development agreement",
"clawback", "recapture"), one term per request to keep each query specific
enough to page through. Scoped generically by state + county pulled from
the feed, not hardcoded to any specific site.

Output (data/dispute_watch.csv):
  state, county, search_term, case_name, court, date_filed, docket_number,
  docket_url

Usage:
  python3 dispute_watch.py --state VA
  python3 dispute_watch.py --all --out data/dispute_watch.csv
  python3 dispute_watch.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(ROOT, "master_opposition_clean.csv")
OUT = os.path.join(ROOT, "data", "dispute_watch.csv")
CACHE_PATH = os.path.join(ROOT, "data", "dispute_watch_cache.json")

API_BASE = "https://www.courtlistener.com/api/rest/v4/search/"
BASE_URL = "https://www.courtlistener.com"
USER_AGENT = "data-center-map-dispute-watch/1.0 (+github.com/pricephillips/data-center-map)"
THROTTLE_S = 1.5
TIMEOUT_S = 20

INCENTIVE_TERMS = ["tax increment", "abatement", "development agreement",
                   "clawback", "recapture"]

OUT_COLS = ["state", "county", "search_term", "case_name", "court",
           "date_filed", "docket_number", "docket_url"]

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)


def truthy(v: str) -> bool:
    return (v or "").strip().lower() == "true"


def jurisdictions_from_feed(path: str, state_filter: str | None = None) -> list[tuple[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return sorted({
        ((r.get("State") or "").strip().upper(), (r.get("County") or "").strip())
        for r in rows
        if (r.get("County") or "").strip() and not truthy(r.get("is_statewide"))
        and (not state_filter or (r.get("State") or "").strip().upper() == state_filter)
    })


class ThrottledError(RuntimeError):
    """The API refused the request for quota reasons.

    Raised rather than returned so a throttled response is never mistaken
    for, or cached as, a genuine empty result set.
    """


def api_search(query: str) -> list[dict]:
    """Return candidate docket results, or raise ThrottledError on quota refusal.

    Unauthenticated CourtListener allows only 125 requests/day, far below the
    5 terms x 611 counties this module needs for a dashboard-wide pass. Set
    COURTLISTENER_API_TOKEN (free registration) to raise that ceiling.
    """
    url = f"{API_BASE}?{urllib.parse.urlencode({'q': query, 'type': 'r'})}"
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ThrottledError(query)
        return []
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    try:
        payload = json.loads(body)
    except Exception:
        return []
    detail = str(payload.get("detail") or "").lower()
    if "results" not in payload and "throttled" in detail:
        raise ThrottledError(query)
    return payload.get("results", [])


class Cache:
    """One JSON file of query -> results, so a rerun on the same day does
    not re-issue every (jurisdiction x term) request against the live API."""

    def __init__(self, path: str):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except Exception:
                self.data = {}

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, results: list[dict]):
        self.data[key] = results

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)


def county_hits(state: str, county: str, cache: Cache) -> list[dict]:
    rows = []
    county_short = re.sub(r"\s+(County|Borough|Parish|Municipality)$", "", county)
    for term in INCENTIVE_TERMS:
        key = f"{state}:{county}:{term}"
        cached = cache.get(key)
        if cached is None:
            time.sleep(THROTTLE_S)
            # a ThrottledError propagates: never cache a refused request as []
            cached = api_search(f'"{county_short}" "{term}"')
            cache.put(key, cached)
        for r in cached:
            rows.append({
                "state": state, "county": county, "search_term": term,
                "case_name": r.get("caseName") or "",
                "court": r.get("court_citation_string") or r.get("court") or "",
                "date_filed": r.get("dateFiled") or "",
                "docket_number": r.get("docketNumber") or "",
                "docket_url": (BASE_URL + r["docket_absolute_url"])
                              if r.get("docket_absolute_url") else "",
            })
    return rows


def write_csv(path: str, rows: list[dict], cols: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def leak_audit(paths: list[str]) -> None:
    for path in paths:
        if not os.path.exists(path):
            continue
        hits = sum(1 for line in open(path, encoding="utf-8") if LEAK_RE.search(line))
        name = os.path.relpath(path, ROOT)
        print(f"leak audit {name}: {'clean' if not hits else f'{hits} hits'}")


def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  FAIL {label}")
        else:
            print(f"  pass {label}")

    import unittest.mock as mock
    fake_result = {
        "caseName": "County v. Example Developer LLC", "court_citation_string": "E.D. Va.",
        "dateFiled": "2026-01-05", "docketNumber": "1:26-cv-00001",
        "docket_absolute_url": "/docket/1/county-v-example-developer-llc/",
    }
    cache = Cache("/tmp/_dispute_watch_selftest_cache.json")
    cache.data = {}
    with mock.patch(f"{__name__}.api_search", return_value=[fake_result]):
        rows = county_hits("VA", "Test County", cache)
    check(len(rows) == len(INCENTIVE_TERMS),
          "one row per incentive term when every term hits")
    check(rows[0]["docket_url"] == BASE_URL + fake_result["docket_absolute_url"],
          "docket_url is the absolute_url prefixed with the site base")
    check(rows[0]["case_name"] == "County v. Example Developer LLC",
          "case_name carried through from the API result")

    with mock.patch(f"{__name__}.api_search", return_value=[]):
        rows = county_hits("VA", "Empty County", Cache("/tmp/_dispute_watch_selftest_cache2.json"))
    check(rows == [], "no hits produces no rows")

    cache2 = Cache("/tmp/_dispute_watch_selftest_cache3.json")
    cache2.data = {}
    calls = {"n": 0}

    def counting_search(q):
        calls["n"] += 1
        return [fake_result]

    with mock.patch(f"{__name__}.api_search", side_effect=counting_search):
        county_hits("VA", "Cache County", cache2)
        county_hits("VA", "Cache County", cache2)
    check(calls["n"] == len(INCENTIVE_TERMS),
          "second call for the same jurisdiction reuses the cache, no new requests")

    # A throttled request must never be recorded as a genuine empty result,
    # which would make a quota failure indistinguishable from 'no disputes'.
    cache3 = Cache("/tmp/_dispute_watch_selftest_cache4.json")
    cache3.data = {}
    with mock.patch(f"{__name__}.api_search", side_effect=ThrottledError("quota")):
        raised = False
        try:
            county_hits("VA", "Throttled County", cache3)
        except ThrottledError:
            raised = True
    check(raised, "a throttled request propagates ThrottledError to the caller")
    check(cache3.data == {},
          "a throttled request is not cached as an empty result set")

    throttle_body = json.dumps(
        {"detail": "Request was throttled. Rate limit exceeded: 125/day."}
    ).encode()

    class FakeResp:
        def read(self):
            return throttle_body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
        raised = False
        try:
            api_search("anything")
        except ThrottledError:
            raised = True
    check(raised, "a 200-with-throttle-detail body raises instead of returning []")

    for p in ("/tmp/_dispute_watch_selftest_cache.json",
             "/tmp/_dispute_watch_selftest_cache2.json",
             "/tmp/_dispute_watch_selftest_cache3.json"):
        if os.path.exists(p):
            os.remove(p)

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", help="two-letter state code")
    ap.add_argument("--all", action="store_true",
                    help="every county with a local record in the feed")
    ap.add_argument("--out", default=OUT, help="output CSV path")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.state and not args.all:
        ap.print_help()
        return 0

    pairs = jurisdictions_from_feed(FEED, None if args.all else args.state.strip().upper())
    cache = Cache(CACHE_PATH)
    rows = []
    searched = 0
    throttled = False
    for i, (state, county) in enumerate(pairs):
        try:
            rows.extend(county_hits(state, county, cache))
        except ThrottledError as e:
            throttled = True
            print(f"stopping early: rate limit reached at {state} {county} ({e}). "
                  f"Set COURTLISTENER_API_TOKEN to raise the daily ceiling.")
            break
        searched += 1
        if i % 10 == 0:
            cache.save()  # incremental: survives interruption
    cache.save()

    write_csv(args.out, rows, OUT_COLS)
    print(f"dispute watch: {searched} of {len(pairs)} jurisdictions searched, "
         f"{len(rows)} candidate docket hits -> {os.path.relpath(args.out, ROOT)}"
         + (" (INCOMPLETE: rate limited)" if throttled else ""))
    leak_audit([args.out])
    return 0


if __name__ == "__main__":
    sys.exit(main())
