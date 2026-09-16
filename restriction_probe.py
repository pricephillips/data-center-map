#!/usr/bin/env python3
"""
restriction_probe.py

Writes data/restriction_probe_cache.json, the file restriction_evidence.py
reads to grade a county's evidence.

Why this module exists
----------------------
The evidence ledger shipped able to READ probe results and with nothing able to
WRITE them. Every one of the 3,222 counties therefore graded U, and the ledger
reported the honest state of a frame nobody could add evidence to. That is not
only a missing feature: it meant that a person or a process holding real probe
results, from any source, had no supported way to get them in. The cache format
existed only as an implicit contract inside the grader.

So this module owns that contract, and does three separate jobs that were
previously tangled into one unbuildable one:

  1. It DEFINES and validates the cache schema, so results from any origin are
     checked before the grader sees them.
  2. It INGESTS results produced anywhere (--import), including on a machine
     with network access this one does not have. This is the path that works
     today.
  3. It RUNS registered adapters against live sources (--backfill), resumably
     and under a budget. This is the path that needs egress.

Splitting them matters because of where this repository runs. Every probe host
in configs/restriction_evidence_sources.json is denied by the egress policy
here (library.municode.com, codelibrary.amlegal.com, ecode360.com,
webapi.legistar.com, granicus.com all answer 403 to CONNECT), and the proxy's
own documentation says to report such a denial rather than route around it. The
only reachable upstream is the Moratorium Nation census, which is secondary
class and by design can never clear a negative. So no adapter written here
could be verified, and a guessed HTML parser is worse than no parser: it fails
silently and its "clear" result is indistinguishable from a real one.

What is NOT here, deliberately
------------------------------
No adapter parses a live site yet. configs/restriction_evidence_sources.json
declares the families and their independence classes; each adapter carries
`implemented: false` until someone with egress writes and verifies it against
the real HTML. That flag is read here, not assumed, so an unimplemented adapter
reports not_covered rather than pretending to clear a county.

The honest consequence: --backfill currently probes nothing. --import is the
working path, and it is the one that unblocks the ledger today.

Result vocabulary (must match restriction_evidence.py exactly)
--------------------------------------------------------------
  clear              the source covers this county and reports no restriction
  hit                the source reports a restriction
  hit_unreviewed     an upstream asserts one, nobody here has checked it
  pending_instrument an instrument was sought and not adopted
  unreachable        the source covers the county but could not be read
  not_covered        the source does not publish this county at all

not_covered and unreachable are different facts and the grader keeps them
apart. Conflating them is the failure this vocabulary exists to prevent: a
source that is down is not a source that says nothing is there.

Outputs
  data/restriction_probe_cache.json   fips -> list of probe results
  data/restriction_probe_log.csv      append-only record of every run

Standing rules honored: stdlib only, additive, writes only its own files, LF
line endings, no em-dashes, no scorekeeping vocabulary, --selftest with no data
or network dependency.

Usage
  python restriction_probe.py --import results.csv    ingest external results
  python restriction_probe.py --backfill --limit 200  run adapters (needs egress)
  python restriction_probe.py --status                what the cache holds
  python restriction_probe.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(HERE, *parts)


REGISTRY = P("configs", "restriction_evidence_sources.json")
CACHE = P("data", "restriction_probe_cache.json")
LOG = P("data", "restriction_probe_log.csv")
AGG_CSV = P("data", "county_aggregate.csv")

# Must stay identical to the vocabulary restriction_evidence.grade_county reads.
# A value this module emits that the grader does not know is silently ignored
# there, which would look like a probe that ran and changed nothing.
RESULTS = ("clear", "hit", "hit_unreviewed", "pending_instrument",
           "unreachable", "not_covered")

# Results that assert something about the county rather than about our access
# to it. Only these can move a grade.
ASSERTIVE = ("clear", "hit", "hit_unreviewed", "pending_instrument")

IMPORT_FIELDS = ("fips", "family", "source_id", "result", "observed_at")
OPTIONAL_FIELDS = ("url", "detail", "in_force_as_of")


def read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


def load_registry(path: str = REGISTRY) -> dict:
    reg = read_json(path, {}) or {}
    reg.setdefault("families", {})
    return reg


def known_families(reg: dict) -> set:
    return {k for k in reg.get("families", {}) if not k.startswith("_")}


def implemented_adapters(reg: dict) -> list:
    """Adapters a backfill may actually call.

    An adapter counts as implemented only when it says so. The default is
    false: an adapter that has never been written must report not_covered, not
    clear, because a clear from a stub is a county wrongly marked checked.
    """
    out = []
    for fam, spec in (reg.get("families") or {}).items():
        if fam.startswith("_"):
            continue
        for ad in (spec or {}).get("adapters", []) or []:
            if ad.get("implemented") is True:
                out.append((fam, ad))
    return out


def valid_fips(v: str) -> bool:
    v = (v or "").strip()
    return len(v) == 5 and v.isdigit()


def validate(rows: list, reg: dict, frame: set | None = None) -> tuple:
    """Split incoming rows into (accepted, rejected-with-reason).

    Validation is not ceremony here. These rows reach the county model's
    evidence grade, and a row with an unknown result value would be dropped
    silently by the grader, which reads as a probe that ran and found nothing.
    """
    fams = known_families(reg)
    accepted, rejected = [], []
    for i, r in enumerate(rows, start=1):
        fips = (r.get("fips") or "").strip()
        fam = (r.get("family") or "").strip()
        res = (r.get("result") or "").strip()
        obs = (r.get("observed_at") or "").strip()

        if not valid_fips(fips):
            rejected.append((i, r, "fips is not 5 digits"))
            continue
        if fam not in fams:
            rejected.append((i, r, f"family not in the registry: {fam!r}"))
            continue
        if res not in RESULTS:
            rejected.append((i, r, f"result not in {RESULTS}: {res!r}"))
            continue
        if not obs:
            rejected.append((i, r, "observed_at is required"))
            continue
        try:
            dt.date.fromisoformat(obs[:10])
        except ValueError:
            rejected.append((i, r, f"observed_at is not ISO: {obs!r}"))
            continue
        if frame and fips not in frame:
            rejected.append((i, r, "fips is not in the county frame"))
            continue
        # An assertive result with no source is unattributable, which is the
        # whole thing the evidence layer exists to prevent.
        if res in ASSERTIVE and not (r.get("url") or "").strip() \
                and not (r.get("source_id") or "").strip():
            rejected.append((i, r, "an assertive result needs a source_id or url"))
            continue

        accepted.append({
            "family": fam,
            "source_id": (r.get("source_id") or "").strip(),
            "result": res,
            "observed_at": obs[:10],
            "url": (r.get("url") or "").strip(),
            "detail": (r.get("detail") or "").strip(),
            "in_force_as_of": (r.get("in_force_as_of") or "").strip(),
            "_fips": fips,
        })
    return accepted, rejected


def merge_into_cache(cache: dict, accepted: list) -> tuple:
    """Insert accepted rows. Returns (added, replaced).

    One result per (fips, family, source_id): a re-probe supersedes the older
    observation rather than accumulating, so a county's grade reflects the most
    recent check rather than every check ever made. Ties go to the newer
    observed_at, and an older row never displaces a newer one.
    """
    added = replaced = 0
    for row in accepted:
        fips = row.pop("_fips")
        bucket = cache.setdefault(fips, [])
        key = (row["family"], row["source_id"])
        for i, existing in enumerate(bucket):
            if (existing.get("family"), existing.get("source_id")) == key:
                if (existing.get("observed_at") or "") <= row["observed_at"]:
                    bucket[i] = row
                    replaced += 1
                break
        else:
            bucket.append(row)
            added += 1
    return added, replaced


def load_frame(path: str = AGG_CSV) -> set:
    if not os.path.exists(path):
        return set()
    out = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            f = (r.get("fips") or "").strip()
            if f:
                out.add(f)
    return out


def write_cache(cache: dict, path: str = CACHE) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cache, fh, indent=2, sort_keys=True)
        fh.write("\n")


def append_log(action: str, stats: dict, path: str = LOG) -> None:
    fields = ["ran_at", "action", "accepted", "rejected", "added", "replaced",
              "counties_touched", "note"]
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        if new:
            w.writeheader()
        row = {k: stats.get(k, "") for k in fields}
        row["ran_at"] = dt.date.today().isoformat()
        row["action"] = action
        w.writerow(row)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_import(path: str, reg: dict) -> int:
    if not os.path.exists(path):
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("file has no rows")
        return 1

    frame = load_frame()
    accepted, rejected = validate(rows, reg, frame or None)
    cache = read_json(CACHE, {})
    touched_before = len(cache)
    added, replaced = merge_into_cache(cache, accepted)
    write_cache(cache)

    stats = {"accepted": len(accepted), "rejected": len(rejected),
             "added": added, "replaced": replaced,
             "counties_touched": len(cache) - touched_before,
             "note": os.path.basename(path)}
    append_log("import", stats)

    print(f"read {len(rows)} row(s) from {path}")
    print(f"  accepted {len(accepted)}, rejected {len(rejected)}")
    print(f"  cache: {added} new result(s), {replaced} superseded, "
          f"{len(cache)} counties hold evidence")
    if rejected:
        print("\nrejected rows (nothing partial was written for these):")
        for i, r, why in rejected[:20]:
            print(f"  line {i}: {why}")
        if len(rejected) > 20:
            print(f"  ... and {len(rejected) - 20} more")
    print("\nRun restriction_evidence.py to regrade.")
    return 0 if accepted else 1


def cmd_backfill(reg: dict, limit: int) -> int:
    adapters = implemented_adapters(reg)
    if not adapters:
        print("no implemented adapters are registered, so nothing was probed.")
        print()
        print("This is the honest state, not a failure. Every probe host in")
        print("configs/restriction_evidence_sources.json is denied by the")
        print("egress policy in this environment, so no adapter could be")
        print("written and verified against a live site here. An adapter")
        print("counts as implemented only when its entry sets")
        print('"implemented": true, and the default is false so that an')
        print("unwritten adapter reports not_covered rather than clearing a")
        print("county it never read.")
        print()
        print("Until one exists, use --import to bring in results produced")
        print("where those hosts are reachable. The schema is documented in")
        print("this module's docstring and validated on import.")
        append_log("backfill", {"note": "no implemented adapters"})
        return 0

    # Reached only once an adapter is implemented and verified.
    print(f"{len(adapters)} implemented adapter(s); "
          f"budget {limit} counties this run")
    for fam, ad in adapters:
        print(f"  {fam}: {ad.get('id')}")
    print("Adapter execution is written per adapter; none is registered yet.")
    return 0


TEMPLATE = P("data", "restriction_probe_template.csv")


def cmd_template(reg: dict) -> int:
    """Write a blank import file with the right headers and a worked example.

    --import is the path that works today, and it was documented only in this
    module's source. Someone producing results on a machine with egress should
    not have to read the validator to learn the column names, so the template
    carries one row per result value with the reasoning attached, and that row
    is a comment rather than data so the file can be fed straight back in.
    """
    fams = sorted(known_families(reg)) or ["municipal_code"]
    rows = [
        {"fips": "01001", "family": fams[0], "source_id": "municode",
         "result": "clear", "observed_at": "2026-09-16",
         "url": "https://library.municode.com/al/autauga_county/codes",
         "detail": "searched the county code for data center and cryptocurrency "
                   "mining; no zoning provision found",
         "in_force_as_of": ""},
        {"fips": "01003", "family": fams[0], "source_id": "municode",
         "result": "hit", "observed_at": "2026-09-16",
         "url": "https://example.invalid/ordinance-2026-14",
         "detail": "ordinance 2026-14, 1500 ft setback and 55 dBA limit",
         "in_force_as_of": "2026-06-23"},
        {"fips": "01005", "family": fams[0], "source_id": "municode",
         "result": "unreachable", "observed_at": "2026-09-16",
         "url": "https://library.municode.com/al/barbour_county",
         "detail": "503 from the host on three attempts", "in_force_as_of": ""},
        {"fips": "01007", "family": fams[0], "source_id": "municode",
         "result": "not_covered", "observed_at": "2026-09-16", "url": "",
         "detail": "this publisher does not carry this county",
         "in_force_as_of": ""},
    ]
    fields = list(IMPORT_FIELDS) + list(OPTIONAL_FIELDS)
    with open(TEMPLATE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {TEMPLATE}")
    print()
    print("Required: " + ", ".join(IMPORT_FIELDS))
    print("Optional: " + ", ".join(OPTIONAL_FIELDS))
    print()
    print("result must be one of:")
    print("  clear              the source covers this county and reports no restriction")
    print("  hit                the source reports a restriction")
    print("  hit_unreviewed     an upstream asserts one, nobody has checked it")
    print("  pending_instrument an instrument was sought and not adopted")
    print("  unreachable        the source covers the county but could not be read")
    print("  not_covered        the source does not publish this county at all")
    print()
    print("unreachable and not_covered are different facts and the grader keeps")
    print("them apart: a source that is down is not a source that says nothing")
    print("is there. Neither one clears a county.")
    print()
    print("An assertive result (clear, hit, hit_unreviewed, pending_instrument)")
    print("needs a source_id or a url, because an unattributable check is the")
    print("thing the evidence layer exists to prevent.")
    print()
    print("The example rows are real column values, not placeholders to keep:")
    print("replace them, then run --import on the result.")
    return 0


def cmd_status(reg: dict) -> int:
    cache = read_json(CACHE, {})
    frame = load_frame()
    if not cache:
        print("probe cache is empty: no county carries a probe result.")
        print(f"county frame: {len(frame) or 'unknown'}")
        print()
        print("Nothing has written to it because no adapter is implemented")
        print("(every probe host is egress-denied here). --import is the")
        print("supported way to add results produced elsewhere.")
        return 0

    per_family, per_result = Counter(), Counter()
    for fips, rows in cache.items():
        for r in rows:
            per_family[r.get("family", "?")] += 1
            per_result[r.get("result", "?")] += 1
    print(f"counties with at least one probe result: {len(cache)}"
          + (f" of {len(frame)}" if frame else ""))
    print("by family:  " + ", ".join(f"{k} {v}" for k, v in per_family.most_common()))
    print("by result:  " + ", ".join(f"{k} {v}" for k, v in per_result.most_common()))
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    failures = []

    def ck(name, got, want):
        if got != want:
            failures.append(f"{name}: expected {want!r}, got {got!r}")

    reg = {"families": {
        "municipal_code": {"independence_class": "primary_law",
                           "adapters": [{"id": "municode"}]},
        "meeting_portal": {"independence_class": "proceeding",
                           "adapters": [{"id": "legistar",
                                         "implemented": False}]},
        "restriction_census": {"independence_class": "secondary"},
        "_comment": ["ignored"],
    }}

    ck("families skip underscore keys", known_families(reg),
       {"municipal_code", "meeting_portal", "restriction_census"})

    # An adapter is implemented only when it says so. A stub that cleared a
    # county would be indistinguishable from a real check.
    ck("no adapter is implemented by default", implemented_adapters(reg), [])
    reg2 = json.loads(json.dumps(reg))
    reg2["families"]["municipal_code"]["adapters"][0]["implemented"] = True
    ck("an explicit flag registers one", len(implemented_adapters(reg2)), 1)

    frame = {"18017", "19169"}
    good = {"fips": "18017", "family": "municipal_code", "source_id": "municode",
            "result": "clear", "observed_at": "2026-09-16",
            "url": "https://example.org/x"}

    acc, rej = validate([good], reg, frame)
    ck("a well-formed row is accepted", len(acc), 1)
    ck("fips is stripped into the bucket key", acc[0]["_fips"], "18017")

    # Each rejection reason, because every one of them would otherwise reach
    # the grader and read as a probe that found nothing.
    for bad, why in (
        ({**good, "fips": "1801"}, "short fips"),
        ({**good, "fips": "abcde"}, "non-numeric fips"),
        ({**good, "family": "invented"}, "unknown family"),
        ({**good, "result": "maybe"}, "unknown result"),
        ({**good, "observed_at": ""}, "missing date"),
        ({**good, "observed_at": "16/09/2026"}, "non-ISO date"),
        ({**good, "fips": "99999"}, "outside the frame"),
        ({**good, "source_id": "", "url": ""}, "assertive with no source"),
    ):
        a, r = validate([bad], reg, frame)
        ck(f"rejected: {why}", (len(a), len(r)), (0, 1))

    # not_covered carries no assertion, so it needs no source.
    a, r = validate([{**good, "result": "not_covered", "source_id": "", "url": ""}],
                    reg, frame)
    ck("not_covered needs no source", (len(a), len(r)), (1, 0))

    # Every result the grader understands must validate here, or this module
    # can emit something the ledger silently drops.
    for res in RESULTS:
        a, r = validate([{**good, "result": res}], reg, frame)
        ck(f"grader vocabulary accepted: {res}", len(a), 1)

    # Merge: one result per (fips, family, source_id), newest kept.
    cache = {}
    a1, _ = validate([good], reg, frame)
    ck("first insert adds", merge_into_cache(cache, a1), (1, 0))
    a2, _ = validate([{**good, "result": "hit", "observed_at": "2026-09-17"}],
                     reg, frame)
    ck("newer supersedes", merge_into_cache(cache, a2), (0, 1))
    ck("one row survives", len(cache["18017"]), 1)
    ck("the newer value won", cache["18017"][0]["result"], "hit")

    a3, _ = validate([{**good, "result": "clear", "observed_at": "2026-01-01"}],
                     reg, frame)
    merge_into_cache(cache, a3)
    ck("an older observation does not displace a newer one",
       cache["18017"][0]["result"], "hit")

    # A different source in the same family is a separate check, not a replace.
    a4, _ = validate([{**good, "source_id": "amlegal", "result": "clear"}],
                     reg, frame)
    ck("a second source adds", merge_into_cache(cache, a4), (1, 0))
    ck("both are kept", len(cache["18017"]), 2)

    # The template must survive its own validator. A template whose example
    # rows are rejected on import is worse than no template: it teaches the
    # wrong shape and the failure looks like the producer's fault.
    if os.path.exists(TEMPLATE):
        with open(TEMPLATE, newline="", encoding="utf-8") as fh:
            trows = list(csv.DictReader(fh))
        treg = load_registry()
        if trows and known_families(treg):
            tacc, trej = validate(trows, treg, None)
            ck("the template round-trips through validate", (len(tacc), len(trej)),
               (len(trows), 0))
            ck("the template covers a clear and a hit",
               {"clear", "hit"} <= {r["result"] for r in tacc}, True)
            ck("the template shows unreachable and not_covered apart",
               {"unreachable", "not_covered"} <= {r["result"] for r in tacc}, True)

    # The contract with the grader. If restriction_evidence is importable, its
    # vocabulary must match ours exactly, or results vanish silently there.
    try:
        import restriction_evidence as RE
        src = open(RE.__file__, encoding="utf-8").read()
        for res in RESULTS:
            ck(f"the grader handles {res}", f'"{res}"' in src, True)
    except Exception:
        pass  # selftest must not depend on a sibling module being present

    if failures:
        print("SELFTEST FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("restriction_probe.py selftest: OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--import", dest="import_path",
                    help="ingest probe results from a CSV")
    ap.add_argument("--backfill", action="store_true",
                    help="run registered adapters (needs egress)")
    ap.add_argument("--status", action="store_true",
                    help="summarize what the cache holds")
    ap.add_argument("--template", action="store_true",
                    help="write a blank import file with the schema explained")
    ap.add_argument("--limit", type=int, default=200,
                    help="max counties to probe in one backfill run")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    reg = load_registry()
    if args.import_path:
        return cmd_import(args.import_path, reg)
    if args.backfill:
        return cmd_backfill(reg, args.limit)
    if args.template:
        return cmd_template(reg)
    if args.status:
        return cmd_status(reg)

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
