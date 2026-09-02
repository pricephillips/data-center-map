#!/usr/bin/env python3
"""
state_bounds.py -- one bounding box per state, and the "is this row plotted in
the state it claims" check that reads them.

WHY THIS EXISTS
---------------
In July 2026 a duplicate `id` in data/proposals.csv merged six Pennsylvania
projects with six projects in other states. The joins fanned out silently and
baseline_universe.csv published, among others, "Nebius Butler Township Data
Center", Schuylkill County PA, at 32.507, -91.647 -- Richland Parish,
Louisiana, under operator "Meta". Nothing errored. The contradiction between a
row's own `state` column and its own coordinates was visible in the published
artifact for weeks and no gate looked at it.

assert_unique_project_ids() in project_resolution.py now stops that particular
cause. This module is the more general check: whatever the cause, a row whose
coordinates are not in the state it names is wrong, and saying so is cheap.

WHAT THESE BOXES ARE FOR
------------------------
Catching gross contradictions -- a Pennsylvania project in Louisiana -- not
adjudicating borders. Each box is the state's extent padded by PAD degrees, so
a site legitimately sitting on a state line does not trip it. A box is a
rectangle and a state is not, so a point can be inside the box and outside the
state (the Florida panhandle's box covers part of Georgia). That asymmetry is
deliberate: this check produces no false positives at the cost of missing
near-miss errors, which is the right trade for a blocking gate.

Alaska carries two boxes because the Aleutians cross the antimeridian and a
single rectangle would either miss them or span the globe.

Coordinates are (min_lat, max_lat, min_lon, max_lon).
"""

from __future__ import annotations

# Padding applied to every box, in degrees (~17 km of latitude). Absorbs
# border sites and low-precision geocodes without letting a wrong-state error
# through: the smallest state-to-state error this check needs to catch is far
# larger than this.
PAD = 0.15

# Extents by USPS code. DC and PR included because both appear in the data.
_RAW: dict[str, list[tuple[float, float, float, float]]] = {
    "AL": [(30.14, 35.01, -88.48, -84.89)],
    "AK": [(51.20, 71.45, -180.00, -129.97),
           (51.20, 53.02, 172.40, 180.00)],   # Aleutians, east of the antimeridian
    "AZ": [(31.33, 37.01, -114.82, -109.04)],
    "AR": [(33.00, 36.50, -94.62, -89.64)],
    "CA": [(32.53, 42.01, -124.41, -114.13)],
    "CO": [(36.99, 41.01, -109.06, -102.04)],
    "CT": [(40.98, 42.05, -73.73, -71.79)],
    "DE": [(38.45, 39.84, -75.79, -75.05)],
    "DC": [(38.79, 39.00, -77.12, -76.91)],
    "FL": [(24.40, 31.01, -87.64, -79.97)],
    "GA": [(30.35, 35.01, -85.61, -80.84)],
    "HI": [(18.86, 22.24, -160.25, -154.79)],
    "ID": [(41.99, 49.01, -117.25, -111.04)],
    "IL": [(36.97, 42.51, -91.52, -87.02)],
    "IN": [(37.77, 41.77, -88.10, -84.78)],
    "IA": [(40.37, 43.51, -96.64, -90.14)],
    "KS": [(36.99, 40.01, -102.06, -94.59)],
    "KY": [(36.49, 39.15, -89.58, -81.96)],
    "LA": [(28.92, 33.02, -94.05, -88.76)],
    "ME": [(42.98, 47.46, -71.09, -66.95)],
    "MD": [(37.89, 39.73, -79.49, -75.05)],
    "MA": [(41.24, 42.89, -73.51, -69.93)],
    "MI": [(41.70, 48.31, -90.42, -82.41)],
    "MN": [(43.50, 49.39, -97.24, -89.49)],
    "MS": [(30.17, 35.01, -91.66, -88.10)],
    "MO": [(35.99, 40.62, -95.775, -89.10)],
    "MT": [(44.36, 49.01, -116.06, -104.04)],
    "NE": [(39.99, 43.01, -104.06, -95.31)],
    "NV": [(35.00, 42.01, -120.01, -114.04)],
    "NH": [(42.70, 45.31, -72.56, -70.70)],
    "NJ": [(38.92, 41.36, -75.57, -73.89)],
    "NM": [(31.33, 37.01, -109.06, -103.00)],
    "NY": [(40.49, 45.02, -79.77, -71.85)],
    "NC": [(33.83, 36.59, -84.33, -75.45)],
    "ND": [(45.93, 49.01, -104.06, -96.55)],
    "OH": [(38.40, 42.33, -84.83, -80.51)],
    "OK": [(33.61, 37.01, -103.01, -94.43)],
    "OR": [(41.99, 46.30, -124.57, -116.46)],
    "PA": [(39.71, 42.28, -80.53, -74.68)],
    "PR": [(17.87, 18.53, -67.96, -65.21)],
    "RI": [(41.14, 42.02, -71.91, -71.12)],
    "SC": [(32.03, 35.22, -83.36, -78.54)],
    "SD": [(42.47, 45.95, -104.06, -96.43)],
    "TN": [(34.98, 36.68, -90.31, -81.64)],
    "TX": [(25.83, 36.51, -106.65, -93.50)],
    "UT": [(36.99, 42.01, -114.05, -109.04)],
    "VT": [(42.72, 45.02, -73.44, -71.46)],
    "VA": [(36.54, 39.47, -83.68, -75.24)],
    "WA": [(45.54, 49.01, -124.85, -116.91)],
    "WV": [(37.20, 40.65, -82.65, -77.72)],
    "WI": [(42.49, 47.31, -92.89, -86.76)],
    "WY": [(40.99, 45.01, -111.06, -104.05)],
}

ABBREV_BY_NAME = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "washington dc": "DC", "washington, d.c.": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "puerto rico": "PR", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY",
}

# Padded boxes, built once. Longitude padding is NOT applied across the
# antimeridian: widening the Aleutian box past 180 would make it wrap.
BOXES: dict[str, list[tuple[float, float, float, float]]] = {
    code: [(s - PAD, n + PAD,
            max(-180.0, w - PAD), min(180.0, e + PAD))
           for (s, n, w, e) in boxes]
    for code, boxes in _RAW.items()
}

US_BOX = (17.0, 72.0, -180.0, -64.0)


def normalize_state(value) -> str | None:
    """USPS code for a state given as a code or a full name, else None."""
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    if len(v) == 2 and v.upper() in BOXES:
        return v.upper()
    return ABBREV_BY_NAME.get(v.lower())


def in_box(lat: float, lon: float, box) -> bool:
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


def in_state(lat, lon, state) -> bool | None:
    """True/False if the point is inside the state's box(es).

    None means "cannot tell" -- an unrecognized state, or coordinates that are
    absent or non-numeric. A caller deciding whether to block must treat None
    as "no opinion" rather than as a pass or a failure: this check speaks only
    about rows where both a known state and real coordinates are present.
    """
    code = normalize_state(state)
    if code is None:
        return None
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        return None
    # (0, 0) is a missing geocode, not a location off West Africa. Callers
    # check for it separately; here it is simply not evidence about the state.
    if abs(la) < 0.001 and abs(lo) < 0.001:
        return None
    return any(in_box(la, lo, b) for b in BOXES[code])


def violations(rows, id_key, state_key="state", lat_key="lat", lon_key="lon",
               name_key="name", exempt=()):
    """Rows whose coordinates fall outside the state they claim.

    Returns a list of (row_id, name, state, lat, lon). `exempt` is a set of
    row ids that are known-bad and separately tracked; they are skipped so the
    gate can block on everything else rather than being switched off wholesale.
    """
    out = []
    for r in rows:
        rid = str(r.get(id_key) or "").strip()
        if rid in exempt:
            continue
        verdict = in_state(r.get(lat_key), r.get(lon_key), r.get(state_key))
        if verdict is False:
            out.append((rid, str(r.get(name_key) or "").strip(),
                        str(r.get(state_key) or "").strip(),
                        r.get(lat_key), r.get(lon_key)))
    return out


def selftest() -> int:
    checks = []

    def ok(label, cond):
        checks.append((label, bool(cond)))

    # The case this module exists for: PA project at Richland Parish, LA.
    ok("PA project in Louisiana is caught", in_state(32.507, -91.647, "PA") is False)
    ok("the same point is inside Louisiana", in_state(32.507, -91.647, "LA") is True)
    # The two open facility rows, both real errors still on file.
    ok("New Albany OH plotted in Georgia is caught",
       in_state(33.948, -84.5499, "OH") is False)
    ok("Holly Ridge LA plotted in Oregon is caught",
       in_state(45.5051, -122.9752, "LA") is False)
    # True positives should stay quiet.
    ok("Schuylkill County PA passes", in_state(40.7386, -76.3191, "PA") is True)
    ok("Butler County OH passes", in_state(39.44, -84.56, "OH") is True)
    ok("Shelby County TN passes", in_state(35.10, -89.97, "TN") is True)
    ok("full state name resolves", in_state(40.7386, -76.3191, "Pennsylvania") is True)
    # Alaska's two boxes.
    ok("Anchorage passes", in_state(61.22, -149.90, "AK") is True)
    ok("Attu, west of the antimeridian, passes", in_state(52.88, 173.18, "AK") is True)
    ok("Anchorage is not in Hawaii", in_state(61.22, -149.90, "HI") is False)
    # "Cannot tell" cases must be None, never False: a blocking gate that
    # treated a missing coordinate as a violation would fail on absent data.
    ok("unknown state is None", in_state(40.0, -76.0, "Freedonia") is None)
    ok("blank state is None", in_state(40.0, -76.0, "") is None)
    ok("missing coords are None", in_state("", "", "PA") is None)
    ok("non-numeric coords are None", in_state("n/a", "n/a", "PA") is None)
    ok("null island is None", in_state(0.0, 0.0, "PA") is None)
    ok("out-of-range coords are None", in_state(99.0, -76.0, "PA") is None)
    # Coverage.
    ok("52 jurisdictions carry a box", len(BOXES) == 52)
    ok("every box is well formed",
       all(b[0] < b[1] and b[2] < b[3] for bs in BOXES.values() for b in bs))
    # violations() plumbing, including the exemption path.
    rows = [{"universe_id": "a", "name": "good", "state": "PA", "lat": 40.7, "lon": -76.3},
            {"universe_id": "b", "name": "bad", "state": "PA", "lat": 32.5, "lon": -91.6},
            {"universe_id": "c", "name": "exempt", "state": "OH", "lat": 33.9, "lon": -84.5}]
    v = violations(rows, "universe_id", exempt={"c"})
    ok("violations finds the one unexempted bad row",
       len(v) == 1 and v[0][0] == "b")

    for label, good in checks:
        print(("  PASS  " if good else "  FAIL  ") + label)
    bad = sum(1 for _, g in checks if not g)
    print(f"\n{len(checks) - bad}/{len(checks)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
