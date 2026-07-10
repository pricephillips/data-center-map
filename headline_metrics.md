# Headline metrics (as of 2026-07-10)

Scope note: this dataset tracks opposition incidents, not all data center projects. Every rate below is conditional on a conflict being visible enough to enter the tracker; projects that proceeded without tracked opposition are absent by construction.

## Decided-case confirmed-block rate
Project level, jurisdiction-cluster bootstrap 95% CI, incidents younger than 90 days excluded (right-censoring guard).

- 2026 YTD: 45% of 148 decided (CI 36%-53%)
- 2025: 30% of 98 decided (CI 20%-39%)

## Political context
- Incident share in Trump-won counties: 66% (n=854)
- County base rate (share of counties Trump won): 85%
- Relative to the share of counties Trump won (85%), tracked opposition is UNDER-represented in Trump-won counties at 66%. Quote the pair, never the share alone; an exposure denominator (where projects are proposed) is the fair comparison and siting is not uniform.

## Contested investment (floors, not totals)
- $671B disclosed across 229 primary projects (review-flagged figures excluded)
- $93B behind enacted blocks

## Data caveats attached to every use
- 93 rows have no usable date and are absent from all temporal statistics; these skew toward the newest intake stream, so recent-period counts are floors.
- Severity values in use: ['1', '2'] - the 1-5 scale is effectively binary and should not be treated as a graded intensity measure.
- Mechanism/concern categories are keyword-classified; see validation_sample.csv workflow for measured precision before citing category-level rates externally.
