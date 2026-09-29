#!/usr/bin/env python3
"""
proposal_history.py

Turns the nightly commits of data/proposals.csv into a dated event log.

Why this exists
---------------
The scraper rebuilds data/proposals.csv whole every night, so the file only
ever says what is true today. But every one of those nights was committed, and
the git history of that one file is a daily panel of the proposal universe
going back to 2026-04-23. Nothing read it. A project that moved from proposed
to withdrawn in July, or whose developer changed in August, or that appeared
for the first time last Tuesday, was visible to nobody unless they happened to
diff two commits by hand.

This module reads that history and writes what changed and when:

  first_seen     the project appears for the first time after the baseline
  phase_change   the phase moved (old -> new)
  removed        the project dropped out of the source
  reappeared     a removed project came back
  field_change   capacity_mw, size_acres, companies or name changed
  moved          coordinates moved by more than MOVE_KM

It is descriptive. An event dates when the SOURCE changed, which is an upper
bound on when the underlying thing happened, never the date itself. A phase
change first seen on 2026-07-14 happened on or before 2026-07-14. The output
says so in its column names (seen_date, not event_date).

Two history defects it has to survive
-------------------------------------
1. The 2026-09-02 id migration. Manual additions were renumbered from
   321-332 to 1001-1012, and 321-326 were then reused by the source for six
   Pennsylvania projects. Read naively, that is twelve removals, twelve
   first-seens and six projects that "changed name". Old ids are translated
   only when the row's name also matches the manual addition now holding the
   new id, so a reused id is never mistaken for its predecessor.
2. Degraded snapshots. On 2026-09-10 a source rename emptied six columns and
   the run was committed anyway (the population guard came after). Diffing
   that snapshot would record a date change for 300 projects, and a second
   one when the fix landed. The same collapse test the scraper's guard
   applies is applied here per snapshot: a field that lost more than half its
   population is marked degraded for that snapshot, its changes are not
   recorded, and the last good value is carried. A snapshot that lost more
   than DROP_RATIO of its rows is treated as a partial fetch and records no
   removals.

Outputs
-------
  data/pipeline_intel_events.csv           one row per detected change
  data/pipeline_intel_project_history.csv  one row per project: first seen,
                                           phase changes, time in phase
  data/pipeline_intel_history.json         snapshot coverage, weekly flow,
                                           phase transition counts, degraded
                                           snapshots and why

Usage
-----
  python proposal_history.py
  python proposal_history.py --selftest

Requires the full git history (actions/checkout fetch-depth: 0). Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKED_PATH = "data/proposals.csv"
ADDED_CSV = os.path.join(HERE, "data", "proposals_added.csv")
OUT_EVENTS = os.path.join(HERE, "data", "pipeline_intel_events.csv")
OUT_PROJECTS = os.path.join(HERE, "data", "pipeline_intel_project_history.csv")
OUT_JSON = os.path.join(HERE, "data", "pipeline_intel_history.json")
OUT_CROSSWALK = os.path.join(HERE, "data", "pipeline_intel_id_crosswalk.csv")
OUT_TIMELINE = os.path.join(HERE, "data", "pipeline_intel_id_timeline.csv")
TIMELINE_FIELDS = ["entity", "source_id", "first_snapshot", "last_snapshot",
                   "current_id", "present_now", "name", "state"]

WATCHED_FIELDS = ("capacity_mw", "size_acres", "companies", "name")
MOVE_KM = 5.0
FIELD_LOSS_MIN_PRIOR = 20      # same thresholds as the scraper's guard
FIELD_LOSS_RATIO = 0.5
DROP_RATIO = 0.30              # row-count loss that marks a partial fetch
RECLASS_MIN = 10               # shared phase moves at or above this are a
                               # source vocabulary change candidate

# The 2026-09-02 renumbering, from ARCHITECTURE.md. Applied only with a name
# match; see the module docstring.
ID_MIGRATION = {str(o): str(n) for o, n in zip(range(321, 333), range(1001, 1013))}
MIGRATION_DATE = "2026-09-02"

EVENT_FIELDS = ["seen_date", "project_id", "source_id", "event_type", "field",
                "old_value", "new_value", "prev_snapshot", "snapshot", "commit"]
CROSSWALK_FIELDS = ["snapshot", "prev_snapshot", "old_id", "new_id", "name",
                    "state", "basis"]
PROJECT_FIELDS = ["project_id", "source_ids", "name", "state", "phase", "in_baseline",
                  "first_seen", "last_seen", "present_now", "n_snapshots",
                  "n_phase_changes", "phase_path", "last_phase_change",
                  "prior_phase", "phase_since", "days_in_phase",
                  "n_field_changes", "last_change"]


# ---------------------------------------------------------------------------
# reading history
# ---------------------------------------------------------------------------

def git(*args, cwd=HERE):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


SNAPSHOT_TIMES = {}   # day -> ISO commit time of the snapshot kept for it


def list_snapshots(path=TRACKED_PATH, cwd=HERE):
    """[(day, commit)] oldest first, keeping the LAST commit of each UTC day.

    Days are UTC. The kept commit's full timestamp goes to SNAPSHOT_TIMES so
    id spans can be compared with the time a hand-maintained row was written.
    """
    out = git("log", "--format=%H %cI", "--", path, cwd=cwd)
    by_day = {}
    for line in reversed(out.strip().splitlines()):
        sha, iso = line.split(" ", 1)
        utc = datetime.fromisoformat(iso).astimezone(timezone.utc)
        day = utc.date().isoformat()
        by_day[day] = sha          # later commits overwrite earlier ones
        SNAPSHOT_TIMES[day] = utc.isoformat().replace("+00:00", "Z")
    return sorted(by_day.items())


def read_snapshot(sha, path=TRACKED_PATH, cwd=HERE):
    body = git("show", f"{sha}:{path}", cwd=cwd)
    return list(csv.DictReader(io.StringIO(body)))


def _norm_name(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def migration_names(added_csv=ADDED_CSV):
    """new id -> normalized name, from the manual additions file."""
    if not os.path.exists(added_csv):
        return {}
    with open(added_csv, newline="", encoding="utf-8-sig") as fh:
        return {str(r["id"]).strip(): _norm_name(r.get("name"))
                for r in csv.DictReader(fh)}


def translate_id(raw_id, name, day, names_by_new_id):
    """Old manual id -> new id, only before the migration and on a name match."""
    rid = str(raw_id).strip()
    if day > MIGRATION_DATE or rid not in ID_MIGRATION:
        return rid
    new = ID_MIGRATION[rid]
    want = names_by_new_id.get(new, "")
    got = _norm_name(name)
    if want and got and (got.startswith(want[:14]) or want.startswith(got[:14])):
        return new
    return rid


# ---------------------------------------------------------------------------
# degraded-snapshot detection
# ---------------------------------------------------------------------------

def _present(v):
    return v is not None and str(v).strip() != ""


def degraded_fields(prev_rows, rows, fields):
    """Fields whose population collapsed between two snapshots."""
    out = []
    for f in fields:
        p = sum(1 for r in prev_rows if _present(r.get(f)))
        n = sum(1 for r in rows if _present(r.get(f)))
        if p >= FIELD_LOSS_MIN_PRIOR and n <= p * (1.0 - FIELD_LOSS_RATIO):
            out.append((f, p, n))
    return out


def _haversine_km(a, b):
    try:
        lat1, lon1, lat2, lon2 = (float(x) for x in (*a, *b))
    except (TypeError, ValueError):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _same(field, a, b):
    """Change test that ignores formatting-only differences."""
    a, b = str(a or "").strip(), str(b or "").strip()
    if a == b:
        return True
    if field in ("capacity_mw", "size_acres"):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except ValueError:
            return False
    if field == "companies":
        split = lambda s: frozenset(x.strip().lower() for x in s.split(";") if x.strip())
        return split(a) == split(b)
    return _norm_name(a) == _norm_name(b)


# ---------------------------------------------------------------------------
# identity across snapshots
#
# The source's id is NOT a stable key. It renumbered its whole id space on
# 2026-09-17 and again on 2026-09-22 (and partially on 2026-05-01, 05-21 and
# 07-02): id 141 was Project Delta (NC) on the 14th, Millville (NJ) on the
# 17th and Armory Innovation (MO) from the 22nd. Diffing by id reads each
# renumbering as three hundred simultaneous renames, moves and phase changes.
#
# So rows are matched on what the project IS, strongest evidence first:
#   name_state   same normalized name and state, unique on both sides
#   id_name      same id, same state, and names sharing most tokens (an
#                ordinary rename that kept its id)
#   coords       same state, within MATCH_KM and sharing a name token, or
#                within TIGHT_KM outright
#   id_coords    same id, same state, within MOVE_KM (renamed, not moved)
# Anything left over is a genuinely new project. Every match records its
# basis, and every id change it implies is written to the crosswalk.
# ---------------------------------------------------------------------------

MATCH_KM = 1.5
TIGHT_KM = 0.25
_GENERIC = {"data", "center", "centre", "campus", "project", "the", "of", "and",
            "technology", "tech", "park", "digital", "dc", "ai", "site", "llc", "inc"}


def _tokens(s):
    return frozenset(t for t in _norm_name(s).split() if t not in _GENERIC)


def _jacc(a, b):
    a, b = _tokens(a), _tokens(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_rows(prev, cur):
    """prev, cur: {source_id: row}. Returns {cur_id: (prev_id, basis)}."""
    out, used = {}, set()
    key = lambda r: (_norm_name(r.get("name")), str(r.get("state", "")).strip().lower())
    pk, ck = Counter(key(r) for r in prev.values()), Counter(key(r) for r in cur.values())
    by_key = {key(r): i for i, r in prev.items() if pk[key(r)] == 1}
    for cid, r in cur.items():
        k = key(r)
        if k[0] and ck[k] == 1 and k in by_key and by_key[k] not in used:
            out[cid] = (by_key[k], "name_state")
            used.add(by_key[k])
    for cid, r in cur.items():
        if cid in out or cid not in prev or cid in used:
            continue
        p = prev[cid]
        if p.get("state") == r.get("state") and _jacc(p.get("name"), r.get("name")) >= 0.5:
            out[cid] = (cid, "id_name")
            used.add(cid)
    for cid, r in cur.items():
        if cid in out:
            continue
        best = None
        for pid, p in prev.items():
            if pid in used or p.get("state") != r.get("state"):
                continue
            d = _haversine_km((p.get("lat"), p.get("lon")), (r.get("lat"), r.get("lon")))
            if d is None:
                continue
            j = _jacc(p.get("name"), r.get("name"))
            if d <= TIGHT_KM or (d <= MATCH_KM and j > 0):
                score = (j, -d)
                if best is None or score > best[0]:
                    best = (score, pid)
        if best:
            out[cid] = (best[1], "coords")
            used.add(best[1])
    for cid, r in cur.items():
        if cid in out or cid not in prev or cid in used:
            continue
        p = prev[cid]
        d = _haversine_km((p.get("lat"), p.get("lon")), (r.get("lat"), r.get("lon")))
        if p.get("state") == r.get("state") and d is not None and d <= MOVE_KM:
            out[cid] = (cid, "id_coords")
            used.add(cid)
    return out


# ---------------------------------------------------------------------------
# the diff
# ---------------------------------------------------------------------------

def build_events(snapshots, names_by_new_id=None):
    """snapshots: [(day, commit, rows)] oldest first.

    Returns (events, state, meta). `state` is keyed by entity, and each entity
    carries its CURRENT source id; events are relabeled to that id at the end
    so every row of the log joins to today's data/proposals.csv.
    """
    names_by_new_id = names_by_new_id or {}
    events = []
    state = {}           # entity -> carried view
    meta = {"snapshots": [], "degraded": [], "crosswalk": [], "renumberings": [],
            "reclassifications": []}
    prev_rows = prev_keyed = None
    prev_ent = {}        # source id -> entity, as of the previous snapshot
    present_prev = set() # entities present in the previous snapshot
    prev_day = None
    n_ent = 0
    departed, last_row = set(), {}

    for i, (day, sha, rows) in enumerate(snapshots):
        keyed = {}
        for r in rows:
            sid = translate_id(r.get("id"), r.get("name"), day, names_by_new_id)
            if sid:
                keyed[sid] = r
        deg = degraded_fields(prev_rows, rows, ("phase",) + WATCHED_FIELDS) if prev_rows else []
        deg_names = {f for f, _, _ in deg}
        partial = bool(prev_rows) and len(rows) < len(prev_rows) * (1 - DROP_RATIO)
        if deg or partial:
            meta["degraded"].append({
                "snapshot": day, "commit": sha[:10],
                "fields": [{"field": f, "prior": p, "now": n} for f, p, n in deg],
                "partial_fetch": partial, "rows": len(rows),
                "prior_rows": len(prev_rows) if prev_rows else None})
        meta["snapshots"].append({"snapshot": day, "commit": sha[:10], "rows": len(rows)})
        baseline = i == 0

        matches = match_rows(prev_keyed, keyed) if prev_keyed else {}
        # Projects that dropped out earlier are matched too, so a return is a
        # reappearance of the same project rather than a new one.
        ghosts = {}
        for ent in departed:
            g = last_row[ent]
            ghosts.setdefault(str(g.get("_sid")), (ent, g))
        if ghosts:
            rest = {k: v for k, v in keyed.items() if k not in matches}
            for sid, (gsid, basis) in match_rows({k: v[1] for k, v in ghosts.items()},
                                                 rest).items():
                matches[sid] = ("\0ghost", ghosts[gsid][0], basis)
        cur_ent = {}
        n_changed = 0
        for sid, r in keyed.items():
            if sid in matches and matches[sid][0] == "\0ghost":
                _, ent, basis = matches[sid]
                old_sid = state[ent].get("sid", sid)
                if old_sid != sid:
                    meta["crosswalk"].append({
                        "snapshot": day, "prev_snapshot": state[ent].get("last_seen", ""),
                        "old_id": old_sid, "new_id": sid,
                        "name": r.get("name", ""), "state": r.get("state", ""),
                        "basis": basis + "_after_absence"})
            elif sid in matches:
                old_sid, basis = matches[sid]
                ent = prev_ent[old_sid]
                if old_sid != sid:
                    n_changed += 1
                    meta["crosswalk"].append({
                        "snapshot": day, "prev_snapshot": prev_day,
                        "old_id": old_sid, "new_id": sid,
                        "name": r.get("name", ""), "state": r.get("state", ""),
                        "basis": basis})
            else:
                n_ent += 1
                ent = f"e{n_ent}"
            cur_ent[sid] = ent
        if n_changed:
            meta["renumberings"].append({"snapshot": day, "prev_snapshot": prev_day,
                                         "ids_changed": n_changed, "rows": len(rows)})

        def emit(ent, sid, etype, field="", old="", new=""):
            events.append({"seen_date": day, "_ent": ent, "source_id": sid,
                           "event_type": etype, "field": field,
                           "old_value": old, "new_value": new,
                           "prev_snapshot": prev_day or "", "snapshot": day,
                           "commit": sha[:10]})

        for sid, r in keyed.items():
            ent = cur_ent[sid]
            st = state.get(ent)
            if st is None:
                st = state[ent] = {
                    "in_baseline": baseline, "first_seen": day,
                    "phase": r.get("phase", ""), "phase_since": None if baseline else day,
                    "phase_path": [r.get("phase", "")], "n_phase_changes": 0,
                    "last_phase_change": "", "prior_phase": "",
                    "n_field_changes": 0, "last_change": "", "n_snapshots": 0,
                    "vals": {f: r.get(f, "") for f in WATCHED_FIELDS},
                    "coords": (r.get("lat"), r.get("lon")),
                    "name": r.get("name", ""), "state": r.get("state", ""),
                    "ids": [sid]}
                if not baseline:
                    emit(ent, sid, "first_seen", new=r.get("phase", ""))
            elif ent not in present_prev:
                emit(ent, sid, "reappeared", new=r.get("phase", ""))
            if st["ids"][-1] != sid:
                st["ids"].append(sid)
            t = SNAPSHOT_TIMES.get(day, day)
            spans = st.setdefault("spans", [])
            if spans and spans[-1][0] == sid:
                spans[-1][2] = t
            else:
                spans.append([sid, t, t])
            st["sid"] = sid
            st["n_snapshots"] += 1
            st["last_seen"] = day
            st["name"] = r.get("name", "") or st["name"]
            st["state"] = r.get("state", "") or st["state"]

            ph = r.get("phase", "")
            if "phase" not in deg_names and ph and ph != st["phase"]:
                emit(ent, sid, "phase_change", "phase", st["phase"], ph)
                st["prior_phase"], st["phase"] = st["phase"], ph
                st["phase_path"].append(ph)
                st["n_phase_changes"] += 1
                st["last_phase_change"] = st["phase_since"] = day
                st["last_change"] = day
            for f in WATCHED_FIELDS:
                if f in deg_names:
                    continue              # carry the last good value
                new = r.get(f, "")
                if not _same(f, st["vals"].get(f, ""), new):
                    old = st["vals"].get(f, "")
                    st["vals"][f] = new
                    emit(ent, sid, "field_change", f, old, new)
                    st["n_field_changes"] += 1
                    st["last_change"] = day
            c = (r.get("lat"), r.get("lon"))
            d = _haversine_km(st["coords"], c)
            if d is not None and d > MOVE_KM:
                emit(ent, sid, "moved", "latlon", f"{st['coords'][0]},{st['coords'][1]}",
                     f"{c[0]},{c[1]}")
                st["last_change"] = day
            if all(_present(x) for x in c):
                st["coords"] = c
            last_row[ent] = dict(r, _sid=sid)

        # A source vocabulary change looks like a mass phase move: on
        # 2026-09-22, 32 of 34 "approved" projects became "proposed" in one
        # night because the source stopped using the value, not because 32
        # approvals were rescinded. A move shared by RECLASS_MIN projects that
        # empties at least half of its origin phase is relabeled, so it is
        # visible as what it is and never counted as a transition.
        today_moves = Counter((e["old_value"], e["new_value"]) for e in events
                              if e["snapshot"] == day and e["event_type"] == "phase_change")
        if prev_rows:
            prior_phase_n = Counter(r.get("phase", "") for r in prev_rows)
            for (a, b), n in today_moves.items():
                if n >= RECLASS_MIN and n >= 0.5 * prior_phase_n.get(a, 0):
                    meta["reclassifications"].append(
                        {"snapshot": day, "from": a, "to": b, "n": n,
                         "prior_population": prior_phase_n.get(a, 0)})
                    for e in events:
                        if (e["snapshot"] == day and e["event_type"] == "phase_change"
                                and e["old_value"] == a and e["new_value"] == b):
                            e["event_type"] = "source_reclassified"
                            st = state[e["_ent"]]
                            st["n_phase_changes"] -= 1
                            st["phase_path"][-1] = f"{b} (reclassified from {a})"

        present_now = set(cur_ent.values())
        if not partial:
            for ent in present_prev - present_now:
                emit(ent, state[ent].get("sid", ""), "removed", old=state[ent]["phase"])
                departed.add(ent)
        else:
            present_now |= present_prev
        departed -= present_now
        present_prev = present_now
        prev_rows, prev_keyed, prev_day = rows, keyed, day
        # A partial fetch keeps the previous id map for the projects it missed.
        prev_ent = ({**prev_ent, **cur_ent} if partial else cur_ent)

    for ent, st in state.items():
        st["present_now"] = ent in present_prev
        st["pid"] = st.get("sid", "")
    for e in events:
        e["project_id"] = f"prj_{state[e.pop('_ent')]['pid']}"
    return events, {st["pid"] + ("" if st["present_now"] else f"~{ent}"): st
                    for ent, st in state.items()}, meta


def project_rows(state, as_of):
    out = []
    for pid, st in sorted(state.items(), key=lambda kv: (len(kv[0]), kv[0])):
        since = st["phase_since"]
        days = ""
        if since:
            days = (date.fromisoformat(as_of) - date.fromisoformat(since)).days
        out.append({
            "project_id": f"prj_{st['pid']}", "name": st["name"], "state": st["state"],
            "source_ids": " > ".join(st["ids"]),
            "phase": st["phase"], "in_baseline": "yes" if st["in_baseline"] else "no",
            "first_seen": st["first_seen"], "last_seen": st.get("last_seen", ""),
            "present_now": "yes" if st["present_now"] else "no",
            "n_snapshots": st["n_snapshots"],
            "n_phase_changes": st["n_phase_changes"],
            "phase_path": " > ".join(st["phase_path"]),
            "last_phase_change": st["last_phase_change"],
            "prior_phase": st["prior_phase"],
            # Unknown for baseline projects that never changed phase: the
            # panel starts on the baseline date, so "since" is left-censored.
            "phase_since": since or "",
            "days_in_phase": days,
            "n_field_changes": st["n_field_changes"],
            "last_change": st["last_change"]})
    return out


def timeline_rows(state):
    """Which source id each project carried over which snapshot interval.

    This is what lets a hand-maintained row keyed on a source id be resolved
    to the project it was written about: find the span holding that id at the
    time the row was written. See project_id_rekey.py.
    """
    out = []
    for key, st in state.items():
        for sid, t0, t1 in st.get("spans", []):
            out.append({"entity": key, "source_id": sid, "first_snapshot": t0,
                        "last_snapshot": t1, "current_id": st["pid"],
                        "present_now": "yes" if st["present_now"] else "no",
                        "name": st["name"], "state": st["state"]})
    out.sort(key=lambda r: (r["first_snapshot"], len(r["source_id"]), r["source_id"]))
    return out


def summarize(events, state, meta):
    weekly = defaultdict(Counter)
    for e in events:
        d = date.fromisoformat(e["seen_date"])
        wk = (d.toordinal() - d.weekday())
        weekly[date.fromordinal(wk).isoformat()][e["event_type"]] += 1
    transitions = Counter((e["old_value"], e["new_value"]) for e in events
                          if e["event_type"] == "phase_change")
    snaps = meta["snapshots"]
    return {
        "_generated": date.today().isoformat(),
        "_note": ("seen dates are when the source changed: an upper bound on "
                  "when the underlying change happened, never the date itself."),
        "panel_start": snaps[0]["snapshot"] if snaps else None,
        "panel_end": snaps[-1]["snapshot"] if snaps else None,
        "n_snapshots": len(snaps),
        "baseline_projects": sum(1 for s in state.values() if s["in_baseline"]),
        "projects_ever_seen": len(state),
        "projects_present_now": sum(1 for s in state.values() if s["present_now"]),
        "event_counts": dict(Counter(e["event_type"] for e in events)),
        "field_change_counts": dict(Counter(e["field"] for e in events
                                            if e["event_type"] == "field_change")),
        "phase_transitions": [{"from": a or "(blank)", "to": b, "n": n}
                              for (a, b), n in transitions.most_common()],
        "weekly": [{"week": wk, **dict(c)} for wk, c in sorted(weekly.items())],
        "degraded_snapshots": meta["degraded"],
        "source_renumberings": meta["renumberings"],
        "source_reclassifications": meta["reclassifications"],
        "snapshots": snaps,
    }


def write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run(cwd=HERE):
    snaps = list_snapshots(cwd=cwd)
    if not snaps:
        raise SystemExit("proposal_history: no history for data/proposals.csv. "
                         "Is this a shallow clone? CI needs fetch-depth: 0.")
    loaded = [(day, sha, read_snapshot(sha, cwd=cwd)) for day, sha in snaps]
    events, state, meta = build_events(loaded, migration_names())
    as_of = snaps[-1][0]
    write_csv(OUT_EVENTS, EVENT_FIELDS, events)
    write_csv(OUT_PROJECTS, PROJECT_FIELDS, project_rows(state, as_of))
    write_csv(OUT_CROSSWALK, CROSSWALK_FIELDS, meta["crosswalk"])
    write_csv(OUT_TIMELINE, TIMELINE_FIELDS, timeline_rows(state))
    summ = summarize(events, state, meta)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summ, fh, indent=1)
        fh.write("\n")
    print(f"proposal_history: {len(snaps)} daily snapshots "
          f"{summ['panel_start']} -> {summ['panel_end']}; "
          f"{len(events)} events {summ['event_counts']}; "
          f"{len(meta['degraded'])} degraded snapshot(s); "
          f"{len(meta['renumberings'])} snapshot(s) where the source changed ids "
          f"({sum(r['ids_changed'] for r in meta['renumberings'])} id changes)")
    return summ


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest():
    checks = []

    def check(label, ok):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    def row(i, phase="proposed", cap="100", name=None, lat=None, lon="-80", **kw):
        lat = lat if lat is not None else f"{40 + i * 0.05:.4f}"
        r = {"id": str(i), "name": name or f"Project {i}", "phase": phase,
             "capacity_mw": cap, "size_acres": "50", "companies": "DevCo",
             "lat": lat, "lon": lon, "state": "PA"}
        r.update(kw)
        return r

    base = [row(i) for i in range(1, 31)]
    day2 = [dict(r) for r in base]
    day2[0]["phase"] = "withdrawn"
    day2.append(row(99))
    day2 = [r for r in day2 if r["id"] != "2"]
    day3 = [dict(r) for r in day2] + [row(2)]
    ev, st, meta = build_events([("2026-05-01", "a" * 40, base),
                                 ("2026-05-02", "b" * 40, day2),
                                 ("2026-05-03", "c" * 40, day3)])
    kinds = Counter(e["event_type"] for e in ev)
    check("baseline projects are not first_seen events", kinds["first_seen"] == 1)
    check("a phase move is recorded with old and new",
          any(e["event_type"] == "phase_change" and e["old_value"] == "proposed"
              and e["new_value"] == "withdrawn" for e in ev))
    check("a project that disappears is removed", kinds["removed"] == 1)
    check("and one that returns is reappeared", kinds["reappeared"] == 1)
    check("time in phase starts at the change", st["1"]["phase_since"] == "2026-05-02")
    check("baseline phase_since is left-censored, not invented",
          st["3"]["phase_since"] is None)

    # 2026-09-10: six columns emptied in one committed run, then restored.
    broken = [dict(r, capacity_mw="") for r in base]
    ev2, _, meta2 = build_events([("2026-09-09", "a" * 40, base),
                                  ("2026-09-10", "b" * 40, broken),
                                  ("2026-09-11", "c" * 40, base)])
    check("a collapsed column records no field changes",
          not [e for e in ev2 if e["field"] == "capacity_mw"])
    check("and the snapshot is reported degraded with its counts",
          meta2["degraded"][0]["fields"][0] == {"field": "capacity_mw",
                                                "prior": 30, "now": 0})
    real = [dict(r) for r in base]
    real[4]["capacity_mw"] = "250"
    ev3, _, _ = build_events([("d1", "a" * 40, base), ("d2", "b" * 40, real)])
    check("a real single-project change is still recorded",
          [e["new_value"] for e in ev3 if e["field"] == "capacity_mw"] == ["250"])
    check("formatting-only numeric differences are not changes",
          not build_events([("d1", "a" * 40, [row(1, cap="100")]),
                            ("d2", "b" * 40, [row(1, cap="100.0")])])[0])
    check("company order is not a change",
          not build_events([("d1", "a" * 40, [row(1, companies="A; B")]),
                            ("d2", "b" * 40, [row(1, companies="B;A")])])[0])

    partial = base[:10]
    ev4, _, meta4 = build_events([("d1", "a" * 40, base), ("d2", "b" * 40, partial)])
    check("a partial fetch records no removals",
          not [e for e in ev4 if e["event_type"] == "removed"])
    check("and is reported as a partial fetch", meta4["degraded"][0]["partial_fetch"])

    moved = [row(1, lat="40.3")]
    ev5, _, _ = build_events([("d1", "a" * 40, [row(1)]), ("d2", "b" * 40, moved)])
    check("a move over MOVE_KM is recorded", [e["event_type"] for e in ev5] == ["moved"])

    # 2026-09-02 id migration.
    names = {"1001": _norm_name("Harper Road Technology Park")}
    pre = [row(321, name="Harper Road Technology Park", lat="38.7")]
    post = [row(1001, name="Harper Road Technology Park", lat="38.7"),
            row(321, name="PNK Valley View Data Center", lat="41.2")]
    ev6, st6, _ = build_events([("2026-08-30", "a" * 40, pre),
                                ("2026-09-03", "b" * 40, post)], names)
    check("a migrated manual id is the same project across the migration",
          "1001" in st6 and st6["1001"]["in_baseline"])
    check("the reused id is a new project, not a rename of the old one",
          [e["event_type"] for e in ev6] == ["first_seen"]
          and ev6[0]["project_id"] == "prj_321")
    check("a pre-migration id without the name match is not translated",
          translate_id("321", "Something Else", "2026-08-30", names) == "321")
    check("post-migration ids are never translated",
          translate_id("321", "Harper Road Technology Park", "2026-09-10", names) == "321")

    # 2026-09-17: the source renumbered every id. Same projects, new ids.
    shuffled = [dict(r, id=str(int(r["id"]) + 500)) for r in base]
    shuffled[0]["phase"] = "approved"
    ev7, st7, meta7 = build_events([("2026-09-14", "a" * 40, base),
                                    ("2026-09-17", "b" * 40, shuffled)])
    check("a renumbering is not read as mass change",
          Counter(e["event_type"] for e in ev7) == Counter({"phase_change": 1}))
    check("the real change inside it survives, under the current id",
          ev7[0]["project_id"] == "prj_501" and ev7[0]["new_value"] == "approved")
    check("every id change is written to the crosswalk",
          len(meta7["crosswalk"]) == 30 and meta7["crosswalk"][0]["old_id"] == "1"
          and meta7["crosswalk"][0]["new_id"] == "501")
    check("and the renumbering is reported", meta7["renumberings"][0]["ids_changed"] == 30)
    check("a project keeps its id history", st7["501"]["ids"] == ["1", "501"])
    swap = [dict(r) for r in base]
    swap[0], swap[1] = dict(swap[0], id="2"), dict(swap[1], id="1")
    ev8, _, m8 = build_events([("d1", "a" * 40, base), ("d2", "b" * 40, swap)])
    check("two projects trading ids are matched by what they are, not by id",
          not ev8 and len(m8["crosswalk"]) == 2)
    renamed = [dict(r) for r in base]
    renamed[3]["name"] = "Project 4 Phase Two"
    ev9, _, m9 = build_events([("d1", "a" * 40, base), ("d2", "b" * 40, renamed)])
    check("an ordinary rename is a name change, not a new project",
          [e["event_type"] for e in ev9] == ["field_change"] and not m9["crosswalk"])

    many = [row(i, phase="approved") for i in range(1, 25)] + [row(i) for i in range(25, 40)]
    folded = [dict(r, phase="proposed") for r in many]
    folded[0]["phase"] = "approved"
    ev10, st10, m10 = build_events([("d1", "a" * 40, many), ("d2", "b" * 40, folded)])
    check("a source vocabulary change is relabeled, not counted as transitions",
          Counter(e["event_type"] for e in ev10) == Counter({"source_reclassified": 23})
          and m10["reclassifications"][0]["n"] == 23)
    check("and does not count as a phase change on the project",
          st10["2"]["n_phase_changes"] == 0)

    rows = project_rows(st, "2026-05-10")
    r1 = next(r for r in rows if r["project_id"] == "prj_1")
    check("days in phase count from the change", r1["days_in_phase"] == 8)
    check("the phase path is readable", r1["phase_path"] == "proposed > withdrawn")
    s = summarize(ev, st, meta)
    check("transitions are counted", s["phase_transitions"][0]["n"] == 1)
    check("weekly flow is keyed by Monday", s["weekly"][0]["week"] == "2026-04-27")

    n_ok = sum(checks)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    run()


if __name__ == "__main__":
    main()
