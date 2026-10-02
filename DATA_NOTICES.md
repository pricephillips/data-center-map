# Third-party data notices

This repository republishes or derives from the third-party datasets below.
Only notices a provider's terms or licence require are shown in the app:

- `county-profile.html`: the (i) button (via `data-notices.js`) carries the
  Census Bureau API and NASS API notices for the whole application, plus the
  PUDL, IM3 Atlas, OpenStreetMap, Epoch AI and Moratorium Nation attributions.
- `index.html`, `developments-dashboard.html`: IM3 Atlas / OpenStreetMap (ODbL)
  and Epoch AI (CC-BY) credit lines on the page.
- Every map with Esri tiles: "Powered by Esri" and the service credit in the
  map's attribution control (`basemap.js`).

Courtesy credits (Drought Monitor, USGS, Open States, GDELT, tonmcg) are
recorded here only, except that terrain plates (spec 013) print the USGS 3DEP
credit in their footer and GeoLibre projects (spec 012) carry their credits in
the project description.

| File(s) | Source | Licence | Obligation |
|---|---|---|---|
| `atlas.csv`, `data/facility_registry.csv` (atlas-derived rows) | IM3 Open Source Data Center Atlas, PNNL (derived from OpenStreetMap) | ODbL 1.0 | Attribution. Share-alike: these files and any database derived from them are offered under ODbL 1.0 (https://opendatacommons.org/licenses/odbl/1-0/). |
| `data/facility_candidates_osm*.csv` | OpenStreetMap contributors | ODbL 1.0 | Same as above. |
| `ai_centers.csv` | Epoch AI, 'AI data centers' (https://epoch.ai/data/data-centers-documentation) | CC-BY | Credit Epoch AI as the source. |
| `data/county_votes.json`, margin columns in `data/county_aggregate.csv`, `data/features/political*.csv` | MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024 (Harvard Dataverse, doi:10.7910/DVN/VOQCHQ) once the parity gate in `configs/feature_sources.json` passes; until then, and permanently in `data/county_votes_legacy.json`, tonmcg, US_County_Level_Election_Results_08-24 (from Townhall.com 2016, Fox News 2024 results) | MEDSL: license as recorded from Dataverse in `data/features/features_manifest.json` (gate accepts CC0 or CC BY only). tonmcg: MIT | Cite MEDSL and the DOI. For the legacy file keep the MIT notice below; its compiler notes it is not an authoritative source. `data/features/features_manifest.json` (`sources.political.info.promotion`) records whether the switch has happened. |
| `data/county_census_features.csv` and ACS columns | U.S. Census Bureau Data API | Public domain; API terms | "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau." |
| `data/features/grid_generation.csv`, `data/features/retail_price.csv` | PUDL, Catalyst Cooperative (EIA-860, EIA-861) | CC-BY-4.0 | Credit Catalyst Cooperative PUDL and the licence. No endorsement implied. |
| `data/features/farmland.csv` | USDA NASS Quick Stats API | Public domain; API terms | "This product uses the NASS API but is not endorsed or certified by NASS." |
| `data/features/drought.csv` | U.S. Drought Monitor (NDMC, USDA, NOAA) | Public | Citation requested: NDMC, USDA and NOAA. |
| `data/features/water_use.csv` | USGS county water use, 2015 | Public domain | Credit requested. |
| `data/external_restriction_census*.csv` (Moratorium Nation rows) | Bommarito, Michael J. (2026), Moratorium Nation: U.S. Infrastructure Moratorium Data (https://github.com/mjbommar/moratorium-data-2026) | CC-BY-4.0 | Credit the author, dataset and licence. |
| `data/bill_*` | Open States (Plural) | Public domain dedication | Attribution appreciated, not required. |
| `outputs/plates/*.png` (terrain plates, not committed; spec 013) | U.S. Geological Survey, 3D Elevation Program (3DEP) 1/3 arc-second and 1 arc-second DEMs, read from the 3DEP staged products (https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/) | Public domain | Courtesy credit, printed in every plate footer with the product and access date: "Elevation: U.S. Geological Survey, 3D Elevation Program." No endorsement implied. |
| `data/geolibre/*.geolibre.json` (spec 012) | Derived from the files above: Census 2024 county boundaries, `data/county_policy_scores.csv`, `master_opposition_clean.csv`, and for the swipe project the presidential margin in `data/county_votes.json` | As for each source | Each project's description carries its credits; the margin credit follows the MEDSL / tonmcg rule in the row above. |

## MIT notice for US_County_Level_Election_Results_08-24

Copyright (c) 2025 Tony McGovern

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Proposals intelligence sources (2026-09-28)

- EPA ECHO air facility data (`data/proposal_candidates_airpermits.csv`): U.S. government work, public domain.
- EIA Form 860M (`data/grid_planned_generation.csv`): U.S. government work, public domain.
- EIA Form 861 via PUDL (`data/county_grid_territory.csv`): Catalyst Cooperative, CC-BY-4.0. Attribution is carried in `data/county_grid_territory_manifest.json`.
- News discovery (`data/proposal_candidates_news.csv`) stores headlines, URLs and extracted numbers only, never article text.

