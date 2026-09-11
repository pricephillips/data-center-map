# Stakeholder registry QC report

Generated 2026-09-11.

- Published rows: **128** (103 county, 25 state)
- Published with a warning flag: **1**
- Withheld rows: **7**
- Counties covered: **36**
- States covered: **18**

A withheld row is one whose identity could not be trusted: no name,
no office, an unresolvable county, a source that is not a URL, a
duplicate office, or a term that has already ended. A warned row is
published with the unusable contact or relevance field blanked.

## Flag counts

| flag | rows |
| --- | --- |
| name_missing | 5 |
| office_missing | 4 |
| duplicate_office | 2 |
| phone_unparseable | 1 |

## Withheld rows

| level | state | fips | office_class | name | flags |
| --- | --- | --- | --- | --- | --- |
| county | IA | 19155 | county_admin | (blank) | name_missing;office_missing |
| county | OR | 41059 | county_admin | (blank) | name_missing;office_missing |
| county | TX | 48139 | county_admin | (blank) | name_missing;office_missing |
| county | TX | 48453 | county_admin | (blank) | name_missing |
| county | WY | 56021 | county_admin | (blank) | name_missing;office_missing |
| state | GA |  | bill_sponsor | Jason Anavitarte | duplicate_office |
| state | NJ |  | bill_sponsor | Joe Danielsen | duplicate_office |

## Acquisition notes

- CA: no governor returned by OpenStates
- KS: executive lookup failed (429: {"detail":"exceeded limit of 10/min: 13"})
- KS: no governor returned by OpenStates
- KS: SB 98 lists no sponsors
- NJ: no governor returned by OpenStates
- NV: no matched data center bill to draw sponsors from
- OR: executive lookup failed (429: {"detail":"exceeded limit of 10/min: 13"})
- OR: no governor returned by OpenStates
- OR: sponsor lookup for HB 4084 failed (429: {"detail":"exceeded limit of 10/min: 16"})
- PA: no matched data center bill to draw sponsors from
- TX: no matched data center bill to draw sponsors from
- VA: no governor returned by OpenStates
- WY: no matched data center bill to draw sponsors from
