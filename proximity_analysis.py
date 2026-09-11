"""
proximity_analysis.py
========================================================================
Spatial analysis over incident coordinates, run after the clean feed is built.
Produces data/proximity_report.md plus data/contagion_rows.csv.

The default output directory was `out/` until 2026-09-11, a directory no other
module writes to and which is not in the repository. The analysis ran, exited
clean, and dropped its results somewhere nothing collected them, which is why
it read as dormant while being perfectly functional. Everything derived lands
in data/; so does this.

What it computes today (all from existing lat/lon):
  1. Contagion: share of new incidents arising within RADIUS_MILES of an
     enacted block from the prior LOOKBACK_DAYS, vs. a shuffled-date baseline.
  2. Nearest-neighbor distances between incidents (clustering summary).
  3. State-level hotspot density (incidents per 1,000 sq mi, top 10).

8-mile readiness: group_distance() takes any table of group coordinates
(canonical_name, lat, lon) and computes distance from each group to its
nearest contested facility, reproducing the Axios claim against our data the
day group geocodes exist. Geocoding itself requires network access and is
intentionally out of scope here.
"""

from __future__ import annotations

import csv
import math
import os
import sys
from datetime import datetime, timedelta

RADIUS_MILES = 50.0
LOOKBACK_DAYS = 365
EIGHT_MILES = 8.0

STATE_SQMI = {  # land area, used for density
    "OH": 40861, "MI": 56539, "VA": 39490, "GA": 57513, "NC": 48618,
    "TX": 261232, "PA": 44743, "WI": 54158, "IN": 35826, "IL": 55519,
    "NJ": 7354, "NY": 47126, "MD": 9707, "TN": 41235, "SC": 30061,
}


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _load(path: str):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    pts = []
    for r in rows:
        try:
            lat, lon = float(r.get("lat", "")), float(r.get("lon", ""))
        except (TypeError, ValueError):
            continue
        try:
            dt = datetime.fromisoformat(str(r.get("Date", ""))[:10])
        except ValueError:
            dt = None
        pts.append({"lat": lat, "lon": lon, "dt": dt,
                    "state": str(r.get("State", "")).strip(),
                    "incident": r.get("Incident", ""),
                    "block": str(r.get("qc_block_status", "")) == "enacted_block"})
    return pts


def contagion(pts, radius=RADIUS_MILES, lookback=LOOKBACK_DAYS):
    dated = [p for p in pts if p["dt"]]
    blocks = [p for p in dated if p["block"]]
    hits, rows = 0, []
    for p in dated:
        window = [b for b in blocks
                  if b is not p and b["dt"] < p["dt"]
                  and (p["dt"] - b["dt"]) <= timedelta(days=lookback)]
        near = next((b for b in window
                     if haversine_miles(p["lat"], p["lon"], b["lat"], b["lon"]) <= radius), None)
        if near:
            hits += 1
            rows.append({"incident": p["incident"], "state": p["state"],
                         "date": p["dt"].date().isoformat(),
                         "near_block": near["incident"],
                         "block_date": near["dt"].date().isoformat()})
    # Null distribution: permute dates across the same geography K times,
    # so the baseline carries uncertainty instead of a single arbitrary shift.
    import random
    import statistics
    rng = random.Random(20260707)
    dates = [p["dt"] for p in dated]
    K = 50
    base_samples = []
    for _ in range(K):
        perm = dates[:]
        rng.shuffle(perm)
        bh = 0
        for p, pdt in zip(dated, perm):
            window = [b for b in blocks
                      if b is not p and b["dt"] < pdt
                      and (pdt - b["dt"]) <= timedelta(days=lookback)]
            if any(haversine_miles(p["lat"], p["lon"], b["lat"], b["lon"]) <= radius
                   for b in window):
                bh += 1
        base_samples.append(bh)
    base_mean = statistics.mean(base_samples)
    base_sd = statistics.pstdev(base_samples) or 1.0
    z = (hits - base_mean) / base_sd
    return hits, len(dated), (base_mean, base_sd, z), rows


def nearest_neighbor(pts):
    ds = []
    for i, p in enumerate(pts):
        best = min((haversine_miles(p["lat"], p["lon"], q["lat"], q["lon"])
                    for j, q in enumerate(pts) if j != i), default=None)
        if best is not None:
            ds.append(best)
    ds.sort()
    med = ds[len(ds) // 2] if ds else 0
    w8 = sum(1 for d in ds if d <= EIGHT_MILES)
    return med, w8, len(ds)


def group_distance(groups: list[dict], facilities: list[dict]) -> list[dict]:
    """groups: [{canonical_name, lat, lon}], facilities: [{incident, lat, lon}].
    Returns per-group nearest-facility distance; the 8-mile test is
    share of groups with nearest_miles > 8."""
    out = []
    for g in groups:
        best, which = None, ""
        for f in facilities:
            d = haversine_miles(float(g["lat"]), float(g["lon"]),
                                float(f["lat"]), float(f["lon"]))
            if best is None or d < best:
                best, which = d, f.get("incident", "")
        out.append({"canonical_name": g.get("canonical_name", ""),
                    "nearest_miles": round(best, 1) if best is not None else "",
                    "nearest_facility": which,
                    "beyond_8mi": (best or 0) > EIGHT_MILES})
    return out


def main(path: str, outdir: str = "data",
         radius: float = RADIUS_MILES, lookback: int = LOOKBACK_DAYS):
    os.makedirs(outdir, exist_ok=True)
    pts = _load(path)
    if not pts:
        print("no coordinate rows found; nothing to analyze")
        return
    hits, n, (base_mean, base_sd, z), rows = contagion(pts, radius=radius, lookback=lookback)
    med, w8, nn = nearest_neighbor(pts)
    dens = []
    from collections import Counter
    cnt = Counter(p["state"] for p in pts)
    for st, c in cnt.items():
        if st in STATE_SQMI:
            dens.append((st, c, 1000 * c / STATE_SQMI[st]))
    dens.sort(key=lambda t: -t[2])

    with open(os.path.join(outdir, "contagion_rows.csv"), "w",
              newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)

    lines = [
        "# Proximity analysis",
        f"Coordinates: {len(pts)} incidents; dated: {n}.",
        "",
        f"**Contagion:** {hits}/{n} incidents ({100*hits/max(n,1):.1f}%) arose within "
        f"{radius:.0f} miles of an enacted block from the prior {lookback} days, "
        f"vs. {100*base_mean/max(n,1):.1f}% (sd {100*base_sd/max(n,1):.1f}pp) under a "
        f"50-permutation date-shuffled null (z = {z:+.1f}; |z| < 2 means no "
        "evidence of spatial contagion beyond baseline geography). "
        "Rows in contagion_rows.csv.",
        "",
        f"**Clustering:** median nearest-neighbor distance {med:.1f} miles; "
        f"{w8}/{nn} incidents ({100*w8/max(nn,1):.0f}%) have another incident within 8 miles.",
        "",
        "**Density (incidents per 1,000 sq mi, covered states):** " +
        ", ".join(f"{st} {d:.2f} (n={c})" for st, c, d in dens[:10]),
        "",
        "_8-mile test scaffold ready: feed group geocodes to group_distance() "
        "to reproduce the Axios protester-distance claim against our data._",
    ]
    rpath = os.path.join(outdir, "proximity_report.md")
    open(rpath, "w").write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nwritten: {rpath}")


def selftest():
    """Geometry and window logic only. No files, no feed, no network."""
    from datetime import datetime as _dt
    checks = []

    def check(label, ok):
        checks.append((label, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    # ~69 miles per degree of latitude at the equator.
    d = haversine_miles(0.0, 0.0, 1.0, 0.0)
    check("a degree of latitude is about 69 miles", 68.0 < d < 70.0)
    check("zero distance to itself", haversine_miles(40.0, -83.0, 40.0, -83.0) == 0.0)
    check("distance is symmetric",
          round(haversine_miles(40.0, -83.0, 41.0, -84.0), 6)
          == round(haversine_miles(41.0, -84.0, 40.0, -83.0), 6))

    def pt(lat, lon, day, block=False):
        return {"lat": lat, "lon": lon, "dt": _dt(2026, 1, day),
                "state": "OH", "incident": f"i{day}", "block": block}

    # A block, then an incident 10 days later at the same spot: inside both
    # the radius and the lookback, so it counts once.
    hits, n, _, rows = contagion([pt(40.0, -83.0, 1, block=True), pt(40.0, -83.0, 11)])
    check("an incident near a prior block counts as a hit", hits == 1 and n == 2)
    check("the hit row names the block it was near",
          rows and rows[0]["near_block"] == "i1")

    # Same pair, but the block is outside the radius.
    far, _, _, _ = contagion([pt(40.0, -83.0, 1, block=True), pt(45.0, -83.0, 11)])
    check("a block beyond the radius does not count", far == 0)

    # Same pair, but the block is after the incident.
    later, _, _, _ = contagion([pt(40.0, -83.0, 11, block=True), pt(40.0, -83.0, 1)])
    check("a block after the incident does not count", later == 0)

    # Same pair, but the block is outside the lookback.
    stale, _, _, _ = contagion([pt(40.0, -83.0, 1, block=True), pt(40.0, -83.0, 11)],
                               lookback=5)
    check("a block older than the lookback does not count", stale == 0)

    med, w8, nn = nearest_neighbor([pt(40.0, -83.0, 1), pt(40.0, -83.0, 2)])
    check("co-located incidents are within eight miles", w8 == 2 and nn == 2)
    check("co-located incidents have zero median distance", med == 0)

    g = group_distance([{"canonical_name": "G", "lat": 40.0, "lon": -83.0}],
                       [{"incident": "F", "lat": 40.0, "lon": -83.0}])
    check("a group at a facility is not beyond eight miles",
          g[0]["beyond_8mi"] is False and g[0]["nearest_facility"] == "F")
    g2 = group_distance([{"canonical_name": "G", "lat": 41.0, "lon": -83.0}],
                        [{"incident": "F", "lat": 40.0, "lon": -83.0}])
    check("a group a degree away is beyond eight miles", g2[0]["beyond_8mi"] is True)

    n_ok = sum(1 for _, ok in checks if ok)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default="master_opposition_clean.csv")
    ap.add_argument("outdir", nargs="?", default="data")
    ap.add_argument("--radius", type=float, default=RADIUS_MILES,
                    help="contagion radius in miles")
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS,
                    help="contagion lookback window in days")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    main(a.csv, a.outdir, radius=a.radius, lookback=a.lookback)
