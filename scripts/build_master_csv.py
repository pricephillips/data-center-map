"""
build_master_csv.py

Nightly ingest for master_opposition.csv.

Sources
-------
  datacentertracker.org fights feed   ->  rows written to master_opposition.csv
  GDELT 2.0 Doc API (signal_harvest)  ->  candidates written to a review queue

Change 2026-07-24: the Google News RSS step previously appended rows straight
into master_opposition.csv with Entity "Unknown", no state, no county, no
date, and no outcome. Those rows entered the source of truth unverified and
were indistinguishable from curated rows except by their blank fields (369 of
them are in the file today). That step is replaced by signal_harvest, which
covers more outlets and writes to data/signal_candidates.csv for review
instead of writing to the database.

Existing rows are never deleted or altered by this change. What changes is
that NEW unverified rows stop being appended.

The data_source column is also fixed here. build_row previously emitted the
key "datasource" while the CSV header carries "data_source", so the writer's
extrasaction="ignore" silently dropped every provenance value. Both keys are
now written, so provenance persists under either header spelling.
"""

import csv
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import signal_harvest
except ImportError:                      # module absent; CSV build must still run
    signal_harvest = None

SOURCE_URL = "https://datacentertracker.org/data/fights.json"
OUTPUT_CSV = "master_opposition.csv"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lookback for the candidate harvest. The job runs daily, so a 7-day window
# gives six days of overlap and makes a single missed run harmless.
HARVEST_DAYS = 7

def load_proposals(path="data/proposals.csv"):
    proposals = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row.get("state", "").strip().upper(), row.get("companies", "").strip().lower())
                proposals[key] = row
    except FileNotFoundError:
        pass
    return proposals

def score_severity(record):
    score = 1
    if record.get("petition_signatures") and int(record.get("petition_signatures") or 0) > 1000:
        score += 1
    if record.get("authority_level") in ("state", "federal"):
        score += 1
    if record.get("status") in ("ongoing", "escalated"):
        score += 1
    return min(score, 5)

def join_list(value):
    if not value:
        return ""
    if isinstance(value, list):
        return "; ".join(str(x) for x in value if x is not None)
    return str(value)

def clean(value):
    if value is None:
        return ""
    return value

def load_existing_rows(path):
    existing = {}
    header = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            for row in reader:
                key = (row.get("Incident", "").strip(), row.get("Entity", "").strip())
                existing[key] = row
    except FileNotFoundError:
        pass
    return existing, header

def build_row(record, proposals=None):
    sources = record.get("sources") or []
    jurisdiction = clean(record.get("jurisdiction"))
    state = clean(record.get("state"))
    company = clean(record.get("company"))

    proposal = (proposals or {}).get((state.upper(), company.lower()), {})

    return {
        "Incident": jurisdiction,
        "City": jurisdiction,
        "Date": clean(record.get("date")),
        "Entity": company or "Unknown",
        "Location": f"{jurisdiction}, {state}" if jurisdiction and state else jurisdiction or state,
        "Opposition Type": join_list(record.get("action_type")),
        "Severity": score_severity(record),
        "Source URL": sources[0] if sources else "",
        "State": state,
        "County": clean(record.get("county")),
        "Scope": clean(record.get("scope")),
        "Issue Category": join_list(record.get("issue_category")),
        "Objective": clean(record.get("objective")),
        "Authority Level": clean(record.get("authority_level")),
        "Status": clean(record.get("status")),
        "Community Outcome": clean(record.get("community_outcome")),
        "Hyperscaler": clean(record.get("hyperscaler")),
        "Company": company,
        "Project Name": clean(record.get("project_name")),
        "Investment Million USD": clean(record.get("investment_million_usd")),
        "Megawatts": clean(record.get("megawatts")) or proposal.get("capacity_mw", ""),
        "Acreage": clean(record.get("acreage")) or proposal.get("size_acres", ""),
        "Sponsors": join_list(record.get("sponsors")),
        "Opposition Groups": join_list(record.get("opposition_groups")),
        "Summary": clean(record.get("summary")),
        "Sources": join_list(sources),
        "Opposition Website": clean(record.get("opposition_website")),
        "Opposition Facebook": clean(record.get("opposition_facebook")),
        "Opposition Instagram": clean(record.get("opposition_instagram")),
        "Petition URL": clean(record.get("petition_url")),
        "Petition Signatures": clean(record.get("petition_signatures")),
        "datasource": "datacentertracker.org",
        "data_source": "datacentertracker.org",
        "lat": clean(record.get("lat")),
        "lon": clean(record.get("lng")),
    }

def main():
    existing_rows, header = load_existing_rows(OUTPUT_CSV)
    proposals = load_proposals()

    if not header:
         header = [
            "Incident", "City", "Date", "Entity", "Location", "Opposition Type", 
            "Severity", "Source URL", "State", "County", "Scope", "Issue Category", 
            "Objective", "Authority Level", "Status", "Community Outcome", 
            "Hyperscaler", "Company", "Project Name", "Investment Million USD", 
            "Megawatts", "Acreage", "Sponsors", "Opposition Groups", "Summary", 
            "Sources", "Opposition Website", "Opposition Facebook", 
            "Opposition Instagram", "Petition URL", "Petition Signatures", 
            "datasource", "lat", "lon"
        ]

    try:
        response_obj = requests.get(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response_obj.raise_for_status()
        payload = response_obj.json()
        records = payload["data"] if isinstance(payload, dict) else payload
    except requests.RequestException:
        records = []

    for record in records:
        row = build_row(record, proposals)
        key = (row.get("Incident", "").strip(), row.get("Entity", "").strip())
        existing_rows[key] = row
        
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows.values())

    # Candidate harvest runs AFTER the CSV is written, so it dedupes against
    # the rows just ingested. It writes only to the review queue.
    n = 0
    if signal_harvest is not None:
        n = signal_harvest.harvest_to_queue(days=HARVEST_DAYS, repo_root=REPO_ROOT)
    print(f"build_master_csv: {len(existing_rows)} rows written; "
          f"{n} harvest candidates queued for review")


if __name__ == "__main__":
    main()
