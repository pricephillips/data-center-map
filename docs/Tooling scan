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
