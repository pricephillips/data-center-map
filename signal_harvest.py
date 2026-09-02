"""
signal_harvest.py

Candidate opposition-event harvester. Queries the GDELT 2.0 Doc API (free, no
key) for data center opposition coverage, drops anything already in the
database, geotags what it can against the county gazetteer, and writes a
ranked review worklist.

This runs as part of the existing nightly ingest rather than as a separate
job. scripts/build_master_csv.py calls harvest_to_queue() after it refreshes
from the fights feed, replacing the Google News RSS step that previously
appended untagged rows straight into master_opposition.csv. The CLI below is
still available for ad-hoc runs and for the self-test.

It never writes to master_opposition.csv. Everything it produces is a
CANDIDATE requiring human verification before entry, which is what keeps the
defensibility rule intact while cutting the find-it step out of the daily
loop. The worklist is ordered so the highest-value rows sit at the top: a
recognized mechanism keyword plus a resolvable county plus a domain that is
not already saturated in the database.

The harvester sees two different things and used to file them in one place.
An article about a county adopting a moratorium is a Layer C opposition event.
An article about a data center opening, breaking ground, or being announced is
a Layer A facility signal, and it was landing in the opposition worklist with
an empty mechanism_hint and a low priority, where it was either misfiled or
quietly ignored. Facility signals now route to their own candidates file, which
keeps the opposition worklist about opposition and gives the facility layer its
first standing intake. A row carrying both an opposition mechanism and a
facility verb stays with opposition: the mechanism is the stronger signal and
the reviewer needs it in the queue that gets worked.

Outputs
-------
  data/signal_candidates.csv    ranked opposition worklist, one row per article
  data/facility_candidates.csv  Layer A facility signals (openings, ground
                                breaking, announcements, expansions)
  data/signal_harvest_log.csv   append-only run log (query, window, counts)

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

import gazetteer

HERE = os.path.dirname(os.path.abspath(__file__))

OPPOSITION_CANDIDATES = [
    os.path.join(HERE, "master_opposition_clean.csv"),
    os.path.join(HERE, "master_opposition.csv"),
]
FIPS_LOOKUP_JSON = os.path.join(HERE, "data", "county_fips_lookup.json")
COUNTY_AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")

OUT_CSV = os.path.join(HERE, "data", "signal_candidates.csv")
FACILITY_CSV = os.path.join(HERE, "data", "facility_candidates.csv")
LOG_CSV = os.path.join(HERE, "data", "signal_harvest_log.csv")

# Counties whose bare name is shorter than this match only in the literal
# form "<name> County", because the bare word is ordinary English. See
# locate().
SHORT_COUNTY_LEN = 5

# Ordering used to decide whether the place pass may improve on the county
# pass. Only ranks that locate() actually emits appear here.
CONFIDENCE_RANK = {"none": 0, "low": 1, "state_only": 2, "medium": 3, "high": 4}

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

# Layer A facility signals. These describe a facility's own lifecycle rather
# than any community response to it, which is why they belong in a different
# file and not merely a different row type. Deliberately narrow: a verb that
# could describe either a facility or a policy (approves, considers, reviews)
# is left out, because a false facility candidate costs more than a missed one
# when the file is meant to seed a registry.
FACILITY_HINTS = [
    (r"\bopens?\b|\bopened\b|\bgoes? (online|live)\b|\bwent online\b|"
     r"\bnow (open|operational)\b|\bbegins? operations?\b", "operational"),
    (r"\bbreaks? ground\b|\bbroke ground\b|\bgroundbreaking\b|"
     r"\bconstruction (begins|starts|underway)\b|\bstarts? construction\b",
     "construction_start"),
    (r"\bannounces? (a |its |plans? )?(new )?(data cent(er|re)|campus)\b|"
     r"\bto build (a |its )?(new )?data cent(er|re)\b|\bunveils?\b|"
     r"\bplans? (a |its )?(new )?\$?[\d.]*\s*(billion|million)?\s*data cent(er|re)\b",
     "announcement"),
    (r"\bexpands?\b|\bexpansion\b|\bphase (two|three|2|3|ii|iii)\b|"
     r"\badds? (a )?(second|third|new) (building|campus|phase)\b", "expansion"),
    (r"\bbuys? (land|acreage|a site)\b|\bacquires? (land|a site|acreage)\b|"
     r"\bpurchased? \d+ acres\b", "site_acquisition"),
]

# Routing guard. MECHANISM_HINTS is a list of recognized mechanisms, not a
# complete vocabulary of opposition, so a headline can describe a fight without
# matching any of them: "Residents sue after data center opens" carries no
# mechanism hint under the patterns above, and a facility verb alone would
# route it to the facility file, out of the queue a reviewer works. The guard
# is broad and errs toward keeping rows in the opposition worklist, because a
# facility candidate that should have been an opposition candidate is a missed
# event while the reverse is only a row a reviewer skips.
OPPOSITION_GUARD = re.compile(
    r"\boppos|\bprotest|\bresidents?\b|\bneighbors?\b|\bsue[sd]?\b|\bsuing\b|"
    r"\blawsuit|\bpetition|\breject|\bdenie[sd]\b|\bdeny\b|\bhalt|\bpause[sd]?\b|"
    r"\bblock(s|ed|ing)?\b|\bban(s|ned|ning)?\b|\bmoratori|\bfight|\bbacklash|"
    r"\bconcerns?\b|\bpushback\b|\bobject(s|ed|ion|ions)?\b|\bcritic|"
    r"\bagainst\b|\bopposition\b|\breferendum\b|\brecall\b",
    re.IGNORECASE)

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


def facility_hint(title):
    """Layer A facility-lifecycle signal in a headline, or "" if none."""
    t = (title or "").lower()
    hits = [name for pat, name in FACILITY_HINTS if re.search(pat, t)]
    return "; ".join(dict.fromkeys(hits))


def is_facility_signal(row):
    """A facility signal is an article with a facility verb, no opposition
    mechanism, and nothing in the headline that reads as community response.

    When a mechanism is present it wins: it is the stronger claim and the
    reviewer needs the row in the queue that gets worked. The guard covers the
    case a mechanism list cannot, which is opposition described in words no
    mechanism pattern matches.
    """
    if not row.get("facility_hint") or row.get("mechanism_hint"):
        return False
    return not OPPOSITION_GUARD.search(row.get("title") or "")


def locate(title, cidx, state_filter=None, pidx=None, national=False):
    """Best-effort county/state from the headline. Returns (county, state,
    confidence). Ambiguous county names across states resolve only when the
    state is also named in the headline.

    Two passes, county names first and place names second. The county pass is
    the older and stricter of the two and keeps precedence: a headline naming
    both a county and a town inside it should key on the county it named.

    The place pass exists because matching county names alone is not a neutral
    filter. The jurisdiction that acts on a data center differs by region — a
    Virginia headline says "Loudoun County", an Arizona headline says Tucson —
    so a county-only index reads the east and drops the west. See gazetteer.py
    for the ambiguity rules, which refuse far more often than they guess.
    """
    t = title or ""
    tl = t.lower()
    named_states = [ab for ab, full in STATE_ABBREV.items()
                    if re.search(rf"\b{re.escape(full)}\b", t, re.IGNORECASE)]
    best = None
    for name_l, options in cidx.items():
        if len(name_l) < SHORT_COUNTY_LEN:
            # Short county names produce too many false hits on their own —
            # Ada, Lyon, Kern, Nye, Iron, Pima are all ordinary English or
            # ordinary nouns. Requiring the literal "<name> county" removes the
            # false hits without removing the counties: Pima and Ada are the
            # third and sixth ranked western opposition counties we hold, and
            # under the old unconditional skip neither could ever match.
            if not re.search(rf"\b{re.escape(name_l)}\s+(county|parish|borough)\b", tl):
                continue
        elif not re.search(rf"\b{re.escape(name_l)}\b", tl):
            continue
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

    if pidx and (best is None or best[2] not in ("high", "medium")):
        hit = gazetteer.resolve(t, pidx, named_states=named_states,
                                national=national)
        if hit:
            county, state, _fips, conf = hit
            # Never downgrade: a county-pass result already better than what
            # the place pass offers stands.
            if best is None or CONFIDENCE_RANK.get(conf, 0) > CONFIDENCE_RANK.get(best[2], 0):
                best = (county, state, conf)

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

# Layer A candidates. Deliberately not the same shape as the opposition
# worklist: these rows describe a facility, carry no mechanism or priority
# ordering built for opposition review, and are pre-promotion by construction.
# Nothing here is a source of record.
FACILITY_FIELDS = ["seen_date", "facility_signal", "title", "domain", "url",
                   "county", "state", "location_confidence", "harvested_on"]


def harvest(days=7, state_filter=None, fixture=None, articles_by_label=None):
    seen = known_urls()
    cidx = county_index()
    # Place index. Absent file -> empty dict -> county-only behaviour, exactly
    # as before this index existed. Building the gazetteer is a separate job
    # (gazetteer.py --build), so a harvest never blocks on it.
    pidx = gazetteer.load_index()
    # Whether the place index may be treated as complete. A sparse index cannot
    # support "this name occurs once, so it is unambiguous"; see gazetteer.py.
    pidx_national = gazetteer.index_is_national()
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

    rows, facility_rows, dupes, out_of_scope = [], [], 0, 0
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
            loc = locate(title, cidx, state_filter, pidx=pidx,
                         national=pidx_national)
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
                "facility_hint": facility_hint(title),
                "county": county,
                "state": st,
                "location_confidence": conf,
                "county_already_tracked": "yes" if (county.lower(), st) in tracked_counties else "no",
                "harvested_on": date.today().isoformat(),
            }
            if is_facility_signal(row):
                facility_rows.append({
                    "seen_date": row["seen_date"],
                    "facility_signal": row["facility_hint"],
                    "title": row["title"],
                    "domain": row["domain"],
                    "url": row["url"],
                    "county": row["county"],
                    "state": row["state"],
                    "location_confidence": row["location_confidence"],
                    "harvested_on": row["harvested_on"],
                })
                emitted.add(nu)
                continue
            row.pop("facility_hint", None)
            row["priority"] = priority(row)
            rows.append(row)
            emitted.add(nu)

    rows.sort(key=lambda r: -r["priority"])
    facility_rows.sort(key=lambda r: (r["seen_date"], r["title"]), reverse=True)
    return rows, facility_rows, {"already_in_database": dupes,
                                 "filtered_out_of_scope": out_of_scope,
                                 "facility_signals": len(facility_rows)}


def _existing_row_count(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return sum(1 for _ in csv.DictReader(fh))
    except (OSError, csv.Error):
        return 0


def _existing_candidate_count():
    return _existing_row_count(OUT_CSV)


def _write_or_keep(path, fields, rows, label):
    """Write rows, unless there are none and the file already holds some.

    A run that returns nothing must not erase a run that returned something.
    The harvest log records this happening: two runs on 2026-07-24 returned
    249 candidates each and a third returned 0, and the committed worklist
    ended up empty. GDELT throttles repeat callers, so an empty return is far
    more often a throttled call than a genuine absence of coverage.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    prior = _existing_row_count(path)
    if not rows and prior:
        print(f"signal_harvest: 0 {label} returned; keeping the existing "
              f"{prior}-row file at {path} rather than clearing it. "
              f"An empty return is usually a throttled call.")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_outputs(rows, facility_rows, stats, days):
    # The log row is written either way below, so an empty run stays visible.
    _write_or_keep(OUT_CSV, FIELDS, rows, "candidates")
    _write_or_keep(FACILITY_CSV, FACILITY_FIELDS, facility_rows,
                   "facility signals")
    leak_hits = LEAK_RE.findall(",".join(r["title"] for r in rows))
    # Headlines are third-party text, not generated language, so a hit here is
    # reported rather than fatal. It flags rows a reviewer must reword before
    # any of that phrasing reaches a deliverable.
    log_exists = os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["run_date", "window_days", "candidates",
                                           "already_in_database", "filtered_out_of_scope",
                                           "headline_vocab_flags", "facility_signals"],
                           lineterminator="\n")
        if not log_exists:
            w.writeheader()
        w.writerow({"run_date": date.today().isoformat(), "window_days": days,
                    "candidates": len(rows),
                    "already_in_database": stats["already_in_database"],
                    "filtered_out_of_scope": stats["filtered_out_of_scope"],
                    "headline_vocab_flags": len(leak_hits),
                    "facility_signals": stats.get("facility_signals", 0)})
    print(f"signal_harvest: {len(rows)} candidates -> {OUT_CSV} "
          f"({stats['already_in_database']} already in the database, "
          f"{stats['filtered_out_of_scope']} outside the state filter)")
    print(f"signal_harvest: {len(facility_rows)} facility signals -> {FACILITY_CSV}")
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
# Entry point for the nightly ingest
# ---------------------------------------------------------------------------

def harvest_to_queue(days=7, state_filter=None, repo_root=None):
    """Called by scripts/build_master_csv.py. Harvests candidates and writes
    the review queue. Never touches master_opposition.csv.

    Returns the number of candidates written. Any failure is swallowed and
    reported, because a harvest problem must never be able to stop the
    nightly CSV build; the worst case is a stale queue.
    """
    global HERE, OPPOSITION_CANDIDATES, FIPS_LOOKUP_JSON, COUNTY_AGG_CSV
    global OUT_CSV, FACILITY_CSV, LOG_CSV
    if repo_root:
        HERE = os.path.abspath(repo_root)
        OPPOSITION_CANDIDATES = [os.path.join(HERE, "master_opposition_clean.csv"),
                                 os.path.join(HERE, "master_opposition.csv")]
        FIPS_LOOKUP_JSON = os.path.join(HERE, "data", "county_fips_lookup.json")
        COUNTY_AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
        OUT_CSV = os.path.join(HERE, "data", "signal_candidates.csv")
        FACILITY_CSV = os.path.join(HERE, "data", "facility_candidates.csv")
        LOG_CSV = os.path.join(HERE, "data", "signal_harvest_log.csv")
    try:
        rows, facility_rows, stats = harvest(days=days, state_filter=state_filter)
        write_outputs(rows, facility_rows, stats, days)
        return len(rows)
    except Exception as exc:
        print(f"signal_harvest: harvest skipped ({exc}). The CSV build is unaffected.")
        return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest_no_clobber():
    """A zero-candidate run must not empty a populated worklist, and the same
    protection has to cover the facility file, which is written by the same
    throttled call."""
    global OUT_CSV, FACILITY_CSV, LOG_CSV
    import tempfile
    td = tempfile.mkdtemp()
    keep_out, keep_fac, keep_log = OUT_CSV, FACILITY_CSV, LOG_CSV
    OUT_CSV = os.path.join(td, "signal_candidates.csv")
    FACILITY_CSV = os.path.join(td, "facility_candidates.csv")
    LOG_CSV = os.path.join(td, "signal_harvest_log.csv")
    try:
        row = {k: "" for k in FIELDS}
        row.update({"title": "County adopts a data center ordinance",
                    "priority": "5.0", "county": "Loudoun", "state": "VA"})
        frow = {k: "" for k in FACILITY_FIELDS}
        frow.update({"title": "Data center opens in Story County",
                     "facility_signal": "operational", "state": "IA"})
        stats = {"already_in_database": 0, "filtered_out_of_scope": 0,
                 "facility_signals": 1}
        write_outputs([row], [frow], stats, 7)
        first = _existing_row_count(OUT_CSV)
        first_fac = _existing_row_count(FACILITY_CSV)
        write_outputs([], [], stats, 7)
        after = _existing_row_count(OUT_CSV)
        after_fac = _existing_row_count(FACILITY_CSV)
        log_rows = sum(1 for _ in open(LOG_CSV, encoding="utf-8")) - 1
        return (first == 1 and after == 1 and first_fac == 1
                and after_fac == 1 and log_rows == 2)
    finally:
        OUT_CSV, FACILITY_CSV, LOG_CSV = keep_out, keep_fac, keep_log


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

    # Short county names. The bare word is refused; the literal "<name> County"
    # is accepted. Before this rule Pima and Ada could never match at all.
    short_idx = {"pima": [("Pima", "AZ")], "ada": [("Ada", "ID")]}
    c, s, conf = locate("Pima County supervisors reject data center rezoning", short_idx)
    expect((c, s) == ("Pima", "AZ"), "short county name resolves in the literal 'X County' form")
    c, s, conf = locate("Ada is a common word in this headline about data centers", short_idx)
    expect(conf == "none", "bare short county name does not match")

    # Place pass. A town-named headline that the county index cannot see.
    pidx = {"tucson": [("Pima", "AZ", "04019")],
            "aurora": [("Adams", "CO", "08001"), ("Kane", "IL", "17089")]}
    c, s, conf = locate("Tucson council rejects Project Blue data center", cidx,
                        pidx=pidx, national=True)
    expect((c, s, conf) == ("Pima", "AZ", "medium"),
           "a town-named headline resolves through the place index")
    c, s, conf = locate("Tucson council rejects Project Blue data center", cidx,
                        pidx=pidx, national=False)
    expect(conf == "none",
           "a sparse place index withholds the uniqueness inference")
    c, s, conf = locate("Aurora weighs a data center moratorium", cidx, pidx=pidx,
                        national=True)
    expect(conf == "none", "an ambiguous town with no state named still does not resolve")
    c, s, conf = locate("Aurora, Colorado weighs a data center moratorium", cidx, pidx=pidx)
    expect((c, s, conf) == ("Adams", "CO", "high"),
           "the same town resolves once the headline names the state")
    c, s, conf = locate("Loudoun County and Tucson both appear here", cidx, pidx=pidx,
                        national=True)
    expect((c, s) == ("Loudoun", "VA"),
           "the county pass keeps precedence over the place pass")
    c, s, conf = locate("Tucson council rejects data center", cidx, pidx=None)
    expect(conf == "none",
           "with no place index the harvester behaves exactly as it did before")

    expect("operational" in facility_hint("Data center opens quietly"),
           "an opening is a facility signal")
    expect("construction_start" in facility_hint("Meta breaks ground on Iowa campus"),
           "ground breaking is a facility signal")
    expect("expansion" in facility_hint("Operator expands its Loudoun campus"),
           "an expansion is a facility signal")
    expect(facility_hint("County adopts data center moratorium") == "",
           "a policy headline carries no facility signal")
    expect(facility_hint("Board approves data center rezoning") == "",
           "approves is deliberately not a facility verb: it describes policy "
           "at least as often as it describes a building")
    expect(is_facility_signal({"facility_hint": "operational", "mechanism_hint": "",
                               "title": "Data center opens quietly"}),
           "facility verb with no mechanism routes to the facility file")
    expect(not is_facility_signal({"facility_hint": "operational",
                                   "mechanism_hint": "moratorium",
                                   "title": "Data center opens despite moratorium"}),
           "an opposition mechanism keeps the row in the opposition worklist")
    expect(not is_facility_signal({"facility_hint": "", "mechanism_hint": "",
                                   "title": "Something else entirely"}),
           "a headline with neither signal stays where it was")
    expect(not is_facility_signal({"facility_hint": "operational",
                                   "mechanism_hint": "",
                                   "title": "Residents sue after data center opens"}),
           "opposition described in words no mechanism pattern matches still "
           "stays in the opposition worklist")
    expect(not is_facility_signal({"facility_hint": "construction_start",
                                   "mechanism_hint": "",
                                   "title": "Company breaks ground amid neighbor concerns"}),
           "the guard keeps a contested ground breaking with opposition")

    fake = {"fixture": [
        {"url": "https://example.com/story-a", "title": "Fairfield County adopts data center moratorium",
         "domain": "example.com", "seendate": "20260720T000000Z"},
        {"url": "https://msn.com/story-b", "title": "Data center opens quietly",
         "domain": "msn.com", "seendate": "20260720T000000Z"},
        {"url": "https://example.com/story-c",
         "title": "Residents file a lawsuit after the data center opens in Loudoun County",
         "domain": "example.com", "seendate": "20260720T000000Z"},
    ]}
    rows, facility_rows, stats = harvest(articles_by_label=fake)
    expect(len(rows) == 2, "opposition worklist keeps the two opposition articles")
    expect(len(facility_rows) == 1, "the opening routes out of the opposition worklist")
    expect(facility_rows[0]["url"].endswith("story-b"),
           "the routed row is the opening, not the lawsuit")
    expect(facility_rows[0]["facility_signal"] == "operational",
           "the facility signal is recorded on the row")
    expect(all(r["url"].endswith(("story-a", "story-c")) for r in rows),
           "no facility-only row remains in the opposition worklist")
    expect(rows[0]["url"].endswith("story-a"), "mechanism plus recency ranks the moratorium story first")
    expect(all(f in rows[0] for f in FIELDS), "all worklist fields present")
    expect("facility_hint" not in rows[0],
           "the internal hint does not leak into the worklist schema")
    expect(all(f in facility_rows[0] for f in FACILITY_FIELDS),
           "all facility candidate fields present")
    expect(stats["facility_signals"] == 1, "the run log counts routed signals")

    expect(_selftest_no_clobber(),
           "zero-candidate run clears neither the worklist nor the facility file")

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
    rows, facility_rows, stats = harvest(days=args.days, state_filter=state_filter,
                                         fixture=args.fixture)
    write_outputs(rows, facility_rows, stats, args.days)


if __name__ == "__main__":
    main()
