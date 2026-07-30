#!/usr/bin/env python3
"""
group_registry_audit.py

Additive audit of group_registry.csv. Writes only new files; never mutates
group_registry.csv, master_opposition.csv, or any other source of truth.

Flags five defect classes in the canonical opposition-group registry:

  split_artifact       Canonical name is a fragment of a longer organization
                       name that the registry's delimiter regex cut in half
                       (the " and (?=[A-Z])" and ",(?=\\s*[A-Z])" rules).
                       Example: "Party for Socialism and Liberation" becomes
                       a canonical entry named "Party for Socialism".

  cross_state_merge    One canonical entry spans multiple states while its
                       variants carry different place tokens, meaning
                       distinct local organizations were merged.
                       Example: "Madison County residents" (AL),
                       "Marion County residents" (KY), "Mason County
                       residents" (SC) collapsed into one entry.

  suffix_merge         Two variants share a normalized key only because
                       norm_key() strips the organizational-form word
                       (coalition, group, alliance, association, ...).
                       Example: "Citizens Action Coalition" (IN) merged
                       with "WV Citizens Action Group" (WV).

  placeholder          Canonical name is a descriptive label, not a named
                       organization. Example: "Pike Township residents",
                       "Indianapolis environmental groups", "Fulton County".

  degenerate_rate      n_incidents below the interpretability floor, so
                       blocked_share is 0.0 or 1.0 by construction and must
                       not be surfaced in any client-facing artifact.

Outputs
  data/group_registry_audit.csv
  data/group_registry_audit_summary.json

Usage
  python group_registry_audit.py
  python group_registry_audit.py --selftest
  python group_registry_audit.py --state IN
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_CSV = os.path.join(HERE, "group_registry.csv")
MASTER_CLEAN = os.path.join(HERE, "master_opposition_clean.csv")
MASTER_RAW = os.path.join(HERE, "master_opposition.csv")
OUT_CSV = os.path.join(HERE, "data", "group_registry_audit.csv")
OUT_JSON = os.path.join(HERE, "data", "group_registry_audit_summary.json")

# Incidents below this floor make any per-group rate uninterpretable.
RATE_FLOOR = 5

# Mirrors the fuzzy-merge threshold in group_registry.build_registry().
FUZZY_THRESHOLD = 0.90

# Above this, two surface names are casing or punctuation restatements of
# each other rather than distinct organizations.
SAME_ORG_THRESHOLD = 0.95


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

# Mirrors group_registry.py so the audit reasons about the same keys.
_SPLIT_ALL = re.compile(r"[;|]| and (?=[A-Z])|,(?=\s*[A-Z])")
_SPLIT_SAFE = re.compile(r"[;|]")
_DROP_SUFFIX = re.compile(
    r"\b(inc|llc|coalition|committee|alliance|association|group|organization|"
    r"org|network|initiative|project|team|coa)\b\.?", re.I)
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")

# Descriptor tokens that mark a string as a description of people rather
# than a name. Matched CASE-SENSITIVELY and in lowercase only: a capitalized
# "Citizens" is part of a proper name ("Citizens Action Coalition"), a
# lowercase "citizens" is a description ("Hanover County citizens").
_PLACEHOLDER = re.compile(
    r"(^|\s)(residents?|neighbors?|citizens|locals?|landowners?|farmers|"
    r"homeowners|families|opponents|stakeholders|advocates|groups|"
    r"community members|ad hoc)(\s|$|\))")

# A bare place name with no organizational content. Kept short so that
# "Fulton County" trips it and "Save Hancock County" does not.
_BARE_PLACE = re.compile(
    r"^[A-Z][A-Za-z.\-']*( [A-Z][A-Za-z.\-']*)?"
    r" (County|Township|Parish|Borough|Town|City|Village)$")

# Leading tokens that make a string an organization name rather than a place.
_ACTION_LEAD = re.compile(
    r"^(save|protect|stop|no|keep|halt|defend|preserve|citizens|friends|"
    r"concerned|coalition|alliance|neighbors|residents|say)\b", re.I)
_GENERIC_EXACT = {
    "no data centers", "residents", "citizens", "community", "neighbors",
    "local residents", "local officials", "unknown", "various", "none",
    "n/a", "na", "community members", "local farmers", "multiple groups",
}

# Place-bearing tokens used to detect merges across distinct localities.
_PLACE_STOP = {
    "county", "township", "parish", "borough", "town", "city", "village",
    "residents", "resident", "citizens", "against", "data", "centers",
    "center", "for", "the", "of", "and", "no", "stop", "save", "protect",
    "concerned", "ad", "hoc", "area", "neighbors", "united", "alliance",
    "coalition", "group", "groups", "association", "committee", "network",
}


# --------------------------------------------------------------------------
# normalization helpers
# --------------------------------------------------------------------------

def norm_key(name: str) -> str:
    """Reproduces group_registry.norm_key()."""
    k = _PUNCT.sub(" ", str(name).lower())
    k = _DROP_SUFFIX.sub(" ", k)
    k = _WS.sub(" ", k).strip()
    return k


def norm_key_keep_form(name: str) -> str:
    """Same, but retains the organizational-form word."""
    k = _PUNCT.sub(" ", str(name).lower())
    k = _WS.sub(" ", k).strip()
    return k


def split_all(cell: str) -> list[str]:
    """Reproduces group_registry.split_groups() including the lossy rules."""
    parts = [p.strip(" .;,") for p in _SPLIT_ALL.split(str(cell or ""))]
    return [p for p in parts if p]


def split_safe(cell: str) -> list[str]:
    """Splits only on unambiguous delimiters."""
    parts = [p.strip(" .;,") for p in _SPLIT_SAFE.split(str(cell or ""))]
    return [p for p in parts if p]


def place_tokens(name: str) -> frozenset:
    toks = [t for t in norm_key_keep_form(name).split()
            if t not in _PLACE_STOP and not t.isdigit() and len(t) > 2]
    return frozenset(toks)


def is_placeholder(name: str) -> bool:
    n = str(name or "").strip()
    if not n:
        return True
    if n.lower() in _GENERIC_EXACT:
        return True
    if _BARE_PLACE.match(n) and not _ACTION_LEAD.match(n):
        return True
    return bool(_PLACEHOLDER.search(n))


# --------------------------------------------------------------------------
# defect detection
# --------------------------------------------------------------------------

def find_split_artifacts(records: list[dict]) -> dict:
    """Maps a fragment produced by the lossy split to the intact chunk."""
    out: dict[str, str] = {}
    for r in records:
        cell = r.get("Opposition Groups", "") or ""
        for chunk in split_safe(cell):
            frags = split_all(chunk)
            if len(frags) > 1:
                for f in frags:
                    out.setdefault(f, chunk)
    return out


def audit(registry: list[dict], records: list[dict]) -> list[dict]:
    frag_map = find_split_artifacts(records)

    # state of every surface variant, taken from the records themselves
    variant_states: dict[str, set] = defaultdict(set)
    for r in records:
        st = (r.get("State") or "").strip()
        for v in split_all(r.get("Opposition Groups", "") or ""):
            if st:
                variant_states[v].add(st)

    rows = []
    for e in registry:
        variants = [v.strip() for v in (e.get("variants") or "").split(";")
                    if v.strip()]
        name = e.get("canonical_name", "")
        try:
            n_inc = int(e.get("n_incidents") or 0)
        except ValueError:
            n_inc = 0
        try:
            n_states = int(e.get("n_states") or 0)
        except ValueError:
            n_states = 0

        flags = []
        detail = []

        # split_artifact
        hit = next((frag_map[v] for v in variants if v in frag_map), None)
        if hit:
            flags.append("split_artifact")
            detail.append(f"fragment of {hit!r}")

        # suffix_merge: a variant pair clears the fuzzy threshold only after
        # norm_key() strips the organizational-form word.
        pairs = [(a, b) for i, a in enumerate(variants)
                 for b in variants[i + 1:]]
        suffix_pair = next(
            ((a, b) for a, b in pairs
             if ratio(norm_key(a), norm_key(b)) >= FUZZY_THRESHOLD
             and ratio(norm_key_keep_form(a), norm_key_keep_form(b))
             < FUZZY_THRESHOLD), None)
        if suffix_pair:
            flags.append("suffix_merge")
            detail.append(f"{suffix_pair[0]!r} and {suffix_pair[1]!r} merge "
                          f"only once the form word is stripped")

        # cross_state_merge: entry spans states and holds variants that are
        # not merely casing or punctuation restatements of each other.
        if n_states > 1 and pairs:
            far = next(((a, b) for a, b in pairs
                        if ratio(norm_key_keep_form(a),
                                 norm_key_keep_form(b)) < SAME_ORG_THRESHOLD),
                       None)
            if far:
                flags.append("cross_state_merge")
                detail.append(f"{far[0]!r} and {far[1]!r} merged across "
                              f"{e.get('states', '')}")

        # placeholder
        if is_placeholder(name):
            flags.append("placeholder")
            detail.append("descriptive label, not a named organization")

        # degenerate_rate
        if n_inc < RATE_FLOOR and (e.get("blocked_share") or "") != "":
            flags.append("degenerate_rate")
            detail.append(f"n_incidents={n_inc} below floor {RATE_FLOOR}")

        rows.append({
            "canonical_id": e.get("canonical_id", ""),
            "canonical_name": name,
            "states": e.get("states", ""),
            "n_variants": e.get("n_variants", ""),
            "n_incidents": n_inc,
            "variants": "; ".join(variants),
            "flags": "; ".join(flags),
            "n_flags": len(flags),
            "detail": "; ".join(detail),
            "client_safe": "0" if flags else "1",
        })
    return rows


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_rows(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ["canonical_id", "canonical_name", "states", "n_variants",
              "n_incidents", "variants", "flags", "n_flags", "detail",
              "client_safe"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def summarize(rows: list[dict]) -> dict:
    fc = Counter()
    for r in rows:
        for f in r["flags"].split("; "):
            if f:
                fc[f] += 1
    return {
        "n_canonical": len(rows),
        "n_flagged": sum(1 for r in rows if r["n_flags"]),
        "n_clean": sum(1 for r in rows if not r["n_flags"]),
        "by_flag": dict(sorted(fc.items(), key=lambda kv: -kv[1])),
        "rate_floor": RATE_FLOOR,
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    registry = [
        {"canonical_id": "G0001", "canonical_name": "Party for Socialism",
         "n_variants": "1", "variants": "Party for Socialism",
         "n_incidents": "9", "n_states": "1", "states": "WI",
         "blocked_share": "0.5"},
        {"canonical_id": "G0002", "canonical_name": "Citizens Action Coalition",
         "n_variants": "2",
         "variants": "Citizens Action Coalition; WV Citizens Action Group",
         "n_incidents": "17", "n_states": "2", "states": "IN; WV",
         "blocked_share": "0.571"},
        {"canonical_id": "G0003", "canonical_name": "Pike Township residents",
         "n_variants": "1", "variants": "Pike Township residents",
         "n_incidents": "1", "n_states": "1", "states": "IN",
         "blocked_share": "1.0"},
        {"canonical_id": "G0004", "canonical_name": "Fulton County",
         "n_variants": "1", "variants": "Fulton County",
         "n_incidents": "1", "n_states": "1", "states": "IN",
         "blocked_share": "1.0"},
        {"canonical_id": "G0005",
         "canonical_name": "Southern Environmental Law Center",
         "n_variants": "1", "variants": "Southern Environmental Law Center",
         "n_incidents": "31", "n_states": "1", "states": "VA",
         "blocked_share": "0.4"},
    ]
    records = [
        {"State": "WI",
         "Opposition Groups": "Party for Socialism and Liberation (PSL)"},
        {"State": "IN", "Opposition Groups": "Citizens Action Coalition"},
        {"State": "WV", "Opposition Groups": "WV Citizens Action Group"},
        {"State": "IN", "Opposition Groups": "Pike Township residents"},
        {"State": "IN", "Opposition Groups": "Fulton County"},
        {"State": "VA",
         "Opposition Groups": "Southern Environmental Law Center"},
    ]

    rows = audit(registry, records)
    by_id = {r["canonical_id"]: r for r in rows}
    checks = []

    def want(cid, flag, present=True):
        got = flag in by_id[cid]["flags"]
        checks.append((f"{cid} {'has' if present else 'lacks'} {flag}",
                       got is present))

    want("G0001", "split_artifact")
    want("G0002", "suffix_merge")
    want("G0002", "cross_state_merge")
    want("G0003", "placeholder")
    want("G0003", "degenerate_rate")
    want("G0004", "placeholder")
    want("G0005", "placeholder", present=False)
    want("G0005", "degenerate_rate", present=False)
    want("G0005", "split_artifact", present=False)

    checks.append(("G0005 client_safe", by_id["G0005"]["client_safe"] == "1"))
    checks.append(("G0002 client_safe", by_id["G0002"]["client_safe"] == "0"))

    # pure-function checks
    checks.append(("norm_key drops form word",
                   norm_key("Citizens Action Coalition") == "citizens action"))
    checks.append(("norm_key_keep_form retains it",
                   norm_key_keep_form("Citizens Action Coalition")
                   == "citizens action coalition"))
    checks.append(("split_all is lossy",
                   len(split_all("Basin and Range Watch")) == 2))
    checks.append(("split_safe is not",
                   len(split_safe("Basin and Range Watch")) == 1))
    checks.append(("bare place is placeholder",
                   is_placeholder("Marshall County") is True))
    checks.append(("real org is not placeholder",
                   is_placeholder("Hoosier Environmental Council") is False))
    checks.append(("action-led place name is not placeholder",
                   is_placeholder("Save Hancock County") is False))
    checks.append(("capitalized Citizens is not placeholder",
                   is_placeholder("Citizens Action Coalition") is False))
    checks.append(("lowercase residents is placeholder",
                   is_placeholder("Martindale-Brightwood residents") is True))
    checks.append(("lowercase groups is placeholder",
                   is_placeholder("Indianapolis environmental groups") is True))

    s = summarize(rows)
    checks.append(("summary counts", s["n_canonical"] == 5
                   and s["n_flagged"] == 4 and s["n_clean"] == 1))

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--state", default=None,
                    help="restrict console summary to one state code")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    registry = read_csv(REGISTRY_CSV)
    master = MASTER_CLEAN if os.path.exists(MASTER_CLEAN) else MASTER_RAW
    records = read_csv(master)

    rows = audit(registry, records)
    write_rows(rows, OUT_CSV)
    summary = summarize(rows)

    if args.state:
        sel = [r for r in rows if args.state in r["states"].split("; ")]
        summary["state_filter"] = args.state
        summary["state_n_canonical"] = len(sel)
        summary["state_n_flagged"] = sum(1 for r in sel if r["n_flags"])

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(f"source: {os.path.basename(master)} ({len(records)} records)")
    print(f"canonical groups: {summary['n_canonical']}")
    print(f"flagged: {summary['n_flagged']}  clean: {summary['n_clean']}")
    for k, v in summary["by_flag"].items():
        print(f"  {k}: {v}")
    if args.state:
        print(f"{args.state}: {summary['state_n_flagged']} flagged of "
              f"{summary['state_n_canonical']}")
    print(f"wrote {OUT_CSV}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
