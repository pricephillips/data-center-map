# Scraper field audit

Generated 2026-09-17 by `scripts/scrape-trackdatacenters-proposals.py`.

341 records; 49 distinct top-level keys in the response.

## Read by the mapping, absent from the response

These are the fields that will arrive empty. A field here was dropped or renamed by the source; it is not a data gap.

- `approx`
- `bringingOwnEnergy`
- `locationTbd`
- `moratoriumExempt`
- `status`
- `yearOpened`

## Sent by the response, read by nothing

Candidate landing places for anything in the list above.

- `additionalLocations`
- `btmPower`
- `btmPowerName`
- `capacityMaxMw`
- `coolingSource`
- `coolingType`
- `dateCreated`
- `dateOnline`
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
- `sources`
- `stakeholders`
- `zip`

## Suggested pairings

By name similarity only. A prompt to go and check the response, never a mapping to apply unread: two fields can have similar names and different meanings.

| absent | candidates |
|---|---|
| `locationTbd` | `locationConfidence` |

