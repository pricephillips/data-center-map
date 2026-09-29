# Scraper field audit

Generated 2026-09-29 by `scripts/scrape-trackdatacenters-proposals.py`.

384 records; 53 distinct top-level keys in the response.

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


## Id stability

Stable: none of 384 shared ids changed project.
