# County FIPS resolution

Proposals examined: 336
Already resolved by the existing lookup: 326
Applied by this pass: 9
Held for confirmation: 0
Still unresolved: 1

Network steps were allowed.

## Method

| method | confidence | resolved |
| :-- | :-- | --: |
| `lookup_normalized` | high | 1 |
| `census_reverse` | high | 7 |
| `name_extract` | medium | 1 |

Retired codes caught: 5. A retired code is a FIPS the lookup still returns that no longer exists in the county universe, so it joins to nothing while looking valid. Connecticut is the live case: it replaced counties with planning regions in 2022.

## Applied

| project | state | recorded county | resolved | fips | method |
| :-- | :-- | :-- | :-- | :-- | :-- |
| prj_17 | Connecticut | New London County | Southeastern Connecticut Planning Region, Connecticut | 09180 | `census_reverse` |
| prj_18 | Connecticut | New Haven County | South Central Connecticut Planning Region, Connecticut | 09170 | `census_reverse` |
| prj_19 | Connecticut | Hartford County | Capitol Planning Region, Connecticut | 09110 | `census_reverse` |
| prj_20 | Connecticut | Fairfield County | Greater Bridgeport Planning Region, Connecticut | 09120 | `census_reverse` |
| prj_21 | Connecticut | Hartford County | Naugatuck Valley Planning Region, Connecticut | 09140 | `census_reverse` |
| prj_31 | Indiana | Marlon County | Marion County, Indiana | 18097 | `census_reverse` |
| prj_39 | Indiana | La Porte County | LaPorte County, Indiana | 18091 | `lookup_normalized` |
| prj_149 | North Carolina | (blank) | Catawba County, North Carolina | 37035 | `name_extract` |
| prj_158 | North Carolina | (blank) | Wake County, North Carolina | 37183 | `census_reverse` |

## Still unresolved

| project | state | recorded county | why |
| :-- | :-- | :-- | :-- |
| prj_324 | Pennsylvania | (blank) | no county, no usable coordinates, and no county named in the text |
