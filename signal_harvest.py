"""
signal_harvest.py

Candidate opposition-event harvester. Queries the GDELT 2.0 Doc API (free, no
key) for data center opposition coverage, drops anything already in the
database, geotags what it can against the county gazetteer, and writes a
ranked review worklist.

It never writes to master_opposition.csv. Everything it produces is a
CANDIDATE requiring human verification before entry, which is what keeps the
defensibility rule intact while cutting the find-it step out of the daily
loop. The worklist is ordered so the highest-value rows sit at the top: a
recognized mechanism keyword plus a resolvable county plus a domain that is
not already saturated in the database.

Outputs
-------
  data/signal_candidates.csv   ranked worklist, one row per candidate article
  data/signal_harvest_log.csv  append-only run log (query, window, counts)

Usage
-----
  python signal_harvest.py --selftest
  python signal_harvest.py --days 7                    # live harvest
  python signal_harvest.py --days 7 --states VA,OH,IA  # narrow by state
  python signal_harvest.py --fixture path/to/gdelt.json  # offline replay

Requires network access to api.gdeltproject.org for live runs. Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))

OPPOSITION_CANDIDATES = [
    os.path.join(HERE, "master_opposition_clean.csv"),
    os.path.join(HERE, "master_opposition.csv"),
]
FIPS_LOOKUP_JSON = os.path.join(HERE, "data", "county_fips_lookup.json")
COUNTY_AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")

OUT_CSV = os.path.join(HERE, "data", "signal_candidates.csv")
LOG_CSV = os.path.join(HERE, "data", "signal_harvest_log.csv")

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "hawthorn-dc-tracker/1.0 (opposition monitoring; contact repo owner)"
MAX_RECORDS = 250

# Query set. Each entry is (label, GDELT query string). Kept narrow enough that
# the return is reviewable by a person in a sitting.
QUERIES = [
    ("moratorium", '"data center" (moratorium OR "moratoria")'),
    ("zoning", '"data center" (rezoning OR "zoning" OR "special exception" OR "conditional use")'),
    ("denial", '"data center" (denied OR rejected OR "voted down" OR withdrawn)'),
    ("litigation", '"data center" (lawsuit OR "sued" OR "legal challenge" OR appeal)'),
    ("legislation", '"data center" (bill OR legislation OR ordinance) (ratepayer OR water OR noise OR tax)'),
    ("organizing", '"data center" (residents OR neighbors OR "petition" OR "opposition group")'),
]

# Mechanism keywords -> the Opposition Type value a reviewer would most likely
# assign. Suggestions only; the reviewer sets the field.
MECHANISM_HINTS = [
    (r"\bmoratori(um|a)\b", "moratorium"),
    (r"\brezon|zoning|special exception|conditional use\b", "zoning_restriction"),
    (r"\blawsuit|sued|litigation|legal challenge|appeal\b", "lawsuit"),
    (r"\bordinance\b", "ordinance"),
    (r"\bbill\b|\blegislat", "legislation"),
    (r"\bpublic (hearing|comment|meeting)\b|\btown hall\b", "public_comment"),
    (r"\bwithdrew|withdrawn|pulled (its|their) application\b", "project_withdrawal"),
    (r"\bpetition\b", "other_opposition"),
]

STATE_ABBREV = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

# Aggregators and syndicators: real coverage, but rarely the primary source a
# record should cite. Demoted, not dropped.
DEMOTED_DOMAINS = {"msn.com", "news.yahoo.com", "finance.yahoo.com", "flipboard.com",
                   "newsbreak.com", "aol.com", "reddit.com", "medium.com"}


# ---------------------------------------------------------------------------
# Loading existing state
# ---------------------------------------------------------------------------

def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def normalize_url(u):
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u


def known_urls():
    """Every URL already cited anywhere in the database, normalized."""
    seen = set()
    for path in OPPOSITION_CANDIDATES:
        for r in load_csv(path):
            blob = " ".join(str(r.get(f) or "") for f in
                            ("Source URL", "Sources", "Opposition Website", "Petition URL"))
            for m in re.findall(r"https?://[^\s'\"}\],]+", blob):
                seen.add(normalize_url(m))
        if seen:
            break
    return seen


def county_index():
    """(county_lower, state_abbrev) pairs plus a name -> [(county,state)] index
    for gazetteer matching against headlines."""
    idx = {}
    for r in load_csv(COUNTY_AGG_CSV):
        # county_name is "Loudoun County, Virginia"; keep the bare county name
        raw = (r.get("county_name") or "").split(",")[0].strip()
        name = re.sub(r"\s+(County|Parish|Borough|Census Area|Municipality|City and Borough)$",
                      "", raw, flags=re.IGNORECASE)
        st = (r.get("state") or "").strip().upper()
        if name and st:
            idx.setdefault(name.lower(), []).append((name, st))
    return idx


# ---------------------------------------------------------------------------
# GDELT
# ---------------------------------------------------------------------------

def gdelt_fetch(query, days, timeout=45):
    params = {
        "query": f"{query} sourcecountry:US",
        "mode": "ArtList",
        "maxrecords": str(MAX_RECORDS),
        "timespan": f"{int(days)}d",
        "format": "json",
        "sort": "datedesc",
    }
    url = GDELT_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw).get("articles", [])
    except json.JSONDecodeError:
        return []


def parse_seendate(s):
    s = (s or "").strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Scoring and enrichment
# ---------------------------------------------------------------------------

def mechanism_hint(title):
    t = (title or "").lower()
    hits = [name for pat, name in MECHANISM_HINTS if re.search(pat, t)]
    return "; ".join(dict.fromkeys(hits))


def locate(title, cidx, state_filter=None):
    """Best-effort county/state from the headline. Returns (county, state,
    confidence). Ambiguous county names across states resolve only when the
    state is also named in the headline."""
    t = title or ""
    tl = t.lower()
    named_states = [ab for ab, full in STATE_ABBREV.items()
                    if re.search(rf"\b{re.escape(full)}\b", t, re.IGNORECASE)]
    best = None
    for name_l, options in cidx.items():
        if len(name_l) < 5:
            continue  # short county names produce too many false hits
        if re.search(rf"\b{re.escape(name_l)}\b", tl):
            if len(options) == 1:
                cand = (options[0][0], options[0][1], "high" if named_states else "medium")
            else:
                match = [o for o in options if o[1] in named_states]
                if len(match) == 1:
                    cand = (match[0][0], match[0][1], "high")
                else:
                    cand = (options[0][0], "", "low")
            if best is None or cand[2] == "high":
                best = cand
    if best is None and named_states:
        best = ("", named_states[0], "state_only")
    if best is None:
        best = ("", "", "none")
    if state_filter and best[1] and best[1] not in state_filter:
        return None
    return best


def priority(row):
    """Higher is more worth a reviewer's next ten minutes."""
    s = 0.0
    if row["mechanism_hint"]:
        s += 3.0
    if "moratorium" in row["mechanism_hint"] or "project_withdrawal" in row["mechanism_hint"]:
        s += 1.5
    conf = row["location_confidence"]
    s += {"high": 3.0, "medium": 1.5, "state_only": 0.5, "low": 0.5, "none": 0.0}.get(conf, 0.0)
    if row["domain"] in DEMOTED_DOMAINS:
        s -= 2.0
    if row["seen_date"]:
        try:
            age = (date.today() - datetime.strptime(row["seen_date"], "%Y-%m-%d").date()).days
            s += max(0.0, 2.0 - age / 7.0)
        except ValueError:
            pass
    if row["county_already_tracked"] == "no" and row["state"]:
        s += 1.0   # a county with no record yet is a coverage gap, not noise
    return round(s, 2)


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

FIELDS = ["priority", "seen_date", "query_label", "title", "domain", "url",
          "mechanism_hint", "county", "state", "location_confidence",
          "county_already_tracked", "harvested_on"]


def harvest(days=7, state_filter=None, fixture=None, articles_by_label=None):
    seen = known_urls()
    cidx = county_index()
    tracked_counties = set()
    for path in OPPOSITION_CANDIDATES:
        rows = load_csv(path)
        if rows:
            for r in rows:
                c = re.sub(r"\s+(County|Parish|Borough)$", "",
                           (r.get("County") or "").strip(), flags=re.IGNORECASE).lower()
                st = (r.get("State") or "").strip().upper()
                if c and st:
                    tracked_counties.add((c, st))
            break

    if articles_by_label is None:
        articles_by_label = {}
        if fixture:
            with open(fixture, encoding="utf-8") as fh:
                articles_by_label["fixture"] = json.load(fh).get("articles", [])
        else:
            for label, q in QUERIES:
                try:
                    articles_by_label[label] = gdelt_fetch(q, days)
                except Exception as exc:                      # network, timeout, throttling
                    print(f"signal_harvest: query '{label}' failed ({exc}); continuing")
                    articles_by_label[label] = []

    rows, dupes, out_of_scope = [], 0, 0
    emitted = set()
    for label, articles in articles_by_label.items():
        for a in articles:
            url = a.get("url") or ""
            nu = normalize_url(url)
            if not nu or nu in seen:
                dupes += 1
                continue
            if nu in emitted:
                continue
            title = (a.get("title") or "").strip()
            loc = locate(title, cidx, state_filter)
            if loc is None:
                out_of_scope += 1
                continue
            county, st, conf = loc
            d = parse_seendate(a.get("seendate"))
            row = {
                "seen_date": d.isoformat() if d else "",
                "query_label": label,
                "title": title,
                "domain": (a.get("domain") or "").lower(),
                "url": url,
                "mechanism_hint": mechanism_hint(title),
                "county": county,
                "state": st,
                "location_confidence": conf,
                "county_already_tracked": "yes" if (county.lower(), st) in tracked_counties else "no",
                "harvested_on": date.today().isoformat(),
            }
            row["priority"] = priority(row)
            rows.append(row)
            emitted.add(nu)

    rows.sort(key=lambda r: -r["priority"])
    return rows, {"already_in_database": dupes, "filtered_out_of_scope": out_of_scope}


def write_outputs(rows, stats, days):
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    leak_hits = LEAK_RE.findall(",".join(r["title"] for r in rows))
    # Headlines are third-party text, not generated language, so a hit here is
    # reported rather than fatal. It flags rows a reviewer must reword before
    # any of that phrasing reaches a deliverable.
    log_exists = os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["run_date", "window_days", "candidates",
                                           "already_in_database", "filtered_out_of_scope",
                                           "headline_vocab_flags"])
        if not log_exists:
            w.writeheader()
        w.writerow({"run_date": date.today().isoformat(), "window_days": days,
                    "candidates": len(rows),
                    "already_in_database": stats["already_in_database"],
                    "filtered_out_of_scope": stats["filtered_out_of_scope"],
                    "headline_vocab_flags": len(leak_hits)})
    print(f"signal_harvest: {len(rows)} candidates -> {OUT_CSV} "
          f"({stats['already_in_database']} already in the database, "
          f"{stats['filtered_out_of_scope']} outside the state filter)")
    if leak_hits:
        print(f"signal_harvest: {len(leak_hits)} candidate headlines contain scorekeeping "
              f"vocabulary. Reword before any of that phrasing reaches a deliverable.")
    top = rows[:5]
    if top:
        print("top candidates:")
        for r in top:
            where = ", ".join(x for x in [r["county"], r["state"]] if x) or "location unresolved"
            print(f"  [{r['priority']}] {where}: {r['title'][:90]}")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    ok = True

    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    expect(normalize_url("https://WWW.Example.com/a/?utm=1#x") == "example.com/a",
           "url normalization strips scheme, www, query, fragment, trailing slash")
    expect(normalize_url("http://example.com/a") == normalize_url("https://example.com/a/"),
           "scheme and trailing slash do not create a false new candidate")
    expect("moratorium" in mechanism_hint("County adopts data center moratorium"),
           "moratorium hinted")
    expect("lawsuit" in mechanism_hint("Residents file lawsuit over data center"),
           "lawsuit hinted")
    expect(mechanism_hint("Data center opens in town") == "", "no hint when no mechanism")
    expect(parse_seendate("20260715T120000Z") == date(2026, 7, 15), "seendate parsed")
    expect(parse_seendate("nonsense") is None, "bad seendate returns None")

    cidx = {"loudoun": [("Loudoun", "VA")], "montgomery": [("Montgomery", "MD"), ("Montgomery", "OH")]}
    c, s, conf = locate("Loudoun County supervisors weigh data center rules", cidx)
    expect((c, s) == ("Loudoun", "VA"), "unique county name resolves")
    c, s, conf = locate("Montgomery County, Maryland pauses data center permits", cidx)
    expect((c, s, conf) == ("Montgomery", "MD", "high"), "ambiguous county resolved by named state")
    c, s, conf = locate("Montgomery County board hears data center plan", cidx)
    expect(conf == "low", "ambiguous county without a named state is low confidence")
    expect(locate("Loudoun County data center vote", cidx, state_filter={"OH"}) is None,
           "state filter excludes out-of-scope rows")

    fake = {"fixture": [
        {"url": "https://example.com/story-a", "title": "Fairfield County adopts data center moratorium",
         "domain": "example.com", "seendate": "20260720T000000Z"},
        {"url": "https://msn.com/story-b", "title": "Data center opens quietly",
         "domain": "msn.com", "seendate": "20260720T000000Z"},
    ]}
    rows, stats = harvest(articles_by_label=fake)
    expect(len(rows) == 2, "both fixture articles emitted as candidates")
    expect(rows[0]["url"].endswith("story-a"), "mechanism plus recency ranks the moratorium story first")
    expect(rows[1]["priority"] < rows[0]["priority"], "aggregator domain demoted")
    expect(all(f in rows[0] for f in FIELDS), "all worklist fields present")

    print("ALL PASS" if ok else "FAILURES PRESENT")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Candidate opposition-event harvester")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--states", help="comma-separated two-letter codes to keep")
    ap.add_argument("--fixture", help="replay a saved GDELT JSON response instead of querying")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    state_filter = None
    if args.states:
        state_filter = {s.strip().upper() for s in args.states.split(",") if s.strip()}
    rows, stats = harvest(days=args.days, state_filter=state_filter, fixture=args.fixture)
    write_outputs(rows, stats, args.days)


if __name__ == "__main__":
    main()
