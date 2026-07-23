"""
spot_check_census.py

Validates data/county_census_features.csv after each fetch, before it is
committed. Two layers:

1. Internal consistency (vintage-independent): row count, FIPS format and
   uniqueness, density equals population divided by land area for every row,
   value ranges within physical plausibility.
2. Pinned external reference values (vintage-specific): a small set of
   county values verified by hand against independent mirrors of the same
   ACS vintage. Verified 2026-07-22 for ACS5 2019-2023. When ACS_YEAR bumps
   in fetch_census_features.py, these pins MUST be re-verified and updated;
   the script fails loudly if the vintage stamp does not match PIN_VINTAGE.

Exit nonzero on any failure so CI blocks the commit.
"""

from __future__ import annotations

import csv
import os
import sys

CSV_PATH = os.path.join("data", "county_census_features.csv")

PIN_VINTAGE = "ACS5 2023 (2019-2023)"

# fips -> (field, expected, tolerance)
# Loudoun VA verified against multiple independent ACS mirrors 2026-07-22.
# New York NY population verified against ACS 2019-2023 reporting.
PINS = [
    ("51107", "median_hh_income", 178707, 0),
    ("51107", "population", 427082, 0),
    ("51107", "pct_bachelors_plus", 64.0, 0.5),
    ("36061", "population", 1627788, 5000),
]

EXPECTED_MIN_COUNTIES = 3000
EXPECTED_MAX_COUNTIES = 3400


def fail(msg: str) -> None:
    print(f"SPOT-CHECK FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    if not os.path.exists(CSV_PATH):
        fail(f"{CSV_PATH} not found")
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))

    # --- layer 1: internal consistency ---
    if not (EXPECTED_MIN_COUNTIES <= len(rows) <= EXPECTED_MAX_COUNTIES):
        fail(f"county count {len(rows)} outside "
             f"[{EXPECTED_MIN_COUNTIES}, {EXPECTED_MAX_COUNTIES}]")

    seen = set()
    n_density_checked = 0
    for r in rows:
        f = r["fips"]
        if len(f) != 5 or not f.isdigit():
            fail(f"malformed fips {f!r}")
        if f in seen:
            fail(f"duplicate fips {f}")
        seen.add(f)
        pop, land, dens = r["population"], r["land_sqmi"], r["pop_density_sqmi"]
        if pop and land and dens:
            expected = float(pop) / float(land)
            if abs(expected - float(dens)) > max(0.02 * expected, 0.05):
                fail(f"{f}: density {dens} inconsistent with "
                     f"population/land = {expected:.2f}")
            n_density_checked += 1
        inc = r["median_hh_income"]
        if inc and not (5000 <= float(inc) <= 400000):
            fail(f"{f}: implausible median_hh_income {inc}")
        ba = r["pct_bachelors_plus"]
        if ba and not (0 <= float(ba) <= 100):
            fail(f"{f}: implausible pct_bachelors_plus {ba}")
    if n_density_checked < len(rows) * 0.95:
        fail(f"density recomputable for only {n_density_checked}/{len(rows)} rows")

    # --- layer 2: pinned external references (vintage-gated) ---
    vintages = {r["acs_vintage"] for r in rows}
    if vintages != {PIN_VINTAGE}:
        fail(f"vintage {vintages} does not match pinned vintage "
             f"{PIN_VINTAGE!r}; re-verify PINS against the new vintage "
             f"before updating PIN_VINTAGE")
    by_fips = {r["fips"]: r for r in rows}
    for fips, field, expected, tol in PINS:
        r = by_fips.get(fips)
        if r is None:
            fail(f"pinned county {fips} missing")
        val = float(r[field])
        if abs(val - expected) > tol:
            fail(f"{fips} {field}: got {val}, expected {expected} (+/-{tol})")

    print(f"spot-check passed: {len(rows)} counties, "
          f"{n_density_checked} density recomputations, "
          f"{len(PINS)} pinned values verified against {PIN_VINTAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
