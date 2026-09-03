#!/usr/bin/env python3
"""
discover_socrata_dataset.py — resolve the Socrata resource id behind a named
open-data dataset, so fetch_permits.py's socrata adapter can pull it.

Why this exists

discover_arcgis_layer.py already solves this problem for ArcGIS: a source is
registered by name with `"url": null` plus a discovery block, CI resolves the
service URL, and the fetch self-completes with no code change per jurisdiction.
Socrata portals had no equivalent, so a Socrata source could only be registered
by someone opening the portal in a browser, reading the four-by-four resource
id out of the page, and pasting it into a config. That is a small task that has
to happen once per source and therefore never scales, and it is the difference
between "we could add Washington" and Washington being added.

The immediate case is Washington's SEPA Register — every SEPA and NEPA record
filed with the Department of Ecology since 2000, statewide, published on
data.wa.gov. Statewide coverage from one config matters more here than the
per-county configs the permit machinery has collected so far, because west of
the Rockies the acting jurisdiction is usually a city, and enumerating western
cities one config at a time is not a plan.

How it works

Socrata portals expose a public catalog search (`/api/catalog/v1`) that needs no
credentials. This queries it, scores each result against the config's keywords,
and writes a ranked candidate list. When exactly one candidate clears the
threshold it also writes a `resolved` block holding the SODA query URL, which
fetch_permits.py reads automatically. The output shape deliberately matches
discover_arcgis_layer.py's, so both discoverers feed the same hook.

Discovery is a suggestion, not a promotion. The resolved URL gets the candidate
file fetched; nothing reaches the dated baseline until someone writes the
column map at configs/<source>_ingest.json, exactly as with every other permit
source.

Usage
  python discover_socrata_dataset.py --config configs/wa_sepa.json
  python discover_socrata_dataset.py --selftest

Stdlib only. Selftest is fully offline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

USER_AGENT = "hawthorn-baseline/1.0 (socrata dataset discovery)"
CATALOG_PATH = "/api/catalog/v1"


def http_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def catalog_search(domain: str, query: str, limit: int = 50) -> list[dict]:
    params = {"q": query, "limit": str(limit), "only": "dataset"}
    url = f"https://{domain}{CATALOG_PATH}?" + urllib.parse.urlencode(params)
    return (http_json(url) or {}).get("results", [])


def to_query_url(domain: str, resource_id: str) -> str:
    """SODA endpoint for a resource. JSON rather than CSV because
    fetch_permits.fetch_socrata parses JSON and pages with $limit/$offset."""
    return f"https://{domain}/resource/{resource_id}.json"


FOURBYFOUR = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")


def score(result: dict, keywords: list[str]) -> int:
    """One point per distinct keyword appearing in the name or description.

    Deliberately blunt, and deliberately the same shape as the ArcGIS scorer:
    the threshold in the config is what makes a source resolve, and a clever
    scorer whose behaviour nobody can predict would make that threshold
    meaningless.
    """
    res = result.get("resource") or {}
    blob = " ".join([
        str(res.get("name") or ""),
        str(res.get("description") or ""),
        " ".join(result.get("classification", {}).get("domain_tags", []) or []),
    ]).lower()
    return sum(1 for k in keywords if k.lower() in blob)


def candidates_from(results: list[dict], keywords: list[str]) -> list[dict]:
    out = []
    for r in results:
        res = r.get("resource") or {}
        rid = str(res.get("id") or "").strip().lower()
        if not FOURBYFOUR.match(rid):
            continue
        out.append({
            "id": rid,
            "name": str(res.get("name") or "").strip(),
            "description": str(res.get("description") or "").strip()[:400],
            "updated_at": str(res.get("updatedAt") or "").strip(),
            "rows": res.get("rows_count"),
            "score": score(r, keywords),
        })
    # Highest score first, then most recently updated, then id for stability:
    # two runs over an unchanged catalog must rank identically or the resolved
    # block would flap between equally-scored datasets.
    out.sort(key=lambda c: (-c["score"], c["updated_at"] or "", c["id"]),
             reverse=False)
    out.sort(key=lambda c: -c["score"])
    return out


def resolve(candidates: list[dict], threshold: int, domain: str):
    """A `resolved` block only when exactly one candidate clears the bar.

    Two datasets clearing it is not a tie to be broken; it means the keywords
    do not identify one dataset, and guessing between them would pin the source
    to whichever happened to sort first. The report lists both and a person
    adds a keyword.
    """
    clearing = [c for c in candidates if c["score"] >= threshold]
    if len(clearing) != 1:
        return None, clearing
    top = clearing[0]
    return {
        "resource_id": top["id"],
        "name": top["name"],
        "query_url": to_query_url(domain, top["id"]),
    }, clearing


def write_report(out: dict, source: str) -> tuple[str, str]:
    os.makedirs(DATA, exist_ok=True)
    jpath = os.path.join(DATA, f"socrata_discovery_{source}.json")
    mpath = os.path.join(DATA, f"socrata_discovery_{source}.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    lines = [f"# Socrata discovery: {source}", "",
             f"Domain: `{out['domain']}`  ",
             f"Keywords: {', '.join(out['keywords'])}  ",
             f"Score threshold: {out['score_threshold']}", ""]
    if out.get("resolved"):
        r = out["resolved"]
        lines += ["## Resolved", "",
                  f"- **{r['name']}** (`{r['resource_id']}`)",
                  f"- Query URL: `{r['query_url']}`", "",
                  "fetch_permits.py picks this up automatically on the next "
                  "run. It still needs a column map at "
                  f"`configs/{source}_ingest.json` before anything reaches the "
                  "dated baseline.", ""]
    else:
        lines += ["## Not resolved", "",
                  "No single dataset cleared the threshold. Candidates below; "
                  "add a keyword to narrow, or pin the resource id into the "
                  "config by hand.", ""]
    lines += ["## Candidates", ""]
    if not out["candidates"]:
        lines.append("_none returned_")
    for c in out["candidates"][:15]:
        lines.append(f"- `{c['id']}` score {c['score']} — {c['name']}")
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return jpath, mpath


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.config:
        print("ERROR: --config required")
        return 1

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    disc = cfg.get("discovery") or {}
    source = cfg.get("source") or "unknown"
    domain = disc.get("domain")
    keywords = [k for k in disc.get("keywords", []) if k]
    threshold = int(disc.get("score_threshold", 2))
    if not domain:
        print(f"{source}: no discovery.domain in config; nothing to search")
        return 1

    results: list[dict] = []
    for q in (disc.get("queries") or [" ".join(keywords)]):
        try:
            results.extend(catalog_search(domain, q))
        except Exception as exc:
            print(f"{source}: catalog query {q!r} failed ({exc})")

    # One dataset can match several queries; collapse before scoring so a
    # dataset does not appear three times in a report and read as three
    # competing candidates.
    seen, uniq = set(), []
    for r in results:
        rid = ((r.get("resource") or {}).get("id") or "").lower()
        if rid and rid not in seen:
            seen.add(rid)
            uniq.append(r)

    cands = candidates_from(uniq, keywords)
    resolved, clearing = resolve(cands, threshold, domain)
    out = {"source": source, "domain": domain, "keywords": keywords,
           "score_threshold": threshold, "candidates": cands,
           "resolved": resolved}
    jpath, _ = write_report(out, source)
    if resolved:
        print(f"{source}: resolved -> {resolved['query_url']}")
    else:
        print(f"{source}: unresolved ({len(clearing)} candidates cleared the "
              f"threshold; discovery needs exactly one). See "
              f"{os.path.relpath(jpath, HERE)}")
    return 0


def selftest() -> int:
    ok = True

    def expect(cond, msg):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + msg)
        ok = ok and cond

    expect(to_query_url("data.wa.gov", "abcd-1234")
           == "https://data.wa.gov/resource/abcd-1234.json",
           "resource id becomes a SODA JSON endpoint")
    expect(bool(FOURBYFOUR.match("abcd-1234")), "four-by-four accepted")
    expect(not FOURBYFOUR.match("not-an-id-at-all"), "non four-by-four refused")

    results = [
        {"resource": {"id": "aaaa-1111", "name": "SEPA Register",
                      "description": "SEPA and NEPA records since 2000",
                      "updatedAt": "2026-07-28"}},
        {"resource": {"id": "bbbb-2222", "name": "Ferry ridership",
                      "description": "Monthly counts", "updatedAt": "2026-01-01"}},
        # returned twice by two different queries
        {"resource": {"id": "aaaa-1111", "name": "SEPA Register",
                      "description": "SEPA and NEPA records since 2000",
                      "updatedAt": "2026-07-28"}},
    ]
    seen, uniq = set(), []
    for r in results:
        rid = r["resource"]["id"]
        if rid not in seen:
            seen.add(rid)
            uniq.append(r)
    expect(len(uniq) == 2, "a dataset returned by two queries collapses to one")

    kws = ["sepa", "environmental"]
    cands = candidates_from(uniq, kws)
    expect(cands[0]["id"] == "aaaa-1111", "the keyword match ranks first")
    expect(cands[0]["score"] == 1 and cands[1]["score"] == 0,
           "score counts distinct keyword hits")

    res, clearing = resolve(cands, 1, "data.wa.gov")
    expect(res and res["resource_id"] == "aaaa-1111",
           "a single clearing candidate resolves")
    expect(res["query_url"] == "https://data.wa.gov/resource/aaaa-1111.json",
           "resolved block carries the query url fetch_permits reads")

    tie = candidates_from(uniq, ["records", "counts"])
    res2, clearing2 = resolve(tie, 1, "data.wa.gov")
    expect(res2 is None and len(clearing2) == 2,
           "two candidates clearing the bar refuses to resolve rather than "
           "picking one")

    expect(candidates_from(uniq, kws) == candidates_from(list(reversed(uniq)), kws),
           "ranking is order-independent, so a re-run does not flap")

    print("\nSELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
