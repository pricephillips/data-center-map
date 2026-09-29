#!/usr/bin/env python3
"""
status_resolution.py

Closes the gap between "a harvested row said a vote was coming" and "the vote
happened", which the pipeline had no path for.

Why this module exists
----------------------
promote_signal_candidates.py writes every harvested headline as Status
"pending" with an empty outcome, by design: a headline is not proof of an
outcome. The docstring there says outcome fields are "filled later by the
normal update paths". In practice there was no such path for recent rows:

  - stale_pending_audit.py only flags a moratorium row after 120 days, so a
    row that resolved in week one sat pending for four months;
  - each follow-up article became its OWN row (the headline is the Incident,
    so the headline slug is the project_id), so "Palm Beach moves toward a
    moratorium", "gives initial approval" and "unanimously approves" were three
    unrelated pending projects, and the final vote never updated anything.

Found 2026-09-28: Palm Beach, Levy and Wakulla counties and the City of
Pinellas Park had each adopted a restriction, confirmed in the cited source,
while every one of their rows was still pending. None reached the county label,
so the county restriction count understated Florida.

What it does
------------
  scan (default)  Reads master_opposition.csv. Flags pending local restrictive
                  rows whose own headline or summary reports a completed
                  adoption, and groups same-county pending rows within 120
                  days so earlier-stage coverage of the same instrument is
                  proposed as superseded. Writes
                  data/status_resolution_worklist.csv. Proposes only.

                  Syndicated copies (spec 006): pending rows that carry the
                  same story under different outlets' URLs (one AP or
                  group-owned article on several mastheads) are clustered by
                  event_dedupe.py and every copy but the earliest is proposed
                  as supersede, signal "syndicated copy", with the cluster id.
                  Same worklist, same reviewer confirmation, same
                  hold_superseded(); there is no second mechanism. Needs
                  datasketch; without it the pass is skipped and the worklist
                  is what it was.

  --auto-supersede
                  Confirms the syndicated-copy supersede proposals without a
                  reviewer (owner decision, 2026-09-29): appends one supersede
                  row per copy to data/status_resolutions.csv, naming the kept
                  row as superseded_by and evidence_url, with the cluster id in
                  the note. Only the "syndicated copy" signal is automated;
                  earlier-stage coverage and resolve proposals still need a
                  reviewer. Reversible: rows stay in master, and changing the
                  action of an auto row to "keep" returns the copy to the feed
                  and stops it being proposed again. Needs datasketch.

  --apply         Applies data/status_resolutions.csv, the reviewer-confirmed
                  file, to master_opposition.csv. Idempotent: re-running
                  changes nothing. Rows are matched on normalized Source URL.

  hold_superseded(df)
                  Imported by build_clean_feed.py. Rows marked "supersede" stay
                  in master_opposition.csv (nothing is deleted) and are held
                  out of the clean feed, so one instrument covered by three
                  articles counts once.

Defensibility rule
------------------
A headline never changes a status by itself. The scan proposes; only a row in
data/status_resolutions.csv, which names the evidence URL and the date it was
confirmed, is applied. This keeps the harvest's "promotion never asserts an
outcome" rule intact and moves the confirmation from four months to one run.

data/status_resolutions.csv columns
  source_url        Source URL of the master row(s) to change (matched normalized)
  action            resolve | supersede | correct_geo | keep
                    (keep: no change; stops the row being proposed or
                    auto-superseded again)
  status            new Status (resolve)
  opposition_type   new Opposition Type (resolve, optional)
  authority_level   new Authority Level (optional)
  community_outcome new raw Community Outcome (optional; raw schema vocabulary)
  state, county     corrected geography (correct_geo, or alongside resolve)
  superseded_by     for supersede: Source URL of the row that carries the outcome
  evidence_url      where the outcome was confirmed (required for resolve)
  confirmed_on      ISO date the evidence was read (required)
  note              free text

Standing rules: stdlib only, additive, LF line endings on its own outputs,
master_opposition.csv keeps its existing CRLF terminator, no em-dashes, no
scorekeeping vocabulary in generated text, --selftest with no data or network.

Usage
  python status_resolution.py                  scan, write the worklist
  python status_resolution.py --auto-supersede confirm syndicated copies
  python status_resolution.py --apply          apply confirmed resolutions
  python status_resolution.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import os
import re
import sys
from collections import defaultdict

try:
    import event_dedupe as ED
except ImportError:                          # pragma: no cover - same repo
    ED = None

HERE = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(HERE, *parts)


MASTER_CSV = P("master_opposition.csv")
RESOLUTIONS_CSV = P("data", "status_resolutions.csv")
WORKLIST_CSV = P("data", "status_resolution_worklist.csv")

ACTIONS = ("resolve", "supersede", "correct_geo", "keep")
AUTO_NOTE = "auto-confirmed syndicated copy"
RESOLUTION_FIELDS = ["source_url", "action", "status", "opposition_type",
                     "authority_level", "community_outcome", "state", "county",
                     "superseded_by", "evidence_url", "confirmed_on", "note"]
WORKLIST_FIELDS = ["proposal", "signal", "state", "county", "date", "incident",
                   "source_url", "group_key", "group_final_url",
                   # Appended by spec 006 (FR-004); existing columns unchanged.
                   "cluster_id", "archived_url"]
ARCHIVE_CSV = P("data", "source_archive.csv")
SYNDICATED = "syndicated copy"

RESTRICTIVE_TYPES = {"moratorium", "zoning_restriction", "ban", "ordinance"}

# The raw schema records direction in Community Outcome, and both
# outcome_defensibility.classify_record and county_aggregator's "approved"
# guard read it: a restrictive instrument with a terminal status and an empty
# outcome stays "pending" forever. A confirmed adoption of a restrictive
# instrument is, in the raw schema's own vocabulary, the restriction side
# prevailing, so resolve rows fill it when the reviewer leaves it blank. This is
# an internal raw-column value (leak_audit INTERNAL_QUOTES), never output text.
RAW_RESTRICTION_ADOPTED = "win"
ADOPTING_STATUSES = {"passed", "approved", "enacted", "active", "extended"}
SUPERSEDE_WINDOW_DAYS = 120

# A completed adoption, stated in the past or present tense.
FINAL = re.compile(
    r"\b(unanimously (approve[sd]?|adopt(s|ed)?|pass(es|ed)?|vote[sd]?)|"
    r"approve[sd]?|adopt(s|ed)?|pass(es|ed)?|enact(s|ed)?|impose[sd]?|"
    r"extend(s|ed)?|halts?|officially adopts?|votes? (to|for) "
    r"(approve|adopt|enact|impose|extend|pass))\b", re.I)
# The instrument must be restrictive, or the verb may describe a project.
INSTRUMENT = re.compile(r"\b(moratori(um|a)|ban|prohibit\w*|pause)\b", re.I)
# Any of these means the vote was not final, or not a restriction.
NOT_FINAL = re.compile(
    r"\b(initial|first reading|preliminary|tentative(ly)?|consider(s|ing)?|"
    r"weigh(s|ing)?|to vote|will vote|set to|plans? to|propos(e|es|ed|al)|"
    r"draft(s|ing)?|directs? staff|move[sd]? toward|towards?|hearing|"
    r"could|may|seeks?|push(es)? for|calls? for|urge[sd]?|asks?|"
    r"against|reject(s|ed)?|den(y|ies|ied)|table[sd]?|fail(s|ed)?|"
    r"lift(s|ed)?|end(s|ed)?|rescind(s|ed)?|expire[sd]?|"
    r"cannot|can't|unable|lawsuit|sues?|claims?|"
    r"to (be )?(enact|extend|adopt|approve|impose|pass)\w*)\b", re.I)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def normalize_url(u: str) -> str:
    """Same normalization as signal_harvest.normalize_url, kept local so the
    selftest has no import dependency."""
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("?")[0].split("#")[0].rstrip("/")


def parse_date(v: str):
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return dt.datetime.strptime(v[:10] if fmt == "%Y-%m-%d" else v[:7],
                                        fmt).date()
        except ValueError:
            continue
    return None


def norm_county(c: str) -> str:
    s = (c or "").lower()
    s = re.sub(r"\b(county|parish|borough)\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def detect_terminator(path: str) -> str:
    with open(path, "rb") as fh:
        head = fh.read(65536)
    return "\r\n" if b"\r\n" in head else "\n"


def final_signal(row: dict) -> str:
    """The matched adoption phrase, or "" when the row does not report one."""
    text = " ".join(str(row.get(k) or "") for k in ("Incident", "Summary"))
    if not INSTRUMENT.search(text) or NOT_FINAL.search(text):
        return ""
    m = FINAL.search(text)
    return m.group(0) if m else ""


def is_candidate(row: dict) -> bool:
    status = (row.get("Status") or "").strip().lower()
    scope = (row.get("Scope") or "").strip().lower()
    toks = {t.strip().lower() for t in (row.get("Opposition Type") or "").split(";")}
    return (status == "pending" and scope not in ("statewide", "federal")
            and bool(toks & RESTRICTIVE_TYPES)
            and bool((row.get("County") or "").strip()))


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def scan(master: list[dict], resolutions: list[dict],
         archive: dict | None = None) -> list[dict]:
    done = {normalize_url(r.get("source_url", "")) for r in resolutions}
    seen_urls = set()
    groups = defaultdict(list)
    for r in master:
        if not is_candidate(r):
            continue
        u = normalize_url(r.get("Source URL", ""))
        if not u or u in seen_urls:
            continue       # harvest duplicates repeat the same URL
        seen_urls.add(u)
        key = ((r.get("State") or "").strip().upper(),
               norm_county(r.get("County", "")))
        groups[key].append(r)

    out = []
    for key, rows in groups.items():
        rows.sort(key=lambda r: parse_date(r.get("Date", "")) or dt.date.min)
        finals = [r for r in rows if final_signal(r)]
        for f in finals:
            fd = parse_date(f.get("Date", ""))
            fu = normalize_url(f.get("Source URL", ""))
            if fu not in done:
                out.append(_wl("resolve", final_signal(f), f, key, f))
            for r in rows:
                if r is f or final_signal(r):
                    continue
                rd = parse_date(r.get("Date", ""))
                if fd and rd and 0 <= (fd - rd).days <= SUPERSEDE_WINDOW_DAYS \
                        and normalize_url(r.get("Source URL", "")) not in done:
                    out.append(_wl("supersede", "earlier-stage coverage", r, key, f))
    proposed = {normalize_url(r["source_url"]) for r in out}
    out.extend(syndicated(master, done | proposed))
    arch = archive or {}
    for r in out:
        r["archived_url"] = arch.get(normalize_url(r["source_url"]), "")
    out.sort(key=lambda r: (r["proposal"] != "resolve", r["state"], r["county"],
                            r["date"]))
    return out


def syndicated(master: list[dict], skip: set) -> list[dict]:
    """supersede proposals for syndicated copies among pending rows.

    Every pending row with a Source URL (first occurrence of each normalized
    URL) is an item: Incident as the title, domain from the URL. In each
    event_dedupe cluster the earliest-dated row, then the first in file
    order, is kept; the others are proposed unless already resolved or
    already proposed by the county pass.
    """
    if ED is None or not ED.available():
        return []
    items, seen = [], set()
    for i, r in enumerate(master):
        if (r.get("Status") or "").strip().lower() != "pending":
            continue
        url = (r.get("Source URL") or "").strip()
        u = normalize_url(url)
        if not u or u in seen:
            continue
        seen.add(u)
        items.append({"id": i, "title": r.get("Incident") or "",
                      "date": (r.get("Date") or "").strip()[:10],
                      "domain": ED.domain_of(url),
                      "state": (r.get("State") or "").strip()})
    out = []
    for grp in ED.cluster(items):
        if len(grp) < 2:
            continue
        keeper = min(grp, key=lambda i: (parse_date(master[i].get("Date", ""))
                                         or dt.date.max, i))
        cid = ED.cluster_id(master[keeper].get("Source URL", ""))
        for i in sorted(grp):
            if i == keeper or normalize_url(master[i].get("Source URL", "")) in skip:
                continue
            w = _wl("supersede", SYNDICATED, master[i], ("cluster", cid), master[keeper])
            w["cluster_id"] = cid
            out.append(w)
    return out


def auto_supersede_rows(master: list[dict], resolutions: list[dict],
                        today: dt.date | None = None) -> list[dict]:
    """status_resolutions.csv rows confirming every current syndicated-copy
    proposal. Rows already in the file (any action, "keep" included) are
    never proposed, so this is idempotent and a reviewer's override holds."""
    today = today or dt.date.today()
    done = {normalize_url(r.get("source_url", "")) for r in resolutions}
    out = []
    for w in syndicated(master, done):
        out.append({"source_url": w["source_url"], "action": "supersede",
                    "superseded_by": w["group_final_url"],
                    "evidence_url": w["group_final_url"],
                    "confirmed_on": today.isoformat(),
                    "note": f"{AUTO_NOTE} ({w['cluster_id']}); "
                            f"set action to keep to return it to the feed"})
    return out


def append_resolutions(rows: list[dict], path: str = RESOLUTIONS_CSV) -> int:
    """Appends rows, keeping every existing byte and the file's own line
    terminator. Writes the header only when the file is new."""
    if not rows:
        return 0
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    term = "\n" if new else detect_terminator(path)
    if not new:
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            needs_nl = fh.read(1) not in (b"\n", b"\r")
    with open(path, "a", newline="", encoding="utf-8") as fh:
        if not new and needs_nl:
            fh.write(term)
        w = csv.DictWriter(fh, fieldnames=RESOLUTION_FIELDS, lineterminator=term,
                           extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in RESOLUTION_FIELDS})
    return len(rows)


def load_archive(path: str = ARCHIVE_CSV) -> dict:
    """normalized URL -> archived_url, from source_archive.py's output."""
    return {normalize_url(r.get("url", "")): r.get("archived_url", "")
            for r in read_csv(path) if r.get("archived_url")}


def _wl(proposal, signal, row, key, final_row) -> dict:
    return {"proposal": proposal, "signal": signal,
            "cluster_id": "", "archived_url": "",
            "state": (row.get("State") or "").strip(),
            "county": (row.get("County") or "").strip(),
            "date": (row.get("Date") or "").strip(),
            "incident": (row.get("Incident") or "").strip()[:200],
            "source_url": (row.get("Source URL") or "").strip(),
            "group_key": f"{key[0]}|{key[1]}",
            "group_final_url": (final_row.get("Source URL") or "").strip()}


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def validate(resolutions: list[dict]) -> tuple[list[dict], list[str]]:
    ok, errors = [], []
    for i, r in enumerate(resolutions, start=2):
        a = (r.get("action") or "").strip()
        if a not in ACTIONS:
            errors.append(f"line {i}: action {a!r} not in {ACTIONS}")
            continue
        if not normalize_url(r.get("source_url", "")):
            errors.append(f"line {i}: source_url is required")
            continue
        if not parse_date(r.get("confirmed_on", "")):
            errors.append(f"line {i}: confirmed_on must be an ISO date")
            continue
        if a == "resolve" and not ((r.get("status") or "").strip()
                                   and (r.get("evidence_url") or "").strip()):
            errors.append(f"line {i}: resolve needs status and evidence_url")
            continue
        if a == "supersede" and not normalize_url(r.get("superseded_by", "")):
            errors.append(f"line {i}: supersede needs superseded_by")
            continue
        if a == "correct_geo" and not ((r.get("state") or "").strip()
                                       or (r.get("county") or "").strip()):
            errors.append(f"line {i}: correct_geo needs state or county")
            continue
        ok.append(r)
    return ok, errors


FIELD_MAP = (("status", "Status"), ("opposition_type", "Opposition Type"),
             ("authority_level", "Authority Level"),
             ("community_outcome", "Community Outcome"),
             ("state", "State"), ("county", "County"))


def apply(master: list[dict], resolutions: list[dict]) -> tuple[int, int, list[str]]:
    """Apply resolve and correct_geo rows in place. Returns
    (rows_changed, fields_changed, unmatched source urls). supersede rows are
    not written to master; build_clean_feed.py holds them out."""
    by_url = defaultdict(list)
    for r in master:
        by_url[normalize_url(r.get("Source URL", ""))].append(r)
    rows_changed = fields_changed = 0
    unmatched = []
    for res in resolutions:
        a = res["action"].strip()
        targets = by_url.get(normalize_url(res["source_url"]), [])
        if not targets:
            unmatched.append(res["source_url"])
            continue
        if a in ("supersede", "keep"):
            continue
        res = dict(res)
        if (a == "resolve" and not (res.get("community_outcome") or "").strip()
                and (res.get("status") or "").strip().lower() in ADOPTING_STATUSES
                and (res.get("opposition_type") or "").strip().lower() in RESTRICTIVE_TYPES):
            res["community_outcome"] = RAW_RESTRICTION_ADOPTED
        for row in targets:
            touched = False
            for src, dst in FIELD_MAP:
                if a == "correct_geo" and src not in ("state", "county"):
                    continue
                v = (res.get(src) or "").strip()
                if v and dst in row and (row.get(dst) or "").strip() != v:
                    row[dst] = v
                    fields_changed += 1
                    touched = True
            rows_changed += touched
    return rows_changed, fields_changed, unmatched


def superseded_urls_from(rows: list[dict]) -> set:
    return {normalize_url(r["source_url"]) for r in validate(rows)[0]
            if r["action"].strip() == "supersede"}


def superseded_urls(path: str = RESOLUTIONS_CSV) -> set:
    return superseded_urls_from(read_csv(path))


def hold_superseded(df, path: str = RESOLUTIONS_CSV):
    """Called by build_clean_feed.py on the raw DataFrame before cleaning.
    Returns (kept_df, n_held). No-op when the file is absent or empty."""
    urls = superseded_urls(path)
    if not urls or "Source URL" not in df.columns:
        return df, 0
    mask = df["Source URL"].map(lambda u: normalize_url(str(u)) in urls)
    return df[~mask].copy(), int(mask.sum())


def write_master(rows: list[dict], fields: list[str], path: str) -> None:
    term = detect_terminator(path)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator=term,
                       extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(buf.getvalue())


def write_csv(rows: list[dict], path: str, fields: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: expected {want!r}, got {got!r}")

    def row(inc, date, url, county="Palm Beach", typ="moratorium",
            status="pending"):
        return {"Incident": inc, "Summary": inc, "Date": date,
                "Source URL": url, "State": "FL", "County": county,
                "Scope": "", "Opposition Type": typ, "Status": status,
                "Authority Level": "", "Community Outcome": ""}

    check("final: unanimously approve", bool(final_signal(row(
        "Commissioners unanimously approve moratorium on data centers", "", ""))), True)
    check("final: passes", bool(final_signal(row(
        "Levy County Commission passes moratorium for large scale data centers",
        "", ""))), True)
    check("final: halts with moratorium", bool(final_signal(row(
        "Pinellas Park Halts Data Center Projects With Year-Long Moratorium",
        "", ""))), True)
    check("final: approve ordinance prohibiting", bool(final_signal(row(
        "Wakulla County commissioners approve ordinance prohibiting data centers",
        "", ""))), True)
    check("not final: initial approval", final_signal(row(
        "Commissioners give initial approval to 1-year moratorium", "", "")), "")
    check("not final: considers", final_signal(row(
        "Highlands considers data center moratorium", "", "")), "")
    check("not final: move towards", final_signal(row(
        "Palm Beach Is Latest County To Move Towards Data Center Moratorium",
        "", "")), "")
    check("not final: votes against", final_signal(row(
        "Council votes against pausing data center projects", "", "")), "")
    check("not final: future tense", final_signal(row(
        "Eau Claire County to enact a moratorium on new data centers", "", "")), "")
    check("not final: to be extended", final_signal(row(
        "Data center moratorium to be extended in Calvert", "", "")), "")
    check("not final: cannot impose", final_signal(row(
        "Officials say county cannot impose data center moratorium", "", "")), "")
    check("not final: no instrument", final_signal(row(
        "Board approves data center rezoning", "", "")), "")

    master = [
        row("Palm Beach Is Latest County To Move Towards Data Center Moratorium",
            "2026-08-27", "https://a.example/1"),
        row("Commissioners give initial approval to 1-year moratorium",
            "2026-08-28", "https://a.example/2"),
        row("Commissioners unanimously approve moratorium on new data centers",
            "2026-09-24", "https://a.example/3"),
        row("Commissioners unanimously approve moratorium on new data centers",
            "2026-09-24", "https://www.a.example/3/"),   # harvest duplicate
        row("Highlands considers data center moratorium", "2026-08-19",
            "https://b.example/1", county="Highlands"),
    ]
    wl = scan(master, [])
    check("scan: one resolve", sum(1 for r in wl if r["proposal"] == "resolve"), 1)
    check("scan: two supersede", sum(1 for r in wl if r["proposal"] == "supersede"), 2)
    check("scan: resolve first", wl[0]["proposal"], "resolve")

    res = [
        {"source_url": "https://a.example/3", "action": "resolve",
         "status": "passed", "opposition_type": "moratorium",
         "authority_level": "county_commission", "community_outcome": "",
         "state": "", "county": "", "superseded_by": "",
         "evidence_url": "https://a.example/3", "confirmed_on": "2026-09-28",
         "note": ""},
        {"source_url": "https://a.example/2", "action": "supersede",
         "status": "", "opposition_type": "", "authority_level": "",
         "community_outcome": "", "state": "", "county": "",
         "superseded_by": "https://a.example/3", "evidence_url": "",
         "confirmed_on": "2026-09-28", "note": ""},
        {"source_url": "https://b.example/1", "action": "correct_geo",
         "status": "should-be-ignored", "opposition_type": "",
         "authority_level": "", "community_outcome": "", "state": "NC",
         "county": "Macon County", "superseded_by": "", "evidence_url": "",
         "confirmed_on": "2026-09-28", "note": ""},
        {"source_url": "https://a.example/9", "action": "resolve",
         "status": "passed", "confirmed_on": "2026-09-28"},  # no evidence_url
    ]
    ok, errs = validate(res)
    check("validate: rejects resolve without evidence", len(errs), 1)
    changed, nfields, unmatched = apply(master, ok)
    check("apply: both duplicate rows resolved",
          [r["Status"] for r in master[2:4]], ["passed", "passed"])
    check("apply: outcome direction filled on adoption",
          master[2]["Community Outcome"], RAW_RESTRICTION_ADOPTED)
    check("apply: supersede leaves master alone", master[1]["Status"], "pending")
    check("apply: geo corrected", (master[4]["State"], master[4]["County"]),
          ("NC", "Macon County"))
    check("apply: correct_geo ignores status", master[4]["Status"], "pending")
    check("apply: no unmatched", unmatched, [])
    c2, f2, _ = apply(master, ok)
    check("apply: idempotent", (c2, f2), (0, 0))
    check("scan: resolved rows drop out",
          sum(1 for r in scan(master, ok) if r["proposal"] == "resolve"), 0)

    # Spec 006: syndicated copies go through the same supersede path.
    check("worklist: spec 006 columns appended last",
          WORKLIST_FIELDS[-2:] + WORKLIST_FIELDS[:1], ["cluster_id", "archived_url", "proposal"])
    title = "Martin Is Latest Florida County To Consider Moratorium On Data Centers"
    synd = [row(title + " | NewsRadio", "2026-08-26", "https://wiod.example/m", county="Martin"),
            row(title + " | 1290 WJNO", "2026-08-27", "https://wjno.example/m", county="Martin"),
            row("Unrelated county zoning story about warehouses and roads", "2026-08-27",
                "https://other.example/z", county="Martin")]
    arch = {"wjno.example/m": "https://web.archive.org/web/20260827000000/https://wjno.example/m"}
    before = [dict(r) for r in synd]
    if ED is not None and ED.available():
        wl2 = scan(synd, [], archive=arch)
        syn = [r for r in wl2 if r["signal"] == SYNDICATED]
        check("syndicated: the later copy is proposed as supersede",
              [(r["proposal"], r["source_url"]) for r in syn],
              [("supersede", "https://wjno.example/m")])
        check("syndicated: superseded_by points at the earliest copy",
              syn[0]["group_final_url"], "https://wiod.example/m")
        check("syndicated: cluster id carried",
              syn[0]["cluster_id"].startswith("evt_") and syn[0]["group_key"]
              == "cluster|" + syn[0]["cluster_id"], True)
        check("syndicated: archived_url looked up from the archive",
              syn[0]["archived_url"], arch["wjno.example/m"])
        done_res = [{"source_url": "https://wjno.example/m", "action": "supersede"}]
        check("syndicated: an already-confirmed copy drops out",
              [r for r in scan(synd, done_res) if r["signal"] == SYNDICATED], [])
        check("syndicated: master is unchanged", synd, before)

        # --auto-supersede: confirms the copy, idempotently, and a keep
        # override holds.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rp = os.path.join(tmp, "res.csv")
            with open(rp, "w", newline="", encoding="utf-8") as fh:
                fh.write(",".join(RESOLUTION_FIELDS) + "\n")
                fh.write("https://x.example/1,resolve,passed,,,,,,,https://x.example/1,"
                         "2026-09-28,hand row")   # no trailing newline
            before_bytes = open(rp, "rb").read()
            rows = auto_supersede_rows(synd, read_csv(rp), dt.date(2026, 9, 29))
            check("auto: one supersede row for the copy",
                  [(r["source_url"], r["action"], r["superseded_by"]) for r in rows],
                  [("https://wjno.example/m", "supersede", "https://wiod.example/m")])
            check("auto: appended", append_resolutions(rows, rp), 1)
            after = open(rp, "rb").read()
            check("auto: existing bytes kept", after.startswith(before_bytes), True)
            ok2, errs2 = validate(read_csv(rp))
            check("auto: rows validate", (len(ok2), errs2), (2, []))
            check("auto: held out by hold_superseded's reader",
                  superseded_urls(rp), {"wjno.example/m"})
            check("auto: idempotent", auto_supersede_rows(synd, read_csv(rp)), [])
            check("auto: no syndicated proposal once confirmed",
                  [r for r in scan(synd, read_csv(rp)) if r["signal"] == SYNDICATED], [])
            kept = [dict(r, action="keep") if "wjno" in r["source_url"] else r
                    for r in read_csv(rp)]
            check("auto: keep override validates and releases the copy",
                  (len(validate(kept)[0]), superseded_urls_from(kept)),
                  (2, set()))
            check("auto: keep override is not re-proposed",
                  auto_supersede_rows(synd, kept), [])
            m2 = [dict(r) for r in synd]
            apply(m2, validate(kept)[0])
            check("auto: apply ignores keep and supersede rows", m2, synd)
    else:
        print("SKIP (datasketch not installed): syndicated-copy checks")

    # hold_superseded, when pandas is present
    try:
        import pandas as pd
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "res.csv")
            write_csv([{k: r.get(k, "") for k in RESOLUTION_FIELDS} for r in ok],
                      p, RESOLUTION_FIELDS)
            df = pd.DataFrame(master)
            kept, n = hold_superseded(df, p)
            check("hold: one superseded row held", n, 1)
            check("hold: rows kept", len(kept), 4)
    except ImportError:
        pass

    if fails:
        print("SELFTEST FAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("status_resolution.py selftest: OK")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="apply data/status_resolutions.csv to master_opposition.csv")
    ap.add_argument("--auto-supersede", action="store_true",
                    help="confirm syndicated-copy supersede proposals into "
                         "data/status_resolutions.csv (no reviewer)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    if not os.path.exists(MASTER_CSV):
        print("master_opposition.csv absent; nothing to do")
        return 0
    with open(MASTER_CSV, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        master = list(reader)

    resolutions, errors = validate(read_csv(RESOLUTIONS_CSV))
    for e in errors:
        print(f"status_resolutions.csv {e}", file=sys.stderr)

    if args.auto_supersede:
        if ED is None or not ED.available():
            print("auto-supersede skipped: datasketch not installed")
            return 0
        rows = auto_supersede_rows(master, read_csv(RESOLUTIONS_CSV))
        n = append_resolutions(rows)
        print(f"auto-supersede: {n} syndicated copies confirmed in "
              f"{os.path.relpath(RESOLUTIONS_CSV, HERE)}")
        return 0

    if args.apply:
        changed, nfields, unmatched = apply(master, resolutions)
        if changed:
            write_master(master, fields, MASTER_CSV)
        print(f"applied {len(resolutions)} confirmed resolution(s): "
              f"{changed} master row(s) changed, {nfields} field(s)")
        for u in unmatched:
            print(f"  no master row carries {u}")
        return 1 if errors else 0

    wl = scan(master, resolutions, archive=load_archive())
    write_csv(wl, WORKLIST_CSV, WORKLIST_FIELDS)
    n_res = sum(1 for r in wl if r["proposal"] == "resolve")
    n_syn = sum(1 for r in wl if r["signal"] == SYNDICATED)
    print(f"wrote {os.path.relpath(WORKLIST_CSV, HERE)}: {n_res} pending row(s) "
          f"report a completed adoption, {len(wl) - n_res - n_syn} earlier-stage "
          f"row(s) and {n_syn} syndicated copies proposed as superseded. "
          f"Confirm in data/status_resolutions.csv.")
    if ED is None or not ED.available():
        print("syndicated-copy pass skipped: datasketch not installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
