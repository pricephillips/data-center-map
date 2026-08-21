"""
discover_arcgis_layer.py -- resolve the ArcGIS service layer behind a state
permit-tracker web app, so fetch_permits.py can pull it.

Registered 2026-08-21. Why this exists: state agencies are starting to ship
data-center permit trackers as JavaScript map apps (first case: PA DEP's
Data Center Permit Tracker at https://gis.dep.pa.gov/DataCenterPermitTracker/).
The data behind such an app is an ArcGIS FeatureServer/MapServer layer that
fetch_permits.py's arcgis adapter can already consume, but the layer URL is
buried in the app's JS bundle or portal item and is not always discoverable
from a sandbox without JS execution. This module runs in GitHub Actions,
where network access is unrestricted, and does the discovery there.

Two strategies, tried in order:

1. app_config: fetch the app's Experience Builder / WAB config JSON
   (config.json and the common cdn/ variants relative to the app URL) and
   any portal item it references, then extract every FeatureServer/MapServer
   URL found anywhere in the JSON.
2. rest_crawl: walk one or more ArcGIS REST services directories
   (?f=pjson), recursing folders, and score every layer name / service
   name / description against the config's keywords.

Output: data/arcgis_discovery_<source>.json with ranked candidates and, when
exactly one candidate clears the score threshold, a "resolved" block holding
the layer query URL. fetch_permits.py reads that block automatically when the
source config's own "url" is null, so the pipeline self-completes on the
first CI run after a tracker is registered; no code change is needed per
state. A markdown report is written next to it for review.

The resolved URL is a suggestion until a human confirms the ingest column
map (configs/<source>_ingest.json); nothing is promoted into the dated
baseline by discovery alone, matching fetch_permits.py's existing
candidates-first design.

No scorekeeping vocabulary. Stdlib only. Selftest is fully offline.

Usage:
  python3 discover_arcgis_layer.py --config configs/pa_dep_datacenter.json
  python3 discover_arcgis_layer.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")

SERVICE_URL_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?/rest/services/[^\s\"'<>\\]+?"
    r"/(?:FeatureServer|MapServer)(?:/\d+)?",
    re.I,
)

APP_CONFIG_PATHS = (
    "config.json",
    "cdn/config.json",
    "configs/config.json",
    "app.json",
)

DEFAULT_TIMEOUT = 45
MAX_FOLDER_FETCHES = 60  # hard cap so a huge directory cannot run away


def http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "hawthorn-discovery/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def http_json(url: str, timeout: int = DEFAULT_TIMEOUT):
    return json.loads(http_get(url, timeout))


# ---------------------------------------------------------------------------
# Matching (pure functions; covered by the offline selftest)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())


def keyword_score(text: str, keywords: list[str]) -> int:
    """Count keyword hits in text. A keyword with a space must appear as a
    phrase; single tokens match on word boundaries. 'data center' and
    'datacenter' are distinct keywords for exactly this reason."""
    hay = normalize(text)
    hay_packed = hay.replace(" ", "")
    score = 0
    for kw in keywords:
        k = normalize(kw).strip()
        if not k:
            continue
        if " " in k:
            if k in hay:
                score += 2
        else:
            if re.search(r"\b" + re.escape(k) + r"\b", hay):
                score += 2
            elif k in hay_packed:
                score += 1
    return score


def extract_service_urls(blob: str) -> list[str]:
    """Every FeatureServer/MapServer URL in a text blob, deduplicated,
    layer-level URLs preferred over bare service URLs."""
    seen: dict[str, None] = {}
    for m in SERVICE_URL_RE.finditer(blob or ""):
        seen.setdefault(m.group(0), None)
    urls = list(seen)
    urls.sort(key=lambda u: (not re.search(r"/\d+$", u), u))
    return urls


def to_query_url(layer_url: str) -> str:
    """Layer URL -> its /query endpoint. Bare service URLs get layer 0."""
    u = layer_url.rstrip("/")
    if not re.search(r"/\d+$", u):
        u = u + "/0"
    return u + "/query"


def rank_candidates(cands: list[dict], threshold: int) -> tuple[list[dict], dict | None]:
    """Sort candidates by score desc. Resolve only when the top candidate
    clears the threshold AND outscores the runner-up (a tie means ambiguity,
    which a human should break, not this script)."""
    ranked = sorted(cands, key=lambda c: (-c["score"], c["url"]))
    if not ranked:
        return ranked, None
    top = ranked[0]
    if top["score"] < threshold:
        return ranked, None
    if len(ranked) > 1 and ranked[1]["score"] == top["score"]:
        return ranked, None
    return ranked, top


# ---------------------------------------------------------------------------
# Strategy 1: app config extraction
# ---------------------------------------------------------------------------

def discover_from_app(app_url: str, keywords: list[str]) -> list[dict]:
    cands: list[dict] = []
    base = app_url.rstrip("/") + "/"
    blobs: list[tuple[str, str]] = []
    for rel in APP_CONFIG_PATHS:
        url = urllib.parse.urljoin(base, rel)
        try:
            blobs.append((url, http_get(url)))
        except Exception:
            continue
    # The app shell itself sometimes inlines the service URL.
    try:
        blobs.append((app_url, http_get(app_url)))
    except Exception:
        pass
    for src_url, blob in blobs:
        for svc in extract_service_urls(blob):
            cands.append({
                "url": svc,
                "score": keyword_score(svc, keywords) + 1,  # +1: named by the app itself
                "via": f"app_config:{src_url}",
                "name": svc.rsplit("/rest/services/", 1)[-1],
            })
    return cands


# ---------------------------------------------------------------------------
# Strategy 2: REST directory crawl
# ---------------------------------------------------------------------------

def discover_from_rest(rest_root: str, keywords: list[str]) -> list[dict]:
    cands: list[dict] = []
    fetches = 0

    def dir_json(url: str):
        nonlocal fetches
        if fetches >= MAX_FOLDER_FETCHES:
            return None
        fetches += 1
        try:
            return http_json(url.rstrip("/") + "?f=pjson")
        except Exception:
            return None

    root = dir_json(rest_root)
    if not root:
        return cands
    folders = [""] + list(root.get("folders") or [])
    for folder in folders:
        base = rest_root.rstrip("/") + ("/" + folder if folder else "")
        listing = root if folder == "" else dir_json(base)
        if not listing:
            continue
        for svc in listing.get("services") or []:
            name = svc.get("name") or ""
            stype = svc.get("type") or ""
            if stype not in ("FeatureServer", "MapServer"):
                continue
            svc_url = rest_root.rstrip("/") + "/" + name.split("/", 1)[-1] + "/" + stype \
                if "/" in name else rest_root.rstrip("/") + "/" + name + "/" + stype
            base_score = keyword_score(name, keywords)
            svc_meta = None
            if base_score > 0:
                svc_meta = dir_json(svc_url)
            layers = (svc_meta or {}).get("layers") or []
            if layers:
                for lyr in layers:
                    lscore = base_score + keyword_score(lyr.get("name") or "", keywords)
                    if lscore > 0:
                        cands.append({
                            "url": f"{svc_url}/{lyr.get('id')}",
                            "score": lscore,
                            "via": f"rest_crawl:{rest_root}",
                            "name": f"{name}/{lyr.get('name')}",
                        })
            elif base_score > 0:
                cands.append({
                    "url": svc_url,
                    "score": base_score,
                    "via": f"rest_crawl:{rest_root}",
                    "name": name,
                })
    return cands


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(config_path: str) -> int:
    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    disc = cfg.get("discovery") or {}
    source = cfg.get("source") or os.path.splitext(os.path.basename(config_path))[0]
    keywords = disc.get("keywords") or ["data center", "datacenter"]
    threshold = int(disc.get("score_threshold", 2))

    cands: list[dict] = []
    for app_url in disc.get("app_urls") or []:
        cands.extend(discover_from_app(app_url, keywords))
    for rest_root in disc.get("rest_roots") or []:
        cands.extend(discover_from_rest(rest_root, keywords))

    # merge duplicates, keeping the best score per URL
    best: dict[str, dict] = {}
    for c in cands:
        cur = best.get(c["url"])
        if cur is None or c["score"] > cur["score"]:
            best[c["url"]] = c
    ranked, top = rank_candidates(list(best.values()), threshold)

    os.makedirs(DATA_DIR, exist_ok=True)
    out = {
        "source": source,
        "keywords": keywords,
        "score_threshold": threshold,
        "candidates": ranked[:25],
        "resolved": None,
    }
    if top:
        out["resolved"] = {
            "layer_url": top["url"],
            "query_url": to_query_url(top["url"]),
            "score": top["score"],
            "via": top["via"],
            "note": ("Auto-resolved: single top-scoring candidate. Confirm the "
                     "field schema and write configs/" + source + "_ingest.json "
                     "before rows are promoted to the dated baseline."),
        }
    json_path = os.path.join(DATA_DIR, f"arcgis_discovery_{source}.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")

    md_path = os.path.join(DATA_DIR, f"arcgis_discovery_{source}.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# ArcGIS layer discovery: {source}\n\n")
        if top:
            fh.write(f"Resolved to `{top['url']}` (score {top['score']}, {top['via']}).\n\n")
            fh.write("fetch_permits.py will use this automatically on its next run "
                     "for this source. Confirm the schema and add the ingest "
                     "column map to promote rows.\n\n")
        else:
            fh.write("Not resolved. Either no candidate cleared the score "
                     "threshold or the top two tied. Ranked candidates below; "
                     "pin the right one into the source config's \"url\" by hand.\n\n")
        fh.write("| score | layer | via |\n|---|---|---|\n")
        for c in ranked[:25]:
            fh.write(f"| {c['score']} | {c['url']} | {c['via']} |\n")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"candidates: {len(ranked)}; resolved: {'yes' if top else 'no'}")
    return 0


# ---------------------------------------------------------------------------
# Selftest (offline)
# ---------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def expect(cond, label):
        if not cond:
            fails.append(label)

    blob = ('{"dataSource":"https://gis.example.gov/arcgis/rest/services/'
            'DEP/DataCenterTracker/FeatureServer/0","other":'
            '"https://gis.example.gov/arcgis/rest/services/Base/Roads/MapServer"}')
    urls = extract_service_urls(blob)
    expect(len(urls) == 2, "extracts both service urls")
    expect(urls[0].endswith("/FeatureServer/0"), "layer-level url ranks first")

    expect(to_query_url("https://x/rest/services/A/B/FeatureServer/3")
           .endswith("/FeatureServer/3/query"), "query url keeps layer id")
    expect(to_query_url("https://x/rest/services/A/B/MapServer")
           .endswith("/MapServer/0/query"), "bare service url gets layer 0")

    kws = ["data center", "datacenter", "permit"]
    expect(keyword_score("DEP/DataCenterPermitTracker", kws) >= 2,
           "packed name matches")
    expect(keyword_score("Data Center Permit Points", kws) >= 4,
           "phrase and token both hit")
    expect(keyword_score("Coal Refuse Areas", kws) == 0, "unrelated scores zero")

    cands = [
        {"url": "u1", "score": 5, "via": "t", "name": "a"},
        {"url": "u2", "score": 3, "via": "t", "name": "b"},
    ]
    ranked, top = rank_candidates(cands, threshold=2)
    expect(top is not None and top["url"] == "u1", "clear winner resolves")
    ranked, top = rank_candidates(
        [{"url": "u1", "score": 3, "via": "t", "name": "a"},
         {"url": "u2", "score": 3, "via": "t", "name": "b"}], threshold=2)
    expect(top is None, "tie does not resolve")
    ranked, top = rank_candidates(
        [{"url": "u1", "score": 1, "via": "t", "name": "a"}], threshold=2)
    expect(top is None, "below threshold does not resolve")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("discover_arcgis_layer selftest: 8 checks OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.config:
        print("ERROR: --config required (or --selftest)")
        return 1
    return run(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
