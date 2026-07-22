"""
fetch_census_features.py
CI-side acquisition of county Census features for the outcome model.
Sources: ACS 5-year (api.census.gov) + Census Gazetteer (land area).
Output: data/county_census_features.csv keyed on 5-digit FIPS.
Review-first: this file is reference data; the model only uses it once
outcome_model.py explicitly joins it. Additive, no existing files touched.
Prediction-moment discipline: all features are slow-moving county attributes
(ACS 2019-2023 5-year), safe as pre-announcement information.
"""

import csv
import io
import json
import os
import sys
import urllib.request
import zipfile

ACS_YEAR = "2023"
ACS_URL = (
    "https://api.census.gov/data/{y}/acs/acs5"
    "?get=NAME,B19013_001E,B01003_001E,"
    "B15003_001E,B15003_022E,B15003_023E,B15003_024E,B15003_025E"
    "&for=county:*"
).format(y=ACS_YEAR)
GAZ_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2023_Gazetteer/2023_Gaz_counties_national.zip"
)
OUT_PATH = os.path.join("data", "county_census_features.csv")


def _get(url):
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if "api.census.gov" in url:
        if not key:
            print(
                "ERROR: api.census.gov requires an API key as of 2026-05-12. "
                "Get a free key at api.census.gov/data/key_signup.html and set "
                "it as the CENSUS_API_KEY repo secret.",
                file=sys.stderr,
            )
            sys.exit(1)
        url = url + "&key=" + key
    req = urllib.request.Request(url, headers={"User-Agent": "hawthorn-dc-pipeline"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # ACS sentinel for missing/suppressed
    if f in (-666666666.0, -999999999.0, -888888888.0) or f < 0:
        return None
    return f


def fetch_acs():
    body = _get(ACS_URL).decode("utf-8", errors="replace")
    try:
        rows = json.loads(body)
    except json.JSONDecodeError:
        print("ERROR: ACS API returned non-JSON response:", file=sys.stderr)
        print(body[:500], file=sys.stderr)
        sys.exit(1)
    header, data = rows[0], rows[1:]
    ix = {h: i for i, h in enumerate(header)}
    out = {}
    for r in data:
        fips = (r[ix["state"]] + r[ix["county"]]).zfill(5)
        income = _num(r[ix["B19013_001E"]])
        pop = _num(r[ix["B01003_001E"]])
        ed_total = _num(r[ix["B15003_001E"]])
        ba_plus = sum(
            x for x in (
                _num(r[ix["B15003_022E"]]),
                _num(r[ix["B15003_023E"]]),
                _num(r[ix["B15003_024E"]]),
                _num(r[ix["B15003_025E"]]),
            ) if x is not None
        )
        pct_ba = round(100.0 * ba_plus / ed_total, 2) if ed_total else None
        out[fips] = {
            "county_name": r[ix["NAME"]],
            "median_hh_income": int(income) if income is not None else "",
            "population": int(pop) if pop is not None else "",
            "pct_bachelors_plus": pct_ba if pct_ba is not None else "",
        }
    return out


def fetch_land_area():
    blob = _get(GAZ_URL)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    name = [n for n in zf.namelist() if n.lower().endswith(".txt")][0]
    text = zf.read(name).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    reader.fieldnames = [f.strip() for f in reader.fieldnames]
    area = {}
    for row in reader:
        geoid = row["GEOID"].strip().zfill(5)
        try:
            area[geoid] = float(row["ALAND_SQMI"].strip())
        except (KeyError, ValueError):
            continue
    return area


def main():
    acs = fetch_acs()
    land = fetch_land_area()
    n_density = 0
    os.makedirs("data", exist_ok=True)
    fields = [
        "fips", "county_name", "median_hh_income", "population",
        "land_sqmi", "pop_density_sqmi", "pct_bachelors_plus",
        "acs_vintage", "source",
    ]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for fips in sorted(acs):
            rec = acs[fips]
            sqmi = land.get(fips)
            dens = ""
            if sqmi and rec["population"] != "":
                dens = round(rec["population"] / sqmi, 2)
                n_density += 1
            w.writerow({
                "fips": fips,
                "county_name": rec["county_name"],
                "median_hh_income": rec["median_hh_income"],
                "population": rec["population"],
                "land_sqmi": sqmi if sqmi is not None else "",
                "pop_density_sqmi": dens,
                "pct_bachelors_plus": rec["pct_bachelors_plus"],
                "acs_vintage": "ACS5 %s (2019-%s)" % (ACS_YEAR, ACS_YEAR),
                "source": "api.census.gov acs/acs5 + 2023 Gazetteer",
            })
    print("wrote %s: %d counties, %d with density" % (OUT_PATH, len(acs), n_density))
    if len(acs) < 3000:
        print("WARNING: county count below expected ~3,220", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
