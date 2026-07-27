# County FIPS resolution

Proposals examined: 333
Already resolved by the existing lookup: 324
Applied by this pass: 2
Held for confirmation: 1
Still unresolved: 6

Network steps were skipped.

## Method

| method | confidence | resolved |
| :-- | :-- | --: |
| `lookup_normalized` | high | 1 |
| `name_extract` | medium | 1 |
| `lookup_nearmatch` | low | 1 (not applied) |

Retired codes caught: 5. A retired code is a FIPS the lookup still returns that no longer exists in the county universe, so it joins to nothing while looking valid. Connecticut is the live case: it replaced counties with planning regions in 2022.

## Applied

| project | state | recorded county | resolved | fips | method |
| :-- | :-- | :-- | :-- | :-- | :-- |
| prj_39 | Indiana | La Porte County | LaPorte County, Indiana | 18091 | `lookup_normalized` |
| prj_149 | North Carolina | (blank) | Catawba County, North Carolina | 37035 | `name_extract` |

## Held for confirmation

Close name matches only. Each needs a person to confirm before it is applied.

| project | state | recorded county | closest match | fips |
| :-- | :-- | :-- | :-- | :-- |
| prj_31 | Indiana | Marlon County | Marion County, Indiana | 18097 |

## Still unresolved

| project | state | recorded county | why |
| :-- | :-- | :-- | :-- |
| prj_17 | Connecticut | New London County | lookup returned 09011, which is not in the current county universe; treated as unresolved; coordinates are present; a reverse geocode resolves this row, so run without --offline |
| prj_18 | Connecticut | New Haven County | lookup returned 09009, which is not in the current county universe; treated as unresolved; coordinates are present; a reverse geocode resolves this row, so run without --offline |
| prj_19 | Connecticut | Hartford County | lookup returned 09003, which is not in the current county universe; treated as unresolved; coordinates are present; a reverse geocode resolves this row, so run without --offline |
| prj_20 | Connecticut | Fairfield County | lookup returned 09001, which is not in the current county universe; treated as unresolved; coordinates are present; a reverse geocode resolves this row, so run without --offline |
| prj_21 | Connecticut | Hartford County | lookup returned 09003, which is not in the current county universe; treated as unresolved; coordinates are present; a reverse geocode resolves this row, so run without --offline |
| prj_158 | North Carolina | (blank) | coordinates are present; a reverse geocode resolves this row, so run without --offline |
