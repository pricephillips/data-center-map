#!/usr/bin/env python3
"""
status_followup.py

Looks for later news coverage of every pending local restriction, so a vote
that happened after the harvested article was written is found without anyone
searching for it.

Why this module exists
----------------------
status_resolution.py scans the headline a row was harvested with. That
headline is usually the earliest coverage ("Southport pushes for a data center
moratorium"), so the scan cannot see the vote that came later. On 2026-09-28,
4 of the 10 rows it proposed (Delta County MI, Southport NC, Manitowoc County
WI, Cranford NJ) turned out to be adopted, which only later coverage showed.

What it does
------------
For each pending local restrictive row (status_resolution.is_candidate), it
groups rows by place (City when set, else County) and state, and searches
Google News RSS for "<place>" <state name> data center. A headline counts as
an adoption when all of these hold:

  - it names the place;
  - it names a restrictive instrument and a completed action, with none of the
    words that mean the action was not final (status_resolution's INSTRUMENT,
    FINAL and NOT_FINAL patterns, plus "OKs", "puts" and "places");
  - it was published on or after the group's earliest row;
  - the outlet is not an aggregator or tracker site.

A lead is corroborated when two different outlets carry such a headline, or
one .gov source does.

Proposes only. Writes data/status_followup_leads.csv with, for each
corroborated place, the proposed action per master row (resolve the latest
row, supersede earlier coverage of the same instrument) and the headlines,
outlets and links behind it. Nothing here changes master_opposition.csv or
data/status_resolutions.csv; a reviewer or the daily research routine confirms
a lead by copying it into data/status_resolutions.csv.

Results are cached in data/status_followup_cache.json for 3 days per query, so
a daily build does not repeat every search.

Standing rules: stdlib only, additive, writes only its own files, LF line
endings, no em-dashes, --selftest offline.

Usage
  python status_followup.py                 search (network) + write leads
  python status_followup.py --offline       use the cache only
  python status_followup.py --max-fetch 50  cap network queries this run
  python status_followup.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

import status_resolution as SR

HERE = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(HERE, *parts)


LEADS_CSV = P("data", "status_followup_leads.csv")
CACHE_JSON = P("data", "status_followup_cache.json")

LEAD_FIELDS = ["place", "state", "proposal", "status", "opposition_type",
               "source_url", "row_date", "row_incident", "superseded_by",
               "evidence_url", "evidence_date", "n_outlets", "headlines"]

RSS = ("https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
CACHE_DAYS = 3
DEFAULT_MAX_FETCH = 150
FETCH_DELAY_S = 1.0
# Stop searching after this many failures in a row: the host is unreachable.
MAX_CONSECUTIVE_ERRORS = 3
# Earlier coverage is superseded only when it falls within this many days
# before the adoption headline; older rows may describe a different instrument.
SUPERSEDE_WINDOW_DAYS = 365

# Aggregators and trackers restate other coverage and are not independent.
AGGREGATORS = {
    "dmnews.com", "savrn.com", "datacenterbans.com", "interconnectedcapital.com",
    "gizmowarehouse.org", "programs.com", "scorepropertygroup.com",
    "citizenportal.ai", "msn.com", "yahoo.com", "news.google.com",
    "writing.strisker.com", "dcmap.us",
}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# Newsroom verbs the shared FINAL pattern does not carry.
FINAL_EXTRA = re.compile(
    r"\b(oks?|okays?|okayed)\b|"
    r"\b(puts?|places?|placed)\s+(an?\s+)?([\w-]+\s+){0,2}"
    r"(moratori\w*|pause|ban|halt|freeze)\b", re.I)
# Headline wording the shared NOT_FINAL pattern misses, found in the first
# live run (2026-09-28): a resolution "calling for" a pause, a moratorium "put
# on hold", a council that "takes a pass on" one, a "delay for" one, opinion
# pieces, and recommending bodies whose vote is not the adoption.
NOT_FINAL_EXTRA = re.compile(
    r"\b(calling|on hold|takes? a pass|pass(es)? on|delay\w*|postpone\w*|"
    r"opinion|editorial|letters?|planning (commission|board)|committee|"
    r"recommend\w*)\b", re.I)
EXTENDED = re.compile(r"\bextend(s|ed)?\b", re.I)
BAN_WORDS = re.compile(r"\b(ban|bans|banned|prohibit\w*)\b", re.I)
PAREN = re.compile(r"\s*\([^)]*\)")
COUNTY_SUFFIX = re.compile(r"\b(county|parish|borough)$", re.I)


# ---------------------------------------------------------------------------
# place and headline matching
# ---------------------------------------------------------------------------

def place_for(row: dict) -> str:
    """The place a row is about: its City when set and distinct from the
    county, else its county with the County suffix restored."""
    city = PAREN.sub("", row.get("City") or "").strip()
    county = PAREN.sub("", row.get("County") or "").strip()
    if city and SR.norm_county(city) != SR.norm_county(county):
        return city
    if county and not COUNTY_SUFFIX.search(county):
        county += " Parish" if (row.get("State") or "").strip().upper() == "LA" \
            else " County"
    return county


def domain_of(url: str) -> str:
    host = urllib.parse.urlparse((url or "").strip()).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_aggregator(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in AGGREGATORS)


def is_adoption_headline(title: str, place: str) -> bool:
    t = title or ""
    if place.lower() not in t.lower():
        return False
    if (not SR.INSTRUMENT.search(t) or SR.NOT_FINAL.search(t)
            or NOT_FINAL_EXTRA.search(t)):
        return False
    return bool(SR.FINAL.search(t) or FINAL_EXTRA.search(t))


def corroborated(items: list[dict]) -> bool:
    outlets = {i["domain"] for i in items}
    return len(outlets) >= 2 or any(d.endswith(".gov") for d in outlets)


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

def parse_rss(xml_text: str) -> list[dict]:
    """Items as {title, link, date, outlet, domain}. Google News titles end
    with " - <outlet>", which is stripped."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        outlet = (src.text or "").strip() if src is not None else ""
        src_url = src.get("url", "") if src is not None else ""
        if outlet and title.endswith(" - " + outlet):
            title = title[: -len(" - " + outlet)].strip()
        pub = None
        try:
            pub = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").date()
        except (TypeError, ValueError):
            pass
        link = (it.findtext("link") or "").strip()
        out.append({"title": title, "link": link,
                    "date": pub.isoformat() if pub else "",
                    "outlet": outlet, "domain": domain_of(src_url or link)})
    return out


def query_for(place: str, state: str) -> str:
    return f'"{place}" {STATE_NAMES.get(state, state)} data center'


def fetch(query: str) -> list[dict]:
    url = RSS.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 "
                                               "(data-center-map status_followup)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_rss(r.read().decode("utf-8", "replace"))


def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cache, fh, indent=1, sort_keys=True)
        fh.write("\n")


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------

def groups_from(master: list[dict], done: set) -> dict:
    """(place, state) -> pending candidate rows, one per Source URL. A group
    with any row already in a resolution file is left to the reviewer."""
    groups, seen, touched = defaultdict(list), set(), set()
    for r in master:
        if not SR.is_candidate(r):
            continue
        u = SR.normalize_url(r.get("Source URL", ""))
        place = place_for(r)
        state = (r.get("State") or "").strip().upper()
        if not u or not place or state not in STATE_NAMES:
            continue
        key = (place, state)
        if u in done:
            touched.add(key)
        if u in seen:
            continue
        seen.add(u)
        groups[key].append(r)
    return {k: v for k, v in groups.items() if k not in touched}


def leads_for(place: str, state: str, rows: list[dict], items: list[dict]) -> list[dict]:
    dated = [(SR.parse_date(r.get("Date", "")), r) for r in rows]
    dated = [(d, r) for d, r in dated if d]
    if not dated:
        return []
    since = min(d for d, _ in dated)
    hits = [i for i in items
            if i["date"] and dt.date.fromisoformat(i["date"]) >= since
            and not is_aggregator(i["domain"])
            and is_adoption_headline(i["title"], place)]
    if not corroborated(hits):
        return []
    hits.sort(key=lambda i: i["date"])
    first = dt.date.fromisoformat(hits[0]["date"])
    window = sorted([(d, r) for d, r in dated
                     if 0 <= (first - d).days <= SUPERSEDE_WINDOW_DAYS],
                    key=lambda x: x[0])
    if not window:
        return []
    status = "extended" if any(EXTENDED.search(h["title"]) for h in hits) else "passed"
    otype = "ban" if any(BAN_WORDS.search(h["title"]) for h in hits) else "moratorium"
    heads = " | ".join(f'{h["date"]} {h["outlet"] or h["domain"]}: {h["title"]}'
                       for h in hits[:4])
    latest_url = window[-1][1].get("Source URL", "")
    out = []
    for d, r in window:
        is_latest = r is window[-1][1]
        out.append({
            "place": place, "state": state,
            "proposal": "resolve" if is_latest else "supersede",
            "status": status if is_latest else "",
            "opposition_type": otype if is_latest else "",
            "source_url": r.get("Source URL", ""),
            "row_date": d.isoformat(),
            "row_incident": (r.get("Incident") or "")[:160],
            "superseded_by": "" if is_latest else latest_url,
            "evidence_url": hits[0]["link"], "evidence_date": hits[0]["date"],
            "n_outlets": len({h["domain"] for h in hits}),
            "headlines": heads[:900],
        })
    return out


def run(master: list[dict], done: set, cache: dict, fetcher, today: dt.date,
        max_fetch: int, offline: bool) -> tuple[list[dict], dict]:
    stats = {"groups": 0, "fetched": 0, "cached": 0, "skipped": 0, "errors": 0}
    attempts = consecutive_errors = 0
    leads = []
    groups = groups_from(master, done)
    stats["groups"] = len(groups)
    # Most recent coverage first, so a capped run spends its queries on the
    # places most likely to have just voted.
    order = sorted(groups.items(), key=lambda kv: max(
        (SR.parse_date(r.get("Date", "")) or dt.date.min) for r in kv[1]),
        reverse=True)
    for (place, state), rows in order:
        q = query_for(place, state)
        entry = cache.get(q)
        fresh = entry and (today - dt.date.fromisoformat(entry["fetched"])).days < CACHE_DAYS
        if (not fresh and not offline and attempts < max_fetch
                and consecutive_errors < MAX_CONSECUTIVE_ERRORS):
            attempts += 1
            try:
                items = fetcher(q)
                consecutive_errors = 0
                # Only headlines naming an instrument can ever match, so only
                # those are kept; the cache is committed on every build.
                items = [i for i in items if SR.INSTRUMENT.search(i.get("title", ""))]
                cache[q] = {"fetched": today.isoformat(), "items": items}
                entry = cache[q]
                stats["fetched"] += 1
                if FETCH_DELAY_S and fetcher is fetch:
                    time.sleep(FETCH_DELAY_S)
            except Exception as e:  # network errors never fail the build
                stats["errors"] += 1
                consecutive_errors += 1
                print(f"  ! {q}: {e.__class__.__name__}: {e}", file=sys.stderr)
        elif entry:
            stats["cached"] += 1
        if not entry:
            stats["skipped"] += 1
            continue
        leads += leads_for(place, state, rows, entry.get("items", []))
    leads.sort(key=lambda r: (r["state"], r["place"], r["proposal"] != "resolve",
                              r["row_date"]))
    return leads, stats


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _rss(items):
    body = "".join(
        f"<item><title>{t} - {o}</title><link>https://news.google.com/x/{n}</link>"
        f"<pubDate>{d}</pubDate><source url=\"{u}\">{o}</source></item>"
        for n, (t, o, u, d) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>"


def selftest() -> int:
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    check("place city", place_for({"City": "Southport", "County": "Brunswick County"}),
          "Southport")
    check("place county suffix", place_for({"County": "Palm Beach", "State": "FL"}),
          "Palm Beach County")
    check("place parish", place_for({"County": "Caddo", "State": "LA"}), "Caddo Parish")
    check("place parenthetical",
          place_for({"County": "Prince William County (Ashton/Amazon)", "State": "VA"}),
          "Prince William County")
    check("place city equals county",
          place_for({"City": "Delta County", "County": "Delta County"}), "Delta County")

    check("headline final", is_adoption_headline(
        "Delta County board OKs moratorium on data centers", "Delta County"), True)
    check("headline not final", is_adoption_headline(
        "Delta County could pause data centers", "Delta County"), False)
    check("headline initial", is_adoption_headline(
        "Delta County gives initial approval to moratorium", "Delta County"), False)
    # Real headlines from the first live run.
    for title, place, want in [
        ("City-County Council passes unanimous resolution calling for a pause on "
         "data center development in Indianapolis", "Indianapolis", False),
        ("Indianapolis data center moratorium put on hold", "Indianapolis", False),
        ("Fayetteville Approves 4-Month Delay for Data Center Moratorium",
         "Fayetteville", False),
        ("Fayetteville council takes a pass on a data center moratorium | Opinion",
         "Fayetteville", False),
        ("Cass County Planning Commission approves moratorium on data center "
         "development", "Cass County", False),
        ("Warrick county puts pause on data center development", "Warrick County", True),
        ("Pima County supervisors impose 120-day moratorium on data center projects",
         "Pima County", True),
        ("Oshkosh Common Council passes temporary moratorium on data centers",
         "Oshkosh", True),
        ("'No-brainer': Harford County Council unanimously votes to ban data "
         "centers in all zoning districts", "Harford County", True),
    ]:
        check(f"live headline: {title[:40]}", is_adoption_headline(title, place), want)
    check("headline other place", is_adoption_headline(
        "Marquette County passes data center moratorium", "Delta County"), False)

    xml = _rss([
        ("Delta County Board passes moratorium on data centers", "Daily Press",
         "https://www.dailypress.net", "Wed, 22 Jul 2026 10:00:00 GMT"),
        ("Delta County board OKs moratorium on data centers", "The Mining Journal",
         "https://www.miningjournal.net", "Thu, 23 Jul 2026 10:00:00 GMT"),
        ("Delta County passes moratorium on data centers", "DM News",
         "https://dmnews.com", "Fri, 24 Jul 2026 10:00:00 GMT"),
        ("Delta County could prevent data center development", "WLUC",
         "https://www.uppermichiganssource.com", "Tue, 03 Mar 2026 10:00:00 GMT"),
    ])
    items = parse_rss(xml)
    check("rss count", len(items), 4)
    check("rss title stripped", items[0]["title"],
          "Delta County Board passes moratorium on data centers")
    check("rss domain", items[1]["domain"], "miningjournal.net")
    check("rss date", items[0]["date"], "2026-07-22")

    master = [
        {"Incident": "Delta County could prevent data centers", "City": "",
         "County": "Delta County", "State": "MI", "Date": "2026-03-03",
         "Status": "pending", "Scope": "local", "Opposition Type": "moratorium",
         "Source URL": "https://a.example/delta-march"},
        {"Incident": "Delta County planning commission backs pause", "City": "",
         "County": "Delta County", "State": "MI", "Date": "2026-07-07",
         "Status": "pending", "Scope": "local", "Opposition Type": "moratorium",
         "Source URL": "https://a.example/delta-july"},
        {"Incident": "Other County weighs pause", "City": "",
         "County": "Other County", "State": "MI", "Date": "2026-07-07",
         "Status": "pending", "Scope": "local", "Opposition Type": "moratorium",
         "Source URL": "https://a.example/other"},
    ]
    feeds = {query_for("Delta County", "MI"): items,
             query_for("Other County", "MI"): parse_rss(_rss([
                 ("Other County passes data center moratorium", "One Paper",
                  "https://one.example", "Wed, 22 Jul 2026 10:00:00 GMT")]))}
    leads, stats = run(master, set(), {}, lambda q: feeds[q], dt.date(2026, 9, 28),
                       10, False)
    by = {l["source_url"]: l for l in leads}
    check("lead count", len(leads), 2)
    check("latest resolves", by["https://a.example/delta-july"]["proposal"], "resolve")
    check("status passed", by["https://a.example/delta-july"]["status"], "passed")
    check("earlier supersedes", by["https://a.example/delta-march"]["proposal"],
          "supersede")
    check("superseded_by", by["https://a.example/delta-march"]["superseded_by"],
          "https://a.example/delta-july")
    check("aggregator ignored", by["https://a.example/delta-july"]["n_outlets"], 2)
    check("single outlet not corroborated", "https://a.example/other" in by, False)
    check("fetched", stats["fetched"], 2)

    # A group a reviewer already touched is left alone.
    leads2, _ = run(master, {SR.normalize_url("https://a.example/delta-march")},
                    {}, lambda q: feeds[q], dt.date(2026, 9, 28), 10, False)
    check("reviewed group skipped",
          any(l["place"] == "Delta County" for l in leads2), False)

    # Cache: a fresh entry is reused, offline never fetches.
    cache = {query_for("Delta County", "MI"): {"fetched": "2026-09-27", "items": items}}
    _, st3 = run(master, set(), cache, lambda q: 1 / 0, dt.date(2026, 9, 28), 10, True)
    check("cache used", st3["cached"], 1)
    check("offline no fetch", st3["fetched"], 0)

    # An unreachable host stops after MAX_CONSECUTIVE_ERRORS attempts.
    def down(q):
        raise OSError("403")
    many = [dict(master[2], County=f"Place{i} County",
                 **{"Source URL": f"https://a.example/p{i}"}) for i in range(6)]
    _, st4 = run(many, set(), {}, down, dt.date(2026, 9, 28), 10, False)
    check("stops when unreachable", st4["errors"], MAX_CONSECUTIVE_ERRORS)

    # .gov alone corroborates.
    gov = parse_rss(_rss([("Town of Hope adopts data center moratorium", "Town of Hope",
                           "https://www.hope.gov", "Wed, 22 Jul 2026 10:00:00 GMT")]))
    check(".gov corroborates", corroborated([i for i in gov]), True)

    if failures:
        print("status_followup.py selftest: FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("status_followup.py selftest: OK")
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use the cache only")
    ap.add_argument("--max-fetch", type=int, default=DEFAULT_MAX_FETCH)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not os.path.exists(SR.MASTER_CSV):
        print("master_opposition.csv absent; nothing to do")
        return 0
    with open(SR.MASTER_CSV, newline="", encoding="utf-8-sig") as fh:
        master = list(csv.DictReader(fh))
    resolutions, _ = SR.validate(SR.read_csv(SR.RESOLUTIONS_CSV))
    done = {SR.normalize_url(r["source_url"]) for r in resolutions}
    cache = load_cache(CACHE_JSON)
    leads, stats = run(master, done, cache, fetch, dt.date.today(),
                       args.max_fetch, args.offline)
    save_cache(cache, CACHE_JSON)
    SR.write_csv(leads, LEADS_CSV, LEAD_FIELDS)
    n_res = sum(1 for l in leads if l["proposal"] == "resolve")
    print(f"status follow-up: {stats['groups']} pending place(s); "
          f"{stats['fetched']} searched, {stats['cached']} from cache, "
          f"{stats['skipped']} not yet searched, {stats['errors']} error(s)")
    print(f"wrote {os.path.relpath(LEADS_CSV, HERE)}: {n_res} corroborated "
          f"adoption lead(s), {len(leads) - n_res} earlier row(s) to supersede. "
          f"Confirm in data/status_resolutions.csv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
