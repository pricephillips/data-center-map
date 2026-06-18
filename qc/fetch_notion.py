"""
fetch_notion.py  (optional)

Pulls the master opposition database from the Notion API and writes a flat
records.json that qc_pipeline.py can read. Each Notion property is flattened to
plain text under its property name, so a property called "Source URL" becomes
record["Source URL"], matching the field names the gate looks for.

Standard library only (urllib), so there is nothing to pip install.

Usage:
    export NOTION_TOKEN=secret_xxx
    export NOTION_DATABASE_ID=xxxxxxxx
    python fetch_notion.py --out records.json

Note: this is the one piece not exercised in the build sandbox. Confirm the
flattened property names line up with what the gate reads (Outcome, Status,
County, City, State, Date, Source URL, Latitude, Longitude, Notes, Opposition
Type, ID). Rename properties in Notion or adjust _flatten below if they differ.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request

API = "https://api.notion.com/v1/databases/{db}/query"
VERSION = "2022-06-28"


def _flatten_property(prop: dict) -> str:
    t = prop.get("type")
    val = prop.get(t)
    if val is None:
        return ""
    if t in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in val)
    if t == "select":
        return val.get("name", "") if isinstance(val, dict) else ""
    if t == "multi_select":
        return ", ".join(o.get("name", "") for o in val)
    if t == "status":
        return val.get("name", "") if isinstance(val, dict) else ""
    if t == "date":
        return (val or {}).get("start", "") if isinstance(val, dict) else ""
    if t in ("url", "email", "phone_number"):
        return val or ""
    if t == "number":
        return str(val)
    if t == "checkbox":
        return "true" if val else "false"
    if t == "formula":
        return _flatten_property({"type": val.get("type"), val.get("type"): val.get(val.get("type"))})
    if t == "people":
        return ", ".join(p.get("name", "") for p in val)
    return str(val)


def _flatten_page(page: dict) -> dict:
    record = {"ID": page.get("id", "")}
    for name, prop in page.get("properties", {}).items():
        record[name] = _flatten_property(prop)
    return record


def fetch(token: str, db: str) -> list[dict]:
    records, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            API.format(db=db), data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        records.extend(_flatten_page(p) for p in data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull the Notion opposition DB to records.json.")
    parser.add_argument("--out", default="records.json")
    args = parser.parse_args()

    token = os.environ.get("NOTION_TOKEN")
    db = os.environ.get("NOTION_DATABASE_ID")
    if not (token and db):
        raise SystemExit("Set NOTION_TOKEN and NOTION_DATABASE_ID environment variables.")

    recs = fetch(token, db)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(recs, fh, indent=2)
    print(f"Wrote {len(recs)} records to {args.out}")
