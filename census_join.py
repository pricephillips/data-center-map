"""
census_join.py
Join helper: attach county Census features to any dataframe carrying a FIPS
column (baseline_universe.csv style). Leaves NaN where no match; never drops
rows. CT caveat: ACS 2023 reports Connecticut as planning regions (09110+),
so legacy CT county FIPS will not match and are left NaN by design.
"""

import pandas as pd

FEATURES_PATH = "data/county_census_features.csv"
FEATURE_COLS = ["median_hh_income", "pop_density_sqmi", "pct_bachelors_plus"]


def add_census_features(df, fips_col="fips", path=FEATURES_PATH):
    cf = pd.read_csv(path, dtype={"fips": str})
    cf["fips"] = cf["fips"].str.zfill(5)
    keep = ["fips"] + FEATURE_COLS
    left = df.copy()
    left["_fips_join"] = (
        left[fips_col].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)
    )
    merged = left.merge(
        cf[keep].rename(columns={"fips": "_fips_join"}),
        on="_fips_join", how="left",
    ).drop(columns=["_fips_join"])
    return merged
