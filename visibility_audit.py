#!/usr/bin/env python3
"""
visibility_audit.py

Measures the gap between what the client-visible surface shows and what the
pipeline actually produces, and writes it as a maintained artifact.

Nothing in this repository had ever measured that gap, which is how the
platform arrived at a state where the Notion hub exposes seven pages reading
eight datasets while data/ holds well over a hundred generated outputs. An
output that no surface reads is not automatically a defect: some are
permanently internal by a recorded ruling, some are caches, some are human
review queues whose rows are internal even when their counts are not. The
point of this module is that the distinction is declared in
configs/surfaces.json rather than assumed, so an output that is invisible for
no stated reason shows up as unclassified and has to be dispositioned.

Reads
  configs/surfaces.json    Notion page -> embed -> HTML registry, plus the
                           disposition overrides and the pattern rules.
  *.html                   Parsed for the datasets each surface loads.
  data/, repo root         Inventory of generated outputs.

Writes
  docs/visibility_matrix.md

Three defect classes are reported alongside the matrix:

  unreferenced_surface   An HTML page in the repo that appears on no Notion
                         page. Either it is dead or the registry is stale.
  broken_reference       A surface fetches a path that does not exist.
  relative_first_chain   A fetch chain that tries the Pages origin before
                         raw.githubusercontent.com. Standing repository rule:
                         raw first, because GitHub Pages sends no CORS
                         headers and an embedded iframe fails silently.

Usage
  python visibility_audit.py
  python visibility_audit.py --selftest
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import sys
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "configs", "surfaces.json")
OUT_MD = os.path.join(HERE, "docs", "visibility_matrix.md")

RAW_HOST = "raw.githubusercontent.com/pricephillips/data-center-map"

# Root-level generated artifacts that are part of the inventory. Standing
# hand-maintained documents (README, PHASE_STATUS, CODEBOOK, IDENTIFIABILITY,
# cost_layer_notes) are not outputs and are excluded by name.
ROOT_DOCS = {
    "headline_metrics.md", "qc_report.md", "data_quality_report.md",
    "untagged_triage.md", "verification_status_report.md",
    "fips_resolution_report.md",
}
ROOT_EXCLUDE_MD = {
    "README.md", "PHASE_STATUS.md", "CODEBOOK.md", "IDENTIFIABILITY.md",
    "cost_layer_notes.md", "ARCHITECTURE.md",
}

DATA_EXT = (".csv", ".json", ".md", ".flag")

# A path literal counts as a dataset the page loads unless it is being handed
# to the browser as a download name or a navigation target, which is why the
# dashboard's export filename is not read as a reference. Bare filenames with
# no directory part are ignored unless they exist at the repository root:
# error-message prose mentions its own data file by name and would otherwise
# register as a broken reference.
PATH_RE = re.compile(r"""['"]([^'"\s]*?\.(?:csv|json))['"]""")
EXPORT_HINT = re.compile(r"\.download\s*=\s*$|location\.href\s*=\s*$")
# The chains are written as `RAW + '/data/x.csv'`, so the literal alone reads
# as a relative path. Resolve the string constants the pages declare and
# rejoin the concatenation before classifying the reference.
CONST_RE = re.compile(r"""(?:const|let|var)\s+(\w+)\s*=\s*['"]([^'"]+)['"]""")
CONCAT_RE = re.compile(r"(\w+)\s*\+\s*$")
# A page can also load a dataset through a shared module it includes, which is
# how the facility surfaces reach data/facility_manifest.json. Following the
# local script tags keeps that dataset counted as surfaced instead of
# reporting it invisible because no HTML file names it.
SCRIPT_SRC_RE = re.compile(r"""<script[^>]*\bsrc\s*=\s*['"]([^'"]+)['"]""",
                           re.I)


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------

def read_registry(path: str = REGISTRY) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def inventory(root: str = HERE) -> list[str]:
    """Every generated output, as a repo-relative path."""
    out = []
    for name in sorted(os.listdir(os.path.join(root, "data"))):
        if name.endswith(DATA_EXT):
            out.append("data/" + name)
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if not os.path.isfile(full):
            continue
        if name.endswith((".csv", ".json")):
            out.append(name)
        elif name.endswith(".md") and name in ROOT_DOCS:
            out.append(name)
    return out


def normalize_ref(ref: str) -> str | None:
    """Reduce a fetch target to a repo-relative path, or None if external."""
    ref = ref.strip()
    if RAW_HOST in ref:
        return ref.split("/main/", 1)[1] if "/main/" in ref else None
    if ref.startswith(("http://", "https://", "//")):
        return None          # third-party geometry and basemaps
    ref = ref.lstrip("./")
    ref = ref.lstrip("/")
    return ref or None


def local_modules(text: str) -> list[str]:
    """Repo-local .js files a page includes, in document order."""
    out = []
    for src in SCRIPT_SRC_RE.findall(text):
        if src.startswith(("http://", "https://", "//")):
            continue
        if not src.endswith(".js"):
            continue
        out.append(src.lstrip("./"))
    return out


def surface_reads(html_path: str, root: str = HERE) -> tuple[set[str], set[str], bool]:
    """(repo-relative paths read, external hosts read, chain is raw-first).

    raw-first is decided per dataset, not per line: the fallback chains are
    written across several lines, so the question is whether the raw URL for a
    given dataset appears before the relative one, not whether any single line
    holds both.
    """
    with open(html_path, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    for module in local_modules(text):
        mod_path = os.path.join(root, module)
        if os.path.isfile(mod_path):
            with open(mod_path, encoding="utf-8", errors="ignore") as fh:
                text += "\n" + fh.read()
    reads: set[str] = set()
    external: set[str] = set()
    first_raw: dict[str, int] = {}
    first_rel: dict[str, int] = {}
    env = dict(CONST_RE.findall(text))

    for m in PATH_RE.finditer(text):
        literal = m.group(1)
        before = text[max(0, m.start() - 40):m.start()]
        if EXPORT_HINT.search(before):
            continue
        concat = CONCAT_RE.search(before)
        if concat and concat.group(1) in env:
            literal = env[concat.group(1)] + literal
        norm = normalize_ref(literal)
        if norm is None:
            if literal.startswith(("http", "//")):
                parts = literal.split("/")
                if len(parts) > 2:
                    external.add(parts[2])
            continue
        is_raw = RAW_HOST in literal
        if not is_raw and "/" not in literal.lstrip("./"):
            if not os.path.exists(os.path.join(root, norm)):
                continue          # prose mention of a bare filename
        reads.add(norm)
        table = first_raw if is_raw else first_rel
        table.setdefault(norm, m.start())

    raw_first = True
    for path, rel_at in first_rel.items():
        raw_at = first_raw.get(path)
        if raw_at is None or raw_at > rel_at:
            raw_first = False
            break
    return reads, external, raw_first


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def disposition(path: str, reg: dict) -> tuple[str, str]:
    over = reg.get("dispositions", {})
    if path in over:
        klass, note = over[path]
        return klass, note
    base = os.path.basename(path)
    for pattern, klass, note in reg.get("disposition_rules", []):
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(base, pattern):
            return klass, note
    return "unclassified", ""


def audit(reg: dict, root: str = HERE) -> dict:
    pages = reg["pages"]
    embeds = OrderedDict()
    for page in pages:
        for html in page["embeds"]:
            embeds.setdefault(html, []).append(page)

    optional = reg.get("optional_references", {})
    per_surface = OrderedDict()
    broken = []
    absent_optional = []
    relative_first = []
    for html, owners in embeds.items():
        full = os.path.join(root, html)
        if not os.path.isfile(full):
            broken.append((html, "<page file missing>"))
            continue
        reads, external, raw_first = surface_reads(full, root)
        for r in sorted(reads):
            if os.path.exists(os.path.join(root, r)):
                continue
            why = optional.get(html, {}).get(r)
            if why:
                absent_optional.append((html, r, why))
            else:
                broken.append((html, r))
        if not raw_first:
            relative_first.append(html)
        per_surface[html] = {
            "owners": owners, "reads": sorted(reads),
            "external": sorted(external), "raw_first": raw_first,
        }

    surfaced = set()
    for info in per_surface.values():
        surfaced.update(info["reads"])

    rows = []
    for path in inventory(root):
        if path in surfaced:
            readers = sorted(h for h, i in per_surface.items()
                             if path in i["reads"])
            klass, note = disposition(path, reg)
            if klass not in ("surfaced_no_pipeline",):
                klass, note = "surfaced", ""
            rows.append({"path": path, "class": klass, "note": note,
                         "readers": readers})
        else:
            klass, note = disposition(path, reg)
            rows.append({"path": path, "class": klass, "note": note,
                         "readers": []})

    declared_html = set(embeds)
    repo_html = {os.path.basename(p) for p in glob.glob(os.path.join(root, "*.html"))}
    unreferenced = sorted(repo_html - declared_html)

    return {
        "surfaces": per_surface,
        "rows": rows,
        "broken": broken,
        "absent_optional": absent_optional,
        "relative_first": relative_first,
        "unreferenced": unreferenced,
        "expected_unreferenced": reg.get("unreferenced_expected", {}),
    }


def summarize(result: dict) -> dict:
    counts = Counter(r["class"] for r in result["rows"])
    return {
        "outputs": len(result["rows"]),
        "surfaced": counts["surfaced"] + counts["surfaced_no_pipeline"],
        "unclassified": counts["unclassified"],
        "by_class": dict(sorted(counts.items())),
        "broken_references": len(result["broken"]),
        "absent_optional_references": len(result["absent_optional"]),
        "relative_first_chains": len(result["relative_first"]),
        "unreferenced_surfaces": len(result["unreferenced"]),
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

CLASS_ORDER = ["surfaced", "surfaced_no_pipeline", "planned", "candidate",
               "internal_ruling", "internal_operational", "methodology",
               "archive", "unclassified"]

CLASS_HEADING = {
    "surfaced": "Surfaced",
    "surfaced_no_pipeline": "Surfaced, no acquisition pipeline",
    "planned": "Closure planned",
    "candidate": "Closure candidate, undecided",
    "internal_ruling": "Internal by recorded ruling",
    "internal_operational": "Internal by nature",
    "methodology": "Methodology layer",
    "archive": "Dated archive",
    "unclassified": "Unclassified (needs a disposition)",
}

CLASS_BLURB = {
    "surfaced": "Read by at least one embedded page.",
    "surfaced_no_pipeline": "Read by a page and carrying its provenance, but nothing refreshes it. The page can say how old the file is; nobody can make it newer.",
    "planned": "Invisible today with a named closure in the current pass.",
    "candidate": "Invisible today, plausibly surfaceable, no decision recorded yet.",
    "internal_ruling": "Invisible on purpose, under a ruling that does not expire with sample size.",
    "internal_operational": "Caches, review queues, ingest scaffolds and CI markers. Counts may be surfaced; rows are not.",
    "methodology": "Generated method and limitation notes that travel with their datasets.",
    "archive": "Dated handoffs and decision records. Historical by construction.",
    "unclassified": "No disposition on record. Each row here is either a closure to implement or a line to add to configs/surfaces.json.",
}


def render(result: dict, summary: dict, reg: dict) -> str:
    L = []
    A = L.append
    A("# Visibility matrix")
    A("")
    A("Generated by visibility_audit.py. Do not hand edit; edit "
      "configs/surfaces.json and regenerate.")
    A("")
    A("What the client-visible surface shows, against what the pipeline "
      "produces. The premise is that an output nobody can see is worth what "
      "an output that does not exist is worth, and that the difference "
      "between the two lists should be a stated decision rather than an "
      "accident of what happened to get an HTML page written for it.")
    A("")
    A(f"- Generated outputs inventoried: {summary['outputs']}")
    A(f"- Read by a client-visible page: {summary['surfaced']}")
    A(f"- Invisible with no disposition on record: {summary['unclassified']}")
    A(f"- Broken references: {summary['broken_references']}")
    A(f"- Absent optional references: "
      f"{summary['absent_optional_references']}")
    A(f"- Fetch chains not raw-first: {summary['relative_first_chains']}")
    A(f"- Repository pages on no Notion page: {summary['unreferenced_surfaces']}")
    A("")

    A("## Surface inventory")
    A("")
    A("Every page under the hub, its embed, and the datasets that embed "
      "loads. Verified against Notion on "
      f"{reg['hub'].get('pages_verified', 'unknown')}.")
    A("")
    A("| Section | Page | Embed | Datasets loaded |")
    A("|---|---|---|---|")
    for html, info in result["surfaces"].items():
        for page in info["owners"]:
            reads = ", ".join(f"`{r}`" for r in info["reads"]) or "none"
            A(f"| {page['section']} | {page['page']} | `{html}` | {reads} |")
    A("")

    if result["unreferenced"]:
        A("### Repository pages on no Notion page")
        A("")
        for html in result["unreferenced"]:
            why = result["expected_unreferenced"].get(
                html, "Not in configs/surfaces.json. Either dead or the "
                      "registry is stale.")
            A(f"- `{html}`: {why}")
        A("")

    if result["broken"]:
        A("### Broken references")
        A("")
        for html, ref in result["broken"]:
            A(f"- `{html}` loads `{ref}`, which does not exist.")
        A("")

    if result["absent_optional"]:
        A("### Absent optional references")
        A("")
        A("Loads a surface is built to survive without. Reported so that an "
          "absence stays a stated expectation rather than an unexplained "
          "hole.")
        A("")
        for html, ref, why in result["absent_optional"]:
            A(f"- `{html}` loads `{ref}` when it exists. {why}")
        A("")

    if result["relative_first"]:
        A("### Fetch chains not raw-first")
        A("")
        A("Standing repository rule: raw.githubusercontent.com comes first in "
          "every fallback chain, because GitHub Pages sends no CORS headers "
          "and an embedded iframe fails silently on the Pages origin.")
        A("")
        for html in result["relative_first"]:
            A(f"- `{html}`")
        A("")

    A("## Output visibility")
    A("")
    by_class: dict[str, list] = {}
    for row in result["rows"]:
        by_class.setdefault(row["class"], []).append(row)
    for klass in CLASS_ORDER:
        rows = by_class.get(klass)
        if not rows:
            continue
        A(f"### {CLASS_HEADING[klass]} ({len(rows)})")
        A("")
        A(CLASS_BLURB[klass])
        A("")
        if klass in ("surfaced",):
            A("| Output | Read by |")
            A("|---|---|")
            for r in sorted(rows, key=lambda r: r["path"]):
                A(f"| `{r['path']}` | {', '.join(r['readers'])} |")
        elif klass == "unclassified":
            for r in sorted(rows, key=lambda r: r["path"]):
                A(f"- `{r['path']}`")
        else:
            A("| Output | Reason |")
            A("|---|---|")
            for r in sorted(rows, key=lambda r: r["path"]):
                A(f"| `{r['path']}` | {r['note']} |")
        A("")

    A("## How to maintain this")
    A("")
    A("1. A new Notion page or embed goes in `configs/surfaces.json` under "
      "`pages`. An embed that is not registered makes its HTML file report "
      "as unreferenced.")
    A("2. A new generated output that should stay invisible gets a line in "
      "`dispositions` with the reason, or matches a pattern in "
      "`disposition_rules`. Anything else lands in Unclassified.")
    A("3. Unclassified is the working list. It is meant to be empty, and a "
      "row sitting there is a decision nobody has made yet.")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    check("raw url normalizes to repo path",
          normalize_ref("https://raw.githubusercontent.com/pricephillips/"
                        "data-center-map/main/data/proposals.csv")
          == "data/proposals.csv")
    check("relative url normalizes",
          normalize_ref("./data/proposals.csv") == "data/proposals.csv")
    check("root-relative url normalizes",
          normalize_ref("/master_opposition.csv") == "master_opposition.csv")
    check("third-party host is external",
          normalize_ref("https://raw.githubusercontent.com/plotly/datasets/"
                        "master/geojson-counties-fips.json") is None)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "data"))
        os.makedirs(os.path.join(tmp, "docs"))
        for rel in ("data/shown.csv", "data/hidden.csv", "data/x_cache.json",
                    "data/notes_notes.md"):
            open(os.path.join(tmp, rel), "w").write("a\n")
        raw = ("https://raw.githubusercontent.com/pricephillips/"
               "data-center-map/main")
        good = os.path.join(tmp, "good.html")
        open(good, "w").write(
            "<script>\n"
            f"const URLS = ['{raw}/data/shown.csv', './data/shown.csv'];\n"
            "a.download = 'data/export_only.csv';\n"
            f"fetch('{raw}/data/gone.csv');\n"
            "window.location.href = 'data/nav_only.csv';\n"
            "</script>\n")
        bad = os.path.join(tmp, "bad.html")
        open(bad, "w").write(
            "<script>Papa.parse('./data/shown.csv', {download:true});</script>")
        open(os.path.join(tmp, "orphan.html"), "w").write("<p>hi</p>")

        reads, external, raw_first = surface_reads(good, tmp)
        check("reads the raw chain target", "data/shown.csv" in reads)
        check("download filename is not a read",
              "data/export_only.csv" not in reads)
        check("navigation target is not a read",
              "data/nav_only.csv" not in surface_reads(good, tmp)[0])
        check("good page is raw-first", raw_first is True)
        concat = os.path.join(tmp, "concat.html")
        open(concat, "w").write(
            f"<script>const RAW = '{raw}';\n"
            "const U = [RAW + '/data/shown.csv', './data/shown.csv'];\n"
            "</script>")
        c_reads, _, c_raw_first = surface_reads(concat, tmp)
        check("concatenated raw url resolves",
              "data/shown.csv" in c_reads)
        check("concatenated chain counts as raw-first", c_raw_first is True)
        open(os.path.join(tmp, "shared.js"), "w").write(
            f"var U = ['{raw}/data/hidden.csv', './data/hidden.csv'];")
        viamod = os.path.join(tmp, "viamod.html")
        open(viamod, "w").write(
            '<script src="./shared.js"></script>\n'
            '<script src="https://cdn.example.com/lib.js"></script>')
        m_reads, _, _ = surface_reads(viamod, tmp)
        check("dataset loaded through a shared module counts",
              "data/hidden.csv" in m_reads)
        check("third-party script is not followed",
              local_modules('<script src="https://cdn.example.com/lib.js">'
                            "</script>") == [])
        reads_b, _, raw_first_b = surface_reads(bad, tmp)
        check("relative-only page is not raw-first", raw_first_b is False)

        reg = {
            "hub": {"pages_verified": "test"},
            "pages": [
                {"section": "S", "page": "Good", "notion_url": "u",
                 "embeds": ["good.html"]},
                {"section": "S", "page": "Bad", "notion_url": "u",
                 "embeds": ["bad.html"]},
            ],
            "unreferenced_expected": {},
            "dispositions": {
                "data/hidden.csv": ["internal_ruling", "gated"],
            },
            "disposition_rules": [
                ["*_cache.json", "internal_operational", "cache"],
                ["*_notes.md", "methodology", "method"],
            ],
        }
        res = audit(reg, root=tmp)
        s = summarize(res)
        by = {r["path"]: r for r in res["rows"]}
        check("shown.csv classified surfaced",
              by["data/shown.csv"]["class"] == "surfaced")
        check("hidden.csv takes its override",
              by["data/hidden.csv"]["class"] == "internal_ruling")
        check("cache matches a pattern rule",
              by["data/x_cache.json"]["class"] == "internal_operational")
        check("notes matches a pattern rule",
              by["data/notes_notes.md"]["class"] == "methodology")
        check("missing target is a broken reference",
              ("good.html", "data/gone.csv") in res["broken"])
        reg_opt = dict(reg, optional_references={
            "good.html": {"data/gone.csv": "expected while the gate is closed"}})
        res_opt = audit(reg_opt, root=tmp)
        check("declared optional absence is not broken",
              not res_opt["broken"] and len(res_opt["absent_optional"]) == 1)
        check("relative-only page reported", res["relative_first"] == ["bad.html"])
        check("unregistered page reported",
              "orphan.html" in res["unreferenced"])
        check("summary counts surfaced", s["surfaced"] == 1)
        check("nothing unclassified in the fixture", s["unclassified"] == 0)
        md = render(res, s, reg)
        check("renders a matrix", "# Visibility matrix" in md
              and "Broken references" in md)

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit nonzero if any output is unclassified or any "
                         "reference is broken")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    reg = read_registry()
    result = audit(reg)
    summary = summarize(result)
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(result, summary, reg))

    print(f"wrote {os.path.relpath(OUT_MD, HERE)}")
    for k, v in summary.items():
        if k != "by_class":
            print(f"  {k}: {v}")
    print("  by_class: " + ", ".join(f"{k}={v}" for k, v in
                                     summary["by_class"].items()))
    if args.strict and (summary["unclassified"] or
                        summary["broken_references"]):
        print("strict: unclassified outputs or broken references present")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
