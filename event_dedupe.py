#!/usr/bin/env python3
"""
event_dedupe.py

Event-level near-duplicate clustering for harvested coverage (spec 006, US3).

The harvester dedupes by URL, so one hearing syndicated to eighteen public
radio sites arrives as eighteen candidates, and a story republished by a
newspaper group lands in master once per masthead. This module groups those
copies so the reviewer sees one event. It is the only module that imports
datasketch.

Method
------
  text        title with a trailing outlet suffix removed (" - KCBD",
              " | Local News", " \u2013 Daily Breeze"), lowercased, tokenized to
              [a-z0-9]+; plus the first words of the article body ("lead")
              when the caller extracted one
  signature   MinHash, 128 permutations, seed 1, over 5-word shingles; one
              signature on the title and one on title+lead, each in its own
              MinHashLSH index at the Jaccard threshold (0.7)
  match       both items have a lead: the title+lead estimate must reach the
              threshold. Otherwise the title estimate must, and both titles
              need at least min_title_tokens tokens, because a short title
              ("Latest Articles", "Targeted News Service") is shared by
              unrelated pages
  compatible  checked against every member already in the cluster: dates no
              more than max_days_apart apart when both are known; the same
              domain only on the same date (a daily roundup reuses its title);
              states equal or one of them blank
  grouping    greedy in (date, id) order, so the result is deterministic and
              cannot chain A-B-C across the date window

The guards exist because title-only MinHash at 0.7 merged eight daily
"Capitol Fax" roundups and seven "Utility Dive" index pages on real data
(specs/006-source-durability-dedupe/research.md D6). All of those pairs have
Jaccard 1.0, so no threshold separates them.

Without datasketch, cluster() returns singletons and says so once. Callers
then behave exactly as they did before this module existed.

Consumers
---------
  signal_harvest.py      collapses tonight's candidate worklist to one row
                         per cluster
  status_resolution.py   proposes syndicated copies already in master as
                         supersede, through the existing reviewer-confirmed
                         path

Writes nothing. Config: configs/source_durability.json, "dedupe".

Usage
  python event_dedupe.py --selftest
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "configs", "source_durability.json")
FIXTURE = os.path.join(HERE, "tests", "fixtures", "source_durability",
                       "dedupe_articles.json")

try:
    from datasketch import MinHash, MinHashLSH
    HAVE_DATASKETCH = True
except ImportError:
    MinHash = MinHashLSH = None
    HAVE_DATASKETCH = False

DEFAULTS = {"threshold": 0.7, "num_perm": 128, "shingle_words": 5,
            "min_title_tokens": 6, "max_days_apart": 3, "seed": 1}

# A trailing " - Outlet", " | Section", " \u2013 Masthead". Stripped only when at
# least four words remain in front of it, so "Mid - Day Digest" keeps its
# words instead of collapsing to "mid".
SUFFIX = re.compile(r"\s+(?:-|\||\u2013|\u2014)\s+[^-|\u2013\u2014]{2,60}$")
TOKEN = re.compile(r"[a-z0-9]+")

_warned = False


def available() -> bool:
    return HAVE_DATASKETCH


def load_config(path: str = CONFIG) -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh).get("dedupe", {}))
    except (OSError, ValueError):
        pass
    return cfg


def normalize_url(u: str) -> str:
    """Same normalization as signal_harvest.normalize_url."""
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("?")[0].split("#")[0].rstrip("/")


def domain_of(url: str) -> str:
    return normalize_url(url).split("/")[0]


def cluster_id(url: str) -> str:
    return "evt_" + hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:10]


def normalize_title(title: str) -> list[str]:
    t = (title or "").strip()
    m = SUFFIX.search(t)
    if m and len(TOKEN.findall(t[:m.start()].lower())) >= 4:
        t = t[:m.start()]
    return TOKEN.findall(t.lower())


def tokens(text: str) -> list[str]:
    return TOKEN.findall((text or "").lower())


def shingles(words: list[str], k: int) -> set[str]:
    if not words:
        return set()
    if len(words) <= k:
        return {" ".join(words)}
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _date(v):
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _minhash(sh: set[str], cfg: dict):
    m = MinHash(num_perm=int(cfg["num_perm"]), seed=int(cfg["seed"]))
    for s in sorted(sh):
        m.update(s.encode("utf-8"))
    return m


def _compatible(a: dict, b: dict, cfg: dict) -> bool:
    da, db = _date(a.get("date")), _date(b.get("date"))
    if da and db and abs((da - db).days) > int(cfg["max_days_apart"]):
        return False
    dom_a, dom_b = (a.get("domain") or "").lower(), (b.get("domain") or "").lower()
    if dom_a and dom_a == dom_b and (da != db or not da):
        return False
    sa, sb = (a.get("state") or "").strip().upper(), (b.get("state") or "").strip().upper()
    if sa and sb and sa != sb:
        return False
    return True


def cluster(items: list[dict], cfg: dict | None = None) -> list[list]:
    """Groups items into clusters of near-duplicate coverage.

    items: dicts with "id" (unique, sortable as str) and "title"; optional
    "lead", "date" (ISO), "domain", "state". Returns a list of id lists; each
    list starts with its earliest member, and every item appears exactly once.
    """
    global _warned
    cfg = {**DEFAULTS, **(cfg or {})}
    if not items:
        return []
    if not HAVE_DATASKETCH:
        if not _warned:
            print("event_dedupe: datasketch not installed; every item is its own cluster")
            _warned = True
        return [[it["id"]] for it in items]

    if len({str(it["id"]) for it in items}) != len(items):
        raise ValueError("event_dedupe.cluster: item ids must be unique")
    th = float(cfg["threshold"])
    k = int(cfg["shingle_words"])
    by_id = {it["id"]: it for it in items}
    title_idx = MinHashLSH(threshold=th, num_perm=int(cfg["num_perm"]))
    full_idx = MinHashLSH(threshold=th, num_perm=int(cfg["num_perm"]))
    title_mh, full_mh, title_ok = {}, {}, {}
    for it in items:
        key = str(it["id"])
        words = normalize_title(it.get("title", ""))
        title_ok[key] = len(words) >= int(cfg["min_title_tokens"])
        sh = shingles(words, k)
        if sh:
            title_mh[key] = _minhash(sh, cfg)
            title_idx.insert(key, title_mh[key])
        lead = tokens(it.get("lead", ""))
        if lead and words:
            full_mh[key] = _minhash(shingles(words + lead, k), cfg)
            full_idx.insert(key, full_mh[key])

    def matches(a: str, b: str) -> bool:
        if a in full_mh and b in full_mh:
            return full_mh[a].jaccard(full_mh[b]) >= th
        if a in title_mh and b in title_mh and title_ok[a] and title_ok[b]:
            return title_mh[a].jaccard(title_mh[b]) >= th
        return False

    str_to_id = {str(it["id"]): it["id"] for it in items}
    order = sorted(str_to_id, key=lambda s: (str(by_id[str_to_id[s]].get("date") or "9999"), s))
    pos = {s: i for i, s in enumerate(order)}
    assigned: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for key in order:
        cands = set()
        if key in title_mh:
            cands.update(title_idx.query(title_mh[key]))
        if key in full_mh:
            cands.update(full_idx.query(full_mh[key]))
        joined = None
        for other in sorted(cands, key=lambda s: (pos[s], s)):
            if other == key or other not in assigned:
                continue
            root = assigned[other]
            if joined == root:
                continue
            if matches(key, other) and all(
                    _compatible(by_id[str_to_id[key]], by_id[str_to_id[m]], cfg)
                    for m in members[root]):
                joined = root
                break
        root = joined or key
        assigned[key] = root
        members.setdefault(root, []).append(key)
    return [[str_to_id[m] for m in grp] for grp in members.values()]


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # Package-free checks run everywhere.
    check("outlet suffix stripped when four words remain",
          normalize_title("Salem moratorium on new data centers could pass - KATU")
          == ["salem", "moratorium", "on", "new", "data", "centers", "could", "pass"])
    check("a short title keeps its words (no 'Mid - Day Digest' -> 'mid')",
          normalize_title("Mid - Day Digest") == ["mid", "day", "digest"])
    check("cluster_id is stable across scheme and www",
          cluster_id("https://www.example.com/a/") == cluster_id("http://example.com/a"))
    check("shingles of a short title are one shingle",
          shingles(["a", "b", "c"], 5) == {"a b c"})

    if not HAVE_DATASKETCH:
        print("SKIP (datasketch not installed): clustering checks need "
              "datasketch>=2.0,<3 from requirements/ci.txt")
        print("ALL PASS" if not fails else "FAILURES PRESENT")
        return 1 if fails else 0

    pinned = dict(DEFAULTS)          # behavior pinned at 0.7, whatever the config says
    with open(FIXTURE, encoding="utf-8") as fh:
        arts = json.load(fh)["articles"]
    cl = cluster(arts, pinned)
    check("three fixture articles give two clusters", len(cl) == 2)
    check("the two syndicated copies share a cluster",
          sorted(map(sorted, cl)) == [["a", "b"], ["c"]])

    no_lead = [{k: v for k, v in a.items() if k != "lead"} for a in arts]
    check("title-only copies still cluster", len(cluster(no_lead, pinned)) == 2)

    # Recurring same-domain column: identical title, different days.
    roundup = [{"id": i, "title": "Capitol Fax . com - Your Illinois News Radar Isabel afternoon roundup",
                "domain": "capitolfax.com", "date": d, "state": "IL"}
               for i, d in enumerate(["2026-08-18", "2026-08-31", "2026-09-11"])]
    check("a recurring same-domain title on different days stays apart",
          len(cluster(roundup, pinned)) == 3)
    same_day = [{"id": 1, "title": roundup[0]["title"], "domain": "capitolfax.com",
                 "date": "2026-08-31"},
                {"id": 2, "title": roundup[0]["title"], "domain": "capitolfax.com",
                 "date": "2026-08-31"}]
    check("the same domain on the same date is one item (URL variant)",
          len(cluster(same_day, pinned)) == 1)

    short = [{"id": 1, "title": "Latest Articles", "domain": "a.com", "date": "2026-08-07"},
             {"id": 2, "title": "Latest Articles", "domain": "b.com", "date": "2026-08-07"}]
    check("a short title does not merge on title alone", len(cluster(short, pinned)) == 2)

    t = "Pima County supervisors approve data center moratorium on new projects"
    spread = [{"id": 1, "title": t, "domain": "a.com", "date": "2026-09-01"},
              {"id": 2, "title": t, "domain": "b.com", "date": "2026-09-03"},
              {"id": 3, "title": t, "domain": "c.com", "date": "2026-09-06"}]
    got = cluster(spread, pinned)
    check("the date window holds for every member (no chaining past 3 days)",
          sorted(map(sorted, got)) == [[1, 2], [3]])

    states = [{"id": 1, "title": t, "domain": "a.com", "date": "2026-09-01", "state": "AZ"},
              {"id": 2, "title": t, "domain": "b.com", "date": "2026-09-01", "state": "TX"},
              {"id": 3, "title": t, "domain": "c.com", "date": "2026-09-01", "state": ""}]
    check("conflicting states stay apart; a blank state may join",
          sorted(map(sorted, cluster(states, pinned))) == [[1, 3], [2]])

    diff_lead = [dict(arts[0]), dict(arts[1])]
    diff_lead[1]["lead"] = ("An entirely different report about budget hearings, "
                            "school bonds and road repairs across the region this fall "
                            "with nothing at all in common with the other story")
    check("identical titles with different bodies do not merge when both leads exist",
          len(cluster(diff_lead, pinned)) == 2)

    loose = dict(pinned, threshold=0.2)
    near = [{"id": 1, "title": "County adopts data center moratorium after long hearing",
             "domain": "a.com", "date": "2026-09-01"},
            {"id": 2, "title": "County adopts data center moratorium after short debate tonight",
             "domain": "b.com", "date": "2026-09-01"}]
    check("the threshold is a config value (0.7 keeps a near pair apart, 0.2 joins it)",
          len(cluster(near, pinned)) == 2 and len(cluster(near, loose)) == 1)

    check("deterministic across runs", cluster(arts, pinned) == cluster(arts, pinned))
    mixed = spread + [dict(r, id=r["id"] + 10) for r in states]
    check("every item appears exactly once",
          sorted(x for c in cluster(mixed, pinned) for x in c)
          == [1, 2, 3, 11, 12, 13])

    print("ALL PASS" if not fails else "FAILURES PRESENT")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
