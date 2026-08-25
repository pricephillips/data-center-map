"""
build_master_csv.py

Nightly ingest for master_opposition.csv.

Sources
-------
  datacentertracker.org fights feed   ->  rows written to master_opposition.csv
  GDELT 2.0 Doc API (signal_harvest)  ->  candidates written to a review queue

Change 2026-07-24: the Google News RSS step previously appended rows straight
into master_opposition.csv with Entity "Unknown", no state, no county, no
date, and no outcome. Those rows entered the source of truth unverified and
were indistinguishable from curated rows except by their blank fields (369 of
them are in the file today). That step is replaced by signal_harvest, which
covers more outlets and writes to data/signal_candidates.csv for review
instead of writing to the database.

Existing rows are never deleted or altered by this change. What changes is
that NEW unverified rows stop being appended.

Change 2026-08-25: the merge was destroying curated rows. Two defects, both
observed in production between 2026-08-21 and 2026-08-25. First, the file
was loaded into a dict keyed on (Incident, Entity), so any two rows sharing
that key silently collapsed to one on every run; 51 legitimate rows were one
run from deletion when this was caught. Second, upstream rows overwrote ANY
existing row sharing the key regardless of provenance: the Nebraska "Cass
County" fight overwrote Indiana's Cass County ban (hand-verified), upstream's
2022 Clay County NC row overwrote the county's 2026 ban, and hand-applied
corrections to Mercer County ND were reverted to upstream coding. The merge
now follows the ownership rule used everywhere else in the pipeline: this
sync owns only rows whose data_source is its own (or blank, for rows written
before provenance was recorded). Rows from every other source pass through
byte-identical, keyed rows include State so same-named counties in different
states never collide, and an upstream record whose key matches a protected
row is skipped entirely, because a hand-verified correction supersedes the
upstream coding of the same event.

The data_source column is also fixed here. build_row previously emitted the
key "datasource" while the CSV header carries "data_source", so the writer's
extrasaction="ignore" silently dropped every provenance value. Both keys are
now written, so provenance persists under either header spelling.
"""

import csv
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import signal_harvest
except ImportError:                      # module absent; CSV build must still run
    signal_harvest = None

SOURCE_URL = "https://datacentertracker.org/data/fights.json"
OUTPUT_CSV = "master_opposition.csv"

# Ownership rule (2026-08-25). This sync may refresh or replace only rows it
# wrote itself: data_source "datacentertracker.org", or blank for rows that
# predate provenance recording. Rows from any other source are never
# altered, and an incoming upstream record whose key matches one of them is
# skipped rather than duplicated.
UPSTREAM_SOURCE = "datacentertracker.org"
REFRESHABLE_SOURCES = {UPSTREAM_SOURCE, ""}

# Status normalization on ingest (2026-08-25). The QC gate's canonical
# status vocabulary (qc/qc_pipeline.py _CANONICAL_STATUS) is the contract;
# upstream sends free-text statuses that accumulated 44 STATUS_VOCAB
# findings and turned the gate red the first time it re-armed. Every
# incoming status is mapped here; the original wording is preserved in the
# Summary so no information is lost. Mappings follow the legislative
# outcome discipline: a bill past one chamber or awaiting a signature is
# pending, not passed; sine die and died-in-committee are terminal.
STATUS_NORMALIZATION = {
    "mixed": "resolved",
    "decided": "resolved",
    "superseded": "resolved",
    "incorporated": "resolved",
    "published": "resolved",
    "died-sine-die": "died",
    "died-in-committee": "died",
    "died in committee": "died",
    "relocated": "withdrawn",
    "drafted": "proposed",
    "paused": "delayed",
    "held": "delayed",
    "tabled": "postponed",
    "litigation": "ongoing",
    "moratorium": "active",
    "moratorium enacted": "enacted",
    "enforced": "active",
    "first_reading": "pending",      # a first reading is not an enactment
    "second_reading": "pending",
    "recommended": "pending",        # a recommendation is non-terminal
    "passed (first hearing)": "pending",
    # A nonbinding resolution concluded but imposes nothing; "adopted" is
    # canonical for the QC gate and is deliberately NOT in the county
    # aggregator's enacted-status set, so a nonbinding moratorium
    # resolution can never flip a county's enacted label.
    "passed (nonbinding)": "adopted",
    "5-0 to draft ban": "proposed",
    "protest": "ongoing",
    "organizing": "ongoing",
    "exploratory": "considering",
    "interim-committee-discussion": "considering",
    "wells approved": "approved",
}
_STATUS_PREFIX_RULES = (
    # (prefix or substring, canonical). Checked when the exact map misses.
    ("passed legislature", "pending"),   # awaiting signature = not enacted
    ("passed house", "pending"),
    ("passed senate", "pending"),
    ("passed committee", "pending"),
    ("active", "active"),                # "active - permit granted, ..." variants
    ("postponed", "postponed"),          # "postponed to june 23" variants
    ("pending", "pending"),              # "pending - discussion draft ..." variants
)


def normalize_status(raw):
    """(canonical_status, was_remapped). Canonical values pass through."""
    s = str(raw or "").strip()
    low = s.lower()
    if not low:
        return s, False
    canonical = {"passed", "signed", "approved", "enacted", "defeated",
                 "dead", "died", "cancelled", "canceled", "expired",
                 "withdrawn", "vetoed", "failed", "denied", "rejected",
                 "adopted", "moratorium adopted", "moratorium passed",
                 "resolved", "active", "pending", "ongoing", "proposed",
                 "filed", "hearing", "delayed", "introduced", "monitoring",
                 "considering", "review", "announced", "postponed",
                 "plan unveiled", "changed", "extended"}
    if low in canonical:
        return low, False
    if low in STATUS_NORMALIZATION:
        return STATUS_NORMALIZATION[low], True
    for prefix, target in _STATUS_PREFIX_RULES:
        if low.startswith(prefix) or prefix in low:
            return target, True
    # Unknown status: keep it visible rather than guessing a semantic; the
    # QC gate will flag it and the mapping table gets the new entry.
    return s, False
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lookback for the candidate harvest. The job runs daily, so a 7-day window
# gives six days of overlap and makes a single missed run harmless.
HARVEST_DAYS = 7

def load_proposals(path="data/proposals.csv"):
    proposals = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row.get("state", "").strip().upper(), row.get("companies", "").strip().lower())
                proposals[key] = row
    except FileNotFoundError:
        pass
    return proposals

def score_severity(record):
    score = 1
    if record.get("petition_signatures") and int(record.get("petition_signatures") or 0) > 1000:
        score += 1
    if record.get("authority_level") in ("state", "federal"):
        score += 1
    if record.get("status") in ("ongoing", "escalated"):
        score += 1
    return min(score, 5)

def join_list(value):
    if not value:
        return ""
    if isinstance(value, list):
        return "; ".join(str(x) for x in value if x is not None)
    return str(value)

def clean(value):
    if value is None:
        return ""
    return value

def load_existing_rows(path):
    """Every row, in file order, with no key-based collapse. The previous
    dict load deleted rows sharing (Incident, Entity) on every run."""
    rows = []
    header = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            rows = list(reader)
    except FileNotFoundError:
        pass
    return rows, header


def sync_key(row):
    """Identity for upstream refresh. State is part of the key because
    county-named incidents repeat across states; the Nebraska and Indiana
    Cass County rows must never share a key."""
    return (row.get("Incident", "").strip(),
            row.get("Entity", "").strip(),
            row.get("State", "").strip().upper())


def row_source(row):
    return (row.get("data_source") or row.get("datasource") or "").strip()


def merge_rows(existing, incoming):
    """Apply the ownership rule. Returns (rows, stats).

    refreshed: upstream-owned row replaced in place by its upstream refresh
    appended:  upstream record with no matching owned row
    shielded:  upstream record skipped because a protected row owns the key
    """
    rows = list(existing)
    refreshable = {}
    protected_keys = set()
    for i, r in enumerate(rows):
        if row_source(r) in REFRESHABLE_SOURCES:
            refreshable.setdefault(sync_key(r), i)
        else:
            protected_keys.add(sync_key(r))
    stats = {"refreshed": 0, "appended": 0, "shielded": 0}
    for row in incoming:
        k = sync_key(row)
        if k in protected_keys:
            stats["shielded"] += 1
            continue
        if k in refreshable:
            rows[refreshable[k]] = row
            stats["refreshed"] += 1
        else:
            rows.append(row)
            refreshable[k] = len(rows) - 1
            stats["appended"] += 1
    return rows, stats

def build_row(record, proposals=None):
    sources = record.get("sources") or []
    jurisdiction = clean(record.get("jurisdiction"))
    state = clean(record.get("state"))
    company = clean(record.get("company"))

    proposal = (proposals or {}).get((state.upper(), company.lower()), {})

    status, remapped = normalize_status(clean(record.get("status")))
    summary = clean(record.get("summary"))
    if remapped:
        summary = (summary + " Status as reported by the source: '"
                   + clean(record.get("status"))
                   + "'; normalized on ingest.").strip()

    return {
        "Incident": jurisdiction,
        "City": jurisdiction,
        "Date": clean(record.get("date")),
        "Entity": company or "Unknown",
        "Location": f"{jurisdiction}, {state}" if jurisdiction and state else jurisdiction or state,
        "Opposition Type": join_list(record.get("action_type")),
        "Severity": score_severity(record),
        "Source URL": sources[0] if sources else "",
        "State": state,
        "County": clean(record.get("county")),
        "Scope": clean(record.get("scope")),
        "Issue Category": join_list(record.get("issue_category")),
        "Objective": clean(record.get("objective")),
        "Authority Level": clean(record.get("authority_level")),
        "Status": status,
        "Community Outcome": clean(record.get("community_outcome")),
        "Hyperscaler": clean(record.get("hyperscaler")),
        "Company": company,
        "Project Name": clean(record.get("project_name")),
        "Investment Million USD": clean(record.get("investment_million_usd")),
        "Megawatts": clean(record.get("megawatts")) or proposal.get("capacity_mw", ""),
        "Acreage": clean(record.get("acreage")) or proposal.get("size_acres", ""),
        "Sponsors": join_list(record.get("sponsors")),
        "Opposition Groups": join_list(record.get("opposition_groups")),
        "Summary": summary,
        "Sources": join_list(sources),
        "Opposition Website": clean(record.get("opposition_website")),
        "Opposition Facebook": clean(record.get("opposition_facebook")),
        "Opposition Instagram": clean(record.get("opposition_instagram")),
        "Petition URL": clean(record.get("petition_url")),
        "Petition Signatures": clean(record.get("petition_signatures")),
        "datasource": "datacentertracker.org",
        "data_source": "datacentertracker.org",
        "lat": clean(record.get("lat")),
        "lon": clean(record.get("lng")),
    }

def main():
    existing_rows, header = load_existing_rows(OUTPUT_CSV)
    proposals = load_proposals()

    if not header:
         header = [
            "Incident", "City", "Date", "Entity", "Location", "Opposition Type", 
            "Severity", "Source URL", "State", "County", "Scope", "Issue Category", 
            "Objective", "Authority Level", "Status", "Community Outcome", 
            "Hyperscaler", "Company", "Project Name", "Investment Million USD", 
            "Megawatts", "Acreage", "Sponsors", "Opposition Groups", "Summary", 
            "Sources", "Opposition Website", "Opposition Facebook", 
            "Opposition Instagram", "Petition URL", "Petition Signatures", 
            "datasource", "lat", "lon"
        ]

    try:
        response_obj = requests.get(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response_obj.raise_for_status()
        payload = response_obj.json()
        records = payload["data"] if isinstance(payload, dict) else payload
    except requests.RequestException:
        records = []

    incoming = [build_row(record, proposals) for record in records]
    merged, stats = merge_rows(existing_rows, incoming)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    # Candidate harvest runs AFTER the CSV is written, so it dedupes against
    # the rows just ingested. It writes only to the review queue.
    n = 0
    if signal_harvest is not None:
        n = signal_harvest.harvest_to_queue(days=HARVEST_DAYS, repo_root=REPO_ROOT)
    print(f"build_master_csv: {len(merged)} rows written "
          f"({stats['refreshed']} refreshed, {stats['appended']} appended, "
          f"{stats['shielded']} shielded by protected rows); "
          f"{n} harvest candidates queued for review")


def selftest():
    fails = []

    def expect(cond, label):
        if not cond:
            fails.append(label)

    protected = {"Incident": "Cass County", "Entity": "Unknown",
                 "State": "IN", "data_source": "hawthorn_manual_verification",
                 "Summary": "curated"}
    owned = {"Incident": "Clay County", "Entity": "Unknown", "State": "NC",
             "data_source": "datacentertracker.org", "Status": "pending"}
    legacy_blank = {"Incident": "Old Fight", "Entity": "Unknown",
                    "State": "TX", "data_source": ""}
    twin_a = {"Incident": "Same Title", "Entity": "", "State": "",
              "data_source": "signal_harvest_auto", "Date": "2026-08-17"}
    twin_b = {"Incident": "Same Title", "Entity": "", "State": "",
              "data_source": "signal_harvest_auto", "Date": "2026-08-19"}
    existing = [protected, owned, legacy_blank, twin_a, twin_b]

    up_cass_ne = {"Incident": "Cass County", "Entity": "Unknown",
                  "State": "NE", "data_source": "datacentertracker.org"}
    up_cass_in = {"Incident": "Cass County", "Entity": "Unknown",
                  "State": "IN", "data_source": "datacentertracker.org"}
    up_clay = {"Incident": "Clay County", "Entity": "Unknown", "State": "NC",
               "data_source": "datacentertracker.org", "Status": "passed"}
    up_legacy = {"Incident": "Old Fight", "Entity": "Unknown", "State": "TX",
                 "data_source": "datacentertracker.org"}

    rows, stats = merge_rows(existing,
                             [up_cass_ne, up_cass_in, up_clay, up_legacy])
    expect(protected in rows and rows.count(protected) == 1,
           "protected row survives untouched")
    expect(stats["shielded"] == 1,
           "same-key upstream record is shielded, not merged")
    expect(up_cass_ne in rows,
           "different-state same-name upstream row appends cleanly")
    expect(up_clay in rows and owned not in rows,
           "upstream-owned row refreshes in place")
    expect(up_legacy in rows and legacy_blank not in rows,
           "blank-provenance legacy row is refresh-eligible")
    expect(rows.count(twin_a) == 1 and rows.count(twin_b) == 1,
           "same-key existing rows both survive the load")
    expect(len(rows) == 6, "row arithmetic: 5 existing - 2 refreshed in "
                           "place + 2 appended")

    expect(normalize_status("died-sine-die") == ("died", True),
           "sine die is terminal")
    expect(normalize_status("passed legislature, awaiting governor signature")
           == ("pending", True),
           "awaiting signature is pending, never passed (legislative "
           "outcome discipline)")
    expect(normalize_status("active - permit granted, litigation filed")
           == ("active", True), "compound active strings collapse to active")
    expect(normalize_status("extended") == ("extended", False),
           "extended is canonical and passes through")
    expect(normalize_status("passed") == ("passed", False),
           "canonical statuses are untouched")
    expect(normalize_status("some new upstream phrase")[1] is False,
           "unknown statuses stay visible for the QC gate to flag")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("build_master_csv selftest: 13 checks OK")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    main()
