#!/usr/bin/env python3
"""
render_county_pdf.py

County profile PDFs (spec 010 US2): one PDF per requested FIPS, rendered with
WeasyPrint from templates/county_profile.html.j2. The profile shows the same
facts as county-profile.html and the location report, read through
report_data.py at run time, with tables from Great Tables and charts from
charts.py (the same Vega-Lite specs the web pages can embed). Every page's
footer records the data vintage and the commit SHA.

--plate follows the spec 013 slot contract exactly as the Word report does
(specs/010-deliverable-generation/plan.md): fixed slot under the title block at
full text width, sidecar caption and credits, the same refusals.

WeasyPrint needs the Pango system libraries. When they are missing, the
script prints the install line and exits non-zero.

Reads
  everything report_data.py and charts.py read; templates/county_profile.html.j2;
  outputs/plates/<id>.png and .json (with --plate)
Writes
  outputs/county_pdfs/<fips>_<slug>.pdf

Usage
  python scripts/render_county_pdf.py --fips 13255 [--fips 13035 ...]
         [--plate outputs/plates/13255_spalding.png] [--draft]
  python scripts/render_county_pdf.py --selftest
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import export_geolibre as eg  # noqa: E402
import report_data as rd  # noqa: E402

TEMPLATE = "templates/county_profile.html.j2"
OUT_DIR = "outputs/county_pdfs"
PDF_CHARTS = ("score_distribution", "peer_comparison")
INSTALL_HINT = ("WeasyPrint cannot load its system libraries (Pango). Install them with:\n"
                "  sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0   # Debian, Ubuntu\n"
                "  brew install pango                                         # macOS")

# Hawthorn print colors (research R12); outcome colors come from viz-palette.json
INK, INK_MUTED, ACCENT = "#1f2937", "#4b5563", "#1f4e79"
TAG_RE = re.compile(r"<[^>]+>")


class MissingLibraries(Exception):
    pass


def weasyprint_html():
    try:
        from weasyprint import HTML
    except OSError as exc:  # cffi raises OSError when libpango is absent
        raise MissingLibraries(str(exc)) from exc
    return HTML


# --------------------------------------------------------------------------
# tables (Great Tables)
# --------------------------------------------------------------------------

def gt_table(rows: list[dict], columns: list[tuple[str, str]], links: tuple[str, ...] = ()) -> str:
    """Great Tables HTML; link columns carry 'text|url' and render as anchors."""
    import pandas as pd
    from great_tables import GT

    if not rows:
        return ""
    from markupsafe import escape

    def anchor(v: str) -> str:
        text, _, url = v.partition("|")
        return (f'<a href="{escape(url)}">{escape(text)}</a>' if url.startswith("http")
                else str(escape(text)))

    df = pd.DataFrame([{label: r.get(key, "") or "" for key, label in columns} for r in rows])
    for label in links:
        df[label] = df[label].map(anchor)
    gt = GT(df)
    if links:
        # A formatter's str result is inserted as HTML; the anchors were escaped
        # above. (Great Tables' html() wrapper cannot be stored in pandas 3
        # string columns.) Unformatted columns stay escaped by Great Tables.
        gt = gt.fmt(lambda v: str(v), columns=list(links))
    return gt.opt_table_font(font="Georgia").as_raw_html()


def _host(url: str) -> str:
    from urllib.parse import urlparse

    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def tables_for(facts: dict) -> dict:
    hist = [dict(r, src=f"{_host(r['url'])}|{r['url']}") for r in facts["history"]["rows"]]
    cases = [dict(r, src=f"{_host(r['url'])}|{r['url']}") for r in facts["cases"]["rows"]]
    census = [dict(r, src=r["source"]) for r in facts["history"]["census_rows"]]
    return {
        "history": gt_table(hist, [("date", "Date"), ("place", "Place"), ("level", "Level"),
                                   ("type", "Type"), ("status", "Status"), ("src", "Source")],
                            links=("Source",)),
        "census": gt_table(census, [("date", "Date enacted"), ("instrument", "Instrument"),
                                    ("status", "Status"), ("src", "Source")]),
        "cases": gt_table(cases, [("date", "Date"), ("place", "Place"), ("type", "Type"),
                                  ("status", "Status"), ("outcome", "Outcome"),
                                  ("project", "Project"), ("src", "Source")], links=("Source",)),
        "neighbors": gt_table(facts["neighbors"]["rows"],
                              [("county", "County"), ("state", "State"),
                               ("enacted", "Enacted restriction on record"),
                               ("decile", "Decile"), ("cases", "Tracked cases")]),
        "sources": gt_table(facts["sources"]["files"],
                            [("path", "File"), ("sha", "File hash (first twelve characters)")]),
    }


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def html_for(facts: dict, *, root: str, plate: dict | None, charts: bool) -> str:
    import jinja2
    from markupsafe import Markup, escape

    import charts as ch

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(root, "templates")),
                             autoescape=True, undefined=jinja2.StrictUndefined)
    tpl = env.get_template(os.path.basename(TEMPLATE))
    r = facts["report"]
    footer = (f"{r['title']}. Data as of {r['as_of']}. Commit {r['commit_short']}."
              .replace("\\", "\\\\").replace('"', '\\"'))
    chart_svgs = []
    if charts:
        for name in PDF_CHARTS:
            # vl-convert's PNG keeps its own text layout; WeasyPrint lays out SVG
            # text with substitute font metrics and clipped the axis labels.
            out = ch.render(ch.build_chart(name, r["fips"], root, facts))
            uri = "data:image/png;base64," + base64.b64encode(out["png"]).decode("ascii")
            title = json.loads(out["spec"])["title"]["text"]
            chart_svgs.append({"svg": Markup(f'<img src="{uri}" alt="{escape(title)}">')})
    pl = None
    if plate:
        with open(plate["png"], "rb") as fh:
            uri = "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
        pl = dict(plate, data_uri=uri)
    tables = {k: Markup(v) for k, v in tables_for(facts).items()}
    return tpl.render(
        report=r, history=facts["history"], score=facts["score"], cases=facts["cases"],
        neighbors=facts["neighbors"], sources=facts["sources"], plate=pl, charts=chart_svgs,
        tables=tables, define=rd.Definer(facts["definitions"]), footer=footer,
        ink=INK, ink_muted=INK_MUTED, accent=ACCENT,
        outcome_colors=ch.load_palette(root)["outcome"])


def visible_text(html: str) -> str:
    body = re.sub(r"<(style|svg)\b.*?</\1>", " ", html, flags=re.S)
    import html as h

    return h.unescape(re.sub(r"\s+", " ", TAG_RE.sub(" ", body)))


def checks(html: str, facts: dict, plate: dict | None) -> None:
    text = visible_text(html)
    own = text
    if plate:
        for line in [f"{plate['title']} {plate['subtitle']}", plate["title"], *plate["credits"]]:
            own = own.replace(line, "")
    rd.check_text(text + " " + facts["report"]["title"], where="county PDF")
    rd.check_first_use(own, facts["definitions"])
    if not facts["history"]["enacted"]:
        m = re.search(r'<section class="history">(.*?)</section>', html, re.S)
        rd.check_no_rate(visible_text(m.group(1)) if m else "")


def render(fips: str, *, root: str = ROOT, plate: str | None = None, draft: bool = False,
           charts: bool = True, allow_uncommitted: bool = False, sha: str | None = None) -> dict:
    HTML = weasyprint_html()
    t0 = time.time()
    facts = rd.build(fips, root, sha=sha, allow_uncommitted=allow_uncommitted, draft=draft)
    r = facts["report"]
    r["title"] = f"{r['county_name']}, {r['state_name']}: county profile"
    pl = rd.load_plate(plate, draft=draft, root=root) if plate else None
    html = html_for(facts, root=root, plate=pl, charts=charts)
    checks(html, facts, pl)
    pdf = HTML(string=html, base_url=root).write_pdf()
    slug = re.sub(r"[^a-z0-9]+", "-", r["county_name"].lower().replace(" county", "")).strip("-")
    path = os.path.join(root, OUT_DIR, f"{r['fips']}_{slug}" + ("-draft" if draft else "") + ".pdf")
    write_pdf(path, pdf)
    return {"pdf": path, "seconds": round(time.time() - t0, 2), "html": html}


def write_pdf(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def pdf_text(path: str) -> str:
    from pdfminer.high_level import extract_text

    return extract_text(path)


def _selftest() -> int:
    checks_: list[tuple[str, bool]] = []

    def check(name: str, ok) -> None:
        checks_.append((name, bool(ok)))

    def refused(fn, *a, **k) -> bool:
        try:
            fn(*a, **k)
        except rd.ReportError:
            return True
        return False

    try:
        weasyprint_html()
    except MissingLibraries:
        print(INSTALL_HINT, file=sys.stderr)
        print("FAIL WeasyPrint system libraries are missing")
        return 1

    import charts as ch

    tmp = tempfile.mkdtemp(prefix="county_pdf_")
    sha = rd.write_fixture(tmp)
    os.makedirs(os.path.join(tmp, "templates"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, TEMPLATE), os.path.join(tmp, TEMPLATE))
    shutil.copy(os.path.join(ROOT, ch.PALETTE_JSON), os.path.join(tmp, ch.PALETTE_JSON))

    outs = {f: render(f, root=tmp, sha=sha, charts=(f == "13001")) for f in ("13001", "13003", "13005")}
    texts = {f: pdf_text(o["pdf"]) for f, o in outs.items()}
    check("three PDFs render and open", all(o["pdf"].endswith(".pdf") and texts[f].strip()
                                            for f, o in outs.items()))
    check("every page footer records data vintage and commit",
          all(f"Commit {sha[:12]}" in t and "Data as of" in t for t in texts.values()))
    check("no em-dash in any PDF", not any(rd.EM_DASH in t for t in texts.values()))
    facts = rd.build("13001", tmp, sha=sha)
    flat = re.sub(r"\s+", " ", texts["13001"])
    check("every fact value is printed", all(f["formatted"] in flat for f in facts["facts"]))
    check("Great Tables renders the case table", "gt_table" in outs["13001"]["html"]
          and "Campus One" in flat)
    check("charts are images rendered by charts.py",
          outs["13001"]["html"].count('<img src="data:image/png') >= 2
          and 'alt="Alpha County sits in decile 8' in outs["13001"]["html"])
    t3 = re.sub(r"\s+", " ", texts["13003"])
    check("no-enacted county states it plainly",
          "No enacted restriction is on record for Beta County, Georgia." in t3)
    check("first use carries the definition", "decile (the county" in flat)
    png = rd.write_plate(tmp, sha)
    rp = render("13001", root=tmp, sha=sha, plate=png, charts=False)
    check("plate under the title block with sidecar caption and credits",
          rp["html"].index('class="plate"') < rp["html"].index("<h2>Summary")
          and "Alpha County has two tracked cases" in rp["html"]
          and "Elevation: U.S. Geological Survey" in rp["html"])
    rn = render("13001", root=tmp, sha=sha, charts=False)
    check("without --plate there is no plate slot", 'class="plate"' not in rn["html"])
    prev = rd.write_plate(tmp, sha, preview=True)
    check("preview plate refused on a final PDF",
          refused(render, "13001", root=tmp, sha=sha, plate=prev, charts=False))
    rd.write_plate(tmp, "e" * 40)
    check("plate from a non-ancestor commit refused",
          refused(render, "13001", root=tmp, sha=sha, plate=png, charts=False))

    fails = 0
    for name, ok in checks_:
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    print(f"{len(checks_) - fails}/{len(checks_)} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fips", action="append")
    ap.add_argument("--plate")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--allow-uncommitted", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.fips:
        ap.error("--fips (one or more) or --selftest is required")
    if args.plate and len(args.fips) > 1:
        ap.error("--plate applies to one county; pass a single --fips with it")
    try:
        for fips in args.fips:
            res = render(fips, plate=args.plate, draft=args.draft, charts=not args.no_charts,
                         allow_uncommitted=args.allow_uncommitted)
            print(f"wrote {os.path.relpath(res['pdf'], ROOT)} in {res['seconds']} s")
    except MissingLibraries:
        print(INSTALL_HINT, file=sys.stderr)
        return 1
    except (rd.ReportError, eg.ExportError) as exc:
        print(f"render_county_pdf: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
