# Research: Terrain Plates for Static Deliverables

Every forge3d call and option below was read from the installed
`forge3d==1.40.1` wheel (Python sources and strings in `_forge3d.abi3.so`) in
a Python 3.11 venv on 2026-10-01, not from memory.

## R1. forge3d open-core API used

| Need | Call | Note |
|---|---|---|
| Terrain render | `forge3d.path_tracing.render_terrain_poster(heightmap, width, height, camera, albedo_map=, albedo_sampling=, spacing=, exaggeration=, sun_azimuth_deg=, sun_elevation_deg=, spp=, min_frames=, max_frames=, variance_threshold=, tile=)` | Converged GPU path trace, tiled. Raises rather than return an unconverged image. |
| Camera | dict `origin, look_at, up, fov_y, aspect, exposure, model="orthographic", half_height` | Verified empirically: terrain is centred on the origin, `x = (col - (W-1)/2) * spacing[0]`, `z = (row - (H-1)/2) * spacing[1]`, row 0 at -z (north), height on +y times `exaggeration`. Screen projection reproduced to under one pixel, so pins and labels are drawn in screen space. |
| Adapter | `forge3d.device_probe(backend)` | Returns `name, backend, device_type, driver, software_fallback`. |
| Cartography | `forge3d.legend.Legend`, `forge3d.scale_bar.ScaleBar`, `forge3d.north_arrow.NorthArrow`, `forge3d.text.shape` + `rasterize_shaped_run` (halo by mask dilation) | All CPU, no license gate. Font: bundled Noto Sans Latin subset (SIL OFL 1.1). |
| Pro (never used) | `set_license_key`, `MapPlate*`, `export_svg/export_pdf`, `add_buildings*`, `save_bundle/load_bundle`, Mapbox style loading | Gated by `_license._check_pro_access`; banned in repo code by `integration_audit.py`. `ScaleBar.compute_meters_per_pixel` takes a `map_plate.BBox`; the plate computes metres per pixel itself instead of importing from `map_plate`. |

**Backend and fallback option names.** forge3d 1.40.1 pins the wgpu backend
with the environment variable `WGPU_BACKENDS` (`vulkan|dx12|metal|gl`;
`WGPU_BACKEND` is also read by `forge3d.determinism`). There is no fallback-
adapter flag in the Python API or the native strings: the native layer asks
for a hardware adapter and falls back to a software adapter on its own
("A hardware adapter was not found and the software fallback adapter is also
unavailable" is its error when neither exists). So each probe leg forces the
software path by pinning a backend on a runner that has no GPU, and logs what
`device_probe` reports.

## R2. 3DEP access method and client library licence (US1)

- **Source.** USGS 3DEP staged products, cloud-optimized GeoTIFF, one file per
  1-degree cell named by its north-west corner:
  `https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n34w085/USGS_13_n34w085.tif`
  (1/3 arc-second, 476 MB file, overviews 2 to 32) and the `.../Elevation/1/...`
  1 arc-second equivalent. Public domain; courtesy credit "U.S. Geological
  Survey, 3D Elevation Program" (added to `DATA_NOTICES.md`).
- **Method.** HTTP range reads of only the window needed (GDAL `/vsicurl/`),
  resampled to at most 2,048 samples on the long side using the COG
  overviews. Spalding (0.46 x 0.20 degrees with padding) fetched in 3.5 s as
  a 2048 x 1031 grid at about 21 m spacing. Areas wider than 0.75 degrees use
  the 1 arc-second product; multi-cell areas are mosaicked with
  `rasterio.merge`.
- **Library.** rasterio 1.4.4, BSD-3-Clause; its wheels bundle GDAL (MIT/X)
  and PROJ (MIT). Registered as `rasterio` (selected, session 10). Only the
  fetch imports it.
- **Rejected.** forge3d's own `CogDataset` fails on 3DEP files ("unsupported
  planetary COG CRS ... EPSG:4269 to EPSG:4326 requires an unavailable PROJ
  backend"). The 3DEP ImageServer (`elevation.nationalmap.gov`) also works in
  principle but was blocked by this sandbox's egress policy and adds a server
  dependency the staged COGs do not need.
- **Datum.** 3DEP is NAD83 geographic; the NAD83 to WGS84 offset (about a
  metre in the lower 48) is under one DEM sample, and the plate is drawn in
  the DEM's own frame.
- **Cache.** `.cache/dem/<key>.npz` (gitignored), keyed by product, padded
  bbox and grid shape; the sidecar records product, tile URLs and access date.
  A warm cache makes no network request (selftest). A failed fetch names the
  cache path and the host to retry; no global DEM is substituted.

## R3. Plate design choices

- Orthographic camera so the scale bar measures true horizontal distance left
  to right (stated in the footer); the north arrow is rotated from the
  projected north vector.
- County boundary painted into the albedo (follows the terrain); outside the
  area the tint is dimmed. Pins are projected through the camera and drawn
  over the render so palette colors stay exact (painted pins would be shaded).
- The renderer returns opaque environment color where rays miss; the plate
  restores its background outside the projected terrain perimeter.
- Relief exaggeration above 1.5x is printed in the footer. The fixture uses
  2.5x (Spalding spans 193 to 292 m).

## R4. Where it runs: the probe and the decision rule (US4)

`.github/workflows/plate-probe.yml` (workflow_dispatch only) fetches the DEM
once, runs four legs with a 10-minute timeout each, and a `decide` job
applies the rule with `scripts/render_terrain_plate.py --probe-decide`
(selftested): the first leg in A, B, C, D order with a non-blank render
(luminance standard deviation above 4) inside its timeout wins; none means the
local-render fallback.

The workflow cannot be dispatched from this session (it is not on the default
branch and nothing may be pushed), so legs B and C were reproduced locally on
Ubuntu 24.04, the `ubuntu-latest` image, after installing the same Mesa
packages the legs install:

| Leg | Runner | Adapter reported | 512x512 render | Luminance std | Result |
|---|---|---|---|---|---|
| A | windows-latest, DX12 | not run (needs dispatch) | | | pending |
| B | ubuntu, Vulkan | `llvmpipe (LLVM 20.1.2, 256 bits)`, Vulkan, Cpu, driver llvmpipe (Mesa lavapipe), software_fallback true | 5.2 s render, 6.2 s total | 21.9 | pass |
| C | ubuntu, GL | `llvmpipe (LLVM 20.1.2, 256 bits)`, Gl, Cpu, software_fallback true | 7.8 s render, 8.4 s total | 23.5 | pass |
| D | macos-14, Metal | not run (needs dispatch) | | | pending |

**Decision recorded (automatic rule over the available records):** route
`ci:B`. Provisional only in one respect: if leg A passes on the first real
dispatch, the rule's A-first preference makes A the route, and
`render-plates.yml` takes the leg as its `route` input, so that is an input
change, not a code change. Either way a leg passes, so the fallback route
(local renders plus `plate_freshness.py` lock files) is not built; it remains
specified in spec US4 for the case where a real dispatch fails every leg.

**Full print render on leg B (local, 4 vCPU, lavapipe).** 3000x2000 plate,
2933x1637 map, spp 2, 64 to 1,024 frames:

- With `variance_threshold` 2e-4 the render did not converge in 512 frames
  (variance 8.97e-4) and forge3d refused to return it after 8.3 minutes.
  forge3d's own default threshold, 1e-3, is now the plate default.
- With 1e-3 and 512 frames, one tile still ended at 1.11e-3 (refused after
  16.5 minutes). `max_frames` is now 1,024.
- With 1e-3 and up to 1,024 frames: converged, **2,142 s render, 2,145 s
  total (35.7 minutes)**, luminance std 25.7. This is the measured full print
  render on leg B, inside the 60-minute budget with about 40 percent headroom.
- Reduced-sample preview (32 frames, spp 1), same size: 79 s render, 81 s
  total.

GitHub's standard Linux runner also has 4 vCPUs, so these times are the
expected order on leg B; the dispatch input `full_leg` re-measures on the
runner itself. Since a full print render fits inside 60 minutes,
`render-plates.yml` renders full quality by default; `preview=true` is
available and labels files and sidecars `preview`.

## R5. Self-hosted runner on Price's Mac (option only, not set up)

Not configured, because the repository is public and GitHub warns that
self-hosted runners on public repositories can be made to run untrusted code
from forks. If Price decides to use one later, the safeguards are:

1. Workflows that target it trigger on `workflow_dispatch` only; no
   `pull_request` or `pull_request_target` trigger on any workflow that
   targets it.
2. Runner label `plates-mac`, used only by `render-plates.yml`.
3. Ephemeral mode (`./config.sh --ephemeral`), one job per registration.
4. A dedicated macOS user account with no access to Price's own files,
   keychain or credentials.
5. Repository setting "Require approval for all outside collaborators" kept on.

Leg D (macos-14, Metal) would tell whether a GitHub-hosted Mac exposes its
virtual GPU, which would make the self-hosted option unnecessary for Metal.
