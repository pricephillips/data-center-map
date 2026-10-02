#!/usr/bin/env python3
"""
scripts/export_geolibre.py

Builds GeoLibre map projects (.geolibre.json) from committed platform files
(spec 012 US2). One command writes four projects an analyst can open in the
GeoLibre desktop app or Jupyter widget without hand styling. Nothing is
uploaded anywhere: the projects are local files, and the public sharing host
is forbidden by configs/integrations.json (entry geolibre-share) and by
integration_audit.py.

Projects
  national_restriction_model   county choropleth of calibrated_score deciles
  national_opposition_cases    opposition pins colored by outcome_defensible
  score_vs_politics_swipe      score choropleth | 2024 presidential margin
  opposition_timeline          pins carrying a verified action year

Rules
  - Inputs are tracked files only; an untracked input fails, a modified one is
    recorded with a "-dirty" SHA suffix. The commit SHA goes in every
    project's description and metadata.
  - Popup and tooltip fields come only from configs/geolibre_export.json.
    Group-level outcome columns and site_screener.py composite fields are
    refused even if the allowlist names them.
  - Outcome colors come from the platform vocabulary only. A row carrying any
    other value fails the export rather than inventing a color class.
  - No heatmap, density or extrusion renderer is ever set.
  - Colors are read from viz-palette.js (inferno floored at t=0.10, fill
    opacity 0.88, OUTCOME_COLOR, the diverging margin pair).

Reads
  data/geo/counties_2024.topojson, data/county_policy_scores.csv,
  data/county_aggregate.csv (county and state names), data/county_votes.json,
  data/features/features_manifest.json (political promotion state),
  master_opposition_clean.csv, configs/geolibre_export.json, viz-palette.js
Writes
  data/geolibre/<project>.geolibre.json

Usage
  python scripts/export_geolibre.py              write all four projects
  python scripts/export_geolibre.py --only national_opposition_cases
  python scripts/export_geolibre.py --selftest   fixture run, no repo writes
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def P(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


TOPOJSON = "data/geo/counties_2024.topojson"
SCORES = "data/county_policy_scores.csv"
AGGREGATE = "data/county_aggregate.csv"
VOTES = "data/county_votes.json"
FEATURES_MANIFEST = "data/features/features_manifest.json"
CASES = "master_opposition_clean.csv"
EXPORT_CONFIG = "configs/geolibre_export.json"
PALETTE_JS = "viz-palette.js"
OUT_DIR = "data/geolibre"

PROJECTS = (
    "national_restriction_model",
    "national_opposition_cases",
    "score_vs_politics_swipe",
    "opposition_timeline",
)

# outcome_defensibility.OUTCOME_GRADES, the platform vocabulary. Kept as a
# literal (not imported) so this script runs without the pipeline's
# dependencies; --selftest checks the two agree.
OUTCOME_TERMS = (
    "advanced_confirmed", "restricted_conditional", "blocked_confirmed", "pending",
    "blocked_unverified", "advanced_unverified", "mixed",
)
OUTCOME_LABEL = {
    "advanced_confirmed": "Advanced (confirmed)",
    "restricted_conditional": "Restricted (conditional)",
    "blocked_confirmed": "Blocked (confirmed)",
    "pending": "Pending",
    "blocked_unverified": "Blocked (unverified)",
    "advanced_unverified": "Advanced (unverified)",
    "mixed": "Mixed",
}

# Never exported, whatever the allowlist says. Group-level outcome columns
# (decided, confirmed_blocks, blocked_share) and every column site_screener.py
# writes into its composite (data/site_screen.csv).
GROUP_LEVEL = {"decided", "confirmed_blocks", "blocked_share"}
SCREENER_COMPOSITE = {
    "tier", "composite", "local_activity", "local_enacted", "county_model",
    "state_activity", "org_capacity", "pct_local_activity", "pct_local_enacted",
    "pct_county_model", "pct_state_activity", "pct_org_capacity",
    "events_within_50mi",
}

# Coordinates are rounded to 4 decimals (about 11 m), well under the 1:5m
# boundary's own generalization, which keeps each county layer near 3 MB.
COORD_DECIMALS = 4

CREDITS = {
    "census": "County boundaries: U.S. Census Bureau, 2024 cartographic boundary file 1:5m (public domain).",
    "scores": "County scores: this platform's county policy model (data/county_policy_scores.csv).",
    "cases": "Opposition cases: this platform's opposition tracker (master_opposition_clean.csv), sourced records.",
    "medsl": ("Presidential margin: MIT Election Data and Science Lab, County Presidential "
              "Election Returns 2000-2024, Harvard Dataverse, doi:10.7910/DVN/VOQCHQ."),
    "tonmcg": ("Presidential margin: tonmcg, US_County_Level_Election_Results_08-24 (MIT licence; "
               "compiled from Townhall.com 2016 and Fox News 2024 results; the compiler notes it "
               "is not an authoritative source)."),
}

DEFINITIONS = (
    "The calibrated score is a resemblance measure: how closely a county's "
    "measured characteristics resemble those of counties that have already "
    "enacted a data center restriction, rescaled so that it reads as a share "
    "(calibration means the scores were adjusted so that, across many counties, "
    "a score of 0.2 corresponds to roughly one in five such counties having "
    "enacted one). It is not a forecast that a county will act. A decile is one "
    "of ten equal-sized groups after sorting all scored counties by score: "
    "decile 1 is the lowest tenth, decile 10 the highest."
)


class ExportError(RuntimeError):
    """Raised for any input the export refuses to paper over."""


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def _git(*args: str, root: str = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          timeout=30)


def commit_sha(inputs: list[str], root: str = ROOT, allow_uncommitted: bool = False) -> str:
    """HEAD SHA; fails on an untracked input, suffixes -dirty on a modified one.

    allow_uncommitted turns the untracked failure into an -uncommitted suffix,
    for a pre-commit preview only; the committed export is re-run after commit.
    """
    head = _git("rev-parse", "HEAD", root=root)
    if head.returncode != 0:
        raise ExportError("not a git checkout: the commit SHA cannot be recorded")
    sha = head.stdout.strip()
    dirty = untracked = False
    for rel in inputs:
        if _git("ls-files", "--error-unmatch", rel, root=root).returncode != 0:
            if not allow_uncommitted:
                raise ExportError(f"input is not a committed file: {rel}")
            untracked = True
        elif _git("diff", "--quiet", "HEAD", "--", rel, root=root).returncode != 0:
            dirty = True
    return sha + ("-uncommitted" if untracked else "-dirty" if dirty else "")


# --------------------------------------------------------------------------
# palette (viz-palette.js is the single source)
# --------------------------------------------------------------------------

def read_palette(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        js = fh.read()

    def grab(pattern: str) -> str:
        m = re.search(pattern, js, re.S)
        if not m:
            raise ExportError(f"viz-palette.js: pattern not found: {pattern}")
        return m.group(1)

    inferno = [tuple(int(v) for v in t) for t in
               re.findall(r"\[(\d+),\s*(\d+),\s*(\d+)\]", grab(r"var INFERNO = \[(.*?)\];"))]
    outcome = dict(re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'",
                              grab(r"var OUTCOME_COLOR = \{(.*?)\};")))

    def rgb(name: str) -> tuple[int, int, int]:
        return tuple(int(v) for v in grab(rf"var {name} = \[([\d,\s]+)\]").split(","))

    return {
        "inferno": inferno,
        "seq_floor": float(grab(r"var SEQ_FLOOR = ([\d.]+);")),
        "fill_opacity": float(grab(r"var FILL_OPACITY = ([\d.]+);")),
        "outcome": outcome,
        "div_neg": rgb("DIV_NEG"), "div_pos": rgb("DIV_POS"), "div_mid": rgb("DIV_MID"),
    }


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c)


def _lerp(a, b, f):
    return [a[i] + (b[i] - a[i]) * f for i in range(3)]


def sequential(pal: dict, t: float) -> str:
    """Same as VizPalette.sequential: inferno, floored at SEQ_FLOOR."""
    t = min(1.0, max(0.0, t))
    x = (pal["seq_floor"] + (1 - pal["seq_floor"]) * t) * (len(pal["inferno"]) - 1)
    i = min(len(pal["inferno"]) - 2, int(math.floor(x)))
    return _hex([round(v) for v in _lerp(pal["inferno"][i], pal["inferno"][i + 1], x - i)])


def diverging(pal: dict, t: float) -> str:
    """Same as VizPalette.diverging, t in [-1, 1]."""
    v = max(-1.0, min(1.0, t))
    end = pal["div_pos"] if v >= 0 else pal["div_neg"]
    return _hex([round(c) for c in _lerp(pal["div_mid"], end, abs(v))])


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def topojson_features(topo: dict, obj: str = "counties") -> list[dict]:
    """Decodes one TopoJSON GeometryCollection into GeoJSON features."""
    tr = topo.get("transform")
    arcs = []
    for arc in topo["arcs"]:
        pts, x, y = [], 0, 0
        for p in arc:
            if tr:
                x, y = x + p[0], y + p[1]
                pts.append([round(x * tr["scale"][0] + tr["translate"][0], COORD_DECIMALS),
                            round(y * tr["scale"][1] + tr["translate"][1], COORD_DECIMALS)])
            else:
                pts.append([round(p[0], COORD_DECIMALS), round(p[1], COORD_DECIMALS)])
        arcs.append(pts)

    def ring(idx: list[int]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in idx:
            pts = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(pts if not out else pts[1:])
        return out

    feats = []
    for g in topo["objects"][obj]["geometries"]:
        if g["type"] == "Polygon":
            geom = {"type": "Polygon", "coordinates": [ring(r) for r in g["arcs"]]}
        elif g["type"] == "MultiPolygon":
            geom = {"type": "MultiPolygon",
                    "coordinates": [[ring(r) for r in poly] for poly in g["arcs"]]}
        else:
            continue
        feats.append({"type": "Feature", "id": str(g.get("id")),
                      "properties": dict(g.get("properties") or {}), "geometry": geom})
    return feats


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    refused = GROUP_LEVEL | SCREENER_COMPOSITE | set(cfg.get("refused_columns", []))
    for block in ("pins", "counties", "margin"):
        names = [f["field"] for f in cfg[block]["popup"]] + list(cfg[block]["tooltip"])
        bad = sorted({n for n in names if n.strip().lower() in refused})
        if bad:
            raise ExportError(f"{path}: {block} allowlist names refused column(s): {bad}")
        extra = sorted(set(cfg[block]["tooltip"]) - {f["field"] for f in cfg[block]["popup"]})
        if extra:
            raise ExportError(f"{path}: {block} tooltip fields not in its popup: {extra}")
    return cfg


def political_source(manifest_path: str) -> str:
    """'medsl' once data/features/features_manifest.json records promotion."""
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return "tonmcg"
    promo = (((m.get("sources") or {}).get("political") or {}).get("info") or {}).get("promotion")
    return "medsl" if str(promo).lower() in {"promoted", "true", "medsl"} else "tonmcg"


def county_names(rows: list[dict]) -> dict[str, tuple[str, str]]:
    out = {}
    for r in rows:
        name = (r.get("county_name") or "").split(",")[0].strip()
        out[r["fips"].zfill(5)] = (name, r.get("state", ""))
    return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean_text(v: str) -> str:
    """Source text as carried into a project; em-dashes become spaced hyphens."""
    return re.sub(r"\s*\u2014\s*", " - ", v).strip()


def _iso_date(v: str) -> date | None:
    try:
        return date.fromisoformat((v or "").strip()[:10])
    except ValueError:
        return None


def case_features(rows: list[dict], cfg: dict, *, require_date: bool = False) -> tuple[list[dict], Counter]:
    """Pins from clean rows; returns (features, exclusion counts)."""
    keep = [f["field"] for f in cfg["pins"]["popup"]]
    missing = [k for k in keep if rows and k not in rows[0]]
    if missing:
        raise ExportError(f"{CASES}: allowlisted column(s) absent: {missing}")
    planted = sorted(c for c in (rows[0] if rows else {})
                     if c.strip().lower() in GROUP_LEVEL and c in keep)
    if planted:
        raise ExportError(f"refusing group-level outcome column(s): {planted}")
    excluded: Counter = Counter()
    feats = []
    for r in rows:
        outcome = (r.get("outcome_defensible") or "").strip()
        if outcome not in OUTCOME_TERMS:
            raise ExportError(
                f"outcome {outcome!r} on {r.get('project_id') or r.get('Project Name')!r} "
                f"is not a platform term {OUTCOME_TERMS}")
        if str(r.get("map_pinnable", "")).strip() != "True":
            excluded["not_pinnable"] += 1
            continue
        lat, lon = _num(r.get("lat")), _num(r.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            excluded["no_coordinates"] += 1
            continue
        props = {k: _clean_text(r.get(k) or "") for k in keep}
        if require_date:
            d = _iso_date(r.get("Date", ""))
            if (d is None or str(r.get("date_parseable")) != "True"
                    or str(d.year) != str(r.get("action_year", "")).strip()):
                excluded["no_verified_date"] += 1
                continue
            props["action_year"] = d.year
            props["action_date"] = d.isoformat()
        feats.append({"type": "Feature",
                      "properties": props,
                      "geometry": {"type": "Point",
                                   "coordinates": [round(lon, 5), round(lat, 5)]}})
    return feats, excluded


# --------------------------------------------------------------------------
# project building
# --------------------------------------------------------------------------

def _description(parts: list[str], sha: str, credits: list[str]) -> str:
    return " ".join(parts + ["Sources: " + " ".join(credits),
                             f"Built by scripts/export_geolibre.py from commit {sha}."])


def _popup_fields(block: dict, popup_field) -> list[dict]:
    hover = set(block["tooltip"])
    return [popup_field(f["field"], label=f.get("label"), hover=f["field"] in hover)
            for f in block["popup"]]


def build_projects(paths: dict, sha: str, only: set[str] | None = None) -> dict[str, dict]:
    from geolibre import authoring as A
    from geolibre import project as GP

    pal = read_palette(paths["palette"])
    cfg = load_config(paths["config"])
    for term in OUTCOME_TERMS:
        if term not in pal["outcome"]:
            raise ExportError(f"viz-palette.js OUTCOME_COLOR has no color for {term}")

    with open(paths["topojson"], encoding="utf-8") as fh:
        geo = {f["id"]: f for f in topojson_features(json.load(fh))}
    scores = read_csv(paths["scores"])
    names = county_names(read_csv(paths["aggregate"]))
    missing = sorted(r["fips"].zfill(5) for r in scores if r["fips"].zfill(5) not in geo)
    if missing:
        raise ExportError(f"{len(missing)} scored FIPS missing from the geometry: {missing}")

    score_feats = []
    for r in scores:
        fips = r["fips"].zfill(5)
        cname, st = names.get(fips, (geo[fips]["properties"].get("name", ""), ""))
        score_feats.append({
            "type": "Feature", "id": fips, "geometry": geo[fips]["geometry"],
            "properties": {
                "fips": fips, "county": cname, "state": st,
                "calibrated_score": _num(r["calibrated_score"]),
                "score_decile": int(r["score_decile"]),
                "has_enacted_restrictive": "yes" if r["has_enacted_restrictive"] == "1" else "no",
            }})

    # Decile classes: the GeoLibre graduated builder supplies the structure,
    # the canonical palette supplies the colors.
    deciles = [f["properties"]["score_decile"] for f in score_feats]
    frag = A.build_choropleth_style(deciles, "score_decile", class_count=10, colormap="inferno")
    for i, stop in enumerate(frag["vectorStyleStops"]):
        stop["color"] = sequential(pal, i / 9)
        stop["label"] = f"Decile {i + 1}"
    decile_stops = frag["vectorStyleStops"]

    out: dict[str, dict] = {}
    want = set(only or PROJECTS)
    score_desc = [
        "County data center restriction resemblance, by decile of calibrated score.",
        DEFINITIONS,
    ]

    def new_project(pid: str, title: str) -> dict:
        p = GP.build_empty_project(title, center=[-96.5, 38.5], zoom=3.4)
        p["metadata"] = {"commit_sha": sha, "generator": "scripts/export_geolibre.py",
                         "spec": "012", "project_id": pid}
        return p

    def score_layer(p: dict) -> str:
        layer = GP.geojson_layer(
            "Calibrated score decile",
            {"type": "FeatureCollection", "features": score_feats},
            fillOpacity=pal["fill_opacity"], strokeColor="#2c3544", strokeWidth=0.35)
        lid = A.add_layer(p, layer)
        A.apply_style(p, lid, frag)
        A.set_popup(p, lid, _popup_fields(cfg["counties"], GP.popup_field),
                    title="county", show_feature_id=False, hover=True)
        return lid

    if "national_restriction_model" in want:
        p = new_project("national_restriction_model", "National restriction model")
        score_layer(p)
        A.add_legend(p, "Calibrated score decile",
                     labels=[s["label"] for s in decile_stops],
                     colors=[s["color"] for s in decile_stops])
        p["description"] = _description(score_desc, sha, [CREDITS["census"], CREDITS["scores"]])
        out["national_restriction_model"] = p

    if "score_vs_politics_swipe" in want:
        src = political_source(paths["manifest"])
        with open(paths["votes"], encoding="utf-8") as fh:
            votes = json.load(fh)
        margin_feats, no_margin = [], 0
        for f in score_feats:
            m = _num((votes.get(f["id"]) or {}).get("2024"))
            if m is None:
                no_margin += 1
                continue
            props = {k: f["properties"][k] for k in ("fips", "county", "state")}
            props["margin_2024"] = round(m, 4)
            margin_feats.append({"type": "Feature", "id": f["id"],
                                 "geometry": f["geometry"], "properties": props})
        # Symmetric classes on the diverging pair, margin as a signed share.
        edges = [-0.6, -0.4, -0.2, -0.05, 0.05, 0.2, 0.4, 0.6]
        mstops = [{"value": -1.0, "color": diverging(pal, -0.8), "label": "below -0.6"}]
        for i, e in enumerate(edges):
            mid = e if i == len(edges) - 1 else (e + edges[i + 1]) / 2
            mstops.append({"value": e, "color": diverging(pal, max(-1, min(1, mid / 0.75))),
                           "label": f"{e:+.2f} and above" if i == len(edges) - 1
                           else f"{e:+.2f} to {edges[i + 1]:+.2f}"})
        p = new_project("score_vs_politics_swipe", "Restriction score vs 2024 margin")
        left = score_layer(p)
        layer = GP.geojson_layer("2024 presidential margin",
                                 {"type": "FeatureCollection", "features": margin_feats},
                                 fillOpacity=pal["fill_opacity"], strokeColor="#2c3544",
                                 strokeWidth=0.35)
        right = A.add_layer(p, layer)
        A.apply_style(p, right, {"vectorStyleMode": "graduated",
                                 "vectorStyleProperty": "margin_2024",
                                 "vectorStyleClassCount": len(mstops),
                                 "vectorStyleStops": mstops})
        A.set_popup(p, right, _popup_fields(cfg["margin"], GP.popup_field),
                    title="county", show_feature_id=False, hover=True)
        A.add_swipe(p, left_layers=[left], right_layers=[right])
        A.add_legend(p, "Calibrated score decile (left)",
                     labels=[s["label"] for s in decile_stops],
                     colors=[s["color"] for s in decile_stops])
        A.add_legend(p, "2024 margin (right; positive = Democratic lean)",
                     labels=[s["label"] for s in mstops], colors=[s["color"] for s in mstops],
                     position="bottom-right")
        p["description"] = _description(
            score_desc + [
                "The right side shows the 2024 presidential margin as a signed share of "
                "the two-party vote (positive leans Democratic, negative Republican). "
                f"{no_margin} scored counties have no 2024 margin in the source and are "
                "absent from the right side."],
            sha, [CREDITS["census"], CREDITS["scores"], CREDITS[src]])
        p["metadata"]["political_source"] = src
        out["score_vs_politics_swipe"] = p

    rows = None
    if want & {"national_opposition_cases", "opposition_timeline"}:
        rows = read_csv(paths["cases"])
    outcome_stops = [{"value": t, "color": pal["outcome"][t], "label": OUTCOME_LABEL[t]}
                     for t in OUTCOME_TERMS]

    def pin_layer(p: dict, name: str, feats: list[dict], cluster: bool) -> str:
        style = GP.marker_style(radius=5, stroke_color="#0b0f14", stroke_width=0.6)
        if cluster:
            style.update({"pointRenderer": "cluster", "clusterRadius": 40, "clusterMaxZoom": 6})
        layer = GP.geojson_layer(name, {"type": "FeatureCollection", "features": feats}, **style)
        lid = A.add_layer(p, layer)
        A.apply_style(p, lid, {"vectorStyleMode": "categorized",
                               "vectorStyleProperty": "outcome_defensible",
                               "vectorStyleClassCount": len(outcome_stops),
                               "vectorStyleStops": outcome_stops})
        fields = _popup_fields(cfg["pins"], GP.popup_field)
        if any(f["properties"].get("action_date") for f in feats[:1]):
            fields.append(GP.popup_field("action_date", label="Action date", kind="date",
                                         date_format="date"))
        A.set_popup(p, lid, fields, title="Project Name", show_feature_id=False, hover=True)
        A.add_legend(p, "Outcome", labels=[s["label"] for s in outcome_stops],
                     colors=[s["color"] for s in outcome_stops], shape="circle")
        return lid

    outcome_note = (
        "Pin color is the graded outcome of the opposed project or measure, never a side: "
        "confirmed tiers carry independent finality evidence; unverified tiers are as "
        "recorded and are shown in lighter tints.")

    if "national_opposition_cases" in want:
        feats, excl = case_features(rows, cfg)
        p = new_project("national_opposition_cases", "National opposition cases")
        pin_layer(p, "Opposition cases", feats, cluster=True)
        p["description"] = _description(
            [f"{len(feats)} opposition case pins, clustered below zoom 7.", outcome_note,
             f"Excluded: {excl['no_coordinates']} rows without coordinates and "
             f"{excl['not_pinnable']} rows not marked map_pinnable (statewide or "
             "jurisdiction-level records)."],
            sha, [CREDITS["cases"]])
        out["national_opposition_cases"] = p

    if "opposition_timeline" in want:
        feats, excl = case_features(rows, cfg, require_date=True)
        years = Counter(f["properties"]["action_year"] for f in feats)
        p = new_project("opposition_timeline", "Opposition timeline")
        pin_layer(p, "Opposition cases by action year", feats, cluster=False)
        p["metadata"]["time_property"] = "action_year"
        p["description"] = _description(
            [f"{len(feats)} pins carrying a verified action year "
             f"({', '.join(f'{y}: {n}' for y, n in sorted(years.items()))}).",
             "The year is that of the recorded action date (Date column), kept only "
             "where it parses as a full ISO date that agrees with action_year. "
             f"Excluded for a missing or unverified date: {excl['no_verified_date']} rows; "
             f"for missing coordinates: {excl['no_coordinates']}; not map_pinnable: "
             f"{excl['not_pinnable']}.",
             "Bind the Time Slider to the action_year property in the app (Layers, "
             "Bind property); geolibre 3.2.0 has no Python builder for slider state.",
             outcome_note],
            sha, [CREDITS["cases"]])
        out["opposition_timeline"] = p

    return out


def write_projects(projects: dict[str, dict], out_dir: str) -> list[str]:
    from geolibre import authoring as A
    from geolibre import project as GP

    written = []
    for pid, proj in projects.items():
        _assert_no_forbidden_renderer(proj)
        path = os.path.join(out_dir, f"{pid}.geolibre.json")
        _save_compact(path, GP.redact_credentials(proj))
        summary = A.describe_project(A.load_project(path))
        if summary["layerCount"] < 1:
            raise ExportError(f"{path}: reloaded with no layers")
        written.append(path)
    return written


def _save_compact(path: str, project: dict) -> None:
    """geolibre.authoring.save_project's atomic write, minus its indent=2.

    save_project pretty-prints every coordinate pair onto its own lines, which
    took the swipe project from 2.9 MB to 11.5 MB, past the 5 MB point at which
    configs/integrations.json says a layer needs PMTiles. The bytes GeoLibre
    reads are identical; load_project and describe_project re-validate each
    file after it is written.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=f".{os.path.basename(path)}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(project, separators=(",", ":"), allow_nan=False,
                                ensure_ascii=False) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _assert_no_forbidden_renderer(proj: dict) -> None:
    for layer in proj.get("layers", []):
        st = layer.get("style") or {}
        if st.get("pointRenderer") in {"heatmap", "density", "hexbin"} or layer.get("type") in {
                "heatmap", "extrusion", "fill-extrusion"}:
            raise ExportError(f"layer {layer.get('name')!r}: heatmap/density/extrusion is forbidden")


def default_paths(root: str = ROOT) -> dict[str, str]:
    j = lambda rel: os.path.join(root, rel)  # noqa: E731
    return {"topojson": j(TOPOJSON), "scores": j(SCORES), "aggregate": j(AGGREGATE),
            "votes": j(VOTES), "manifest": j(FEATURES_MANIFEST), "cases": j(CASES),
            "config": j(EXPORT_CONFIG), "palette": j(PALETTE_JS)}


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def ck(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    def raises(fn) -> bool:
        try:
            fn()
        except ExportError:
            return True
        return False

    try:
        sys.path.insert(0, ROOT)
        from outcome_defensibility import OUTCOME_GRADES
        ck("vocabulary matches outcome_defensibility", tuple(OUTCOME_GRADES) == OUTCOME_TERMS)
    except ImportError:
        ck("vocabulary matches outcome_defensibility (module absent, literal kept)", True)

    pal = read_palette(P(PALETTE_JS))
    ck("palette floor is 0.10", abs(pal["seq_floor"] - 0.10) < 1e-9)
    ck("palette fill opacity is 0.88", abs(pal["fill_opacity"] - 0.88) < 1e-9)
    ck("palette colors every outcome term", all(t in pal["outcome"] for t in OUTCOME_TERMS))
    ck("sequential(0) is floored, not near-black", sequential(pal, 0) != "#000004")

    topo = {"type": "Topology", "transform": {"scale": [1, 1], "translate": [0, 0]},
            "arcs": [[[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1]], [[5, 5], [1, 0], [0, 1], [-1, -1]]],
            "objects": {"counties": {"type": "GeometryCollection", "geometries": [
                {"type": "Polygon", "id": "13255", "arcs": [[0]], "properties": {"name": "Spalding"}},
                {"type": "Polygon", "id": "13035", "arcs": [[1]], "properties": {"name": "Butts"}}]}}}
    feats = topojson_features(topo)
    ck("topojson decodes delta arcs",
       feats[0]["geometry"]["coordinates"][0] == [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])

    tmp = tempfile.mkdtemp(prefix="geolibre_selftest_")

    def w(name: str, text: str) -> str:
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return path

    cfg_path = P(EXPORT_CONFIG)
    cfg = load_config(cfg_path)
    head = ",".join(["project_id", "Project Name", "City", "County", "State",
                     "outcome_defensible", "qc_mechanism", "action_year", "Date",
                     "date_parseable", "lat", "lon", "map_pinnable"])
    good = ["prj_1,Alpha,Griffin,Spalding,GA,pending,zoning,2025,2025-03-04,True,33.25,-84.28,True",
            "prj_2,Beta,Griffin,Spalding,GA,blocked_confirmed,moratorium,2026,2026-01-01,True,33.2,-84.3,True",
            "prj_3,Gamma,,Butts,GA,mixed,,2026,,True,,,True",
            "prj_4,Delta,,Butts,GA,advanced_confirmed,,2024,2024-05-05,True,33.3,-84.0,False"]
    paths = default_paths()
    paths.update({
        "topojson": w("c.topojson", json.dumps(topo)),
        "scores": w("s.csv", "fips,raw_oof_score,calibrated_score,score_decile,has_enacted_restrictive\n"
                             "13255,0.2,0.21,9,1\n13035,0.05,0.04,2,0\n"),
        "aggregate": w("a.csv", 'fips,county_name,state\n13255,"Spalding County, Georgia",GA\n'
                                '13035,"Butts County, Georgia",GA\n'),
        "votes": w("v.json", json.dumps({"13255": {"2024": -0.31}})),
        "manifest": w("m.json", "{}"),
        "cases": w("o.csv", head + "\n" + "\n".join(good) + "\n"),
    })
    rows = read_csv(paths["cases"])
    pins, excl = case_features(rows, cfg)
    ck("pins keep only allowlisted fields",
       set(pins[0]["properties"]) == {f["field"] for f in cfg["pins"]["popup"]})
    ck("rows without coordinates are counted, not geocoded", excl["no_coordinates"] == 1)
    ck("rows not map_pinnable are counted", excl["not_pinnable"] == 1)
    tl, texcl = case_features(rows, cfg, require_date=True)
    ck("timeline keeps verified dates only", len(tl) == 2 and texcl["no_coordinates"] == 1)

    dashed, _ = case_features([dict(rows[0], City="Griffin \u2014 north")], cfg)
    ck("em-dash in source text normalized", dashed[0]["properties"]["City"] == "Griffin - north")
    bad_rows = [dict(rows[0], outcome_defensible="won")]
    ck("unknown outcome term fails", raises(lambda: case_features(bad_rows, cfg)))
    planted = json.loads(json.dumps(cfg))
    planted["pins"]["popup"].append({"field": "confirmed_blocks"})
    ck("planted group-level column refused",
       raises(lambda: load_config(w("cfg.json", json.dumps(planted)))))
    planted = json.loads(json.dumps(cfg))
    planted["counties"]["popup"].append({"field": "composite"})
    ck("screener composite field refused",
       raises(lambda: load_config(w("cfg2.json", json.dumps(planted)))))
    ck("political source defaults to tonmcg", political_source(paths["manifest"]) == "tonmcg")

    try:
        import geolibre  # noqa: F401
        have_geolibre = True
    except ImportError:
        have_geolibre = False
    if have_geolibre:
        from geolibre import authoring as A
        missing_geo = dict(paths, scores=w("s2.csv", "fips,raw_oof_score,calibrated_score,"
                                                     "score_decile,has_enacted_restrictive\n"
                                                     "99999,0.1,0.1,5,0\n"))
        try:
            build_projects(missing_geo, "selftest")
            ck("missing FIPS fails with the FIPS listed", False)
        except ExportError as e:
            ck("missing FIPS fails with the FIPS listed", "99999" in str(e))
        projects = build_projects(paths, "0" * 40)
        ck("four projects built", sorted(projects) == sorted(PROJECTS))
        projects["national_restriction_model"]["basemapStyleUrl"] = (
            "https://tiles.example.com/style.json?api_key=SECRET123")
        out_dir = os.path.join(tmp, "out")
        written = write_projects(projects, out_dir)
        loaded = {os.path.basename(p): A.load_project(p) for p in written}
        ck("each project reloads", len(loaded) == 4)
        layer_counts = {k: A.describe_project(v)["layerCount"] for k, v in loaded.items()}
        ck("layer counts", layer_counts == {
            "national_restriction_model.geolibre.json": 1,
            "national_opposition_cases.geolibre.json": 1,
            "score_vs_politics_swipe.geolibre.json": 2,
            "opposition_timeline.geolibre.json": 1})
        ck("credentials redacted on save",
           "SECRET123" not in json.dumps(loaded["national_restriction_model.geolibre.json"]))
        swipe = A.describe_project(loaded["score_vs_politics_swipe.geolibre.json"])
        ck("swipe control present", "swipe" in swipe["mapControls"])
        ck("commit SHA in description",
           all("0" * 40 in v.get("description", "") for v in loaded.values()))
        ck("score description defines its terms",
           "resemblance measure" in loaded["national_restriction_model.geolibre.json"]["description"])
        text = json.dumps(loaded)
        ck("no group-level or screener field exported",
           not any(f'"{c}"' in text for c in GROUP_LEVEL | {"composite", "pct_county_model"}))
        heat = dict(projects["national_opposition_cases"])
        heat["layers"] = [dict(heat["layers"][0], style={"pointRenderer": "heatmap"})]
        ck("heatmap renderer refused", raises(lambda: _assert_no_forbidden_renderer(heat)))
    else:
        ck("geolibre not installed: project build checks skipped", True)

    fails = 0
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    print(f"{len(checks) - fails}/{len(checks)} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--only", action="append", choices=PROJECTS)
    ap.add_argument("--out-dir", default=P(OUT_DIR))
    ap.add_argument("--allow-uncommitted", action="store_true",
                    help="preview before commit: stamp the SHA -uncommitted instead of failing")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    paths = default_paths()
    try:
        sha = commit_sha([os.path.relpath(p, ROOT) for p in paths.values()],
                         allow_uncommitted=a.allow_uncommitted)
        projects = build_projects(paths, sha, set(a.only) if a.only else None)
        written = write_projects(projects, a.out_dir)
    except ExportError as e:
        print(f"EXPORT FAILED: {e}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {os.path.relpath(path, ROOT)} ({os.path.getsize(path) // 1024} KB)")
    print(f"commit {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
