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

Run from repo root:  python3 promote_signal_candidates.py
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
    return promoted, kept, report


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


def main() -> int:
    queue = load_csv(QUEUE_CSV)
    if not queue:
        print("promotion: queue is empty, nothing to do")
        return 0
    fields = master_fieldnames(MASTER_CSV)
    promoted, kept, report = promote(queue, fields, sh.known_urls())
    append_master(promoted, MASTER_CSV, fields)
    rewrite_queue(kept, QUEUE_CSV)
    append_report(report, REPORT_CSV)
    dupes = len(queue) - len(promoted) - len(kept)
    print(f"promotion: {len(queue)} queued -> {len(promoted)} promoted, "
          f"{len(kept)} blocked (stay in queue), {dupes} duplicates dropped")
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

    print(f"selftest: {'OK' if not failures else f'{len(failures)} FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
