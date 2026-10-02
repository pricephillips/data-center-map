# Tasks: Terrain Plates for Static Deliverables

**Input**: [plan.md](./plan.md), [research.md](./research.md), [spec.md](./spec.md)
**Status**: implemented 2026-10-01, uncommitted in the working tree for Price.

## Phase 1: Setup

- [x] T001 Local venv: forge3d==1.40.1, rasterio, Pillow; read forge3d's `_gpu.py`, `path_tracing.py`, `cog.py`, `legend.py`, `scale_bar.py`, `north_arrow.py`, `text.py`, `_license.py` (research R1)
- [x] T002 `requirements/plates.in` with exact pins (FR-001); `.gitignore` gains `outputs/`

## Phase 2: User Story 2, Pro boundary (P1)

- [x] T003 [US2] Boundary scan in `integration_audit.py` and its planted-fixture selftests (shared with spec 012 T004, T005)
- [x] T004 [US2] Registry: `forge3d` selected with Pro risk notes, `forge3d-pro` eliminated

## Phase 3: User Story 1, a plate from a config (P1)

- [x] T005 [US1] Config schema and validation (title states a finding, area, camera, sun, output, overlays, dem)
- [x] T006 [US1] 3DEP fetch through rasterio windowed reads; `.cache/dem` with no network on a warm cache; failure message with cache path and retry hint
- [x] T007 [US1] Albedo (quiet tint, dimmed outside the area, boundary line); orthographic camera; `render_terrain_poster`
- [x] T008 [US1] Screen-space pins (vocabulary enforced), labels with halo through `forge3d.text`, Legend, ScaleBar, NorthArrow; Pillow composition; footer with SHA, credits, z-scale disclosure, no-case line
- [x] T009 [US1] Sidecar JSON: inputs with hashes, versions, SHA, DEM product and access date, adapter, timings
- [x] T010 [US1] `configs/plates/13255_spalding.json`; rendered locally (preview and full)
- [x] T011 [US1] `--selftest` (26 checks, mocked render)
- [x] T012 [P] [US1] `DATA_NOTICES.md` USGS 3DEP courtesy credit (FR-005)

## Phase 4: User Story 3, plate slot in spec 010 renderers (P2)

- [x] T013 [US3] Spec 010 renderers do not exist yet: slot contract recorded in `specs/010-deliverable-generation/plan.md`

## Phase 5: User Story 4, where it runs (P2)

- [x] T014 [US4] `.github/workflows/plate-probe.yml`: workflow_dispatch only, DEM job plus legs A to D, 10-minute leg timeout, adapter logged, PNG and timing uploaded, decide job
- [x] T015 [US4] `--probe` and `--probe-decide` (the decision rule as code, selftested)
- [x] T016 [US4] Legs B and C reproduced locally on Ubuntu 24.04 with Mesa; rule applied; full print render measured on leg B (research R4)
- [x] T017 [US4] `.github/workflows/render-plates.yml` (workflow_dispatch only, artifacts only)
- [x] T018 [US4] Self-hosted runner recorded as an option only (research R5); not set up
- [x] T019 [US4] Dispatched `plate-probe.yml` (runs 37059805730, 37061768374, 37061771545); route `ci:B` confirmed, A fails on WARP, full renders B 57.6 min and D 4.9 min recorded in research R4; probe job timeout fixed for the `full_leg` job
- [ ] T020 [US4] Only if every leg fails on the dispatch: build the local-render fallback (`scripts/render_plates_local.sh`, `scripts/plate_freshness.py --selftest`, `configs/plates/<id>.lock.json`, a pipeline.yml freshness step)

## Phase 6: User Story 5, timeline animation (P3)

- [ ] T021 [US5] Deferred until a newsletter or deck asks; register ffmpeg as `GPL-2.0-cli` then
