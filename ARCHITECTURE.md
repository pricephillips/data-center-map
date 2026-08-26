# Architecture: the dataset layers

Standing document. `configs/layers.json` is the machine-readable half and
`layer_audit.py` enforces it on every pipeline run; this file is the reasoning.
Change one and change the other.

## Why declare layers at all

The layering was always real. Facilities, proposed projects, opposition events,
policy instruments and derived analytics are five different kinds of record
with five different keys, and every module in this repository already treated
them that way. What was missing was a statement of it and a check on it.

Implicit layering is cheap while a repository is small and expensive the first
time a module writes into a file it does not own. That has happened here once
already: the `master_opposition.csv` corruption, which the daily sync's
ownership rule was written to stop. That rule was correct and specific to one
file. The two rules below are the same rule generalized, and they cost nothing
now because the tree already complies.

## The two rules

**One writing process per file.** A file written by two processes has no owner
and the last run decides what it contains. Where two writers are genuinely
right, the reason is written into `configs/layers.json` and the audit reports
it as declared rather than passing over it. There are exactly two such files
today and both are argued for below.

**No writer crosses a layer boundary undeclared.** A module that writes into
two layers is a place where two kinds of record can be merged by accident.
Where a crossing is real and intended, it is declared with its reason and
reported as declared. There are exactly two, and in both cases the crossing is
the module's purpose rather than an accident of where a file happened to land.

`layer_audit.py` resolves what a module writes with an AST walk rather than by
searching for filenames, because a module that only reads a path mentions it
exactly the same way a module that writes it does.

## The one convention that makes the rules enforceable

Layer membership follows what a file is a record *of*, with one exception:
anything derived, regenerable and never hand-edited is Layer E regardless of
which layer it summarizes. Without that convention every audit and every model
would legitimately touch three layers, and the crossing rule would mean
nothing. With it, "this module writes into two layers" is a real signal.

## The layers

### Layer A, facilities

Existing and operating data centers.

| | |
|---|---|
| Key | `facility_id`, planned. The snapshots have no stable key today, which is the first thing the facility registry has to fix. |
| Files | `atlas.csv`, `ai_centers.csv`, `data/facility_*` |
| Writers | `facility_manifest.py` (provenance), `signal_harvest.py` (candidates only) |
| Sources of record | `atlas.csv` and `ai_centers.csv`, both hand-placed snapshots |

This is the largest dataset in the repository and the only layer with no
acquisition pipeline. `configs/facility_sources.json` declares what each
snapshot is and `data/facility_manifest.json` publishes rows, repository change
date and declared upstream vintage, so the surfaces can state their own
provenance. Both sources currently read `vintage_status: undeclared`, which
means the age of the data is genuinely unknown and the pages say so.

`data/facility_candidates.csv` is the layer's first standing intake: facility
openings, ground breakings, announcements and expansions the signal harvester
already saw. Nothing in it is a source of record.

### Layer B, proposed projects

Announced, proposed and under-construction projects, and their lifecycles.

| | |
|---|---|
| Key | `project_id` |
| Files | `data/proposals.csv`, `data/project_lifecycles.csv`, `data/project_links*.csv`, `data/project_decision_dates.csv`, `data/permit_candidates_*.csv`, `project_overrides.csv` |
| Writers | `project_resolution.py`, the scrapers, `apply_link_suggestions.py`, `triage_accelerator.py` (drafts only) |
| Sources of record | `data/proposals.csv`, plus the hand-maintained overlay and verified-date files, each row of which carries a source and URL |

A permit that reaches built or operating status is the intended graduation path
from Layer B into Layer A. That is mechanical once the facility registry has
stable identifiers, and it does not exist yet.

### Layer C, opposition events

Recorded opposition events and the entities that produce them.

| | |
|---|---|
| Key | `opp_id`, joined to `project_id` through `data/project_links.csv` and nothing else |
| Files | `master_opposition.csv`, `master_opposition_clean.csv`, `change_log.csv`, `quarantine.json`, `group_registry.csv`, the review queues, `data/signal_*` |
| Writers | `scripts/build_master_csv.py`, `clean_opposition_data.py`, `build_clean_feed.py`, `signal_harvest.py`, `promote_signal_candidates.py`, `census_gap_candidates.py` |
| Source of record | `master_opposition.csv`, whose filename never changes |

The join to Layer B runs through the existing entity linkage only. Nothing
matches an event to a project on name similarity at write time; that is what
`project_resolution.py` is for, and its adjudications are auditable rows.

### Layer D, policy instruments

Enacted and pending restrictions, the county frame they attach to, and the
reference geography that frame is built on.

| | |
|---|---|
| Key | `fips` |
| Files | `data/external_restriction_census*`, `data/county_aggregate.csv`, `data/county_policy_*`, `data/restriction_*`, `data/bill_*`, `data/stale_pending_*`, the county reference geography |
| Writers | `county_aggregator.py`, `county_policy_model.py`, `county_policy_intervals.py`, `restriction_worklist.py`, `census_gap_candidates.py`, `bill_sync.py`, `stale_pending_audit.py`, `refresh_external_census.py`, the county fetchers |
| Source of record | The tracker itself. `data/external_restriction_census.csv` is an external lower bound and a pointer, never a source of record: nothing is ingestable from it until a primary source URL is supplied |

### Layer E, derived analytics

Everything computed from the layers above: baselines, models, audits,
worklists, reports.

| | |
|---|---|
| Key | Inherits whatever it was derived from |
| Files | `data/baseline_*`, `data/matched_controls.csv`, every model artifact, every audit output, every worklist, `docs/*.md`, `headline_metrics.md` |
| Writers | One module each, listed in `configs/layers.json` |
| Sources of record | None, by definition |

Declared derived, never hand-edited, always regenerable. A hand edit to a
Layer E file is a defect even when the edited value is correct, because the
next run silently reverts it and the correction is lost.

`data/baseline_universe.csv` is the file this declaration exists for. It mixes
rows describing Layer A facilities with rows describing Layer B projects, and
it had been read in places as though it were source data for one or the other.
It is neither. It is regenerable in full from four named inputs
(`data/proposals.csv`, `data/project_lifecycles.csv`, `ai_centers.csv`,
`atlas.csv`), and its `source` column is the only safe way to read it: a query
that ignores that column is mixing a built facility with a proposed project. It
answers one question, which is what an unopposed comparable looks like.

## The declared exceptions

Four, each with its reason recorded in `configs/layers.json` and reported by
the audit on every run.

**`master_opposition.csv`, three writers.** The source of record for Layer C
and the one file with more than one writer by design.
`scripts/build_master_csv.py` refreshes it from upstream under the ownership
rule that ended the corruption; `census_gap_candidates.py` and
`promote_signal_candidates.py` append gated promotions only. All three are
append-or-refresh under a stated discipline, none rewrites another's rows, and
every promotion lands in an audit trail. This is the exception that had to be
argued for, and arguing for it is why the rule exists everywhere else.

**`data/signal_candidates.csv`, two writers.** A handoff, not shared
ownership. `signal_harvest.py` writes the queue; `promote_signal_candidates.py`
consumes it and rewrites what remains, so the file holds exactly the rows the
gate did not promote. They never run concurrently.

**`signal_harvest.py`, Layers A and C.** One GDELT call returns both
facility-lifecycle articles and opposition coverage, and splitting the network
call in two would double the quota cost to separate what a single classifier
can route. The module routes and never merges: a row lands in exactly one
layer's file, the opposition side wins any tie, and neither file is a source of
record.

**`census_gap_candidates.py`, Layers C and D.** The gap-closure promotion path,
where a Layer D census gap becomes a Layer C opposition record. The crossing is
the module's purpose. It is gated on being complete, dated, sourced,
dedup-guarded and census-corroborated, it appends only, and every promote, hold
and block decision is written to `data/gap_promotion_report.csv`.

## Working with the layers

- A new generated file gets a layer pattern in `configs/layers.json` in the
  same commit that creates it. Otherwise the audit reports it as undeclared,
  which is the intended nag.
- A new writer for an existing file is a design decision, not a convenience.
  Either the existing writer gains the responsibility, or the exception is
  declared with the reason someone will need in a year.
- A module that finds itself needing to write into a second layer should first
  check whether what it is writing is actually derived, in which case it is
  Layer E and there is no crossing at all. That was the case for
  `data/audit_discovered_opposition.csv`, which reads like an opposition record
  and is in fact a regenerable list of audit findings queued for collection.
