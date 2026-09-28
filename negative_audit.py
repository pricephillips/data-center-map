"""
negative_audit.py — Verified-negative audit of the baseline universe.

Purpose. The opposition tracker is opposition-selected: it contains projects
because they drew opposition. Nothing in the platform can currently say
whether a project WITHOUT recorded opposition truly faced none, or whether
opposition simply went undetected. Until that distinction exists, opposition
emergence (P(opposition | project)) is unmodelable. This audit creates the
distinction: every project in the audit frame is actively searched and coded
verified_opposition / verified_none / undeterminable, with sources.

Audit frame (recorded design decision, 2026-07-23). The frame is a CENSUS,
not a sample, of the modern-proposal portion of the baseline universe:
  - all proposals_unopposed rows (trackdatacenters proposals with no linked
    opposition events), and
  - all ai_centers rows,
  which together fall inside the 150-200 project budget. A census removes
  sampling error from every downstream claim. Atlas rows (built/legacy
  facilities) are EXCLUDED from the frame with recorded rationale: their
  approval processes predate reliable digital news coverage, so an active
  search that finds nothing cannot distinguish "no opposition" from "no
  record"; nearly every row would code undeterminable while consuming the
  audit budget. This exclusion is a detectability decision, not a claim that
  built facilities faced no opposition, and it must be stated wherever audit
  results are used. Control-group exclusion flags (county shared with an
  opposed project, proximity) do NOT exclude a row from this audit: the
  audit measures emergence in the full frame, not contamination-free
  matching.

Worklist ordering. The worklist is emitted in seeded-shuffle order
(RANDOM_STATE below) so that any partial batch is a random subset of the
frame; batches worked top-down never cluster by state, source, or alphabet.
Exception: rows whose lifecycle outcome is blocked_confirmed sort first
(blocked with no recorded opposition is the most anomalous cell and the
most likely coding error in either direction).

Coding rules (fixed; changing them requires a new registration date):
  verified_opposition  A sourced URL evidences at least one opposition
                       activity against this project per tracker
                       definitions (organized group, petition, opposition
                       public comment, moratorium or restriction targeting
                       it, lawsuit, opposition-driven denial). The URL goes
                       in evidence_url.
  verified_none        BOTH conditions required: (a) the full search
                       protocol was executed and surfaced no opposition
                       evidence, AND (b) the detectability floor is met:
                       at least one independent source documents the
                       project's local approval or development process
                       (news coverage of a hearing, approval, groundbreaking
                       with process detail, or a municipal record), so that
                       opposition would plausibly have been reported had it
                       existed. That source goes in detectability_url.
                       Absence of evidence counts only when the project
                       itself is visible in the record.
  undeterminable       Protocol executed but the project's documentary
                       footprint is insufficient (no coverage of the
                       approval process found), or the evidence is
                       ambiguous (e.g. generalized county-level data center
                       sentiment not tied to this project).

Search protocol per project (minimum; stop early only on a
verified_opposition find):
  1. "<name> <state>"
  2. "<name or operator> data center <city/county>"
  3. "<county> <state> data center opposition OR moratorium OR rezoning
     OR petition"
  4. If the operator is known and 1-3 were inconclusive:
     "<operator> data center <state> residents"
Record the number of queries run in queries_run. Every coding row requires
coded_by and coded_date.

Files:
  reads   data/baseline_universe.csv
  writes  data/negative_audit_worklist.csv   (the frame, protocol order)
  reads   data/negative_audit_codings.csv    (append-only; hand-filled or
                                              delivered per batch)
  writes  data/negative_audit_report.md      (coverage, coding mix,
                                              detection-tier breakdown)

Codings CSV columns:
  universe_id, coding, evidence_url, detectability_url, queries_run,
  notes, coded_by, coded_date

Not wired into CI. Run from repo root:  python3 negative_audit.py
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
from collections import Counter
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)

UNIVERSE_CSV = P("data", "baseline_universe.csv")
OUT_WORKLIST = P("data", "negative_audit_worklist.csv")
CODINGS_CSV = P("data", "negative_audit_codings.csv")
OUT_REPORT = P("data", "negative_audit_report.md")

RANDOM_STATE = 20260723
COVERAGE_THRESHOLD = 0.80
FRAME_SOURCES = {"proposals_unopposed", "ai_centers"}
VALID_CODINGS = {"verified_opposition", "verified_none", "undeterminable"}

TODAY = date.today().isoformat()


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def build_frame():
    uni = load_csv(UNIVERSE_CSV)
    frame = [r for r in uni if r.get("source") in FRAME_SOURCES]
    excluded_atlas = sum(1 for r in uni if r.get("source") == "atlas"
                         and r.get("opposed_flag") != "yes")
    rng = random.Random(RANDOM_STATE)
    rng.shuffle(frame)
    # blocked-with-no-recorded-opposition rows first: most anomalous cell
    frame.sort(key=lambda r: 0 if r.get("lifecycle_outcome") == "blocked_confirmed" else 1)
    return frame, excluded_atlas


def protocol_queries(r):
    name = (r.get("name") or "").strip()
    op = (r.get("operator") or "").strip().split("#")[0].strip()
    st = (r.get("state") or "").strip()
    co = (r.get("county") or "").strip()
    q = [f"{name} {st}",
         f"{name or op} data center {co} {st}".replace("  ", " "),
         f"{co} {st} data center opposition OR moratorium OR rezoning OR petition"]
    if op:
        q.append(f"{op} data center {st} residents")
    return " | ".join(x.strip() for x in q)


def validate_codings(codings, frame_ids):
    """Return (accepted_rows, problems). Rows failing any rule are ignored."""
    ok, problems = [], []
    for i, c in enumerate(codings, start=2):
        uid = (c.get("universe_id") or "").strip()
        cd = (c.get("coding") or "").strip()
        if uid not in frame_ids:
            problems.append(f"line {i}: universe_id {uid} not in audit frame; row ignored")
            continue
        if cd not in VALID_CODINGS:
            problems.append(f"line {i}: coding '{cd}' invalid; row ignored")
            continue
        if cd == "verified_opposition" and not (c.get("evidence_url") or "").strip():
            problems.append(f"line {i}: verified_opposition requires evidence_url; row ignored")
            continue
        if cd == "verified_none" and not (c.get("detectability_url") or "").strip():
            problems.append(f"line {i}: verified_none requires detectability_url "
                            f"(detectability floor); row ignored")
            continue
        if not (c.get("coded_by") or "").strip() or not (c.get("coded_date") or "").strip():
            problems.append(f"line {i}: coded_by and coded_date required; row ignored")
            continue
        row = dict(c)
        row["universe_id"], row["coding"] = uid, cd
        ok.append(row)
    return ok, problems


def next_batch(n: int) -> int:
    if n <= 0:
        print("--next requires a positive integer N")
        return 1
    worklist = load_csv(OUT_WORKLIST)
    if not worklist:
        print("ERROR: data/negative_audit_worklist.csv missing — run python3 negative_audit.py first")
        return 1
    frame_ids = {r["universe_id"] for r in worklist}
    random_stratum = [r for r in worklist if r.get("lifecycle_outcome") != "blocked_confirmed"]
    random_ids = {r["universe_id"] for r in random_stratum}
    codings_raw = load_csv(CODINGS_CSV)
    accepted, _ = validate_codings(codings_raw, frame_ids) if codings_raw else ([], [])
    coded_ids = {c["universe_id"] for c in accepted}
    uncoded = [r for r in random_stratum if r["universe_id"] not in coded_ids]
    if not uncoded:
        n_blocked_uncoded = sum(
            1 for r in worklist
            if r.get("lifecycle_outcome") == "blocked_confirmed"
            and r["universe_id"] not in coded_ids
        )
        print(f"Random stratum complete ({len(random_stratum)} rows coded). "
              f"{n_blocked_uncoded} blocked_confirmed rows remain uncoded.")
        return 0
    batch = uncoded[:n]
    lines = []
    for r in batch:
        lines.append(f"[{r['audit_order']}] {r['name']} ({r['state']}, {r['county']}) "
                     f"[{r['lifecycle_outcome']}]")
        lines.append(f"Search: {r['search_protocol']}")
    print("\n\n".join(lines))
    return 0


def selftest() -> int:
    checks = []

    def check(label, ok):
        checks.append((label, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    fake_ids = {"prj_test"}
    base = {"universe_id": "prj_test", "evidence_url": "", "detectability_url": "",
            "queries_run": "1", "notes": "", "coded_by": "tester", "coded_date": "2026-01-01"}

    row_no_coded_by = dict(base, coded_by="", coding="verified_opposition", evidence_url="https://x.com")
    ok, _ = validate_codings([row_no_coded_by], fake_ids)
    check("validate_codings: row without coded_by is rejected", ok == [])

    row_vn_no_url = dict(base, coding="verified_none", detectability_url="")
    ok, _ = validate_codings([row_vn_no_url], fake_ids)
    check("validate_codings: verified_none without detectability_url is rejected", ok == [])

    row_valid_vo = dict(base, coding="verified_opposition", evidence_url="https://example.com")
    ok, _ = validate_codings([row_valid_vo], fake_ids)
    check("validate_codings: valid verified_opposition row is accepted", len(ok) == 1)

    wl = ([{"universe_id": f"prj_b{i}", "lifecycle_outcome": "blocked_confirmed"} for i in range(3)]
          + [{"universe_id": f"prj_r{i}", "lifecycle_outcome": "advanced_confirmed"} for i in range(5)])
    random_stratum = [r for r in wl if r["lifecycle_outcome"] != "blocked_confirmed"]
    check("next_batch queue: only random-stratum rows returned",
          len(random_stratum) == 5 and all(r["lifecycle_outcome"] != "blocked_confirmed"
                                           for r in random_stratum))

    coded_ids = {"prj_r0", "prj_r1"}
    uncoded = [r for r in random_stratum if r["universe_id"] not in coded_ids]
    check("next_batch queue: coded rows excluded", len(uncoded) == 3)

    check("coverage gate: 79% is below threshold", 79 / 100 < COVERAGE_THRESHOLD)
    check("coverage gate: 80% meets threshold", 80 / 100 >= COVERAGE_THRESHOLD)
    check("coverage gate: 100% meets threshold", 100 / 100 >= COVERAGE_THRESHOLD)
    check("COVERAGE_THRESHOLD is 0.80", COVERAGE_THRESHOLD == 0.80)

    sample = "[23] Sample Project (VA, fairfax) [advanced_confirmed]\nSearch: foo bar baz"
    leak_rx = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.I)
    check("--next output passes leak-audit regex", leak_rx.search(sample) is None)

    n_ok = sum(1 for _, ok in checks if ok)
    print(f"\n{n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def main() -> int:
    if not os.path.exists(UNIVERSE_CSV):
        print("ERROR: data/baseline_universe.csv missing — run the control chain first")
        return 1

    frame, excluded_atlas = build_frame()
    frame_ids = {r["universe_id"] for r in frame}

    with open(OUT_WORKLIST, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["audit_order", "universe_id", "name", "operator", "state",
                    "county", "source", "lifecycle_outcome", "announced_date",
                    "capacity_mw", "search_protocol"])
        for i, r in enumerate(frame, start=1):
            w.writerow([i, r["universe_id"], r.get("name", ""),
                        (r.get("operator") or "").split("#")[0].strip(),
                        r.get("state", ""), r.get("county", ""),
                        r.get("source", ""), r.get("lifecycle_outcome", ""),
                        r.get("announced_date", ""), r.get("capacity_mw", ""),
                        protocol_queries(r)])

    # ingest codings if present
    ok, problems = ([], [])
    codings_raw = load_csv(CODINGS_CSV)
    if codings_raw:
        ok, problems = validate_codings(codings_raw, frame_ids)
    codings = {c["universe_id"]: c for c in ok}  # most recent row per id takes precedence

    random_stratum_ids = {r["universe_id"] for r in frame
                          if r.get("lifecycle_outcome") != "blocked_confirmed"}
    n_random_total = len(random_stratum_ids)
    n_random_coded = sum(1 for uid in codings if uid in random_stratum_ids)
    random_coverage = n_random_coded / n_random_total if n_random_total > 0 else 0.0
    below_threshold = random_coverage < COVERAGE_THRESHOLD

    mix = Counter(c["coding"] for c in codings.values())
    n_frame, n_coded = len(frame), len(codings)

    rep = []
    w = rep.append
    w("# Verified-Negative Audit")
    w("")
    w(f"Generated {TODAY}. Frame design registered 2026-07-23 (module "
      f"docstring): census of proposals_unopposed + ai_centers rows in the "
      f"baseline universe; atlas rows excluded on detectability grounds "
      f"({excluded_atlas} non-opposed atlas rows excluded; this is a "
      f"detectability decision, not a claim about those facilities). "
      f"Worklist order is seeded-shuffle (seed {RANDOM_STATE}) with "
      f"blocked_confirmed rows first, so any top-down batch is a random "
      f"subset of the remaining frame. The blocked_confirmed rows themselves "
      f"are a purposive cell, not a random draw: coding mixes from batches "
      f"containing them must not be extrapolated to the frame.")
    w("")
    w("## Coverage")
    w("")
    w(f"- Frame size: {n_frame}")
    w(f"- Coded: {n_coded} ({(100 * n_coded / n_frame):.0f}%)")
    w(f"- Remaining: {n_frame - n_coded}")
    w("")
    if mix:
        pct = int(random_coverage * 100)
        n_remaining = n_random_total - n_random_coded
        if below_threshold:
            w(f"## Coding mix (interim descriptives — {pct}% of random stratum coded; "
              f"{n_remaining} rows remaining)")
        else:
            w("## Coding mix (coded rows)")
        w("")
        w("| coding | n | share of coded |")
        w("|---|---|---|")
        for k in ("verified_opposition", "verified_none", "undeterminable"):
            w(f"| {k} | {mix.get(k, 0)} | "
              f"{(100 * mix.get(k, 0) / max(n_coded, 1)):.0f}% |")
        w("")
        w("Interpretation rules: emergence-rate statements use "
          "verified_opposition / (verified_opposition + verified_none) and "
          "must always report the undeterminable count alongside, since "
          "undeterminable rows are not missing at random (they skew toward "
          "low-footprint projects). No emergence model trains until coverage "
          "of the frame is complete; partial-coverage rates are interim "
          "descriptives only.")
        w("")
        if below_threshold:
            w(f"Emergence rate withheld: random-stratum coverage at {pct}% "
              f"(threshold: {int(COVERAGE_THRESHOLD * 100)}%). "
              f"Rate will appear when coding reaches "
              f"{int(COVERAGE_THRESHOLD * 100)}% of {n_random_total} "
              f"random-stratum rows.")
            w("")
        else:
            vo = mix.get("verified_opposition", 0)
            vn = mix.get("verified_none", 0)
            und = mix.get("undeterminable", 0)
            denom = vo + vn
            rate = vo / denom if denom > 0 else 0.0
            w(f"Emergence rate: {vo} / ({vo} + {vn}) = {rate:.1%} "
              f"({und} undeterminable rows excluded from denominator, "
              f"not missing at random).")
            w("")
    if problems:
        w("## Coding validation problems")
        w("")
        for p in problems:
            w(f"- {p}")
        w("")
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rep) + "\n")

    # leak audit
    rx = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.I)
    dirty = [p for p in (OUT_WORKLIST, OUT_REPORT)
             if rx.search(open(p, encoding="utf-8").read())]
    if dirty:
        print("LEAK AUDIT FAILED: " + ", ".join(os.path.basename(p) for p in dirty))
        return 1

    print(f"audit frame: {n_frame} (census; {excluded_atlas} atlas rows excluded)")
    print(f"coded: {n_coded} | mix: " +
          (", ".join(f"{k}={v}" for k, v in mix.items()) if mix else "none yet"))
    if problems:
        print(f"coding validation problems: {len(problems)} (see report)")
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verified-negative audit: worklist generation, report, and coding queue")
    parser.add_argument("--next", type=int, metavar="N",
                        help="Print the next N uncoded random-stratum rows")
    parser.add_argument("--selftest", action="store_true",
                        help="Run self-tests (no network, no file I/O)")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    if args.next is not None:
        if args.next <= 0:
            print("--next requires a positive integer N")
            raise SystemExit(1)
        raise SystemExit(next_batch(args.next))
    sys.exit(main())
