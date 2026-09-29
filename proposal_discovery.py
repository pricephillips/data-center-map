#!/usr/bin/env python3
"""
proposal_discovery.py -- news discovery for Layer B, proposed projects.

The gap
-------
Every proposed project in data/proposals.csv came from one tracker. When that
tracker has not yet listed a project, the repository does not know it exists,
however loudly the local paper is covering the rezoning. signal_harvest.py
already reads news, but for opposition (Layer C); the facility-lifecycle
headlines it sees are routed to Layer A. Nothing reads the news for the
thing Layer B is about: a new proposal, a land deal, a rezoning application,
a tax-abatement agreement, a utility load letter.

What it does
------------
Runs a proposal-specific query set against GDELT (the same free API and
helpers signal_harvest.py uses) and, where reachable, Google News RSS. For
each article it:

  - geolocates the headline with the repository's county and place indexes
  - extracts the numbers a proposal is described by: MW or GW, acres, dollar
    investment, square footage
  - names the developer when the headline carries a known one (the developer
    vocabulary is read from data/proposals.csv itself, so it grows with it)
  - matches it against every existing project: same state, and either the
    same county with a shared name or developer token, or a strong name match

and writes it to one of two outcomes:

  corroborates   the article is about a project already tracked. It adds an
                 independent source to that project and feeds the corroboration
                 count in proposal_enrichment.py.
  new_candidate  the article looks like a proposal nobody tracks. The review
                 queue, ranked: a located county plus a size number plus a
                 proposal verb ranks highest.

Nothing here writes data/proposals_added.csv. A candidate becomes a project
only when a person adds it there with a source, which is the same
defensibility rule the opposition harvester follows.

Outputs
  data/proposal_candidates_news.csv       current queue, merge-preserving:
                                          first_seen survives reruns and a
                                          reviewer's `review` column is kept
  data/proposal_candidates_news_log.csv   append-only run log

Usage
  python proposal_discovery.py --days 7
  python proposal_discovery.py --fixture path.json
  python proposal_discovery.py --selftest

Stdlib only. Network: api.gdeltproject.org, news.google.com.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime

import signal_harvest as sh

try:
    import gazetteer
except Exception:                                   # the index is optional
    gazetteer = None

ROOT = os.path.dirname(os.path.abspath(__file__))
PROPOSALS = os.path.join(ROOT, "data", "proposals.csv")
OUT_CSV = os.path.join(ROOT, "data", "proposal_candidates_news.csv")
LOG_CSV = os.path.join(ROOT, "data", "proposal_candidates_news_log.csv")
GNEWS = "https://news.google.com/rss/search"

# Proposal-side queries. Each is narrow enough to review in a sitting and aimed
# at the pre-announcement and early-approval paper trail, where the tracker
# lags most.
QUERIES = [
    ("proposed", '"data center" (proposed OR proposal OR plans OR planned) (acres OR megawatts OR MW OR campus)'),
    ("rezoning_app", '"data center" (rezoning application OR "site plan" OR "conditional use permit" OR "special use permit")'),
    ("land", '"data center" ("land purchase" OR "purchased" OR "bought" OR "under contract") acres'),
    ("incentives", '"data center" ("tax abatement" OR PILOT OR "tax incentive" OR "enterprise zone" OR "sales tax exemption")'),
    ("utility_load", '"data center" ("load letter" OR "large load" OR substation OR "electric service agreement")'),
    ("hyperscale", '(hyperscale OR "AI campus" OR "AI data center" OR gigawatt) (county OR township OR city council)'),
    ("codename", '"Project" "data center" (codename OR "code name" OR "known as Project")'),
]

PROPOSAL_VERB = re.compile(
    r"\b(propos\w*|plans?|planned|seeks?|applies|application|rezon\w*|annex\w*|"
    r"site plan|abatement|pilot|incentive|purchase[sd]?|acquir\w*|bought|"
    r"announc\w*|unveil\w*|eyes?|eyed|considers?|coming to|could bring)\b", re.I)
NOT_PROPOSAL = re.compile(r"\b(stock|shares|earnings|quarterly|nasdaq|nyse|analyst)\b", re.I)

MW_RE = re.compile(r"(\d[\d,.]*)\s*(gigawatts?|gw|megawatts?|mw)\b", re.I)
ACRE_RE = re.compile(r"(\d[\d,.]*)\s*[- ]?acres?\b", re.I)
USD_RE = re.compile(r"\$\s?(\d[\d,.]*)\s*(billion|bn|b|million|m)\b", re.I)
SQFT_RE = re.compile(r"(\d[\d,.]*)\s*(million\s+)?(square[- ]feet|sq\.? ?ft)\b", re.I)

FIELDS = ["priority", "outcome", "seen_date", "query_label", "title", "domain", "url",
          "county", "state", "location_confidence", "mw", "acres", "investment_usd",
          "sqft", "developer", "match_project_id", "match_project_name", "match_basis",
          "same_county_projects",
          "first_seen", "last_seen", "review"]


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def _num(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def extract_numbers(text):
    t = text or ""
    out = {"mw": "", "acres": "", "investment_usd": "", "sqft": ""}
    m = MW_RE.search(t)
    if m and _num(m.group(1)) is not None:
        v = _num(m.group(1)) * (1000 if m.group(2).lower().startswith("g") else 1)
        if 1 <= v <= 20000:                           # plausible single-project range
            out["mw"] = round(v)
    m = ACRE_RE.search(t)
    if m and _num(m.group(1)):
        v = _num(m.group(1))
        if 5 <= v <= 50000:
            out["acres"] = round(v)
    m = USD_RE.search(t)
    if m and _num(m.group(1)):
        unit = m.group(2).lower()
        v = _num(m.group(1)) * (1e9 if unit.startswith("b") else 1e6)
        if v >= 1e7:
            out["investment_usd"] = int(v)
    m = SQFT_RE.search(t)
    if m and _num(m.group(1)):
        v = _num(m.group(1)) * (1e6 if m.group(2) else 1)
        if v >= 10000:
            out["sqft"] = int(v)
    return out


_GENERIC = {"data", "center", "centers", "centre", "campus", "project", "the", "of",
            "and", "llc", "inc", "co", "company", "group", "corp", "corporation",
            "holdings", "partners", "development", "developer", "technology",
            "technologies", "digital", "properties", "multiple", "unknown", "park"}


def _tok(s):
    return {t for t in re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split()
            if len(t) > 2 and t not in _GENERIC}


def load_projects(path=PROPOSALS):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            st = sh.STATE_ABBREV and next((a for a, f in sh.STATE_ABBREV.items()
                                           if f.lower() == str(r.get("state", "")).lower()),
                                          str(r.get("state", "")).upper()[:2])
            counties = [re.sub(r"\s+(county|parish|borough)$", "", c.strip(), flags=re.I).lower()
                        for c in str(r.get("counties") or "").split(";") if c.strip()]
            towns = [t.strip().lower() for t in str(r.get("towns") or "").split(";") if t.strip()]
            out.append({"id": str(r.get("id")), "name": r.get("name", ""), "state": st,
                        "counties": counties, "towns": towns,
                        "name_tok": _tok(r.get("name")), "co_tok": _tok(r.get("companies")),
                        "companies": [c.strip() for c in str(r.get("companies") or "").split(";")
                                      if c.strip()]})
    return out


def developer_vocab(projects):
    """Developer names seen in at least one project, longest first."""
    names = Counter()
    for p in projects:
        for c in p["companies"]:
            if len(c) >= 3 and c.lower() not in _GENERIC:
                names[c] += 1
    return sorted(names, key=lambda s: -len(s))


def find_developer(title, vocab):
    tl = (title or "").lower()
    for name in vocab:
        if re.search(rf"\b{re.escape(name.lower())}\b", tl):
            return name
    return ""


def match_project(title, county, state, developer, projects):
    """(project, basis) for the best existing match, or (None, '')."""
    tt = _tok(title)
    tl = (title or "").lower()
    best, basis = None, ""
    for p in projects:
        if state and p["state"] and state != p["state"]:
            continue
        name_overlap = len(tt & p["name_tok"]) / max(1, len(p["name_tok"]))
        dev_hit = bool(developer) and developer in p["companies"]
        place_hit = (county and county.lower() in p["counties"]) or any(
            t and re.search(rf"\b{re.escape(t)}\b", tl) for t in p["towns"])
        if state and name_overlap >= 0.8 and p["name_tok"]:
            b = "name"
        elif place_hit and (dev_hit or name_overlap >= 0.5):
            b = "place+developer" if dev_hit else "place+name"
        else:
            continue
        rank = {"name": 3, "place+developer": 2, "place+name": 1}[b]
        if best is None or rank > best[0]:
            best, basis = (rank, p), b
    return (best[1], basis) if best else (None, "")


def priority(row):
    """0-100. Located, sized, proposal-worded headlines rank highest."""
    s = 0
    s += {"high": 30, "medium": 22, "state_only": 8, "low": 5}.get(row["location_confidence"], 0)
    s += 20 if row["mw"] else 0
    s += 12 if row["acres"] else 0
    s += 10 if row["investment_usd"] else 0
    s += 12 if row["developer"] else 0
    s += 16 if PROPOSAL_VERB.search(row["title"]) else 0
    return min(100, s)


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

def gnews_fetch(query, days, timeout=30):
    q = f"{query} when:{int(days)}d"
    url = f"{GNEWS}?{urllib.parse.urlencode({'q': q, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"
    req = urllib.request.Request(url, headers={"User-Agent": sh.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "")
        src = it.find("source")
        pub = it.findtext("pubDate") or ""
        try:
            seen = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            seen = ""
        # Google News titles end with " - Publisher"; the publisher is the domain proxy.
        title = re.sub(r"\s+-\s+[^-]+$", "", title)
        out.append({"title": title, "url": it.findtext("link") or "",
                    "domain": (src.get("url") if src is not None else "") or "news.google.com",
                    "seendate": seen})
    return out


def collect(days):
    by_label = {}
    for label, q in QUERIES:
        arts = []
        try:
            arts += sh.gdelt_fetch(q, days)
        except Exception as e:
            print(f"proposal_discovery: GDELT '{label}' failed ({e})")
        try:
            arts += gnews_fetch(q.replace('"', ""), days)
        except Exception as e:
            print(f"proposal_discovery: Google News '{label}' failed ({e})")
        by_label[label] = arts
    return by_label


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

def process(articles_by_label, projects=None, cidx=None, pidx=None, national=False, today=None):
    projects = load_projects() if projects is None else projects
    cidx = sh.county_index() if cidx is None else cidx
    vocab = developer_vocab(projects)
    today = today or date.today().isoformat()
    rows, emitted = [], set()
    for label, arts in articles_by_label.items():
        for a in arts:
            url = a.get("url") or ""
            nu = sh.normalize_url(url)
            title = (a.get("title") or "").strip()
            if not nu or nu in emitted or not title:
                continue
            if "data cent" not in title.lower() and "hyperscale" not in title.lower() \
                    and "ai campus" not in title.lower():
                continue
            if NOT_PROPOSAL.search(title):
                continue
            emitted.add(nu)
            county, state, conf = sh.locate(title, cidx, None, pidx=pidx, national=national)
            nums = extract_numbers(title)
            dev = find_developer(title, vocab)
            proj, basis = match_project(title, county, state, dev, projects)
            # Same county, no name or developer link: likely the same project,
            # possibly a second one. The reviewer decides; the row says which.
            nearby = [] if proj else [
                p for p in projects
                if county and p["state"] == state and county.lower() in p["counties"]]
            d = sh.parse_seendate(a.get("seendate"))
            row = {"seen_date": d.isoformat() if d else "", "query_label": label,
                   "title": title, "domain": a.get("domain", ""), "url": url,
                   "county": county, "state": state, "location_confidence": conf,
                   **nums, "developer": dev,
                   "match_project_id": f"prj_{proj['id']}" if proj else "",
                   "match_project_name": proj["name"] if proj else "",
                   "match_basis": basis,
                   "outcome": ("corroborates" if proj else
                               "possible_match" if nearby else "new_candidate"),
                   "same_county_projects": "; ".join(f"prj_{p['id']} {p['name']}"
                                                     for p in nearby[:4]),
                   "first_seen": today, "last_seen": today, "review": ""}
            row["priority"] = priority(row)
            rows.append(row)
    return rows


def merge_prior(rows, path=OUT_CSV, keep_days=120, today=None):
    """Keep reviewer notes and first_seen; retain unseen prior rows for keep_days."""
    today = today or date.today().isoformat()
    prior = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            prior = {sh.normalize_url(r["url"]): r for r in csv.DictReader(fh)}
    out = {}
    for r in rows:
        k = sh.normalize_url(r["url"])
        p = prior.get(k)
        if p:
            r["first_seen"] = p.get("first_seen") or r["first_seen"]
            r["review"] = p.get("review", "")
        out[k] = r
    cutoff = date.fromisoformat(today).toordinal() - keep_days
    for k, p in prior.items():
        if k not in out:
            try:
                if date.fromisoformat(p.get("last_seen") or today).toordinal() >= cutoff:
                    out[k] = p
            except ValueError:
                out[k] = p
    return sorted(out.values(), key=lambda r: (r["outcome"] != "new_candidate",
                                               -int(r.get("priority") or 0),
                                               r.get("seen_date") or ""))


def write(rows, path=OUT_CSV):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def log_run(rows, days, path=LOG_CSV):
    new = not os.path.exists(path)
    c = Counter(r["outcome"] for r in rows)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(["run_date", "window_days", "articles", "corroborates",
                        "possible_match", "new_candidate"])
        w.writerow([date.today().isoformat(), days, len(rows), c["corroborates"],
                    c["possible_match"], c["new_candidate"]])


def run(days=7, fixture=None):
    if fixture:
        with open(fixture, encoding="utf-8") as fh:
            by_label = {"fixture": json.load(fh).get("articles", [])}
    else:
        by_label = collect(days)
    pidx = gazetteer.load_index() if gazetteer else None
    national = gazetteer.index_is_national() if gazetteer else False
    rows = process(by_label, pidx=pidx, national=national)
    log_run(rows, days)
    merged = merge_prior(rows)
    write(merged)
    c = Counter(r["outcome"] for r in rows)
    print(f"proposal_discovery: {len(rows)} articles this run {dict(c)}; "
          f"{len(merged)} rows in queue -> {OUT_CSV}")


def selftest():
    checks = []

    def check(label, ok):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    n = extract_numbers("Developer proposes 1.2 GW, 600-acre, $6 billion data center campus")
    check("gigawatts convert to MW", n["mw"] == 1200)
    check("acreage is extracted", n["acres"] == 600)
    check("billions convert to dollars", n["investment_usd"] == 6_000_000_000)
    check("a year is not megawatts", extract_numbers("2026 data center plans")["mw"] == "")
    check("square footage in millions", extract_numbers("a 1.5 million square-feet data center")["sqft"] == 1_500_000)

    projects = [{"id": "12", "name": "Project Sail", "state": "GA", "counties": ["coweta"],
                 "towns": ["sargent"], "name_tok": {"sail"}, "co_tok": {"prologis"},
                 "companies": ["Prologis"]},
                {"id": "13", "name": "Tract Altoona", "state": "IA", "counties": ["polk"],
                 "towns": ["altoona"], "name_tok": {"tract", "altoona"}, "co_tok": {"tract"},
                 "companies": ["Tract"]}]
    p, b = match_project("Coweta County weighs Project Sail data center", "Coweta", "GA", "", projects)
    check("a named project in its state corroborates", p and p["id"] == "12" and b == "name")
    p, b = match_project("Prologis data center plan advances in Coweta", "Coweta", "GA", "Prologis", projects)
    check("county plus developer corroborates", p and b == "place+developer")
    p, b = match_project("Project Sail data center", "", "TX", "", projects)
    check("the same name in another state does not", p is None)
    check("the developer vocabulary comes from the tracker",
          find_developer("Tract buys 400 acres near Altoona", developer_vocab(projects)) == "Tract")

    cidx = {"coweta": [("Coweta", "GA")], "troup": [("Troup", "GA")],
            "polk": [("Polk", "IA"), ("Polk", "FL")]}
    arts = {"t": [
        {"title": "Developer proposes 300 MW data center on 400 acres in Troup County, Georgia",
         "url": "https://ex.com/a", "domain": "ex.com", "seendate": "20260920T120000Z"},
        {"title": "Coweta County weighs Project Sail data center", "url": "https://ex.com/b",
         "domain": "ex.com", "seendate": "20260921T120000Z"},
        {"title": "Data center stocks rally on earnings", "url": "https://ex.com/c", "domain": "ex.com"},
        {"title": "City council meets Tuesday", "url": "https://ex.com/d", "domain": "ex.com"},
        {"title": "Developer proposes 300 MW data center on 400 acres in Troup County, Georgia",
         "url": "https://www.ex.com/a/", "domain": "ex.com"},
    ]}
    rows = process(arts, projects, cidx, today="2026-09-22")
    by = {r["url"]: r for r in rows}
    check("market news is excluded", "https://ex.com/c" not in by)
    check("headlines without a data center are excluded", "https://ex.com/d" not in by)
    check("the same URL is kept once", len(rows) == 2)
    check("an untracked sized proposal is a new candidate",
          by["https://ex.com/a"]["outcome"] == "new_candidate" and by["https://ex.com/a"]["mw"] == 300)
    check("a tracked project is corroborated", by["https://ex.com/b"]["match_project_id"] == "prj_12")
    r2 = process({"t": [{"title": "Developer plans 200 MW data center in Coweta County, Georgia",
                         "url": "https://ex.com/e"}]}, projects, cidx)[0]
    check("a same-county article with no name link is a possible match, listing the project",
          r2["outcome"] == "possible_match" and "prj_12" in r2["same_county_projects"])
    check("a located, sized proposal ranks above a bare mention",
          by["https://ex.com/a"]["priority"] > by["https://ex.com/b"]["priority"])

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "q.csv")
        old = dict(by["https://ex.com/a"], first_seen="2026-09-01", review="added as prj_1013")
        stale = dict(by["https://ex.com/b"], url="https://ex.com/old", last_seen="2026-01-01")
        write([old, stale], p)
        merged = merge_prior(rows, p, today="2026-09-22")
    m = {r["url"]: r for r in merged}
    check("a reviewer's note survives a rerun", m["https://ex.com/a"]["review"] == "added as prj_1013")
    check("first_seen survives a rerun", m["https://ex.com/a"]["first_seen"] == "2026-09-01")
    check("rows unseen for longer than the retention window age out", "https://ex.com/old" not in m)
    k = sum(checks)
    print(f"\n{k}/{len(checks)} checks passed")
    return 0 if k == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="News discovery for proposed data centers")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--fixture")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    run(a.days, a.fixture)


if __name__ == "__main__":
    main()
