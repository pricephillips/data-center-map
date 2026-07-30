#!/usr/bin/env python3
"""
entity_split.py

One canonical splitter for the free-text multi-entity columns
(Opposition Groups, Company, Hyperscaler, Sponsors). Replaces three
divergent ad hoc implementations that each fabricated entities:

  group_registry.py   split on ";|", " and (?=[A-Z])", ",(?=\\s*[A-Z])"
                      Cut organization names in half. "Party for Socialism
                      and Liberation" became two entries; "Our Land, Our
                      Home, Our Harford" became three.

  outcome_model.py    split on "[;,/]"
  landmark_model.py   split on "[;,/]"
                      Split inside parentheses and across corporate
                      suffixes, so "Multiple (QTS, Switch, CyrusOne, and
                      others)" yielded the tokens "multiple (qts" and
                      "and others)". These tokens become model features.

Two modes:

  STRICT  Semicolon and pipe only. For columns where those are the
          documented delimiter and any other separator is part of a name.
          Opposition Groups is strict: audited against the full file, of
          the cells using a comma and no semicolon, none was a genuine
          multi-entity list.

  LOOSE   Adds comma and slash, but only at bracket depth zero, and never
          leaving a bare corporate suffix or a filler token standing alone.
          Company and Hyperscaler are loose, because "NextEra Energy /
          Clinton County Wind LLC" really is two firms.

Both modes are bracket-aware. Neither ever splits inside ( ) or [ ].

Usage
  from entity_split import split_entities, STRICT, LOOSE
  groups  = split_entities(row["Opposition Groups"], STRICT)
  firms   = split_entities(row["Company"], LOOSE)

  python entity_split.py --selftest
  python entity_split.py --scan master_opposition_clean.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys

STRICT = "strict"
LOOSE = "loose"

_HARD = ";|"
_SOFT = ",/"
_OPEN = "(["
_CLOSE = ")]"

# Fragments that are never an entity on their own. A comma-split that
# produces one of these is a split through a single name.
_SUFFIX = re.compile(
    r"^(inc|llc|l\.l\.c|lp|l\.p|llp|ltd|co|corp|corporation|plc|gmbh|nv|"
    r"n\.v|sa|s\.a|ag|pty|pte|bv|b\.v|holdings?|group|partners?|"
    r"and others|others|et al|etc|jr|sr|ii|iii|iv|"
    r"organizers?|petitioners?|chapter)\.?$", re.I)

# Tokens that describe rather than name. Dropped in both modes.
_GENERIC = {
    "", "n/a", "na", "none", "unknown", "various", "tbd", "undisclosed",
    "multiple", "multiple entities", "several", "other", "others",
}


def _depth_aware_split(text: str, seps: str) -> list[str]:
    """Splits on any character in seps that sits at bracket depth zero."""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        if ch in seps and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def _rejoin_suffixes(parts: list[str]) -> list[str]:
    """Reattaches a bare corporate suffix to the fragment before it, so
    "RCM Hill, LLC" stays one entity rather than becoming two."""
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and _SUFFIX.match(p):
            out[-1] = f"{out[-1]}, {p}"
        else:
            out.append(p)
    return out


def _tidy(part: str) -> str:
    p = part.strip().strip(" .;,|")
    # Repair a bracket left unbalanced by upstream data entry.
    if p.count("(") > p.count(")"):
        p += ")" * (p.count("(") - p.count(")"))
    elif p.count(")") > p.count("("):
        p = p.rstrip(")").strip()
    return p


def split_entities(cell, mode: str = STRICT) -> list[str]:
    """Returns the distinct entity strings in a free-text cell, in order."""
    if mode not in (STRICT, LOOSE):
        raise ValueError(f"mode must be {STRICT!r} or {LOOSE!r}, got {mode!r}")

    text = str(cell or "").strip()
    if not text:
        return []

    parts = _depth_aware_split(text, _HARD)
    if mode == LOOSE:
        expanded: list[str] = []
        for chunk in parts:
            expanded.extend(_rejoin_suffixes(
                _depth_aware_split(chunk, _SOFT)))
        parts = expanded

    seen, out = set(), []
    for p in parts:
        p = _tidy(p)
        if not p or p.lower() in _GENERIC:
            continue
        if p.lower() in seen:
            continue
        seen.add(p.lower())
        out.append(p)
    return out


# --------------------------------------------------------------------------

def scan(path: str) -> int:
    """Reports, per entity column, how the canonical splitter differs from
    the legacy regexes it replaces."""
    legacy_groups = re.compile(r"[;|]| and (?=[A-Z])|,(?=\s*[A-Z])")
    legacy_firms = re.compile(r"[;,/]")

    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    def toks(fn, col_fn):
        s = set()
        for r in rows:
            for t in fn(col_fn(r)):
                if t.strip():
                    s.add(t.strip().lower())
        return s

    g_new = toks(lambda c: split_entities(c, STRICT),
                 lambda r: r.get("Opposition Groups", ""))
    g_old = toks(lambda c: legacy_groups.split(str(c or "")),
                 lambda r: r.get("Opposition Groups", ""))
    f_new = toks(lambda c: split_entities(c, LOOSE),
                 lambda r: r.get("Company", "") + ";" + r.get("Hyperscaler", ""))
    f_old = toks(lambda c: legacy_firms.split(str(c or "")),
                 lambda r: r.get("Company", "") + ";" + r.get("Hyperscaler", ""))

    print(f"rows: {len(rows)}\n")
    for label, new, old in (("Opposition Groups", g_new, g_old),
                            ("Company + Hyperscaler", f_new, f_old)):
        print(f"{label}")
        print(f"  canonical tokens : {len(new)}")
        print(f"  legacy tokens    : {len(old)}")
        gone = sorted(old - new)
        print(f"  no longer emitted: {len(gone)}")
        for t in gone[:10]:
            print(f"      {t!r}")
        if len(gone) > 10:
            print(f"      ... {len(gone) - 10} more")
        print()
    return 0


# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def eq(label, got, want):
        checks.append((f"{label}: {got!r}", got == want))

    # strict mode keeps names containing soft separators intact
    eq("strict keeps ' and '",
       split_entities("Party for Socialism and Liberation", STRICT),
       ["Party for Socialism and Liberation"])
    eq("strict keeps commas",
       split_entities("Our Land, Our Home, Our Harford", STRICT),
       ["Our Land, Our Home, Our Harford"])
    eq("strict keeps ampersand run",
       split_entities("Stop Data Centers in Maumee, Monclova, Waterville "
                      "& Whitehouse", STRICT),
       ["Stop Data Centers in Maumee, Monclova, Waterville & Whitehouse"])
    eq("strict splits semicolons",
       split_entities("Sierra Club; Hoosier Environmental Council", STRICT),
       ["Sierra Club", "Hoosier Environmental Council"])
    eq("strict splits pipes",
       split_entities("A | B", STRICT), ["A", "B"])

    # loose mode splits real multi-firm cells
    eq("loose splits slash",
       split_entities("NextEra Energy / Clinton County Wind LLC", LOOSE),
       ["NextEra Energy", "Clinton County Wind LLC"])
    eq("loose splits comma list",
       split_entities("Meta, Google, QTS", LOOSE), ["Meta", "Google", "QTS"])

    # bracket awareness: the defect that reached model features
    eq("loose respects parentheses",
       split_entities("Multiple (QTS, Switch, CyrusOne, and others)", LOOSE),
       ["Multiple (QTS, Switch, CyrusOne, and others)"])
    eq("loose respects nested parens",
       split_entities("Meta (via Beignet LLC / Blue Owl Capital)", LOOSE),
       ["Meta (via Beignet LLC / Blue Owl Capital)"])
    eq("loose splits outside, keeps inside",
       split_entities("TA Realty (324 MW, Ellenwood) / Digital Realty", LOOSE),
       ["TA Realty (324 MW, Ellenwood)", "Digital Realty"])

    # corporate suffixes never stand alone
    eq("suffix rejoined LLC",
       split_entities("RCM Hill, LLC", LOOSE), ["RCM Hill, LLC"])
    eq("suffix rejoined Inc (trailing period normalized away)",
       split_entities("KRAMBU, Inc.", LOOSE), ["KRAMBU, Inc"])
    eq("suffix rejoined mid-list",
       split_entities("Aligned Data Centers, West Buckeye Rd LP, Meta", LOOSE),
       ["Aligned Data Centers", "West Buckeye Rd LP", "Meta"])

    # generic and empty handling
    eq("drops generic tokens",
       split_entities("Unknown; Google", LOOSE), ["Google"])
    eq("empty cell", split_entities("", STRICT), [])
    eq("none cell", split_entities(None, STRICT), [])
    eq("whitespace cell", split_entities("   ;  ; ", STRICT), [])

    # dedupe, order preserved, case-insensitive
    eq("dedupes case-insensitively",
       split_entities("Google; google; Meta", STRICT), ["Google", "Meta"])

    # unbalanced brackets repaired rather than propagated
    eq("repairs unbalanced open",
       split_entities("Meta (Beaver Dam", LOOSE), ["Meta (Beaver Dam)"])
    eq("strips orphan close",
       split_entities("represented by Vorys LLP)", LOOSE),
       ["represented by Vorys LLP"])

    # mode guard
    try:
        split_entities("x", "sloppy")
        checks.append(("rejects bad mode", False))
    except ValueError:
        checks.append(("rejects bad mode", True))

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scan", metavar="CSV",
                    help="compare canonical splitter against legacy regexes")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.scan:
        return scan(args.scan)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
