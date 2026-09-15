# External restriction census refresh

Upstream rows are filtered to county-level, data-center-sector rows before anything below is counted. The delta is keyed on the county, not the episode.

- Local seeded census rows: 94 across 17 states
- Upstream rows (county-level, data-center sector): 206 across 32 states
- Delta rows (counties absent from the local census): 118

## States upstream covers and the local census does not (15)

AR, CA, FL, LA, MI, MN, MT, NM, NV, OH, SC, SD, TX, UT, WA

## Status distribution in upstream

- active: 154
- expired: 6
- extended: 22
- pending: 6
- replaced: 14
- rescinded: 4

Promoting a delta row into data/external_restriction_census.csv remains a review decision. This module never writes the census.
