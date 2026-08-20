#!/usr/bin/env python3
"""
fetch_county_adjacency.py

Builds a county adjacency table so a new enacted restriction can trigger a
check of the counties that border it.

Why this module exists. The TVA cross-check found Walker County GA because
an analyst noticed it adjoins Hamilton County TN, and found Grundy and
Coffee in the same sweep off Hamilton sourcing. Nothing in the pipeline
does that. Restriction adoption diffuses geographically (Walker/Hamilton,
Coffee/Warren/Franklin/Moore are the documented cases), and the tracker had
no representation of "borders" at all, so an enactment in one county could
never prompt a look at its neighbours.

Source. `https://raw.githubusercontent.com/plotly/datasets/master/
geojson-counties-fips.json`, the SAME county polygon file the choropleth
pages already fetch. That is deliberate: it adds no new external
dependency class, it is on raw.githubusercontent.com (which the standing
fetch rule already routes through first), and adjacency derived from the
same geometry the maps draw cannot disagree with what a reviewer sees on
the map.

Adjacency rule. Two counties are adjacent when their boundaries share at
least one polygon EDGE (a pair of consecutive vertices), after snapping
coordinates to a 1e-4 degree grid (about 11 m). Shared edges, not shared
points, so a corner touch alone does not create a neighbour. `shared_edges`
is carried in the output because a single shared edge is weaker evidence
than a dozen: cartographic simplification can leave one spurious segment
between counties that only nearly touch.

What this table is NOT. It is a search prompt, never evidence. A neighbour
of a restricted county is a county worth checking, and nothing more. The
asymmetry is what justifies the loose rule: a false neighbour costs one
web search, a missed neighbour costs an enactment the tracker never sees.
No downstream label, score, or client-facing claim may be derived from
adjacency alone.

Islands have no neighbours (Nantucket MA, Dukes MA, San Juan WA, the
Hawaii and Alaska island counties). They are recorded with degree 0 in the
manifest rather than dropped, so "no neighbours" reads as a measured
result instead of a join failure.

Outputs
  data/county_adjacency.csv           fips, neighbor_fips, shared_edges
  data/county_adjacency_manifest.json source, retrieval, and shape stats

Standing rules honored: additive, writes only new files, LF line endings,
no em-dashes, four-tier vocabulary, --selftest with no network dependency.

Usage
  python fetch_county_adjacency.py
  python fetch_county_adjacency.py --geojson /path/to/local.json
  python fetch_county_adjacency.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "data", "county_adjacency.csv")
OUT_MANIFEST = os.path.join(HERE, "data", "county_adjacency_manifest.json")

GEOJSON_URL = ("https://raw.githubusercontent.com/plotly/datasets/master/"
               "geojson-counties-fips.json")

# Coordinate snap, in decimal degrees. 1e-4 is about 11 m at the equator:
# tight enough that distinct borders do not merge, loose enough to absorb
# float representation differences between adjacent polygon rings.
SNAP = 4

FIELDS = ["fips", "neighbor_fips", "shared_edges"]


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "data-center-map/adjacency"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def rings(geom: dict) -> list:
    """Every linear ring in a Polygon or MultiPolygon, outer and inner.

    Inner rings (holes) are included on purpose: a county fully enclosed by
    another county borders it along that hole.
    """
    t = (geom or {}).get("type")
    c = (geom or {}).get("coordinates") or []
    if t == "Polygon":
        return list(c)
    if t == "MultiPolygon":
        return [r for poly in c for r in poly]
    return []


def snap_ring(ring: list) -> list:
    out = []
    for pt in ring:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            out.append((round(float(pt[0]), SNAP), round(float(pt[1]), SNAP)))
        except (TypeError, ValueError):
            continue
    return out


def edge_index(features: list) -> tuple[dict, list]:
    """Maps each snapped undirected edge to the set of county FIPS on it."""
    edges: dict[tuple, set] = defaultdict(set)
    seen: list[str] = []
    for f in features:
        fid = str(f.get("id") or "").strip()
        if not fid:
            props = f.get("properties") or {}
            fid = (str(props.get("STATE", "")).strip()
                   + str(props.get("COUNTY", "")).strip())
        if not fid:
            continue
        seen.append(fid)
        for r in rings(f.get("geometry") or {}):
            sp = snap_ring(r)
            for i in range(len(sp) - 1):
                a, b = sp[i], sp[i + 1]
                if a == b:
                    continue
                edges[(a, b) if a < b else (b, a)].add(fid)
    return edges, seen


def adjacency(features: list) -> dict[tuple, int]:
    """Maps an ordered (fips, neighbor_fips) pair to its shared edge count."""
    edges, _ = edge_index(features)
    pairs: Counter = Counter()
    for _edge, fids in edges.items():
        if len(fids) < 2:
            continue
        fl = sorted(fids)
        for i in range(len(fl)):
            for j in range(i + 1, len(fl)):
                pairs[(fl[i], fl[j])] += 1
    return dict(pairs)


def to_rows(pairs: dict[tuple, int]) -> list[dict]:
    """Symmetric rows: every adjacency appears in both directions, so a
    consumer can look up a single county without scanning both columns."""
    rows = []
    for (a, b), n in pairs.items():
        rows.append({"fips": a, "neighbor_fips": b, "shared_edges": n})
        rows.append({"fips": b, "neighbor_fips": a, "shared_edges": n})
    rows.sort(key=lambda r: (r["fips"], r["neighbor_fips"]))
    return rows


def write_rows(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def load_adjacency(path: str = OUT_CSV) -> dict[str, dict[str, int]]:
    """Reader for downstream modules: fips -> {neighbor_fips: shared_edges}.

    Returns an empty mapping when the file is absent, so a consumer degrades
    to no-adjacency rather than failing.
    """
    out: dict[str, dict[str, int]] = defaultdict(dict)
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            a = (r.get("fips") or "").strip()
            b = (r.get("neighbor_fips") or "").strip()
            if not a or not b:
                continue
            try:
                n = int(r.get("shared_edges") or 1)
            except ValueError:
                n = 1
            out[a][b] = n
    return out


def manifest(features: list, pairs: dict, source: str) -> dict:
    deg: Counter = Counter()
    for (a, b) in pairs:
        deg[a] += 1
        deg[b] += 1
    ids = [str(f.get("id") or "") for f in features]
    isolated = sorted(i for i in ids if i and deg.get(i, 0) == 0)
    return {
        "source": source,
        "retrieved_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "snap_decimals": SNAP,
        "rule": ("counties share at least one snapped polygon edge; "
                 "a corner touch alone is not adjacency"),
        "counties_in_geojson": len([i for i in ids if i]),
        "adjacent_pairs": len(pairs),
        "mean_degree": (round(2 * len(pairs) / len([i for i in ids if i]), 2)
                        if ids else None),
        "isolated_counties": len(isolated),
        "isolated_fips": isolated,
        "single_shared_edge_pairs": sum(1 for n in pairs.values() if n == 1),
        "use": ("search prompt only; no label, score, or client-facing "
                "claim may be derived from adjacency alone"),
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _sq(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def selftest() -> int:
    checks = []

    def ck(label, cond):
        checks.append((label, cond))

    # A and B share the vertical edge x=1. C touches B at the single point
    # (2,1) only. D is detached. E fully encloses F (hole adjacency).
    feats = [
        {"id": "00001", "geometry": {"type": "Polygon",
                                     "coordinates": _sq(0, 0, 1, 1)}},
        {"id": "00002", "geometry": {"type": "Polygon",
                                     "coordinates": _sq(1, 0, 2, 1)}},
        {"id": "00003", "geometry": {"type": "Polygon",
                                     "coordinates": _sq(2, 1, 3, 2)}},
        {"id": "00004", "geometry": {"type": "Polygon",
                                     "coordinates": _sq(9, 9, 10, 10)}},
        {"id": "00005", "geometry": {"type": "Polygon", "coordinates": [
            [[0, 5], [4, 5], [4, 9], [0, 9], [0, 5]],
            [[1, 6], [2, 6], [2, 7], [1, 7], [1, 6]]]}},
        {"id": "00006", "geometry": {"type": "Polygon",
                                     "coordinates": _sq(1, 6, 2, 7)}},
    ]

    pairs = adjacency(feats)
    key = lambda a, b: (a, b) if a < b else (b, a)  # noqa: E731

    ck("shared edge is adjacency", key("00001", "00002") in pairs)
    ck("corner touch alone is not adjacency",
       key("00002", "00003") not in pairs)
    ck("detached polygon has no neighbours",
       not any("00004" in k for k in pairs))
    ck("enclosed county borders its enclosing county along the hole",
       key("00005", "00006") in pairs)
    ck("shared edge count recorded", pairs[key("00001", "00002")] == 1)

    rows = to_rows(pairs)
    ck("rows are symmetric", len(rows) == 2 * len(pairs))
    ck("rows sorted by fips then neighbour",
       rows == sorted(rows, key=lambda r: (r["fips"], r["neighbor_fips"])))
    fwd = [r for r in rows
           if r["fips"] == "00001" and r["neighbor_fips"] == "00002"]
    rev = [r for r in rows
           if r["fips"] == "00002" and r["neighbor_fips"] == "00001"]
    ck("both directions present", len(fwd) == 1 and len(rev) == 1)

    # MultiPolygon and coordinate snapping.
    mp = [
        {"id": "00010", "geometry": {"type": "MultiPolygon", "coordinates": [
            _sq(0, 0, 1, 1), _sq(5, 5, 6, 6)]}},
        # Offset by 1e-6, below the snap threshold: still the same edge.
        {"id": "00011", "geometry": {"type": "Polygon",
                                     "coordinates": _sq(1.000001, 0, 2, 1)}},
        # Offset by 1e-2, above the snap threshold: not the same edge.
        {"id": "00012", "geometry": {"type": "Polygon",
                                     "coordinates": _sq(6.01, 5, 7, 6)}},
    ]
    mpairs = adjacency(mp)
    ck("multipolygon parts both index", key("00010", "00011") in mpairs)
    ck("sub-snap offset still joins", key("00010", "00011") in mpairs)
    ck("above-snap offset does not join", key("00010", "00012") not in mpairs)

    # Manifest shape.
    man = manifest(feats, pairs, "fixture")
    ck("manifest counts counties", man["counties_in_geojson"] == 6)
    ck("manifest counts isolated counties", man["isolated_counties"] == 2)
    ck("manifest names the isolated counties",
       man["isolated_fips"] == ["00003", "00004"])
    ck("manifest records the search-prompt-only rule",
       "search prompt only" in man["use"])

    # Malformed input must degrade, not raise.
    bad = [{"id": "00020", "geometry": {"type": "Point",
                                        "coordinates": [0, 0]}},
           {"id": "", "geometry": {"type": "Polygon",
                                   "coordinates": _sq(0, 0, 1, 1)}},
           {"geometry": None}]
    ck("unsupported geometry ignored without raising", adjacency(bad) == {})

    # Reader tolerates a missing file.
    ck("loader degrades on a missing file",
       load_adjacency(os.path.join(HERE, "data", "__no_such_file__.csv")) == {})

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--geojson", default=None,
                    help="build from a local geojson instead of fetching")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.geojson:
        source = args.geojson
        with open(args.geojson, encoding="utf-8") as fh:
            gj = json.load(fh)
    else:
        source = GEOJSON_URL
        print(f"fetching {GEOJSON_URL}")
        try:
            gj = json.loads(http_get(GEOJSON_URL).decode("utf-8"))
        except Exception as exc:  # network, HTTP, or parse
            print(f"ERROR: county geojson fetch failed: {exc}")
            print("adjacency table left as-is; downstream modules degrade "
                  "to no-adjacency")
            return 1

    features = gj.get("features") or []
    if len(features) < 3000:
        print(f"ERROR: only {len(features)} features in the geojson; "
              f"expected the full county file (about 3,221). Refusing to "
              f"overwrite the adjacency table with a partial frame.")
        return 1

    pairs = adjacency(features)
    rows = to_rows(pairs)
    write_rows(rows, OUT_CSV)
    man = manifest(features, pairs, source)
    with open(OUT_MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=2)

    print(f"counties: {man['counties_in_geojson']}")
    print(f"adjacent pairs: {man['adjacent_pairs']}")
    print(f"mean degree: {man['mean_degree']}")
    print(f"isolated counties: {man['isolated_counties']} "
          f"({', '.join(man['isolated_fips']) or 'none'})")
    print(f"pairs resting on a single shared edge: "
          f"{man['single_shared_edge_pairs']}")
    print(f"\nwrote {OUT_CSV}")
    print(f"wrote {OUT_MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
