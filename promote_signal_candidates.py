"""
promote_signal_candidates.py — QC-gated auto-promotion of harvested candidates.

2026-08-12 decision: the QC gate, not a human, is the arbiter for the signal
harvest queue. Every candidate in data/signal_candidates.csv becomes a draft
master row and the batch runs through the same gate that guards the clean feed
(qc/qc_pipeline.py, HIGH/CRITICAL blocks). Gate-passing rows are appended to
master_opposition.csv; blocked rows stay in the queue, which is now the
exception list rather than the default path.

Defensibility rules:
  - A promoted row asserts only what the harvest observed: headline, date,
    mechanism hint, geography, URL. Status is "pending" and Community Outcome
    stays EMPTY — promotion never asserts an outcome. Outcome fields are
    filled later by the normal update paths (review worklists, date recovery,
    manual curation).
  - Promoted rows carry data_source="signal_harvest_auto" so they are
    distinguishable and reversible as a cohort.
  - Every decision (promoted / blocked / duplicate) is appended to
    data/signal_promotion_report.csv with the gate's blocking reasons, so the
    audit trail survives queue rewrites.

Syndicated copies (spec 006): the harvester keeps one worklist row per event
cluster and lists the other URLs in cluster_members. When that row is
promoted, each member is logged as action "cluster_member" (with the kept URL
and cluster id in blocking_reasons) and not promoted. signal_harvest.known_urls
reads those rows, so a copy is not promoted on a later night either. The
date_hint column is never read here: a hint is for a reviewer, and the promoted
Date stays the harvest's seen_date.

Every run also removes exact duplicate signal_harvest_auto rows from master
(dedupe_own_rows, 2026-09-29), keeping the first occurrence and recording each
removal in the promotion report as action "duplicate_removed".

Run from repo root:  python3 promote_signal_candidates.py
Dedupe only:         python3 promote_signal_candidates.py --dedupe
Self-test:           python3 promote_signal_candidates.py --selftest
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "qc"))

import qc_pipeline as qc
import signal_harvest as sh
from promotion_trail import new_decisions

MASTER_CSV = os.path.join(ROOT, "master_opposition.csv")
QUEUE_CSV = os.path.join(ROOT, "data", "signal_candidates.csv")
REPORT_CSV = os.path.join(ROOT, "data", "signal_promotion_report.csv")

PROMOTED_SOURCE_TAG = "signal_harvest_auto"

REPORT_FIELDS = ["run_date", "action", "url", "title", "state", "county",
                 "mechanism_hint", "blocking_reasons"]


def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def master_fieldnames(master_csv: str) -> list[str]:
    with open(master_csv, newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def build_master_row(cand: dict, fields: list[str]) -> dict:
    """Draft master row from a harvest candidate. Only observed facts are
    asserted; outcome fields stay empty by construction."""
    row = {f: "" for f in fields}
    title = (cand.get("title") or "").strip()
    url = (cand.get("url") or "").strip()
    row["Incident"] = title
    row["Date"] = (cand.get("seen_date") or cand.get("harvested_on") or "").strip()
    row["Opposition Type"] = (cand.get("mechanism_hint") or "").strip()
    row["Status"] = "pending"
    row["Source URL"] = url
    row["Sources"] = url
    row["State"] = (cand.get("state") or "").strip()
    row["County"] = (cand.get("county") or "").strip()
    row["Summary"] = title
    row["data_source"] = PROMOTED_SOURCE_TAG
    return row


def _report_row(cand: dict, action: str, reasons: str) -> dict:
    return {"run_date": date.today().isoformat(), "action": action,
            "url": cand.get("url", ""), "title": cand.get("title", ""),
            "state": cand.get("state", ""), "county": cand.get("county", ""),
            "mechanism_hint": cand.get("mechanism_hint", ""),
            "blocking_reasons": reasons}


def promote(queue: list[dict], fields: list[str],
            known: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split the queue: (promoted master rows, kept candidates, report rows).

    Duplicates (URL already cited anywhere in the database, or repeated within
    the batch) are dropped from the queue without promotion. Blocked
    candidates are kept in the queue with their reasons in the report.
    """
    drafts, draft_cands, kept, report = [], [], [], []
    batch_seen: set[str] = set()
    for cand in queue:
        nu = sh.normalize_url(cand.get("url", ""))
        if nu and (nu in known or nu in batch_seen):
            report.append(_report_row(cand, "duplicate",
                                      "url already cited in the database or batch"))
            continue
        if nu:
            batch_seen.add(nu)
        # A missing URL is NOT silently dropped: the draft goes to the gate,
        # which blocks it (SOURCE_MISSING) and keeps it in the queue.
        drafts.append(build_master_row(cand, fields))
        draft_cands.append(cand)

    result = qc.run(drafts)
    promoted: list[dict] = []
    for i, verdict in enumerate(result.verdicts):
        cand, row = draft_cands[i], drafts[i]
        if verdict.blocked:
            reasons = "; ".join(f"{x.code}: {x.message}"
                                for x in verdict.issues
                                if x.severity in qc.BLOCK_AT)
            kept.append(cand)
            report.append(_report_row(cand, "blocked", reasons))
        else:
            promoted.append(row)
            report.append(_report_row(cand, "promoted", ""))
            report.extend(_member_rows(cand))
    return promoted, kept, report


def _member_rows(cand: dict) -> list[dict]:
    """One cluster_member report row per syndicated copy of a promoted row."""
    members = [m.strip() for m in (cand.get("cluster_members") or "").split(";")
               if m.strip()]
    why = (f"syndicated copy of {(cand.get('url') or '').strip()} "
           f"({(cand.get('cluster_id') or '').strip()})")
    return [_report_row(dict(cand, url=m), "cluster_member", why) for m in members]


def append_master(rows: list[dict], master_csv: str, fields: list[str]) -> None:
    if not rows:
        return
    # The appended block must start on a fresh line even if the file does not
    # end with one.
    with open(master_csv, "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        needs_newline = fh.read(1) not in (b"\n",)
    with open(master_csv, "a", newline="", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\r\n")
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writerows(rows)


def dedupe_own_rows(master_csv: str) -> list[tuple[list[str], int]]:
    """Remove exact duplicate rows of this module's own cohort from master.

    Before signal_harvest.known_urls() was fixed (PR #47), the nightly run
    re-promoted articles it had already promoted, which left 4,099 exact
    copies of 1,126 signal_harvest_auto rows in master_opposition.csv. A copy
    carries no information the first occurrence lacks, so the first
    occurrence is kept and later ones are dropped. Decided 2026-09-29 (spec 005
    follow-up, delegated by Price).

    Only rows whose data_source is PROMOTED_SOURCE_TAG are touched, and only
    when every field matches an earlier row exactly. Every other byte of the
    file, including CRLF line endings and quoting, is preserved: records are
    sliced from the raw text rather than re-serialized. Idempotent. Returns
    (row, copies removed) for each duplicated record.
    """
    with open(master_csv, "rb") as fh:
        text = fh.read().decode("utf-8")
    lines = text.splitlines(keepends=True)
    reader = csv.reader(lines)
    try:
        header = next(reader)
    except StopIteration:
        return []
    ds = header.index("data_source") if "data_source" in header else -1
    kept_text = lines[:reader.line_num]
    seen: dict[tuple, int] = {}
    removed: dict[tuple, int] = {}
    start = reader.line_num
    for rec in reader:
        chunk = lines[start:reader.line_num]
        start = reader.line_num
        key = tuple(rec)
        own = ds >= 0 and len(rec) > ds and rec[ds] == PROMOTED_SOURCE_TAG
        if own and key in seen:
            removed[key] = removed.get(key, 0) + 1
            continue
        seen.setdefault(key, 1)
        kept_text.extend(chunk)
    if removed:
        tmp = master_csv + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write("".join(kept_text).encode("utf-8"))
        os.replace(tmp, master_csv)
    return [(list(k), n) for k, n in removed.items()]


def dedupe_report_rows(removed, header: list[str]) -> list[dict]:
    """One audit row per duplicated record, in the promotion report."""
    col = {name: i for i, name in enumerate(header)}
    out = []
    for rec, n in removed:
        get = lambda f: rec[col[f]] if f in col and col[f] < len(rec) else ""
        out.append({"run_date": date.today().isoformat(), "action": "duplicate_removed",
                    "url": get("Source URL"), "title": get("Incident"), "state": get("State"),
                    "county": get("County"), "mechanism_hint": get("Opposition Type"),
                    "blocking_reasons": f"{n} exact copy(ies) removed from master; first occurrence kept"})
    return out


def rewrite_queue(kept: list[dict], queue_csv: str) -> None:
    with open(queue_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=sh.FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)


def append_report(rows: list[dict], report_csv: str) -> int:
    """Append decisions, not re-statements of decisions.

    The queue carries candidates forward across runs, so an unconditional
    append re-recorded the same verdict on the same article every night: 7,342
    rows described 1,937 decisions, 1,344 of them re-decided identically. The
    Data Operations page reports this count as evidence the platform screens
    what it harvests, and a count inflated nearly fourfold by repetition
    overstates that evidence. Returns the number actually recorded.
    """
    if not rows:
        return 0
    existing = load_csv(report_csv) if os.path.exists(report_csv) else []
    # Keyed on url AND title: one batch legitimately emits two verdicts for
    # one URL, promoting the first occurrence and marking the rest duplicate.
    # Keying on url alone made those two rows overwrite each other's recorded
    # state, so every run saw a change and recorded both again forever.
    fresh, _ = new_decisions(existing, rows, key_fields=("url", "title"),
                             state_fields=("action", "blocking_reasons"))
    if not fresh:
        return 0
    new_file = not os.path.exists(report_csv)
    with open(report_csv, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REPORT_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(fresh)
    return len(fresh)


def run_dedupe() -> int:
    removed = dedupe_own_rows(MASTER_CSV)
    n = sum(c for _, c in removed)
    if removed:
        append_report(dedupe_report_rows(removed, master_fieldnames(MASTER_CSV)), REPORT_CSV)
    print(f"dedupe: removed {n} exact duplicate {PROMOTED_SOURCE_TAG} row(s) "
          f"across {len(removed)} record(s)")
    return 0


def main() -> int:
    queue = load_csv(QUEUE_CSV)
    if not queue:
        print("promotion: queue is empty, nothing to do")
        run_dedupe()
        return 0
    fields = master_fieldnames(MASTER_CSV)
    promoted, kept, report = promote(queue, fields, sh.known_urls())
    append_master(promoted, MASTER_CSV, fields)
    rewrite_queue(kept, QUEUE_CSV)
    append_report(report, REPORT_CSV)
    dupes = len(queue) - len(promoted) - len(kept)
    print(f"promotion: {len(queue)} queued -> {len(promoted)} promoted, "
          f"{len(kept)} blocked (stay in queue), {dupes} duplicates dropped")
    run_dedupe()
    return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    global MASTER_CSV, QUEUE_CSV, REPORT_CSV

    failures = []

    def check(name, ok):
        print(("  PASS  " if ok else "  FAIL  ") + name)
        if not ok:
            failures.append(name)

    fields = master_fieldnames(os.path.join(ROOT, "master_opposition.csv"))
    good = {"priority": "9.0", "seen_date": "2026-08-01", "query_label": "organizing",
            "title": "County board adopts data center moratorium",
            "domain": "example-news.com",
            "url": "https://example-news.com/county-moratorium",
            "mechanism_hint": "moratorium", "county": "Franklin", "state": "OH",
            "location_confidence": "high", "county_already_tracked": "no",
            "harvested_on": "2026-08-02"}
    no_url = dict(good, title="Rally against rezoning", url="", domain="")
    dupe = dict(good, title="Same story again")

    promoted, kept, report = promote([good, no_url, dupe], fields, known=set())
    check("gate-passing candidate is promoted", len(promoted) == 1
          and promoted[0]["Incident"] == good["title"])
    check("promoted row carries the auto tag and pending status",
          promoted and promoted[0]["data_source"] == PROMOTED_SOURCE_TAG
          and promoted[0]["Status"] == "pending")
    check("promoted row asserts no outcome",
          promoted and promoted[0]["Community Outcome"] == "")
    check("candidate without a URL is blocked, stays queued",
          len(kept) == 1 and kept[0]["title"] == no_url["title"])
    check("blocked candidate carries gate reasons",
          any(r["action"] == "blocked" and "SOURCE_MISSING" in r["blocking_reasons"]
              for r in report))
    check("within-batch duplicate URL is dropped",
          any(r["action"] == "duplicate" and r["title"] == dupe["title"]
              for r in report))

    # Spec 006: a date hint is never copied into master, and a promoted
    # cluster representative logs its syndicated copies.
    hinted = dict(good, url="https://example-news.com/hinted", title="Board adopts pause",
                  seen_date="2026-01-05", date_hint="2026-01-02", thin_text="no",
                  cluster_id="evt_0123456789",
                  cluster_members="https://copy-a.example/x; https://copy-b.example/y")
    p2, _, r2 = promote([hinted], fields, known=set())
    check("promotion ignores date_hint: Date stays the seen_date",
          p2 and p2[0]["Date"] == "2026-01-05"
          and "2026-01-02" not in ",".join(p2[0].values()))
    members = [r for r in r2 if r["action"] == "cluster_member"]
    check("a promoted cluster row logs one cluster_member row per copy",
          [r["url"] for r in members] == ["https://copy-a.example/x", "https://copy-b.example/y"]
          and all("syndicated copy of https://example-news.com/hinted (evt_0123456789)"
                  == r["blocking_reasons"] for r in members))
    check("the copies are not promoted", len(p2) == 1)
    _, k3, r3 = promote([dict(hinted, url="", domain="")], fields, known=set())
    check("a blocked cluster row logs no members (they stay listed on the queued row)",
          k3 and not any(r["action"] == "cluster_member" for r in r3))

    already_known = promote([good], fields,
                            known={sh.normalize_url(good["url"])})
    check("url already in the database is not promoted",
          already_known[0] == [] and already_known[1] == [])

    # File round-trip on temp copies.
    with tempfile.TemporaryDirectory() as td:
        tmp_master = os.path.join(td, "master.csv")
        with open(tmp_master, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
        append_master(promoted, tmp_master, fields)
        back = load_csv(tmp_master)
        check("appended master round-trips", len(back) == 1
              and back[0]["Source URL"] == good["url"])

        tmp_queue = os.path.join(td, "queue.csv")
        rewrite_queue(kept, tmp_queue)
        check("queue rewrite keeps only blocked rows",
              len(load_csv(tmp_queue)) == 1)
        rewrite_queue([dict(no_url, date_hint="2026-01-02", cluster_id="evt_x",
                            cluster_members="https://a.example/1")], tmp_queue)
        back_q = load_csv(tmp_queue)
        check("queue rewrite keeps the appended spec 006 columns",
              back_q[0]["date_hint"] == "2026-01-02"
              and back_q[0]["cluster_members"] == "https://a.example/1")

        # This previously asserted that appending the same report twice
        # doubled the rows, which encoded the duplication defect as the
        # expected behaviour. The queue carries candidates across runs, so
        # that path ran nightly and inflated the trail nearly fourfold.
        tmp_report = os.path.join(td, "report.csv")
        first = append_report(report, tmp_report)
        again = append_report(report, tmp_report)
        check("report writes the header once and every decision once",
              len(load_csv(tmp_report)) == len(report) and first == len(report))
        check("re-deciding the same articles the same way records nothing",
              again == 0 and len(load_csv(tmp_report)) == len(report))
        # Flip every verdict to promoted. The row that was already promoted
        # has not changed and must stay unrecorded; the other two have and
        # must be recorded. Asserting the exact split is the point: a rule
        # that recorded all three would be back to logging non-events.
        changed = [dict(r, action="promoted", blocking_reasons="")
                   for r in report]
        already = sum(1 for r in report
                      if r["action"] == "promoted" and not r["blocking_reasons"])
        moved = append_report(changed, tmp_report)
        check("only the verdicts that actually changed are recorded",
              moved == len(report) - already and already >= 1)

        # Exact duplicates of the auto cohort go; everything else is untouched
        # byte for byte, including CRLF endings and a quoted multi-line field.
        dm = os.path.join(td, "dedupe.csv")
        raw = ('Incident,Summary,data_source\r\n'
               'A,"line one\r\nline two",signal_harvest_auto\r\n'
               'B,x,datacentertracker.org\r\n'
               'A,"line one\r\nline two",signal_harvest_auto\r\n'
               'B,x,datacentertracker.org\r\n'
               'A,"line one\r\nline two",signal_harvest_auto\r\n'
               'C,y,signal_harvest_auto\r\n')
        with open(dm, "wb") as fh:
            fh.write(raw.encode("utf-8"))
        removed = dedupe_own_rows(dm)
        after = open(dm, "rb").read().decode("utf-8")
        check("dedupe removes exact copies of the auto cohort only",
              sum(n for _, n in removed) == 2 and after.count("A,") == 1
              and after.count("B,x,") == 2 and "C,y," in after)
        check("dedupe preserves every other byte",
              after == ('Incident,Summary,data_source\r\n'
                        'A,"line one\r\nline two",signal_harvest_auto\r\n'
                        'B,x,datacentertracker.org\r\n'
                        'B,x,datacentertracker.org\r\n'
                        'C,y,signal_harvest_auto\r\n'))
        check("dedupe is idempotent", dedupe_own_rows(dm) == [] and
              open(dm, "rb").read().decode("utf-8") == after)
        rep = dedupe_report_rows(removed, ["Incident", "Summary", "data_source"])
        check("dedupe leaves an audit row per record",
              len(rep) == 1 and rep[0]["action"] == "duplicate_removed"
              and rep[0]["title"] == "A")

    print(f"selftest: {'OK' if not failures else f'{len(failures)} FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--dedupe" in sys.argv:
        sys.exit(run_dedupe())
    sys.exit(main())
