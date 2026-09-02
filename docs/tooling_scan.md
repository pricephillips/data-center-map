# External Tooling Scan

Repos, APIs, and datasets worth pulling into the platform, ranked by how much
manual work each one removes per hour of integration. Scanned 2026-07-23.

Two rules apply to everything below. Nothing external writes directly to
`master_opposition.csv`; external feeds produce candidate worklists that a
person verifies and enters. And nothing external becomes a load-bearing
dependency of the clean feed; every integration is a separate workflow that
can fail without holding up the map, the dashboard, or the Iowa sync.

## Tier 1: integrate now

### GDELT 2.0 Doc API
`https://api.gdeltproject.org/api/v2/doc/doc` | client: `alex9smith/gdelt-doc-api` (pip `gdeltdoc`)

Free, no key, no login, updates every 15 minutes, indexes local outlets that
never surface in a Google News query. This is the single highest-payoff item
on the list because the find-it step currently costs more analyst time than
the verify-it step.

Status: implemented this session as `signal_harvest.py`, stdlib-only against
the raw endpoint rather than the `gdeltdoc` client, so the harvester has no
new dependency. If the query set grows past what the raw endpoint handles
cleanly, `gdeltdoc` is the drop-in upgrade; it returns a DataFrame and adds
timeline modes that would let coverage volume itself become a feature.

Caveat: GDELT indexes coverage, not events. Several articles about one
hearing is not several events. The harvester dedupes by URL only, so
event-level dedupe stays a reviewer judgment.

### Open States (Plural) API v3 and bulk data
`https://v3.openstates.org/` | `openstates/pyopenstates` | bulk: `https://open.pluralpolicy.com/data/`

All 50 states plus DC and PR, standardized bill records with actions, votes,
sponsors, and status history. Free API key. Monthly bulk Postgres dumps for
backfill.

Payoff: this replaces manual bill-status checking outright and it maps
directly onto `legislative_outcome.py`. The stage ladder currently reads a
hand-maintained status string; Open States supplies the full action history,
which is exactly what the ladder needs to distinguish a terminal disposition
from a committee milestone. It also solves the recurring sine die problem,
since chamber calendars come with the data.

Integration shape: a `bill_sync.py` that matches tracked `legislation`
records to Open States bill IDs, pulls the action history, and writes a
`data/bill_status_review.csv` flagging any record whose coded status
disagrees with the current action history. Review-gated, same as the
harvester. Roughly a day of work; the state-by-state matching is the fiddly
part.

Built 2026-07-27 as `bill_sync.py`, stdlib only, no new package dependency.
Offline `--extract` pass over the countable feed finds 392 legislative
records, of which 153 carry a parseable bill identifier, 196 carry none, and
43 are federal and skipped. Identifier extraction requires a known prefix
plus digits; the NY and NJ single-letter chamber forms (S731, A796) are
recognized only in those two states so the pattern does not fire on ordinary
prose elsewhere.

Stage classification keys off Open States machine-coded action
classifications rather than prose substrings, mapped onto the same ladder
`qc/legislative_outcome.py` enforces, terminal-first. Chamber passage is
counted per distinct chamber so two votes in one chamber are not mistaken for
both. A sustained veto stays Blocked and an overridden one becomes law. The
`milestone_coded_as_enacted` flag is the HF2690 trap and is emitted at HIGH
severity.

The one thing the API cannot supply is sine die. Open States emits no such
action, so a non-terminal bill with no activity in over a year is flagged
`possible_sine_die_unconfirmed` at LOW severity for a session-calendar check,
never auto-coded as dead. Responses are cached in `data/bill_sync_cache.json`
and terminal bills are never re-fetched, so steady-state runs make close to
zero calls against the 500/day free tier.

Caveat: coverage of local ordinances is nil. Open States is state
legislatures only, so it addresses the `legislation` slice and nothing else.

### Census gazetteer and TIGER county boundaries
`https://www2.census.gov/geo/tiger/` (already partially in use via `fetch_census_features.py`)

The two-mode clickable map needs county polygons. TIGER county shapefiles
simplified to topojson are the standard source and would drop straight into
the existing Leaflet layer alongside `county_policy_scores.csv`. No new
runtime dependency if the simplification runs once and commits the artifact
rather than generating it per build.

## Tier 2: integrate when the county and permit layers mature

### civic-scraper
`biglocalnews/civic-scraper` (pip `civic-scraper`)

Downloads agendas and minutes from CivicPlus, Legistar, Granicus, and
PrimeGov portals. Most county boards in the dataset run one of these four
platforms.

Payoff: agendas are the earliest possible signal. A rezoning application on
next month's planning commission agenda precedes any news coverage by weeks,
which is the difference between a tracker and a leading indicator. It is also
the only route to the announced dates that keep coming back as gaps.

Why Tier 2 rather than Tier 1: it needs a per-jurisdiction portal URL list,
and PDF text extraction on scanned minutes is genuinely messy. Scope it to
the twenty or thirty counties that carry the most tracked projects rather
than attempting national coverage, and it becomes a two-day build instead of
an open-ended one.

Related: `opencivicdata/python-legistar-scraper` if Legistar coverage alone
turns out to be sufficient, which for larger counties it often is.

### Interconnection.fyi / GridTracker
`https://www.interconnection.fyi/`

Free public view of interconnection queue requests across 50-plus ISOs and
utilities, updated daily, including a data-center-projects-by-state cut and
EIA-860 planned plant data. This is the same dataset behind LBNL's Queued Up
reports.

Payoff: a genuine independent baseline universe. The verified-negative audit
is the current bottleneck on modeling opposition emergence, and a queue-based
universe is the cleanest external source of projects that exist but have no
opposition record. It also gives a capacity and timeline anchor for the cost
layer.

Caveat: the free web view is not a bulk feed; the complete dataset with
developer names is a commercial subscription. Evaluate whether the free cut
carries enough to seed the audit sample before committing to a paid tier.
Also note that a generation interconnection request is not a data center, so
the join to tracked projects is inferential and would need its own
confidence tiering.

Scoped 2026-07-27 in `docs/interconnection_queue_scoping.md`. The caveat above
understated the problem. LBNL Queued Up covers generation interconnection only
and its own documentation states that load interconnection requests are
excluded, so the free citable dataset structurally cannot contain data center
projects; a data center is load. The Pass 1 framing of the queue as a baseline
universe of data center developments does not hold. Interconnection.fyi does
expose a Load filter and a data-center cut in its free view, but project-level
detail is the commercial GridTracker tier.

Recommendation from the memo, in short: take the Queued Up data file now for
cost-layer timeline anchors, which is free and defensible; do not build a
baseline universe from the generation queue; revisit the paid tier in Q4 2026
against three stated gate conditions. The timing matters because FERC's
2026-06-18 show cause orders put RTO large-load tariff filings in late August
and the PUCT SB 6 transparency rulemaking concludes by December, so what is
public is actively changing.

Two things surfaced that are worth carrying forward independently. GridTracker
markets permit-based data center build tracking, which overlaps this
platform's own coverage layer and is the best available external validation set
for the project list whether or not it is ever licensed. And if FERC's orders
compress interconnection timelines, the opposition-attributable share of total
delay rises even with opposition unchanged, so the cost layer must not hardcode
a 2025 interconnection-delay baseline.

### LBNL Queued Up
`https://emp.lbl.gov/queues`

Published annual dataset and report. Not a live feed, but the source for
defensible timeline anchors: median time from interconnection request to
commercial operation, completion rates by region and vintage. These are
exactly the kind of published industry anchors the cost-translation layer is
specified to use, and citing LBNL is more defensible than citing a vendor.

## Tier 3: worth knowing about, not worth building against yet

- `City-Bureau/city-scrapers` - mature and well-maintained, but Chicago-scoped. The scrapers themselves are not reusable nationally; the Scrapy patterns are.
- `codeforamerica/open-civic-datasets` - a curated index rather than a tool. Useful for finding county-level covariates (CDC PLACES, CDC SVI) if the county model needs more features than the current census and vote set.
- `Data4Democracy/town-council` - the right idea, dormant since 2017. Reference only.
- `govwiki/civic-scraper-v2` - narrower and less maintained than the biglocalnews original. Skip.

## Suggested order

1. `signal_harvest.py` weekly, review the first two worklists, tune the query set and the priority weights against what the reviews actually find. Done this session; the tuning is the next real step.
2. Open States bill sync. Highest ratio of manual work removed to code written, and it directly strengthens an existing discipline rule rather than adding a new surface.
3. TIGER county polygons, which unblocks the two-mode map.
4. civic-scraper scoped to the top counties, once there is a clear list of which counties matter most.
5. Interconnection queue evaluation, timed to whenever the verified-negative audit is actually started.

---

# Pass 2: Second Scan

Scanned 2026-07-23. This pass covers libraries rather than data sources, plus
a licensing screen, because a dependency that cannot ship in a client
deliverable is not a safe dependency no matter how good it is.

## Licensing screen (read this first)

The platform produces client-facing work product and runs a public-facing
site. That makes copyleft licenses a real constraint, not a formality. The
split below is the practical one.

| Tool | License | Safe for this platform |
| :-- | :-- | :-- |
| Splink | MIT | Yes |
| lifelines | MIT | Yes |
| MAPIE | BSD-3-Clause | Yes |
| pdfplumber | MIT | Yes |
| pypdf | BSD-3-Clause | Yes |
| civic-scraper | Apache 2.0 | Yes |
| TIGER/Line shapefiles | Public domain (US government work) | Yes |
| PUDL data outputs | CC-BY-4.0 | Yes, with attribution |
| scikit-survival | GPL-3.0 | Flag before adopting |
| PyMuPDF | AGPL-3.0 | Flag before adopting |
| fitnr/censusgeocode | GPL-3.0 | Avoid; call the API directly instead |
| Regrid / Landgrid parcels | Commercial | Paid, evaluate separately |

The Open States organization mixes licenses across its repos, so check the
specific repo rather than assuming the org default. The API itself is a
service, not a distributed dependency, which sidesteps the question for the
bill-sync use case.

None of the above is legal advice. The point is that these three
(scikit-survival, PyMuPDF, censusgeocode) each have a permissive
substitute that does the same job, so there is no reason to take on the
question at all.

## Modeling and statistics

### Splink
`moj-analytical-services/splink` | MIT | active

Probabilistic record linkage in the Fellegi-Sunter tradition, DuckDB backend,
unsupervised so no training labels required. This is the strongest candidate
on either pass for a piece of work already underway.

Fit: `project_resolution.py` currently links opposition events to projects,
and Phase 2 needs opposed projects matched to a baseline universe. Both are
textbook linkage problems and both are currently solved with hand-written
matching rules. Splink would replace the rules with estimated match
probabilities, which has two advantages beyond accuracy: every link carries a
score, and a score threshold is auditable in a way a rule cascade is not.
That matters directly for the Type B opposition-link review, where the
question is which links are weak enough to need eyes on them.

Caveat that decides the fit: Splink explicitly does not work on a single
"bag of words" column. Linking on project name alone would fail. The dataset
has name plus state plus county plus operator plus capacity, which is exactly
the multi-column, low-correlation shape it wants. Worth a spike before
committing.

Spike result, 2026-07-27: NO-GO on adoption. `splink_spike.py` ran the model
over 1,443 countable events and 333 projects and evaluated it against the 97
human adjudications in `data/project_links_manual.csv`, with the criteria
fixed before the run. Full writeup in `data/splink_spike_report.md`.

The model separates the easy population well, AUC 0.947 against presumed
negatives and 88 percent top-1 recovery of confirmed links, but that is the
population the existing rules already resolve. On the adjudicated pairs it
reaches AUC 0.632 against the incumbent corroboration count at 0.568, and on
the contested subset, which is the Type B population the spike existed to
serve, AUC 0.605 against a base rate of 0.517. It is confidently incorrect on
21 contested rejects, scoring them at or above 0.99.

The reason is structural, not a tuning problem. On a contested pair the
candidate projects share developer, state, county, and often a coordinate
cluster, so every structured field agrees by construction and a
field-comparison model has nothing left to discriminate on. Blocking is the
second constraint: geography blocking cannot reach a cross-border pair, and a
company-token rule that does reach them also generates every same-developer
pair in the region at high probability. Blocking recall on the adjudicated
set was 0.918.

What survives: the score is a useful disagreement surface. Fourteen
rule-confirmed links score below 0.5 and 202 unlinked pairs score at or above
0.99. That is a review worklist the rule cascade cannot produce, and it is
worth a pass on its own terms. The spike module stays in the repo as a
manually run audit tool, out of `pipeline.yml` and out of every CI dependency
list.

### lifelines
`CamDavidsonPilon/lifelines` | MIT

Cox proportional hazards, AFT models, Kaplan-Meier, Nelson-Aalen,
Aalen-Johansen. `survival_model.py` already implements Cox; lifelines would
mainly buy AFT variants and, more usefully, the competing-risks estimators.
The recorded finding that the Cox model's near-chance discrimination reflects
a competing-risks structure rather than a modeling failure is exactly the
claim that Aalen-Johansen cause-specific cumulative incidence functions would
let the platform substantiate rather than assert.

Prefer this over scikit-survival, which covers similar ground under GPL-3.0.

### MAPIE
`scikit-learn-contrib/mapie` | BSD-3-Clause

Conformal prediction on top of any scikit-learn estimator. Produces
distribution-free prediction intervals with coverage guarantees, using a
familiar fit and predict interface.

Fit: the platform's own rule says model outputs must be reported as
calibrated ranges with uncertainty and never as unexplained point estimates.
Conformal intervals are the cleanest way to satisfy that rule, because the
coverage guarantee holds without assuming the model is well specified, which
is the honest position at the current sample size. This applies to the cost
layer most directly, where a defensible dollar range matters far more than a
defensible dollar number, and it would also let the site screener eventually
carry an interval rather than a bare tier.

Small dependency, no new infrastructure, and it strengthens the most exposed
part of the product. Second priority after the Open States sync.

## Energy and infrastructure data

### PUDL (Public Utility Data Liberation)
`catalyst-cooperative/pudl` | data CC-BY-4.0 | nightly builds

Cleaned, integrated EIA 860, EIA 923, EIA 861, EPA CEMS, and FERC Form 1
data, published as parquet with nightly continuous integration and quarterly
versioned releases on Zenodo. It is the only regularly maintained free
connection between FERC and EIA plant identifiers.

Fit: county-level generation capacity, plant locations, and utility service
territory are all plausible county-model features, and unlike most candidate
features they come from a source with a stable schema and a documented update
cadence. It also underpins any serious version of the cost layer, since
capacity and utility context are what a cost-of-delay figure has to be
anchored to.

Practical note: take the published parquet outputs, not the ETL pipeline
itself. Running PUDL's own pipeline in CI would be a heavy dependency for no
benefit; a scheduled fetch of the specific tables needed is the right shape,
mirroring how `fetch_census_features.py` already works.

Built 2026-07-27 as `fetch_pudl.py`, writing
`data/county_pudl_features.csv` keyed on 5-digit FIPS, same role as
`county_census_features.csv`. Features: operating capacity total and by fuel
group, plant count, planned capacity (total, gas, renewable) held in separate
columns, and capacity with a retirement date inside five years. Quarterly
workflow pinned to a versioned release rather than the nightly build, since a
nightly is not reproducible.

The design constraint that drove the module: PUDL renames and restructures
tables between releases, so nothing about the column layout is hardcoded.
Each concept the aggregation needs is resolved through a candidate-name list,
the resolution is verified at runtime, and a missing required concept aborts
the run and prints the file's actual column list. Same discipline as
`county_policy_intervals.py` refusing to ship on a guessed MAPIE layout. If
no FIPS column exists in a given release, the module falls back to a
county-name join against `data/county_aggregate.csv` and reports every name
it could not resolve.

Planned capacity is kept in its own columns on purpose. Operating capacity is
a slow-moving stock and is safe pre-announcement information; planned
capacity is forward-looking, so it is stamped with its report year and any
model that uses it has to respect the year boundary rather than treating it
as timeless.

Not yet wired in. `county_aggregator.py` does not join this file and
`county_policy_model.py` has no spec that uses it. That is the follow-on
pass, deliberately held until a real fetch exists to check the join and the
coverage rate against, since a join validated only against fixtures is not
validated.

### EIA Open Data API v2
`https://www.eia.gov/opendata/` | free key

Direct API access to the same underlying series. Useful for anything needing
more recency than PUDL's release cadence.

Honest note on the Python wrappers: the ecosystem is thin. `pyEIA` has been
dormant since 2015, `eiapy` is not on PyPI, and the others are lightly
maintained one-person projects. The API is a plain REST JSON endpoint, so
call it directly with stdlib and skip the wrapper question entirely, the same
approach `signal_harvest.py` takes with GDELT.

## Geocoding and boundaries

### Census Geocoding Services API
`https://geocoding.geo.census.gov/geocoder/` | public, no key

One-line address geocoding and a batch endpoint good for 10,000 rows per
file, returning coordinates plus the full census geography hierarchy, which
means FIPS comes back for free. This directly serves the four proposals that
currently fail FIPS resolution and any future address-level ingest from
permit or agenda sources.

Call the REST endpoint directly. The popular `censusgeocode` wrapper is
GPL-3.0, and the API is simple enough that the wrapper saves nothing worth
the license question.

### TIGERweb and TIGER/Line
`https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb` | public domain

Already flagged in Pass 1 for county polygons. Adding here that TIGERweb
exposes the same geography as a REST service, which is useful for
point-in-polygon county assignment without carrying shapefiles in the repo.

## Document extraction

### pdfplumber, with pypdf as fallback
`jsvine/pdfplumber` | MIT | `py-pdf/pypdf` | BSD-3-Clause

If civic-scraper moves up the queue, agenda and minutes PDFs need parsing.
pdfplumber handles native PDFs and tables well, has minimal dependencies, and
is permissively licensed.

PyMuPDF is genuinely faster and appears at the top of most comparisons, but
it is AGPL-3.0. For a platform that runs a public site and produces client
deliverables, that is a question worth not having. Speed is not the binding
constraint on a few hundred agenda PDFs a week.

Note on scanned minutes: none of these do OCR. Older county minutes are
frequently scans, and text extraction returns empty rather than failing
loudly. Any agenda pipeline needs an explicit per-page emptiness check that
routes to a manual queue rather than silently indexing nothing. That check is
cheap to write and is the single most common failure in this kind of
pipeline.

Be skeptical of the PDF extraction benchmark posts that dominate search
results on this topic. Several of the highest-ranking ones are published by
vendors whose own product tops their own benchmark.

## What to skip

- `scikit-survival`, `PyMuPDF`, `fitnr/censusgeocode`: good tools, copyleft licenses, permissive substitutes exist for all three.
- `pyEIA`, `Data4Democracy/town-council`, `govwiki/civic-scraper-v2`: dormant. A dormant dependency in a nightly pipeline is a future outage.
- Hosted PDF and entity-resolution products (LlamaParse, Tilores, Senzing): all viable, none justified at current volume, and each sends data off-premise.
- Vendor benchmark blogs as a basis for tool selection. Use them to find candidates, then verify against the repos.

## Revised order

Unchanged at the top. Open States bill sync first, then MAPIE conformal
intervals on whatever model output reaches clients first, since that is the
smallest change with the largest defensibility return. Splink after that, as
a spike against the existing link set to see whether estimated match
probabilities beat the current rules on the Type B cases specifically. TIGER
polygons, PUDL fetch, and civic-scraper follow, in whatever order the county
map and the negative audit end up needing them.

Status as of 2026-07-27: the MAPIE work shipped as
`county_policy_intervals.py`, and the Splink spike came back NO-GO on
adoption, so the order below it is unchanged and Open States is next.

## 2026-07-30: entity layer and vocabulary enforcement (built, shipped)

Three modules landed from the Indiana entity verification, all stdlib, all
with passing selftests, all additive. None is Indiana-specific.

`entity_split.py`: one canonical splitter for the free-text multi-entity
columns, replacing three divergent implementations that fabricated
entities. STRICT mode (semicolon and pipe) for Opposition Groups; LOOSE
mode (adds comma and slash at bracket depth zero, corporate suffixes
rejoined) for Company and Hyperscaler. Measured effect on the current
clean feed: 73 phantom group tokens and 52 phantom company tokens no
longer emitted; the company tokens were model feature inputs in
outcome_model.py and landmark_model.py, both now rewired onto the shared
splitter with no feature-schema change. `--scan` prints the diff against
the legacy regexes for any future rule change.

`group_registry_audit.py`: five-flag defect audit of the canonical group
registry (split_artifact, cross_state_merge, suffix_merge, placeholder,
degenerate_rate), writing `data/group_registry_audit.csv` with a
per-entry `client_safe` bit. With the patched registry build, structural
defects fell from 86 entries to 17 and split artifacts from 69 to 1.
Reporting only; never mutates the registry.

`leak_audit.py`: repo-wide scorekeeping-vocabulary audit in three tiers
(blocking for pipeline-composed prose and data, advisory for inherited
source columns, client-side identifiers, and source modules, exempt for
rule definitions and URLs). CSVs scanned per column and JSON per key.
Replaces six private per-module copies of the regex as the repo-level
check; the per-module copies stay as local fast-fail. Wired into
pipeline.yml as a hard gate after the clean feed builds, with the three
internal triage columns exempted per the 2026-07-30 ruling that
internal-only artifacts may quote raw vocabulary.

Registry ruling recorded the same day: the per-group outcome columns
(decided, confirmed_blocks, blocked_share) are permanently internal at
any sample size, group level only; county and project statistics in
four-tier vocabulary remain client-eligible.

Follow-ups noted, not built: selftest coverage stands at 17 of 45
modules against a universal house rule, with metrics.py,
county_aggregator.py, county_policy_model.py, and project_resolution.py
the priority gaps; outcome_model.py report prose carries pre-existing
em-dashes on the internal-diagnostic side of the rule.

## 2026-07-30 (second entry): coverage audit and county label repair (built, shipped)

`coverage_audit.py`: measures the tracker's recall of county-level
restrictive actions against `data/external_restriction_census.csv`, a
lower-bound external census seeded from the Moratorium Nation dataset
(mjbommar/moratorium-data-2026, CC-BY-4.0, 222 moratoria coded from
roughly 4,400 primary documents) plus manually sourced Indiana rows. Per
state it reports counties covered with terminal confirmation, covered
without, and missing outright, and counts census-enacted counties the
county model trains on as negatives. First national run: 80 census
counties in scope, recall 0.625, 30 counties with zero tracker record
(worst: Kansas and North Dakota near 5 missing each, Georgia 5, Indiana
4 including the Cass County ban).

The audit also exposed a label-construction defect in
`county_aggregator.py`: the has_enacted_restrictive rule used exact
string equality on the multi-valued Opposition Type field and a
two-value Status vocabulary, catching only 78 of 125 of the tracker's
own confirmed county-level halts. Fixed by token-splitting the type
field, adding ban to the restrictive set, and widening enacted statuses
to include active, extended, expired (the label is historical), and the
recorded variant. Effect: positives 197 to 323, none dropped, base rate
6.1 to 10.0 percent. Indiana labeled counties 9 to 18 against the IU
ERI reference of roughly 30.

The label change forces a county model retrain. Preview run: AUC 0.82
[0.77-0.85], Brier 0.078 against 0.092 no-skill, post-recalibration
slope 0.99 at the 10 percent base rate. Promotion goes through the
calibration gate as usual; restriction-model.html copy must be updated
to the new base rate and interval set before any client exposure.

Moratorium Nation is now a standing external dependency for the census
seed; refresh cadence and attribution (CC-BY-4.0) documented in the
census source column. Remaining known gap: municipal-level census rows
are out of scope for the county label but represent the next recall
surface.

---

## 2026-09-02: Pass 3, western coverage scan (scoping, not built)

Triggered by a reader question about why `opposition-map.html` looks empty
west of the Rockies. Two separate causes, and only one of them is a source
problem.

`data/proposals.csv` has zero western rows out of 338 — the pin layer's blank
is inherited from trackdatacenters.com, whose registry is East and Midwest
only, and `scripts/scrape-trackdatacenters-proposals.py` applies no
geographic filter of its own. The county layer is not blank: 156 events
across 72 western counties, but at 1.98 events per million people against
4.70 elsewhere, and against a p99 ramp ceiling of 14 most of them render
close to the no-data grey.

Full scan in `docs/western_coverage_sources.md`. Headline findings:

- The cheapest fix is internal. `signal_harvest.locate()` resolves geography
  from headlines against a county-name gazetteer with no place names, and
  164 of 227 rows in the current `data/signal_candidates.csv` carry
  `location_confidence = "none"`. Western headlines name cities, not
  counties, so what goes unresolved is regionally skewed.
- `local_meeting_feed.py` discovery resolves 3 of 808 jurisdictions and has
  no Granicus or PrimeGov probe. This is the evidence for promoting the
  Tier 2 `civic-scraper` entry above.
- Washington's SEPA Register is on `data.wa.gov`, which is Socrata, and
  therefore drops into `fetch_permits.py` as a config with no new code.
  Statewide, back to 2000. Best coverage-per-hour on the list.
- Oregon DLCD PAPA notices and California CEQAnet are the statewide
  equivalents for those states; both need an adapter or a pin.
- The west has no load interconnection queue to scrape. Non-ISO utilities
  connect large loads through state-approved retail tariffs, which relocates
  the signal to state PUC dockets and means the FERC show-cause timeline in
  `docs/interconnection_queue_scoping.md` will not improve western
  disclosure.
- `jwklee/data-center-opposition-tracker` is an independent, county-coded,
  vocabulary-controlled opposition dataset. Sized against ours it deepens
  about twenty thin western counties and adds two new ones. Enrichment, not
  a gap-filler. CSV on request; email rather than scrape.

### 2026-09-02, same day: items 1 to 3 built

The scan above is no longer purely a scan. Three of its seven recommended
steps are implemented in the same change:

- `gazetteer.py` builds `data/place_gazetteer.csv` from the Census national
  county-subdivision file, with an offline fallback derived from the
  (City, County, State) triples already in `master_opposition_clean.csv` and
  `data/proposals.csv`. The cousubs file is chosen over the places file
  because its GEOID already carries the county, which removes any need for a
  relationship file, a point-in-polygon pass, or 32,000 geocoder calls.
  `signal_harvest.locate()` gains a second pass against it, and the 5-char
  county rule now matches short names in the literal "<name> County" form
  instead of skipping them, which is what lets Pima and Ada resolve.
- `fetch_osm_facilities.py` pulls OpenStreetMap data centers through Overpass
  into `data/facility_candidates_osm.csv`, gated into the registry by a new
  `facility_registry.osm_candidates()` stream. This is the route around the
  `needs_manual_pin` status on `osti_im3_atlas`: the IM3 atlas is OSM-derived,
  and Overpass answers where osti.gov does not.
- `discover_socrata_dataset.py` resolves a Socrata four-by-four from a portal
  catalog the way `discover_arcgis_layer.py` resolves an ArcGIS layer, and
  `configs/wa_sepa.json` registers Washington's SEPA Register against it.
  `fetch_permits.py` now reads either discoverer's resolved block, so a
  Socrata jurisdiction is a config drop like an ArcGIS one.

One design note that generalizes beyond this change. The place resolver
refuses to treat "this name appears once in my index" as evidence of
uniqueness unless the index is national, and records which it is in the build
manifest. A sparse index makes every rare name look unique, which is how a
Portland headline about Oregon lands in Chautauqua County, New York. Any
future index-backed matcher here should carry the same flag.
