"""
untagged_triage.py

Builds the per-row review worklist for the rows verification_status.py holds
out of the feed, and, where the network allows, resolves their Google News
redirect links to publisher URLs so they can be verified and promoted.

The 369 held rows carry no mechanism, no state, no date, and a
news.google.com redirect instead of a citation. What they do carry is a usable
Summary field in the form "Headline - Outlet", which is enough to produce a
ranked worklist without inventing anything:

  outlet                from the Summary suffix, no network call needed
  suggested_mechanism   keyword hint from the headline (signal_harvest logic)
  suggested_county      gazetteer match against the headline, with confidence
  existing_coverage     whether a sourced row already covers that county and
                        mechanism, so recovery does not duplicate a record
  resolved_url          publisher URL, only when --resolve succeeds

The redirect tokens are the opaque post-2024 format. They do not decode
offline: base64 of the token yields no URL for any of the 369 rows, which was
verified before this module was written. Resolution therefore requires a live
call to news.google.com, so --resolve is off by default and cached.

promote_ready is True only when a row has a resolved publisher URL, a
mechanism hint, and a high or medium confidence county. It means "worth a
reviewer opening the article," not "verified." Nothing here writes to
master_opposition.csv, and nothing is promoted without a person reading the
source.

Usage
-----
  python untagged_triage.py                      worklist from local fields only
  python untagged_triage.py --resolve            also resolve redirects (network)
  python untagged_triage.py --resolve --limit 25 resolve a first batch
  python untagged_triage.py --promote-template   draft rows for promote_ready
  python untagged_triage.py --selftest

Outputs
-------
  data/untagged_triage.csv          ranked worklist, one row per held record
  data/untagged_triage.md           counts, outlets, and what is recoverable
  data/untagged_resolved.csv        append-only redirect resolution cache
  data/untagged_promote_draft.csv   optional, master_opposition schema, draft
"""

import argparse
import csv
import hashlib
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
IN_CSV = os.path.join(HERE, "master_opposition.csv")
COUNTY_AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
OUT_CSV = os.path.join(HERE, "data", "untagged_triage.csv")
OUT_MD = os.path.join(HERE, "data", "untagged_triage.md")
CACHE_CSV = os.path.join(HERE, "data", "untagged_resolved.csv")
DRAFT_CSV = os.path.join(HERE, "data", "untagged_promote_draft.csv")

USER_AGENT = ("Mozilla/5.0 (compatible; hawthorn-dc-tracker/1.0; "
              "opposition monitoring)")
RESOLVE_TIMEOUT = 20

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

try:
    import verification_status as VS
except ImportError:
    VS = None

try:
    import signal_harvest as SH
except ImportError:
    SH = None

# Fallbacks so this module runs standalone if either import is unavailable.
_MECHANISM_HINTS = [
    (r"\bmoratori(um|a)\b", "moratorium"),
    (r"\brezon|zoning|special exception|conditional use\b", "zoning_restriction"),
    (r"\blawsuit|sued|litigation|legal challenge|appeal\b", "lawsuit"),
    (r"\bordinance\b", "ordinance"),
    (r"\bbill\b|\blegislat", "legislation"),
    (r"\bpublic (hearing|comment|meeting)\b|\btown hall\b", "public_comment"),
    (r"\bwithdrew|withdrawn|pulled (its|their) application\b", "project_withdrawal"),
    (r"\bpetition\b", "other_opposition"),
]

_STATE_ABBREV = {
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

DEMOTED_OUTLETS = {"msn", "yahoo", "yahoo news", "flipboard", "newsbreak",
                   "aol", "reddit", "medium"}


# ---------------------------------------------------------------------------
# Local field extraction
# ---------------------------------------------------------------------------

_EMPTYISH = {"", "nan", "none", "null", "na", "n/a", "<na>", "nat"}


def _s(row, key):
    v = row.get(key, "")
    if v is None:
        return ""
    if isinstance(v, float) and v != v:
        return ""
    out = str(v).strip()
    return "" if out.lower() in _EMPTYISH else out


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def row_key(row):
    raw = _s(row, "Incident") + "|" + _s(row, "Source URL")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def split_summary(summary):
    """'Headline - Outlet' into (headline, outlet). The separator Google uses
    is ' - ' before the outlet, so the split is from the right. Headlines that
    contain ' - ' themselves keep it."""
    s = (summary or "").strip()
    if " - " not in s:
        return s, ""
    head, outlet = s.rsplit(" - ", 1)
    # An outlet name is short and has no sentence punctuation.
    if len(outlet) > 60 or outlet.endswith((".", "?", "!")):
        return s, ""
    return head.strip(), outlet.strip()


def mechanism_hint(title):
    if SH is not None:
        return SH.mechanism_hint(title)
    t = (title or "").lower()
    hits = [name for pat, name in _MECHANISM_HINTS if re.search(pat, t)]
    return "; ".join(dict.fromkeys(hits))


def county_index():
    if SH is not None:
        try:
            return SH.county_index()
        except Exception:
            pass
    idx = {}
    for r in load_csv(COUNTY_AGG_CSV):
        raw = (r.get("county_name") or "").split(",")[0].strip()
        name = re.sub(r"\s+(County|Parish|Borough|Census Area|Municipality|City and Borough)$",
                      "", raw, flags=re.IGNORECASE)
        st = (r.get("state") or "").strip().upper()
        if name and st:
            idx.setdefault(name.lower(), []).append((name, st))
    return idx


def locate(title, cidx):
    if SH is not None:
        try:
            out = SH.locate(title, cidx)
            if out:
                return out
        except Exception:
            pass
    t = title or ""
    tl = t.lower()
    named = [ab for ab, full in _STATE_ABBREV.items()
             if re.search(rf"\b{re.escape(full)}\b", t, re.IGNORECASE)]
    best = None
    for name_l, options in cidx.items():
        if len(name_l) < 5:
            continue
        if re.search(rf"\b{re.escape(name_l)}\b", tl):
            if len(options) == 1:
                cand = (options[0][0], options[0][1], "high" if named else "medium")
            else:
                match = [o for o in options if o[1] in named]
                cand = (match[0][0], match[0][1], "high") if len(match) == 1 \
                    else (options[0][0], "", "low")
            if best is None or cand[2] == "high":
                best = cand
    if best is None and named:
        best = ("", named[0], "state_only")
    return best or ("", "", "none")


def coverage_index(rows):
    """(county_lower, state_upper) -> set of mechanisms already sourced there,
    so recovery can flag a record that may already exist."""
    idx = defaultdict(set)
    for r in rows:
        if VS is not None and not VS.is_countable(r):
            continue
        c = _s(r, "County").lower()
        c = re.sub(r"\s+(county|parish|borough)$", "", c)
        st = _s(r, "State")
        st = st if len(st) == 2 else next(
            (ab for ab, full in _STATE_ABBREV.items() if full.lower() == st.lower()), "")
        for m in re.split(r"[;,]", _s(r, "Opposition Type")):
            m = m.strip().lower()
            if m and (c or st):
                idx[(c, st.upper())].add(m)
    return idx


# ---------------------------------------------------------------------------
# Redirect resolution
# ---------------------------------------------------------------------------

CACHE_FIELDS = ["row_key", "redirect_url", "resolved_url", "resolved_domain",
                "resolve_status", "method"]


def load_cache():
    out = {}
    for r in load_csv(CACHE_CSV):
        if r.get("row_key"):
            out[r["row_key"]] = r
    return out


def append_cache(records):
    if not records:
        return
    os.makedirs(os.path.dirname(CACHE_CSV), exist_ok=True)
    new = not os.path.exists(CACHE_CSV)
    with open(CACHE_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CACHE_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in records:
            w.writerow({k: r.get(k, "") for k in CACHE_FIELDS})


GOOGLE_HOST_MARKERS = ("google.", "gstatic.", "googleapis.", "googleusercontent.",
                       "ggpht.", "youtube.", "schema.org", "w3.org")
ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".css",
                  ".js", ".woff", ".woff2")


def _is_publisher_url(u):
    host = urllib.parse.urlparse(u).netloc.lower()
    if not host or any(m in host for m in GOOGLE_HOST_MARKERS):
        return False
    path = urllib.parse.urlparse(u).path.lower()
    return not path.endswith(ASSET_SUFFIXES)


def _external_url_from_html(html):
    """Google serves the article link inside the redirect page. Take the first
    absolute publisher URL, preferring the attribute Google uses for it."""
    m = re.search(r'data-n-au="(https?://[^"]+)"', html)
    if m and _is_publisher_url(m.group(1)):
        return m.group(1), "data-n-au"
    for m in re.finditer(r'(?:href|content)="(https?://[^"]+)"', html):
        if _is_publisher_url(m.group(1)):
            return m.group(1), "html_href"
    for m in re.finditer(r'"(https?://[^"\s]{15,})"', html):
        if _is_publisher_url(m.group(1)):
            return m.group(1), "html_json"
    return "", ""


def resolve_redirect(url, opener=None, timeout=RESOLVE_TIMEOUT):
    """Returns (resolved_url, status, method). Network call. Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=timeout) as resp:
            final = resp.geturl()
            body = resp.read(400000).decode("utf-8", errors="replace")
    except Exception as e:
        return "", f"error:{type(e).__name__}", ""
    host = urllib.parse.urlparse(final).netloc.lower()
    if host and "google." not in host:
        return final, "ok", "http_redirect"
    u, method = _external_url_from_html(body)
    if u:
        return u, "ok", method
    return "", "unresolved", ""


# ---------------------------------------------------------------------------
# Worklist
# ---------------------------------------------------------------------------

FIELDS = ["rank_score", "row_key", "verification_status", "outlet",
          "headline", "suggested_mechanism", "suggested_county",
          "suggested_state", "location_confidence", "existing_coverage",
          "resolved_url", "resolved_domain", "resolve_status",
          "promote_ready", "redirect_url"]


def rank(row):
    s = 0.0
    if row["suggested_mechanism"]:
        s += 3.0
    if "moratorium" in row["suggested_mechanism"] or \
            "project_withdrawal" in row["suggested_mechanism"]:
        s += 1.5
    s += {"high": 3.0, "medium": 1.5, "state_only": 0.5,
          "low": 0.5, "none": 0.0}.get(row["location_confidence"], 0.0)
    if row["resolved_url"]:
        s += 2.0
    if row["existing_coverage"] == "possible_duplicate":
        s -= 1.5
    if row["outlet"].lower() in DEMOTED_OUTLETS:
        s -= 2.0
    return round(s, 2)


def build(rows, resolve=False, limit=None, opener=None):
    held = []
    for r in rows:
        if VS is not None:
            st = VS.classify(r)
            if st not in VS.HOLDOUT:
                continue
        else:
            if _s(r, "Opposition Type"):
                continue
            st = "headline_only"
        held.append((r, st))

    cidx = county_index()
    cov = coverage_index(rows)
    cache = load_cache()
    fresh = []

    out = []
    resolved_count = 0
    for r, st in held:
        key = row_key(r)
        headline, outlet = split_summary(_s(r, "Summary"))
        if not headline:
            headline = _s(r, "Incident")
        mech = mechanism_hint(headline)
        county, state, conf = locate(headline, cidx)

        cached = cache.get(key, {})
        resolved_url = cached.get("resolved_url", "")
        resolve_status = cached.get("resolve_status", "not_attempted")
        resolved_domain = cached.get("resolved_domain", "")

        if resolve and not resolved_url and (limit is None or resolved_count < limit):
            resolved_url, resolve_status, method = resolve_redirect(
                _s(r, "Source URL"), opener=opener)
            resolved_domain = urllib.parse.urlparse(resolved_url).netloc.lower() \
                if resolved_url else ""
            fresh.append({"row_key": key, "redirect_url": _s(r, "Source URL"),
                          "resolved_url": resolved_url,
                          "resolved_domain": resolved_domain,
                          "resolve_status": resolve_status, "method": method})
            resolved_count += 1

        mech_first = mech.split(";")[0].strip() if mech else ""
        st_ab = state if len(state) == 2 else ""
        existing = "possible_duplicate" if mech_first and \
            mech_first in cov.get((county.lower(), st_ab), set()) else "none_found"

        rec = {
            "row_key": key,
            "verification_status": st,
            "outlet": outlet,
            "headline": headline,
            "suggested_mechanism": mech,
            "suggested_county": county,
            "suggested_state": state,
            "location_confidence": conf,
            "existing_coverage": existing,
            "resolved_url": resolved_url,
            "resolved_domain": resolved_domain,
            "resolve_status": resolve_status,
            "redirect_url": _s(r, "Source URL"),
        }
        rec["promote_ready"] = "yes" if (
            resolved_url and mech and conf in ("high", "medium")) else "no"
        rec["rank_score"] = rank(rec)
        out.append(rec)

    append_cache(fresh)
    out.sort(key=lambda x: (-x["rank_score"], x["outlet"], x["headline"]))
    return out


def write_worklist(recs, path=OUT_CSV):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in recs:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return path


def render_markdown(recs):
    conf = Counter(r["location_confidence"] for r in recs)
    mech = Counter((r["suggested_mechanism"].split(";")[0].strip() or "none")
                   for r in recs)
    outl = Counter(r["outlet"] or "unknown" for r in recs)
    res = Counter(r["resolve_status"] for r in recs)
    promo = sum(1 for r in recs if r["promote_ready"] == "yes")
    dup = sum(1 for r in recs if r["existing_coverage"] == "possible_duplicate")

    L = ["# Untagged row triage", "",
         f"Held rows in the worklist: {len(recs)}", "",
         "These rows are excluded from the clean feed and from every count. "
         "They are recoverable only by resolving the redirect to a publisher "
         "article and reading it. Everything below is a suggestion derived "
         "from the headline text, not a verified field.", "",
         "## Location resolution from the headline", "",
         "| confidence | rows |", "| :-- | --: |"]
    for k in ("high", "medium", "state_only", "low", "none"):
        if conf.get(k):
            L.append(f"| {k} | {conf[k]} |")
    L += ["", "## Mechanism hint", "", "| hint | rows |", "| :-- | --: |"]
    for k, n in mech.most_common():
        L.append(f"| {k} | {n} |")
    L += ["", "## Redirect resolution", "", "| status | rows |", "| :-- | --: |"]
    for k, n in res.most_common():
        L.append(f"| {k} | {n} |")
    L += ["",
          f"Rows ready for a reviewer to open: {promo}",
          f"Rows whose county and mechanism are already covered by a sourced "
          f"row: {dup}",
          "", "## Outlets, top 15", "", "| outlet | rows |", "| :-- | --: |"]
    for k, n in outl.most_common(15):
        L.append(f"| {k} | {n} |")
    L += ["",
          "Outlet is parsed from the Summary suffix and needs no network call, "
          "so it is available for every row. A resolved publisher URL is "
          "required before any row is promoted.", ""]
    return "\n".join(L)


MASTER_FIELDS = ["Incident", "City", "Date", "Entity", "Location",
                 "Opposition Type", "Severity", "Source URL", "State",
                 "County", "Scope", "Issue Category", "Objective",
                 "Authority Level", "Status", "Community Outcome",
                 "Hyperscaler", "Company", "Project Name",
                 "Investment Million USD", "Megawatts", "Acreage", "Sponsors",
                 "Opposition Groups", "Summary", "Sources",
                 "Opposition Website", "Opposition Facebook",
                 "Opposition Instagram", "Petition URL", "Petition Signatures",
                 "data_source", "lat", "lon", "verification_status"]


def write_promote_draft(recs, path=DRAFT_CSV):
    """Draft rows in the master schema for promote_ready records only. Every
    field here is a suggestion. Date is deliberately left blank: a date must
    come from the article, never from a headline."""
    ready = [r for r in recs if r["promote_ready"] == "yes"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MASTER_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in ready:
            w.writerow({
                "Incident": r["headline"][:120],
                "Date": "",
                "Entity": "Unknown",
                "Opposition Type": r["suggested_mechanism"].split(";")[0].strip(),
                "Severity": "1",
                "Source URL": r["resolved_url"],
                "State": r["suggested_state"],
                "County": r["suggested_county"],
                "Status": "ongoing",
                "Summary": r["headline"],
                "Sources": r["resolved_url"],
                "data_source": "google_news_backfill_reviewed",
                "verification_status": "sourced",
            })
    return path, len(ready)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            ok = False

    h, o = split_summary("Socorro County commissioners approve a moratorium - KOAT")
    check("summary split", h.endswith("moratorium") and o == "KOAT")
    h2, o2 = split_summary("A headline with no outlet suffix.")
    check("no separator keeps the whole string", o2 == "" and h2.endswith("suffix."))
    h3, o3 = split_summary("Data centers - and the grid - draw scrutiny - Kentucky Lantern")
    check("split takes the last separator", o3 == "Kentucky Lantern")

    check("mechanism hint", "moratorium" in mechanism_hint("City passes a moratorium"))
    check("no hint on a bare headline", mechanism_hint("Data center opens") == "")

    cidx = {"loudoun": [("Loudoun", "VA")], "carlton": [("Carlton", "MN")],
            "franklin": [("Franklin", "OH"), ("Franklin", "VA")]}
    check("single-state county resolves",
          locate("Loudoun residents object", cidx)[0] == "Loudoun")
    check("ambiguous county needs a named state",
          locate("Franklin board defers", cidx)[2] in ("low", "none"))
    check("named state resolves the ambiguity",
          locate("Franklin County, Ohio board defers", cidx)[:2] == ("Franklin", "OH"))

    html = '<a data-n-au="https://example-news.org/story-1">read</a>'
    u, m = _external_url_from_html(html)
    check("extracts the publisher URL", u == "https://example-news.org/story-1")
    check("ignores google assets",
          _external_url_from_html('<img src="https://gstatic.com/x.png">')[0] == "")

    class _Resp:
        def __init__(self, url, body):
            self._u, self._b = url, body.encode()

        def geturl(self):
            return self._u

        def read(self, n=None):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_ok(req, timeout=None):
        return _Resp("https://publisher.example/a", "<html></html>")

    def fake_google(req, timeout=None):
        return _Resp("https://news.google.com/x", html)

    def fake_fail(req, timeout=None):
        raise OSError("no network")

    check("http redirect resolves",
          resolve_redirect("https://news.google.com/rss/articles/x", fake_ok)[1] == "ok")
    check("html fallback resolves",
          resolve_redirect("https://news.google.com/rss/articles/x", fake_google)[0]
          == "https://example-news.org/story-1")
    check("network failure is contained",
          resolve_redirect("https://news.google.com/rss/articles/x",
                           fake_fail)[1].startswith("error:"))

    rows = [
        {"Incident": "Socorro County approves a moratorium", "Entity": "Unknown",
         "Opposition Type": "", "Source URL": "https://news.google.com/rss/articles/a",
         "Summary": "Socorro County commissioners approve one-year moratorium on data centers - KOAT",
         "State": "", "County": ""},
        {"Incident": "Curated row", "Entity": "Acme", "Opposition Type": "moratorium",
         "Source URL": "https://publisher.example/b", "Summary": "Curated",
         "State": "Virginia", "County": "Loudoun"},
    ]
    recs = build(rows)
    check("only held rows enter the worklist", len(recs) == 1)
    check("outlet parsed without a network call", recs[0]["outlet"] == "KOAT")
    check("promote_ready is no without a resolved URL",
          recs[0]["promote_ready"] == "no")

    md = render_markdown(recs)
    check("report has no scorekeeping language", not LEAK_RE.search(md))
    check("report has no em-dash", "\u2014" not in md)
    check("worklist fields are complete",
          all(k in recs[0] for k in FIELDS))

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=IN_CSV)
    ap.add_argument("--resolve", action="store_true",
                    help="resolve Google News redirects to publisher URLs (network)")
    ap.add_argument("--limit", type=int, default=None,
                    help="with --resolve, cap the number of new fetches this run")
    ap.add_argument("--promote-template", action="store_true",
                    help="write data/untagged_promote_draft.csv for promote_ready rows")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    rows = load_csv(a.inp)
    if not rows:
        print(f"untagged_triage: no rows read from {a.inp}")
        return 1

    recs = build(rows, resolve=a.resolve, limit=a.limit)
    write_worklist(recs)
    open(OUT_MD, "w", encoding="utf-8").write(render_markdown(recs))

    conf = Counter(r["location_confidence"] for r in recs)
    print(f"untagged_triage: {len(recs)} held rows triaged")
    print(f"  with a mechanism hint : "
          f"{sum(1 for r in recs if r['suggested_mechanism'])}")
    print(f"  county high or medium : {conf['high'] + conf['medium']}")
    print(f"  outlet parsed         : {sum(1 for r in recs if r['outlet'])}")
    print(f"  resolved URLs         : "
          f"{sum(1 for r in recs if r['resolved_url'])}")
    print(f"  promote_ready         : "
          f"{sum(1 for r in recs if r['promote_ready'] == 'yes')}")
    print(f"  wrote {OUT_CSV}")
    print(f"  wrote {OUT_MD}")

    if a.promote_template:
        p, n = write_promote_draft(recs)
        print(f"  wrote {p} ({n} draft rows, each requires source review)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
