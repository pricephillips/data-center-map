"""
manual_records.py — manual corrections and manual uploads for the master
opposition database, through the SAME gates the automated ingest uses.

Why this exists (2026-08-26): corrections from record reviews were being
hand-applied ad hoc, and there was no first-class way to push a curated row
into master_opposition.csv short of impersonating one of the automated
ingest paths. This module gives both operations a declared intake, the QC
gate, URL and content dedup, provenance tagging, and an append-only audit
trail — so a manual row is exactly as accountable as an auto-promoted one.

The two intakes (edit these, then run `python3 manual_records.py --apply`,
or just commit them — .github/workflows/manual-records.yml applies them in CI):

  data/manual_corrections.csv   field-level corrections to existing rows.
      One row per field change. Rows with status blank or "pending" are
      processed; every processed row is marked applied/blocked in place, so
      the file doubles as the corrections ledger and a re-run is a no-op.

      Columns: submitted_on, submitted_by, match_incident, match_date,
               match_url, field, new_value, reason, status, applied_on,
               result_note

      Matching: match_incident is required and exact (whitespace-normalized);
      match_date, when given, must equal the row's Date; match_url, when
      given, must be a substring of the row's Source URL. The match must hit
      exactly ONE master row — except DELETE_ROW (below).

      `field` is any master column, or one of the pseudo-fields:
        APPEND_SUMMARY  appends " [Update YYYY-MM-DD: <new_value>]" to Summary
        APPEND_SOURCES  appends "; <new_value>" to Sources
        DELETE_ROW      new_value "DELETE" deletes every matched row;
                        new_value "DEDUP" keeps the first and deletes the
                        rest. Multiple matches are allowed ONLY when all
                        matched rows are identical (the dedup-repair case).
                        Deleted rows are archived, never just dropped.

  data/manual_additions.csv     new rows to append. Any subset of the master
      columns (start from `--template`); a trailing `_note` column is the
      tool's feedback channel. Gate-passing rows are appended to
      master_opposition.csv and removed from the intake; blocked and
      duplicate rows STAY in the intake with `_note` filled, as the
      exception queue — mirroring the signal-harvest promotion contract.

Gates (identical policy to the automated paths):
  - qc/qc_pipeline.py record gate: HIGH/CRITICAL findings block.
  - URL dedup: a URL already cited anywhere in the database, or repeated
    within the batch, blocks an addition (signal_harvest.normalize_url).
  - Content dedup: an addition whose (jurisdiction, name, date) matches an
    existing master row is blocked.
  - A correction must leave the corrected row gate-passing; a correction
    that would put a HIGH/CRITICAL finding on the row is rejected whole
    (all field changes for that target stand or fall together).

Provenance and the nightly sync: build_master_csv.py refreshes rows whose
data_source is "datacentertracker.org" or blank straight from upstream, so a
hand edit to such a row would be REVERTED the next night. Every corrected
row therefore gets data_source="manual_correction" (original value recorded
in result_note), which the sync's ownership rule shields; uploads carry
data_source="manual_upload". Both cohorts stay distinguishable and
reversible, like signal_harvest_auto.

Outputs:
  master_opposition.csv            corrected / extended in place
  data/manual_records_report.csv   append-only audit trail of every decision
  data/manual_removed_rows.csv     archive of rows deleted by DELETE_ROW

Run from repo root:  python3 manual_records.py --apply
Preview only:        python3 manual_records.py --dry-run
Blank addition row:  python3 manual_records.py --template
Self-test:           python3 manual_records.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "qc"))

import qc_pipeline as qc
import schema_adapter as A
import enrichment as E
import signal_harvest as sh

MASTER_CSV = os.path.join(ROOT, "master_opposition.csv")
CORRECTIONS_CSV = os.path.join(ROOT, "data", "manual_corrections.csv")
ADDITIONS_CSV = os.path.join(ROOT, "data", "manual_additions.csv")
REPORT_CSV = os.path.join(ROOT, "data", "manual_records_report.csv")
REMOVED_CSV = os.path.join(ROOT, "data", "manual_removed_rows.csv")

CORRECTION_SOURCE_TAG = "manual_correction"
UPLOAD_SOURCE_TAG = "manual_upload"

CORRECTION_FIELDS = ["submitted_on", "submitted_by", "match_incident",
                     "match_date", "match_url", "field", "new_value",
                     "reason", "status", "applied_on", "result_note"]
PSEUDO_FIELDS = {"APPEND_SUMMARY", "APPEND_SOURCES", "DELETE_ROW"}
_PENDING = {"", "pending"}

REPORT_FIELDS = ["run_date", "kind", "action", "incident", "date", "field", "detail"]
NOTE_COL = "_note"


# ---------------------------------------------------------------------------
# Shared IO
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def master_fields(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return next(csv.reader(fh))


def write_csv(path: str, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def append_csv(path: str, rows: list[dict], fields: list[str]) -> None:
    if not rows:
        return
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def _today() -> str:
    return date.today().isoformat()


def _report(kind: str, action: str, incident: str, dt: str, fld: str, detail: str) -> dict:
    return {"run_date": _today(), "kind": kind, "action": action,
            "incident": incident, "date": dt, "field": fld, "detail": detail}


def _gate_reasons(verdict) -> str:
    return "; ".join(f"{i.code}: {i.message}" for i in verdict.issues
                     if i.severity in qc.BLOCK_AT)


def _row_passes_gate(row: dict) -> tuple[bool, str]:
    result = qc.run([row])
    v = result.verdicts[0]
    return (not v.blocked), ("" if not v.blocked else _gate_reasons(v))


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------

def find_matches(master: list[dict], corr: dict) -> list[int]:
    want_incident = _norm_ws(corr.get("match_incident", ""))
    want_date = (corr.get("match_date") or "").strip()
    want_url = (corr.get("match_url") or "").strip()
    hits = []
    for i, row in enumerate(master):
        if _norm_ws(row.get("Incident", "")) != want_incident:
            continue
        if want_date and (row.get("Date") or "").strip() != want_date:
            continue
        if want_url and want_url not in (row.get("Source URL") or ""):
            continue
        hits.append(i)
    return hits


def _apply_field(row: dict, fld: str, value: str) -> None:
    if fld == "APPEND_SUMMARY":
        base = (row.get("Summary") or "").rstrip()
        row["Summary"] = (base + " " if base else "") + f"[Update {_today()}: {value}]"
    elif fld == "APPEND_SOURCES":
        base = (row.get("Sources") or "").rstrip().rstrip(";")
        row["Sources"] = (base + "; " if base else "") + value
    else:
        row[fld] = value


def apply_corrections(master: list[dict], corrections: list[dict],
                      fields: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """Apply pending corrections. Returns (new_master, removed_rows, report).
    The corrections list is annotated in place (status/applied_on/result_note)."""
    report: list[dict] = []
    removed: list[dict] = []
    valid_fields = set(fields) | PSEUDO_FIELDS

    pending = [c for c in corrections
               if (c.get("status") or "").strip().lower() in _PENDING]

    # Group by target so all field changes for one record stand or fall together.
    groups: dict[tuple, list[dict]] = {}
    for c in pending:
        key = (_norm_ws(c.get("match_incident", "")),
               (c.get("match_date") or "").strip(),
               (c.get("match_url") or "").strip())
        groups.setdefault(key, []).append(c)

    delete_idx: set[int] = set()
    staged: dict[int, dict] = {}          # master index -> corrected copy

    def mark(cs, status, note):
        for c in cs:
            c["status"] = status
            c["applied_on"] = _today() if status == "applied" else ""
            c["result_note"] = note
        fld = "; ".join(c.get("field", "") for c in cs)
        report.append(_report("correction",
                              "applied" if status == "applied" else status,
                              key[0], key[1], fld, note))

    for key, cs in groups.items():
        bad_field = next((c for c in cs
                          if (c.get("field") or "").strip() not in valid_fields), None)
        if bad_field is not None:
            mark(cs, "blocked", f"unknown field '{bad_field.get('field')}'")
            continue

        hits = find_matches(master, cs[0])
        # All corrections in a group share match keys by construction; verify.
        if any(find_matches(master, c) != hits for c in cs[1:]):
            mark(cs, "blocked", "corrections in this group disagree on their match")
            continue
        if not hits:
            mark(cs, "blocked", "no master row matches")
            continue

        deletes = [c for c in cs if c.get("field") == "DELETE_ROW"]
        edits = [c for c in cs if c.get("field") != "DELETE_ROW"]
        if deletes and edits:
            mark(cs, "blocked", "DELETE_ROW cannot be combined with field edits")
            continue

        if deletes:
            verb = (deletes[0].get("new_value") or "").strip().upper()
            if len(deletes) > 1 or verb not in {"DELETE", "DEDUP"}:
                mark(cs, "blocked",
                     "DELETE_ROW needs exactly one row with new_value DELETE or DEDUP")
                continue
            if len(hits) > 1:
                first = master[hits[0]]
                if any(master[i] != first for i in hits[1:]):
                    mark(cs, "blocked",
                         f"matches {len(hits)} rows that are not identical; refusing to delete")
                    continue
            doomed = hits[1:] if verb == "DEDUP" else hits
            if not doomed:                # DEDUP with a single match: nothing to drop
                mark(cs, "applied", "DEDUP found a single row; nothing removed")
                continue
            delete_idx.update(doomed)
            mark(cs, "applied",
                 f"removed {len(doomed)} row(s)" + (" (kept first)" if verb == "DEDUP" else ""))
            continue

        if len(hits) > 1:
            mark(cs, "blocked", f"matches {len(hits)} rows; match must be unique")
            continue

        idx = hits[0]
        candidate = dict(staged.get(idx, master[idx]))
        for c in edits:
            _apply_field(candidate, (c.get("field") or "").strip(),
                         c.get("new_value") or "")
        original_source = (master[idx].get("data_source") or "").strip()
        if not any((c.get("field") or "").strip() == "data_source" for c in edits):
            candidate["data_source"] = CORRECTION_SOURCE_TAG

        ok, reasons = _row_passes_gate(candidate)
        if not ok:
            staged.pop(idx, None)
            mark(cs, "blocked", f"corrected row fails the QC gate: {reasons}")
            continue
        staged[idx] = candidate
        mark(cs, "applied",
             f"was data_source='{original_source}'" if original_source and
             candidate["data_source"] != original_source else "")

    new_master: list[dict] = []
    for i, row in enumerate(master):
        if i in delete_idx:
            removed.append({**row, "_removed_on": _today()})
            report.append(_report("correction", "deleted",
                                  row.get("Incident", ""), row.get("Date", ""),
                                  "DELETE_ROW", row.get("Source URL", "")))
            continue
        new_master.append(staged.get(i, row))
    return new_master, removed, report


# ---------------------------------------------------------------------------
# Additions
# ---------------------------------------------------------------------------

def _content_key(row: dict) -> tuple:
    n = A.normalize_record(row)
    return (E.jurisdiction_key(n),
            _norm_ws(qc.record_name(n)).lower(),
            _norm_ws(qc.text(n, "Date")).lower())


def _urls_of(row: dict) -> set[str]:
    import re
    blob = " ".join(str(row.get(f) or "") for f in
                    ("Source URL", "Sources", "Opposition Website", "Petition URL"))
    return {sh.normalize_url(m) for m in re.findall(r"https?://[^\s'\"}\],]+", blob)}


def apply_additions(master: list[dict], additions: list[dict],
                    fields: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """Gate the additions. Returns (promoted_rows, kept_intake_rows, report)."""
    report: list[dict] = []
    promoted: list[dict] = []
    kept: list[dict] = []

    known = set()
    for r in master:
        known |= _urls_of(r)
    master_keys = {_content_key(r) for r in master}
    batch_urls: set[str] = set()
    batch_keys: set[tuple] = set()

    for raw in additions:
        row = {f: (raw.get(f) or "").strip() for f in fields}
        row["data_source"] = UPLOAD_SOURCE_TAG
        incident, dt = row.get("Incident", ""), row.get("Date", "")

        primary = sh.normalize_url(row.get("Source URL", ""))
        dupe_url = next((u for u in ([primary] if primary else [])
                         if u in known or u in batch_urls), None)
        if dupe_url:
            raw[NOTE_COL] = f"blocked: Source URL already cited in the database ({dupe_url})"
            kept.append(raw)
            report.append(_report("addition", "duplicate_url", incident, dt,
                                  "Source URL", dupe_url))
            continue

        ckey = _content_key(row)
        if ckey in master_keys or ckey in batch_keys:
            raw[NOTE_COL] = ("blocked: a master row with the same jurisdiction, "
                             "name, and date already exists")
            kept.append(raw)
            report.append(_report("addition", "duplicate_content", incident, dt,
                                  "Incident/Date", str(ckey)))
            continue

        ok, reasons = _row_passes_gate(row)
        if not ok:
            raw[NOTE_COL] = f"blocked by QC gate: {reasons}"
            kept.append(raw)
            report.append(_report("addition", "blocked", incident, dt, "", reasons))
            continue

        promoted.append(row)
        batch_urls |= _urls_of(row)
        batch_keys.add(ckey)
        report.append(_report("addition", "promoted", incident, dt, "",
                              f"data_source={UPLOAD_SOURCE_TAG}"))
    return promoted, kept, report


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_apply(dry_run: bool = False) -> int:
    fields = master_fields(MASTER_CSV)
    master = load_csv(MASTER_CSV)
    corrections = load_csv(CORRECTIONS_CSV)
    additions_raw = load_csv(ADDITIONS_CSV)

    master2, removed, rep_c = apply_corrections(master, corrections, fields)
    promoted, kept, rep_a = apply_additions(master2, additions_raw, fields)
    report = rep_c + rep_a

    n_applied = sum(1 for r in rep_c if r["action"] == "applied")
    n_blocked_c = sum(1 for r in rep_c if r["action"] == "blocked")
    n_deleted = sum(1 for r in rep_c if r["action"] == "deleted")
    print(f"corrections: {n_applied} applied, {n_blocked_c} blocked, "
          f"{n_deleted} row(s) removed")
    print(f"additions:   {len(promoted)} promoted, {len(kept)} kept in intake "
          f"(blocked or duplicate)")
    for r in report:
        if r["action"] not in ("applied", "promoted", "deleted"):
            print(f"  {r['kind']} {r['action']}: {r['incident']} | {r['detail']}")

    if dry_run:
        print("dry run: nothing written")
        return 0

    changed = (n_applied or n_deleted or promoted)
    if changed:
        write_csv(MASTER_CSV, master2 + promoted, fields)
    if corrections:
        write_csv(CORRECTIONS_CSV, corrections, CORRECTION_FIELDS)
    if additions_raw or os.path.exists(ADDITIONS_CSV):
        write_csv(ADDITIONS_CSV, kept, fields + [NOTE_COL])
    if removed:
        append_csv(REMOVED_CSV, removed, fields + ["_removed_on"])
    append_csv(REPORT_CSV, report, REPORT_FIELDS)
    return 0


def write_template() -> int:
    fields = master_fields(MASTER_CSV)
    if not os.path.exists(ADDITIONS_CSV):
        write_csv(ADDITIONS_CSV, [], fields + [NOTE_COL])
        print(f"wrote blank additions intake: {ADDITIONS_CSV}")
    else:
        print(f"additions intake already exists: {ADDITIONS_CSV}")
    if not os.path.exists(CORRECTIONS_CSV):
        write_csv(CORRECTIONS_CSV, [], CORRECTION_FIELDS)
        print(f"wrote blank corrections ledger: {CORRECTIONS_CSV}")
    else:
        print(f"corrections ledger already exists: {CORRECTIONS_CSV}")
    return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    failures: list[str] = []

    def check(name, ok):
        print(("  PASS  " if ok else "  FAIL  ") + name)
        if not ok:
            failures.append(name)

    fields = master_fields(MASTER_CSV)

    def mk(incident, dt, url, **kw):
        row = {f: "" for f in fields}
        row.update({"Incident": incident, "Date": dt, "Source URL": url,
                    "Sources": url, "Opposition Type": "moratorium",
                    "State": "OH", "County": "Franklin County", "Scope": "local",
                    "Authority Level": "county_commission", "Status": "passed",
                    "Community Outcome": "win", "data_source": "datacentertracker.org",
                    "Summary": "The county adopted a 12-month moratorium on new "
                               "data center construction countywide."})
        row.update(kw)
        return row

    base = mk("Testville", "2026-01-05", "https://www.reuters.com/testville")
    other = mk("Otherville", "2026-02-06", "https://www.reuters.com/otherville",
               County="Delaware County")
    twin_a = mk("Twin story", "2026-03-01", "https://www.reuters.com/twin")
    twin_b = dict(twin_a)
    master = [base, other, twin_a, twin_b]

    def corr(incident, dt, fld, val, url=""):
        return {"submitted_on": "2026-08-26", "submitted_by": "selftest",
                "match_incident": incident, "match_date": dt, "match_url": url,
                "field": fld, "new_value": val, "reason": "selftest",
                "status": "", "applied_on": "", "result_note": ""}

    # A field edit applies, flips provenance, and passes the gate.
    cs = [corr("Testville", "2026-01-05", "Opposition Type", "moratorium; public_comment")]
    m2, removed, rep = apply_corrections([dict(r) for r in master], cs, fields)
    check("field edit applies",
          m2[0]["Opposition Type"] == "moratorium; public_comment")
    check("corrected row flips to manual_correction provenance",
          m2[0]["data_source"] == CORRECTION_SOURCE_TAG and cs[0]["status"] == "applied"
          and "datacentertracker.org" in cs[0]["result_note"])
    check("untouched rows keep provenance", m2[1]["data_source"] == "datacentertracker.org")

    # Unknown field, no match, ambiguous match are all blocked.
    cs = [corr("Testville", "2026-01-05", "Not A Column", "x")]
    _, _, _ = apply_corrections([dict(r) for r in master], cs, fields)
    check("unknown field blocked", cs[0]["status"] == "blocked")
    cs = [corr("Nowhere", "2026-01-05", "Status", "passed")]
    apply_corrections([dict(r) for r in master], cs, fields)
    check("no-match blocked", cs[0]["status"] == "blocked")
    cs = [corr("Twin story", "2026-03-01", "Status", "passed")]
    apply_corrections([dict(r) for r in master], cs, fields)
    check("ambiguous non-delete match blocked", cs[0]["status"] == "blocked")

    # A correction that breaks the row is rejected whole.
    cs = [corr("Testville", "2026-01-05", "Date", "2031-01-01"),
          corr("Testville", "2026-01-05", "Status", "extended")]
    m2, _, _ = apply_corrections([dict(r) for r in master], cs, fields)
    check("gate-failing correction group rejected whole",
          all(c["status"] == "blocked" for c in cs)
          and m2[0]["Date"] == "2026-01-05" and m2[0]["Status"] == "passed")

    # APPEND_SUMMARY appends a dated update note.
    cs = [corr("Testville", "2026-01-05", "APPEND_SUMMARY", "Still in effect.")]
    m2, _, _ = apply_corrections([dict(r) for r in master], cs, fields)
    check("APPEND_SUMMARY appends dated note",
          m2[0]["Summary"].endswith("Still in effect.]")
          and "[Update " in m2[0]["Summary"]
          and m2[0]["Summary"].startswith("The county adopted"))

    # DELETE on identical twins removes both and archives them.
    cs = [corr("Twin story", "2026-03-01", "DELETE_ROW", "DELETE")]
    m2, removed, _ = apply_corrections([dict(r) for r in master], cs, fields)
    check("identical multi-match DELETE removes all and archives",
          len(m2) == 2 and len(removed) == 2 and cs[0]["status"] == "applied")

    # DEDUP keeps one.
    cs = [corr("Twin story", "2026-03-01", "DELETE_ROW", "DEDUP")]
    m2, removed, _ = apply_corrections([dict(r) for r in master], cs, fields)
    check("DEDUP keeps first, removes rest", len(m2) == 3 and len(removed) == 1)

    # Non-identical multi-match refuses to delete.
    variant = dict(twin_b, Status="pending", **{"Community Outcome": "pending"})
    cs = [corr("Twin story", "2026-03-01", "DELETE_ROW", "DELETE")]
    m2, removed, _ = apply_corrections([base, other, dict(twin_a), variant], cs, fields)
    check("non-identical multi-match DELETE blocked",
          cs[0]["status"] == "blocked" and len(m2) == 4)

    # Additions: gate-passing row promoted with upload provenance.
    add_ok = mk("Newplace", "2026-04-01", "https://www.reuters.com/newplace",
                County="Licking County")
    add_dup_url = mk("Dup URL place", "2026-04-02", "https://www.reuters.com/testville",
                     County="Stark County")
    add_dup_content = mk("Testville", "2026-01-05", "https://www.reuters.com/fresh",
                         County="Franklin County")
    add_bad = mk("No source place", "2026-04-03", "", Sources="", County="Summit County")
    promoted, kept, rep = apply_additions(
        master, [dict(add_ok), dict(add_dup_url), dict(add_dup_content), dict(add_bad)],
        fields)
    check("gate-passing addition promoted with manual_upload tag",
          len(promoted) == 1 and promoted[0]["Incident"] == "Newplace"
          and promoted[0]["data_source"] == UPLOAD_SOURCE_TAG)
    check("addition citing an already-cited URL stays in intake",
          any("already cited" in (k.get(NOTE_COL) or "") for k in kept))
    check("addition duplicating jurisdiction+name+date stays in intake",
          any("same jurisdiction" in (k.get(NOTE_COL) or "") for k in kept))
    check("gate-blocked addition stays in intake with reasons",
          any("SOURCE_MISSING" in (k.get(NOTE_COL) or "") for k in kept))
    check("every decision is in the report", len(rep) == 4)

    # Within-batch URL duplicate is caught.
    a1 = mk("Batch one", "2026-05-01", "https://www.reuters.com/batch",
            County="Wood County")
    a2 = mk("Batch two", "2026-05-02", "https://www.reuters.com/batch",
            County="Lucas County")
    promoted, kept, _ = apply_additions(master, [dict(a1), dict(a2)], fields)
    check("within-batch duplicate URL blocked", len(promoted) == 1 and len(kept) == 1)

    # Already-processed ledger rows are left alone (idempotency).
    done = corr("Testville", "2026-01-05", "Status", "extended")
    done["status"] = "applied"
    m2, _, rep = apply_corrections([dict(r) for r in master], [done], fields)
    check("applied ledger rows are not reprocessed",
          m2[0]["Status"] == "passed" and rep == [])

    print(f"selftest: {'OK' if not failures else f'{len(failures)} FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Manual corrections and uploads "
                                            "through the standard QC gates.")
    p.add_argument("--apply", action="store_true", help="apply both intakes")
    p.add_argument("--dry-run", action="store_true", help="report, write nothing")
    p.add_argument("--template", action="store_true", help="write blank intakes")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if args.template:
        sys.exit(write_template())
    if args.dry_run:
        sys.exit(run_apply(dry_run=True))
    sys.exit(run_apply())
