import csv
import json
import urllib.request

SOURCE_URL = "https://datacentertracker.org/data/fights.json"
OUTPUT_CSV = "master_opposition.csv"

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

def load_existing_header(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        return next(reader)

def build_row(record):
    sources = record.get("sources") or []
    jurisdiction = clean(record.get("jurisdiction"))
    state = clean(record.get("state"))

    return {
        "Incident": jurisdiction,
        "City": jurisdiction,
        "Date": clean(record.get("date")),
        "Entity": clean(record.get("company")) or "Unknown",
        "Location": f"{jurisdiction}, {state}" if jurisdiction and state else jurisdiction or state,
        "Opposition Type": join_list(record.get("action_type")),
        "Severity": 4,
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
        "Company": clean(record.get("company")),
        "Project Name": clean(record.get("project_name")),
        "Investment Million USD": clean(record.get("investment_million_usd")),
        "Megawatts": clean(record.get("megawatts")),
        "Acreage": clean(record.get("acreage")),
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
        "lat": clean(record.get("lat")),
        "lon": clean(record.get("lng")),
    }

def main():
    header = load_existing_header(OUTPUT_CSV)

    import requests
    response_obj = requests.get(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response_obj.raise_for_status()

    records = payload["data"] if isinstance(payload, dict) else payload

    records = payload
    rows = [build_row(record) for record in records]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

if __name__ == "__main__":
    main()
