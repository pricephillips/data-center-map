# Pending: declare the county geometry in the layer registry (spec 009, FR-001)

`docs/pending_map_geometry.patch` declares `data/geo/counties_2024.topojson`
and `data/geo/counties_2024_manifest.json` as Layer D reference geography,
with the `boundaries` job in `.github/workflows/acquire-geo-sources.yml` as
their only writer. It edits two files:

- `configs/layers.json`: adds both paths to Layer D's `files`, next to the
  place gazetteer (the other Census reference file an acquisition job writes).
- `ARCHITECTURE.md`: names the files in the Layer D table and adds the job to
  that table's writers.

## Why it is a patch

Session 6 (spec 009) ran in parallel with the spec 005 session, which owns
changes to the shared registry and the architecture document. Session 6 was
limited to the HTML pages, `basemap.js`, `legend-filter.js`,
`map-permalink.js`, `data/geo/`, `tests/ui/` and `acquire-geo-sources.yml`.
The workflow change itself did push, so only this declaration is pending.

## Nothing fails without it

`layer_audit.py` inventories top-level `data/*` files and Python writers.
`data/geo/` is a subdirectory, and its writer is a mapshaper step in a
workflow, not a Python module. So the audit reports 0 undeclared findings
today, with or without the patch. The declaration is for Principle VIII: one
writer per file, stated where a reader looks for it.

## Apply

```bash
git apply docs/pending_map_geometry.patch
python layer_audit.py --strict --no-write   # still 0 undeclared
git rm docs/pending_map_geometry.patch docs/pending_map_geometry.md
```

The patch was checked with `git apply --check` against `main` on 2026-09-29.
If spec 005 has since moved the Layer D lines, apply it by hand: it adds two
entries to a list and one clause to each of two table cells.
