#!/usr/bin/env python3
"""local_meeting_feed.py — normalized local (county/city) meeting and agenda
feed, per jurisdiction, across every county in the dashboard.

Local board/commission meeting agendas are the automatable half of the local
approval-pathway picture (the other half, staff/EDO sentiment, is not on any
API and stays analyst-researched). Three platform families expose read-only,
unauthenticated interfaces we can poll on a schedule:

  civicclerk    OData REST (Events, then publishedFiles for the agenda PDF)
  civicplus_rss AgendaCenter RSS feed, resolved to the agenda/minutes PDF
  legistar      OData REST (Matters / EventItems / Votes / RollCalls)
  primegov      JSON PublicPortal (ListUpcomingMeetings)
  granicus      ViewPublisher HTML, parsed conservatively

The last two were added 2026-09-03. Discovery was resolving 3 jurisdictions
out of 808, and the two platforms it probed are not the ones a western city is
likely to run; PrimeGov and Granicus are. That matters for the same reason the
place gazetteer does: west of the Rockies the body that acts on a data center
is usually a city, so a probe set aimed only at county platforms cannot reach
the jurisdiction doing the deciding.

Not every jurisdiction runs one of these, and Aurora, CO's eSCRIBE portal
confirmed no documented API. For a jurisdiction with none, this module
records that once in the discovery cache and skips it on future runs; it is
not retried on every scheduled run per karpathy-guidelines (probing is
read-only and cached, not repeated live each run).

Two-pass design, matching permit_ingest.py's config-not-code convention:

  1. Discovery (--discover): for each (state, county) pair in the feed,
     probe likely platform URL patterns once, cache the result (platform
     found, base URL, or "none") to configs/local_meeting_sources.json.
     Re-running discovery only re-probes jurisdictions not already cached;
     use --redo to force a full re-probe.
  2. Fetch (--fetch): read the discovery cache (plus any manual overrides in
     configs/local_meeting_sources_overrides.json, which always wins), pull
     new events since each jurisdiction's watermark, and emit normalized
     rows to data/local_meeting_feed.csv:
       jurisdiction, state, county, body, meeting_datetime, item_title,
       item_status, document_url, platform, source_url

Overrides file (misdetection fixes, same shape as the discovery cache, one
entry per "STATE::County Name"):
  {
    "VA::Powhatan County": {"platform": "civicplus_rss",
                            "base_url": "https://www.powhatanva.gov"}
  }

Usage:
  python3 local_meeting_feed.py --discover
  python3 local_meeting_feed.py --discover --redo --state VA
  python3 local_meeting_feed.py --fetch
  python3 local_meeting_feed.py --selftest
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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(ROOT, "master_opposition_clean.csv")
CONFIGS = os.path.join(ROOT, "configs")
DISCOVERY_CACHE = os.path.join(CONFIGS, "local_meeting_sources.json")
OVERRIDES = os.path.join(CONFIGS, "local_meeting_sources_overrides.json")
# Counties queued by adjacency_scan.py. Both frames below are otherwise built
# from the clean feed, which means a county with zero tracker records can
# never be probed and never be polled: the ingestion frame is defined by what
# the tracker already knows. That is the structural half of the
# small-jurisdiction blind spot (Grundy, Coffee and Walker were all outside
# the frame at the time they enacted). Unioning this file in is what lets a
# county enter ingestion on adjacency evidence alone.
WATCHLIST = os.path.join(CONFIGS, "local_meeting_watchlist.csv")
OUT_FEED = os.path.join(ROOT, "data", "local_meeting_feed.csv")

FEED_COLS = ["jurisdiction", "state", "county", "body", "meeting_datetime",
             "item_title", "item_status", "document_url", "platform",
             "source_url"]

THROTTLE_S = 1.0
TIMEOUT_S = 15
USER_AGENT = "data-center-map-local-meeting-feed/1.0 (+github.com/pricephillips/data-center-map)"
LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# HTTP helper (stdlib only, matches bill_sync.py's api_get style)
# ---------------------------------------------------------------------------

def http_get(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    url = urllib.parse.quote(url, safe=":/?&=$,")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def strip_county_words(name: str) -> str:
    """Bare jurisdiction name, with the administrative suffix removed.

    Four probes now build a client slug from the same name, and three of them
    were each carrying their own chain of .replace() calls that had already
    drifted apart (one stripped "Municipality", another did not). One function,
    so a slug is the same string whichever probe asks for it."""
    return re.sub(r"\b(county|borough|parish|municipality|city and borough|census area)\b",
                  "", name, flags=re.IGNORECASE).strip()


# ---------------------------------------------------------------------------
# Jurisdiction list (dashboard-wide, not hardcoded to any specific sites)
# ---------------------------------------------------------------------------

def truthy(v: str) -> bool:
    return (v or "").strip().lower() == "true"


def jurisdictions_from_feed(path: str, state_filter: str | None = None) -> list[tuple[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    pairs = sorted({
        ((r.get("State") or "").strip().upper(), (r.get("County") or "").strip())
        for r in rows
        if (r.get("County") or "").strip() and not truthy(r.get("is_statewide"))
        and (not state_filter or (r.get("State") or "").strip().upper() == state_filter)
    })
    return pairs


def jurisdictions_from_watchlist(path: str = WATCHLIST,
                                 state_filter: str | None = None) -> list[tuple[str, str]]:
    """(state, county) pairs from the adjacency watchlist.

    Only rows still queued are returned; a retired row is kept in the file so
    a reviewer's note survives, but it is not polled. Absent file returns an
    empty list, so this is additive by construction.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []
    pairs = set()
    for r in rows:
        if str(r.get("still_queued", "1")).strip() not in ("1", "true", "True"):
            continue
        state = (r.get("state") or "").strip().upper()
        county = (r.get("county") or "").strip()
        if not state or not county:
            continue
        if state_filter and state != state_filter:
            continue
        pairs.add((state, county))
    return sorted(pairs)


def jurisdiction_frame(feed_path: str = FEED,
                       state_filter: str | None = None,
                       watchlist_path: str = WATCHLIST) -> list[tuple[str, str]]:
    """The clean feed's jurisdictions unioned with the adjacency watchlist."""
    pairs = set(jurisdictions_from_feed(feed_path, state_filter))
    pairs |= set(jurisdictions_from_watchlist(watchlist_path, state_filter))
    return sorted(pairs)


def ambiguous_county_names(path: str,
                           pairs: list[tuple[str, str]] | None = None) -> set[str]:
    """Bare county names (state suffix words stripped) that belong to more
    than one state across the WHOLE feed, e.g. Clark County exists in both
    NV and OH. A slug-guessed platform match for one of these cannot be
    trusted without state confirmation the source APIs don't expose, so
    these names are excluded from auto-detection and must go through the
    manual overrides file instead.

    `pairs` lets the caller pass the full jurisdiction frame (feed plus
    watchlist). Ambiguity has to be judged over everything being probed:
    a watchlist county whose name also exists in another state is exactly as
    unsafe to slug-guess as a feed one."""
    all_pairs = (pairs if pairs is not None
                 else jurisdictions_from_feed(path, state_filter=None))
    states_by_name: dict[str, set[str]] = {}
    for state, county in all_pairs:
        bare = re.sub(r"\b(county|borough|parish|municipality)\b", "", county,
                      flags=re.IGNORECASE).strip().lower()
        states_by_name.setdefault(bare, set()).add(state)
    return {name for name, states in states_by_name.items() if len(states) > 1}


def jur_key(state: str, county: str) -> str:
    return f"{state}::{county}"


# ---------------------------------------------------------------------------
# Platform probes (one read-only request family per platform)
# ---------------------------------------------------------------------------

def probe_civicclerk(county: str) -> dict | None:
    """{client}.api.civicclerk.com/v1/Events, no auth. Client slug is a
    guess from the county name; confirmed to generalize for Wyandotte/KCK."""
    candidates = [slugify(strip_county_words(county))]
    for slug in candidates:
        if not slug:
            continue
        url = f"https://{slug}.api.civicclerk.com/v1/Events?$top=1"
        status, body = http_get(url)
        if status == 200 and body.strip().startswith(b"{"):
            return {"platform": "civicclerk", "base_url": f"https://{slug}.api.civicclerk.com"}
    return None


def probe_civicplus_rss(county: str, state: str) -> dict | None:
    """CivicPlus AgendaCenter RSS. No reliable client-slug rule from county
    name alone (city/county government domains vary too much to guess), so
    this probe only confirms the pattern on a caller-supplied domain via
    the overrides file; discovery cannot invent the domain itself."""
    return None


def probe_legistar(county: str) -> dict | None:
    """webapi.legistar.com/v1/{client}/bodies, no auth. Client slug is a
    guess from the county name; confirmed working for several cities,
    confirmed NOT provisioned for Powhatan/Aurora/Wyandotte.

    The client slug is often a common word (madison, clark, fulton...) that
    collides with an unrelated city's Legistar client of the same name. A
    200 response alone is not accepted as a match, and neither is a body
    name that merely mentions the county's name word (a city's ordinances
    routinely say e.g. "Madison General Ordinance" for the City of Madison,
    which is not this county). Only a body name containing the county's
    proper name word immediately followed by "county" (e.g. "DuPage County
    Board", "Clark County Board of Commissioners") is accepted, since
    Legistar county clients consistently name their primary body this way.
    Still probabilistic; a wrong match should be corrected in the
    overrides file."""
    county_words = [w.lower() for w in re.findall(r"[A-Za-z]+", county)
                    if w.lower() not in ("county", "borough", "parish", "municipality")]
    if not county_words:
        return None
    slug = slugify(strip_county_words(county))
    if not slug:
        return None
    status, body = http_get(f"https://webapi.legistar.com/v1/{slug}/bodies?$top=50")
    if status != 200 or not body.strip().startswith(b"["):
        return None
    try:
        bodies = json.loads(body)
    except Exception:
        return None
    name_word = county_words[0]
    pattern = re.compile(rf"\b{re.escape(name_word)}\s+county\b", re.IGNORECASE)
    if not any(pattern.search(b.get("BodyName") or "") for b in bodies):
        return None  # slug resolved to a real client, but not this county's
    return {"platform": "legistar", "base_url": f"https://webapi.legistar.com/v1/{slug}"}


def probe_primegov(county: str) -> dict | None:
    """{client}.primegov.com/api/v2/PublicPortal/ListUpcomingMeetings, no auth.

    Added because discovery was resolving 3 of 808 jurisdictions and neither of
    the two platforms it probed is the one a western city is likely to run.
    PrimeGov and Granicus below are both common in exactly the places the
    county-only probe set could never reach.

    The response is a JSON array of meetings, which is a strong enough shape
    test on its own: an unprovisioned slug returns an error page or a redirect,
    not a list.
    """
    slug = slugify(strip_county_words(county))
    if not slug:
        return None
    url = f"https://{slug}.primegov.com/api/v2/PublicPortal/ListUpcomingMeetings"
    status, body = http_get(url)
    if status != 200 or not body.strip().startswith(b"["):
        return None
    try:
        json.loads(body)
    except Exception:
        return None
    return {"platform": "primegov", "base_url": f"https://{slug}.primegov.com"}


def probe_granicus(county: str) -> dict | None:
    """{client}.granicus.com/ViewPublisher.php?view_id=N.

    Granicus publishes agendas as HTML rather than JSON, so the shape test is
    weaker than the others' and the confirmation has to be stricter to
    compensate: a 200 alone is not accepted, because Granicus serves a generic
    landing page for slugs it does not host. The page must also carry the
    jurisdiction's own name word, which its masthead does.
    """
    bare = strip_county_words(county)
    slug = slugify(bare)
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", bare)]
    if not slug or not words:
        return None
    url = f"https://{slug}.granicus.com/ViewPublisher.php?view_id=1"
    status, body = http_get(url)
    if status != 200 or not body:
        return None
    text = body.decode("utf-8", errors="replace").lower()
    if "granicus" not in text:
        return None
    if not re.search(rf"\b{re.escape(words[0])}\b", text):
        return None       # a real Granicus page, but not this jurisdiction's
    return {"platform": "granicus",
            "base_url": f"https://{slug}.granicus.com"}


# Ordered cheapest and most decisive first. A jurisdiction stops at its first
# confirmed platform, so putting the two JSON probes ahead of the HTML one
# keeps the weakest shape test as the last resort rather than the first answer.
PROBES = [probe_civicclerk, probe_legistar, probe_primegov, probe_granicus]
# civicplus_rss still needs a known domain and stays override-only.


def discover_one(state: str, county: str, ambiguous_names: set[str]) -> dict:
    bare = re.sub(r"\b(county|borough|parish|municipality)\b", "", county,
                  flags=re.IGNORECASE).strip().lower()
    if bare in ambiguous_names:
        # This bare name is shared by another state in the roster and the
        # source APIs don't expose a state field to disambiguate a slug
        # match, so this jurisdiction is skipped rather than risking a
        # wrong-state match; only a manual override entry can cover it.
        return {"platform": "ambiguous", "base_url": "",
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for probe in PROBES:
        time.sleep(THROTTLE_S)
        result = probe(county)
        if result:
            result["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return result
    return {"platform": "none", "base_url": "",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def discover(state_filter: str | None, redo: bool) -> dict:
    cache = load_json(DISCOVERY_CACHE)
    feed_pairs = jurisdictions_from_feed(FEED, state_filter)
    pairs = jurisdiction_frame(FEED, state_filter)
    watch_only = len(pairs) - len(set(feed_pairs))
    ambiguous_names = ambiguous_county_names(
        FEED, jurisdiction_frame(FEED, state_filter=None))
    checked = 0
    for state, county in pairs:
        key = jur_key(state, county)
        if key in cache and not redo:
            continue
        cache[key] = discover_one(state, county, ambiguous_names)
        checked += 1
        if checked % 10 == 0:
            save_json(DISCOVERY_CACHE, cache)  # incremental: survives interruption
    save_json(DISCOVERY_CACHE, cache)
    found = sum(1 for v in cache.values() if v.get("platform") not in ("none", "ambiguous"))
    skipped_ambiguous = sum(1 for v in cache.values() if v.get("platform") == "ambiguous")
    print(f"discovery frame: {len(pairs)} jurisdictions "
          f"({watch_only} from the adjacency watchlist, not in the feed)")
    print(f"discovery: {checked} newly probed, {len(cache)} total cached, "
         f"{found} with a detected platform, {skipped_ambiguous} skipped as "
         f"cross-state name-ambiguous -> "
         f"{os.path.relpath(DISCOVERY_CACHE, ROOT)}")
    return cache


# ---------------------------------------------------------------------------
# Fetch adapters (normalize each platform's events to FEED_COLS)
# ---------------------------------------------------------------------------

def fetch_civicclerk(base_url: str, jurisdiction: str, state: str, county: str) -> list[dict]:
    status, body = http_get(f"{base_url}/v1/Events?$orderby=startDateTime desc&$top=25")
    if status != 200 or not body:
        return []
    try:
        events = json.loads(body).get("value", [])
    except Exception:
        return []
    rows = []
    for ev in events:
        rows.append({
            "jurisdiction": jurisdiction, "state": state, "county": county,
            "body": ev.get("categoryName") or "", "meeting_datetime": ev.get("startDateTime") or "",
            "item_title": ev.get("eventName") or "", "item_status": ev.get("status") or "",
            "document_url": (ev.get("publishedFiles") or [{}])[0].get("fileLink", "")
                             if ev.get("publishedFiles") else "",
            "platform": "civicclerk", "source_url": f"{base_url}/v1/Events",
        })
    return rows


def fetch_legistar(base_url: str, jurisdiction: str, state: str, county: str) -> list[dict]:
    status, body = http_get(f"{base_url}/Events?$orderby=EventDate desc&$top=25")
    if status != 200 or not body:
        return []
    try:
        events = json.loads(body)
    except Exception:
        return []
    rows = []
    for ev in events:
        rows.append({
            "jurisdiction": jurisdiction, "state": state, "county": county,
            "body": ev.get("EventBodyName") or "",
            "meeting_datetime": ev.get("EventDate") or "",
            "item_title": ev.get("EventComment") or "",
            "item_status": ev.get("EventAgendaStatusName") or "",
            "document_url": ev.get("EventAgendaFile") or "",
            "platform": "legistar", "source_url": f"{base_url}/Events",
        })
    return rows


def fetch_civicplus_rss(base_url: str, jurisdiction: str, state: str, county: str) -> list[dict]:
    url = f"{base_url}/RSSFeed.aspx?ModID=65&CID=All-agendacenter"
    status, body = http_get(url)
    if status != 200 or not body:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    rows = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        rows.append({
            "jurisdiction": jurisdiction, "state": state, "county": county,
            "body": "", "meeting_datetime": pub, "item_title": title,
            "item_status": "", "document_url": link,
            "platform": "civicplus_rss", "source_url": url,
        })
    return rows


def fetch_primegov(base_url: str, jurisdiction: str, state: str, county: str) -> list[dict]:
    status, body = http_get(
        f"{base_url}/api/v2/PublicPortal/ListUpcomingMeetings")
    if status != 200 or not body:
        return []
    try:
        meetings = json.loads(body)
    except Exception:
        return []
    rows = []
    for mt in meetings if isinstance(meetings, list) else []:
        # PrimeGov attaches several documents per meeting (agenda, packet,
        # minutes). The agenda is the one that carries a rezoning item before
        # the vote, so it is what the row cites when present.
        doc = ""
        for d in (mt.get("documentList") or []):
            name = (d.get("templateName") or d.get("name") or "").lower()
            if "agenda" in name and d.get("id"):
                doc = f"{base_url}/Portal/Meeting?meetingTemplateId={d['id']}"
                break
        rows.append({
            "jurisdiction": jurisdiction, "state": state, "county": county,
            "body": mt.get("title") or mt.get("meetingGroupName") or "",
            "meeting_datetime": mt.get("dateTime") or mt.get("date") or "",
            "item_title": mt.get("title") or "",
            "item_status": mt.get("meetingStatus") or "",
            "document_url": doc,
            "platform": "primegov",
            "source_url": f"{base_url}/api/v2/PublicPortal/ListUpcomingMeetings",
        })
    return rows


def fetch_granicus(base_url: str, jurisdiction: str, state: str, county: str) -> list[dict]:
    """Granicus ViewPublisher rows.

    HTML rather than an API, so this parses conservatively: a row is emitted
    only when it carries both a date-looking cell and an agenda link. Granicus
    templates vary between clients, and a loose parse would put rows with
    invented dates into a feed whose whole purpose is knowing when a hearing
    is. Missing a meeting costs a reviewer nothing; a wrong date costs trust.
    """
    url = f"{base_url}/ViewPublisher.php?view_id=1"
    status, body = http_get(url)
    if status != 200 or not body:
        return []
    html = body.decode("utf-8", errors="replace")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [re.sub(r"<[^>]+>", " ", c) for c in
                 re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        text = " ".join(cells)
        m = re.search(r"\b([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\b", text)
        if not m:
            continue
        link = re.search(r'href=["\']([^"\']*(?:AgendaViewer|agenda)[^"\']*)["\']',
                         tr, re.I)
        if not link:
            continue
        href = link.group(1).replace("&amp;", "&")
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = base_url + href
        rows.append({
            "jurisdiction": jurisdiction, "state": state, "county": county,
            "body": re.sub(r"\s+", " ", cells[0]).strip() if cells else "",
            "meeting_datetime": m.group(1).replace(",", ""),
            "item_title": re.sub(r"\s+", " ", cells[0]).strip() if cells else "",
            "item_status": "",
            "document_url": href,
            "platform": "granicus", "source_url": url,
        })
    return rows


FETCHERS = {"civicclerk": fetch_civicclerk, "legistar": fetch_legistar,
            "civicplus_rss": fetch_civicplus_rss,
            "primegov": fetch_primegov, "granicus": fetch_granicus}


def fetch(state_filter: str | None) -> list[dict]:
    cache = load_json(DISCOVERY_CACHE)
    overrides = load_json(OVERRIDES)
    merged = {**cache, **overrides}
    pairs = jurisdiction_frame(FEED, state_filter)
    rows = []
    for state, county in pairs:
        key = jur_key(state, county)
        entry = merged.get(key)
        if not entry or entry.get("platform") in (None, "none"):
            continue
        fetcher = FETCHERS.get(entry["platform"])
        if not fetcher:
            continue
        time.sleep(THROTTLE_S)
        try:
            rows.extend(fetcher(entry["base_url"], f"{county}, {state}", state, county))
        except Exception as e:
            print(f"  fetch error {key}: {e}")
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


# ---------------------------------------------------------------------------
# Selftest (network-free: pure functions and parsers only)
# ---------------------------------------------------------------------------

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

    wrong_city_bodies = json.dumps([
        {"BodyName": "COMMON COUNCIL",
         "BodyDescription": "Madison General Ordinance Sec. 33.02"},
    ]).encode()
    with mock.patch(f"{__name__}.http_get", return_value=(200, wrong_city_bodies)):
        result = probe_legistar("Madison County")
    check(result is None,
          "legistar probe rejects a city client whose bodies only mention the name word, not '<Name> County'")

    right_county_bodies = json.dumps([{"BodyName": "Madison County Board of Supervisors",
                                       "BodyDescription": ""}]).encode()
    with mock.patch(f"{__name__}.http_get", return_value=(200, right_county_bodies)):
        result = probe_legistar("Madison County")
    check(result is not None and result["platform"] == "legistar",
          "legistar probe accepts a client whose bodies say '<Name> County'")

    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as tf:
        writer = csv.DictWriter(tf, fieldnames=["State", "County", "is_statewide"])
        writer.writeheader()
        writer.writerow({"State": "NV", "County": "Clark County", "is_statewide": ""})
        writer.writerow({"State": "OH", "County": "Clark County", "is_statewide": ""})
        writer.writerow({"State": "VA", "County": "Powhatan County", "is_statewide": ""})
        fixture_path = tf.name
    ambiguous = ambiguous_county_names(fixture_path)
    os.unlink(fixture_path)
    check("clark" in ambiguous and "powhatan" not in ambiguous,
          "ambiguous_county_names flags a name shared across states, not a unique one")

    result = discover_one("OH", "Clark County", ambiguous_names={"clark"})
    check(result["platform"] == "ambiguous",
          "discover_one skips probing for a cross-state-ambiguous county name")

    check(slugify("Powhatan County") == "powhatancounty", "slugify strips spaces/case")
    check(slugify("Wyandotte County, Unified Government") == "wyandottecountyunifiedgovernment",
          "slugify strips punctuation")
    check(jur_key("VA", "Powhatan County") == "VA::Powhatan County", "jur_key format")

    civicclerk_body = json.dumps({"value": [{
        "categoryName": "Board of Supervisors", "startDateTime": "2026-06-01T18:00:00",
        "eventName": "Regular Meeting", "status": "Final",
        "publishedFiles": [{"fileLink": "https://example.com/agenda.pdf"}],
    }]}).encode()
    with mock.patch(f"{__name__}.http_get", return_value=(200, civicclerk_body)):
        rows = fetch_civicclerk("https://x.api.civicclerk.com", "Test County, VA", "VA", "Test County")
    check(len(rows) == 1 and rows[0]["platform"] == "civicclerk",
          "civicclerk adapter normalizes an event")
    check(rows[0]["document_url"] == "https://example.com/agenda.pdf",
          "civicclerk adapter extracts the agenda file link")

    legistar_body = json.dumps([{
        "EventBodyName": "City Council", "EventDate": "2026-06-01T00:00:00",
        "EventComment": "Zoning hearing", "EventAgendaStatusName": "Final",
        "EventAgendaFile": "https://example.com/legistar.pdf",
    }]).encode()
    with mock.patch(f"{__name__}.http_get", return_value=(200, legistar_body)):
        rows = fetch_legistar("https://webapi.legistar.com/v1/test", "Test City, VA", "VA", "Test City")
    check(len(rows) == 1 and rows[0]["platform"] == "legistar",
          "legistar adapter normalizes an event")

    rss_body = (b"<?xml version='1.0'?><rss><channel>"
               b"<item><title>Agenda Item</title>"
               b"<link>https://example.com/agenda.pdf</link>"
               b"<pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate></item>"
               b"</channel></rss>")
    with mock.patch(f"{__name__}.http_get", return_value=(200, rss_body)):
        rows = fetch_civicplus_rss("https://example.gov", "Test County, VA", "VA", "Test County")
    check(len(rows) == 1 and rows[0]["item_title"] == "Agenda Item",
          "civicplus_rss adapter parses RSS items")

    # --- primegov and granicus (2026-09-03) ---
    primegov_body = json.dumps([{
        "title": "Planning Commission", "dateTime": "2026-09-15T18:00:00",
        "meetingStatus": "Scheduled",
        "documentList": [{"templateName": "Packet", "id": 11},
                         {"templateName": "Agenda", "id": 22}],
    }]).encode()
    with mock.patch(f"{__name__}.http_get", return_value=(200, primegov_body)):
        rows = fetch_primegov("https://mesa.primegov.com", "Maricopa County, AZ",
                              "AZ", "Maricopa County")
    check(len(rows) == 1 and rows[0]["platform"] == "primegov",
          "primegov adapter normalizes a meeting")
    check(rows[0]["document_url"].endswith("meetingTemplateId=22"),
          "primegov adapter prefers the agenda over the packet")
    check(rows[0]["meeting_datetime"] == "2026-09-15T18:00:00",
          "primegov adapter carries the meeting time")

    granicus_body = (
        b"<html><body>granicus<table>"
        b"<tr><td>City Council</td><td>September 15, 2026</td>"
        b"<td><a href='//example.granicus.com/AgendaViewer.php?view_id=1&amp;clip_id=9'>Agenda</a></td></tr>"
        b"<tr><td>Header row with no date and no link</td></tr>"
        b"<tr><td>Board</td><td>October 2, 2026</td><td>video only</td></tr>"
        b"</table></body></html>")
    with mock.patch(f"{__name__}.http_get", return_value=(200, granicus_body)):
        rows = fetch_granicus("https://example.granicus.com", "Pima County, AZ",
                              "AZ", "Pima County")
    check(len(rows) == 1, "granicus adapter emits only rows with a date and an agenda link")
    check(rows[0]["meeting_datetime"] == "September 15 2026",
          "granicus adapter reads the meeting date")
    check(rows[0]["document_url"] == "https://example.granicus.com/AgendaViewer.php?view_id=1&clip_id=9",
          "granicus adapter resolves a protocol-relative link and unescapes it")

    check(set(FETCHERS) >= {p.__name__.replace("probe_", "") for p in PROBES},
          "every probed platform has a fetcher, so discovery cannot record a "
          "platform nothing can poll")

    check(strip_county_words("Anchorage Municipality") == "Anchorage"
          and strip_county_words("North Slope Borough") == "North Slope",
          "one slug helper strips every administrative suffix")

    with mock.patch(f"{__name__}.http_get", return_value=(500, b"")):
        rows = fetch_civicclerk("https://x.api.civicclerk.com", "Test County, VA", "VA", "Test County")
    check(rows == [], "adapters return no rows on a non-200 response")

    with mock.patch(f"{__name__}.http_get", return_value=(200, b"<html>granicus</html>")):
        check(probe_granicus("Nowhere County") is None,
              "a granicus page that does not name the jurisdiction is refused")
    with mock.patch(f"{__name__}.http_get", return_value=(200, b"not json")):
        check(probe_primegov("Nowhere County") is None,
              "a primegov slug returning non-JSON is refused")

    with mock.patch(f"{__name__}.http_get", return_value=(0, b"")):
        rows = fetch_legistar("https://webapi.legistar.com/v1/test", "Test City, VA", "VA", "Test City")
    check(rows == [], "adapters return no rows on a network error")

    # --- adjacency watchlist union (2026-08-18) ---
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        feed_path = os.path.join(td, "feed.csv")
        with open(feed_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["State", "County", "is_statewide"])
            w.writerow(["TN", "Hamilton County", "False"])
            w.writerow(["NV", "Clark County", "False"])
            w.writerow(["TN", "Statewide bill", "True"])

        wl_path = os.path.join(td, "watchlist.csv")
        with open(wl_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["state", "county", "fips", "reason", "priority",
                        "first_queued_utc", "last_seen_utc", "still_queued",
                        "reviewer_note"])
            w.writerow(["GA", "Walker County", "13295", "adjacency", "1",
                        "", "", "1", ""])
            w.writerow(["TN", "Hamilton County", "47065", "adjacency", "2",
                        "", "", "1", ""])
            w.writerow(["OH", "Clark County", "39023", "adjacency", "2",
                        "", "", "1", ""])
            w.writerow(["TN", "Retired County", "47999", "adjacency", "2",
                        "", "", "0", "resolved, no action"])

        feed_only = jurisdictions_from_feed(feed_path)
        frame = jurisdiction_frame(feed_path, None, wl_path)
        check(("GA", "Walker County") not in feed_only,
              "a county with no tracker record is outside the feed frame")
        check(("GA", "Walker County") in frame,
              "the watchlist brings a zero-record county into the frame")
        check(("TN", "Hamilton County") in frame
              and len([p for p in frame if p == ("TN", "Hamilton County")]) == 1,
              "a county in both the feed and the watchlist appears once")
        check(("TN", "Retired County") not in frame,
              "a retired watchlist row is kept on file but not polled")
        check(("TN", "Statewide bill") not in frame,
              "statewide rows stay excluded from the frame")
        check(jurisdiction_frame(feed_path, "GA", wl_path)
              == [("GA", "Walker County")],
              "the state filter applies to the watchlist too")
        check(jurisdiction_frame(feed_path, None, os.path.join(td, "none.csv"))
              == feed_only,
              "an absent watchlist leaves the frame unchanged")
        check("clark" in ambiguous_county_names(feed_path, frame),
              "a watchlist county name that exists in two states is "
              "ambiguous, so it is never slug-guessed")
        check("clark" not in ambiguous_county_names(feed_path, feed_only),
              "the same name is unambiguous over the feed alone, which is "
              "why ambiguity must be judged over the whole frame")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--discover", action="store_true",
                    help="probe undetected jurisdictions and cache results")
    ap.add_argument("--redo", action="store_true",
                    help="re-probe jurisdictions already in the cache")
    ap.add_argument("--fetch", action="store_true",
                    help="pull events for cached/override jurisdictions")
    ap.add_argument("--state", help="limit to one two-letter state code")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    state_filter = args.state.strip().upper() if args.state else None

    if args.discover:
        discover(state_filter, args.redo)
        return 0

    if args.fetch:
        rows = fetch(state_filter)
        write_csv(OUT_FEED, rows, FEED_COLS)
        print(f"fetch: {len(rows)} meeting items -> {os.path.relpath(OUT_FEED, ROOT)}")
        leak_audit([OUT_FEED])
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
