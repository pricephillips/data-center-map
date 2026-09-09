# Western Coverage: Source Scan

Prepared 2026-09-02. Answers one question: what can we scrape alongside
`trackdatacenters.com` to close the western hole in the project and event
layers. The scan came first and the top of the list is now built; the status
table below says which parts. Nothing here writes to `master_opposition.csv`
or `data/proposals.csv` — every candidate lands in a worklist a person clears,
per the two standing rules at the top of `docs/tooling_scan.md`.

"West" throughout means WA OR CA NV ID MT WY UT CO AZ NM AK HI: 449 counties,
78.6M people.

## Status: what of this is now built

Updated 2026-09-09. Items 1, 2, 3 and 5 in the recommended order at the bottom
are implemented, along with the two map-side fixes the original measurement
turned up. Items 6 and 7 are now registered for reconnaissance rather than
built: the thing blocking both was never the adapter, it was that nobody had
confirmed what the sites serve, so that question is now asked by a scheduled
job instead of sitting in this memo. Item 4 stands as scoped — it needs a
person, not code.

| # | Step | Status |
| :-- | :-- | :-- |
| 1 | Place gazetteer in `signal_harvest.locate()` | **Built and live.** `gazetteer.py`, wired into `locate()` as a second pass. The 5-char county rule is replaced rather than removed: a short name now matches in the literal "<name> County" form, so Pima and Ada resolve where before they could not match at all. The Census index landed 2026-09-06, so the national gate described below is open. |
| 2 | WA SEPA Register as a Socrata config | **Built.** `configs/wa_sepa.json` plus `discover_socrata_dataset.py`, which resolves the 4x4 resource id the way `discover_arcgis_layer.py` resolves an ArcGIS layer. `fetch_permits.py` reads either discoverer's output, so no new fetch code. |
| 3 | Overpass pull into `data/facility_candidates.csv` | **Built.** `fetch_osm_facilities.py` writes `data/facility_candidates_osm.csv`; `facility_registry.osm_candidates()` gates it. |
| 4 | Email `jwklee` for the tracker CSV | Not started; needs a person, not code. |
| 5 | Granicus + PrimeGov probes in `local_meeting_feed.py` | **Built.** Both probes and both fetchers added, and the four probes now share one slug helper instead of three drifted copies of the same `.replace()` chain. |
| 6 | CEQAnet pin, then Oregon PAPA adapter | **Reconnaissance registered.** `configs/ca_ceqanet.json` and `configs/or_dlcd_papa.json` register both sites as probe sources, and `discover_endpoint.py` reports in CI what each candidate URL actually serves. Neither can fetch anything: both carry `"adapter": null`, so `fetch_permits.list_sources()` does not enumerate them. The adapter, if one is warranted, gets written against the report rather than against a guess. |
| 7 | Western PUC docket scan | **Reconnaissance registered.** All four commissions named in Finding 4 are probe sources: `configs/wa_utc_dockets.json`, `configs/co_puc_efilings.json`, `configs/or_puc_edockets.json`, `configs/az_acc_edocket.json`. Same terms as item 6 — `"adapter": null`, no fetch possible, a report per commission saying what each route actually serves. The open-ended part was always "none is a documented API"; that is now a question with a scheduled answer rather than an estimate. |

Two things about item 1 are worth stating plainly, because the measured
result is smaller than the scan implied and the reason matters.

**The place pass is gated on a national index, and shipping it did not turn
it on.** "This name occurs once in the index, so it is unambiguous" is only
sound if the index covers the country. Built from repo records alone it does
not: it holds exactly one Portland, and a headline reading "Portland moves to
keep data center deals out of the shadows" resolved to Chautauqua County, New
York, when the article is about Oregon. So `gazetteer.py` records
`national: false` in its manifest when the Census fetch has not run, and
`resolve()` then refuses every match the headline does not also state a
state for.

`.github/workflows/acquire-geo-sources.yml` ran on 2026-09-06 and flipped it.
The manifest now reads `national: true`, 32,856 rows over 16,686 distinct
place names, and the place pass is live.

That run also broke `main`, and the way it broke is worth recording next to
the source scan rather than only in the fix. Ten of those 32,856 Census
county-subdivision names contain a word `leak_audit.py` treats as scorekeeping
vocabulary at the blocking tier: one township name shared by four counties, a
Minnesota lake, and five others, all of them ordinary proper nouns naming real
places. (They are listed in `leak_audit.py`'s exemption comment rather than
here, because writing them out in prose trips the same audit.) `pipeline.yml`
runs the audit as
a hard gate, so the nightly build aborted before its commit step for four
consecutive nights. The names are federal reference data and are exempted as
exact file-plus-column pairs. **The generalizable point: the file was checked
for exactly this before it shipped, but against the 799-row offline build,
which contains none of those names. A check run against a stand-in for what a
generator produces is not a check of what it produces.**

**Measured against the live worklist.** On the 146 rows in
`data/signal_candidates.csv` as of 2026-09-09, county-level resolution
(`high` or `medium`) stands at 18 and rows carrying no geography at all at 93.
The earlier estimate in this memo — 27 -> 47 on a 227-row worklist — was
computed from the 802-row offline gazetteer and is superseded by the figures
above; the worklist has itself turned over since, so the two are not a
before-and-after pair and should not be read as one.

Four false attributions found by spot-checking the first draft are now
refused by name, and each is a selftest: "El Reno" no longer resolves to Reno
NV, "Buckeye Country 105.5" no longer resolves to Maricopa County, and
"Industry warns of blackouts" and "The Rapid Buildout of Data Centers" no
longer resolve at all.

## The two map fixes

Both come straight from the measurement at the top and neither is about
acquisition, so they are recorded here rather than left implicit.

**A blank region on a pin map is ambiguous by construction.** It reads as "no
projects here" when it can equally mean "this registry does not cover here",
and for the west it is the second. Project-pins mode now carries a coverage
note computed from the loaded rows rather than written as prose, so it states
what is true on the day it renders and retires itself the moment the registry
gains western projects. Verified against the real registry: 329 projects with
coordinates, 0 western, note shown; with six western pins injected the note
disappears on its own.

**A county with one recorded event was rendering as a county with none.** The
count ramp starts at the bottom of the sequential scale, but a count scale
never renders a zero — `countColor()` returns null and the county takes the
no-data fill — so the ramp's bottom is a real value competing with the absence
of one, and it was invisible: composited over the basemap, one event sat at
contrast 1.74 against the no-data grey, against the 3:1 that non-text contrast
needs. `SequentialScale` gained an optional `minPosition`, set for the count
scale only at 0.30, the smallest floor that clears 3:1 for a single event.

The effect on the current data, counting counties rendered under 3:1 against
the no-data fill: **568 before (62 of them western), 0 after.** That is 88
percent of every county carrying at least one recorded event. The floor moves
the ramp, not the data: ordering, saturation and the legend's tick values are
unchanged, the legend gradient now starts where the map's does, and the
distribution histogram still bins on the unfloored position so it keeps
showing the true shape. The calibrated-score scale is deliberately untouched —
its low end is a meaningful probability and lifting it would misreport one.

## The hole, measured

| Layer | File | Western coverage |
| :-- | :-- | :-- |
| Project registry | `data/proposals.csv` | **0 of 338 rows.** Westernmost pins are four in North Dakota and one in South Dakota; nothing past -103.8 |
| Opposition events (clean) | `master_opposition_clean.csv` | 198 of 1,657 rows (11.9%) |
| Opposition events (raw) | `master_opposition.csv` | 503 of 4,957 rows (10.1%) |
| County rollup | `data/county_aggregate.csv` | 156 events across 72 of 449 counties (16% of counties shaded, vs 21% elsewhere) |
| Facilities | `atlas.csv` + `ai_centers.csv` | 483 of 1,477 (33%) |

Two things follow. The clean feed is not the problem: the western share rises
from 10.1% to 11.9% across the cleaning step, and `quarantine.json` holds four
western rows out of 153. And the west has a third of the tracked facility base
but a sixth of the recorded events — 1.98 events per million people against
4.70 elsewhere. The shortfall is in acquisition.

## Finding 1: the cheapest fix is not an external source

Two mechanical limits inside the repo cost more western coverage than any
source on this list would add.

**`signal_harvest.locate()` resolves geography from the headline against a
county-name gazetteer only.** `county_index()` (`signal_harvest.py:198`) is
built from the bare county names in `county_aggregate.csv`; `locate()`
(`signal_harvest.py:277`) matches those plus full state names. There is no
place gazetteer. The result is visible in the current worklist: **164 of 227
candidates in `data/signal_candidates.csv` carry
`location_confidence = "none"`** and only 15 are `high`.

This is regionally asymmetric, because the acting jurisdiction differs by
region. In Virginia, Ohio and Michigan the county is the political unit and
the headline says so — "Loudoun County", "Prince William County". In the west
the acting unit is a city or town, and the headline says Tucson, Chandler,
Mesa, Prineville, Hermiston, Quincy, Eagle Mountain, Cheyenne, Reno,
Henderson, Boardman. None of those resolve to a county today. The independent
tracker sampled in Finding 3 sources its Tucson entries to KGUN9 — a local
outlet writing city-named headlines, which is exactly the shape this step
drops on the floor.

Fix: add a place-to-FIPS gazetteer (Census Gazetteer place file, already a
Tier 1 entry in `docs/tooling_scan.md`, plus `census_geocode.py` which is
already in the repo) as a second pass after the county match. Config and data,
not new architecture.

**Secondary: `locate()` skips any county name under five characters**
(`signal_harvest.py:287`) to suppress false hits. Nationally that is 8.5% of
counties and in the west 10.0% — mild on its own, but it removes **Pima (6
events), Ada (5), Lyon (3), Utah (3), Iron (2), Kern, Weld, Nye, King** from
headline matching, and Pima and Ada are the third and sixth ranked western
opposition counties we have. A place gazetteer makes the length rule
unnecessary for these, since "Tucson" and "Boise" are unambiguous where "Pima"
is not.

**Also: `local_meeting_feed.py` discovery resolves 3 jurisdictions out of
808.** `PROBES` (`local_meeting_feed.py:259`) runs civicclerk and legistar
only, both slug-guessed from the *county* name; civicplus is override-only,
and there is no Granicus or PrimeGov probe at all. Granicus and PrimeGov are
disproportionately western-city platforms, and western land-use authority sits
with cities, so county-slug guessing cannot reach it by construction. The
Tier 2 `civic-scraper` entry in `docs/tooling_scan.md` covers all four
platforms and is the existing plan; this is the evidence for moving it up.

## Finding 2: state environmental and land-use registers, one config per state

This is the strongest external category, and it is western-specific in a way
that is worth stating plainly: the three Pacific states each run a *statewide*
register that every discretionary project passes through. One source covers a
whole state, where the eastern equivalent is one config per county.

**Washington — SEPA Register.** Every SEPA and NEPA record filed with the
Department of Ecology since 2000, statewide. Published on `data.wa.gov`, which
is Socrata, and mirrored on `catalog.data.gov`; catalog last refreshed
2026-07-28. **This drops into `fetch_permits.py`'s existing socrata adapter as
a JSON config with zero new code** — the same shape as
`configs/loudoun_lola.json`. Highest ratio of western coverage to effort on
this list. Needs one thing: the dataset's 4x4 resource id, which is one look at
the portal page from an unrestricted network.

**Oregon — DLCD PAPA notices.** Oregon requires every city and county to file
a Post-Acknowledgement Plan Amendment notice with DLCD for any comprehensive
plan amendment or zone change, and DLCD must publish proposals and adoptions
weekly. That is precisely the instrument an Oregon data center rezoning uses,
captured statewide before the vote. Delivery is PAPA Online plus a
subscription notification service and an on-demand reporting service (2017).

The scan called this "not Socrata, so a small adapter rather than a config".
That was an assumption, not a finding, and `configs/or_dlcd_papa.json` now
tests it directly: its first probe asks `data.oregon.gov` — which is a Socrata
portal — whether plan amendments are published there, because if they are the
source collapses to a config like `configs/wa_sepa.json` with no new code at
all. Its second probe asks whether the public reporting tool at
`db.lcd.state.or.us/PAPA_Subscription/` is a `__VIEWSTATE` postback form; if
it is, "a small adapter" is the wrong shape and the route to look for is the
reporting or subscription service instead of the form.

**California — CEQAnet.** The State Clearinghouse database of every CEQA
document filed for state review since 1990, carrying project title, location,
lead agency, contact and description. California is the largest western state
by tracked facilities (112) and by recorded events (25), and every
discretionary data center there files a CEQA notice. API and bulk-export path
are unconfirmed and need a pin, exactly like the PA DEP layer.

Two corrections to the original entry. The host in the 2026-09-02 scan,
`ceqanet.opr.ca.gov`, is stale: OPR is now the Office of Land Use and Climate
Innovation and the database is served from `ceqanet.lci.ca.gov`.
`configs/ca_ceqanet.json` probes both, so whether the old address redirects or
is retired becomes a recorded fact rather than an assumption. And the entry
treated the site as the only route; the config also probes `data.ca.gov` and
`catalog.data.gov`, both CKAN, because a CEQA dataset published on either
would be materially cheaper than the site itself. CEQAnet is also *not*
comprehensive — only documents submitted to the State Clearinghouse reach it —
so it is a floor on California coverage, not a census of it.

Two California specifics worth carrying alongside CEQAnet, both of which fire
*earlier* than a land-use decision. Air-district Authority to Construct
permits for backup generator sets are mandatory (BAAQMD Rule 9-8, SCAQMD 1110.2
and 1470, SJVAPCD 4702, SDAPCD 69.4.1), and a data center's genset bank is
large enough to be conspicuous in a permit stream. And the CEC's Small Power
Plant Exemption docket line (`efiling.energy.ca.gov`, e.g. 19-SPPE-01) is the
route large Silicon Valley campuses take for on-site generation.

## Finding 3: an independent opposition tracker, as a cross-check

`jwklee/data-center-opposition-tracker` — 234 movements, 2022 through
2026-07-06, one schema with controlled vocabularies for level, status and
event type, every entry carrying at least one working source URL.

I sized it against our own data by parsing the published tracker page. 205
entries carry county-level geography and **30 are western (14.6%, against our
11.9%)**. Cross-checking the western entries against
`data/county_aggregate.csv`: most name counties we already have at 1–2 events
(Pima, Maricopa, Pinal, Imperial, Riverside, Washoe, Clark, Denver, Box Elder,
Utah, Doña Ana, Laramie, Silver Bow, Anchorage, Morrow, Hood River, El Paso,
Bannock, Socorro, Larimer), and **two are counties we have at zero: Klickitat
County WA and Deschutes County OR.**

So the honest read: this deepens roughly twenty thin western counties and adds
two new ones. It is a corroboration and enrichment source, not a gap-filler,
and it is worth having for exactly that — it is the only independent,
methodologically documented, county-coded opposition dataset found in this
scan.

Terms: page content is CC-BY-4.0; the CSV is available on request and its
terms depend on use, and the author invites research collaboration. The
correct move is to email for the CSV rather than scrape the page, and to cite
the BibTeX entry in the README.

## Finding 4: why the west has no interconnection-queue route

`docs/interconnection_queue_scoping.md` established that LBNL Queued Up is
generation-only and cannot serve as a data center universe. The western
picture is worse than that memo's national framing, and the reason should be
recorded: in the Intermountain West (PacifiCorp, Idaho Power, NV Energy, APS,
Public Service Co. of Colorado, NorthWestern) and the Northwest (BPA, Portland
General, Puget Sound Energy, Avista) **there is no regional load
interconnection queue at all.** These are non-ISO territories, so a large load
connects through the individual utility's state-approved retail tariff and
line-extension rules, increasingly via a purpose-built large-load tariff.

That relocates the western signal from a queue to **state PUC dockets** — a
utility filing a large-load tariff or a special contract names the customer
and the load. Oregon PUC eDockets, the Colorado PUC E-Filings system, the
Arizona Corporation Commission and the Washington UTC all expose public docket
search; none is a documented API, and Texas PUCT Interchange is the only one in
this scan with a described programmatic search. Medium effort, high
specificity, genuinely western. It also means the FERC show-cause timeline in
the interconnection memo — RTO compliance filings, which landed in late August
2026 — does **not** improve western disclosure, because these utilities are not
FERC-jurisdictional RTOs. Do not expect the west to be fixed by that docket.

### What the four commissions actually expose

Recorded 2026-09-09 while registering them. None of this is a substitute for
the probe reports — that is the point of the probes — but it is what a search
turns up without reaching any of the hosts, and it changes the ranking Finding
4 implies.

**Washington UTC is the outlier, in a good way.** A public citation exposes
`apiproxy.utc.wa.gov/cases/GetDocument?docID=&year=&docketNumber=` — a host
literally named apiproxy, taking a parameter triple. Every other commission in
this scan exposes a search form. Case pages also live at a predictable
`/casedocket/<year>/<number>`, which makes a docket number from any other
source directly resolvable even with no index at all.

**Colorado's E-Filings runs on the Oracle PL/SQL Web Toolkit** (`/pls/efi/`),
so its document and filing routes are plain GET parameters with no viewstate —
an unusually tractable shape for a filings system. It also publishes something
called an "Index of Major Docket Activity and Index of Dockets and Decisions
Tracking Data", which is probed first: a published index would beat querying
the system row by row.

**Oregon is mid-migration.** The commission has a published eDockets and
eDiscovery Replacement Project. That is worth knowing before writing an
adapter rather than after, so the replacement page is itself a probe.

**Arizona's eDocket looks like a single-page app** — `/search/<thing>/
item-detail/<numeric id>` — which usually means a JSON backend worth finding
rather than a page worth scraping. Its documented filter set (company name,
docket number, document code, decision number, filed date range) is close to
what a large-load tariff query needs.

### The pattern that makes these worth the effort

Counting tracked facilities against recorded opposition events by county, in
`data/county_aggregate.csv` as of 2026-09-09:

| County | Facilities | Recorded events |
| :-- | --: | --: |
| Grant, WA | 39 | 1 |
| Umatilla, OR | 31 | 1 |
| Morrow, OR | 28 | 1 |
| Douglas, WA | 13 | 1 |
| Maricopa, AZ | 63 | 14 |

The first four are the Northwest's largest data center clusters, and each
generates one recorded event. Maricopa, with a comparable facility count and
an order of magnitude more events, is what observation looks like when it is
working. **A county hosting thirty data centers and producing one recorded
event is far more likely to be under-observed than quiet** — and that is the
specific gap a tariff docket naming the customer and the load would close,
because in non-ISO territory it is filed regardless of whether any local
reporter covers it.

## Finding 5: national sources, ranked by ease

**OpenStreetMap via Overpass.** Free, no key, one HTTP POST, `telecom=data_center`.
This is the upstream of `atlas.csv` — the IM3 atlas is OSM-derived — which
means it directly unblocks the `needs_manual_pin` acquisition status recorded
for `osti_im3_atlas` in `configs/facility_sources.json`, whose stated blocker
is that osti.gov and msdlive.org are unreachable from CI. Overpass is
reachable, current, and diffable into `data/facility_candidates.csv` under the
existing rule that a re-pull never overwrites the snapshot. Layer A only —
this is facilities, not proposals. Coverage is strong in major markets and
thin for small or low-profile sites.

**EPA ECHO air facility REST service.** National, free, documented, filters on
NAICS; 518210 is the data-processing/hosting code. A data center with a Title V
or synthetic-minor permit for its generator bank appears here. Caveat that
belongs in the config note: many data centers are minor sources permitted by
delegated state programs, and ICIS-Air's coverage of those is uneven, so this
is a floor and not a census — the same caveat the county opposition metric
already carries.

**Hyperscaler first-party region and campus pages.** Already registered as
`hyperscaler_footprints` in `configs/facility_sources.json` and already
described there as the "highest-value unbuilt source." Repeating it here only
to note the western argument for it: the west is where the operator-named
campuses are (Prineville, Quincy, Umatilla, Eagle Mountain, Kuna, Cheyenne,
Los Lunas, Storey County), so first-party pages return more per page in the
west than anywhere else.

**Good Jobs First Subsidy Tracker.** 759,000 award entries, and the
organization has published dedicated data center research. The disqualifying
number for our purposes is theirs: at least 36 states have data-center-specific
subsidies and **only 11 disclose which companies receive them.** That makes it
a validation set for named projects in disclosing states, not a discovery
source. Free search, paid bulk download ($25/mo, 1,000 records per search).

**Data Center Watch (10a Labs).** The most-cited opposition tracker — 28
states in the original report, $64bn blocked or delayed, and a Q1 2026 update
reporting ~75 projects and ~$130bn with active opposition groups in 49 states
and group counts rising from 396 to 833. Published as PDF reports; no
project-level machine-readable release found. It is also, per NBC and CUFT
reporting, funded by an undisclosed party, which for a platform whose whole
claim is defensibility makes it a thing to cite carefully and never to ingest
as fact. Use as a recall benchmark: their state counts against ours.

**Commercial project registries** (usdatacenterprojects.com, Aterio, dcmap.us,
CleanView, ElectricChoice). Several claim national pipelines an order of
magnitude larger than ours — Aterio 7,848 facilities, dcmap.us 4,998 with
1,042 planned. All are lead-gen or subscription products with no free bulk
export found. Same posture as GridTracker in the interconnection memo: the
best available external validation set for our project list, and a
buy-versus-build question, not a scrape.

## Recommended order

| # | Step | Effort | Why first |
| :-- | :-- | :-- | :-- |
| 1 | Place gazetteer in `signal_harvest.locate()`, plus retire the 5-char rule for gazetteer-resolved places | 1 day | Fixes 164 of 227 unlocated candidates; the only item that improves every future harvest rather than adding one source |
| 2 | WA SEPA Register as a socrata config | Half day + one portal look | Statewide western coverage, zero new code, existing adapter |
| 3 | Overpass pull, diffed into `data/facility_candidates.csv` | Half day | Unblocks the atlas acquisition the manifest is already blocked on |
| 4 | Email `jwklee` for the tracker CSV; reconcile the 30 western entries | Half day | Independent cross-check; two new counties, twenty deepened |
| 5 | Granicus + PrimeGov probes, and city-level slugs, in `local_meeting_feed.py` | 2 days | 3 of 808 is not a working discovery step, west or east |
| 6 | CEQAnet pin, then Oregon PAPA adapter | 2–3 days | Largest western state, then the state with the cleanest land-use register |
| 7 | Western PUC docket scan, scoped to large-load tariff filings | Open-ended | Real but no API; do it after 1–6 have landed |

Nothing above changes the standing rule that `data/proposals.csv` is fed by
the CMS export and curated manual additions. A western source promotes into
the manual-addition id block (1001+) through the same gate, and
`project_resolution.assert_unique_project_ids()` still holds.
