#!/usr/bin/env python3
"""
restriction_verification_worklist.py

Turns every weakly supported restrictive county label into a research task
with everything a reviewer needs on one row, and feeds completed research back
into the evidence ledger.

Why this module exists
----------------------
restriction_evidence.py grades each county's label by the independent sources
behind it, and the only source family that has ever run is the Moratorium
Nation census (secondary class). The primary_law and proceeding families are
registered and unimplemented, because every code-library and agenda host is
denied by the egress policy where these modules are written
(restriction_probe.py documents the 403s). restriction_probe.py --import was
built as the working path for results gathered elsewhere, and nothing ever
called it: data/restriction_probe_cache.json has never existed, so no county
could rise above what the census alone supports.

As of 2026-09-28 that left 338 of 460 positive counties uncorroborated:
  label_positive_no_support         189  tracker-sourced, no outside source
  label_positive_circular_support   149  census-promoted, census the only source

What it does
------------
  1. Builds data/restriction_verification_worklist.csv: one row per county
     carrying either conflict, with the label's own basis records (incident,
     date, status, URL), the census instrument and date, and the Moratorium
     Nation legal-basis citation when the upstream inventory is reachable
     (e.g. "Resolution 29-26 (creating Polk County Code Ch. 42-41)"), which is
     usually enough to find the adopted instrument on the county's own site.
  2. Preserves reviewer columns across rebuilds (merge on fips), so an
     auto-commit can never discard research.
  3. Writes every completed row to data/restriction_probe_import.csv in the
     exact format restriction_probe.py --import validates, which pipeline.yml
     now runs before restriction_evidence.py.

A completed row is one with evidence_family, result, evidence_url and
observed_at filled. evidence_family must be primary_law-class
(municipal_code, state_aggregator) or proceeding-class (meeting_portal): a
second news article or a second census is the same class as what the label
already rests on and raises nothing. That rule is enforced here, not left to
the reviewer.

Standing rules: stdlib only, additive, writes only its own files, LF line
endings, no em-dashes, no scorekeeping vocabulary, --selftest offline.

Usage
  python restriction_verification_worklist.py             build + export
  python restriction_verification_worklist.py --offline   skip the upstream fetch
  python restriction_verification_worklist.py --selftest
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
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(HERE, *parts)


EVIDENCE_CSV = P("data", "restriction_evidence.csv")
AGG_CSV = P("data", "county_aggregate.csv")
MASTER_CSV = P("master_opposition.csv")
CENSUS_CSV = P("data", "external_restriction_census.csv")
REGISTRY = P("configs", "restriction_evidence_sources.json")
OUT_CSV = P("data", "restriction_verification_worklist.csv")
IMPORT_CSV = P("data", "restriction_probe_import.csv")

UPSTREAM_INVENTORY = ("https://raw.githubusercontent.com/mjbommar/"
                      "moratorium-data-2026/main/data/moratorium_inventory.csv")

TARGET_CONFLICTS = {"label_positive_circular_support": 1,
                    "label_positive_no_support": 2}
# Only these classes add independent support to a label that already rests on
# press coverage or a compilation.
ACCEPTED_CLASSES = {"primary_law", "proceeding"}
RESULTS = ("hit", "clear", "pending_instrument", "unreachable", "not_covered")

RESTRICTIVE = {"moratorium", "zoning_restriction", "ban"}
ENACTED = {"passed", "approved", "enacted", "active", "extended", "expired",
           "moratorium passed"}

TASK_FIELDS = ["priority", "fips", "county_name", "state", "conflict",
               "label_provenance", "evidence_grade", "basis_records",
               "census_instrument", "census_date", "census_status",
               "mn_moratorium_id", "mn_legal_basis", "search_hint"]
REVIEW_FIELDS = ["evidence_family", "source_id", "result", "evidence_url",
                 "observed_at", "in_force_as_of", "detail"]
FIELDS = TASK_FIELDS + REVIEW_FIELDS
IMPORT_FIELDS = ["fips", "family", "source_id", "result", "observed_at", "url",
                 "detail", "in_force_as_of"]


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_csv(rows: list[dict], path: str, fields: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def norm_county(name: str) -> str:
    s = (name or "").split(",")[0].lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(county|parish|borough|city and borough|census area)\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def family_classes(path: str = REGISTRY) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            reg = json.load(fh)
        return {k: (v or {}).get("independence_class", "secondary")
                for k, v in reg.get("families", {}).items()}
    except (OSError, ValueError):
        return {"municipal_code": "primary_law", "meeting_portal": "proceeding",
                "state_aggregator": "primary_law",
                "restriction_census": "secondary"}


def load_inventory(offline: bool) -> dict:
    if offline:
        return {}
    try:
        req = urllib.request.Request(UPSTREAM_INVENTORY, headers={
            "User-Agent": "hawthorn-verification-worklist/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - absent upstream degrades, never fails
        print(f"note: upstream inventory unavailable ({e}); mn_ columns blank")
        return {}
    idx = {}
    for r in csv.DictReader(io.StringIO(text)):
        if (r.get("jurisdiction_type") or "").strip() != "County":
            continue
        key = ((r.get("state_abbrev") or "").strip().upper(),
               norm_county(r.get("jurisdiction", "")))
        if key[0] and key[1] and (r.get("enacted_status") or "") != "pending":
            idx.setdefault(key, r)
    return idx


def basis_records(master: list[dict]) -> dict:
    """(state, county) -> the enacted restrictive rows the label rests on."""
    out = defaultdict(list)
    for r in master:
        toks = {t.strip().lower() for t in (r.get("Opposition Type") or "").split(";")}
        if not (toks & RESTRICTIVE):
            continue
        if (r.get("Status") or "").strip().lower() not in ENACTED:
            continue
        key = ((r.get("State") or "").strip().upper(), norm_county(r.get("County", "")))
        if key[1]:
            out[key].append(r)
    return out


def clean(v: str) -> str:
    """Source text can carry em or en dashes; generated files never do."""
    return (v or "").replace("\u2014", "-").replace("\u2013", "-")


def build(evidence, agg, master, census, inventory, prior) -> list[dict]:
    agg_by = {(r.get("fips") or "").strip(): r for r in agg}
    basis = basis_records(master)
    cen = {}
    for r in census:
        cen.setdefault(((r.get("state") or "").strip().upper(),
                        norm_county(r.get("county", ""))), r)
    prior_by = {(r.get("fips") or "").strip(): r for r in prior}

    rows = []
    for e in evidence:
        conflict = (e.get("conflict") or "").strip()
        if conflict not in TARGET_CONFLICTS:
            continue
        fips = (e.get("fips") or "").strip()
        st = (e.get("state") or "").strip().upper()
        key = (st, norm_county(e.get("county_name", "")))
        recs = basis.get(key, [])
        recs.sort(key=lambda r: (r.get("Date") or ""), reverse=True)
        c = cen.get(key, {})
        mn = inventory.get(key, {})
        cname = (e.get("county_name") or "").split(",")[0].strip()
        legal = clean((mn.get("legal_basis") or "").strip())
        row = {
            "priority": TARGET_CONFLICTS[conflict],
            "fips": fips, "county_name": e.get("county_name", ""), "state": st,
            "conflict": conflict,
            "label_provenance": agg_by.get(fips, {}).get("label_provenance", ""),
            "evidence_grade": e.get("evidence_grade", ""),
            "basis_records": clean(" || ".join(
                f"{(r.get('Incident') or '')[:90]} | {r.get('Date','')} | "
                f"{r.get('Status','')} | {r.get('Source URL','')}" for r in recs[:3])),
            "census_instrument": c.get("instrument", ""),
            "census_date": c.get("date_enacted", ""),
            "census_status": c.get("census_status", ""),
            "mn_moratorium_id": mn.get("moratorium_id", ""),
            "mn_legal_basis": clean(legal[:240]),
            "search_hint": (f"\"{cname}\" {st} "
                            + (f"\"{legal[:60]}\"" if legal else "data center moratorium ordinance")
                            + " site:.gov OR minutes OR agenda"),
        }
        p = prior_by.get(fips, {})
        for f in REVIEW_FIELDS:
            row[f] = clean((p.get(f) or "").strip())
        rows.append(row)
    # Research already entered for a county that has since left the target set
    # (for example, because the import raised its grade) is carried forward so
    # it keeps flowing into the import file.
    have = {r["fips"] for r in rows}
    for fips, p in prior_by.items():
        if fips not in have and all((p.get(f) or "").strip()
                                   for f in ("evidence_family", "result",
                                             "evidence_url", "observed_at")):
            rows.append({f: p.get(f, "") for f in FIELDS})
    rows.sort(key=lambda r: (int(r["priority"] or 9), r["state"], r["county_name"]))
    return rows


def export(rows: list[dict], classes: dict) -> tuple[list[dict], list[str]]:
    out, problems = [], []
    for r in rows:
        fam = (r.get("evidence_family") or "").strip()
        filled = [f for f in ("evidence_family", "result", "evidence_url",
                              "observed_at") if (r.get(f) or "").strip()]
        if not filled:
            continue
        if len(filled) < 4:
            problems.append(f"{r['fips']}: incomplete review row ({', '.join(filled)} only)")
            continue
        if classes.get(fam) not in ACCEPTED_CLASSES:
            problems.append(f"{r['fips']}: family {fam!r} is not primary_law or "
                            f"proceeding class; it cannot add independent support")
            continue
        res = r["result"].strip()
        if res not in RESULTS:
            problems.append(f"{r['fips']}: result {res!r} not in {RESULTS}")
            continue
        out.append({"fips": r["fips"], "family": fam,
                    "source_id": (r.get("source_id") or "manual_review").strip(),
                    "result": res, "observed_at": r["observed_at"].strip()[:10],
                    "url": r["evidence_url"].strip(),
                    "detail": (r.get("detail") or "").strip()[:300],
                    "in_force_as_of": (r.get("in_force_as_of") or "").strip()})
    return out, problems


def selftest() -> int:
    fails = []

    def check(n, g, w):
        if g != w:
            fails.append(f"{n}: expected {w!r}, got {g!r}")

    evidence = [
        {"fips": "55095", "county_name": "Polk County, Wisconsin", "state": "WI",
         "conflict": "label_positive_circular_support", "evidence_grade": "D"},
        {"fips": "19169", "county_name": "Story County, Iowa", "state": "IA",
         "conflict": "label_positive_no_support", "evidence_grade": "U"},
        {"fips": "01001", "county_name": "Autauga County, Alabama", "state": "AL",
         "conflict": "", "evidence_grade": "B"},
    ]
    agg = [{"fips": "55095", "label_provenance": "census_only"},
           {"fips": "19169", "label_provenance": "tracker"}]
    master = [{"Incident": "Story County moratorium", "Date": "2026-02-02",
               "Status": "active", "Opposition Type": "moratorium", "State": "IA",
               "County": "Story County", "Source URL": "https://news.example/story"}]
    inv = {("WI", "polk"): {"moratorium_id": "wi-polk-county-2026",
                            "legal_basis": "Resolution 29-26"}}
    prior = [{"fips": "19169", "evidence_family": "meeting_portal",
              "source_id": "county_minutes", "result": "hit",
              "evidence_url": "https://storycountyiowa.gov/minutes.pdf",
              "observed_at": "2026-09-28", "detail": "adopted 2026-02-02"}]
    rows = build(evidence, agg, master, [], inv, prior)
    check("two targets", len(rows), 2)
    check("circular first", rows[0]["fips"], "55095")
    check("legal basis carried", rows[0]["mn_legal_basis"], "Resolution 29-26")
    check("basis record carried", "news.example/story" in rows[1]["basis_records"], True)
    check("review preserved", rows[1]["evidence_url"],
          "https://storycountyiowa.gov/minutes.pdf")
    classes = {"meeting_portal": "proceeding", "restriction_census": "secondary",
               "municipal_code": "primary_law"}
    out, probs = export(rows, classes)
    check("one export row", len(out), 1)
    check("export family", out[0]["family"], "meeting_portal")
    bad = [dict(rows[1], evidence_family="restriction_census")]
    out2, probs2 = export(bad, classes)
    check("secondary rejected", (len(out2), len(probs2)), (0, 1))
    part = [dict(rows[0], evidence_family="municipal_code")]
    out3, probs3 = export(part, classes)
    check("incomplete rejected", (len(out3), len(probs3)), (0, 1))
    # A county whose grade rose leaves the target set; its research persists.
    rows2 = build([evidence[2]], agg, master, [], inv, prior)
    check("completed research carried forward", [r["fips"] for r in rows2], ["19169"])
    if fails:
        print("SELFTEST FAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("restriction_verification_worklist.py selftest: OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    evidence = read_csv(EVIDENCE_CSV)
    if not evidence:
        print("no evidence ledger yet; nothing to build")
        return 0
    rows = build(evidence, read_csv(AGG_CSV), read_csv(MASTER_CSV),
                 read_csv(CENSUS_CSV), load_inventory(args.offline),
                 read_csv(OUT_CSV))
    write_csv(rows, OUT_CSV, FIELDS)
    out, problems = export(rows, family_classes())
    write_csv(out, IMPORT_CSV, IMPORT_FIELDS)
    by = defaultdict(int)
    for r in rows:
        by[r["conflict"] or "carried"] += 1
    print(f"wrote {os.path.relpath(OUT_CSV, HERE)}: {len(rows)} task(s) "
          f"{dict(by)}; {len(out)} completed row(s) exported to "
          f"{os.path.relpath(IMPORT_CSV, HERE)}")
    for p in problems[:20]:
        print("  held: " + p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
