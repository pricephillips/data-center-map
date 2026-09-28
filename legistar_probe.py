#!/usr/bin/env python3
"""
legistar_probe.py

Finds adopted data center restrictions in county Legistar systems and records
them as official-record evidence in the county verification worklist.

Why this module exists
----------------------
data/restriction_verification_worklist.csv holds one research task per county
whose restrictive label no official record supports (308 open on 2026-09-28).
Many county boards publish legislation through Legistar, whose public web API
(webapi.legistar.com/v1/<client>/matters) returns each matter's title, status
and passed date as JSON. The 2026-09-28 research found Lake IL, Jackson MO and
Charlotte (Mecklenburg NC) this way. This module does that lookup for every
open county on every build, with no one searching by hand.

What it does
------------
For each worklist row with an empty result:
  1. Client names to try: configs/legistar_clients.json (fips -> [client],
     verified by a person), else names built from the county: <name>county<st>,
     <name>co<st>, <name><st>, plus <name>county and <name>co when that county
     name is unique nationally. A client without a state suffix is never
     guessed for a name shared across states ("Lake County" exists in 12), so
     a lookup cannot land in the wrong state.
  2. Client existence is checked once (GET /bodies) and remembered in
     data/legistar_discovery.json: found clients for good, misses for 60 days.
  3. Matters whose title mentions a data center are fetched. A matter counts
     when its title names a moratorium, ban, prohibition or pause (not a
     repeal, rescission or lifting) and it has a passed date or an
     adopted/passed/approved/enacted status. The most recent one is used.
  4. The row is filled: evidence_family meeting_portal, source_id legistar,
     result hit, evidence_url the matter's public Legistar page, observed_at
     today, in_force_as_of the passed date, detail the file number and title.

Only hits are written. Finding nothing in Legistar proves nothing about the
county, so no row is marked clear. restriction_verification_worklist.py then
exports completed rows to data/restriction_probe_import.csv as usual.

Standing rules: stdlib only, additive, writes only the worklist's reviewer
columns and its own discovery file, LF line endings, no em-dashes, --selftest
offline.

Usage
  python legistar_probe.py                  probe open worklist rows (network)
  python legistar_probe.py --max-requests 100
  python legistar_probe.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(HERE, *parts)


WORKLIST_CSV = P("data", "restriction_verification_worklist.csv")
AGG_CSV = P("data", "county_aggregate.csv")
CLIENTS_JSON = P("configs", "legistar_clients.json")
DISCOVERY_JSON = P("data", "legistar_discovery.json")

API = "https://webapi.legistar.com/v1/{client}"
WEB = "https://{client}.legistar.com/LegislationDetail.aspx?ID={id}&GUID={guid}"
MISS_RECHECK_DAYS = 60
DEFAULT_MAX_REQUESTS = 300
REQUEST_DELAY_S = 0.3
MAX_CONSECUTIVE_ERRORS = 5

INSTRUMENT = re.compile(
    r"\b(moratori(um|a)|prohibit\w*|ban|bans|banning|pause|"
    r"temporary (suspension|halt|stay))\b", re.I)
UNDO = re.compile(r"\b(repeal\w*|rescind\w*|rescission|lift\w*|terminat\w*|"
                  r"end(s|ing)? the moratorium)\b", re.I)
ADOPTED_STATUS = re.compile(r"\b(adopt\w*|pass\w*|approv\w*|enact\w*|final action)\b",
                            re.I)
SUFFIX = re.compile(r"\s+(county|parish|borough|city and borough|census area|"
                    r"municipality)$", re.I)


# ---------------------------------------------------------------------------
# client names
# ---------------------------------------------------------------------------

def base_name(county_name: str) -> str:
    """'St. Mary's County, Maryland' -> 'stmarys'."""
    name = county_name.split(",")[0].strip()
    name = SUFFIX.sub("", name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def unique_names(agg_rows: list[dict]) -> set:
    c = Counter(base_name(r.get("county_name", "")) for r in agg_rows)
    return {n for n, k in c.items() if k == 1}


def candidate_clients(fips: str, county_name: str, state: str,
                      configured: dict, unique: set) -> list[str]:
    if configured.get(fips):
        return list(configured[fips])
    b, st = base_name(county_name), (state or "").strip().lower()
    if not b or not st:
        return []
    out = [f"{b}county{st}", f"{b}co{st}", f"{b}{st}"]
    if b in unique:
        out += [f"{b}county", f"{b}co"]
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_json(url: str):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "data-center-map legistar_probe"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def client_exists(client: str, get=http_json) -> bool | None:
    """True/False when Legistar answers, None when the network failed."""
    try:
        data = get(API.format(client=client) + "/bodies?$top=1")
        return isinstance(data, list)
    except urllib.error.HTTPError:
        return False           # unknown clients answer with an HTTP error
    except ValueError:
        return False
    except OSError:
        return None


def data_center_matters(client: str, get=http_json) -> list[dict]:
    flt = urllib.parse.quote("substringof('data cent',MatterTitle)")
    return get(API.format(client=client) + f"/matters?$filter={flt}&$top=500") or []


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

def adopted_restriction(matters: list[dict]) -> dict | None:
    best, best_date = None, ""
    for m in matters:
        title = f"{m.get('MatterTitle') or ''} {m.get('MatterName') or ''}"
        if not INSTRUMENT.search(title) or UNDO.search(title):
            continue
        passed = (m.get("MatterPassedDate") or "")[:10]
        if not passed and not ADOPTED_STATUS.search(m.get("MatterStatusName") or ""):
            continue
        key = passed or (m.get("MatterIntroDate") or "")[:10]
        if best is None or key > best_date:
            best, best_date = m, key
    return best


def evidence_row(client: str, m: dict, today: dt.date) -> dict:
    passed = (m.get("MatterPassedDate") or "")[:10]
    title = " ".join((m.get("MatterTitle") or m.get("MatterName") or "").split())
    detail = (f"Legistar {client}: file {m.get('MatterFile') or m.get('MatterId')}, "
              f"{m.get('MatterTypeName') or 'matter'}, "
              f"{m.get('MatterStatusName') or 'status n/a'}"
              f"{', passed ' + passed if passed else ''}: {title}")
    return {
        "evidence_family": "meeting_portal",
        "source_id": "legistar",
        "result": "hit",
        "evidence_url": WEB.format(client=client, id=m.get("MatterId", ""),
                                   guid=m.get("MatterGuid", "")),
        "observed_at": today.isoformat(),
        "in_force_as_of": passed,
        "detail": ("auto (legistar_probe) " + detail).replace("\u2014", "-")[:400],
    }


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

class Budget:
    def __init__(self, n: int):
        self.left, self.errors = n, 0

    def ok(self) -> bool:
        return self.left > 0 and self.errors < MAX_CONSECUTIVE_ERRORS


def run(worklist: list[dict], configured: dict, unique: set, discovery: dict,
        get, today: dt.date, max_requests: int, delay: float = 0.0) -> dict:
    stats = {"open": 0, "probed": 0, "clients_found": 0, "hits": 0, "errors": 0}
    budget = Budget(max_requests)

    def call(fn, *a):
        budget.left -= 1
        out = fn(*a, get=get)
        if delay:
            time.sleep(delay)
        return out

    for row in worklist:
        if (row.get("result") or "").strip():
            continue
        stats["open"] += 1
        if not budget.ok():
            continue
        clients = candidate_clients(row.get("fips", ""), row.get("county_name", ""),
                                    row.get("state", ""), configured, unique)
        stats["probed"] += 1 if clients else 0
        for client in clients:
            if not budget.ok():
                break
            known = discovery.get(client)
            if known and not known["ok"] and \
                    (today - dt.date.fromisoformat(known["checked"])).days < MISS_RECHECK_DAYS:
                continue
            if not (known and known["ok"]):
                exists = call(client_exists, client)
                if exists is None:
                    budget.errors += 1
                    stats["errors"] += 1
                    continue
                budget.errors = 0
                discovery[client] = {"ok": exists, "checked": today.isoformat()}
                if not exists:
                    continue
                stats["clients_found"] += 1
            try:
                matters = call(data_center_matters, client)
                budget.errors = 0
            except (OSError, ValueError) as e:
                budget.errors += 1
                stats["errors"] += 1
                print(f"  ! {client}: {e.__class__.__name__}: {e}", file=sys.stderr)
                continue
            m = adopted_restriction(matters)
            if m:
                row.update(evidence_row(client, m, today))
                stats["hits"] += 1
                break
    return stats


def read_csv(path: str) -> tuple[list[dict], list[str]]:
    if not os.path.exists(path):
        return [], []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        return list(rd), list(rd.fieldnames or [])


def load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_json(obj: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
        fh.write("\n")


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    check("base name", base_name("St. Mary's County, Maryland"), "stmarys")
    check("base parish", base_name("Caddo Parish, Louisiana"), "caddo")
    agg = [{"county_name": "Lake County, Illinois"}, {"county_name": "Lake County, Ohio"},
           {"county_name": "Boone County, Iowa"}, {"county_name": "Archuleta County, Colorado"}]
    uniq = unique_names(agg)
    check("unique", sorted(uniq), ["archuleta", "boone"])
    check("shared name gets state suffix only",
          candidate_clients("39085", "Lake County, Ohio", "OH", {}, uniq),
          ["lakecountyoh", "lakecooh", "lakeoh"])
    check("unique name also bare",
          candidate_clients("08007", "Archuleta County, Colorado", "CO", {}, uniq)[-2:],
          ["archuletacounty", "archuletaco"])
    check("configured wins",
          candidate_clients("17097", "Lake County, Illinois", "IL",
                            {"17097": ["lakecounty"]}, uniq), ["lakecounty"])

    matters = [
        {"MatterId": 1, "MatterGuid": "g1", "MatterFile": "26-1", "MatterTitle":
         "Ordinance imposing a temporary moratorium on data centers",
         "MatterStatusName": "Adopted", "MatterPassedDate": "2026-06-22T00:00:00",
         "MatterTypeName": "Ordinance"},
        {"MatterId": 2, "MatterGuid": "g2", "MatterFile": "26-2", "MatterTitle":
         "Ordinance repealing the data center moratorium",
         "MatterStatusName": "Adopted", "MatterPassedDate": "2026-09-01T00:00:00"},
        {"MatterId": 3, "MatterGuid": "g3", "MatterFile": "26-3", "MatterTitle":
         "Data center moratorium ordinance", "MatterStatusName": "Introduced",
         "MatterPassedDate": None},
        {"MatterId": 4, "MatterGuid": "g4", "MatterFile": "26-4", "MatterTitle":
         "Data center zoning text amendment workshop", "MatterStatusName": "Adopted",
         "MatterPassedDate": "2026-08-01T00:00:00"},
    ]
    m = adopted_restriction(matters)
    check("adopted picked", m and m["MatterId"], 1)
    check("nothing adopted", adopted_restriction(matters[1:]), None)

    wl = [
        {"fips": "29095", "county_name": "Jackson County, Missouri", "state": "MO",
         "result": ""},
        {"fips": "08007", "county_name": "Archuleta County, Colorado", "state": "CO",
         "result": "hit", "evidence_url": "https://x"},
        {"fips": "39085", "county_name": "Lake County, Ohio", "state": "OH", "result": ""},
    ]

    def fake(url):
        if "/jacksonco/bodies" in url:
            return [{"BodyId": 1}]
        if "/jacksonco/matters" in url:
            return matters
        raise urllib.error.HTTPError(url, 500, "unknown client", {}, None)

    disc = {}
    st = run(wl, {"29095": ["jacksonco"]}, uniq, disc, fake, dt.date(2026, 9, 28), 50)
    check("hit written", wl[0]["result"], "hit")
    check("family", wl[0]["evidence_family"], "meeting_portal")
    check("web url", wl[0]["evidence_url"],
          "https://jacksonco.legistar.com/LegislationDetail.aspx?ID=1&GUID=g1")
    check("in force", wl[0]["in_force_as_of"], "2026-06-22")
    check("completed row untouched", wl[1]["evidence_url"], "https://x")
    check("miss stays open", wl[2]["result"], "")
    check("misses remembered", disc.get("lakecountyoh", {}).get("ok"), False)
    check("hits", st["hits"], 1)
    check("no em dash", "\u2014" in wl[0]["detail"], False)

    # A remembered miss is not re-probed inside the recheck window.
    calls = []
    run([{"fips": "39085", "county_name": "Lake County, Ohio", "state": "OH",
          "result": ""}], {}, uniq, disc, lambda u: calls.append(u) or [],
        dt.date(2026, 10, 1), 50)
    check("miss not reprobed", calls, [])

    # Network failure stops early and caches nothing.
    def down(url):
        raise urllib.error.URLError("403")
    disc2 = {}
    st2 = run([dict(wl[2], result="")] * 4, {}, uniq, disc2, down,
              dt.date(2026, 9, 28), 50)
    check("network error not cached", disc2, {})
    check("stops after errors", st2["errors"], MAX_CONSECUTIVE_ERRORS)

    if failures:
        print("legistar_probe.py selftest: FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("legistar_probe.py selftest: OK")
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    worklist, fields = read_csv(WORKLIST_CSV)
    if not worklist:
        print("verification worklist absent or empty; nothing to do")
        return 0
    agg, _ = read_csv(AGG_CSV)
    configured = {k: v for k, v in load_json(CLIENTS_JSON).items()
                  if not k.startswith("_")}
    discovery = load_json(DISCOVERY_JSON)
    stats = run(worklist, configured, unique_names(agg), discovery, http_json,
                dt.date.today(), args.max_requests, REQUEST_DELAY_S)
    save_json(discovery, DISCOVERY_JSON)
    if stats["hits"]:
        with open(WORKLIST_CSV, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(worklist)
    print(f"legistar probe: {stats['open']} open task(s), {stats['probed']} probed, "
          f"{stats['clients_found']} new Legistar client(s) found, "
          f"{stats['hits']} adopted restriction(s) recorded, {stats['errors']} "
          f"network error(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
