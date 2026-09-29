# Config Contract

## fetch_permits.py `tabular` adapter: `earliest_date_from` (generic, optional)

```json
"earliest_date_from": {
  "url": "https://.../timelines.csv",
  "key": "Data center",
  "on": "Name",
  "date_field": "Date",
  "as": "first_dated_observation"
}
```

The adapter fetches the second CSV and, for each main row, sets `as` to the
minimum ISO date (`YYYY-MM-DD` or `YYYY-MM`) among the rows whose `key`
equals the main row's `on`. When there is no dated row, the value is blank.
Absent the block, behavior is unchanged.

## permit_ingest.py (generic, optional keys)

```json
"state_from": {"column": "Address", "regex": ",\\s*([A-Z]{2})\\s+\\d{5}"},
"strip_regex": {"operator": "\\s*#\\w+"},
"match_projects": {"key_map": "data/project_key_map.csv",
                   "strong": 0.60, "soft": 0.34},
"attribution": "Epoch AI, Frontier Data Centers, https://epoch.ai/data/data-centers (CC-BY 4.0)",
"sampling_note": "..."
```

- `state_from` applies when neither `state` nor `columns.state` gives a
  value. The first capture group is used. No match means a reject reason of
  `missing state`.
- `strip_regex` removes pattern matches from the named output field.
- `match_projects` holds `confirmed` and `review` rows out of `--out`, and
  writes them to `data/baseline_external_matches_<config stem minus _ingest>.csv`.
- `attribution` and `sampling_note` are printed on every run. They are also
  the manifest's record of the terms of use, so both must stay in the
  config.

## configs/epoch_frontier_dc.json (new fetch registration)

A `tabular` adapter over `data_centers.csv`, with `contains:
["United States"]` and `earliest_date_from` over
`data_center_timelines.csv`. `fetch_permits.py --list-sources` picks it up,
because it has an `adapter` key.

## configs/facility_sources.json, entry `epoch_frontier`

- `license` becomes `CC-BY-4.0`.
- New `attribution` string.
- `landing_urls` gains the two CSV export URLs.
- `notes` records the very-large-campus sampling limit.
- `acquisition.status` records that the export URL is now pinned and that
  the dated rows flow through `fetch-permits.yml`. The `ai_centers.csv`
  snapshot is not overwritten.
