#!/usr/bin/env python3
"""
project_key_map.py

A permanent in-repo key for every Layer B project, and the sync that keeps the
hand-maintained Layer B files pointing at their projects when the source
renumbers.

Why this exists
---------------
project_id is "prj_" + TrackDataCenters' own id, and the source renumbered its
whole id space on 2026-09-17 and 2026-09-22 (and partly on 05-01, 05-21 and
07-02). Every hand-maintained row keyed on that id silently followed the
number instead of the project: 190 rows were attached to the wrong project
before project_id_rekey.py repaired them. That repair is reactive and needs a
person. This module makes the key ours:

  * each project gets a permanent key, pk_NNNNN, minted once and never reused;
  * every nightly scrape is matched to those keys on identity, not id;
  * every hand-file row is anchored to the key it was written about, and when
    that key's source id moves, only the id cell is rewritten.

Identity rules (in order; the first rule that decides, decides)
  1. slug          the source's own slug, when both sides carry one
  2. coordinates   identical coordinates, states agreeing or one blank. The source renames
                   freely ("Colossus" -> "xAI Colossus 1") but keeps
                   coordinates, and no two current projects share a pair.
  3. name + state  normalized name, unique on both sides
  4. same id       same id and state, and either a similar name (token
                   Jaccard >= 0.5) or coordinates within 1.5 km ("Cayuga Data
                   Campus" became "Lake Hawkeye" 0.6 km away on 2026-09-25)
  Slug and coordinates are both strong signals: if they point at different
  keys the row is ambiguous and is held.

Automation boundary (Ruling 5, 2026-09-28)
  * Rewriting the id cell of a row whose project moved is automatic.
  * Ambiguous matches, orphaned rows (their project left the source) and
    unknown ids are HELD for a person and never guessed.
  * A scrape in which more than 15% of known projects vanish is treated as
    degraded: nothing is written.
  * A row whose id a person changed by hand is re-anchored to the project that
    id named at the time, and never reverted.
  * Only the id cells change. Every other byte of a hand file is preserved,
    including quoting and line endings.

Manual additions (data/proposals_added.csv, ids 1000 and up) are keyed on
their in-repo id, which never moves.

Files
  data/project_key_map.csv       one row per key: current id, identity, status
  data/project_key_aliases.csv   every id, slug and name a key has carried
  data/layer_b_anchors.csv       which key each hand-file row is about
  data/project_key_map_report.md what the last run did and what it held

Usage
  python project_key_map.py --init       once, on repaired rows
  python project_key_map.py              sync after a scrape (no-op before --init)
  python project_key_map.py --dry-run    sync, report only, write nothing
  python project_key_map.py --selftest

Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

PROPOSALS = "proposals.csv"
ADDED = "proposals_added.csv"
MAP = "project_key_map.csv"
ALIASES = "project_key_aliases.csv"
ANCHORS = "layer_b_anchors.csv"
REPORT = "project_key_map_report.md"

# (file, id columns, format). "prj" cells read prj_<id>; "bare" cells read <id>.
HAND_FILES = [
    ("proposals_manual_overlay.csv", ("id",), "bare"),
    ("project_decision_dates.csv", ("project_id",), "prj"),
    ("project_links_manual.csv", ("project_id",), "prj"),
    ("project_duplicates.csv", ("keep_id", "drop_id"), "prj"),
    ("negative_audit_codings.csv", ("universe_id",), "prj"),
]

MAP_FIELDS = ["pk", "source", "current_id", "name", "state", "lat", "lon",
              "slug", "status", "first_seen", "last_seen", "last_id_change"]
ALIAS_FIELDS = ["pk", "kind", "value", "first_seen", "last_seen"]
ANCHOR_FIELDS = ["file", "row_sig", "occurrence", "column", "pk", "written_id",
                 "anchored_on", "basis"]

DEGRADED_SHARE = 0.15
NAME_JACCARD = 0.5
SAME_ID_KM = 1.5            # same id, same state, moved less than this: same project
_GENERIC = {"data", "center", "centre", "campus", "project", "the", "of", "and",
            "technology", "tech", "park", "digital", "dc", "ai", "site", "llc",
            "inc"}


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def norm_name(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def norm_state(s) -> str:
    return str(s or "").strip().lower()


def coord_key(row) -> str:
    try:
        lat, lon = float(row.get("lat")), float(row.get("lon"))
    except (TypeError, ValueError):
        return ""
    if lat == 0 and lon == 0:
        return ""
    return f"{lat:.5f},{lon:.5f}"


def _tokens(s):
    return frozenset(t for t in norm_name(s).split() if t not in _GENERIC)


def jaccard(a, b) -> float:
    a, b = _tokens(a), _tokens(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def km_between(a, b):
    try:
        la1, lo1 = math.radians(float(a.get("lat"))), math.radians(float(a.get("lon")))
        la2, lo2 = math.radians(float(b.get("lat"))), math.radians(float(b.get("lon")))
    except (TypeError, ValueError):
        return None
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def match(keys: list[dict], rows: list[dict]) -> tuple[dict, list[dict]]:
    """Match scraped rows to known keys on identity.

    keys: map rows (pk, current_id, name, state, lat, lon, slug).
    rows: scraped rows (id, name, state, lat, lon, optional slug).
    Returns ({row id: pk}, held) where held lists rows that matched more than
    one key, or whose key another row also claimed. A row absent from both is
    new.
    """
    by_slug, by_coord, by_name, by_id = {}, {}, {}, {}
    state_of = {k["pk"]: k.get("state", "") for k in keys}
    for k in keys:
        if k.get("slug"):
            by_slug.setdefault(k["slug"], set()).add(k["pk"])
        ck = coord_key(k)
        if ck:
            by_coord.setdefault(ck, set()).add(k["pk"])
        nk = (norm_name(k["name"]), norm_state(k["state"]))
        if nk[0]:
            by_name.setdefault(nk, set()).add(k["pk"])
        by_id[str(k["current_id"])] = k
    name_count = {}
    for r in rows:
        nk = (norm_name(r.get("name")), norm_state(r.get("state")))
        name_count[nk] = name_count.get(nk, 0) + 1

    picks, held = {}, []
    for r in rows:
        rid = str(r.get("id") or "").strip()
        slug = str(r.get("slug") or "").strip()
        st = norm_state(r.get("state"))
        s = by_slug.get(slug, set()) if slug else set()
        # Same coordinates, and the states agree or one side has none: the
        # source filled in EdgeConneX Sheridan's blank state and renumbered
        # it (341 -> 17), which a strict state test read as a departure.
        c = {pk for pk in (by_coord.get(coord_key(r), set()) if coord_key(r) else set())
             if not st or not norm_state(state_of[pk]) or norm_state(state_of[pk]) == st}
        if len(s) > 1 or len(c) > 1 or (s and c and s != c):
            held.append({"id": rid, "name": r.get("name", ""),
                         "reason": "slug and coordinates disagree or match "
                                   "several keys"})
            continue
        pk = next(iter(s or c), None)
        if pk is None:
            nk = (norm_name(r.get("name")), st)
            n = by_name.get(nk, set())
            if nk[0] and len(n) == 1 and name_count[nk] == 1:
                pk = next(iter(n))
            elif len(n) > 1 or (n and name_count[nk] > 1):
                held.append({"id": rid, "name": r.get("name", ""),
                             "reason": "name and state are not unique"})
                continue
        if pk is None:
            k = by_id.get(rid)
            if k is not None and norm_state(k["state"]) == st:
                d = km_between(k, r)
                if (jaccard(k["name"], r.get("name")) >= NAME_JACCARD
                        or (d is not None and d <= SAME_ID_KM)):
                    pk = k["pk"]
        if pk is not None:
            picks[rid] = pk

    claims = {}
    for rid, pk in picks.items():
        claims.setdefault(pk, []).append(rid)
    out = {}
    names = {str(r.get("id") or "").strip(): r.get("name", "") for r in rows}
    for pk, rids in claims.items():
        if len(rids) == 1:
            out[rids[0]] = pk
        else:
            for rid in rids:
                held.append({"id": rid, "name": names.get(rid, ""),
                             "reason": f"{len(rids)} rows claim the same key {pk}"})
    return out, held


# --------------------------------------------------------------------------
# byte-preserving CSV cells
# --------------------------------------------------------------------------

def scan_csv(text: str) -> list[list[tuple[int, int]]]:
    """Character spans of every field of every record, quotes included.

    csv.reader cannot say where a cell sits in the file, and rewriting a file
    through csv.writer would re-quote and re-terminate every line of a hand
    file. This scanner records spans so a cell can be replaced in place.
    """
    records, fields = [], []
    i, n, start = 0, len(text), 0
    in_q = False
    while i < n:
        ch = text[i]
        if in_q:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    i += 2
                    continue
                in_q = False
            i += 1
            continue
        if ch == '"':
            in_q = True
            i += 1
        elif ch == ",":
            fields.append((start, i))
            i += 1
            start = i
        elif ch in "\r\n":
            fields.append((start, i))
            records.append(fields)
            fields = []
            i += 2 if ch == "\r" and i + 1 < n and text[i + 1] == "\n" else 1
            start = i
        else:
            i += 1
    if start < n or fields:
        fields.append((start, n))
        records.append(fields)
    return records


def cell_value(text: str, span) -> str:
    raw = text[span[0]:span[1]]
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].replace('""', '"')
    return raw


def read_hand_file(path: str):
    """(text, header, records as (values, spans)). BOM kept in text."""
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    body = text[1:] if text.startswith("﻿") else text
    off = len(text) - len(body)
    recs = []
    for spans in scan_csv(body):
        spans = [(a + off, b + off) for a, b in spans]
        vals = [cell_value(text, s) for s in spans]
        if vals == [""]:
            continue
        recs.append((vals, spans))
    if not recs:
        return text, [], []
    return text, recs[0][0], recs[1:]


def replace_cells(text: str, edits: list[tuple[tuple[int, int], str]]) -> str:
    for (a, b), new in sorted(edits, key=lambda e: e[0][0], reverse=True):
        raw = text[a:b]
        quoted = len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"'
        text = text[:a] + (f'"{new}"' if quoted else new) + text[b:]
    return text


def row_sig(values: list[str], header: list[str], id_cols) -> str:
    keep = [v for h, v in zip(header, values) if h not in id_cols]
    return hashlib.sha1("\x1f".join(keep).encode("utf-8")).hexdigest()[:16]


def to_id(cell: str, fmt: str) -> str | None:
    v = cell.strip()
    if fmt == "prj":
        return v[4:] if v.startswith("prj_") and v[4:].isdigit() else None
    return v if v.isdigit() else None


def from_id(i: str, fmt: str) -> str:
    return f"prj_{i}" if fmt == "prj" else i


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: str, fields, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def added_ids(data_dir: str) -> set[str]:
    return {str(r.get("id") or "").strip()
            for r in read_csv(os.path.join(data_dir, ADDED))}


def _identity(r) -> dict:
    return {"name": r.get("name", ""), "state": r.get("state", ""),
            "lat": str(r.get("lat") or ""), "lon": str(r.get("lon") or ""),
            "slug": str(r.get("slug") or "")}


def _alias(aliases: dict, pk: str, kind: str, value: str, today: str) -> None:
    value = str(value or "").strip()
    if not value:
        return
    a = aliases.get((pk, kind, value))
    if a is None:
        aliases[(pk, kind, value)] = {"pk": pk, "kind": kind, "value": value,
                                      "first_seen": today, "last_seen": today}
    else:
        a["last_seen"] = today


# --------------------------------------------------------------------------
# anchoring and rewriting the hand files
# --------------------------------------------------------------------------

def plan_hand_files(data_dir, anchors, id_to_pk_before, pk_after, today):
    """Work out every id-cell rewrite. Pure: writes nothing.

    anchors:         {(file, sig, occ, col): anchor row}
    id_to_pk_before: {source id: pk} as the ids stood when rows were written
    pk_after:        {pk: map row} after this run's match
    Returns (edits {file: [(span, new)]}, new anchors, findings).
    """
    edits, new_anchors, findings = {}, {}, []
    for fname, cols, fmt in HAND_FILES:
        path = os.path.join(data_dir, fname)
        if not os.path.exists(path):
            continue
        text, header, recs = read_hand_file(path)
        seen = {}
        for line_no, (vals, spans) in enumerate(recs, start=2):
            sig = row_sig(vals, header, cols)
            occ = seen.get(sig, 0)
            seen[sig] = occ + 1
            for col in cols:
                if col not in header:
                    continue
                ci = header.index(col)
                if ci >= len(vals):
                    continue
                cur = to_id(vals[ci], fmt)
                if cur is None:
                    continue                    # aic_ ids and blanks: not Layer B keys
                key = (fname, sig, str(occ), col)
                a = anchors.get(key)
                if a is None:
                    pk, basis = id_to_pk_before.get(cur), "new_row"
                elif a["written_id"] != cur:
                    pk, basis = id_to_pk_before.get(cur), "human"
                    findings.append(("human", fname, line_no, col, cur, pk or "",
                                     "id changed by hand; re-anchored, not reverted"))
                else:
                    pk, basis = a["pk"] or None, a["basis"]
                anchor = {"file": fname, "row_sig": sig, "occurrence": str(occ),
                          "column": col, "pk": pk or "", "written_id": cur,
                          "anchored_on": (a["anchored_on"] if a and basis == a["basis"]
                                          and a["pk"] == (pk or "") else today),
                          "basis": basis}
                if pk is None:
                    findings.append(("unknown", fname, line_no, col, cur, "",
                                     "id names no known project; held"))
                    new_anchors[key] = anchor
                    continue
                k = pk_after.get(pk)
                if k is None or k["status"] != "active":
                    status = k["status"] if k else "missing"
                    findings.append(("orphan" if status == "gone" else "held",
                                     fname, line_no, col, cur, pk,
                                     f"key is {status}; cell left as is"))
                    new_anchors[key] = anchor
                    continue
                target = str(k["current_id"])
                if target != cur:
                    edits.setdefault(fname, []).append(
                        (spans[ci], from_id(target, fmt)))
                    findings.append(("rewrite", fname, line_no, col, cur, pk,
                                     f"{cur} -> {target} ({k['name']})"))
                    anchor["written_id"] = target
                new_anchors[key] = anchor
    return edits, new_anchors, findings


def apply_edits(data_dir, edits) -> None:
    for fname, e in edits.items():
        path = os.path.join(data_dir, fname)
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(replace_cells(text, e))


# --------------------------------------------------------------------------
# init and sync
# --------------------------------------------------------------------------

def load_state(data_dir):
    keys = read_csv(os.path.join(data_dir, MAP))
    aliases = {(a["pk"], a["kind"], a["value"]): a
               for a in read_csv(os.path.join(data_dir, ALIASES))}
    anchors = {(a["file"], a["row_sig"], a["occurrence"], a["column"]): a
               for a in read_csv(os.path.join(data_dir, ANCHORS))}
    return keys, aliases, anchors


def _next_pk(keys) -> int:
    nums = [int(k["pk"][3:]) for k in keys if re.fullmatch(r"pk_\d+", k["pk"])]
    return (max(nums) + 1) if nums else 1


def init(data_dir, today, force=False) -> dict:
    if os.path.exists(os.path.join(data_dir, MAP)) and not force:
        raise SystemExit(f"{MAP} already exists; --init runs once "
                         "(use --force to rebuild from scratch)")
    manual = added_ids(data_dir)
    rows = read_csv(os.path.join(data_dir, PROPOSALS))
    rows.sort(key=lambda r: (int(r["id"]) if str(r["id"]).isdigit() else 10**9,
                             str(r["id"])))
    keys, aliases = [], {}
    for n, r in enumerate(rows, start=1):
        pk = f"pk_{n:05d}"
        rid = str(r["id"]).strip()
        keys.append({"pk": pk, "source": "manual" if rid in manual else "trackdatacenters",
                     "current_id": rid, **_identity(r), "status": "active",
                     "first_seen": today, "last_seen": today, "last_id_change": ""})
        _alias(aliases, pk, "id", rid, today)
        _alias(aliases, pk, "slug", r.get("slug"), today)
        _alias(aliases, pk, "name", r.get("name"), today)
    id_to_pk = {k["current_id"]: k["pk"] for k in keys}
    pk_after = {k["pk"]: k for k in keys}
    edits, anchors, findings = plan_hand_files(data_dir, {}, id_to_pk, pk_after, today)
    assert not edits, "init must never rewrite a cell"
    write_csv(os.path.join(data_dir, MAP), MAP_FIELDS, keys)
    write_csv(os.path.join(data_dir, ALIASES), ALIAS_FIELDS,
              sorted(aliases.values(), key=lambda a: (a["pk"], a["kind"], a["value"])))
    write_csv(os.path.join(data_dir, ANCHORS), ANCHOR_FIELDS,
              sorted(anchors.values(), key=lambda a: (a["file"], a["row_sig"],
                                                      a["occurrence"], a["column"])))
    result = {"mode": "init", "keys": len(keys), "anchored": len(anchors),
              "findings": findings, "moved": [], "held": [], "minted": [],
              "gone": [], "degraded": False}
    write_report(data_dir, result, today)
    return result


def sync(data_dir, today, dry_run=False) -> dict:
    keys, aliases, anchors = load_state(data_dir)
    if not keys:
        print("project_key_map: not initialised; nothing to do "
              "(run --init once after the re-key is applied)")
        return {"mode": "noop"}
    manual = added_ids(data_dir)
    rows = read_csv(os.path.join(data_dir, PROPOSALS))
    src_rows = [r for r in rows if str(r["id"]).strip() not in manual]
    man_rows = {str(r["id"]).strip(): r for r in rows if str(r["id"]).strip() in manual}

    id_to_pk_before = {k["current_id"]: k["pk"] for k in keys if k["status"] == "active"}
    src_keys = [k for k in keys if k["source"] != "manual"]
    with_slug = {a["pk"]: a["value"] for a in aliases.values() if a["kind"] == "slug"}
    for k in src_keys:
        k.setdefault("slug", "")
        if not k["slug"] and k["pk"] in with_slug:
            k["slug"] = with_slug[k["pk"]]

    matched, held = match(src_keys, src_rows)
    active_src = [k for k in src_keys if k["status"] == "active"]
    hit = set(matched.values())
    vanished = [k for k in active_src if k["pk"] not in hit]
    held_ids = {h["id"] for h in held}
    share = len(vanished) / len(active_src) if active_src else 0.0
    result = {"mode": "sync", "moved": [], "held": held, "minted": [],
              "gone": [], "findings": [], "degraded": False,
              "vanished_share": share, "keys": len(keys)}
    if not src_rows or share > DEGRADED_SHARE:
        result["degraded"] = True
        print(f"::warning::project_key_map: {len(vanished)} of {len(active_src)} "
              f"known projects vanished ({share:.0%}); degraded scrape, nothing written")
        write_report(data_dir, result, today, write=not dry_run)
        return result

    by_pk = {k["pk"]: k for k in keys}
    next_n = _next_pk(keys)
    for r in src_rows:
        rid = str(r["id"]).strip()
        if rid in held_ids:
            continue
        pk = matched.get(rid)
        if pk is None:
            pk = f"pk_{next_n:05d}"
            next_n += 1
            k = {"pk": pk, "source": "trackdatacenters", "current_id": rid,
                 **_identity(r), "status": "active", "first_seen": today,
                 "last_seen": today, "last_id_change": ""}
            keys.append(k)
            by_pk[pk] = k
            result["minted"].append((pk, rid, r.get("name", "")))
        else:
            k = by_pk[pk]
            if k["current_id"] != rid:
                result["moved"].append((pk, k["current_id"], rid, r.get("name", "")))
                k["last_id_change"] = today
            k.update({"current_id": rid, **{f: v for f, v in _identity(r).items()
                                            if v or f != "slug"}},
                     status="active", last_seen=today)
        _alias(aliases, pk, "id", rid, today)
        _alias(aliases, pk, "slug", r.get("slug"), today)
        _alias(aliases, pk, "name", r.get("name"), today)
    # A vanished key in the same state as a held row may be that row's
    # project, so it is held rather than declared gone. Either way its rows
    # are left as they are; the difference is what the report asks for.
    held_states = {norm_state(r.get("state")) for r in src_rows
                   if str(r["id"]).strip() in held_ids}
    for k in vanished:
        if norm_state(k["state"]) in held_states:
            k["status"] = "held"
        else:
            k["status"] = "gone"
            result["gone"].append((k["pk"], k["current_id"], k["name"]))
    for k in keys:
        if k["source"] == "manual":
            if k["current_id"] in man_rows:
                k.update(status="active", last_seen=today)
            else:
                k["status"] = "gone"

    # Ids that moved AWAY from a key may now name a different project; a row
    # anchored before that is resolved by its anchor, never by the number.
    edits, new_anchors, findings = plan_hand_files(
        data_dir, anchors, id_to_pk_before, by_pk, today)
    result["findings"] = findings
    if not dry_run:
        apply_edits(data_dir, edits)
        write_csv(os.path.join(data_dir, MAP), MAP_FIELDS,
                  sorted(keys, key=lambda k: k["pk"]))
        write_csv(os.path.join(data_dir, ALIASES), ALIAS_FIELDS,
                  sorted(aliases.values(), key=lambda a: (a["pk"], a["kind"], a["value"])))
        write_csv(os.path.join(data_dir, ANCHORS), ANCHOR_FIELDS,
                  sorted(new_anchors.values(), key=lambda a: (
                      a["file"], a["row_sig"], a["occurrence"], a["column"])))
    write_report(data_dir, result, today, write=not dry_run)
    return result


def id_translation(prev_rows, new_rows, data_dir=DATA):
    """{previous id: new id} for the scraper's overlay, from the key map when
    it is initialised and from a direct snapshot match otherwise.

    Only confidently matched ids appear. An id missing from the result either
    left the source or is ambiguous, and an overlay row keyed on it must not
    be applied to whatever now holds that number.
    """
    if not prev_rows:
        return None
    keys = [k for k in read_csv(os.path.join(data_dir, MAP))
            if k["source"] != "manual" and k["status"] == "active"]
    if not keys:
        keys = [{"pk": f"t{str(r.get('id')).strip()}",
                 "current_id": str(r.get("id")).strip(), **_identity(r)}
                for r in prev_rows]
    rows = [dict(r, id=str(r.get("id")).strip()) for r in new_rows]
    matched, _ = match(keys, rows)
    pk_to_new = {pk: rid for rid, pk in matched.items()}
    return {k["current_id"]: pk_to_new[k["pk"]] for k in keys if k["pk"] in pk_to_new}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def write_report(data_dir, result, today, write=True) -> str:
    kinds = {}
    for f in result.get("findings", []):
        kinds[f[0]] = kinds.get(f[0], 0) + 1
    lines = ["# Project key map report", "",
             f"Run: {today}, mode `{result['mode']}`. Generated by "
             "`project_key_map.py`; see its docstring for the rules.", ""]
    if result.get("degraded"):
        lines += [f"**Degraded scrape: {result.get('vanished_share', 0):.0%} of "
                  "known projects vanished. Nothing was written.** The map, "
                  "anchors and hand files are as the previous run left them.", ""]
    lines += ["| measure | count |", "|---|---|",
              f"| keys | {result.get('keys', 0)} |",
              f"| ids moved | {len(result.get('moved', []))} |",
              f"| keys minted | {len(result.get('minted', []))} |",
              f"| keys gone from the source | {len(result.get('gone', []))} |",
              f"| scraped rows held (ambiguous) | {len(result.get('held', []))} |"]
    for k in ("rewrite", "orphan", "held", "unknown", "human"):
        lines.append(f"| hand-file cells: {k} | {kinds.get(k, 0)} |")
    if result.get("moved"):
        lines += ["", "## Ids that moved", "", "| key | old id | new id | project |",
                  "|---|---|---|---|"]
        lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in result["moved"]]
    if result.get("held"):
        lines += ["", "## Held for a person: scraped rows", "", "| id | project | why |",
                  "|---|---|---|"]
        lines += [f"| {h['id']} | {h['name']} | {h['reason']} |" for h in result["held"]]
    if result.get("gone"):
        lines += ["", "## Keys gone from the source", "", "| key | last id | project |",
                  "|---|---|---|"]
        lines += [f"| {a} | {b} | {c} |" for a, b, c in result["gone"]]
    fs = [f for f in result.get("findings", []) if f[0] != "rewrite"]
    rw = [f for f in result.get("findings", []) if f[0] == "rewrite"]
    if rw:
        lines += ["", "## Cells rewritten", "", "| file | line | column | change |",
                  "|---|---|---|---|"]
        lines += [f"| {f[1]} | {f[2]} | {f[3]} | {f[6]} |" for f in rw]
    if fs:
        lines += ["", "## Held for a person: hand-file rows", "",
                  "| kind | file | line | column | id | key | detail |",
                  "|---|---|---|---|---|---|---|"]
        lines += ["| " + " | ".join(str(x) for x in f) + " |" for f in fs]
    text = "\n".join(lines) + "\n"
    if write:
        with open(os.path.join(data_dir, REPORT), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)
    return text


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    import tempfile

    checks = []

    def check(label, ok):
        checks.append((label, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    HDR = ["id", "name", "state", "lat", "lon", "phase"]

    def proj(rows):
        return [dict(zip(HDR, r)) for r in rows]

    def put(d, name, text):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def get(d, name):
        with open(os.path.join(d, name), encoding="utf-8", newline="") as fh:
            return fh.read()

    def put_proposals(d, rows):
        write_csv(os.path.join(d, PROPOSALS), HDR, proj(rows))

    base = [("1", "Project Delta", "North Carolina", "35.1", "-80.1", "proposed"),
            ("2", "Armory Innovation", "Missouri", "38.6", "-90.2", "proposed"),
            ("3", "Colossus", "Tennessee", "35.0", "-90.0", "operational"),
            ("4", "Kenwood", "New York", "42.6", "-73.8", "proposed"),
            ("5", "Sheridan", "", "", "", "proposed"),
            ("6", "Other A", "Ohio", "40.0", "-83.0", "proposed"),
            ("7", "Other B", "Ohio", "40.1", "-83.1", "proposed")]

    # --- the CSV scanner ----------------------------------------------------
    t = 'a,b\r\n1,"x, ""y""\nz"\r\n2,\r\n'
    recs = scan_csv(t)
    check("scanner finds three records across a quoted newline", len(recs) == 3)
    check("scanner reads a quoted cell back exactly",
          cell_value(t, recs[1][1]) == 'x, "y"\nz')
    check("scanner keeps an empty trailing cell", cell_value(t, recs[2][1]) == "")
    out = replace_cells(t, [(recs[1][0], "9"), (recs[2][0], "8")])
    check("replacing cells preserves every other byte incl. CRLF",
          out == 'a,b\r\n9,"x, ""y""\nz"\r\n8,\r\n')
    check("a quoted cell stays quoted",
          replace_cells('"1",x\n', [((0, 3), "7")]) == '"7",x\n')

    # --- matching -----------------------------------------------------------
    keys = [{"pk": f"pk_{i}", "current_id": r[0], "name": r[1], "state": r[2],
             "lat": r[3], "lon": r[4], "slug": ""} for i, r in enumerate(base, 1)]
    renum = proj([("11", "Project Delta", "North Carolina", "35.1", "-80.1", "p"),
                  ("12", "Armory Innovation", "Missouri", "38.6", "-90.2", "p"),
                  ("1", "xAI Colossus 1", "Tennessee", "35.0", "-90.0", "o"),
                  ("2", "Kenwood Commons", "New York", "42.6", "-73.8", "p"),
                  ("16", "Other A", "Ohio", "40.0", "-83.0", "p"),
                  ("17", "Other B", "Ohio", "40.1", "-83.1", "p"),
                  ("20", "Brand New", "Texas", "31.0", "-97.0", "p")])
    m, h = match(keys, renum)
    check("a renumbered project follows its identity", m.get("11") == "pk_1")
    check("an id reused by another project is not inherited", m.get("1") == "pk_3")
    check("a rename with the same coordinates keeps its key", m.get("2") == "pk_4")
    check("a new project matches nothing", "20" not in m and not h)
    filled = proj([("17", "Sheridan", "Arkansas", "34.3065", "-92.4045", "p")])
    k5 = [dict(keys[4], lat="34.3065", lon="-92.4045")]
    check("a blank state filled in later is not a new project",
          match(k5, filled)[0] == {"17": "pk_5"})
    moved = proj([("4", "Lake Hawkeye", "New York", "42.6079", "-73.8003", "p")])
    check("same id, same state, 0.6 km away is the same project despite a new name",
          match(keys, moved)[0] == {"4": "pk_4"})
    dup = proj([("30", "Twin", "Ohio", "40.0", "-83.0", "p"),
                ("31", "Twin", "Ohio", "40.0", "-83.0", "p")])
    m2, h2 = match(keys, dup)
    check("two rows claiming one key are both held", not m2 and len(h2) == 2)
    sk = [dict(keys[0], slug="delta"), dict(keys[1], slug="armory")]
    conflict = proj([("40", "Project Delta", "Missouri", "38.6", "-90.2", "p")])
    conflict[0]["slug"] = "delta"
    m3, h3 = match(sk, conflict)
    check("slug and coordinates that disagree are held", not m3 and h3)
    slugged = proj([("41", "Totally Renamed", "North Carolina", "0", "0", "p")])
    slugged[0]["slug"] = "delta"
    check("a slug match decides without coordinates", match(sk, slugged)[0] == {"41": "pk_1"})

    with tempfile.TemporaryDirectory() as d:
        today = "2026-09-28"
        put_proposals(d, base + [("1001", "Manual One", "Ohio", "41", "-81", "p")])
        put(d, ADDED, "id,name\n1001,Manual One\n")
        put(d, "proposals_manual_overlay.csv",
            "id,field,value,reason,source\n1,phase,proposed,\"voided, see note\",court\n")
        put(d, "project_decision_dates.csv",
            "project_id,decision_date,note\r\nprj_2,2026-01-01,x\r\nprj_1001,2026-02-02,m\r\n")
        put(d, "project_links_manual.csv",
            "opp_id,project_id,action,note\nopp_a,prj_4,reject,\"a, b\"\n"
            "opp_b,prj_5,confirm,sheridan\nopp_c,prj_999,confirm,unknown\n")
        put(d, "project_duplicates.csv", "keep_id,drop_id,reason\nprj_6,prj_7,dup\n")
        put(d, "negative_audit_codings.csv",
            "universe_id,coding\nprj_3,verified\naic_0003,none\n")

        # sync before init is a no-op
        check("sync before init writes nothing",
              sync(d, today)["mode"] == "noop" and not os.path.exists(os.path.join(d, MAP)))

        r0 = init(d, today)
        check("init keys every project incl. manual additions", r0["keys"] == 8)
        check("init flags the unknown id and anchors nothing to it",
              any(f[0] == "unknown" and f[4] == "999" for f in r0["findings"]))
        try:
            init(d, today)
            again = False
        except SystemExit:
            again = True
        check("init refuses to run twice", again)
        before_dd = get(d, "project_decision_dates.csv")

        # --- the 09-17 style renumbering, Sheridan leaves -----------------
        put_proposals(d, [
            ("11", "Project Delta", "North Carolina", "35.1", "-80.1", "p"),
            ("12", "Armory Innovation", "Missouri", "38.6", "-90.2", "p"),
            ("1", "xAI Colossus 1", "Tennessee", "35.0", "-90.0", "o"),
            ("2", "Kenwood Commons", "New York", "42.6", "-73.8", "p"),
            ("16", "Other A", "Ohio", "40.0", "-83.0", "p"),
            ("17", "Other B", "Ohio", "40.1", "-83.1", "p"),
            ("1001", "Manual One", "Ohio", "41", "-81", "p")])
        dry = sync(d, today, dry_run=True)
        check("a dry run writes nothing",
              get(d, "project_decision_dates.csv") == before_dd and dry["moved"])
        r1 = sync(d, "2026-09-29")
        check("moved ids are reported", len(r1["moved"]) == 6)
        ov = get(d, "proposals_manual_overlay.csv")
        check("the overlay follows Project Delta, other bytes intact",
              ov == "id,field,value,reason,source\n11,phase,proposed,\"voided, see note\",court\n")
        dd = get(d, "project_decision_dates.csv")
        check("decision dates follow Armory and keep CRLF",
              dd == "project_id,decision_date,note\r\nprj_12,2026-01-01,x\r\nprj_1001,2026-02-02,m\r\n")
        ln = get(d, "project_links_manual.csv")
        check("a renamed project's link follows it", "opp_a,prj_2,reject" in ln)
        check("an orphan row is left untouched", "opp_b,prj_5,confirm" in ln)
        check("the orphan is held for a person",
              any(f[0] == "orphan" and f[4] == "5" for f in r1["findings"]))
        check("both duplicate columns follow",
              get(d, "project_duplicates.csv") == "keep_id,drop_id,reason\nprj_16,prj_17,dup\n")
        na = get(d, "negative_audit_codings.csv")
        check("colossus coding follows, aic ids untouched",
              "prj_1,verified" in na and "aic_0003,none" in na)
        check("the departed project's key is gone, not deleted",
              any(k["name"] == "Sheridan" and k["status"] == "gone"
                  for k in read_csv(os.path.join(d, MAP))))

        # --- a second run on the same data changes nothing --------------
        snap = {f: get(d, f) for f, _, _ in HAND_FILES}
        r2 = sync(d, "2026-09-30")
        check("a repeat run is idempotent",
              not r2["moved"] and all(get(d, f) == s for f, s in snap.items()))

        # --- a person re-points a row by hand ---------------------------
        put(d, "project_links_manual.csv", ln.replace("opp_a,prj_2,", "opp_a,prj_16,"))
        r3 = sync(d, "2026-10-01")
        check("a hand re-point is recorded", any(f[0] == "human" for f in r3["findings"]))
        check("and not reverted", "opp_a,prj_16,reject" in get(d, "project_links_manual.csv"))
        put_proposals(d, [
            ("11", "Project Delta", "North Carolina", "35.1", "-80.1", "p"),
            ("12", "Armory Innovation", "Missouri", "38.6", "-90.2", "p"),
            ("1", "xAI Colossus 1", "Tennessee", "35.0", "-90.0", "o"),
            ("2", "Kenwood Commons", "New York", "42.6", "-73.8", "p"),
            ("26", "Other A", "Ohio", "40.0", "-83.0", "p"),
            ("27", "Other B", "Ohio", "40.1", "-83.1", "p"),
            ("1001", "Manual One", "Ohio", "41", "-81", "p")])
        sync(d, "2026-10-02")
        check("the re-pointed row then follows its new project",
              "opp_a,prj_26,reject" in get(d, "project_links_manual.csv"))

        # --- the returning project -----------------------------------------
        put_proposals(d, [
            ("11", "Project Delta", "North Carolina", "35.1", "-80.1", "p"),
            ("12", "Armory Innovation", "Missouri", "38.6", "-90.2", "p"),
            ("1", "xAI Colossus 1", "Tennessee", "35.0", "-90.0", "o"),
            ("2", "Kenwood Commons", "New York", "42.6", "-73.8", "p"),
            ("26", "Other A", "Ohio", "40.0", "-83.0", "p"),
            ("27", "Other B", "Ohio", "40.1", "-83.1", "p"),
            ("55", "Sheridan", "", "", "", "p"),
            ("1001", "Manual One", "Ohio", "41", "-81", "p")])
        sync(d, "2026-10-03")
        check("a project that comes back takes its rows with it",
              "opp_b,prj_55,confirm" in get(d, "project_links_manual.csv"))

        # --- degraded scrape ------------------------------------------------
        snap = {f: get(d, f) for f, _, _ in HAND_FILES + [(MAP, 0, 0)]}
        put_proposals(d, [("11", "Project Delta", "North Carolina", "35.1", "-80.1", "p"),
                          ("1001", "Manual One", "Ohio", "41", "-81", "p")])
        r4 = sync(d, "2026-10-04")
        check("a scrape that lost most projects is degraded",
              r4["degraded"] and all(get(d, f) == s for f, s in snap.items()))

        # --- scraper translation --------------------------------------------
        prev = proj([("11", "Project Delta", "North Carolina", "35.1", "-80.1", "p"),
                     ("12", "Armory Innovation", "Missouri", "38.6", "-90.2", "p")])
        new = [{"id": 99, "name": "Project Delta", "state": "North Carolina",
                "lat": 35.1, "lon": -80.1},
               {"id": 11, "name": "Armory Innovation", "state": "Missouri",
                "lat": 38.6, "lon": -90.2}]
        tr = id_translation(prev, new, d)
        check("translation maps the old id to the new one via the map",
              tr.get("11") == "99" and tr.get("12") == "11")
    with tempfile.TemporaryDirectory() as d2:
        tr2 = id_translation(prev, new, d2)
        check("translation works before init, from the snapshots",
              tr2 == {"11": "99", "12": "11"})
        check("no previous run means no translation", id_translation(None, new, d2) is None)

    n_ok = sum(1 for _, ok in checks if ok)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--init", action="store_true",
                    help="mint keys and anchor every hand-file row, once")
    ap.add_argument("--force", action="store_true",
                    help="with --init: rebuild even though a map exists")
    ap.add_argument("--dry-run", action="store_true",
                    help="sync and report, write nothing")
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    today = dt.date.today().isoformat()
    if args.init:
        r = init(args.data, today, force=args.force)
        print(f"project_key_map: {r['keys']} keys, {r['anchored']} cells anchored, "
              f"{sum(1 for f in r['findings'] if f[0] == 'unknown')} unknown ids held")
        return 0
    r = sync(args.data, today, dry_run=args.dry_run)
    if r["mode"] == "sync" and not r["degraded"]:
        kinds = {}
        for f in r["findings"]:
            kinds[f[0]] = kinds.get(f[0], 0) + 1
        print(f"project_key_map: {len(r['moved'])} ids moved, "
              f"{kinds.get('rewrite', 0)} cells rewritten, "
              f"{len(r['held']) + kinds.get('orphan', 0) + kinds.get('held', 0) + kinds.get('unknown', 0)} "
              f"held for a person" + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
