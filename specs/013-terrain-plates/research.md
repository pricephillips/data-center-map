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

### Runner results (2026-10-02)

Probe [run 37059805730](https://github.com/pricephillips/data-center-map/actions/runs/37059805730)
on `main`, 512x512, the adapter each leg reported through `device_probe()`:

| Leg | Runner | Adapter reported | 512x512 probe | Result |
|---|---|---|---|---|
| A | windows-latest, DX12 | `Microsoft Basic Render Driver` (WARP) | 117 s, then error | **fail**: `RuntimeError: [Render] Render error: terrain PT ReSTIR reuse chain produced no valid reservoirs for a sun-lit scene` |
| B | ubuntu-latest, Vulkan | `llvmpipe (LLVM 20.1.2, 256 bits)`, Cpu, Mesa 25.2.8 lavapipe, software_fallback true | 8.75 s | pass |
| C | ubuntu-latest, GL | `llvmpipe (LLVM 20.1.2, 256 bits)`, Gl, Cpu, software_fallback true | 8.84 s | pass |
| D | macos-14, Metal | `Apple Paravirtual device`, IntegratedGpu, software_fallback **false** | 7.01 s | pass |

**Decision (the automatic rule, `decide` job):** route **`ci:B`**. A fails, so
B is the first passing leg in A, B, C, D order. The local-render fallback
(`render_plates_local.sh`, `plate_freshness.py`, lock files) stays unbuilt;
it remains specified in US4 for the case where every leg fails.

That run's full-render timing was lost: the probe job's own 10-minute limit
cancelled the full print render step at 9.6 minutes, despite the step's
70-minute limit. The job limit is now 80 minutes for the `full_leg` job only
(other legs keep 10). The full renders were then re-measured from the branch
carrying that fix:

| Leg | Run | Full print render (3000x2000 plate, 2933x1637 map, spp 2, 64 to 1,024 frames, threshold 1e-3) |
|---|---|---|
| B | [37061768374](https://github.com/pricephillips/data-center-map/actions/runs/37061768374) | converged, **3,455 s render, 3,458 s total (57.6 minutes)**, luminance std 25.7 |
| D | [37061771545](https://github.com/pricephillips/data-center-map/actions/runs/37061771545) | converged, **293 s render, 294 s total (4.9 minutes)**, luminance std 27.5 |

What this means for `render-plates.yml`:

- **Route B fits, barely.** The full print render on B finishes inside 60
  minutes, so under the US4 rule `render-plates.yml` keeps full quality as its
  default. The margin is about 2.4 minutes for one county plate, and the
  runner was 61 percent slower than the local 4-vCPU measurement below. A
  second config, or a slightly larger area, will not fit in one job (its
  limit is 75 minutes). Render several plates with `preview=true`, or one
  plate per dispatch.
- **Leg D is the faster route by a factor of about 12.** The macos-14 runner
  exposes a paravirtualized Apple GPU to Metal (not a software fallback), and
  it renders the full plate in under 5 minutes. The rule's A, B, C, D order
  put B first because the spec expected the Mac leg to be the least certain.
  Switching CI to D is a decision for Price, not something the rule does on
  its own: macOS minutes cost more than Linux minutes on GitHub's billing for
  private repositories (this repository is public, so standard runners are
  free either way), and D is a virtual GPU whose behavior may change with
  the runner image. To use it today, dispatch `render-plates.yml` with
  `route=D`; no code change is needed.
- **WARP is not usable** for this renderer in forge3d 1.40.1: the ReSTIR pass
  gets no valid reservoirs on the Basic Render Driver. Recheck on a forge3d
  upgrade.

### Earlier local measurements (Ubuntu 24.04 container, 4 vCPU)

Before the workflow could be dispatched, legs B and C were reproduced locally
with the same Mesa packages: B 5.2 s and C 7.8 s at 512x512, both passing. The
local full print render on lavapipe also settled the sample settings:

- With `variance_threshold` 2e-4 the render did not converge in 512 frames
  (variance 8.97e-4) and forge3d refused to return it after 8.3 minutes.
  forge3d's own default threshold, 1e-3, is now the plate default.
- With 1e-3 and 512 frames, one tile still ended at 1.11e-3 (refused after
  16.5 minutes). `max_frames` is now 1,024.
- With 1e-3 and up to 1,024 frames: converged in 2,142 s (35.7 minutes).
- Reduced-sample preview (32 frames, spp 1), same size: 79 s render.

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

Leg D answered the open question: the GitHub-hosted macos-14 runner exposes
its paravirtualized GPU to Metal and renders the full plate in 4.9 minutes
(R4). A Metal route therefore exists without a self-hosted runner, which
removes the main reason to consider one.
