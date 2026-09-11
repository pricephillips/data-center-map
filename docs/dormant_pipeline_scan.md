# Dormant Pipeline Scan

What in this repository is gated off, wired to nothing, or frozen, and what
should happen to each. Scanned 2026-09-10 against `claude/busy-rubin-0g89ec`.

Method: every `.py` in the tree was checked for a workflow invocation and for
an importer, every `configs/*.json` for a consumer, and every module found
dormant was then executed against current data to see whether it still works.
That last step is the one that matters, and it is how the regression in the
first section was found.

Two things this scan deliberately does not do. It does not treat "registered
but not yet run" as dormant: seven permit sources were registered on
2026-09-09 and their workflow next fires 2026-09-15, so they are pending, not
stalled. And it does not treat a closed analytic gate as dormant: the landmark,
emergence and calibration gates are closed on data quality, which is them
working, not them failing.

---

## 0. Found while scanning: a live regression, not a dormant part

**Six columns of `data/proposals.csv` went empty in the 2026-09-10 scrape and
nothing noticed.** This is not a gated-off part of the pipeline. It is the
running pipeline silently losing a third of its fields, and it outranks
everything else in this document.

Field population, `data/proposals.csv`, 338 rows both sides:

| column | 2026-09-03 | 2026-09-10 |
|---|---|---|
| `date` | 316 | 10 |
| `lastUpdated` | 331 | 12 |
| `bringingOwnEnergy` | 326 | 0 |
| `moratoriumExempt` | 326 | 0 |
| `size_acres` | 224 | 7 |
| `capacity_mw` | 123 | 3 |

Every other column is unchanged (`id`, `name`, `state`, `lat`, `lon`,
`companies`, `phase` all hold at their prior counts), so this is not a failed
fetch or an empty response. It is upstream schema drift: TrackDataCenters
renamed or dropped six fields, and
`scripts/scrape-trackdatacenters-proposals.py` maps them with
`record.get('date', '')` and friends, which turns a missing field into an empty
string with no guard and no error.

The residue confirms it. The only rows that still carry `date` are ids 1001
through 1012 — the manual additions from `data/proposals_added.csv`, which are
appended verbatim and never touch the API mapping. Every scraped row came
through with the field empty.

Downstream, in the committed `data/project_lifecycles.csv`:

| column | 2026-09-05 | 2026-09-10 |
|---|---|---|
| `announced_date` | 302 | 10 |
| `announced_precision` | 302 | 10 |
| `days_announced_to_decision` | 24 | 3 |
| `days_announced_to_first_opposition` | 10 | 5 |

`announced_date` is the landmark anchor. It was re-registered to
`announced_date` on 2026-07-28, and PHASE_STATUS records the frame as "173
opposed projects anchored". Ten projects now carry the field. Under the
registered specification the landmark gate cannot open at any window, for any
amount of decision-date recovery, until this is repaired — and the reason will
not be visible in the gate report, which will simply keep saying GATE CLOSED
for coverage.

`capacity_mw` falling to 3 takes the cost layer with it: `cost_translation.py`
never imputes MW by rule, so no capacity means no dollar figure.

**Confirmed persistent on 2026-09-11.** The next night's scrape (commit
`b04b579`, run started 10:59 UTC) left all six columns exactly where they were
— `date` 10, `lastUpdated` 12, `bringingOwnEnergy` 0, `moratoriumExempt` 0,
`size_acres` 7, `capacity_mw` 3 — while the same run added a new project, so
`name`, `lat` and `phase` all moved 338 → 339. The API is returning fresh,
working data for every other field and nothing for these six. That rules out a
one-off bad response and settles it as standing schema drift.
`data/project_lifecycles.csv` rebuilt on the same run still reads
`announced_date` 10.

Worth noting how this looks from the outside: the run that caused it
(2026-09-10, run #144) is recorded as `conclusion: success`, and so is the one
that confirmed it. The workflow has now gone green twice while a third of the
record arrives empty. That is the argument for the guard below, stated more
plainly than any inventory could.

The repair is two pieces, and the second is the one worth the effort:

1. Find the current upstream field names and remap them. One look at a live API
   response.
2. Add a population guard to the scraper. The rule this repository already
   applies elsewhere — `assert_unique_project_ids` stops the pipeline rather
   than publishing a merged project — applied to field population: a field that
   was populated on the previous run and is empty on this one fails the scrape
   instead of committing the emptied column. Without it the next rename lands
   the same way.

This is listed again as item B1 below, because the guard is the durable part.

---

## 1. Remove

Five were proposed; three survived checking. Each was re-verified against the
whole tree before acting, and two did not survive that: the migration script
(below) is idempotent and stays, and `qc/fetch_notion.py` and
`qc/enrichment.py` are held for the reasons recorded on each.

**Done:** `census_join.py`, and the two stray root worklists with the two
now-orphaned patterns they had in `configs/layers.json`.
**Held:** `qc/fetch_notion.py`, `qc/enrichment.py`.
**Withdrawn:** `scripts/fix_project_id_collision.py`.

The bar throughout is that keeping a thing has to cost something — a second
writer, a second copy to keep in sync, a hazard — not merely that nothing calls
it. Two entries failed that bar on re-reading, and the corrections are recorded
in place rather than quietly deleted.

### `census_join.py` — redundant

27 lines, pandas, no importers, no workflow. It attaches
`data/county_census_features.csv` to a dataframe by FIPS. `county_aggregator.py`
already reads that file directly, as do `fetch_pudl.py` and
`spot_check_census.py`. The "any dataframe carrying a FIPS column" use it was
written for never materialised, and it is the only pandas-dependent helper in
the tree with no caller.

**Done.** A sweep of every file type in the tree found zero references of any
kind. Its Connecticut planning-region caveat moved to
`fetch_census_features.py`, which is the module that actually produces the file
the caveat is about.

### `qc/fetch_notion.py` — superseded, but held

Pulls the Notion database into `records.json` for the gate. `qc/README.md` calls
it "the one file not exercised in the build", and that has been true since
`scripts/build_master_csv.py` made datacentertracker.org the ingest and
`master_opposition.csv` the source of record. ARCHITECTURE is explicit that
Layer C has one source of record with three declared writers. The outbound Iowa
Notion sync in `pipeline.yml` is unrelated and stays.

**Held, and one claim here corrected.** An earlier draft called this a path
that "would make Notion a fourth writer" to Layer C's source of record. It
would not: it writes `records.json`, an *input* the gate can read via
`--records`, and never touches `master_opposition.csv`. The case for removing
it is therefore weaker than stated — it is an unexercised operator tool, not a
hazard to the source of record.

Held because removing it is not the zero-risk change the rest of this section
is. `qc/README.md` documents it as an available option in two places, so this
is a deliberate withdrawal of a documented capability rather than a deletion of
dead code, and that is the maintainer's call to make.

### `qc/enrichment.py` — a shadow copy that nothing keeps in sync

Byte-identical to root `enrichment.py` today (`md5 8e5458dbc…` on both).
`build_clean_feed.py` carries a comment stating the intent — "Root stays first
on `sys.path`, so `import enrichment` inside the gate resolves to the single
root classifier" — and that holds for its own in-process import. It does not
hold for `qc_pipeline.py`, which is run as a script and therefore gets its own
directory prepended to `sys.path` by the interpreter. It imports
`legislative_outcome`, `schema_adapter` and `enrichment` as siblings, so it
binds `qc/enrichment.py` **on every invocation, from any working directory** —
not, as an earlier draft of this entry said, because `qc.yml` sets
`working-directory: qc`. The mechanism is stronger than that framing suggested.
They agree today and nothing enforces it. If they drift, the QC gate and the clean-feed build classify the same
record differently, and the two committed artifacts disagree about what the
database contains.

The fix would be to delete the copy and move the `sys.path.insert` at
`qc/qc_pipeline.py:116` above the `import enrichment` at line 61.

**Held.** The two files are byte-identical today, so the drift this guards
against is entirely prospective, while the change itself reorders imports
inside the gate that guards the source of record — and `qc_pipeline.py` resolves
three sibling modules that way, not one. Verified as a baseline that the gate's
selftests pass from both the repository root and from `qc/`; that is evidence
the change is tractable, not evidence it is free. Worth doing deliberately,
with the QC gate exercised on a real feed rather than on selftests alone.

### `scripts/fix_project_id_collision.py` — KEEP. This entry was wrong.

An earlier draft of this document listed the migration script for removal on
the grounds that ARCHITECTURE says the migration "must never be run twice" and
re-running it would renumber live projects a second time. **That is not what
the script does, and the recommendation is withdrawn.**

Checked before acting on it. Its own docstring states that "every step is
idempotent and independently skippable: re-running reports 'already applied'
and changes nothing", and the code backs that up: the renumber step skips when
the manual-addition block is already at the floor, the curated-reference step
skips when the migration has already been applied, the guard-installation step
skips when the guard is present, and a target id already in use is a hard exit
rather than an overwrite. `--check` is a read-only validator and it passes
today:

```
data/proposals.csv: 339 rows, 339 distinct ids
  OK     no duplicate or missing ids
--check: nothing written.
```

ARCHITECTURE's warning is about the migration as a concept — applying the
renumbering twice would shift ids again — and the script is precisely the
thing that defends against it. Keeping a passing idempotent `--check` for the
defect that caused six projects to merge is worth more than the tidiness of
removing a file nothing calls. The error in the original entry was inferring
the hazard from the warning in ARCHITECTURE without reading the script's
guards.

### Also delete: two stray worklists at the repository root

`decision_date_worklist.csv` and `announce_date_worklist.csv` sit at the root,
last written 2026-08-31, and are dead. `landmark_model.py` writes the live ones
to `data/decision_date_worklist.csv` (48 rows) and
`data/announce_date_worklist.csv`. The root copies hold 46 and 15 rows and
nothing reads them. Two files with one name and different contents is exactly
the confusion the layer rules exist to prevent.

---

## 2. Easy to wire — priority list

Ranked by value per line of change. Every module here was executed against
current data during this scan; the result is recorded.

### B1. Population guard on the scraper — **do this first**

Not strictly "wiring", and not free, but it belongs at the top of any list
because item 0 is live and will recur. See section 0. The remap is a lookup;
the guard is maybe thirty lines and one selftest, in a repository that already
has the pattern in `assert_unique_project_ids` and `state_bounds.py`.

**The guard is implemented on this branch**, as `assert_field_population` in
`scripts/scrape-trackdatacenters-proposals.py`, with a ten-check selftest wired
into the pipeline gate. Replayed against the real files it names exactly the
six fields on the 2026-09-10 transition and stays quiet on a normal night.
Measured on scraped rows alone the collapse is total -- all six go to 0, not to
the 10, 12, 7 and 3 above, which were entirely the manual additions.

It does not repair the current breakage and cannot: the baseline it compares
against is now itself empty in those columns, so the fields sit below the
sparse-field floor and nothing fires. **The remap is still outstanding and
still needs one look at a live API response.** The guard earns its place on the
next rename, not this one.

### B2. `incentive_durability_proxy.py` → `local-signals.yml`

Two lines. **Verified working this scan**: `--selftest` passes 7/7, and
`--all --out` renders a scored section for every state.

Its twin `incentive_execution_risk_proxy.py` is already wired into that
workflow with an identical CLI (`--state/--all/--data/--out/--selftest`) and an
identical output shape. The durability proxy exists to give Incentive
Durability Risk a documented starting number "until
`data/incentive_agreement_registry.csv` has enough rows to fit a real model."
That registry has **0 rows**. The condition the module was written for is fully
in force and the module has simply never been connected.

```yaml
- run: python incentive_durability_proxy.py --selftest
- run: python incentive_durability_proxy.py --all --out data/incentive_durability_proxy.md
```

**Done on this branch.** Both lines added to `local-signals.yml`, output added
to its commit list. `data/incentive_*.md` in `configs/layers.json` already
covers the new file, so no declaration was needed.

### B3. `landmark_diagnostics.py` → `gate-check.yml`

One line, and it closes a documented-but-false claim.

`gate-check.yml:99-107` already stages `data/landmark_recovery_priority.csv` and
`data/landmark_anchor_comparison.csv` for commit. Nothing in any workflow
produces them — `landmark_diagnostics.py` does, and it is invoked nowhere. Those
two `git add` lines have been permanent no-ops and both files are frozen at
2026-08-31.

Meanwhile PHASE_STATUS says "gate-check.yml keeps all three current weekly,
which is the staleness it was added to prevent", and
`docs/visibility_matrix.md` lists `landmark_recovery_priority.csv` as the
ranking of the outstanding decision dates. It ranks the 54 from an older frame;
the worklist is now 46.

`gate-check.yml` already installs numpy, which is the module's only unmet
dependency. Add the run step after the landmark step, inside the same
`steps.landmark.outcome == 'success'` condition the commit block uses.

One consequence to expect rather than be alarmed by. The first run after
wiring replaces a worklist built on pre-regression data with one built on
current data, and while section 0 stands that means
`data/landmark_recovery_priority.csv` drops from 46 ranked projects to 1, with
the diagnostics reporting no feasible window at the ceiling under either
anchor. That is the anchor collapse showing through, not the diagnostics
failing. It reverts on its own when the six columns are restored, and a
worklist that reports the truth beats one frozen at a friendlier number.

**Done on this branch.** The two artifacts were also moved out of the landmark
step's staging block and into the diagnostics step that actually writes them,
which is what made the original `git add` a permanent no-op.

### B4. `feature_asymmetry_check.py` → `gate-check.yml` or `retrain.yml`

One line. **Verified working this scan**: exits 0 and writes its report.

It is the robustness check on the `days_to_first_opposition` feature in the
outcome model — whether the arrival-speed effect is an artifact of asymmetric
date quality across outcome arms. Its docstring says "not wired into CI (run
manually after date recovery passes)", which was a reasonable call when date
recovery was a discrete event and is not one now that `gate-check.yml` runs
weekly.

It is also the module that surfaced item 0. Re-running it against current data
moved the both-dates-present share from 88% to 4% in both arms. Had it been
wired, the regression would have been visible the morning after the scrape
instead of being found by a manual audit ten days later. That is the argument
for wiring it, independent of the feature question it was built to answer.

**Done on this branch**, on `gate-check.yml` alongside the diagnostics, with
`data/feature_asymmetry_report.md` staged on a green step.

### B5. `proximity_analysis.py` — repoint or retire

**Verified working this scan**: exits 0, produces a contagion analysis,
nearest-neighbour clustering and a state hotspot density table.

It writes to `out/`, a directory that exists in no other module and is not in
the repository. Every other module writes to `data/`. So its output has never
landed anywhere a surface or an audit could read it, which is the whole reason
it reads as dormant.

**Done on this branch**, kept rather than deleted. The default output directory
moved from `out/` to `data/`, a twelve-check selftest was added covering the
geometry and the contagion window logic, and it is wired into `pipeline.yml`
after the clean feed with its outputs declared as Layer E.

Nothing about the module needed fixing — `outdir` was always a parameter, and
the `out/` default was the whole defect. It ran, exited clean, and put its
results where nothing collected them.

The result argues for keeping it. Contagion comes out at 626 of 1439 dated
incidents within 50 miles of an enacted block from the prior year, against
43.1 pct under a 50-permutation date-shuffled null: **z = +0.5**, which is no
evidence of spatial contagion beyond baseline geography. A negative result on a
claim the platform might otherwise be tempted to make is worth keeping current,
and it is cheaper to keep true than to re-derive. The `group_distance()`
scaffold remains genuinely blocked on group geocodes and says so.

### B6. Reconcile the four stale claims in the standing documents

Free, and the standing documents are load-bearing here. **Three done on this
branch; the fourth fixed itself.** Found during this scan:

- ARCHITECTURE says the Layer B → Layer A graduation path "does not exist yet."
  It does: `facility_registry.graduation_candidates()` is implemented, has three
  selftests, and `data/facility_promotion_report.csv` carries **10 rows** with
  `stream=layer_b_graduation`. `configs/facility_sources.json` already records
  that source as `live`, so the config and the prose disagree. **Corrected.**
- ARCHITECTURE repeats the entire `prj_321`–`prj_332` migration table twice,
  along with the "`project_id` is `prj_` + the `id` column" paragraph and the
  2026-07 collision narrative. Two copies of a translation table is one copy
  that can go stale unnoticed. **Corrected**, second copy removed.
- PHASE_STATUS lists Phase 4 as "Scaffolded in CI, NOT implemented".
  `cost_translation.py` is implemented, runs in `retrain.yml`, and has produced
  `data/cost_anchors.csv`, `data/cost_translation_methodology.md` and
  `data/cost_translation_demo.csv`. What is deferred is *client-facing
  publication*, which is a different claim and the accurate one. **Corrected**,
  and the row now also names what blocks publication today: the scraper
  regression took `capacity_mw` to 3 rows, and the module never imputes MW.
- PHASE_STATUS's weekly-freshness claim for the landmark worklists, per B3.
  **No longer false**: wiring `landmark_diagnostics.py` made the sentence true
  rather than needing the sentence rewritten, which was the better repair.


### B7. The layer audit can detect a file it cannot attribute

Found while declaring the proximity outputs, and worth recording because it is
the same shape as everything else here: a check that quietly covers less than
it appears to.

`layer_audit.py` resolves what a module writes with an AST walk, by design and
for a good reason recorded in ARCHITECTURE. The walk resolves string constants,
names and concatenation. It cannot resolve `os.path.join(outdir, "name.csv")`
where `outdir` is a function parameter, which is how `proximity_analysis.py`
writes both of its outputs.

The file-level half of the audit still works — removing the new patterns from
`configs/layers.json` does produce the undeclared nag, verified by experiment.
What fails is attribution, and it fails silently into a misleading message:

```
UNDECLARED  data/proximity_report.md [no writing module found; hand maintained or retired]
```

"No writing module found" is true of a hand-maintained file and equally true of
a module whose paths are computed, and the audit cannot tell them apart. Two of
the six standing undeclared findings really are hand-maintained; this one would
have read identically while having a writer sitting in the tree.

Not fixed here. Making one module's paths static to satisfy the auditor is the
tail wagging the dog, and changing the resolver is a real piece of work on a
module that gates the pipeline. Recorded so the next person reading that
message knows it has two meanings.
---

## 3. Real work, still worth doing — priority list

Ranked by how much each unblocks relative to its cost.

### C1. Turn the Splink disagreement surface into a standing worklist

`splink_spike.py` did its job and returned **NO-GO** on all four
pre-registered criteria (G1 0.918/0.95, G2 0.632/0.80, G3 best precision 0.614,
G4 0.605/0.75). The verdict is sound and the recommendation — keep the rule
cascade and human adjudication — should stand.

But the report identifies a byproduct worth more than the spike: **14
rule-confirmed links score below 0.5, and 202 unlinked pairs score at or above
0.99.** Those are 216 concrete rows where the rule cascade and a probabilistic
model disagree, sitting in `data/splink_spike_scores.csv` under `rule_status`.
The report says they are "worth a review pass on their own terms" and no pass
has happened.

**Done on this branch**, as `link_disagreement_audit.py`, wired into
`pipeline.yml` after project resolution with an eleven-check selftest.

It is deliberately asymmetric about what is live. The match probabilities stay
frozen, read from the committed `data/splink_spike_scores.csv`, because Splink
is not a dependency of this repository and is not being made one. The rule
state is re-read every run from `project_links.csv`, `project_link_review.csv`
and `project_links_manual.csv`. A row therefore leaves the worklist when the
rules change their mind or a person adjudicates the pair, which is what makes
it a worklist rather than a snapshot.

That the live half moves is already visible: the spike reported 14 and 202, and
against the current rule state the same thresholds give **7 rule-confirmed
below 0.5 and 171 unlinked at or above 0.99**, 178 open. Adjudicated pairs are
excluded outright — a person who has ruled has settled it, and that is the
population the model scored worst on anyway.

The cost of the frozen half is reported rather than hidden: **281 of 311**
current links carry a score, so 30 were created after the spike ran and cannot
be audited. When that gap grows, the answer is to re-run the spike, not to read
a shrinking sample as the whole.

The spike itself was **not** retired. Removing it would discard the only thing
that can regenerate the scores this audit depends on, which would be a strange
way to finish wiring the audit up.

### C2. The five `needs_manual_pin` facility sources

`configs/facility_sources.json` declares six Layer A acquisition sources. One
(`permit_graduations`) is live. The other five are each blocked on **one manual
look**, because the CI egress proxy blocks their hosts:

| source | blocked on |
|---|---|
| `osti_im3_atlas` | a direct download URL for the current release, plus cadence |
| `epoch_frontier` | a machine-readable export path |
| `hyperscaler_footprints` | four page URLs and a note on each page's shape |
| `pa_dep_datacenter` | one ArcGIS layer URL from browser dev tools |
| `ercot_large_load` | the stable report URL from the ERCOT product library |

This is the largest dataset in the repository and the only layer with no
acquisition pipeline; both snapshots read `vintage_status: undeclared`, meaning
the age of the facility data is genuinely unknown. The registry, identifiers and
provenance shipped 2026-08-26. Only the pins are missing.

The pins are not the work — a person with a browser does those in an afternoon.
The work is the five adapters behind them, and they are not equal. Do
`pa_dep_datacenter` first: `discover_arcgis_layer.py` already ran in CI and
returned zero candidates, the ArcGIS adapter and ingest machinery already exist
and already run weekly for Loudoun, and one `configs/pa_dep_datacenter_ingest.json`
joins it to the existing run with no new code. The other four are new adapters.

### C3. Supply `configs/frames/tva_198_counties.csv`

`configs/frames/` does not exist, so `subframe_audit.py` reports the TVA frame as
PROVISIONAL and its recall figures as a proxy over an approximated frame. The
county list is in the deliverable's appendix. Dropping the file in flips the
audit to COMPLETE on the next nightly run **with no code change** — the audit
already takes `--fips-file`.

Ranked here rather than in section 2 only because the work is locating and
transcribing an authoritative 198-county list, which is care rather than code.

### C4. Make the permit layer more than one county

Nine registered permit sources; **one** (Loudoun LOLA) is resolved and
producing `data/permit_candidates_loudoun_lola.csv`. `wa_sepa` has a Socrata
discovery kind and an unresolved URL. Six western probe sources were registered
2026-09-09 and their first run is 2026-09-15 — pending, and correctly excluded
from this scan.

The right move on the probe sources themselves is still to let the 2026-09-15
run happen and read the reports before building anything against them.

The standing gap was separate and is **addressed on this branch**. Each newly
resolved source needed a hand-written `configs/<source>_ingest.json`, and only
Loudoun had one, so a resolved layer produced candidates and stopped there.
`permit_ingest_scaffold.py` now reads the rows a fetch just produced and
proposes the column map, the terminal-status vocabulary and the source URL. It
runs from `fetch-permits.yml` in exactly the branch that previously only echoed
"candidates fetched only, not promoted".

Validated across the vocabularies the common portal platforms use — Accela,
Socrata-style snake_case, Legistar, CivicPlus, plain ArcGIS — rather than
against any one jurisdiction. Only one source in the tree has both fetched rows
and a hand-written config, which makes it the only pair available to check
against; it is a fixture, not a target. Patterns tuned to one county's column
names would propose nothing useful for the other nine sources, which is the
whole point of the item.

It reproduces that hand-written config exactly (`name: PlanName`,
`announced_date: PlanApplicationDate`, `status: PlanStatus`,
`terminal_statuses: [approved, denied]`, plus the source URL), and resolves all
three fields on Accela, snake_case, Legistar and CivicPlus shapes too.

The traps matter more than the hits, because a wrong proposal here does not
fail — it labels or dates every row of a jurisdiction with the wrong field and
exits clean:

| header set | proposes |
|---|---|
| `ApplicantName`, `PermitStatus` | status only; **no name** |
| `OwnerName`, `Acres` | **nothing** |
| `ProjectName`, `Status` (no date column) | name and status; **no date** |
| `ProjectName`, `Applicability` | name only; **no date** |

A person or contact column will match a bare `name` pattern all day, so
applicant, owner, agent, engineer and the rest are excluded outright; and a
header is eligible for the date slot only if it actually says date. In each
case the field comes back unresolved, listed in the scaffold, for a reviewer to
fill in. Nineteen-check selftest covering all of the above.

Worth recording that the selftest earned its place immediately. The first
implementation anchored its patterns as `\bstatus\b`, which matches nothing in
`PlanStatus`: there is no word boundary between "n" and "S", and portal schemas
are overwhelmingly CamelCase. The check that reproduces the real Loudoun map
failed on the first run and the anchors came out.

### C5. Cost translation, from built to client-facing

`cost_translation.py` is implemented and running weekly. It is held back by its
own rule — nothing client-facing "until the delay measurement reaches adequate n
and the anchors are re-verified at publication time" — and by item 0, which has
taken `capacity_mw` from 123 rows to 3 and so emptied the demo.

Correctly gated. It sits last here because its gate is a genuine dependency on
the landmark and delay work, not neglect. Worth stating explicitly so it is not
repeatedly rediscovered as "unused code": it is deferred, and deliberately.

---

## 4. Correctly dormant — leave alone

Recorded so this ground is not re-litigated next scan.

- **Six western probe configs** (`az_acc_edocket`, `co_puc_efilings`,
  `or_dlcd_papa`, `or_puc_edockets`, `wa_utc_dockets`, `ca_ceqanet`). Registered
  2026-09-09; `fetch-permits.yml` fires Tuesdays and next runs 2026-09-15. They
  are enumerated dynamically by `discover_endpoint.py --list-probe-sources`, so
  a filename search finds no consumer and reports a false orphan. Pending, not
  dormant.
- **The landmark gate (CLOSED), the emergence gate (LOCKED), the calibration
  gate (waiting).** All three are closed on data quality by design and all three
  re-evaluate on a schedule. A closed gate that reports why it is closed is the
  system working.
- **`scripts/run_dispute_watch_batch.py`.** A deliberate manual helper that
  appends across runs because `dispute_watch.py --out` overwrites. Legitimately
  operator-invoked.
- **`qc/qc_report.md` and `qc/quarantine.json`** versus the root copies. Not
  strays: `qc.yml` writes the `qc/` pair on `qc/**` changes only, `pipeline.yml`
  writes the root pair nightly, and the workflows document the split. The
  same-name-different-content hazard is real but declared; noting it, not
  changing it.

---

## Summary

| | count | items |
|---|---|---|
| Live regression | 1 | six dropped scraper columns; landmark anchor at 10/337 |
| Remove | 5 | `census_join.py`, `qc/fetch_notion.py`, `qc/enrichment.py`, `scripts/fix_project_id_collision.py`, two root worklists |
| Easy to wire | 6 | B1–B6, four of them one or two lines |
| Real work | 5 | C1–C5 |
| Correctly dormant | 4 | probe configs, three gates, batch helper, qc artifact split |

The finding worth carrying forward is not any single dormant module. It is that
a module nobody runs was the only thing that could see a live regression, and it
could not report it because it was not wired in. Three of the six easy items
(B2, B3, B4) are modules that already work, already have selftests, and in two
cases already have their outputs committed by a workflow that never generates
them. The cheapest reliability improvement available here is connecting checks
that were already written.
