
#!/usr/bin/env python3
"""refresh_external_census.py

Refreshes the external restriction census against the current
Moratorium Nation dataset.

  --refresh     fetch upstream and write a delta file
  --promote     fetch upstream and APPEND gate-passing rows to the census
  --hold-thin   with --promote, hold promotions resting on one source that
                flip a county label with no primary-source URL
  --force       with --promote, append even if the batch QC gate refuses
  --selftest    offline invariants

Two gates, answering different questions, added 2026-09-16.

gate_row() is per row: is this well formed, and does upstream assert it. It
cannot see the shape of a run. If upstream ships four hundred rows in a week,
or its row count collapses because a fetch truncated, or every row for one
state changes at once, each row passes and the batch is still wrong.

batch_qc() is per run and refuses the whole append rather than writing a bad
batch one good-looking row at a time. It checks volume against the trailing
median, upstream shrinkage, single-state concentration, thin-promotion volume,
and hold-rate collapse. That last one is the subtle case: the gate leans on
upstream's own uncertainty markers, so if upstream stops setting them the gate
quietly stops holding anything and the report reads as a sudden quality
improvement. It is the brake coming off. Nothing else here would notice.

confidence_tier() then says how much sits behind each individual promotion:
corroborated (the tracker already holds a restrictive record for the county),
single_source, or thin (one source, flips the label, no primary-source URL).
The tier is written to data/census_promotion_report.csv per row, so an
unattended run stays reviewable afterwards rather than reconstructable. On the
2026-09 upstream the split is 33 corroborated to 35 thin.

Correction, 2026-09-09: this docstring previously said the delta was
written "for coverage_audit.py and restriction_worklist.py to consume".
Neither module references it -- both read the seeded census directly --
so the delta has no automated consumer and is a worklist for a person,
in the same sense as data/permit_candidates_*.csv.

That was recorded as the intended shape. It was not: the delta had no
consumer because it was unusable. normalize_upstream_rows() shipped as a
placeholder, still carrying its own "TODO: adjust these field names"
marker, and every field it read was wrong against the real upstream
header. See that function for the itemized list. A delta of 533 rows
carrying full state names, cities, townships, empty statuses and
free-text dates could not have been consumed by anything.

Both halves are repaired as of 2026-09-15. The normalizer now filters to
county-level data-center rows and maps the real columns, and --promote
appends gate-passing rows to the census automatically, replacing manual
review at Price's direction.

Posture change, stated plainly. This module used to refuse to write
data/external_restriction_census.csv and that refusal is gone: the census
is now maintained automatically rather than by hand, and this module is
its sole writer. Appends only, so a hand-corrected row is never
clobbered. What did NOT change is the path from a census row to a county
label: a promoted row still has to pass census_gap_candidates.py's own
gate before it becomes a tracker record. The census is still a pointer,
not a source of record for any fact; it is simply a pointer that now
keeps itself current.

The workflow that runs this had never succeeded before 2026-09-09. It
invoked a --merge flag this module has never accepted, and its commit
step staged the census (read-only at the time) rather than the delta.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import os
import re
import sys
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))

EXTERNAL_CSV = os.path.join(HERE, "data", "external_restriction_census.csv")
DELTA_CSV = os.path.join(HERE, "data", "external_restriction_census_delta.csv")
REPORT_MD = os.path.join(HERE, "data", "external_restriction_census_refresh_report.md")
PROMOTION_REPORT = os.path.join(HERE, "data", "census_promotion_report.csv")
AGG_CSV = os.path.join(HERE, "data", "county_aggregate.csv")
FIPS_LOOKUP_JSON = os.path.join(HERE, "data", "county_fips_lookup.json")

# NOTE: adjust this URL if Moratorium Nation changes its repo or path.
# Path corrected 2026-08-21: the inventory lives under data/ in the
# upstream repo; the bare-root path 404s.
UPSTREAM_URL = (
    "https://raw.githubusercontent.com/mjbommar/moratorium-data-2026/"
    "main/data/moratorium_inventory.csv"
)


def load_local_census() -> list[dict]:
    if not os.path.exists(EXTERNAL_CSV):
        raise FileNotFoundError(EXTERNAL_CSV)
    with open(EXTERNAL_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_moratorium_csv() -> list[dict]:
    resp = urllib.request.urlopen(UPSTREAM_URL)
    text = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


COUNTY_JURISDICTION_TYPES = {"county", "parish"}
DATA_CENTER_SECTOR = "data_center"


def _norm_join(name: str) -> str:
    """Normalization used ONLY for delta comparison, never for output.

    Both sides spell a county differently ("Carroll County" upstream,
    "Carroll County" locally, but "Athens-Clarke County" against "Clarke
    County"), so comparing raw strings reported every upstream row as new.
    """
    s = (name or "").split(",")[0].strip().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(county|parish|borough|municipio)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def normalize_upstream_rows(upstream_rows: list[dict]) -> list[dict]:
    """Map Moratorium Nation's schema into the tracker census schema.

    Target schema:
      state, county, instrument, census_status, date_enacted, source

    Repaired 2026-09-15. This function shipped as a placeholder and still
    carried its own "TODO: adjust these field names to match the actual
    Moratorium Nation CSV" marker. Every one of its field reads was wrong
    against the real upstream header, and each error pushed in the same
    direction: the delta was unusable, which is why the module's docstring
    records that the delta "has no automated consumer". The delta was not
    lacking a consumer because promoting a census row is a review decision.
    It was lacking one because nothing could have consumed it.

      state          read row["state"], which upstream spells in full
                     ("Alabama"), while the seeded census and every joiner in
                     the repo use the abbreviation. state_abbrev was in the
                     fallback position and therefore never reached.
      jurisdiction   taken with no jurisdiction_type filter, so 173 City, 71
                     Township, 47 Town and 16 Village rows entered a census
                     the coverage audit reads as county-level. Those rows
                     cannot join the county frame and would have been counted
                     as unjoined census defects.
      status         read row["status"], a column upstream does not have. The
                     fallback row["census_status"] does not exist either, so
                     census_status came out empty on all 533 rows, and an
                     empty status cannot be told apart from a pending one.
      date_enacted   read the free-text column ("On or about 2026-06-25, per
                     WBRC reporting of the city council vote") rather than
                     date_enacted_iso, which upstream publishes and populates
                     on 199 of 206 county rows.
      sectors        no filter, so solar, wind and crypto-only moratoria
                     entered a data-center census.

    Net effect: 533 rows of the wrong shape, against 206 genuinely relevant
    ones. The seeded census holds 94 rows across 17 states while upstream
    carries 206 data-center county rows across 32 states, so the repair makes
    roughly 112 county-level restriction records in 15 additional states
    reviewable for the first time.

    instrument stays "moratorium". legal_basis is free-text prose upstream
    ("Board of Supervisors resolution/ordinance (exact instrument number not
    confirmed in this pass)") and cannot be mapped to the census vocabulary
    without inventing a classification, and 91 of the 94 seeded rows already
    carry "moratorium".

    This function itself only normalizes; it writes nothing. Whether a
    normalized row reaches the census is decided by gate_row() under
    --promote, never here.
    """
    normalized: list[dict] = []

    for row in upstream_rows:
        jtype = (row.get("jurisdiction_type") or "").strip().lower()
        if jtype not in COUNTY_JURISDICTION_TYPES:
            continue

        sectors = (row.get("sectors") or "").lower()
        if DATA_CENTER_SECTOR not in sectors:
            continue

        state = (row.get("state_abbrev") or "").strip().upper()
        county = (row.get("jurisdiction") or "").strip()
        if not state or not county:
            continue  # skip rows that cannot be keyed cleanly

        # enacted_status carries the census vocabulary (active, extended,
        # replaced, expired, rescinded, pending). current_status is prose.
        status = (row.get("enacted_status") or "").strip().lower()

        # ISO only. A free-text date would be carried into the census as if
        # it were a date and would fail every downstream parse.
        date = (row.get("date_enacted_iso") or "").strip()

        source_id = row.get("moratorium_id") or ""
        if source_id:
            source = f"moratorium-nation:{source_id} (CC-BY-4.0, github.com/mjbommar/moratorium-data-2026)"
        else:
            source = "moratorium-nation:unknown-id (CC-BY-4.0, github.com/mjbommar/moratorium-data-2026)"

        normalized.append(
            {
                "state": state,
                "county": county,
                "instrument": "moratorium",
                "census_status": status,
                "date_enacted": date,
                "source": source,
            }
        )

    return normalized


def make_row_key(row: dict) -> tuple:
    """Key rows at the county level for delta comparison.

    Was keyed on (state, county, instrument, date_enacted) with raw strings.
    Because the two sides disagreed on state spelling and date format, no
    upstream row ever matched a local one and all 533 were reported as new.
    The date is also the wrong key component: an upstream row whose date was
    later corrected would read as a new county rather than as a changed
    episode. Recall is a property of counties, the same unit coverage_audit.py
    settled on, so the key is the county.
    """
    return (
        (row.get("state") or "").strip().upper(),
        _norm_join(row.get("county") or ""),
    )


def compute_delta(local_rows: list[dict], upstream_rows: list[dict]) -> list[dict]:
    local_keys = {make_row_key(r) for r in local_rows}
    delta: list[dict] = []

    for row in upstream_rows:
        key = make_row_key(row)
        if key in local_keys:
            continue
        delta.append(row)

    return delta


def write_delta_csv(delta_rows: list[dict]) -> None:
    if not delta_rows:
        # If there is no delta, remove any stale file so CI can see a clean no-op.
        if os.path.exists(DELTA_CSV):
            os.remove(DELTA_CSV)
        return

    fieldnames = [
        "state",
        "county",
        "instrument",
        "census_status",
        "date_enacted",
        "source",
    ]
    with open(DELTA_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in delta_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_refresh_report(local_rows: list[dict], upstream_rows: list[dict],
                         delta_rows: list[dict]) -> None:
    counts = Counter(r.get("census_status") or "(blank)" for r in upstream_rows)
    local_states = {(r.get("state") or "").strip().upper() for r in local_rows}
    local_states.discard("")
    up_states = {(r.get("state") or "").strip().upper() for r in upstream_rows}
    up_states.discard("")
    new_states = sorted(up_states - local_states)

    with open(REPORT_MD, "w", encoding="utf-8", newline="\n") as f:
        # String literals repaired 2026-08-21: raw newlines had been
        # written inside these strings, a double-escaping casualty that made
        # the whole module a SyntaxError, so the refresh workflow failed at
        # parse time on every scheduled run.
        f.write("# External restriction census refresh\n\n")
        f.write("Upstream rows are filtered to county-level, data-center-sector "
                "rows before anything below is counted. The delta is keyed on "
                "the county, not the episode.\n\n")
        f.write(f"- Local seeded census rows: {len(local_rows)} "
                f"across {len(local_states)} states\n")
        f.write(f"- Upstream rows (county-level, data-center sector): "
                f"{len(upstream_rows)} across {len(up_states)} states\n")
        f.write(f"- Delta rows (counties absent from the local census): "
                f"{len(delta_rows)}\n\n")
        if new_states:
            f.write(f"## States upstream covers and the local census does not "
                    f"({len(new_states)})\n\n")
            f.write(", ".join(new_states) + "\n\n")
        if delta_rows:
            f.write("## Status distribution in upstream\n\n")
            for status, n in sorted(counts.items()):
                f.write(f"- {status}: {n}\n")
            f.write("\n")
        f.write("Promoting a delta row into "
                "data/external_restriction_census.csv remains a review "
                "decision. This module never writes the census.\n")


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------
#
# Added 2026-09-15, replacing manual review of the delta at Price's direction.
# This follows the pattern census_gap_candidates.py established on 2026-08-25
# and promote_signal_candidates.py on 2026-08-12: a blocking per-row gate
# stands in for the human, rows that fail stay visible as the exception queue,
# and every decision lands in an append-only report.
#
# What changed in posture, stated plainly. The seeded census used to be hand
# maintained and this module refused to write it. It is now maintained
# automatically. What did NOT change is where a fact comes from: a promoted row
# carries its upstream citation, and it still has to pass
# census_gap_candidates.py's own gate (complete, dated, http source URL,
# dedup-guarded, still an open coverage gap) before it becomes a tracker record
# and moves a county label. This gate widens what the census covers; it does
# not shorten the path from a census row to a label.
#
# The gate is deliberately stricter than a person skimming a list, because it
# runs unattended. It holds on upstream's own uncertainty markers rather than
# second-guessing them: has_verify_tags is upstream saying it has not confirmed
# the row, and date_enacted_uncertainty == "unverified" is upstream saying it
# could not pin the date. Promoting either would put an unconfirmed instrument
# into the label under an automated process with nobody reading it.

GATE_REASONS = (
    "already in census",
    "does not join county frame",
    "pending, not enacted",
    "no ISO date",
    "no source citation",
    "upstream verify tag",
    "date unverified",
)


def load_county_frame(path: str = AGG_CSV) -> set:
    """(state, normalized county) for every county in the national frame.

    A row that cannot join the frame is a name defect, not a coverage result,
    and must never enter the census: coverage_audit.py would read it back as an
    unjoined census row on every future run.
    """
    if not os.path.exists(path):
        return set()
    out = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            st = (r.get("state") or "").strip().upper()
            cty = _norm_join(r.get("county_name") or "")
            if st and cty:
                out.add((st, cty))
    return out


def gate_row(raw: dict, norm: dict, held_keys: set, frame: set) -> str:
    """Return "" to promote, or the reason to hold.

    Ordered cheapest disqualifier first, so the report reads as a funnel.
    """
    key = make_row_key(norm)
    if key in held_keys:
        return "already in census"
    if frame and key not in frame:
        return "does not join county frame"
    if (norm.get("census_status") or "").strip().lower() == "pending":
        return "pending, not enacted"
    if not (norm.get("date_enacted") or "").strip():
        # A dateless row would enter the census and then be held out of the
        # clean feed downstream as incomplete, leaving the label pointing at a
        # record the feed cannot see. census_gap_candidates.py holds on the
        # same condition for the same reason.
        return "no ISO date"
    if not (norm.get("source") or "").strip():
        return "no source citation"
    if (raw.get("has_verify_tags") or "").strip() == "True":
        return "upstream verify tag"
    if (raw.get("date_enacted_uncertainty") or "").strip() == "unverified":
        return "date unverified"
    return ""


def promote(local_rows: list[dict], upstream_raw: list[dict],
            frame: set) -> tuple:
    """Gate every upstream row. Returns (promoted, decisions)."""
    held_keys = {make_row_key(r) for r in local_rows}
    decisions, promoted = [], []

    for raw in upstream_raw:
        norm_one = normalize_upstream_rows([raw])
        if not norm_one:
            continue  # not county-level, or not a data-center instrument
        norm = norm_one[0]
        reason = gate_row(raw, norm, held_keys, frame)
        decisions.append((norm, reason))
        if not reason:
            promoted.append(norm)
            # Guard against two upstream episodes for one county both
            # promoting: recall is a property of counties, not of episodes.
            held_keys.add(make_row_key(norm))
    return promoted, decisions


# ---------------------------------------------------------------------------
# Confidence tiering, and the batch gate
# ---------------------------------------------------------------------------
#
# The per-row gate in gate_row() answers "is this row well formed and asserted
# by upstream". It cannot answer two other questions, and both of them are
# where an unattended promotion actually goes wrong.
#
# HOW WELL SUPPORTED IS THIS PARTICULAR ROW. A county the tracker already holds
# a restrictive record for is corroborated by a second, independent reading. A
# county where this upstream row is the only thing asserting a restriction, and
# where promoting it flips the label from 0 to 1, is a much larger claim resting
# on a much smaller base. Both pass gate_row identically. confidence_tier()
# separates them and records the tier on every decision, so "this promotion was
# thin" is visible afterwards rather than reconstructable.
#
# IS THIS BATCH NORMAL. Per-row validation passing says nothing about the shape
# of the run. If upstream ships four hundred rows in a week, or its row count
# collapses because a fetch truncated, or every row for one state changes at
# once, each row can pass while the batch is obviously wrong. batch_qc() checks
# the run against its own history and refuses the whole append rather than
# writing a bad batch one good-looking row at a time.
#
# The subtle check is HOLD-RATE COLLAPSE. The gate leans on upstream's own
# uncertainty markers, so if upstream stops populating has_verify_tags the gate
# quietly stops holding anything and promotes everything. On the report that
# reads as a sudden quality improvement. It is the opposite: the brake came off.
# Nothing else in this module would notice, which is exactly why it is checked.

TIER_CORROBORATED = "corroborated"
TIER_SINGLE_SOURCE = "single_source"
TIER_THIN = "thin"

# Batch thresholds. Deliberately loose: this is a circuit breaker for a run
# that has gone wrong, not a quality score. A gate that trips on ordinary weeks
# gets disabled, and a disabled gate protects nothing.
VOLUME_MULTIPLE = 4.0        # promotions vs trailing median
MIN_HISTORY_RUNS = 3         # runs needed before volume is judged at all
UPSTREAM_SHRINK_FLOOR = 0.6  # upstream smaller than this share of last seen
HOLD_RATE_COLLAPSE = 0.25    # trailing hold rate must not fall below this share
STATE_CONCENTRATION = 0.7    # share of promotions allowed in one state
LABEL_FLIP_ABSOLUTE = 150    # label flips in one run


def confidence_tier(norm: dict, raw: dict, restrictive_counties: set,
                    frame_labels: dict | None = None) -> str:
    """How much sits behind this one promotion.

    corroborated   the tracker already holds a restrictive record for the
                   county, so upstream is a second reading rather than the
                   only one
    single_source  upstream is the only assertion, but promoting it does not
                   move the county's label
    thin           upstream is the only assertion AND promoting it would flip
                   the label from 0 to 1 AND there is no primary-source URL
                   behind it, only the upstream citation
    """
    key = make_row_key(norm)
    if key in restrictive_counties:
        return TIER_CORROBORATED

    flips = bool(frame_labels) and frame_labels.get(key) == "0"
    has_primary = bool(re.search(r"https?://", (norm.get("source") or "")))
    if flips and not has_primary:
        return TIER_THIN
    return TIER_SINGLE_SOURCE


def load_frame_labels(path: str = AGG_CSV) -> tuple:
    """(labels by county key, set of counties already holding a restriction)."""
    labels, restrictive = {}, set()
    if not os.path.exists(path):
        return labels, restrictive
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            st = (r.get("state") or "").strip().upper()
            key = (st, _norm_join(r.get("county_name") or ""))
            if not key[0] or not key[1]:
                continue
            labels[key] = (r.get("has_enacted_restrictive") or "").strip()
            if labels[key] == "1":
                restrictive.add(key)
    return labels, restrictive


def load_promotion_history(path: str = PROMOTION_REPORT) -> list:
    """Per-run totals from the append-only report, oldest first.

    Each run is one decided_at date. Returns
    [{"decided_at", "promoted", "held", "upstream_seen"}].
    """
    if not os.path.exists(path):
        return []
    runs = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            day = (r.get("decided_at") or "").strip()
            if not day:
                continue
            slot = runs.setdefault(day, {"decided_at": day, "promoted": 0,
                                         "held": 0})
            if (r.get("decision") or "") == "promote":
                slot["promoted"] += 1
            else:
                slot["held"] += 1
    return [runs[d] for d in sorted(runs)]


def _median(values: list) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def batch_qc(promoted: list, decisions: list, history: list,
             upstream_count: int, tiers: dict) -> list:
    """Return a list of reasons this batch should NOT be appended.

    Empty list means the run looks like its own history and may proceed.
    """
    failures = []
    n_prom = len(promoted)
    considered = [d for d in decisions if d[1] != "already in census"]
    n_held = sum(1 for _, reason in considered if reason)
    n_considered = len(considered)

    # Volume. Only judged once there is enough history to have a normal.
    prior = [h["promoted"] for h in history]
    if len(prior) >= MIN_HISTORY_RUNS:
        med = _median(prior)
        if med > 0 and n_prom > med * VOLUME_MULTIPLE:
            failures.append(
                f"{n_prom} promotions is more than {VOLUME_MULTIPLE:g}x the "
                f"trailing median of {med:g}; upstream may have restructured")

    # Upstream shrinkage. A truncated fetch looks like a clean small delta.
    prior_seen = [h.get("upstream_seen") or 0 for h in history]
    prior_seen = [v for v in prior_seen if v]
    if prior_seen and upstream_count:
        last = prior_seen[-1]
        if upstream_count < last * UPSTREAM_SHRINK_FLOOR:
            failures.append(
                f"upstream returned {upstream_count} county rows against "
                f"{last} last run; a truncated fetch reads as a clean delta")

    # Hold-rate collapse: the brake coming off, not quality improving.
    if n_considered >= 20:
        rate = n_held / n_considered
        prior_rates = [h["held"] / (h["held"] + h["promoted"])
                       for h in history if (h["held"] + h["promoted"]) >= 20]
        if len(prior_rates) >= MIN_HISTORY_RUNS:
            prior_med = _median(prior_rates)
            if prior_med >= HOLD_RATE_COLLAPSE and rate < prior_med * HOLD_RATE_COLLAPSE:
                failures.append(
                    f"hold rate fell to {rate:.0%} from a trailing {prior_med:.0%}; "
                    f"upstream may have stopped setting the uncertainty markers "
                    f"the gate depends on")

    # One state dominating a run.
    if n_prom >= 20:
        by_state = Counter((r.get("state") or "") for r in promoted)
        state, top = by_state.most_common(1)[0]
        if top / n_prom > STATE_CONCENTRATION:
            failures.append(
                f"{top} of {n_prom} promotions are {state}; a single-state "
                f"batch is usually an upstream edit rather than real activity")

    # Label movement in one run.
    flips = sum(1 for k in tiers if tiers[k] in (TIER_THIN, TIER_SINGLE_SOURCE))
    thin = sum(1 for k in tiers if tiers[k] == TIER_THIN)
    if thin > LABEL_FLIP_ABSOLUTE:
        failures.append(
            f"{thin} thin promotions (single source, flips a label, no primary "
            f"source URL) exceeds {LABEL_FLIP_ABSOLUTE} in one run")

    return failures


def append_to_census(promoted: list[dict]) -> int:
    """Append promoted rows to the seeded census.

    Append-only: existing rows are never rewritten, so a hand-corrected row
    cannot be clobbered by a later automated run.
    """
    if not promoted:
        return 0
    fieldnames = ["state", "county", "instrument", "census_status",
                  "date_enacted", "source"]
    with open(EXTERNAL_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        for row in promoted:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    return len(promoted)


def write_promotion_report(decisions: list, tiers: dict | None = None,
                           upstream_seen: int = 0,
                           batch_verdict: str = "") -> None:
    """Append-only audit trail of every promote and hold.

    confidence_tier is recorded per promotion so "this one was thin" is visible
    afterwards rather than something a reader has to reconstruct. upstream_seen
    and batch_verdict are recorded on every row of the run because the batch
    gate reads its own history out of this file: without the upstream count
    there is no way to notice a truncated fetch next time.
    """
    tiers = tiers or {}
    stamp = dt.date.today().isoformat()
    fieldnames = ["decided_at", "state", "county", "census_status",
                  "date_enacted", "decision", "reason", "confidence_tier",
                  "upstream_seen", "batch_verdict", "source"]
    new_file = not os.path.exists(PROMOTION_REPORT)
    with open(PROMOTION_REPORT, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        if new_file:
            writer.writeheader()
        for norm, reason in decisions:
            if reason == "already in census":
                continue  # steady-state noise; every run would re-log it
            writer.writerow({
                "decided_at": stamp,
                "state": norm.get("state", ""),
                "county": norm.get("county", ""),
                "census_status": norm.get("census_status", ""),
                "date_enacted": norm.get("date_enacted", ""),
                "decision": "promote" if not reason else "hold",
                "reason": reason,
                "confidence_tier": ("" if reason
                                    else tiers.get(make_row_key(norm), "")),
                "upstream_seen": upstream_seen,
                "batch_verdict": batch_verdict,
                "source": norm.get("source", ""),
            })


def run_promote(force: bool = False, hold_thin: bool = False) -> int:
    local = load_local_census()
    upstream_raw = fetch_moratorium_csv()
    frame = load_county_frame()
    if not frame:
        print("WARNING: no county frame at data/county_aggregate.csv; the "
              "join check is skipped and unjoinable rows may be promoted.")
    promoted, decisions = promote(local, upstream_raw, frame)

    labels, restrictive = load_frame_labels()
    raw_by_key = {}
    for raw in upstream_raw:
        one = normalize_upstream_rows([raw])
        if one:
            raw_by_key[make_row_key(one[0])] = raw
    tiers = {}
    for row in promoted:
        key = make_row_key(row)
        tiers[key] = confidence_tier(row, raw_by_key.get(key, {}),
                                     restrictive, labels)

    # --hold-thin is the stricter posture, available without editing code: a
    # promotion that rests on one source, flips the county label, and has no
    # primary-source URL behind it is routed back to the exception queue
    # instead of appended. On the 2026-09 upstream that is 35 of 68.
    if hold_thin:
        thin_keys = {k for k, t in tiers.items() if t == TIER_THIN}
        if thin_keys:
            kept = [r for r in promoted if make_row_key(r) not in thin_keys]
            decisions = [(n, r if r else
                          ("thin: one source, flips the label, no primary URL"
                           if make_row_key(n) in thin_keys else r))
                         for n, r in decisions]
            print(f"--hold-thin: holding {len(promoted) - len(kept)} thin "
                  f"promotion(s), keeping {len(kept)}")
            promoted = kept
            tiers = {k: v for k, v in tiers.items() if k not in thin_keys}

    upstream_seen = len(normalize_upstream_rows(upstream_raw))
    history = load_promotion_history()
    failures = batch_qc(promoted, decisions, history, upstream_seen, tiers)

    held = Counter(r for _, r in decisions if r and r != "already in census")
    by_tier = Counter(tiers.values())

    if failures:
        # Nothing is appended. The census is append-only, so a bad batch is
        # expensive to undo and cheap to refuse; the delta stays as the queue
        # and the next run re-evaluates from scratch.
        verdict = "held: " + "; ".join(failures)
        write_promotion_report(decisions, tiers, upstream_seen,
                               "batch_held" if not force else "batch_forced")
        print("BATCH QC FAILED. Nothing was appended to the census.\n")
        for f in failures:
            print(f"  {f}")
        print(f"\n{len(promoted)} row(s) would have been promoted "
              f"({', '.join(f'{k} {v}' for k, v in by_tier.most_common())}).")
        print("Every decision was still recorded, so the run is reviewable:")
        print(f"  {PROMOTION_REPORT}")
        if not force:
            print("\nIf this batch is legitimate, re-run with --promote --force.")
            return 1
        print("\n--force given: appending anyway.")

    n = append_to_census(promoted)
    if not failures:
        write_promotion_report(decisions, tiers, upstream_seen, "batch_ok")

    print(f"promoted {n} row(s) into the census "
          f"({len(local)} -> {len(local) + n})")
    if promoted:
        print(f"  states represented: "
              f"{len(set(r['state'] for r in promoted))}")
        print("  confidence: " + ", ".join(f"{k} {v}"
                                           for k, v in by_tier.most_common()))
        if by_tier.get(TIER_THIN):
            print(f"  {by_tier[TIER_THIN]} promotion(s) are THIN: one source, "
                  f"flips the county label, and no primary-source URL behind "
                  f"the upstream citation. Filter confidence_tier in the report "
                  f"to review them.")
    for reason, count in held.most_common():
        print(f"  held, {reason}: {count}")
    print(f"decisions appended to {PROMOTION_REPORT}")
    return 0


def run_selftest() -> None:
    """Offline invariants.

    Rewritten 2026-09-15. This made a live network call to the upstream repo,
    so it could not join the blocking self-test gate in pipeline.yml, and it
    reported an upstream outage as a code failure. The fixtures below carry the
    exact upstream header spellings the normalizer reads, which is precisely
    what the placeholder version got wrong.
    """
    fixture = [
        # County, data-center sector: kept.
        {"state": "Indiana", "state_abbrev": "IN", "jurisdiction": "DeKalb County",
         "jurisdiction_type": "County", "sectors": '["data_center"]',
         "enacted_status": "active", "date_enacted_iso": "2026-04-13",
         "date_enacted": "On or about 2026-04-13, per local reporting",
         "moratorium_id": "in-dekalb-county-2026"},
        # City: dropped, the census is county-level.
        {"state": "Alabama", "state_abbrev": "AL", "jurisdiction": "Birmingham",
         "jurisdiction_type": "City", "sectors": '["data_center"]',
         "enacted_status": "active", "date_enacted_iso": "2026-03-03",
         "moratorium_id": "al-birmingham-2026"},
        # Township: dropped.
        {"state": "Michigan", "state_abbrev": "MI", "jurisdiction": "Scio Township",
         "jurisdiction_type": "Township", "sectors": '["data_center"]',
         "enacted_status": "active", "date_enacted_iso": "2026-02-02",
         "moratorium_id": "mi-scio-2026"},
        # County but not a data-center instrument: dropped.
        {"state": "Kansas", "state_abbrev": "KS", "jurisdiction": "Reno County",
         "jurisdiction_type": "County", "sectors": '["solar"]',
         "enacted_status": "active", "date_enacted_iso": "2026-01-01",
         "moratorium_id": "ks-reno-2026"},
        # Parish counts as county-level.
        {"state": "Louisiana", "state_abbrev": "LA", "jurisdiction": "Caddo Parish",
         "jurisdiction_type": "Parish", "sectors": '["data_center","solar"]',
         "enacted_status": "pending", "date_enacted_iso": "",
         "moratorium_id": "la-caddo-2026"},
    ]
    norm = normalize_upstream_rows(fixture)
    failures = []

    def ck(name, got, want):
        if got != want:
            failures.append(f"{name}: expected {want!r}, got {got!r}")

    ck("county and sector filters", len(norm), 2)
    ck("state is abbreviated", norm[0]["state"], "IN")
    ck("status is mapped", norm[0]["census_status"], "active")
    ck("date is ISO, not prose", norm[0]["date_enacted"], "2026-04-13")
    ck("parish is county-level", norm[1]["state"], "LA")
    ck("pending status survives", norm[1]["census_status"], "pending")
    ck("blank iso date stays blank", norm[1]["date_enacted"], "")
    ck("source cites the upstream id",
       norm[0]["source"].startswith("moratorium-nation:in-dekalb-county-2026"), True)

    # A county the local census already holds must not read as new.
    local = [{"state": "IN", "county": "DeKalb County", "instrument": "moratorium",
              "census_status": "active", "date_enacted": "2026-04-13"}]
    ck("known county is not delta", len(compute_delta(local, norm)), 1)

    # The key normalizes, so a suffix difference is not a new county.
    local_suffix = [{"state": "LA", "county": "Caddo Parish (Phase 1)"}]
    ck("normalized key matches across suffixes",
       len(compute_delta(local_suffix, norm)), 1)

    # A corrected date is a changed episode, not a newly covered county.
    local_date = [{"state": "IN", "county": "DeKalb County",
                   "date_enacted": "2026-04-14"},
                  {"state": "LA", "county": "Caddo Parish"}]
    ck("date change is not a new county",
       len(compute_delta(local_date, norm)), 0)

    # State spelling must not resurrect the original defect.
    local_fullname = [{"state": "Indiana", "county": "DeKalb County"}]
    ck("full state name does not match an abbreviated key",
       len(compute_delta(local_fullname, norm)), 2)

    # --- confidence tiering -------------------------------------------------
    row_cited = {"state": "OH", "county": "Franklin County",
                 "source": "moratorium-nation:oh-franklin (CC-BY-4.0, "
                           "github.com/mjbommar/moratorium-data-2026)"}
    row_primary = dict(row_cited,
                       source="https://franklin.oh.gov/ord-2026-14.pdf")
    k = make_row_key(row_cited)

    ck("a county the tracker already calls restrictive is corroborated",
       confidence_tier(row_cited, {}, {k}, {k: "1"}), TIER_CORROBORATED)
    ck("one source flipping a label with no primary URL is thin",
       confidence_tier(row_cited, {}, set(), {k: "0"}), TIER_THIN)
    ck("a primary source URL lifts it out of thin",
       confidence_tier(row_primary, {}, set(), {k: "0"}), TIER_SINGLE_SOURCE)
    ck("one source not moving a label is single_source",
       confidence_tier(row_cited, {}, set(), {k: "1"}), TIER_SINGLE_SOURCE)
    ck("no frame means no thin classification can be claimed",
       confidence_tier(row_cited, {}, set(), {}), TIER_SINGLE_SOURCE)

    # --- batch gate ---------------------------------------------------------
    def runs(n, promoted, held):
        return [{"decided_at": f"2026-0{i+1}-01", "promoted": promoted,
                 "held": held, "upstream_seen": 200} for i in range(n)]

    def prom(n, state="OH"):
        return [{"state": state, "county": f"C{i} County",
                 "source": "x"} for i in range(n)]

    def decs(n_prom, n_held):
        return ([({"state": "OH", "county": f"P{i} County"}, "")
                 for i in range(n_prom)]
                + [({"state": "OH", "county": f"H{i} County"},
                    "upstream verify tag") for i in range(n_held)])

    steady = runs(5, 10, 10)

    ck("a normal run passes",
       batch_qc(prom(12), decs(12, 10), steady, 200, {}), [])

    ck("a volume spike is caught",
       any("trailing median" in f
           for f in batch_qc(prom(90), decs(90, 10), steady, 200, {})), True)

    # Volume needs a normal before it can judge one. Asserted on the volume
    # message specifically: this batch is also single-state, so the run is not
    # failure-free and an emptiness check here would be testing the wrong thing.
    ck("no history means volume is not judged",
       any("trailing median" in f
           for f in batch_qc(prom(90), decs(90, 10), [], 200, {})), False)
    ck("too little history means volume is not judged",
       any("trailing median" in f
           for f in batch_qc(prom(90), decs(90, 10), runs(2, 10, 10), 200, {})),
       False)

    ck("a truncated upstream fetch is caught",
       any("truncated fetch" in f
           for f in batch_qc(prom(5), decs(5, 5), steady, 40, {})), True)

    # The subtle one: upstream stops setting its uncertainty markers, so the
    # gate holds nothing and the run looks like a quality improvement.
    ck("hold-rate collapse is caught",
       any("uncertainty markers" in f
           for f in batch_qc(prom(30), decs(30, 0), steady, 200, {})), True)

    ck("one state dominating a run is caught",
       any("single-state batch" in f
           for f in batch_qc(prom(40), decs(40, 10), steady, 200, {})), True)

    mixed = prom(20, "OH") + prom(20, "GA")
    ck("a spread run is not flagged for concentration",
       any("single-state batch" in f
           for f in batch_qc(mixed, decs(40, 30), steady, 200, {})), False)

    many_thin = {(f"S{i}", f"c{i}"): TIER_THIN
                 for i in range(LABEL_FLIP_ABSOLUTE + 5)}
    ck("too many thin promotions in one run is caught",
       any("thin promotions" in f
           for f in batch_qc(prom(10), decs(10, 10), steady, 200, many_thin)),
       True)

    # History reconstruction from the append-only report.
    ck("history is empty when no report exists",
       load_promotion_history("/nonexistent/report.csv"), [])
    ck("median of an even list", _median([1, 3, 5, 7]), 4.0)
    ck("median of an odd list", _median([1, 3, 100]), 3.0)
    ck("median of nothing is zero", _median([]), 0.0)

    # --- promotion gate -------------------------------------------------
    # The gate runs unattended, so every hold reason is pinned here.
    frame = {("IN", "dekalb"), ("LA", "caddo"), ("OH", "franklin")}

    def one(raw_over, held=frame, census=()):
        raw = {"state": "Ohio", "state_abbrev": "OH",
               "jurisdiction": "Franklin County", "jurisdiction_type": "County",
               "sectors": '["data_center"]', "enacted_status": "active",
               "date_enacted_iso": "2026-05-05",
               "moratorium_id": "oh-franklin-2026"}
        raw.update(raw_over)
        n = normalize_upstream_rows([raw])
        if not n:
            return "filtered out"
        return gate_row(raw, n[0], {make_row_key(r) for r in census}, held)

    ck("clean row promotes", one({}), "")
    ck("county already held is not re-promoted",
       one({}, census=[{"state": "OH", "county": "Franklin County"}]),
       "already in census")
    ck("row outside the county frame is held",
       one({"state_abbrev": "OH", "jurisdiction": "Nowhere County"}),
       "does not join county frame")
    ck("pending is held", one({"enacted_status": "pending"}),
       "pending, not enacted")
    ck("dateless row is held", one({"date_enacted_iso": ""}), "no ISO date")
    ck("upstream verify tag is held", one({"has_verify_tags": "True"}),
       "upstream verify tag")
    ck("unverified date is held",
       one({"date_enacted_uncertainty": "unverified"}), "date unverified")
    ck("an empty frame skips the join check rather than holding everything",
       one({}, held=set()), "")

    # Two episodes for one county must promote once: recall is a property of
    # counties, and a second row would be a duplicate the audit reads back as
    # one county either way.
    two_episodes = [
        {"state": "Ohio", "state_abbrev": "OH", "jurisdiction": "Franklin County",
         "jurisdiction_type": "County", "sectors": '["data_center"]',
         "enacted_status": "active", "date_enacted_iso": "2026-05-05",
         "moratorium_id": "oh-franklin-2026-a"},
        {"state": "Ohio", "state_abbrev": "OH", "jurisdiction": "Franklin County",
         "jurisdiction_type": "County", "sectors": '["data_center"]',
         "enacted_status": "extended", "date_enacted_iso": "2026-08-08",
         "moratorium_id": "oh-franklin-2026-b"},
    ]
    promoted, decisions = promote([], two_episodes, frame)
    ck("one county promotes once", len(promoted), 1)
    ck("the second episode is logged as held",
       [r for _, r in decisions].count("already in census"), 1)

    # A held row must never reach the census writer.
    promoted_pending, _ = promote(
        [], [dict(two_episodes[0], enacted_status="pending")], frame)
    ck("held rows are not promoted", len(promoted_pending), 0)

    if failures:
        for f in failures:
            print("  " + f)
        raise SystemExit("Selftest FAILED")
    print("Selftest passed.")


def run_refresh() -> None:
    local = load_local_census()
    upstream_raw = fetch_moratorium_csv()
    upstream_norm = normalize_upstream_rows(upstream_raw)
    delta = compute_delta(local, upstream_norm)
    write_delta_csv(delta)
    write_refresh_report(local, upstream_norm, delta)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="Run invariants only")
    parser.add_argument("--refresh", action="store_true", help="Fetch upstream census and write delta")
    parser.add_argument("--promote", action="store_true",
                        help="Append gate-passing upstream rows to the census")
    parser.add_argument("--force", action="store_true",
                        help="Append even if the batch QC gate fails")
    parser.add_argument("--hold-thin", action="store_true",
                        help="Hold promotions that rest on one source, flip a "
                             "county label, and carry no primary-source URL")
    args = parser.parse_args()

    if args.selftest:
        run_selftest()
        return
    if args.refresh:
        run_refresh()
        return
    if args.promote:
        sys.exit(run_promote(force=args.force,
                             hold_thin=args.hold_thin))

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
