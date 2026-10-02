# Implementation Plan: Terrain Plates for Static Deliverables

**Branch**: `013-terrain-plates` | **Date**: 2026-10-01 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/013-terrain-plates/spec.md`; depends on spec 012 (registry entries, Pro-boundary audit, `OUTCOME_COLOR`).

## Summary

One script, `scripts/render_terrain_plate.py`, turns a config in
`configs/plates/` into a print-resolution PNG plus sidecar: 3DEP elevation
(windowed COG reads, cached), the county boundary painted into the terrain
albedo, case pins and labels projected through the same orthographic camera
forge3d renders with, open-core Legend, ScaleBar and NorthArrow, and Pillow
composition. Where it runs is settled by `plate-probe.yml` and the US4
decision rule, applied by `render_terrain_plate.py --probe-decide`. A probe
leg passed, so the CI route applies: `render-plates.yml` renders every config
as artifacts and never commits a PNG (FR-007). The local-render fallback
(`render_plates_local.sh`, `plate_freshness.py`, lock files, a pipeline.yml
freshness step) is specified in US4 but not built, because its precondition
(every leg fails) did not occur. Research: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: forge3d==1.40.1 (open core), numpy, Pillow, rasterio==1.4.4 (DEM fetch only); `requirements/plates.in`, not `ci.in` (FR-001)
**Storage**: `.cache/dem/<key>.npz` (gitignored); `outputs/plates/` (gitignored); configs in `configs/plates/`
**Testing**: `--selftest` with a mocked render (no GPU, no network): config validation, overlay filtering, vocabulary, composition, cache, decision rule
**Target Platform**: Price's Mac (Metal) and GitHub-hosted runners through a software wgpu adapter
**Performance Goals**: a county plate in under three minutes with a warm cache (SC-001 is for Price's Mac with Metal; on GitHub runners a full print render takes 57.6 min on route B, lavapipe, and 4.9 min on leg D, paravirtual Metal; research R4)
**Constraints**: no Pro APIs; inputs from committed files and the config only; LF; no em-dashes; title states the finding
**Scale/Scope**: one fixture plate (Spalding, FIPS 13255); configs added per engagement

## Constitution Check

| Principle | How this plan complies |
|---|---|
| I Defensibility | Unverified tiers drawn as recorded in lighter tints; relief exaggeration above 1.5x disclosed; "no tracked cases" stated, never an empty legend. |
| II Vocabulary | Pins fail on any value outside `OUTCOME_GRADES`; title, subtitle and labels fail on scorekeeping words or em-dashes. |
| VI Reproducibility | Sidecar records commit SHA, input hashes, versions, DEM product, URLs and access date. |
| VII Additive | New files only; `.gitignore` gains `outputs/`. |
| IX Selftested | `--selftest` (26 checks). |

## Project Structure

```text
specs/013-terrain-plates/{spec.md, plan.md, research.md, tasks.md}
scripts/render_terrain_plate.py
configs/plates/13255_spalding.json
requirements/plates.in
.github/workflows/plate-probe.yml
.github/workflows/render-plates.yml
specs/010-deliverable-generation/plan.md   (US3 slot contract note)
DATA_NOTICES.md                            (USGS 3DEP courtesy credit)
configs/integrations.json                  (forge3d, rasterio, forge3d-pro)
```

## Complexity Tracking

| Choice | Why |
|---|---|
| rasterio for the DEM | forge3d's `CogDataset` refuses 3DEP's NAD83 CRS without PROJ; the ImageServer host is not needed when the staged COGs serve range reads. |
| Pins drawn in screen space | Pins painted into the albedo would be shaded and shift color; projecting through the camera keeps palette colors exact. |
| Orthographic camera | Keeps the scale bar true left to right; perspective would make any scale bar wrong. |
