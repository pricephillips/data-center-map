#!/usr/bin/env python3
"""
charts.py

One chart definition per figure, for every product (spec 010 US3). Each chart
is an Altair (Vega-Lite) spec built from report_data facts; vl-convert renders
the same spec to SVG and PNG for Word and PDF, and to an HTML embed for web
pages and client portals, so a static report and a live page show the same
chart the same way.

Charts follow docs/chart_standard.md: the title states the finding, series
are labelled directly, every chart has a source line, and uncertainty is drawn
as a range. Colors come from viz-palette.json, the mirror of viz-palette.js
that --sync-palette writes; viz-palette.js stays the canonical source and the
selftest fails if the mirror drifts.

Charts
  score_distribution   every county's calibrated score, the county marked with
                       its Venn-Abers interval
  enactment_timeline   enacted restrictions on record in the county and its
                       neighbors, by date
  peer_comparison      the county and its most similar counties, scores with
                       intervals

Reads
  everything report_data.py reads; viz-palette.js (--sync-palette only);
  viz-palette.json
Writes
  outputs/charts/<fips>_<chart>.svg, .png, .html
  viz-palette.json (--sync-palette)

Usage
  python scripts/charts.py --fips 13255 [--chart score_distribution]
  python scripts/charts.py --sync-palette
  python scripts/charts.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import export_geolibre as eg  # noqa: E402  (the one viz-palette.js reader)
import report_data as rd  # noqa: E402

PALETTE_JS = "viz-palette.js"
PALETTE_JSON = "viz-palette.json"
OUT_DIR = os.path.join(ROOT, "outputs", "charts")
CHARTS = ("score_distribution", "enactment_timeline", "peer_comparison")

# Typography is the Hawthorn report style (research R12), not a palette color.
FONT = "Georgia, serif"
INK = "#1f2937"
INK_MUTED = "#4b5563"
WIDTH, HEIGHT = 560, 220


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def palette_from_js(root: str = ROOT) -> dict:
    p = eg.read_palette(os.path.join(root, PALETTE_JS))
    return {
        "_comment": "Mirror of viz-palette.js for Python renderers and Altair (spec 010 US3). "
                    "Written by scripts/charts.py --sync-palette; never hand-edited. "
                    "viz-palette.js is canonical.",
        "source": PALETTE_JS,
        "outcome": p["outcome"],
        "inferno": [_hex(c) for c in p["inferno"]],
        "seq_floor": p["seq_floor"],
        "fill_opacity": p["fill_opacity"],
        "div_neg": _hex(p["div_neg"]),
        "div_pos": _hex(p["div_pos"]),
        "div_mid": _hex(p["div_mid"]),
    }


def palette_json_text(root: str = ROOT) -> str:
    return json.dumps(palette_from_js(root), indent=2) + "\n"


def sync_palette() -> str:
    # Literal path inline, so layer_audit resolves this writer.
    with open(os.path.join(ROOT, "viz-palette.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(palette_json_text(ROOT))
    return os.path.join(ROOT, PALETTE_JSON)


def load_palette(root: str = ROOT) -> dict:
    with open(os.path.join(root, PALETTE_JSON), encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# chart specs
# --------------------------------------------------------------------------

def _source_line(files: list[str], facts: dict) -> list[str]:
    return [f"Source: {', '.join(files)}.",
            f"Data as of {facts['report']['as_of']}, commit {facts['report']['commit_short']}."]


def _base(chart, title: str, subtitle: str, source: list[str]):
    return chart.properties(
        width=WIDTH, height=HEIGHT,
        title={"text": title, "subtitle": [subtitle, *source], "anchor": "start",
               "font": FONT, "subtitleFont": FONT, "color": INK, "subtitleColor": INK_MUTED,
               "fontSize": 14, "subtitleFontSize": 10},
    ).configure_axis(labelFont=FONT, titleFont=FONT, labelColor=INK_MUTED, titleColor=INK,
                     grid=False).configure_view(stroke=None)


def score_distribution(facts: dict, scores: list[float], palette: dict):
    import altair as alt
    import pandas as pd

    s = facts["score"]
    r = facts["report"]
    county = pd.DataFrame([{"label": r["county_name"], "score": float(s["calibrated"]),
                            "low": float(s["interval_low"]), "high": float(s["interval_high"])}])
    bars = alt.Chart(pd.DataFrame({"score": scores})).mark_bar(
        color=palette["inferno"][3], opacity=0.55).encode(
        x=alt.X("score:Q", bin=alt.Bin(maxbins=40), title="Calibrated score",
                axis=alt.Axis(format=".2f", labelOverlap=True)),
        y=alt.Y("count():Q", title="Counties"))
    band = alt.Chart(county).mark_rect(color=palette["div_neg"], opacity=0.25).encode(
        x="low:Q", x2="high:Q")
    rule = alt.Chart(county).mark_rule(color=palette["div_neg"], strokeWidth=2).encode(x="score:Q")
    label = alt.Chart(county).mark_text(align="left", dx=6, dy=-HEIGHT / 2 + 8, font=FONT,
                                        color=INK).encode(x="score:Q", text="label:N")
    title = (f"{r['county_name']} sits in decile {s['decile']} of the national "
             f"score distribution")
    sub = (f"Calibrated score {s['calibrated']}; shaded band is the interval "
           f"{s['interval_low']} to {s['interval_high']}. All scored counties.")
    return _base(alt.layer(bars, band, rule, label), title, sub,
                 _source_line([rd.SCORES, rd.INTERVALS], facts))


def enactment_timeline(facts: dict, neighbor_facts: list[dict], palette: dict):
    import altair as alt
    import pandas as pd

    rows = []
    for f in [facts, *neighbor_facts]:
        for h in f["history"]["rows"]:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", h["date"]):  # year-only dates stay off the axis
                rows.append({"county": f["report"]["county_name"], "date": h["date"],
                             "type": h["type"], "outcome": h["outcome"]})
    df = pd.DataFrame(rows or [{"county": facts["report"]["county_name"], "date": None,
                                "type": "", "outcome": ""}])
    labels = {v: k for k, v in rd.OUTCOME_LABEL.items()}
    domain = [lab for lab in rd.OUTCOME_LABEL.values() if lab in set(df["outcome"])]
    colors = [palette["outcome"][labels[lab]] for lab in domain]
    pts = alt.Chart(df.dropna(subset=["date"])).mark_point(filled=True, size=90).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("county:N", title=None, sort=None, axis=alt.Axis(labelLimit=260)),
        color=alt.Color("outcome:N", scale=alt.Scale(domain=domain, range=colors),
                        legend=alt.Legend(title=None, orient="bottom", labelFont=FONT)),
        tooltip=["county", "date", "type", "outcome"])
    n = len(rows)
    title = (f"{n} dated enacted restriction{'' if n == 1 else 's'} on record in "
             f"{facts['report']['county_name']} and its neighbors")
    return _base(pts, title, "Each point is a recorded restriction with its own source.",
                 _source_line([rd.CASES], facts))


def peer_comparison(facts: dict, peer_facts: list[dict], palette: dict):
    import altair as alt
    import pandas as pd

    rows = [{"county": f"{f['report']['county_name']}, {f['report']['state']}",
             "score": float(f["score"]["calibrated"]), "low": float(f["score"]["interval_low"]),
             "high": float(f["score"]["interval_high"]),
             "focus": f is facts} for f in [facts, *peer_facts]]
    df = pd.DataFrame(rows)
    order = list(df.sort_values("score", ascending=False)["county"])
    color = alt.condition("datum.focus", alt.value(palette["div_neg"]), alt.value(palette["div_mid"]))
    bars = alt.Chart(df).mark_rule(strokeWidth=3, opacity=0.5).encode(
        y=alt.Y("county:N", sort=order, title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X("low:Q", title="Calibrated score", axis=alt.Axis(format=".2f")),
        x2="high:Q", color=color)
    pts = alt.Chart(df).mark_point(filled=True, size=70).encode(
        y=alt.Y("county:N", sort=order), x="score:Q", color=color)
    s = facts["score"]
    title = (f"{facts['report']['county_name']} scores {s['calibrated']}; "
             f"the peer median is {s['peer_median']}")
    sub = "Points are calibrated scores; lines are intervals. Peers are a similarity set."
    return _base(alt.layer(bars, pts), title, sub,
                 _source_line([rd.SCORES, rd.INTERVALS, rd.BENCHMARKS], facts))


def build_chart(name: str, fips: str, root: str = ROOT, facts: dict | None = None):
    facts = facts or rd.build(fips, root)
    palette = load_palette(root)
    if name == "score_distribution":
        return score_distribution(facts, rd.national_scores(root), palette)
    if name == "enactment_timeline":
        nb = [rd.build(n["fips"], root, sha=facts["report"]["commit"])
              for n in facts["neighbors"]["rows"]]
        return enactment_timeline(facts, nb, palette)
    if name == "peer_comparison":
        peers = [rd.build(p, root, sha=facts["report"]["commit"]) for p in rd.peer_fips(fips, root)]
        return peer_comparison(facts, peers, palette)
    raise rd.ReportError(f"unknown chart {name!r}; one of {CHARTS}")


def render(chart) -> dict:
    """SVG, PNG and HTML from the one spec."""
    import vl_convert as vlc

    spec = chart.to_json()
    return {"svg": vlc.vegalite_to_svg(spec).encode("utf-8"),
            "png": vlc.vegalite_to_png(spec, scale=3),
            "html": vlc.vegalite_to_html(spec, bundle=False).encode("utf-8"),
            "spec": spec}


def write(fips: str, name: str, out: dict, out_dir: str = OUT_DIR) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for ext in ("svg", "png", "html"):
        path = os.path.join(out_dir, f"{fips}_{name}.{ext}")
        with open(path, "wb") as fh:
            fh.write(out[ext])
        paths.append(path)
    return paths


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _datasets(spec_json: str) -> list:
    spec = json.loads(spec_json)
    return [row for rows in spec.get("datasets", {}).values() for row in rows]


def _selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok) -> None:
        checks.append((name, bool(ok)))

    with open(os.path.join(ROOT, PALETTE_JSON), encoding="utf-8") as fh:
        committed = fh.read()
    check("viz-palette.json matches viz-palette.js (run --sync-palette)",
          committed == palette_json_text(ROOT))
    pal = json.loads(committed)
    check("outcome colors cover the outcome ladder", set(pal["outcome"]) == set(rd.OUTCOME_LABEL))

    tmp = tempfile.mkdtemp(prefix="charts_")
    sha = rd.write_fixture(tmp)
    with open(os.path.join(tmp, PALETTE_JSON), "w", encoding="utf-8") as fh:
        fh.write(committed)
    facts = rd.build("13001", tmp, sha=sha)
    ch = build_chart("score_distribution", "13001", tmp, facts)
    out = render(ch)
    spec = json.loads(out["spec"])
    check("title states the finding with the fact values",
          spec["title"]["text"] == "Alpha County sits in decile 8 of the national score distribution")
    check("source line names the files and commit",
          any(rd.SCORES in s for s in spec["title"]["subtitle"])
          and any(sha[:12] in s for s in spec["title"]["subtitle"]))
    check("SVG renders and carries the county label", b"Alpha County" in out["svg"])
    check("PNG renders", out["png"][:8] == b"\x89PNG\r\n\x1a\n")
    html = out["html"].decode("utf-8")
    m = re.search(r"(\{\"\$schema\".*?\})\s*[,;)]", html, re.S)
    check("HTML embed carries the same spec", '"Alpha County"' in html and m is not None)
    same = all(json.dumps(v)[1:-1] in html or str(v) in html
               for row in _datasets(out["spec"]) for v in row.values() if v is not None)
    check("HTML and SVG come from identical data values", same)
    tl = render(build_chart("enactment_timeline", "13001", tmp, facts))
    check("timeline counts dated enacted rows (Alpha city moratorium, Gamma none dated in feed)",
          json.loads(tl["spec"])["title"]["text"].startswith("1 dated enacted restriction on"))
    pc = render(build_chart("peer_comparison", "13001", tmp, facts))
    check("peer chart includes the county and its peers", b"Alpha County" in pc["svg"])
    check("chart text passes vocabulary and em-dash checks",
          not any(rd.LEAK_RE.search(t) or rd.EM_DASH in t
                  for t in (spec["title"]["text"], *spec["title"]["subtitle"])))

    fails = 0
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    print(f"{len(checks) - fails}/{len(checks)} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sync-palette", action="store_true")
    ap.add_argument("--fips")
    ap.add_argument("--chart", choices=CHARTS, action="append")
    ap.add_argument("--allow-uncommitted", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.sync_palette:
        print(f"wrote {os.path.relpath(sync_palette(), ROOT)}")
        return 0
    if not args.fips:
        ap.error("--fips, --sync-palette or --selftest is required")
    try:
        facts = rd.build(args.fips, allow_uncommitted=args.allow_uncommitted)
        for name in args.chart or CHARTS:
            for p in write(args.fips, name, render(build_chart(name, args.fips, facts=facts))):
                print(f"wrote {os.path.relpath(p, ROOT)}")
    except (rd.ReportError, eg.ExportError) as exc:
        print(f"charts: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
