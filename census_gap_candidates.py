"""
census_gap_candidates.py -- turn coverage-audit misses into ready-to-review
ingest candidates, nationally, from any upstream the census cites.

Registered 2026-08-21. Why this exists: the TVA and national gap repairs
were done by hand -- reading coverage_gap_report.csv, joining each missing
county to the Moratorium Nation inventory, and writing master_opposition
rows one at a time. That process is general, so it is now a module. Any
future census-enacted county with no tracker record gets a fully populated
candidate row built automatically; promotion into master_opposition.csv
stays a deliberate human step, mirroring fetch_permits.py's
candidates-first design.

Inputs (all optional beyond the gap report; absent inputs degrade the
enrichment, never crash the run):
  data/coverage_gap_report.csv        which counties are missing
  data/external_restriction_census.csv  instrument / status / date / source
  Moratorium Nation inventory         legal basis, trigger, coordinates,
                                      exact dates (fetched from the upstream
                                      repo, or --inventory for a local copy)
  master_opposition.csv               dedup guard

Outputs:
  data/restriction_ingest_candidates.csv   full master_opposition schema,
                                           ready to append after review
  data/restriction_ingest_candidates.md    per-county disposition report

Hard-won rules encoded from the 2026-08 repairs:
  - Dedup guard (the Mercer ND lesson): when master_opposition already has
    ANY restrictive-typed row for the county, no candidate is emitted;
    the county is routed to the report's review-existing section instead,
    because the fix is usually correcting the existing row's coding, not
    adding a duplicate that the quarantine will catch anyway.
  - Single-token instrument types only. Compound types like
    "moratorium; regulatory_action" steered the QC mechanism classifier
    off "moratorium" entirely.
  - Summaries lead with classifier-recognizable instrument phrasing
    ("enacted a moratorium on", "adopted a ban on new data centers"),
    matching qc/enrichment.py's phrase lists, so the clean feed classifies
    the row the way the label rule will count it.
  - Month-precision dates when the upstream flags date uncertainty; dates
    are never invented.

No scorekeeping vocabulary is introduced beyond the raw schema's own
Community Outcome values, which every master row already carries.

Usage:
  python3 census_gap_candidates.py                 # network fetch upstream
  python3 census_gap_candidates.py --inventory /path/to/moratorium_inventory.csv
  python3 census_gap_candidates.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
GAP_CSV = os.path.join(DATA, "coverage_gap_report.csv")
CENSUS_CSV = os.path.join(DATA, "external_restriction_census.csv")
MASTER_CSV = os.path.join(HERE, "master_opposition.csv")
OUT_CSV = os.path.join(DATA, "restriction_ingest_candidates.csv")
OUT_MD = os.path.join(DATA, "restriction_ingest_candidates.md")

# Corrected 2026-08-21: the inventory lives under data/ in the upstream
# repo; the bare-root path 404s.
UPSTREAM_URL = ("https://raw.githubusercontent.com/mjbommar/"
                "moratorium-data-2026/main/data/moratorium_inventory.csv")

RESTRICTIVE_TOKENS = {"moratorium", "zoning_restriction", "ban"}

STATE_PAGE = {
    "AL": "alabama", "AR": "arkansas", "AZ": "arizona", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida",
    "GA": "georgia", "IA": "iowa", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "MA": "massachusetts", "MD": "maryland", "ME": "maine", "MI": "michigan",
    "MN": "minnesota", "MO": "missouri", "MS": "mississippi", "MT": "montana",
    "NC": "north-carolina", "ND": "north-dakota", "NE": "nebraska",
    "NH": "new-hampshire", "NJ": "new-jersey", "NM": "new-mexico",
    "NV": "nevada", "NY": "new-york", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode-island",
    "SC": "south-carolina", "SD": "south-dakota", "TN": "tennessee",
    "TX": "texas", "UT": "utah", "VA": "virginia", "VT": "vermont",
    "WA": "washington", "WI": "wisconsin", "WV": "west-virginia",
    "WY": "wyoming",
}
AUTHORITY = {"County": "county_commission", "City": "city_council",
             "Township": "township_board", "Town": "city_council"}
TRIGGER_CATEGORY = {
    "grid_energy": "grid_energy", "water": "water",
    "environmental": "environmental", "land_use_compatibility": "zoning",
    "regulatory_gap": "zoning", "noise": "noise",
    "infrastructure_capacity": "community_impact",
    "fire_safety": "community_impact",
}

FIELDS = ["Incident", "City", "Date", "Entity", "Location", "Opposition Type",
          "Severity", "Source URL", "State", "County", "Scope",
          "Issue Category", "Objective", "Authority Level", "Status",
          "Community Outcome", "Hyperscaler", "Company", "Project Name",
          "Investment Million USD", "Megawatts", "Acreage", "Sponsors",
          "Opposition Groups", "Summary", "Sources", "Opposition Website",
          "Opposition Facebook", "Opposition Instagram", "Petition URL",
          "Petition Signatures", "data_source", "lat", "lon"]


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def norm_county(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"\b(county|parish|borough|census area|city and borough)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


def load_inventory(local_path: str | None) -> list[dict]:
    if local_path:
        return read_csv(local_path)
    try:
        req = urllib.request.Request(
            UPSTREAM_URL, headers={"User-Agent": "hawthorn-gap-candidates/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
        return list(csv.DictReader(io.StringIO(text)))
    except Exception as e:
        print(f"note: upstream inventory unavailable ({e}); candidates will "
              f"carry census fields only")
        return []


def inventory_index(inv: list[dict]) -> dict:
    idx = {}
    for r in inv:
        key = (r.get("state_abbrev", "").strip().upper(),
               norm_county(r.get("jurisdiction", "")))
        if key[0] and key[1]:
            idx.setdefault(key, r)
    return idx


def master_restrictive_counties(master: list[dict]) -> set:
    """Counties that already carry a restrictive-typed row: the dedup guard."""
    out = set()
    for r in master:
        toks = {t.strip().lower()
                for t in str(r.get("Opposition Type") or "").split(";")}
        if toks & RESTRICTIVE_TOKENS:
            out.add((str(r.get("State") or "").strip().upper(),
                     norm_county(r.get("County") or "")))
    return out


def issue_categories(mn_row: dict) -> str:
    try:
        cats = json.loads(mn_row.get("trigger_categories") or "[]")
    except Exception:
        cats = []
    seen = []
    for c in cats:
        v = TRIGGER_CATEGORY.get(c)
        if v and v not in seen:
            seen.append(v)
    return "; ".join(seen[:3]) if seen else "zoning"


def instrument_phrase(instrument: str, county: str, state: str,
                      date: str) -> tuple[str, str]:
    """(Objective, summary lead) in wording the QC mechanism classifier
    recognizes. The phrase lists live in qc/enrichment.py; 'moratorium' and
    'ban on new data' are the anchors used here."""
    when = f", effective {date}" if date else ""
    if instrument == "ban":
        return (f"Adopt a ban on new data centers countywide in {county}",
                f"{county} ({state}) adopted a ban on new data centers "
                f"countywide{when}.")
    return (f"Enact a moratorium on data-center development in {county}",
            f"{county} ({state}) enacted a moratorium on data-center and "
            f"related development{when}.")


def build_candidate(gap: dict, census_row: dict | None,
                    mn_row: dict | None) -> dict:
    state = (gap.get("state") or "").strip().upper()
    county = (gap.get("county") or "").strip()
    instrument = ((census_row or {}).get("instrument")
                  or gap.get("census_instrument") or "moratorium").strip()
    if instrument not in ("moratorium", "ban"):
        instrument = "zoning_restriction"

    status = ((census_row or {}).get("census_status")
              or gap.get("census_status") or "active").strip().lower()
    if status not in ("active", "extended", "expired", "passed"):
        status = "active"

    date = ""
    if mn_row:
        date = (mn_row.get("date_enacted_iso") or "").strip()
        unc = (mn_row.get("date_enacted_uncertainty") or "").strip()
        if unc not in ("", "exact") and len(date) == 10:
            date = date[:7]
    if not date:
        raw = ((census_row or {}).get("date_enacted")
               or gap.get("census_date") or "").strip()
        m = re.match(r"^(\d{4}-\d{2}(-\d{2})?)", raw)
        date = m.group(1) if m else ""

    page = ""
    if state in STATE_PAGE:
        page = (f"https://mjbommar.github.io/moratorium-data-2026/states/"
                f"{STATE_PAGE[state]}.html")
    census_src = ((census_row or {}).get("source")
                  or gap.get("census_source") or "").strip()
    url_in_census = re.search(r"https?://\S+", census_src)
    if mn_row and page:
        source_url = page
    elif url_in_census:
        source_url = url_in_census.group(0).rstrip(").,;")
    else:
        source_url = page

    objective, lead = instrument_phrase(instrument, county, state, date)
    bits = [lead]
    if mn_row:
        lb = (mn_row.get("legal_basis") or "").strip()
        trg = (mn_row.get("trigger") or "").strip()
        if lb:
            bits.append(f"Instrument: {lb[:200]}.")
        if trg:
            bits.append(f"Stated basis: {trg[:180]}.")
        bits.append("Recorded from the Moratorium Nation inventory "
                    "(CC-BY-4.0); candidate generated by "
                    "census_gap_candidates.py to close a coverage-audit gap.")
        mid = (mn_row.get("moratorium_id") or "").strip()
        sources = (f"{source_url}; moratorium-nation:{mid} (CC-BY-4.0, "
                   f"github.com/mjbommar/moratorium-data-2026)")
        data_source = "moratorium_nation_ingest"
        lat = (mn_row.get("latitude") or "").strip()
        lon = (mn_row.get("longitude") or "").strip()
        auth = AUTHORITY.get((mn_row.get("jurisdiction_type") or "County"),
                             "county_commission")
        cats = issue_categories(mn_row)
    else:
        bits.append("Census-derived candidate generated by "
                    "census_gap_candidates.py; the external census records "
                    "this instrument with no matching tracker row. Verify "
                    "against the cited source before promoting.")
        sources = census_src or source_url
        data_source = "coverage_audit_worklist"
        lat = lon = ""
        auth = "county_commission"
        cats = "zoning"

    return {
        "Incident": county, "City": county, "Date": date, "Entity": "Unknown",
        "Location": f"{county}, {state}", "Opposition Type": instrument,
        "Severity": "1", "Source URL": source_url, "State": state,
        "County": county, "Scope": "local", "Issue Category": cats,
        "Objective": objective, "Authority Level": auth, "Status": status,
        "Community Outcome": "win", "Hyperscaler": "", "Company": "",
        "Project Name": "", "Investment Million USD": "", "Megawatts": "",
        "Acreage": "", "Sponsors": "", "Opposition Groups": "",
        "Summary": " ".join(bits), "Sources": sources,
        "Opposition Website": "", "Opposition Facebook": "",
        "Opposition Instagram": "", "Petition URL": "",
        "Petition Signatures": "", "data_source": data_source,
        "lat": lat, "lon": lon,
    }


def run(inventory_path: str | None) -> int:
    gaps = [r for r in read_csv(GAP_CSV)
            if (r.get("gap_class") or "").strip() == "missing"]
    census = read_csv(CENSUS_CSV)
    census_idx = {(r.get("state", "").strip().upper(),
                   norm_county(r.get("county", ""))): r for r in census}
    master = read_csv(MASTER_CSV)
    guard = master_restrictive_counties(master)
    mn_idx = inventory_index(load_inventory(inventory_path))

    candidates, review_existing, unsourced = [], [], []
    for gap in gaps:
        key = ((gap.get("state") or "").strip().upper(),
               norm_county(gap.get("county") or ""))
        if key in guard:
            review_existing.append(gap)
            continue
        cand = build_candidate(gap, census_idx.get(key), mn_idx.get(key))
        if not cand["Source URL"] and not cand["Sources"]:
            unsourced.append(gap)
            continue
        candidates.append(cand)

    os.makedirs(DATA, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\r\n")
        w.writeheader()
        w.writerows(candidates)

    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Restriction ingest candidates\n\n")
        fh.write(f"Missing counties in the gap report: {len(gaps)}\n\n")
        fh.write(f"- Candidates written: {len(candidates)} "
                 f"(review, then append to master_opposition.csv)\n")
        fh.write(f"- Routed to review-existing: {len(review_existing)} "
                 f"(a restrictive-typed row already exists; correct its "
                 f"coding instead of adding a duplicate)\n")
        fh.write(f"- Skipped for missing sources: {len(unsourced)}\n\n")
        if candidates:
            fh.write("## Candidates\n\n")
            for c in candidates:
                fh.write(f"- {c['State']} {c['County']}: "
                         f"{c['Opposition Type']}, {c['Status']}, "
                         f"date {c['Date'] or 'unstated'} "
                         f"({c['data_source']})\n")
        if review_existing:
            fh.write("\n## Review existing rows\n\n")
            for g in review_existing:
                fh.write(f"- {g.get('state')} {g.get('county')}: tracker "
                         f"already carries a restrictive-typed row that the "
                         f"clean feed or label rule is not counting\n")
    print(f"gap rows: {len(gaps)}; candidates: {len(candidates)}; "
          f"review-existing: {len(review_existing)}; "
          f"unsourced: {len(unsourced)}")
    print(f"wrote {OUT_CSV}")
    print(f"wrote {OUT_MD}")
    return 0


def selftest() -> int:
    fails = []

    def expect(cond, label):
        if not cond:
            fails.append(label)

    mn = {"state_abbrev": "KS", "jurisdiction": "Example County",
          "jurisdiction_type": "County", "date_enacted_iso": "2026-03-14",
          "date_enacted_uncertainty": "month",
          "legal_basis": "Resolution 2026-9 per official minutes",
          "trigger": "regulatory gap study",
          "trigger_categories": '["water", "regulatory_gap"]',
          "moratorium_id": "ks-example-county-2026",
          "latitude": "39.1", "longitude": "-95.5"}
    gap = {"state": "KS", "county": "Example County",
           "census_instrument": "moratorium", "census_status": "active",
           "census_date": "", "census_source": "", "gap_class": "missing"}
    cand = build_candidate(gap, None, mn)
    expect(cand["Date"] == "2026-03", "month uncertainty trims the date")
    expect(cand["Opposition Type"] == "moratorium", "single-token type")
    expect("enacted a moratorium on data-center" in cand["Summary"],
           "classifier phrase present for moratorium")
    expect("moratorium-nation:ks-example-county-2026" in cand["Sources"],
           "attribution carries the inventory id")
    expect(cand["Authority Level"] == "county_commission", "authority mapped")
    expect("water" in cand["Issue Category"], "trigger categories mapped")

    ban_gap = dict(gap, census_instrument="ban")
    ban = build_candidate(ban_gap, None, None)
    expect("ban on new data centers" in ban["Summary"],
           "classifier phrase present for ban")
    expect(ban["data_source"] == "coverage_audit_worklist",
           "census-only rows keep worklist provenance")

    master = [{"State": "ND", "County": "Mercer County",
               "Opposition Type": "moratorium"},
              {"State": "VA", "County": "Powhatan County",
               "Opposition Type": "public_comment"}]
    guard = master_restrictive_counties(master)
    expect(("ND", "mercer") in guard, "restrictive row raises the guard")
    expect(("VA", "powhatan") not in guard,
           "non-restrictive rows do not raise the guard")

    expect(norm_county("St. Louis (Independent City)") ==
           norm_county("St Louis Independent City"), "county norm is stable")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("census_gap_candidates selftest: 11 checks OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default=None,
                    help="local moratorium_inventory.csv instead of the "
                         "network fetch")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run(args.inventory)


if __name__ == "__main__":
    raise SystemExit(main())
