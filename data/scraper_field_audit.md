# Scraper field audit

Generated 2026-10-01 by `scripts/scrape-trackdatacenters-proposals.py`.

452 records; 60 distinct top-level keys in the response.

## Retired, and expected to be absent

Declared in `RETIRED_FIELDS`: the source stopped sending these and the reason is recorded in the scraper. They are listed apart from the section below so a new break is never read as one of these.

- `approx`
- `locationTbd`

## Read by the mapping, absent from the response

These are the fields that will arrive empty. A field here was dropped or renamed by the source; it is not a data gap.

- `bringingOwnEnergy`
- `moratoriumExempt`
- `status`
- `yearOpened`

## Sent by the response, read by nothing

Candidate landing places for anything in the list above, with up to 4 distinct observed values each. Strings longer than 40 characters are described by length rather than quoted.

- `alternateNames` -- <list n=0>, <list n=1>
- `jobsConstructionQualifier` -- "minimum"
- `jobsLongTermMax` -- 30, 70
- `numberOfBuildingsMax` -- 24
- `numberOfBuildingsQualifier` -- "minimum"
- `projectCostMax` -- 10000000000
- `sizeAcresQualifier` -- "minimum"


## Id stability

Stable: none of 384 shared ids changed project.
