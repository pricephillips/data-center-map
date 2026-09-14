# Scraper field audit

Generated 2026-09-14 by `scripts/scrape-trackdatacenters-proposals.py`.

327 records; 51 distinct top-level keys in the response.

## Read by the mapping, absent from the response

These are the fields that will arrive empty. A field here was dropped or renamed by the source; it is not a data gap.

- `bringingOwnEnergy`
- `capacity_mw`
- `date`
- `lastUpdated`
- `moratoriumExempt`
- `size_acres`
- `yearOpened`

## Sent by the response, read by nothing

Candidate landing places for anything in the list above.

- `additionalLocations`
- `btmPower`
- `capacityMaxMw`
- `capacityMw`
- `coolingSource`
- `coolingType`
- `dateAnnounced`
- `dateCreated`
- `dateOnline`
- `dateUpdated`
- `dedicatedSubstation`
- `facilitySizeSqft`
- `geojson`
- `informationSource`
- `isExisting`
- `kind`
- `locationConfidence`
- `media`
- `nda`
- `niche`
- `notes`
- `numberOfBuildings`
- `numberOfGenerators`
- `powerSource`
- `projectCost`
- `sizeAcres`
- `sources`
- `stakeholders`
- `zip`

## Suggested pairings

By name similarity only. A prompt to go and check the response, never a mapping to apply unread: two fields can have similar names and different meanings.

| absent | candidates |
|---|---|
| `capacity_mw` | `capacityMw`, `capacityMaxMw` |
| `lastUpdated` | `dateUpdated` |
| `size_acres` | `sizeAcres` |

