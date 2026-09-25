# Scraper field audit

Generated 2026-09-25 by `scripts/scrape-trackdatacenters-proposals.py`.

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

## Sent by the response, read by nothing

Candidate landing places for anything in the list above, with up to 4 distinct observed values each. Strings longer than 40 characters are described by length rather than quoted.

- `additionalSites` -- <list n=0>, <list n=1>, <list n=2>
- `approvals` -- <list n=0>, <list n=1>
- `btmPower` -- "unknown", "true", "false"
- `btmPowerName` -- <list n=0>, <list n=1>
- `capacityMaxMw` -- 200, 20, 1000, 4
- `coolingSource` -- "water", "air", "hybrid"
- `coolingType` -- "closed"
- `dateCreated` -- "2026-9-16", "2026-9-17", "2026-9-18", "2026-9-10"
- `dateOnline` -- "2032", "2026", "2027", "2028"
- `dateOnlineMax` -- "2028"
- `dedicatedSubstation` -- "unknown", "true", "false"
- `facilitySizeSqft` -- 4500000, 263000, 300000, 1000000
- `geojson` -- <list n=1>, <list n=0>, <list n=2>, <list n=4>
- `informationSource` -- <list n=0>, <list n=1>, <list n=2>
- `isExisting` -- True
- `kind` -- "proposal"
- `media` -- <list n=0>, <list n=1>
- `nda` -- <str len=84>, "Childersburg Mayor Ken Vesson", "Company asked town to sign NDA", "true"
- `niche` -- <list n=1>, <list n=0>, <list n=3>, <list n=2>
- `notes` -- <list n=1>, <list n=0>, <list n=2>, <list n=4>
- `numberOfBuildings` -- 18, 1, 2, 4
- `numberOfGenerators` -- 4, 516, 41, 588
- `powerSource` -- <list n=1>, <list n=0>, <list n=2>, <list n=3>
- `projectCost` -- 90000000, 14500000000, 1500000000, 6000000000
- `slug` -- "project-nebius", "project-marvel", "meta-platforms-montgomery", "dc-blox-montgomery"
- `sources` -- <list n=4>, <list n=6>, <list n=5>, <list n=3>
- `stakeholders` -- <dict keys=D>, <dict keys=A, C, D>, <dict keys=D, U>, <dict keys=O>
- `stateSlug` -- "alabama", "alaska", "arkansas", "connecticut"
- `zip` -- "35221", "35022", "35233", "35044"

## Suggested pairings

Paired by name similarity; the values are there so the pairing can be judged rather than guessed. Two fields can have similar names and different meanings, and the values are usually what shows it.

| absent | candidate | values seen |
|---|---|---|
| `status` | `stateSlug` | "alabama", "alaska", "arkansas", "connecticut" |

