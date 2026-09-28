# External restriction census refresh

Upstream rows are filtered to county-level, data-center-sector rows before anything below is counted. The delta is keyed on the county, not the episode.

- Local seeded census rows: 162 across 29 states
- Upstream rows (county-level, data-center sector): 383 across 34 states
- Delta rows (counties absent from the local census): 225

## States upstream covers and the local census does not (5)

AL, AR, AZ, MI, OR

## Status distribution in upstream

- active: 276
- expired: 7
- extended: 43
- pending: 26
- replaced: 26
- rescinded: 5

Promoting a delta row into data/external_restriction_census.csv remains a review decision. This module never writes the census.
