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

## The committed seed

The Census host is not reachable from the sandbox the file was first built
in, so the file committed on 2026-09-29 is a seed, and its manifest says so
(`vintage: 2023`, `scale: 1:10m`). It was built from:

- the Census 2023 cartographic boundary counties at 1:10m, as redistributed
  by the npm package `@severo_bo/us-atlas-2023@4.0.0` (ISC), `counties`
  object only, American Samoa, Guam, the Northern Mariana Islands and the
  Virgin Islands removed (Puerto Rico kept);
- one replacement polygon: Falls Church city, VA (51610) collapses to a
  zero-area sliver at 1:10m, so its geometry is the Census 2016 1:500k
  polygon (`cb_2016_us_county_500k`, from the PyPI package `plotly-geo`).
  Its boundary has not changed since the 2014 adjustment with Fairfax
  County, so the 2016 edition is the current boundary.

It was re-encoded with `mapshaper -o format=topojson id-field=FIPS
quantization=100000`. Every one of the 3,144 scored FIPS has a polygon,
including the nine Connecticut planning regions (09110 to 09190), Chugach
(02063), Copper River (02066), Kusilvak (02158) and Oglala Lakota (46102).

The county set did not change between the 2023 and 2024 editions for any
scored FIPS. The first run of the `boundaries` job replaces the seed in
place with the 2024 1:5m build; no page change is needed.
