# Feature Specification: Terrain Plates for Static Deliverables

**Feature Branch**: `013-terrain-plates`

**Created**: 2026-10-01

**Status**: Draft

**Input**: Review of forge3d (milos-agathon/forge3d, PyPI `forge3d` 1.40.1, Python 3.10+, open core `Apache-2.0 OR MIT`). forge3d renders path-traced terrain from real elevation data at print resolution, with raster and vector overlays, labels, `Legend`, `ScaleBar` and `NorthArrow` in the open core. `MapPlate` composition, SVG/PDF vector export and building import are Pro features behind a commercial license key. This spec adds terrain hero images to the static report line (spec 010) using the open core only.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A plate renders from a config (Priority: P1)

New `scripts/render_terrain_plate.py --config configs/plates/<id>.json` renders one PNG. The config names an area (FIPS, list of FIPS, or bbox), camera (azimuth, elevation, z-scale), sun angle, output size, title, and which overlays to draw. Engagements add a config file, never code.

Pipeline per plate:

1. Elevation: USGS 3DEP (public domain) fetched for the area and cached under a gitignored `.cache/dem/`. Resolution chosen from the area size so a county renders at print quality without huge downloads. Record the 3DEP product and access date.
2. Overlays from committed files only: county boundary from `data/geo/counties_2024.topojson`; project pins from `master_opposition_clean.csv` colored by `outcome_defensible` with platform terms only; optional site marker from the config (client site coordinates enter only through the config).
3. Cartography from the open core: labels with halo, `Legend`, `ScaleBar`, `NorthArrow`.
4. Final composition (title, subtitle, source and vintage footer with commit SHA, margins) with Pillow, not `MapPlate`.

Outputs go to `outputs/plates/<id>.png` (gitignored; deliverable artifacts, not data) plus `<id>.json` recording inputs, versions, commit SHA, and credits.

**Why this priority**: Location reports and county profiles have no geographic hero image today. A terrain plate shows the site, the county line and nearby cases in one figure.

**Independent Test**: Render the fixture config for FIPS 13255 (Spalding GA) and confirm the PNG exists at the requested size, the sidecar JSON lists every input, and the footer carries the SHA.

**Acceptance Scenarios**:

1. **Given** a config with no title, **When** rendered, **Then** the script fails; every plate states its finding in the title (chart standard, spec 010 US5).
2. **Given** an outcome value outside the platform vocabulary, **When** rendered, **Then** the script fails.
3. **Given** the DEM cache is warm, **When** rendered again, **Then** no network request is made.

### User Story 2 - The Pro boundary is enforced (Priority: P1)

The script never calls `set_license_key`, `MapPlate`, vector export, or building import. `integration_audit.py` (spec 012 US1) fails the build if any of these appear in repo code. If a future deliverable truly needs vector export, that is a registry decision with a license purchase, not a code change.

**Independent Test**: Plant `forge3d.set_license_key("x")` in a fixture and confirm the audit fails.

### User Story 3 - Plates slot into the spec 010 renderers (Priority: P2)

`scripts/render_location_report.py` and `scripts/render_county_pdf.py` (spec 010) accept an optional `--plate outputs/plates/<id>.png` and place it in a fixed image slot with its caption and credit. If spec 010 is not yet built, this story adds the slot contract to `specs/010-deliverable-generation/` as a note in `plan.md` instead of editing `spec.md`.

**Independent Test**: Render the Spalding location report with and without a plate; both pass `validate.py --original`.

### User Story 4 - Where it runs is settled by a probe, with a defined fallback (Priority: P2)

forge3d renders through wgpu. GitHub-hosted runners have no GPU, but wgpu can use a software adapter on each OS. A `workflow_dispatch`-only workflow `.github/workflows/plate-probe.yml` runs one matrix job per route, each rendering the Spalding fixture at 512x512 with a 10-minute timeout and uploading the PNG and timing as an artifact:

| Leg | Runner | wgpu backend | Software adapter |
|---|---|---|---|
| A | `windows-latest` | DX12 | WARP (built into Windows) |
| B | `ubuntu-latest` | Vulkan | Mesa lavapipe (`mesa-vulkan-drivers`) |
| C | `ubuntu-latest` | GL | Mesa llvmpipe via EGL |
| D | `macos-14` | Metal | runner's virtual GPU, if exposed |

Each leg forces the software path where one exists (`WGPU_BACKEND`, and forge3d's adapter or fallback-adapter option if it exposes one; read the installed source to confirm the option names). Each leg logs the adapter name it actually got.

Decision rule, applied automatically and recorded in `research.md`:

1. The first leg that renders a non-blank image (pixel variance above a threshold) and finishes inside its timeout becomes the CI route. Prefer A, then B, C, D. Then measure a full print render on that leg; if it finishes inside 60 minutes, add `render-plates.yml` (`workflow_dispatch` only) that renders every config in `configs/plates/` and uploads the PNGs and sidecars as artifacts. It never commits plates. If only previews fit, the workflow renders at reduced samples and labels the output `preview` in the sidecar and filename.
2. If every leg fails, plates render locally on Price's Mac (Metal) through `scripts/render_plates_local.sh`, which renders all configs and writes the sidecars. CI then checks freshness instead of rendering: `scripts/plate_freshness.py` hashes each config's inputs (config file, clean CSV rows inside the area, scores file, geometry file, palette) and compares them with the hashes stored in the committed sidecar `configs/plates/<id>.lock.json`. A stale plate fails `pipeline.yml`'s plate step with a message naming the plate and telling Price to run the local script. Only the small lock files are committed; the PNGs are not.
3. A self-hosted runner on Price's Mac is NOT set up automatically. The repo is public, and GitHub warns that self-hosted runners on public repos can run untrusted code. Record it in `research.md` as an option for Price to decide on, with the required safeguards if he approves later: `workflow_dispatch` only, no `pull_request` or `pull_request_target` triggers on any workflow that targets it, runner label `plates-mac`, ephemeral mode, and a dedicated macOS user account.

**Independent Test**: Run `plate-probe.yml`; confirm each leg uploads an artifact or a logged failure reason, and that `research.md` records the chosen route. In the fallback route, edit one row in a fixture CSV inside a plate area and confirm `plate_freshness.py` fails; edit a row outside the area and confirm it passes.

### User Story 5 - Optional timeline animation (Priority: P3)

`--frames year` renders one frame per announced year with pins accumulating (verified dates only) and assembles them with ffmpeg called as a subprocess. Defer unless a newsletter or pitch deck asks for it; register `ffmpeg` as `GPL-2.0-cli` if used.

### Edge Cases

- Areas crossing state lines or multi-county frames (for example a TVA subframe): the config takes a FIPS list; the DEM covers the union bbox.
- Flat counties: z-scale from the config; the default must not exaggerate relief misleadingly. Record the z-scale in the footer when it is above 1.5.
- No pins in the area: the plate renders with a footer line stating that no tracked cases fall inside it, never an empty legend.
- 3DEP service unreachable: fail with the cache path and retry hint; no fallback to a lower-quality global DEM without a config flag.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `forge3d` pinned to an exact version in a new `requirements/plates.in` (not `ci.in`, since CI does not render plates unless US4 says so).
- **FR-002**: Only open-core forge3d APIs; composition with Pillow.
- **FR-003**: Inputs from committed files and the config only; sidecar JSON records commit SHA, versions, DEM product and access date.
- **FR-004**: Colors from the canonical palette (the shared JSON mirror from spec 010 US3 if present, otherwise read from `viz-palette.js` the same way).
- **FR-005**: Titles and captions pass the leak audit and the em-dash check; credits match `DATA_NOTICES.md`, with a USGS 3DEP courtesy credit added there.
- **FR-007**: CI never commits plate images. Either the probe-selected CI route renders them as artifacts, or `plate_freshness.py` gates committed lock files against current inputs.
- **FR-006**: `--selftest` runs without a GPU by mocking the render call and checking config validation, overlay filtering, vocabulary, and composition.

## Success Criteria *(mandatory)*

- **SC-001**: A county plate renders locally in under three minutes with a warm cache.
- **SC-002**: Zero hand edits between render and insertion into a report.
- **SC-003**: Integration audit clean, including the Pro-boundary check.

## Assumptions

- forge3d open core stays `Apache-2.0 OR MIT`; the pinned version is used until a deliberate upgrade.
- 3DEP 1/3 arc-second coverage is enough for county-scale plates in the lower 48; Alaska and Hawaii plates use whatever 3DEP offers and note it.
