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

civic-scraper (spec 007, 2026-09-29). Discovery now asks civic-scraper's
CivicPlus, PrimeGov and Granicus adapters first, then the native probes. It is
imported only in the discovery layer, inside a try, and anything it raises is
logged into the cache entry and falls back to the native probes (FR-001).
Legistar is never asked of it; probe_legistar() here and legistar_probe.py are
the only Legistar clients. CivicPlus hosts carry the state
(va-powhatancounty.civicplus.com), so CivicPlus is also tried for names that
are cross-state ambiguous. Each entry records adapter
(civic_scraper:<platform> or native:<platform>) and civic_scraper (the library
version tried, or "unavailable"); a cached miss that civic-scraper never saw
is re-probed exactly once when the library is importable.

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
  python3 local_meeting_feed.py --discover --no-civic-scraper --max-probes 100
  python3 local_meeting_feed.py --compare 50     # with vs without civic-scraper
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
    """The clean feed's jurisdictions unioned with the adjacency watchlist.

    A pair with no state is dropped. A bare county name does not identify a
    jurisdiction: "Benton" exists in AR, IA, IN, MN, MO, MS, OR, TN and WA, so
    there is nothing to probe and nothing a result could be attributed to.

    Keeping them cost more than the wasted probe. 50 stateless pairs reached
    the discovery cache as keys like "::Beaver", each carrying a checked_at
    that told the next run the county was already done. Worse, they poisoned
    ambiguous_county_names(): a stateless ghost contributes an empty string to
    the set of states a name belongs to, so a county in exactly ONE real state
    plus a ghost read as {"", "XX"}, length two, and was excluded from
    auto-detection as if it were genuinely ambiguous. Sixteen single-state
    counties were suppressed that way, among them Beaver, Caddo, Escambia,
    Forsyth and Linn, and the suppression was invisible because the ghost and
    the real county looked like one ambiguous name.
    """
    pairs = set(jurisdictions_from_feed(feed_path, state_filter))
    pairs |= set(jurisdictions_from_watchlist(watchlist_path, state_filter))
    return sorted((s, c) for s, c in pairs if (s or "").strip() and (c or "").strip())


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
        # A blank state is not a state. Counting it would inflate a county
        # that exists in exactly one real state into a two-state name and
        # exclude it from auto-detection, which is what 50 stateless cache
        # ghosts did to 16 real counties. jurisdiction_frame() now drops these
        # before they get here, but this function is also called with caller
        # supplied pairs, and the arithmetic it does is what actually breaks,
        # so it defends itself rather than trusting every caller to.
        state = (state or "").strip()
        if not state:
            continue
        bare = re.sub(r"\b(county|borough|parish|municipality)\b", "", county,
                      flags=re.IGNORECASE).strip().lower()
        states_by_name.setdefault(bare, set()).add(state)
    return {name for name, states in states_by_name.items() if len(states) > 1}


def jur_key(state: str, county: str) -> str:
    """Cache key for one jurisdiction.

    Refuses a blank state. The frame already drops stateless pairs, but this is
    the function that mints the cache key, and a key like "::Beaver" is not a
    cache miss waiting to be filled: it is a permanent wrong answer that also
    records checked_at, so it suppresses the retry that would fix it. Failing
    loudly here means a future caller that skips the frame cannot reintroduce
    the class.
    """
    state = (state or "").strip()
    county = (county or "").strip()
    if not state or not county:
        raise ValueError(
            f"jur_key needs both a state and a county, got {state!r}, {county!r}. "
            "A bare county name does not identify a jurisdiction.")
    return f"{state}::{county}"


def purge_stateless_cache_entries(cache: dict) -> list[str]:
    """Drop cache entries whose key carries no state. Returns what was removed.

    These can only have come from the defect fixed above, and leaving them is
    not neutral: each one holds a checked_at that suppresses a real probe, and
    each one inflates its county name into a false ambiguity.
    """
    dead = [k for k in cache if not k.split("::", 1)[0].strip()]
    for k in dead:
        cache.pop(k, None)
    return sorted(dead)


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
# The native probes cannot guess a CivicPlus domain; civic-scraper below can,
# because CivicPlus hosts every client at {state}-{name}.civicplus.com.


# ---------------------------------------------------------------------------
# civic-scraper discovery layer (spec 007, FR-001)
#
# civic-scraper is imported here and nowhere else, inside a try, and only for
# the three platforms legistar_probe.py does not cover. Legistar is never asked
# of it: the native probe_legistar() above and legistar_probe.py are the only
# Legistar clients in the repo. Anything civic-scraper raises (import failure,
# API drift, a parser that meets an unexpected feed) is logged into the cache
# entry and discovery falls through to the native probes.
#
# A hit records the EXISTING platform name and a base URL the existing fetcher
# understands, so --fetch never touches civic-scraper.
# ---------------------------------------------------------------------------

CIVIC_SCRAPER_LOOKBACK_DAYS = 120
CIVIC_SCRAPER_TIMEOUT_S = 20
# Transport failures on a guessed host mean "no such client", which is the
# common case and not worth a log line. Anything else civic-scraper raises
# (a parser meeting a feed it did not expect, API drift) is logged and kept
# in the cache entry.
QUIET_MISSES = {"ConnectionError", "ProxyError", "SSLError", "ConnectTimeout",
                "ReadTimeout", "Timeout", "HTTPError", "TooManyRedirects"}


def _bare_slug(county: str) -> str:
    return slugify(strip_county_words(county))


def _civicplus_urls(state: str, county: str) -> list[str]:
    """{st}-{name}county, then {st}-{name}. The state is part of the host, so
    a hit cannot be another state's client: this is the one platform that is
    safe to try for a cross-state-ambiguous county name."""
    st, slug = (state or "").strip().lower(), _bare_slug(county)
    if not st or not slug:
        return []
    hosts = [f"{st}-{slug}county", f"{st}-{slug}"]
    return [f"https://{h}.civicplus.com/AgendaCenter"
            for h in dict.fromkeys(hosts)]


def _primegov_urls(state: str, county: str) -> list[str]:
    slug = _bare_slug(county)
    return [f"https://{slug}.primegov.com/public/portal"] if slug else []


def _granicus_urls(state: str, county: str) -> list[str]:
    slug = _bare_slug(county)
    return ([f"https://{slug}.granicus.com/ViewPublisherRSS.php?view_id=1&mode=agendas"]
            if slug else [])


def _host_base(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# name -> (civic_scraper.platforms class, candidate URLs, cached platform,
#          state-qualified host). Order is the probe order.
CIVIC_SCRAPER_PLATFORMS = {
    "civicplus": ("CivicPlusSite", _civicplus_urls, "civicplus_rss", True),
    "primegov": ("PrimeGovSite", _primegov_urls, "primegov", False),
    "granicus": ("GranicusSite", _granicus_urls, "granicus", False),
}


def civic_scraper_platforms_module():
    """civic_scraper.platforms, or None when the library is not importable.
    Any exception on import counts as not importable."""
    try:
        import civic_scraper.platforms as platforms  # noqa: PLC0415
        return platforms
    except Exception:
        return None


def civic_scraper_version(lib=None) -> str | None:
    if lib is None:
        lib = civic_scraper_platforms_module()
    if lib is None:
        return None
    try:
        import civic_scraper  # noqa: PLC0415
        return str(getattr(civic_scraper, "__version__", "") or "unknown")
    except Exception:
        return "unknown"


def _civic_scrape(lib, name: str, url: str) -> int:
    """Number of assets civic-scraper finds at url in the lookback window.
    Raises whatever civic-scraper raises; the caller catches."""
    import tempfile  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415

    cls_name = CIVIC_SCRAPER_PLATFORMS[name][0]
    cls = getattr(lib, cls_name)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=CIVIC_SCRAPER_LOOKBACK_DAYS)
    with tempfile.TemporaryDirectory() as td:
        cache = None
        try:
            from civic_scraper.base.cache import Cache  # noqa: PLC0415
            cache = Cache(td)
        except Exception:
            cache = None
        site = cls(url, cache=cache) if cache is not None else cls(url)
        if name == "civicplus":
            assets = site.scrape(start_date=start.isoformat(),
                                 end_date=end.isoformat(),
                                 timeout=CIVIC_SCRAPER_TIMEOUT_S)
        elif name == "primegov":
            assets = site.scrape(start_date=start.strftime("%m/%d/%Y"),
                                 end_date=end.strftime("%m/%d/%Y"),
                                 timeout=CIVIC_SCRAPER_TIMEOUT_S)
        else:
            assets = site.scrape(download=False, timeout=CIVIC_SCRAPER_TIMEOUT_S)
    return len(assets or [])


def probe_civic_scraper(state: str, county: str, ambiguous: bool,
                        lib=None) -> tuple[dict | None, str, list[str]]:
    """Try civic-scraper's CivicPlus, PrimeGov and Granicus adapters.

    Returns (result or None, civic-scraper version or "unavailable", errors).
    A platform resolves only when scrape() returns at least one asset: a
    provisioned-but-empty instance looks the same as a placeholder page. For a
    cross-state-ambiguous name only CivicPlus is tried, because only its host
    carries the state. Never raises."""
    if lib is None:
        lib = civic_scraper_platforms_module()
    version = civic_scraper_version(lib)
    if lib is None or version is None:
        return None, "unavailable", []
    errors: list[str] = []
    for name, (_, urls_for, platform, state_qualified) in CIVIC_SCRAPER_PLATFORMS.items():
        if ambiguous and not state_qualified:
            continue
        for url in urls_for(state, county):
            time.sleep(THROTTLE_S)
            try:
                n = _civic_scrape(lib, name, url)
            except Exception as e:  # noqa: BLE001 - FR-001: log, never raise
                if type(e).__name__ in QUIET_MISSES:
                    continue       # no such host, or it refused: a plain miss
                errors.append(f"{name}: {type(e).__name__}")
                print(f"  civic-scraper {name} {jur_key(state, county)}: "
                      f"{type(e).__name__}: {str(e)[:120]} (falling back)")
                continue
            if n > 0:
                return ({"platform": platform, "base_url": _host_base(url),
                         "adapter": f"civic_scraper:{name}"}, version, errors)
    return None, version, errors


def needs_civic_retry(entry: dict, civic_available: bool) -> bool:
    """A cached miss that civic-scraper has never been tried on gets exactly
    one more probe once the library is importable. Resolved entries never do."""
    if not civic_available:
        return False
    if entry.get("platform") not in ("none", "ambiguous"):
        return False
    return (entry.get("civic_scraper") or "unavailable") == "unavailable"


def discover_one(state: str, county: str, ambiguous_names: set[str],
                 use_civic_scraper: bool = True, civic_lib=None) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    bare = re.sub(r"\b(county|borough|parish|municipality)\b", "", county,
                  flags=re.IGNORECASE).strip().lower()
    ambiguous = bare in ambiguous_names

    version, errors = "unavailable", []
    if use_civic_scraper:
        hit, version, errors = probe_civic_scraper(state, county, ambiguous,
                                                   lib=civic_lib)
        if hit:
            hit.update({"checked_at": now, "civic_scraper": version})
            if errors:
                hit["civic_scraper_errors"] = "; ".join(errors)
            return hit

    def miss(platform: str) -> dict:
        out = {"platform": platform, "base_url": "", "adapter": "none",
               "checked_at": now, "civic_scraper": version}
        if errors:
            out["civic_scraper_errors"] = "; ".join(errors)
        return out

    if ambiguous:
        # This bare name is shared by another state in the roster and the
        # source APIs don't expose a state field to disambiguate a slug
        # match, so this jurisdiction is skipped rather than risking a
        # wrong-state match; only a manual override entry can cover it.
        return miss("ambiguous")
    for probe in PROBES:
        time.sleep(THROTTLE_S)
        result = probe(county)
        if result:
            result.update({"adapter": f"native:{result['platform']}",
                           "checked_at": now, "civic_scraper": version})
            if errors:
                result["civic_scraper_errors"] = "; ".join(errors)
            return result
    return miss("none")


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


RESOLVED_EXCLUDE = ("none", "ambiguous")


def adapter_counts(cache: dict) -> dict[str, int]:
    """Resolved entries by adapter. Entries written before spec 007 carry no
    adapter key and were all found by a native probe."""
    out: dict[str, int] = {}
    for v in cache.values():
        if v.get("platform") in RESOLVED_EXCLUDE:
            continue
        a = v.get("adapter") or f"native:{v.get('platform')}"
        out[a] = out.get(a, 0) + 1
    return out


def discover(state_filter: str | None, redo: bool,
             use_civic_scraper: bool = True, max_probes: int = 400) -> dict:
    cache = load_json(DISCOVERY_CACHE)
    civic_version = civic_scraper_version() if use_civic_scraper else None
    civic_available = civic_version is not None
    print(f"civic-scraper: {civic_version or 'not used'}")
    feed_pairs = jurisdictions_from_feed(FEED, state_filter)
    pairs = jurisdiction_frame(FEED, state_filter)
    watch_only = len(pairs) - len(set(feed_pairs))
    ambiguous_names = ambiguous_county_names(
        FEED, jurisdiction_frame(FEED, state_filter=None))
    # Clear out keys minted before jur_key rejected a blank state. Each held a
    # checked_at that suppressed the real probe for that county.
    purged = purge_stateless_cache_entries(cache)
    if purged:
        print(f"purged {len(purged)} stateless discovery cache entr"
              f"{'y' if len(purged) == 1 else 'ies'} "
              f"(e.g. {', '.join(purged[:3])})")

    checked = deferred = 0
    for state, county in pairs:
        key = jur_key(state, county)
        if key in cache and not redo and not needs_civic_retry(cache[key], civic_available):
            continue
        if checked >= max_probes:
            deferred += 1
            continue
        cache[key] = discover_one(state, county, ambiguous_names,
                                  use_civic_scraper=civic_available)
        checked += 1
        if checked % 10 == 0:
            save_json(DISCOVERY_CACHE, cache)  # incremental: survives interruption
    save_json(DISCOVERY_CACHE, cache)
    if deferred:
        print(f"discovery: {deferred} jurisdictions deferred to the next run "
              f"(--max-probes {max_probes})")
    by_adapter = adapter_counts(cache)
    print("resolved by adapter: " + (", ".join(
        f"{k} {n}" for k, n in sorted(by_adapter.items())) or "none"))
    found = sum(1 for v in cache.values() if v.get("platform") not in RESOLVED_EXCLUDE)
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

    result = discover_one("OH", "Clark County", ambiguous_names={"clark"},
                          use_civic_scraper=False)
    check(result["platform"] == "ambiguous",
          "discover_one skips probing for a cross-state-ambiguous county name")

    # --- civic-scraper discovery layer (spec 007) ---
    # A fake civic_scraper.platforms: each Site class answers from a table
    # keyed by URL, so no test touches the network or needs the package.
    import types

    def fake_lib(answers: dict):
        lib = types.SimpleNamespace()
        calls: list[str] = []

        def site_cls(name):
            class _Site:
                def __init__(self, url, cache=None):
                    self.url = url
                    calls.append(url)

                def scrape(self, *a, **k):
                    ans = answers.get(self.url, 0)
                    if isinstance(ans, Exception):
                        raise ans
                    return ["asset"] * ans
            _Site.__name__ = name
            return _Site
        for cls_name, *_ in CIVIC_SCRAPER_PLATFORMS.values():
            setattr(lib, cls_name, site_cls(cls_name))
        lib.calls = calls
        return lib

    check(all(name != "legistar" and "legistar" not in cls.lower()
              for name, (cls, *_rest) in CIVIC_SCRAPER_PLATFORMS.items()),
          "civic-scraper is never asked about Legistar (legistar_probe.py covers it)")
    check(all(plat in FETCHERS for _, _, plat, _ in CIVIC_SCRAPER_PLATFORMS.values()),
          "every civic-scraper hit caches a platform an existing fetcher polls")

    with mock.patch(f"{__name__}.time.sleep"), \
         mock.patch(f"{__name__}.civic_scraper_version", return_value="1.1.0"), \
         mock.patch(f"{__name__}.http_get", return_value=(404, b"")):
        lib = fake_lib({"https://mesa.primegov.com/public/portal": 3})
        r = discover_one("AZ", "Mesa County", set(), civic_lib=lib)
        check(r["platform"] == "primegov" and r["adapter"] == "civic_scraper:primegov",
              "a civic-scraper hit records adapter=civic_scraper:<platform>")
        check(r["base_url"] == "https://mesa.primegov.com" and r["civic_scraper"] == "1.1.0",
              "the hit caches the existing fetcher's base URL and the library version")
        check(not needs_civic_retry(r, True),
              "a resolved jurisdiction is not re-probed on later runs")

        lib = fake_lib({"https://va-powhatancounty.civicplus.com/AgendaCenter": 2})
        r = discover_one("VA", "Powhatan County", set(), civic_lib=lib)
        check(r["platform"] == "civicplus_rss"
              and r["base_url"] == "https://va-powhatancounty.civicplus.com",
              "CivicPlus resolves on the state-qualified host and maps to civicplus_rss")

        lib = fake_lib({"https://va-clarkcounty.civicplus.com/AgendaCenter": 1,
                        "https://clark.primegov.com/public/portal": 5})
        r = discover_one("VA", "Clark County", {"clark"}, civic_lib=lib)
        check(r["platform"] == "civicplus_rss",
              "an ambiguous name can resolve through CivicPlus, whose host names the state")
        check(not any("primegov" in u or "granicus" in u for u in lib.calls),
              "an ambiguous name never tries a slug-only platform")

        lib = fake_lib({"https://ohio.primegov.com/public/portal": 0})
        r = discover_one("OH", "Clark County", {"clark"}, civic_lib=lib)
        check(r["platform"] == "ambiguous" and r["civic_scraper"] == "1.1.0",
              "an ambiguous miss stays ambiguous and records that civic-scraper was tried")

        class ConnectionError(Exception):  # noqa: A001 - the name is the test
            pass
        lib = fake_lib({"https://az-mesacounty.civicplus.com/AgendaCenter": ConnectionError(),
                        "https://mesa.primegov.com/public/portal": ValueError("drift"),
                        "https://mesa.granicus.com/ViewPublisherRSS.php?view_id=1&mode=agendas":
                            KeyError("title")})
        legistar_ok = json.dumps([{"BodyName": "Mesa County Board"}]).encode()
        with mock.patch(f"{__name__}.http_get", return_value=(200, legistar_ok)):
            r = discover_one("AZ", "Mesa County", set(), civic_lib=lib)
        check(r["platform"] == "legistar" and r["adapter"] == "native:legistar",
              "when civic-scraper raises, the native probes run and resolve")
        check("primegov: ValueError" in r.get("civic_scraper_errors", "")
              and "granicus: KeyError" in r.get("civic_scraper_errors", ""),
              "the civic-scraper errors are logged in the entry, not raised")
        check("civicplus" not in r.get("civic_scraper_errors", ""),
              "a guessed host that does not exist is a quiet miss, not a logged error")

        r = discover_one("AZ", "Mesa County", set(), civic_lib=fake_lib({}))
        check(r["platform"] == "none" and r["adapter"] == "none",
              "no platform anywhere records none")

    with mock.patch(f"{__name__}.civic_scraper_platforms_module", return_value=None), \
         mock.patch(f"{__name__}.time.sleep"), \
         mock.patch(f"{__name__}.http_get", return_value=(404, b"")):
        hit, ver, errs = probe_civic_scraper("AZ", "Mesa County", False)
        check(hit is None and ver == "unavailable" and errs == [],
              "an unimportable civic-scraper is a silent fallback, not an error")
        r = discover_one("AZ", "Mesa County", set())
        check(r["civic_scraper"] == "unavailable",
              "an entry probed without the library is marked for one later retry")

    check(needs_civic_retry({"platform": "none"}, True)
          and needs_civic_retry({"platform": "ambiguous", "civic_scraper": "unavailable"}, True),
          "cached misses civic-scraper never saw get one retry")
    check(not needs_civic_retry({"platform": "none", "civic_scraper": "1.1.0"}, True),
          "a miss civic-scraper already tried is not retried")
    check(not needs_civic_retry({"platform": "none"}, False),
          "no retry when the library is not importable")
    check(adapter_counts({"a": {"platform": "legistar"},
                          "b": {"platform": "primegov", "adapter": "civic_scraper:primegov"},
                          "c": {"platform": "none"}})
          == {"native:legistar": 1, "civic_scraper:primegov": 1},
          "resolved counts split by adapter, legacy entries counted as native")
    check(_civicplus_urls("VA", "Powhatan County")
          == ["https://va-powhatancounty.civicplus.com/AgendaCenter",
              "https://va-powhatan.civicplus.com/AgendaCenter"],
          "CivicPlus candidate hosts carry the state")

    check(slugify("Powhatan County") == "powhatancounty", "slugify strips spaces/case")
    check(slugify("Wyandotte County, Unified Government") == "wyandottecountyunifiedgovernment",
          "slugify strips punctuation")
    check(jur_key("VA", "Powhatan County") == "VA::Powhatan County", "jur_key format")

    # A stateless key is a permanent wrong answer that also records checked_at,
    # so minting one must fail rather than be filled in later.
    for bad_state, bad_county in (("", "Beaver"), ("  ", "Beaver"),
                                  ("VA", ""), ("VA", "   ")):
        try:
            jur_key(bad_state, bad_county)
            check(False, f"jur_key rejects ({bad_state!r}, {bad_county!r})")
        except ValueError:
            check(True, f"jur_key rejects ({bad_state!r}, {bad_county!r})")

    # The frame drops stateless pairs rather than passing them to the prober.
    framed = jurisdiction_frame.__doc__ or ""
    check("no state is dropped" in framed or "with no state is dropped" in framed,
          "jurisdiction_frame documents the stateless drop")

    # Purging is what un-suppresses the counties a ghost made look ambiguous.
    ghost_cache = {"::Beaver": {"platform": "none"},
                   "  ::Caddo": {"platform": "none"},
                   "UT::Beaver County": {"platform": "legistar"}}
    removed = purge_stateless_cache_entries(ghost_cache)
    check(removed == ["  ::Caddo", "::Beaver"], "purge removes only stateless keys")
    check(list(ghost_cache) == ["UT::Beaver County"],
          "purge leaves well-formed keys intact")
    check(purge_stateless_cache_entries({"UT::Beaver County": {}}) == [],
          "purge is a no-op on a clean cache")

    # A stateless ghost must not inflate a single-state county into an
    # ambiguous one. This is the second-order defect: 16 real counties were
    # excluded from auto-detection because a ghost shared their name.
    with_ghost = [("", "Beaver"), ("UT", "Beaver County"), ("NV", "Clark County"),
                  ("OH", "Clark County")]
    amb_with_ghost = ambiguous_county_names(FEED, with_ghost)
    check("beaver" not in amb_with_ghost,
          "a stateless ghost does not make a single-state county ambiguous")
    check("clark" in amb_with_ghost,
          "a genuinely two-state county is still ambiguous")

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


def compare_sample(n: int, state_filter: str | None = None,
                   civic_lib=None) -> tuple[int, int, int]:
    """Spec 007 US1 independent test. Runs discovery on a fixed n-jurisdiction
    sample (an even stride through the sorted frame, so every run and every
    machine sees the same sample) with and without civic-scraper, in memory.
    Returns (sample size, resolved without, resolved with). Writes nothing."""
    pairs = jurisdiction_frame(FEED, state_filter)
    stride = max(1, len(pairs) // max(1, n))
    sample = pairs[::stride][:n]
    ambiguous = ambiguous_county_names(FEED, jurisdiction_frame(FEED, None))
    without = with_cs = 0
    for state, county in sample:
        a = discover_one(state, county, ambiguous, use_civic_scraper=False)
        b = discover_one(state, county, ambiguous, use_civic_scraper=True,
                         civic_lib=civic_lib)
        without += a["platform"] not in RESOLVED_EXCLUDE
        with_cs += b["platform"] not in RESOLVED_EXCLUDE
        print(f"  {jur_key(state, county)}: without={a['platform']} "
              f"with={b['platform']} ({b.get('adapter', '')})")
    return len(sample), without, with_cs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--discover", action="store_true",
                    help="probe undetected jurisdictions and cache results")
    ap.add_argument("--redo", action="store_true",
                    help="re-probe jurisdictions already in the cache")
    ap.add_argument("--fetch", action="store_true",
                    help="pull events for cached/override jurisdictions")
    ap.add_argument("--state", help="limit to one two-letter state code")
    ap.add_argument("--no-civic-scraper", action="store_true",
                    help="discovery with the native probes only")
    ap.add_argument("--max-probes", type=int, default=400,
                    help="cap jurisdictions probed per --discover run")
    ap.add_argument("--compare", type=int, metavar="N",
                    help="resolved counts on a fixed N-jurisdiction sample, "
                         "with and without civic-scraper; writes nothing")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    state_filter = args.state.strip().upper() if args.state else None

    if args.compare:
        if civic_scraper_version() is None:
            print("civic-scraper is not importable; install it to compare.")
            return 0
        size, without, with_cs = compare_sample(args.compare, state_filter)
        print(f"compare: {size} jurisdictions, resolved without civic-scraper "
              f"{without}, with civic-scraper {with_cs}")
        return 0

    if args.discover:
        discover(state_filter, args.redo,
                 use_civic_scraper=not args.no_civic_scraper,
                 max_probes=args.max_probes)
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
