# County geometry

`counties_2024.topojson` is the county geometry the choropleth pages
(`restriction-model.html`, `opposition-map.html`) draw on. One TopoJSON
object, `counties`; each geometry's `id` is the 5-digit county FIPS and
`properties.name` is the Census name. `counties_2024_manifest.json` records
where the committed file came from, its SHA-256, size, feature count and
coverage of `data/county_policy_scores.csv`.

Pages load it through the usual chain, `raw.githubusercontent.com` first and
the same-origin copy second, and decode it with topojson-client.

## Writer

The `boundaries` job in `.github/workflows/acquire-geo-sources.yml` is the
only writer. It downloads the Census cartographic boundary file
`cb_2024_us_county_5m.zip`, simplifies it with mapshaper, and runs
`node tests/ui/check_geometry.js --manifest ...`, which fails the job,
before anything is committed, if any scored FIPS lacks a polygon or the file
exceeds 1 MB.

## History

The first committed file (2026-09-29, from the sandbox that could not reach
the Census host) was a seed built from the Census 2023 1:10m cartographic
boundary, with Falls Church city, VA (51610) taken from the Census 2016
1:500k file because it collapses at 1:10m. The `boundaries` job replaced it
the same day with the Census 2024 1:5m build (710 KB, 3,222 features, all
3,144 scored FIPS drawable; see the manifest). Every scored county has its
own polygon, including the nine Connecticut planning regions (09110 to
09190), Chugach (02063), Copper River (02066), Kusilvak (02158) and Oglala
Lakota (46102).
