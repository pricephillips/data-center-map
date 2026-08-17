# Headline metrics (as of 2026-08-17)

Scope note: this dataset tracks opposition incidents, not all data center projects. Every rate below is conditional on a conflict being visible enough to enter the tracker; projects that proceeded without tracked opposition are absent by construction.

## Decided-case confirmed-block rate, incident records
Unit is the primary incident RECORD in the clean feed, not the project entity. Duplicate rows for one incident are collapsed; several incidents attached to the same project are not. Jurisdiction-cluster bootstrap 95% CI, incidents younger than 90 days excluded (right-censoring guard).

- 2026 YTD: 51% of 207 decided records (CI 44%-58%)
- 2025: 29% of 95 decided records (CI 20%-39%)

## Decided-case confirmed-block rate, project entities
Unit is the resolved project in data/project_lifecycles.csv, all periods pooled. Quote this figure whenever the claim is about projects; quote the record figure above only when the claim is explicitly about tracked opposition events.

- 24% of 148 decided projects (36 blocked_confirmed) out of 335 tracked

The two rates differ because the populations differ: one project can carry several decided records, and many decided records are not yet linked to a project entity. Neither number is wrong; quoting either without its unit is.

## Political context
- Incident share in Trump-won counties: 66% (n=910)
- County base rate (share of counties Trump won): 85%
- Relative to the share of counties Trump won (85%), tracked opposition is UNDER-represented in Trump-won counties at 66%. Quote the pair, never the share alone; an exposure denominator (where projects are proposed) is the fair comparison and siting is not uniform.

## Contested investment (floors, not totals)
- $673B disclosed across 231 primary projects (review-flagged figures excluded)
- $86B behind enacted blocks

## Data caveats attached to every use
- 0 rows have no usable date and are absent from all temporal statistics; these skew toward the newest intake stream, so recent-period counts are floors.
- Severity values in use: ['1', '2'] - the 1-5 scale is effectively binary and should not be treated as a graded intensity measure.
- Mechanism/concern categories are keyword-classified; see validation_sample.csv workflow for measured precision before citing category-level rates externally.
