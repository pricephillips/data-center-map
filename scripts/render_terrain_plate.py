#!/usr/bin/env python3
"""
scripts/render_terrain_plate.py

Renders one terrain plate (a print-resolution hero image for the static
report line, spec 013) from a config file. Engagements add a config under
configs/plates/, never code.

Pipeline per plate
  1. Elevation: USGS 3DEP (public domain), read with windowed HTTP range
     requests from the 3DEP staged-products bucket and cached under the
     gitignored .cache/dem/. A warm cache makes no network request. 1/3
     arc-second for areas up to 0.75 degrees across, 1 arc-second above.
  2. Overlays from committed files only: the county boundary from
     data/geo/counties_2024.topojson is painted into the terrain albedo; case
     pins from master_opposition_clean.csv (map_pinnable rows inside the area)
     are projected to screen and drawn over the render, colored by
     outcome_defensible with the platform vocabulary only; an optional client
     site marker enters only through the config.
  3. Cartography from the forge3d open core: labels with halo (forge3d.text),
     Legend, ScaleBar, NorthArrow.
  4. Composition with Pillow: title stating the finding, subtitle, legend,
     source and vintage footer with the commit SHA.

The terrain is path traced by forge3d's open-core render_terrain_poster with
an orthographic camera, so the scale bar measures true horizontal distance
left to right. No forge3d Pro API is used anywhere (integration_audit.py).

Reads
  configs/plates/<id>.json, data/geo/counties_2024.topojson,
  master_opposition_clean.csv, viz-palette.js, .cache/dem/
Writes
  outputs/plates/<id>.png (or <id>-preview.png) and the matching .json sidecar
  (outputs/ is gitignored: plates are deliverable artifacts, not data)
  .cache/dem/<key>.npz on a cold cache
  with --probe: outputs/probe/<leg>.json and .png

Usage
  python scripts/render_terrain_plate.py --config configs/plates/13255_spalding.json
  python scripts/render_terrain_plate.py --config ... --preview
  python scripts/render_terrain_plate.py --config ... --fetch-dem-only
  python scripts/render_terrain_plate.py --config ... --probe --leg B --size 512
  python scripts/render_terrain_plate.py --probe-decide outputs/probe
  python scripts/render_terrain_plate.py --selftest      no GPU, no network
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import export_geolibre as eg  # noqa: E402  (palette, topojson, provenance)

TOPOJSON = "data/geo/counties_2024.topojson"
CASES = "master_opposition_clean.csv"
PALETTE_JS = "viz-palette.js"
DEM_CACHE = ".cache/dem"
OUT_DIR = "outputs/plates"
PROBE_DIR = "outputs/probe"

OUTCOME_TERMS = eg.OUTCOME_TERMS
OUTCOME_LABEL = eg.OUTCOME_LABEL

# 3DEP staged products, cloud-optimized GeoTIFF, one file per 1-degree cell
# named by its north-west corner. NAD83 geographic; the NAD83 to WGS84 offset
# (about a metre in the lower 48) is below one DEM sample at either product.
DEM_PRODUCTS = {
    "13": {"name": "3DEP 1/3 arc-second DEM", "res_deg": 1 / 10800,
           "url": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
                  "{tile}/USGS_13_{tile}.tif"},
    "1": {"name": "3DEP 1 arc-second DEM", "res_deg": 1 / 3600,
          "url": "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/"
                 "{tile}/USGS_1_{tile}.tif"},
}
FINE_PRODUCT_MAX_DEG = 0.75
DEM_MAX_SAMPLES = 2048

M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_EQ = 111_320.0

# Relief above this is disclosed in the footer (spec 013, flat counties).
Z_DISCLOSE = 1.5
# A probe render is "non-blank" above this luminance standard deviation.
PROBE_MIN_STD = 4.0
PROBE_LEG_ORDER = ("A", "B", "C", "D")

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost|won)\b", re.IGNORECASE)
EM_DASH = "\u2014"

CREDIT_3DEP = ("Elevation: U.S. Geological Survey, 3D Elevation Program ({product}), "
               "accessed {accessed}; public domain, courtesy USGS.")
CREDIT_CENSUS = "Boundaries: U.S. Census Bureau 2024 cartographic boundary file, 1:5m."
CREDIT_CASES = "Cases: this platform's opposition tracker, sourced records, commit {sha}."
CREDIT_FONT = "Type: Noto Sans (SIL OFL 1.1) as bundled with forge3d."

# Base terrain tint, sRGB, low to high elevation. Deliberately quiet so the
# outcome colors carry the figure; outside the area the tint is dimmed.
TINT_LOW = (214, 208, 194)
TINT_HIGH = (247, 244, 236)
OUTSIDE_DIM = 0.62
BOUNDARY_RGB = (44, 53, 68)   # VizPalette BASE_STROKE
SITE_RGB = (255, 255, 255)
PLATE_BG = (250, 249, 246)


class PlateError(RuntimeError):
    """Raised for any config or input the renderer refuses to paper over."""


def P(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def _check_text(label: str, text: str) -> None:
    if LEAK_RE.search(text or ""):
        raise PlateError(f"{label} uses scorekeeping vocabulary: {text!r}")
    if EM_DASH in (text or ""):
        raise PlateError(f"{label} contains an em-dash: {text!r}")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    return validate_config(cfg, path)


def validate_config(cfg: dict, path: str = "<config>") -> dict:
    pid = cfg.get("id") or os.path.splitext(os.path.basename(path))[0]
    if not re.fullmatch(r"[a-z0-9_]+", pid):
        raise PlateError(f"{path}: id {pid!r} must be lowercase letters, digits, underscore")
    title = (cfg.get("title") or "").strip()
    if len(title.split()) < 4:
        raise PlateError(f"{path}: every plate states its finding in the title "
                         "(spec 010 US5); title missing or under four words")
    for key in ("title", "subtitle", "caption"):
        _check_text(f"{path}: {key}", cfg.get(key, ""))
    area = cfg.get("area") or {}
    if bool(area.get("fips")) == bool(area.get("bbox")):
        raise PlateError(f"{path}: area needs exactly one of fips (string or list) or bbox")
    if area.get("fips"):
        fips = area["fips"] if isinstance(area["fips"], list) else [area["fips"]]
        if not all(re.fullmatch(r"\d{5}", str(f)) for f in fips):
            raise PlateError(f"{path}: fips must be 5-digit strings: {fips}")
        area["fips"] = [str(f) for f in fips]
    else:
        w, s, e, n = (float(v) for v in area["bbox"])
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise PlateError(f"{path}: bbox must be [west, south, east, north]")
    cam = cfg.setdefault("camera", {})
    cam.setdefault("azimuth_deg", 0.0)
    cam.setdefault("elevation_deg", 55.0)
    cam.setdefault("z_scale", 1.0)
    if not (20 <= float(cam["elevation_deg"]) <= 90):
        raise PlateError(f"{path}: camera elevation_deg must be 20 to 90")
    if not (0.5 <= float(cam["z_scale"]) <= 6):
        raise PlateError(f"{path}: camera z_scale must be 0.5 to 6")
    sun = cfg.setdefault("sun", {})
    sun.setdefault("azimuth_deg", 315.0)
    sun.setdefault("elevation_deg", 35.0)
    out = cfg.setdefault("output", {})
    out.setdefault("width", 3000)
    out.setdefault("height", 2000)
    if not (256 <= int(out["width"]) <= 8000 and 256 <= int(out["height"]) <= 8000):
        raise PlateError(f"{path}: output width and height must be 256 to 8000")
    smp = out.setdefault("samples", {})
    smp.setdefault("spp", 2)
    smp.setdefault("min_frames", 64)
    smp.setdefault("max_frames", 1024)
    smp.setdefault("variance_threshold", 1e-3)
    ov = cfg.setdefault("overlays", {})
    ov.setdefault("county_boundary", True)
    ov.setdefault("pins", True)
    ov.setdefault("labels", True)
    site = ov.get("site")
    if site:
        for k in ("lon", "lat", "label"):
            if k not in site:
                raise PlateError(f"{path}: overlays.site needs lon, lat and label")
        _check_text(f"{path}: site label", site["label"])
    dem = cfg.setdefault("dem", {})
    dem.setdefault("pad_frac", 0.08)
    dem.setdefault("max_samples", DEM_MAX_SAMPLES)
    if dem.get("product") not in (None, "13", "1"):
        raise PlateError(f"{path}: dem.product must be '13' or '1' (3DEP only); a lower-"
                         "quality global DEM needs allow_global_dem, which this version "
                         "does not implement")
    cfg["id"] = pid
    return cfg


# --------------------------------------------------------------------------
# area, geometry, cases
# --------------------------------------------------------------------------

def _rings(geom: dict) -> list[list[list[float]]]:
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    return list(geom["coordinates"])


def point_in_polygons(lon: float, lat: float, polys: list) -> bool:
    for poly in polys:
        inside = False
        for k, ring in enumerate(poly):
            hit = False
            j = len(ring) - 1
            for i in range(len(ring)):
                xi, yi = ring[i]
                xj, yj = ring[j]
                if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    hit = not hit
                j = i
            if k == 0:
                inside = hit
            elif hit:
                inside = False
        if inside:
            return True
    return False


def resolve_area(cfg: dict, topo_path: str) -> dict:
    """bbox (padded), polygons and names for the plate area."""
    area = cfg["area"]
    polys, names = [], []
    if area.get("fips"):
        with open(topo_path, encoding="utf-8") as fh:
            feats = {f["id"]: f for f in eg.topojson_features(json.load(fh))}
        missing = [f for f in area["fips"] if f not in feats]
        if missing:
            raise PlateError(f"FIPS not in {TOPOJSON}: {missing}")
        for f in area["fips"]:
            polys.extend(_rings(feats[f]["geometry"]))
            names.append(feats[f]["properties"].get("name", f))
        xs = [p[0] for poly in polys for ring in poly for p in ring]
        ys = [p[1] for poly in polys for ring in poly for p in ring]
        w, s, e, n = min(xs), min(ys), max(xs), max(ys)
    else:
        w, s, e, n = (float(v) for v in area["bbox"])
    pad = float(cfg["dem"]["pad_frac"])
    dx, dy = (e - w) * pad, (n - s) * pad
    return {"bbox": [round(w - dx, 5), round(s - dy, 5), round(e + dx, 5), round(n + dy, 5)],
            "core_bbox": [w, s, e, n], "polygons": polys, "names": names}


def area_cases(rows: list[dict], area: dict) -> list[dict]:
    """map_pinnable rows inside the area; fails on any non-platform outcome."""
    w, s, e, n = area["core_bbox"]
    out = []
    for r in rows:
        if str(r.get("map_pinnable", "")).strip() != "True":
            continue
        lat, lon = eg._num(r.get("lat")), eg._num(r.get("lon"))
        if lat is None or lon is None or not (w <= lon <= e and s <= lat <= n):
            continue
        if area["polygons"] and not point_in_polygons(lon, lat, area["polygons"]):
            continue
        outcome = (r.get("outcome_defensible") or "").strip()
        if outcome not in OUTCOME_TERMS:
            raise PlateError(f"outcome {outcome!r} on {r.get('project_id')!r} is not a "
                             f"platform term {OUTCOME_TERMS}")
        out.append({"lon": lon, "lat": lat, "outcome": outcome,
                    "project_id": r.get("project_id", "")})
    return out


# --------------------------------------------------------------------------
# DEM
# --------------------------------------------------------------------------

def dem_plan(bbox: list[float], cfg: dict) -> dict:
    w, s, e, n = bbox
    product = cfg["dem"].get("product") or (
        "13" if max(e - w, n - s) <= FINE_PRODUCT_MAX_DEG else "1")
    res = DEM_PRODUCTS[product]["res_deg"]
    lat0 = math.radians((s + n) / 2)
    w_m = (e - w) * M_PER_DEG_LON_EQ * math.cos(lat0)
    h_m = (n - s) * M_PER_DEG_LAT
    native = max((e - w) / res, (n - s) / res)
    long_side = int(min(native, int(cfg["dem"]["max_samples"])))
    if w_m >= h_m:
        cols, rows = long_side, max(16, round(long_side * h_m / w_m))
    else:
        rows, cols = long_side, max(16, round(long_side * w_m / h_m))
    tiles = []
    for top in range(math.floor(s) + 1, math.ceil(n) + 1):
        for west in range(math.floor(w), math.ceil(e)):
            ns = f"n{top:02d}" if top >= 0 else f"s{-top:02d}"
            ew = f"w{-west:03d}" if west < 0 else f"e{west:03d}"
            tiles.append(DEM_PRODUCTS[product]["url"].format(tile=ns + ew))
    key = hashlib.sha256(json.dumps([product, [round(v, 5) for v in bbox], rows, cols])
                         .encode()).hexdigest()[:16]
    return {"product": product, "product_name": DEM_PRODUCTS[product]["name"],
            "rows": rows, "cols": cols, "spacing_m": [w_m / cols, h_m / rows],
            "sources": tiles, "key": key, "bbox": bbox}


def load_or_fetch_dem(plan: dict, cache_dir: str, fetch=None) -> tuple:
    """(heights float32 [rows, cols] north-up, meta). Warm cache: no network."""
    import numpy as np

    path = os.path.join(cache_dir, f"{plan['key']}.npz")
    if os.path.exists(path):
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        meta["cache"] = "warm"
        return z["dem"].astype(np.float32), meta
    fetch = fetch or fetch_3dep
    try:
        dem = fetch(plan)
    except Exception as exc:  # noqa: BLE001  any transport or decode failure
        raise PlateError(
            f"3DEP fetch failed ({exc}). Nothing was cached at {path}. Retry when "
            f"{plan['sources'][0].split('/StagedProducts')[0]} is reachable; no lower-"
            "quality DEM is substituted.") from exc
    dem = np.asarray(dem, dtype=np.float32)
    if dem.shape != (plan["rows"], plan["cols"]):
        raise PlateError(f"DEM shape {dem.shape} != planned {(plan['rows'], plan['cols'])}")
    bad = ~np.isfinite(dem) | (dem < -1000)
    if bad.all():
        raise PlateError("3DEP returned no valid elevation for the area")
    if bad.any():
        dem[bad] = float(np.nanmin(np.where(bad, np.nan, dem)))
    meta = {k: plan[k] for k in ("product", "product_name", "sources", "bbox", "rows", "cols",
                                  "spacing_m")}
    meta["accessed"] = datetime.now(timezone.utc).date().isoformat()
    os.makedirs(cache_dir, exist_ok=True)
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, dem=dem, meta=np.array(json.dumps(meta)))
    os.replace(tmp, path)
    meta["cache"] = "cold"
    return dem, meta


def fetch_3dep(plan: dict):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.merge import merge

    w, s, e, n = plan["bbox"]
    res = ((e - w) / plan["cols"], (n - s) / plan["rows"])
    env = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
           "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif", "GDAL_HTTP_MAX_RETRY": "3",
           "GDAL_HTTP_RETRY_DELAY": "2"}
    with rasterio.Env(**env):
        srcs = [rasterio.open("/vsicurl/" + u) for u in plan["sources"]]
        try:
            arr, _ = merge(srcs, bounds=(w, s, e, n), res=res, nodata=-999999.0,
                           resampling=Resampling.bilinear)
        finally:
            for src in srcs:
                src.close()
    return arr[0, : plan["rows"], : plan["cols"]]


# --------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------

def _srgb_to_linear(c):
    import numpy as np

    c = np.asarray(c, dtype=np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _grid_rc(lon, lat, bbox, rows, cols):
    w, s, e, n = bbox
    return ((n - lat) / (n - s) * rows - 0.5, (lon - w) / (e - w) * cols - 0.5)


def build_albedo(dem, area: dict, plan: dict, cfg: dict):
    """(rows, cols, 4) linear RGBA: quiet tint, dimmed outside, boundary line."""
    import numpy as np
    from PIL import Image, ImageDraw

    rows, cols = dem.shape
    lo, hi = float(np.percentile(dem, 1)), float(np.percentile(dem, 99))
    t = np.clip((dem - lo) / max(1e-6, hi - lo), 0, 1)[..., None]
    lin = _srgb_to_linear(TINT_LOW) * (1 - t) + _srgb_to_linear(TINT_HIGH) * t
    if area["polygons"]:
        mask = Image.new("L", (cols, rows), 0)
        line = Image.new("L", (cols, rows), 0)
        dm, dl = ImageDraw.Draw(mask), ImageDraw.Draw(line)
        width = max(2, round(max(rows, cols) / 450))
        for poly in area["polygons"]:
            for k, ring in enumerate(poly):
                pts = [tuple(reversed(_grid_rc(x, y, plan["bbox"], rows, cols))) for x, y in ring]
                dm.polygon(pts, fill=255 if k == 0 else 0)
                if cfg["overlays"]["county_boundary"]:
                    dl.line(pts + [pts[0]], fill=255, width=width, joint="curve")
        inside = np.asarray(mask, dtype=np.float32)[..., None] / 255.0
        lin = lin * (OUTSIDE_DIM + (1 - OUTSIDE_DIM) * inside)
        edge = np.asarray(line, dtype=np.float32)[..., None] / 255.0
        lin = lin * (1 - edge) + _srgb_to_linear(BOUNDARY_RGB) * edge
    rgba = np.ones((rows, cols, 4), dtype=np.float32)
    rgba[..., :3] = lin
    return rgba, (lo, hi)


class Camera:
    """Orthographic camera framing the DEM; mirrors forge3d's projection."""

    def __init__(self, dem, plan: dict, cfg: dict, width: int, height: int):
        import numpy as np

        self.np = np
        rows, cols = dem.shape
        self.sx, self.sz = plan["spacing_m"]
        self.rows, self.cols, self.W, self.H = rows, cols, width, height
        self.z_scale = float(cfg["camera"]["z_scale"])
        self.exposure = float(cfg["camera"].get("exposure", 1.6))
        az = math.radians(float(cfg["camera"]["azimuth_deg"]))
        el = math.radians(float(cfg["camera"]["elevation_deg"]))
        if el > math.radians(89.5):
            el = math.radians(89.5)
        fwd = np.array([math.sin(az) * math.cos(el), -math.sin(el), -math.cos(az) * math.cos(el)])
        self.fwd = fwd / np.linalg.norm(fwd)
        right = np.cross(self.fwd, [0.0, 1.0, 0.0])
        self.right = right / np.linalg.norm(right)
        self.up = np.cross(self.right, self.fwd)
        ext_x, ext_z = cols * self.sx / 2, rows * self.sz / 2
        zmax = float(dem.max()) * self.z_scale
        zmin = float(dem.min()) * self.z_scale
        corners = np.array([[x, y, z] for x in (-ext_x, ext_x) for z in (-ext_z, ext_z)
                            for y in (zmin, zmax)])
        u = corners @ self.right
        v = corners @ self.up
        aspect = width / height
        self.cx, self.cy = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
        self.half_h = 1.03 * max((v.max() - v.min()) / 2, (u.max() - u.min()) / 2 / aspect)
        self.aspect = aspect
        # right, up and fwd are orthonormal, so this point projects to the
        # centre of the framed extent.
        target = self.cx * self.right + self.cy * self.up
        dist = 4 * max(ext_x, ext_z, zmax - zmin, 1.0)
        self.look_at = target
        self.origin = target - self.fwd * dist

    def as_forge3d(self) -> dict:
        return {"origin": tuple(float(v) for v in self.origin),
                "look_at": tuple(float(v) for v in self.look_at),
                "up": (0.0, 1.0, 0.0), "fov_y": 30.0, "aspect": float(self.aspect),
                "exposure": float(self.exposure), "model": "orthographic", "half_height": float(self.half_h)}

    def project(self, row: float, col: float, height_m: float) -> tuple[float, float]:
        p = self.np.array([(col - (self.cols - 1) / 2) * self.sx, height_m * self.z_scale,
                           (row - (self.rows - 1) / 2) * self.sz]) - self.look_at
        px = (p @ self.right) / (self.half_h * self.aspect)
        py = (p @ self.up) / self.half_h
        return (px + 1) / 2 * self.W, (1 - py) / 2 * self.H

    def meters_per_pixel(self) -> float:
        return 2 * self.half_h * self.aspect / self.W

    def north_rotation_deg(self) -> float:
        a = self.project(0, 0, 0)
        b = self.project(-100, 0, 0)
        return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1])))


def render_terrain(dem, albedo, cam: Camera, cfg: dict, preview: bool) -> dict:
    from forge3d.path_tracing import render_terrain_poster

    smp = dict(cfg["output"]["samples"])
    if preview:
        smp.update({"spp": 1, "min_frames": 32, "max_frames": 32, "variance_threshold": 1.0})
    return render_terrain_poster(
        dem, cam.W, cam.H, cam.as_forge3d(), albedo_map=albedo, albedo_sampling="bilinear",
        spacing=tuple(cam.np.float32(v).item() for v in (cam.sx, cam.sz)),
        exaggeration=cam.z_scale,
        sun_azimuth_deg=float(cfg["sun"]["azimuth_deg"]),
        sun_elevation_deg=float(cfg["sun"]["elevation_deg"]),
        spp=int(smp["spp"]), min_frames=int(smp["min_frames"]),
        max_frames=int(smp["max_frames"]), variance_threshold=float(smp["variance_threshold"]),
        tile=1024)


# --------------------------------------------------------------------------
# cartography and composition
# --------------------------------------------------------------------------

def _font_path() -> str | None:
    try:
        import forge3d

        path = os.path.join(os.path.dirname(forge3d.__file__), "data", "fonts",
                            "NotoSansLatin-subset.ttf")
        return path if os.path.exists(path) else None
    except ImportError:
        return None


def _pil_font(size: int):
    from PIL import ImageFont

    path = _font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def draw_label(img, text: str, x: float, y: float, size: float, color=(17, 24, 39, 255),
               halo=(255, 255, 255, 230), halo_px: int = 3) -> None:
    """Label with halo through forge3d.text; Pillow stroke if the shaper is absent."""
    import numpy as np

    try:
        from forge3d.text import rasterize_shaped_run, shape

        shaped = shape(text, [_font_path()], float(size))
        b = shaped.outline_bounds()
        if b is None:
            return
        pad = halo_px + 2
        wd, ht = int(math.ceil(b[2] - b[0])) + 2 * pad, int(math.ceil(b[3] - b[1])) + 2 * pad
        mask = np.asarray(rasterize_shaped_run(shaped, wd, ht, origin=(pad - b[0], pad - b[1])),
                          dtype=np.float32)
        if mask.max() > 1.0:
            mask = mask / 255.0
        halo_m = mask.copy()
        for _ in range(halo_px):
            m = halo_m
            halo_m = np.maximum.reduce([m, np.roll(m, 1, 0), np.roll(m, -1, 0),
                                        np.roll(m, 1, 1), np.roll(m, -1, 1)])
        x0, y0 = int(x - wd / 2), int(y - ht / 2)
        arr = np.asarray(img).copy()
        h, w = arr.shape[:2]
        sx0, sy0 = max(0, -x0), max(0, -y0)
        x1, y1 = min(w, x0 + wd), min(h, y0 + ht)
        if x1 <= max(0, x0) or y1 <= max(0, y0):
            return
        region = arr[max(0, y0):y1, max(0, x0):x1, :3].astype(np.float32)
        hm = halo_m[sy0:sy0 + region.shape[0], sx0:sx0 + region.shape[1], None] * halo[3] / 255
        tm = mask[sy0:sy0 + region.shape[0], sx0:sx0 + region.shape[1], None] * color[3] / 255
        region = region * (1 - hm) + np.array(halo[:3]) * hm
        region = region * (1 - tm) + np.array(color[:3]) * tm
        arr[max(0, y0):y1, max(0, x0):x1, :3] = np.clip(region, 0, 255).astype(np.uint8)
        from PIL import Image

        img.paste(Image.fromarray(arr, img.mode))
    except Exception:  # noqa: BLE001  shaper unavailable: same label through Pillow
        from PIL import ImageDraw

        ImageDraw.Draw(img).text((x, y), text, font=_pil_font(int(size)), fill=color[:3],
                                 anchor="mm", stroke_width=halo_px, stroke_fill=halo[:3])


def footprint_mask(cam: Camera, dem):
    """Screen mask of the terrain: its perimeter, heights included, projected.

    render_terrain_poster returns opaque environment pixels where a ray misses,
    so the plate background is restored outside this outline.
    """
    from PIL import Image, ImageDraw, ImageFilter

    rows, cols = dem.shape
    step = max(1, max(rows, cols) // 400)
    edge = ([(0, c) for c in range(0, cols, step)] + [(r, cols - 1) for r in range(0, rows, step)]
            + [(rows - 1, c) for c in range(cols - 1, -1, -step)]
            + [(r, 0) for r in range(rows - 1, -1, -step)])
    pts = [cam.project(r, c, float(dem[r, c])) for r, c in edge]
    mask = Image.new("L", (cam.W, cam.H), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(0.8))


def _fit_font(text: str, size: int, width: int):
    font = _pil_font(size)
    while size > 10 and font.getlength(text) > width:
        size -= 1
        font = _pil_font(size)
    return font


def _rgba_to_pil(arr):
    from PIL import Image

    return Image.fromarray(arr.astype("uint8"), "RGBA")


def layout(W: int, H: int) -> dict:
    """Plate margins and bands; the map fills what is left."""
    m = max(16, W // 60)
    lay = {"m": m, "title_h": int(H * 0.055), "sub_h": int(H * 0.032), "foot_h": int(H * 0.085)}
    lay["map_top"] = m + lay["title_h"] + lay["sub_h"]
    lay["map_w"] = W - 2 * m
    lay["map_h"] = H - lay["map_top"] - lay["foot_h"] - m
    return lay


def size_for_map(map_px: int) -> tuple[int, int]:
    """Smallest plate whose map area is at least map_px square (probe legs)."""
    W = map_px + 2 * 16
    while layout(W, W)["map_w"] < map_px:
        W += 1
    H = map_px
    while layout(W, H)["map_h"] < map_px:
        H += 1
    return W, H


def _wrap(text: str, font, width: int) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if cur and font.getlength(trial) > width:
            out.append(cur)
            cur = word
        else:
            cur = trial
    return out + ([cur] if cur else [])


def compose(map_rgba, cam: Camera, cfg: dict, cases: list[dict], dem, area: dict,
            plan: dict, meta: dict, elev_domain, palette: dict, sha: str, preview: bool):
    """Pillow composition of the final plate around the rendered map."""
    import numpy as np
    from PIL import Image, ImageDraw

    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    lay = layout(W, H)
    m, title_h, sub_h, foot_h, map_top = (lay[k] for k in ("m", "title_h", "sub_h", "foot_h",
                                                              "map_top"))
    plate = Image.new("RGB", (W, H), PLATE_BG)
    d = ImageDraw.Draw(plate)
    title = cfg["title"] + (" (preview)" if preview else "")
    title_font = _fit_font(title, int(title_h * 0.62), W - 2 * m)
    sub_font = _fit_font(cfg.get("subtitle", ""), int(sub_h * 0.62), W - 2 * m)
    d.text((m, m), title, font=title_font, fill=(17, 24, 39))
    if cfg.get("subtitle"):
        d.text((m, m + title_h), cfg["subtitle"], font=sub_font, fill=(75, 85, 99))
    map_img = Image.fromarray(np.asarray(map_rgba)[..., :3].astype("uint8"), "RGB")
    plate.paste(Image.new("RGB", map_img.size, PLATE_BG), (m, map_top))
    plate.paste(map_img, (m, map_top), footprint_mask(cam, dem))

    # Screen-space overlays on the map region.
    mreg = plate.crop((m, map_top, m + cam.W, map_top + cam.H))
    md = ImageDraw.Draw(mreg)
    r_pin = max(4, cam.W // 260)
    rows, cols = dem.shape

    def elev(lon, lat):
        rr, cc = _grid_rc(lon, lat, plan["bbox"], rows, cols)
        return float(dem[int(min(rows - 1, max(0, round(rr)))), int(min(cols - 1, max(0, round(cc))))])

    def screen(lon, lat):
        rr, cc = _grid_rc(lon, lat, plan["bbox"], rows, cols)
        return cam.project(rr, cc, elev(lon, lat))

    if cfg["overlays"]["pins"]:
        order = {t: i for i, t in enumerate(("pending", "mixed", "advanced_unverified",
                                             "blocked_unverified", "advanced_confirmed",
                                             "restricted_conditional", "blocked_confirmed"))}
        for c in sorted(cases, key=lambda c: order.get(c["outcome"], 0)):
            x, y = screen(c["lon"], c["lat"])
            md.ellipse([x - r_pin, y - r_pin, x + r_pin, y + r_pin],
                       fill=_hex_rgb(palette["outcome"][c["outcome"]]),
                       outline=(11, 15, 20), width=max(1, r_pin // 4))
    if cfg["overlays"]["labels"] and area["polygons"] and len(area["names"]) <= 6:
        for poly, name in zip(area["polygons"], area["names"]):
            ring = poly[0]
            lon = sum(p[0] for p in ring) / len(ring)
            lat = sum(p[1] for p in ring) / len(ring)
            x, y = screen(lon, lat)
            draw_label(mreg, f"{name} County", x, y - 3 * r_pin, max(14, cam.W // 55))
    site = cfg["overlays"].get("site")
    if site:
        x, y = screen(float(site["lon"]), float(site["lat"]))
        s = r_pin * 2
        md.rectangle([x - s, y - s, x + s, y + s], fill=SITE_RGB, outline=(11, 15, 20),
                     width=max(2, r_pin // 2))
        draw_label(mreg, site["label"], x, y + 3 * s, max(13, cam.W // 65))

    # forge3d open-core cartography: ScaleBar, NorthArrow, elevation Legend.
    from forge3d.legend import Legend, LegendConfig
    from forge3d.north_arrow import NorthArrow, NorthArrowConfig
    from forge3d.scale_bar import ScaleBar, ScaleBarConfig

    k = max(1.0, cam.W / 1400)
    sb = ScaleBar(cam.meters_per_pixel(), ScaleBarConfig(
        units="mi", width_px=int(200 * k), height_px=int(30 * k), font_size=int(13 * k),
        bar_height=int(8 * k), padding=int(8 * k)))
    sb_img = _rgba_to_pil(sb.render())
    na = NorthArrow(NorthArrowConfig(size=int(64 * k), font_size=int(14 * k),
                                     rotation_deg=cam.north_rotation_deg()))
    na_img = _rgba_to_pil(na.render())
    ramp = np.linspace(0, 1, 64)[:, None]
    cm = np.ones((64, 4), np.float32)
    cm[:, :3] = _srgb_to_linear(TINT_LOW) * (1 - ramp) + _srgb_to_linear(TINT_HIGH) * ramp
    lg = Legend(cm, (round(elev_domain[0]), round(elev_domain[1])), LegendConfig(
        orientation="vertical", bar_width=int(14 * k), bar_height=int(90 * k), tick_count=3,
        font_size=int(12 * k), title="Elevation", title_font_size=int(13 * k),
        background=(255, 255, 255, 215)))
    lg_img = _rgba_to_pil(lg.render())
    pad = int(14 * k)
    mreg.paste(na_img, (cam.W - na_img.width - pad, pad), na_img)
    mreg.paste(sb_img, (cam.W - sb_img.width - pad, cam.H - sb_img.height - pad), sb_img)

    # Outcome legend (categorical, Pillow): only tiers present, with counts.
    counts = Counter(c["outcome"] for c in cases)
    if counts and cfg["overlays"]["pins"]:
        lf = _pil_font(int(14 * k))
        rowh = int(22 * k)
        items = [t for t in OUTCOME_TERMS if counts.get(t)]
        box_w = int(260 * k)
        box_h = rowh * (len(items) + 1) + pad
        box = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 215))
        bd = ImageDraw.Draw(box)
        bd.text((pad // 2, pad // 3), "Tracked cases (outcome)", font=lf, fill=(17, 24, 39))
        for i, t in enumerate(items, 1):
            cy = i * rowh + rowh // 2
            bd.ellipse([pad // 2, cy - r_pin, pad // 2 + 2 * r_pin, cy + r_pin],
                       fill=_hex_rgb(palette["outcome"][t]), outline=(11, 15, 20))
            bd.text((pad + 2 * r_pin, cy), f"{OUTCOME_LABEL[t]} ({counts[t]})", font=lf,
                    fill=(17, 24, 39), anchor="lm")
        mreg.paste(box, (pad, cam.H - box_h - lg_img.height - 2 * pad), box)
    mreg.paste(lg_img, (pad, cam.H - lg_img.height - pad), lg_img)
    plate.paste(mreg, (m, map_top))

    # Footer: sources, vintage, SHA, disclosures.
    lines = []
    if not cases:
        lines.append("No tracked cases fall inside this area.")
    z = float(cfg["camera"]["z_scale"])
    if z > Z_DISCLOSE:
        lines.append(f"Relief is exaggerated {z:g}x vertically.")
    lines.append("Pins show the graded outcome of the opposed project or measure; unverified "
                 "tiers are as recorded. Scale applies left to right.")
    lines.append(" ".join([CREDIT_3DEP.format(product=meta["product_name"],
                                              accessed=meta.get("accessed", "unknown")),
                           CREDIT_CENSUS]))
    lines.append(" ".join([CREDIT_CASES.format(sha=sha[:12]), CREDIT_FONT,
                           f"Rendered with forge3d open core; plate {cfg['id']}"
                           f"{' preview' if preview else ''}."]))
    size = max(7, int(foot_h / 5.5))
    while True:
        foot_font = _pil_font(size)
        wrapped = [w for ln in lines for w in _wrap(ln, foot_font, W - 2 * m)]
        if len(wrapped) * size * 1.3 <= foot_h + m * 0.6 or size <= 7:
            break
        size -= 1
    fy = map_top + cam.H + max(4, m // 3)
    for i, ln in enumerate(wrapped):
        d.text((m, fy + i * int(size * 1.3)), ln, font=foot_font, fill=(75, 85, 99))
    return plate, lines


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _versions() -> dict:
    out = {"python": platform.python_version()}
    for mod in ("forge3d", "numpy", "PIL", "rasterio"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except ImportError:
            pass
    return out


def adapter_info() -> dict:
    try:
        import forge3d

        backend = os.environ.get("WGPU_BACKENDS") or os.environ.get("WGPU_BACKEND")
        probe = forge3d.device_probe(backend) if backend else forge3d.device_probe()
        return {k: probe.get(k) for k in ("status", "name", "backend", "device_type", "driver",
                                          "driver_info", "software_fallback", "reason")
                if k in probe} | {"wgpu_backends_env": backend or ""}
    except Exception as exc:  # noqa: BLE001
        return {"status": "probe_error", "reason": repr(exc)}


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def plate_paths(cfg: dict, out_dir: str, preview: bool) -> tuple[str, str]:
    stem = cfg["id"] + ("-preview" if preview else "")
    return os.path.join(out_dir, stem + ".png"), os.path.join(out_dir, stem + ".json")


def run(cfg_path: str, *, preview: bool = False, out_dir: str | None = None,
        cache_dir: str | None = None, render=None, fetch=None, sha: str | None = None,
        size: int | None = None, allow_uncommitted: bool = False, fetch_only: bool = False,
        root: str = ROOT) -> dict:
    import numpy as np

    t_all = time.time()
    cfg = load_config(cfg_path)
    inputs = [TOPOJSON, CASES, PALETTE_JS]
    rel_cfg = os.path.relpath(os.path.abspath(cfg_path), root)
    if sha is None:
        sha = eg.commit_sha(inputs + [rel_cfg], root=root, allow_uncommitted=allow_uncommitted)
    palette = eg.read_palette(os.path.join(root, PALETTE_JS))
    area = resolve_area(cfg, os.path.join(root, TOPOJSON))
    plan = dem_plan(area["bbox"], cfg)
    t0 = time.time()
    dem, meta = load_or_fetch_dem(plan, cache_dir or os.path.join(root, DEM_CACHE), fetch)
    t_dem = time.time() - t0
    if fetch_only:
        return {"dem": meta, "seconds": round(t_dem, 2)}
    cases = area_cases(eg.read_csv(os.path.join(root, CASES)), area) if cfg["overlays"]["pins"] else []

    if size:
        cfg["output"]["width"], cfg["output"]["height"] = size_for_map(int(size))
        map_w = map_h = int(size)
    else:
        lay = layout(int(cfg["output"]["width"]), int(cfg["output"]["height"]))
        map_w, map_h = lay["map_w"], lay["map_h"]
    cam = Camera(dem, plan, cfg, map_w, map_h)
    albedo, elev_domain = build_albedo(dem, area, plan, cfg)
    t0 = time.time()
    result = (render or render_terrain)(dem, albedo, cam, cfg, preview)
    t_render = time.time() - t0
    rgba = np.asarray(result["rgba"])
    lum = rgba[..., :3].astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    plate, footer = compose(rgba, cam, cfg, cases, dem, area, plan, meta, elev_domain,
                            palette, sha, preview)

    out_dir = out_dir or os.path.join(root, OUT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    png, side = plate_paths(cfg, out_dir, preview)
    buf = io.BytesIO()
    plate.save(buf, "PNG", dpi=(300, 300))
    with open(png, "wb") as fh:
        fh.write(buf.getvalue())
    sidecar = {
        "id": cfg["id"], "title": cfg["title"], "subtitle": cfg.get("subtitle", ""),
        "preview": preview, "png": os.path.relpath(png, root),
        "size": [plate.width, plate.height], "map_size": [map_w, map_h],
        "commit_sha": sha,
        "generator": "scripts/render_terrain_plate.py",
        "inputs": [{"path": p, "sha256": _sha256(os.path.join(root, p))}
                   for p in inputs + [rel_cfg]],
        "versions": _versions(),
        "dem": {k: meta.get(k) for k in ("product", "product_name", "sources", "accessed",
                                          "bbox", "rows", "cols", "spacing_m", "cache")},
        "camera": cfg["camera"], "sun": cfg["sun"],
        "samples": "preview" if preview else cfg["output"]["samples"],
        "adapter": adapter_info() if render is None else {"status": "mocked"},
        "cases": {"count": len(cases), "by_outcome": dict(Counter(c["outcome"] for c in cases))},
        "footer": footer,
        "credits": [ln for ln in footer if ln.startswith(("Elevation", "Cases"))],
        "map_luminance_std": round(float(lum.std()), 3),
        "seconds": {"dem": round(t_dem, 2), "render": round(t_render, 2),
                    "total": round(time.time() - t_all, 2)},
    }
    with open(side, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(sidecar, fh, indent=2)
        fh.write("\n")
    return sidecar


def run_probe(cfg_path: str, leg: str, size: int, out_dir: str, cache_dir: str | None) -> int:
    """One plate-probe leg: render at size x size, log adapter and timing."""
    os.makedirs(out_dir, exist_ok=True)
    rec = {"leg": leg, "runner_os": platform.system(), "adapter": adapter_info(),
           "size": size, "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    print("adapter:", json.dumps(rec["adapter"]))
    t0 = time.time()
    try:
        side = run(cfg_path, preview=True, out_dir=out_dir, cache_dir=cache_dir, size=size,
                   allow_uncommitted=True)
        rec.update({"status": "rendered", "seconds": round(time.time() - t0, 2),
                    "render_seconds": side["seconds"]["render"],
                    "map_luminance_std": side["map_luminance_std"], "png": side["png"],
                    "commit_sha": side["commit_sha"]})
        os.replace(os.path.join(ROOT, side["png"]), os.path.join(out_dir, f"{leg}.png"))
        rec["png"] = os.path.join(out_dir, f"{leg}.png")
    except Exception as exc:  # noqa: BLE001  any failure is the leg's result
        rec.update({"status": "failed", "seconds": round(time.time() - t0, 2),
                    "reason": f"{type(exc).__name__}: {exc}"[:2000]})
    with open(os.path.join(out_dir, f"{leg}.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, indent=2)
        fh.write("\n")
    print(json.dumps(rec, indent=2))
    return 0


def decide(records: list[dict], timeout_s: float = 600.0) -> dict:
    """The spec 013 US4 decision rule over probe leg records."""
    by_leg = {r.get("leg"): r for r in records}
    legs = []
    chosen = None
    for leg in PROBE_LEG_ORDER:
        r = by_leg.get(leg)
        if r is None:
            legs.append({"leg": leg, "pass": False, "why": "no record (leg did not report)"})
            continue
        ok = (r.get("status") == "rendered" and float(r.get("seconds", 1e9)) < timeout_s
              and float(r.get("map_luminance_std", 0)) > PROBE_MIN_STD)
        why = ("non-blank render inside timeout" if ok else
               r.get("reason") or f"status={r.get('status')} std={r.get('map_luminance_std')} "
                                  f"seconds={r.get('seconds')}")
        legs.append({"leg": leg, "pass": ok, "why": why,
                     "adapter": (r.get("adapter") or {}).get("name"),
                     "backend": (r.get("adapter") or {}).get("backend"),
                     "seconds": r.get("seconds")})
        if ok and chosen is None:
            chosen = leg
    return {"route": f"ci:{chosen}" if chosen else "fallback:local-render",
            "chosen_leg": chosen, "legs": legs,
            "next": ("measure a full print render on this leg; render-plates.yml applies"
                     if chosen else "every leg failed: render locally and gate lock files")}


def _selftest() -> int:
    import numpy as np

    checks: list[tuple[str, bool]] = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    def raises(fn, exc=PlateError) -> bool:
        try:
            fn()
        except exc:
            return True
        return False

    base = {"id": "t", "title": "Three tracked cases sit on the ridge", "area": {"fips": "13255"}}
    ck("valid config passes", validate_config(json.loads(json.dumps(base)))["id"] == "t")
    ck("config with no title fails", raises(lambda: validate_config({"area": {"fips": "13255"}})))
    ck("scorekeeping title fails", raises(lambda: validate_config(
        dict(base, title="Residents won the zoning fight here"))))
    ck("em-dash title fails", raises(lambda: validate_config(
        dict(base, title="Three cases \u2014 one county here"))))
    ck("fips and bbox together fail", raises(lambda: validate_config(
        dict(base, area={"fips": "13255", "bbox": [-85, 33, -84, 34]}))))
    ck("global DEM refused", raises(lambda: validate_config(dict(base, dem={"product": "srtm"}))))

    plan = dem_plan([-84.45, 33.13, -84.12, 33.36], validate_config(json.loads(json.dumps(base))))
    ck("small area uses 1/3 arc-second", plan["product"] == "13")
    ck("tile named by north-west corner", plan["sources"][0].endswith("USGS_13_n34w085.tif"))
    big = dem_plan([-86.2, 32.1, -84.1, 34.4], validate_config(json.loads(json.dumps(base))))
    ck("large area uses 1 arc-second and several tiles",
       big["product"] == "1" and len(big["sources"]) == 9)

    tmp = tempfile.mkdtemp(prefix="plate_selftest_")
    topo = {"type": "Topology", "transform": {"scale": [0.001, 0.001], "translate": [-84.45, 33.13]},
            "arcs": [[[0, 0], [330, 0], [0, 230], [-330, 0], [0, -230]]],
            "objects": {"counties": {"type": "GeometryCollection", "geometries": [
                {"type": "Polygon", "id": "13255", "arcs": [[0]], "properties": {"name": "Spalding"}}]}}}
    os.makedirs(os.path.join(tmp, "data", "geo"))
    os.makedirs(os.path.join(tmp, "configs", "plates"))
    with open(os.path.join(tmp, TOPOJSON), "w") as fh:
        json.dump(topo, fh)
    with open(os.path.join(ROOT, PALETTE_JS), encoding="utf-8") as fh:
        pal_js = fh.read()
    with open(os.path.join(tmp, PALETTE_JS), "w", encoding="utf-8") as fh:
        fh.write(pal_js)
    head = "project_id,outcome_defensible,lat,lon,map_pinnable\n"
    rows_in = ["p1,pending,33.25,-84.28,True", "p2,blocked_confirmed,33.2,-84.3,True"]
    rows_out = ["p3,advanced_confirmed,35.0,-80.0,True", "p4,pending,33.21,-84.29,False"]

    def write_cases(rows):
        with open(os.path.join(tmp, CASES), "w", encoding="utf-8") as fh:
            fh.write(head + "\n".join(rows) + "\n")

    write_cases(rows_in + rows_out)
    cfg_path = os.path.join(tmp, "configs", "plates", "t.json")
    cfg = dict(base, output={"width": 640, "height": 420}, camera={"z_scale": 2.0})
    with open(cfg_path, "w") as fh:
        json.dump(cfg, fh)

    calls = {"fetch": 0}

    def fake_fetch(p):
        calls["fetch"] += 1
        yy, xx = np.mgrid[0:p["rows"], 0:p["cols"]]
        return (200 + 40 * np.sin(xx / 20.0) * np.cos(yy / 25.0)).astype(np.float32)

    def no_network(p):
        raise AssertionError("network used with a warm cache")

    def fake_render(dem, albedo, cam, cfg, preview):
        img = np.zeros((cam.H, cam.W, 4), np.uint8)
        img[..., :3] = (np.linspace(60, 220, cam.W)[None, :, None]).astype(np.uint8)
        img[..., 3] = 255
        return {"rgba": img}

    cache = os.path.join(tmp, "cache")
    out = os.path.join(tmp, "out")
    side = run(cfg_path, cache_dir=cache, out_dir=out, render=fake_render, fetch=fake_fetch,
               sha="f" * 40, root=tmp)
    ck("cold cache fetches once", calls["fetch"] == 1 and side["dem"]["cache"] == "cold")
    from PIL import Image

    png = os.path.join(tmp, side["png"])
    ck("png exists at the requested size", Image.open(png).size == (640, 420))
    ck("sidecar lists every input",
       {i["path"] for i in side["inputs"]} == {TOPOJSON, CASES, PALETTE_JS,
                                               os.path.relpath(cfg_path, tmp)})
    ck("footer carries the SHA", any("f" * 12 in ln for ln in side["footer"]))
    ck("sidecar records the DEM product and access date",
       side["dem"]["product_name"].startswith("3DEP") and side["dem"]["accessed"])
    ck("z-scale above 1.5 disclosed", any("exaggerated 2x" in ln for ln in side["footer"]))
    ck("only pinnable cases inside the area are drawn",
       side["cases"] == {"count": 2, "by_outcome": {"pending": 1, "blocked_confirmed": 1}})
    side2 = run(cfg_path, cache_dir=cache, out_dir=out, render=fake_render, fetch=no_network,
                sha="f" * 40, root=tmp)
    ck("warm cache makes no network request", side2["dem"]["cache"] == "warm")

    write_cases(rows_in + ["p9,won,33.22,-84.3,True"])
    ck("outcome outside the vocabulary fails", raises(lambda: run(
        cfg_path, cache_dir=cache, out_dir=out, render=fake_render, fetch=no_network,
        sha="f" * 40, root=tmp)))
    write_cases(rows_out)
    side3 = run(cfg_path, cache_dir=cache, out_dir=out, render=fake_render, fetch=no_network,
                sha="f" * 40, root=tmp)
    ck("no pins: footer says so", side3["cases"]["count"] == 0 and any(
        "No tracked cases" in ln for ln in side3["footer"]))
    side4 = run(cfg_path, preview=True, cache_dir=cache, out_dir=out, render=fake_render,
                fetch=no_network, sha="f" * 40, root=tmp)
    ck("preview is labelled in filename and sidecar",
       side4["png"].endswith("t-preview.png") and side4["preview"] is True)

    def failing_fetch(p):
        raise OSError("connection refused")

    try:
        run(cfg_path, cache_dir=os.path.join(tmp, "cold2"), out_dir=out, render=fake_render,
            fetch=failing_fetch, sha="f" * 40, root=tmp)
        ck("unreachable 3DEP fails with cache path and retry hint", False)
    except PlateError as e:
        ck("unreachable 3DEP fails with cache path and retry hint",
           "cold2" in str(e) and "Retry" in str(e))

    cam = Camera(np.zeros((100, 200), np.float32), {"spacing_m": [2.0, 3.0]},
                 {"camera": {"azimuth_deg": 0, "elevation_deg": 90, "z_scale": 1}}, 300, 200)
    x0, y0 = cam.project(0, 100, 0)
    x1, y1 = cam.project(99, 100, 0)
    ck("north-up camera puts row 0 above row 99", y0 < y1 and abs(x0 - x1) < 1e-6)
    ck("north arrow points up for a north-facing camera", abs(cam.north_rotation_deg()) < 1e-6)

    recs = [{"leg": "A", "status": "failed", "reason": "no adapter"},
            {"leg": "B", "status": "rendered", "seconds": 40, "map_luminance_std": 30.0},
            {"leg": "C", "status": "rendered", "seconds": 30, "map_luminance_std": 30.0}]
    dec = decide(recs)
    ck("decision prefers the first passing leg in A-D order", dec["chosen_leg"] == "B")
    ck("blank render does not pass", decide([{"leg": "A", "status": "rendered", "seconds": 5,
                                              "map_luminance_std": 0.5}])["chosen_leg"] is None)
    ck("over-timeout render does not pass", decide([{"leg": "A", "status": "rendered",
                                                     "seconds": 700, "map_luminance_std": 30}])
       ["route"] == "fallback:local-render")

    fails = 0
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    print(f"{len(checks) - fails}/{len(checks)} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config")
    ap.add_argument("--preview", action="store_true", help="reduced samples, labelled preview")
    ap.add_argument("--fetch-dem-only", action="store_true")
    ap.add_argument("--out-dir")
    ap.add_argument("--cache-dir")
    ap.add_argument("--allow-uncommitted", action="store_true",
                    help="stamp the SHA -uncommitted instead of failing on new inputs")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--leg", default="local")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--probe-decide", metavar="DIR")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if a.probe_decide:
        recs = []
        for dirpath, _, files in os.walk(a.probe_decide):
            for fn in sorted(files):
                if fn.endswith(".json"):
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                        rec = json.load(fh)
                    if "leg" in rec:
                        recs.append(rec)
        dec = decide(recs, a.timeout)
        print(json.dumps(dec, indent=2))
        return 0
    if not a.config:
        ap.error("--config is required")
    try:
        if a.probe:
            return run_probe(a.config, a.leg, a.size, a.out_dir or P(PROBE_DIR), a.cache_dir)
        side = run(a.config, preview=a.preview, out_dir=a.out_dir, cache_dir=a.cache_dir,
                   allow_uncommitted=a.allow_uncommitted, fetch_only=a.fetch_dem_only)
    except (PlateError, eg.ExportError) as e:
        print(f"PLATE FAILED: {e}", file=sys.stderr)
        return 1
    print(json.dumps(side, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
