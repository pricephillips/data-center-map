# Interconnection Queue: Scoping Memo

Prepared 2026-07-27. Priority item 5 from the 2026-07-27 handoff list. This is
a scoping document, not a build. It exists to answer one question before any
code gets written: is the interconnection queue a viable independent baseline
universe of U.S. data center developments, and if so, at what cost and on what
timeline.

## Bottom line

Three recommendations, in order of confidence.

**1. Download the LBNL Queued Up 2026 data file now and use it for the cost
layer only.** It is free, project-level, published under a national-lab
imprint, and it is the defensible source for the timeline anchors the cost
translation layer is specified to use. This is a half day of work with no
recurring dependency. Do it.

**2. Do not build a baseline universe of data center projects out of the
generation interconnection queue.** The Pass 1 entry in `docs/tooling_scan.md`
described the queue as a genuine independent baseline universe of data center
developments. That premise does not survive contact with the source. The
detail is in the next section; the short version is that Queued Up is
generation, and a data center is load. Building a denominator out of it would
produce a universe of power plants, not a universe of data centers.

**3. Revisit the commercial option in Q4 2026, not now.** The public
large-load picture is in the middle of changing, and the specific changes have
dates attached. Buying a subscription in July to solve a transparency problem
that regulators may partly solve by December is poor timing. The gate
conditions are listed at the end.

## The category error, and why it matters

The Pass 1 scan treated interconnection queue data as a source of proposed data
center projects. Two different processes are being conflated.

**Generation interconnection** is the process a proposed power plant goes
through to connect to the transmission grid. It has been FERC-jurisdictional
for decades, the queues are public, and LBNL and Interconnection.fyi have
compiled them into the Queued Up dataset covering seven ISOs and RTOs plus
roughly 50 non-ISO balancing areas.

**Load interconnection** is the process a large electricity consumer goes
through. A data center is a load. Historically this has been a state and
utility matter rather than a FERC one, and the project-level lists are mostly
not public.

The LBNL dataset page states the exclusion directly: the data
"does not include load interconnection requests", nor
distribution-connected or behind-the-meter projects.

Source: https://eta.lbl.gov/publications/us-interconnection-queue-data-0

So the free, well-documented, nationally-scoped, citable dataset is the one
that structurally cannot contain the projects the platform tracks. That is the
single most important finding in this memo, and it inverts the original
scoping assumption.

There is a real relationship between the two processes, and it is worth being
precise about it. Data center demand shows up in the generation queue
*indirectly*, as gas and storage capacity proposed to serve it. Queued Up 2026
reports active gas capacity up 86 percent in 2025 while solar, storage, and
wind all declined, which is a demand signal about data centers without being a
list of data centers.

Source: https://emp.lbl.gov/queues

That distinction is exactly the kind of inferential join the standing rules
require confidence tiering for. A gas plant proposed near a tracked project is
corroborating context. It is not the project, and it cannot be counted as one.

## What each source actually is

**LBNL Queued Up 2026 Edition.** Free. Project-level Excel file with a
codebook and 36 summary tabs, reflecting queues through the end of 2025.
Generation only. Roughly 8,200 projects actively seeking interconnection at
year-end 2025, 1,312 GW of generation and 749 GW of storage, with total active
queue volume down 10 percent year over year. Note a versioning quirk: the
current PDF report on the LBNL page has lagged the data file, so cite the data
file vintage rather than the PDF vintage.

Sources: https://emp.lbl.gov/queues and
https://interconnectionfyi.substack.com/p/check-out-the-latest-lbnl-queued

**Interconnection.fyi free public view.** The research firm behind the LBNL
data. Free web view with daily updates, filterable by state and by request
type, and it does expose a Load type filter and a data-center-projects-by-state
cut, so load requests are tracked even though the LBNL extract excludes them.
It is a web view rather than a bulk feed.

Source: https://www.interconnection.fyi/

**GridTracker, the commercial tier.** The complete dataset with developer
names, related regulatory filings and press releases, daily updates, delivered
as monthly CSV, daily Snowflake share, or API, plus an MCP server. It also
advertises tracking data center builds from permits and official records
rather than press releases.

Source: https://www.interconnection.fyi/purchase-data

That last capability deserves a flag rather than a shrug: it overlaps
materially with what this platform's own tracker does. Two honest readings
follow, and they are not mutually exclusive. It is a buy-versus-build question
on the coverage layer. It is also the best available external validation set
for the platform's own project list, which is worth something independent of
whether we ever license it as a feed.

**ERCOT.** Aggregate large-load queue totals are public through monthly
reports and board materials, and they are large: the queue passed 410 GW by
early 2026, up from roughly 226 GW in November 2025 and 63 GW at the end of
2024, with data centers reported as the majority of it. Project-level detail
with names and locations is generally not public.

Sources: https://www.rtoinsider.com/129421-ercot-large-load-requests-soar-again/
and https://www.latitudemedia.com/news/ercots-large-load-queue-has-nearly-quadrupled-in-a-single-year/

ERCOT itself has said the process was designed for a few dozen loads and has
been outgrown, and that a large share of the requests may be speculative. That
is a data-quality warning from the source operator, and it goes directly to
the denominator problem below.

Source: https://www.utilitydive.com/news/ercots-large-load-queue-jumped-almost-300-last-year-official/808820/

## Why the timing recommendation has dates attached

Two regulatory processes are actively changing what large-load data is public,
and both have deadlines inside the next six months.

**FERC.** On 2026-06-18 FERC issued show cause orders under Federal Power Act
section 206 to all six of its jurisdictional RTOs and ISOs (PJM, MISO, SPP,
CAISO, ISO-NE, NYISO), preliminarily finding their tariffs may be inadequate
for large loads and co-located loads including data centers, and directing
tariff filings within 60 days. That puts compliance filings in the second half
of August 2026. FERC chose targeted regional orders over the broad national
rulemaking in Docket RM26-4, which had accumulated comments from roughly 175
parties following the Department of Energy's October 2025 direction.

Sources:
https://natlawreview.com/article/ferc-moves-speed-interconnection-data-centers-and-other-large-loads,
https://www.whitecase.com/insight-alert/ferc-orders-grid-operators-promptly-revise-or-justify-interconnection-rules-data,
https://www.ferc.gov/rm26-4

**Texas.** SB 6 directed the PUCT to create transparency requirements for
large-load customers, explicitly including whether a customer has multiple
similar interconnection requests under review in the state, with the
rulemaking due to conclude by December 2026 (PUC Project 58481, large load
interconnection standards).

Sources: https://www.latitudemedia.com/news/ercots-large-load-queue-has-nearly-quadrupled-in-a-single-year/
and https://www.ercot.com/files/docs/2026/04/01/ERCOT_LargeLoad_Update_April2026_B-C_-Hearing.pdf

The practical read: new large-load interconnection procedures, and plausibly
new public queue reporting, are arriving between August and December 2026. Any
data model built against the current patchwork would need rework. Waiting one
quarter is cheap; rebuilding is not.

There is a second-order point worth noting for the platform's own analytical
purposes, separate from the data question. Faster interconnection changes the
denominator of the cost-of-delay figure. If FERC's orders compress
interconnection timelines materially, then the share of total project delay
attributable to community opposition rises even if opposition itself does not
change. The cost layer should not hardcode a fixed interconnection-delay
assumption from a 2025 baseline.

## The denominator problem

Even granting a hypothetical clean project-level large-load feed, three
structural issues stand between it and a usable baseline universe. These are
not reasons to abandon the idea. They are the specification any build would
have to satisfy.

**One project is not one request.** A campus can file multiple requests across
phases, and a developer shopping sites can file the same project in several
jurisdictions. ERCOT and the Texas legislature both treat duplicate requests
as a known problem serious enough to legislate about. Counting requests as
projects would inflate the universe, and it would inflate it
*non-randomly*, because the developers most likely to multi-file are the large
sophisticated ones whose projects also draw the most opposition. That is
differential measurement error pointing in the worst possible direction for the
verified-negative audit.

**A request is not a commitment.** Speculative or phantom load is the
operator's own characterization. A universe that includes projects which were
never real produces a block rate biased toward zero, because a project that
never existed cannot be opposed and will be coded as an unopposed control.

**Geography is coarse and often withheld.** The county-level join the platform
runs on requires county or coordinates. Load requests are frequently reported
at utility or zone level, which does not resolve to a county without
inference. `census_geocode.py` handles address-to-FIPS when an address exists;
it cannot manufacture one.

Taken together: a queue-derived universe would need entity resolution against
the existing project list before it could be used as a denominator, and the
Splink spike from earlier in this same work cycle came back NO-GO on scored
entity resolution for exactly the multi-candidate, same-developer,
same-region cases this would generate at volume. That is a real dependency,
not a hypothetical one.

## Recommended sequence

| # | Step | Cost | Gate |
| :-- | :-- | :-- | :-- |
| 1 | Download the Queued Up 2026 data file; extract median request-to-operation timelines and completion rates by region and vintage into `data/cost_anchors.csv` with LBNL cited per row | Half day | None, do it now |
| 2 | Hand-sample 20 to 30 tracked projects against the Interconnection.fyi free view; record whether a plausible load request or a serving-generation request is findable, and at what geographic precision | Half day | Measures whether a join is even possible before anyone pays for data |
| 3 | Read the August RTO compliance filings for what large-load queue reporting each RTO proposes to make public | Half day, August | FERC 60-day deadline lands in late August |
| 4 | Reassess the commercial tier with step 2's hit rate and step 3's disclosure picture in hand | Q4 2026 | Gate conditions below |

Step 2 is the one that would change my mind fastest in either direction, and
it costs nothing but time. It is also the honest prerequisite for step 4: no
subscription should be bought on the strength of a product page.

## Gate conditions for the commercial tier

Justify a paid subscription only if all three hold:

1. Step 2 finds a locatable queue record for a clear majority of sampled
   tracked projects, at county precision or better.
2. The verified-negative audit is actually underway and blocked on universe
   size rather than on coding capacity. The audit is currently the stated
   highest-value data investment at roughly 150 to 200 sampled projects; if it
   has not started, more universe does not help.
3. Either an external client engagement pays for it, or the coverage overlap
   with GridTracker's own permit-based data center tracking is confirmed to be
   additive to the platform's list rather than duplicative of it.

If only the first two hold, request a one-time historical export rather than a
recurring subscription. A one-time export supports an audit sample; only a
model in production needs a live feed.

## What would change the recommendation

- An RTO compliance filing in August that commits to a public, project-level
  large-load queue with county geography. That would make step 4 a build
  rather than a purchase.
- The PUCT rulemaking landing with public duplicate-request disclosure, which
  would solve the multi-filing inflation problem for the largest state in the
  dataset at no cost.
- A client engagement whose deliverable requires forward-looking load pipeline
  data, which changes the cost calculus without changing any of the analysis
  above.
- Evidence from step 2 that serving-generation requests cluster tightly enough
  around tracked projects to work as a proxy signal. That would be a feature
  for the county model rather than a baseline universe, which is a smaller
  claim and a more defensible one.

## Open questions

- Does the Queued Up Excel file carry county or coordinates per project, or
  only balancing area and state? This determines whether step 1 can also feed
  the county layer or is confined to the cost layer. Resolve on download.
- Is there a public co-located-load list arising from the December 2025 PJM
  co-location order? Co-located data centers are a distinct and possibly more
  visible subset.
- What does GridTracker's permit-based data center tracking cover that
  `trackdatacenters` and the platform's own additions do not? Answerable from
  the free view without a purchase, and worth knowing regardless of the
  licensing decision.

## Standing rules observed

Every external claim above carries a source URL. No scorekeeping vocabulary.
No em-dashes. Nothing in this memo is entered into any dataset; the
`cost_anchors.csv` work in step 1 is a separate, sourced pass.
