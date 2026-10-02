#!/usr/bin/env python3
"""
report_data.py

The one place a deliverable's numbers come from (spec 010). Every renderer
(render_location_report.py, render_county_pdf.py, charts.py) asks this module
for a county's facts; none of them reads a platform file itself, and no
template or definition carries a typed number. Principle VI, made structural:
a number in a report is a Fact read from a committed file at run time, and the
Fact records the file, row and column it came from so the re-derivation check
can open the same cell again.

What it also owns, because every renderer needs the same answer:
  definitions  configs/definitions.json, printed "term (definition)" on first
               use and as the bare term afterwards (FR-004)
  refusals     group-level outcome columns, screener composites and county rate
               inputs are never loaded (Principle VI; edge case "a figure would
               need a group-level outcome column")
  plate        the spec 013 slot contract (specs/010-deliverable-generation/
               plan.md): sidecar caption and credits, and the four refusals
  checks       vocabulary, em-dash, first-use definitions, the no-rate rule,
               and the docx XML rules of FR-002

Reads
  data/county_policy_scores.csv, data/county_policy_intervals.csv,
  data/county_benchmarks.csv, data/county_adjacency.csv,
  data/county_census_features.csv, data/external_restriction_census.csv,
  master_opposition_clean.csv, configs/definitions.json, DATA_NOTICES.md,
  outputs/plates/<id>.json (only with --plate)
Writes
  nothing

Usage
  python scripts/report_data.py --fips 13255     print the facts as JSON
  python scripts/report_data.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import county_aggregator as ca  # noqa: E402  (the enacted-label rule, not a copy)
import export_geolibre as eg  # noqa: E402  (commit SHA, refused columns, labels)

SCORES = "data/county_policy_scores.csv"
INTERVALS = "data/county_policy_intervals.csv"
BENCHMARKS = "data/county_benchmarks.csv"
ADJACENCY = "data/county_adjacency.csv"
CENSUS_FEATURES = "data/county_census_features.csv"
RESTRICTION_CENSUS = "data/external_restriction_census.csv"
CASES = "master_opposition_clean.csv"
DEFINITIONS = "configs/definitions.json"
DATA_NOTICES = "DATA_NOTICES.md"

INPUTS = [SCORES, INTERVALS, BENCHMARKS, ADJACENCY, CENSUS_FEATURES,
          RESTRICTION_CENSUS, CASES, DEFINITIONS]

# Columns each file may contribute. Anything else in the file is never read
# into memory, so a refused column cannot reach a template by accident.
ALLOW = {
    SCORES: ["fips", "calibrated_score", "score_decile", "has_enacted_restrictive"],
    INTERVALS: ["fips", "va_p_lower", "va_p_upper"],
    BENCHMARKS: ["fips", "county_name", "state", "calibrated_score_pct_national",
                 "calibrated_score_pct_state", "calibrated_score_peer_median", "peer_n",
                 "peer_fips"],
    ADJACENCY: ["fips", "neighbor_fips"],
    CENSUS_FEATURES: ["fips", "county_name"],
    RESTRICTION_CENSUS: ["state", "county", "instrument", "census_status",
                         "date_enacted", "source"],
    # Community Outcome is read for the label rule's direction guard only
    # (county_aggregator, "approved" polarity) and is never printed.
    CASES: ["Date", "City", "County", "State", "Opposition Type", "Status",
            "Community Outcome", "outcome_defensible", "qc_jurisdiction_key",
            "Project Name", "Source URL", "verification_status", "data_source"],
}
NEVER_PRINTED = {"Community Outcome", "verification_status", "data_source"}

# Principle VI: county-level opposition rates are not computable at current
# density and group-level outcome columns are permanently internal.
RATE_INPUTS = {"peer_restriction_rate", "n_decided", "n_blocked_confirmed",
               "n_advanced_confirmed", "decided_share", "opposition_rate"}
RATE_PREFIXES = ("blocked_share_of_decided",)
REFUSED = set(eg.GROUP_LEVEL) | set(eg.SCREENER_COMPOSITE) | RATE_INPUTS


def is_refused(column: str) -> bool:
    c = (column or "").strip().lower()
    return c in REFUSED or c.startswith(RATE_PREFIXES)


OUTCOME_LABEL = dict(eg.OUTCOME_LABEL)
LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)  # = leak_audit.LEAK_RE
EM_DASH = "—"
DIGIT_RE = re.compile(r"\d")
FIPS_RE = re.compile(r"^\d{5}$")
SPLIT_COUNTY_RE = re.compile(r"\s*[;/]\s*")


class ReportError(Exception):
    """A refusal or a failed check. The message says what and where."""


# --------------------------------------------------------------------------
# formatting: one rule per kind of number, shared with --verify
# --------------------------------------------------------------------------

def _dec(raw: str) -> Decimal:
    try:
        return Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ReportError(f"not a number: {raw!r}") from exc


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt(raw, rule: str) -> str:
    if rule == "score2":
        return str(_dec(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if rule == "ordinal":
        return ordinal(int(_dec(raw).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    if rule in ("int", "count"):
        return str(int(_dec(raw)))
    raise ReportError(f"unknown format rule {rule!r}")


# --------------------------------------------------------------------------
# file access
# --------------------------------------------------------------------------

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(rel: str, root: str = ROOT) -> list[dict]:
    """Rows of a committed CSV, restricted to the file's allowlisted columns."""
    cols = ALLOW[rel]
    bad = [c for c in cols if is_refused(c)]
    if bad:
        raise ReportError(f"{rel}: allowlist names refused column(s) {bad}")
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        raise ReportError(f"input missing: {rel}")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in cols if c not in (reader.fieldnames or [])]
        if missing:
            raise ReportError(f"{rel}: expected column(s) missing: {missing}")
        return [{c: (r.get(c) or "") for c in cols} for r in reader]


def index(rows: list[dict], key: str = "fips") -> dict:
    return {r[key].zfill(5): r for r in rows}


def git(*args: str, root: str = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def as_of(inputs: list[str], root: str = ROOT) -> str:
    """Latest commit date among the inputs: the data vintage of the report."""
    dates = []
    for rel in inputs:
        r = git("log", "-1", "--format=%cs", "--", rel, root=root)
        if r.returncode == 0 and r.stdout.strip():
            dates.append(r.stdout.strip())
    if not dates:
        raise ReportError("no input has a commit date; is this a git checkout?")
    return max(dates)


# --------------------------------------------------------------------------
# definitions, first use
# --------------------------------------------------------------------------

def load_definitions(root: str = ROOT) -> dict:
    with open(os.path.join(root, DEFINITIONS), encoding="utf-8") as fh:
        d = json.load(fh)
    for key, t in d["terms"].items():
        if DIGIT_RE.search(t["definition"]) or DIGIT_RE.search(t["term"]):
            raise ReportError(f"{DEFINITIONS}: {key} carries a digit; numbers come from facts")
    for key, text in d.get("notices", {}).items():
        if DIGIT_RE.search(text):
            raise ReportError(f"{DEFINITIONS}: notice {key} carries a digit")
    return d


class Definer:
    """`define(key)`: 'term (definition)' the first time, 'term' after.

    docxtpl and Jinja render the document in order, so "first" follows the
    template's own section order, whatever a client template does with it.
    """

    def __init__(self, defs: dict):
        self.terms = defs["terms"]
        self.used: list[str] = []

    def __call__(self, key: str) -> str:
        if key not in self.terms:
            raise ReportError(f"define({key!r}): no such term in {DEFINITIONS}")
        t = self.terms[key]
        if key in self.used:
            return t["term"]
        self.used.append(key)
        return f"{t['term']} ({t['definition']})"


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------

class Facts:
    """Formatted values plus the provenance of each one."""

    def __init__(self, root: str):
        self.root = root
        self.items: list[dict] = []
        self._hash: dict[str, str] = {}

    def _sha(self, rel: str) -> str:
        if rel not in self._hash:
            self._hash[rel] = sha256(os.path.join(self.root, rel))
        return self._hash[rel]

    def cell(self, name: str, rel: str, key_column: str, key: str, column: str,
             raw: str, rule: str) -> str:
        if is_refused(column):
            raise ReportError(f"refused column {column!r} requested for {name}")
        if str(raw).strip() == "":
            raise ReportError(f"{rel}: {column} is blank for {key_column}={key} ({name})")
        out = fmt(raw, rule)
        self.items.append({"name": name, "formatted": out, "raw": str(raw), "rule": rule,
                           "file": rel, "sha256": self._sha(rel), "key_column": key_column,
                           "key": key, "column": column})
        return out

    def count(self, name: str, rel: str, key: str, filter_name: str, n: int) -> str:
        self.items.append({"name": name, "formatted": str(n), "raw": str(n), "rule": "count",
                           "file": rel, "sha256": self._sha(rel), "key_column": "fips",
                           "key": key, "column": filter_name})
        return str(n)


def county_resolver(root: str = ROOT) -> dict:
    """(normalized county, state) -> fips, as county_aggregator.load_frame builds it."""
    out = {}
    for r in read_rows(CENSUS_FEATURES, root):
        cn, _, stname = r["county_name"].rpartition(",")
        ab = ca.norm_state(stname)
        if ab:
            out[(ca.norm_county(cn), ab)] = r["fips"].zfill(5)
    return out


def row_fips(row: dict, resolver: dict) -> list[str]:
    """Every county a case row names (a cell may list several, as in county-profile.html)."""
    st = ca.norm_state(row.get("State"))
    out = []
    for part in SPLIT_COUNTY_RE.split(row.get("County") or ""):
        f = resolver.get((ca.norm_county(part), st)) if part.strip() else None
        if f and f not in out:
            out.append(f)
    return out


def countable(row: dict) -> bool:
    try:
        import verification_status as vs
    except Exception:  # noqa: BLE001  (same no-op guard as county_aggregator)
        return True
    return vs.is_countable(row)


def is_enacted_row(row: dict) -> bool:
    """county_aggregator's has_enacted_restrictive rule for one master row."""
    toks = ca._type_tokens(row.get("Opposition Type"))
    status = (row.get("Status") or "").strip().lower()
    if not (toks & ca.RESTRICTIVE_TYPES and status in ca.ENACTED_STATUSES):
        return False
    if status in ca.DIRECTION_AMBIGUOUS_STATUSES:
        return (row.get("Community Outcome") or "").strip().lower() == "win"
    return True


def _humanize(value: str) -> str:
    v = (value or "").strip().replace("_", " ")
    return v[:1].upper() + v[1:] if v else ""


def _level(row: dict) -> str:
    key = (row.get("qc_jurisdiction_key") or "").strip()
    return key.rsplit("::", 1)[-1] if "::" in key else ""


def _case_view(row: dict) -> dict:
    grade = (row.get("outcome_defensible") or "").strip()
    if grade not in OUTCOME_LABEL:
        raise ReportError(f"{CASES}: outcome {grade!r} is outside the outcome ladder")
    return {
        "date": (row.get("Date") or "").strip() or "Undated",
        "place": (row.get("City") or row.get("County") or "").strip(),
        "level": _level(row),
        "type": _humanize(row.get("Opposition Type")),
        "status": _humanize(row.get("Status")),
        "outcome": OUTCOME_LABEL[grade],
        "project": (row.get("Project Name") or "").strip(),
        "url": (row.get("Source URL") or "").strip(),
    }


def build(fips: str, root: str = ROOT, *, sha: str | None = None,
          allow_uncommitted: bool = False, draft: bool = False, client: str = "") -> dict:
    """Every fact a county deliverable prints, with provenance."""
    fips = str(fips).strip()
    if not FIPS_RE.match(fips):
        raise ReportError(f"FIPS must be five digits: {fips!r}")
    F = Facts(root)
    scores = index(read_rows(SCORES, root))
    if fips not in scores:
        raise ReportError(f"FIPS {fips} is not in {SCORES}")
    intervals = index(read_rows(INTERVALS, root))
    bench = index(read_rows(BENCHMARKS, root))
    if fips not in intervals or fips not in bench:
        raise ReportError(f"FIPS {fips} is missing from {INTERVALS} or {BENCHMARKS}")
    s, iv, b = scores[fips], intervals[fips], bench[fips]
    county_name, _, state_name = b["county_name"].rpartition(",")
    county_name, state_name = county_name.strip(), state_name.strip()

    resolver = county_resolver(root)
    cases_all = [r for r in read_rows(CASES, root) if countable(r)]
    by_fips: dict[str, list[dict]] = {}
    for r in cases_all:
        for f in row_fips(r, resolver):
            by_fips.setdefault(f, []).append(r)

    # Enacted history: the label from the model frame, rows from the clean feed
    enacted = s["has_enacted_restrictive"].strip() == "1"
    hist_rows = [_case_view(r) | {"census": ca.is_census_derived(r)}
                 for r in by_fips.get(fips, []) if is_enacted_row(r)]
    hist_rows.sort(key=lambda r: r["date"])
    census_rows = []
    for r in read_rows(RESTRICTION_CENSUS, root):
        if resolver.get((ca.norm_county(r["county"]), ca.norm_state(r["state"]))) == fips:
            census_rows.append({"date": r["date_enacted"] or "Undated",
                                "instrument": _humanize(r["instrument"]),
                                "status": _humanize(r["census_status"]),
                                "source": r["source"]})
    census_rows.sort(key=lambda r: r["date"])
    if not enacted:
        hist_rows, census_rows = [], []

    score = {
        "calibrated": F.cell("score.calibrated", SCORES, "fips", fips, "calibrated_score",
                             s["calibrated_score"], "score2"),
        "interval_low": F.cell("score.interval_low", INTERVALS, "fips", fips, "va_p_lower",
                               iv["va_p_lower"], "score2"),
        "interval_high": F.cell("score.interval_high", INTERVALS, "fips", fips, "va_p_upper",
                                iv["va_p_upper"], "score2"),
        "decile": F.cell("score.decile", SCORES, "fips", fips, "score_decile",
                         s["score_decile"], "int"),
        "pct_national": F.cell("score.pct_national", BENCHMARKS, "fips", fips,
                               "calibrated_score_pct_national",
                               b["calibrated_score_pct_national"], "ordinal"),
        "pct_state": F.cell("score.pct_state", BENCHMARKS, "fips", fips,
                            "calibrated_score_pct_state", b["calibrated_score_pct_state"],
                            "ordinal"),
        "peer_median": F.cell("score.peer_median", BENCHMARKS, "fips", fips,
                              "calibrated_score_peer_median",
                              b["calibrated_score_peer_median"], "score2"),
        "peer_n": F.cell("score.peer_n", BENCHMARKS, "fips", fips, "peer_n", b["peer_n"], "int"),
    }

    case_rows = sorted((_case_view(r) for r in by_fips.get(fips, [])), key=lambda r: r["date"])
    cases = {"count": F.count("cases.count", CASES, fips, "cases_in_county", len(case_rows)),
             "rows": case_rows}

    nbr_fips = sorted({r["neighbor_fips"].zfill(5) for r in read_rows(ADJACENCY, root)
                       if r["fips"].zfill(5) == fips} - {fips})
    nbr_rows = []
    for nf in nbr_fips:
        if nf not in scores or nf not in bench:
            continue
        name, _, st_name = bench[nf]["county_name"].rpartition(",")
        nbr_rows.append({
            "fips": nf, "county": name.strip(), "state": bench[nf]["state"],
            "enacted": "Yes" if scores[nf]["has_enacted_restrictive"].strip() == "1" else "No",
            "decile": F.cell(f"neighbors.{nf}.decile", SCORES, "fips", nf, "score_decile",
                             scores[nf]["score_decile"], "int"),
            "cases": F.count(f"neighbors.{nf}.cases", CASES, nf, "cases_in_county",
                             len(by_fips.get(nf, []))),
        })
    n_enacted = sum(1 for r in nbr_rows if r["enacted"] == "Yes")
    neighbors = {"count": F.count("neighbors.count", ADJACENCY, fips, "neighbors", len(nbr_rows)),
                 "n_enacted": F.count("neighbors.n_enacted", SCORES, fips,
                                      "neighbors_enacted", n_enacted),
                 "rows": nbr_rows}

    if sha is None:
        sha = eg.commit_sha(INPUTS, root=root, allow_uncommitted=allow_uncommitted)
    defs = load_definitions(root)
    report = {
        "fips": fips, "county_name": county_name, "state_name": state_name,
        "state": b["state"], "as_of": as_of(INPUTS, root), "commit": sha,
        "commit_short": sha[:12] + (sha[40:] if len(sha) > 40 else ""),
        "draft": bool(draft), "client": client,
        "title": f"{county_name}, {state_name}: location report",
    }
    return {
        "report": report,
        "history": {"enacted": enacted, "unitemized": enacted and not hist_rows and not census_rows,
                    "rows": hist_rows, "census_rows": census_rows},
        "score": score,
        "cases": cases,
        "neighbors": neighbors,
        "sources": {"files": [{"path": p, "sha": F._sha(p)[:12]} for p in INPUTS],
                    "notice": defs["notices"]["descriptive"],
                    "interval_notice": defs["notices"]["interval_first"],
                    "unitemized_notice": defs["notices"]["unitemized"]},
        "definitions": defs,
        "facts": F.items,
    }


def peer_fips(fips: str, root: str = ROOT) -> list[str]:
    """The county's similarity peers (county_benchmarks.py MATCHING), in file order."""
    row = index(read_rows(BENCHMARKS, root)).get(fips) or {}
    return [p.zfill(5) for p in (row.get("peer_fips") or "").split(";") if p.strip()]


def national_scores(root: str = ROOT) -> list[float]:
    """Every county's calibrated score, for the distribution chart."""
    return [float(r["calibrated_score"]) for r in read_rows(SCORES, root)
            if r["calibrated_score"].strip()]


# --------------------------------------------------------------------------
# the plate slot (spec 013 contract, specs/010-deliverable-generation/plan.md)
# --------------------------------------------------------------------------

def _notice_3dep(root: str) -> str:
    with open(os.path.join(root, DATA_NOTICES), encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r'"(Elevation: U\.S\. Geological Survey, 3D Elevation Program)\.?"', text)
    if not m:
        raise ReportError(f"{DATA_NOTICES}: the USGS 3DEP courtesy credit is not recorded")
    return m.group(1)


def load_plate(png: str, *, draft: bool, root: str = ROOT) -> dict:
    """The plate image plus its sidecar caption and credits, or a refusal."""
    png_abs = png if os.path.isabs(png) else os.path.join(root, png)
    side = os.path.splitext(png_abs)[0] + ".json"
    if not os.path.exists(png_abs):
        raise ReportError(f"plate image missing: {png}")
    if not os.path.exists(side):
        raise ReportError(f"plate sidecar missing: {os.path.relpath(side, root)}")
    with open(side, encoding="utf-8") as fh:
        meta = json.load(fh)
    for key in ("title", "credits", "commit_sha", "preview"):
        if key not in meta:
            raise ReportError(f"plate sidecar lacks {key!r}: {os.path.relpath(side, root)}")
    sha = str(meta["commit_sha"])
    if git("merge-base", "--is-ancestor", sha, "HEAD", root=root).returncode != 0:
        raise ReportError(f"plate commit {sha} is not an ancestor of this report's commit")
    if meta["preview"] and not draft:
        raise ReportError("plate is a preview render; only a --draft report may carry it")
    stem = _notice_3dep(root)
    for line in meta["credits"]:
        if line.startswith("Elevation") and not line.startswith(stem):
            raise ReportError(f"plate credit does not match {DATA_NOTICES}: {line!r}")
    # The caption is the sidecar's title as its first sentence, then its
    # subtitle; only a closing period is added when the title has none.
    title = meta["title"].rstrip()
    title += "" if title.endswith((".", "!", "?")) else "."
    caption = [title, meta.get("subtitle") or ""]
    check_text("\n".join(caption + list(meta["credits"])), where="plate caption")
    return {"png": png_abs, "sidecar": side, "title": title,
            "subtitle": meta.get("subtitle") or "", "credits": list(meta["credits"]),
            "commit_sha": sha, "preview": bool(meta["preview"])}


# --------------------------------------------------------------------------
# checks on rendered text and XML
# --------------------------------------------------------------------------

def check_text(text: str, where: str = "report") -> None:
    hits = sorted({m.group(0) for m in LEAK_RE.finditer(text)})
    if hits:
        raise ReportError(f"{where}: scorekeeping vocabulary {hits} (leak_audit blocking tier)")
    if EM_DASH in text:
        raise ReportError(f"{where}: em-dash present")


def check_first_use(text: str, defs: dict) -> None:
    """Each defined term's first occurrence carries its definition."""
    for key, t in defs["terms"].items():
        i = text.lower().find(t["term"].lower())
        if i < 0:
            continue
        want = f"{t['term']} ({t['definition']})".lower()
        if text.lower()[i:i + len(want)] != want:
            raise ReportError(f"first use of {t['term']!r} lacks its definition")


def check_no_rate(history_text: str) -> None:
    """A county with no enacted restriction: one plain sentence, no number, no rate."""
    if DIGIT_RE.search(history_text) or "%" in history_text or " rate" in history_text.lower():
        raise ReportError("history of a county with no enacted restriction prints a number or rate")
    if "no enacted restriction" not in history_text.lower():
        raise ReportError("history of a county with no enacted restriction does not say so")


W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def check_docx_xml(xml: bytes) -> None:
    """FR-002: paraId below 0x80000000, explicit tblGrid, hyperlinks beside runs."""
    from lxml import etree

    root = etree.fromstring(xml)
    for el in root.iter():
        pid = el.get(f"{{{W14}}}paraId")
        if pid is not None and int(pid, 16) >= 0x80000000:
            raise ReportError(f"w14:paraId {pid} is not below 0x80000000")
    for tbl in root.iter(f"{{{W}}}tbl"):
        if tbl.find(f"{{{W}}}tblGrid") is None:
            raise ReportError("a table lacks an explicit w:tblGrid")
    for hl in root.iter(f"{{{W}}}hyperlink"):
        if hl.getparent().tag == f"{{{W}}}r":
            raise ReportError("a hyperlink sits inside a run")
    for t in root.iter(f"{{{W}}}t"):
        if len(t):
            raise ReportError("a w:t element carries child elements (use {{r }} for RichText)")


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def write_fixture(root: str, *, plate: dict | None = None) -> str:
    """A tiny committed platform under root: two scored counties and one neighbor.

    13001 has an enacted city moratorium and one advanced case; 13003 has no
    enacted restriction. Returns the fixture's HEAD SHA.
    """
    files = {
        SCORES: "fips,raw_oof_score,calibrated_score,score_decile,has_enacted_restrictive\n"
                "13001,0.2,0.1717,8,1\n13003,0.1,0.0456,2,0\n13005,0.3,0.3,10,1\n",
        INTERVALS: "fips,calibrated_score,has_enacted_restrictive,va_p_lower,va_p_upper,va_width\n"
                   "13001,0.1717,1,0.175,0.264,0.089\n13003,0.0456,0,0.031,0.075,0.044\n"
                   "13005,0.3,1,0.25,0.35,0.1\n",
        BENCHMARKS: "fips,county_name,state,calibrated_score_pct_national,calibrated_score_pct_state,"
                    "calibrated_score_peer_median,peer_n,peer_fips,peer_restriction_rate\n"
                    '13001,"Alpha County, Georgia",GA,73.5,78.9,0.197,2,13003;13005,0.5\n'
                    '13003,"Beta County, Georgia",GA,12.4,20.5,0.06,2,13001;13005,0.1\n'
                    '13005,"Gamma County, Georgia",GA,95.0,96.0,0.3,2,13001;13003,0.6\n',
        ADJACENCY: "fips,neighbor_fips,shared_edges\n13001,13003,2\n13001,13005,1\n"
                   "13003,13001,2\n13005,13001,1\n",
        CENSUS_FEATURES: "fips,county_name,population\n13001,\"Alpha County, Georgia\",1\n"
                         "13003,\"Beta County, Georgia\",1\n13005,\"Gamma County, Georgia\",1\n",
        RESTRICTION_CENSUS: "state,county,instrument,census_status,date_enacted,source\n"
                            "GA,Gamma County,moratorium,active,2025-09-01,census citation\n",
        CASES: "Date,City,County,State,Opposition Type,Status,Community Outcome,outcome_defensible,"
               "qc_jurisdiction_key,Project Name,Source URL,verification_status,data_source\n"
               "2026-01-13,Griffin (Alpha County),Alpha County,GA,moratorium,passed,win,"
               "blocked_confirmed,georgia::alpha::city,,https://example.org/a,sourced,\n"
               "2026-01-22,Alpha County,Alpha County,GA,public_comment,approved,pending,"
               "advanced_confirmed,georgia::alpha::county,Campus One,https://example.org/b,sourced,\n"
               "2025-05-01,Gamma County,Gamma County; Alpha County,GA,public_comment,pending,"
               "pending,pending,georgia::gamma::county,,https://example.org/c,sourced,\n",
        DEFINITIONS: open(os.path.join(ROOT, DEFINITIONS), encoding="utf-8").read(),
        DATA_NOTICES: '"Elevation: U.S. Geological Survey, 3D Elevation Program." credit\n',
    }
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    env = ["-c", "user.email=selftest@example.org", "-c", "user.name=selftest"]
    git("init", "-q", root=root)
    git("add", "-A", root=root)
    git(*env, "commit", "-q", "-m", "fixture", root=root)
    return git("rev-parse", "HEAD", root=root).stdout.strip()


def write_plate(root: str, sha: str, *, preview: bool = False, credit: str | None = None,
                sidecar: bool = True) -> str:
    """A 3:2 fixture plate PNG plus sidecar, as render_terrain_plate.py writes them."""
    import struct
    import zlib

    w, h = 30, 20
    raw = b"".join(b"\x00" + b"\x80\x90\xa0" * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    os.makedirs(os.path.join(root, "outputs", "plates"), exist_ok=True)
    stem = os.path.join(root, "outputs", "plates", "fixture" + ("-preview" if preview else ""))
    with open(stem + ".png", "wb") as fh:
        fh.write(png)
    if sidecar:
        meta = {"id": "fixture", "title": "Alpha County has two tracked cases",
                "subtitle": "Alpha County, Georgia.", "preview": preview, "commit_sha": sha,
                "credits": [credit or "Elevation: U.S. Geological Survey, 3D Elevation Program "
                            "(3DEP 1/3 arc-second DEM), accessed 2026-10-01; public domain.",
                            f"Cases: this platform's opposition tracker, sourced records, commit {sha[:12]}."]}
        with open(stem + ".json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump(meta, fh)
    return stem + ".png"


def _selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok) -> None:
        checks.append((name, bool(ok)))

    def raises(fn, *a, **k) -> bool:
        try:
            fn(*a, **k)
        except ReportError:
            return True
        return False

    check("score2 rounds half up", fmt("0.175", "score2") == "0.18" and fmt("0.1717", "score2") == "0.17")
    check("ordinal percentile", fmt("73.5", "ordinal") == "74th" and fmt("11.2", "ordinal") == "11th"
          and fmt("22", "ordinal") == "22nd" and fmt("3", "ordinal") == "3rd")
    check("refused group column", is_refused("blocked_share") and is_refused("decided"))
    check("refused county rate inputs", is_refused("peer_restriction_rate")
          and is_refused("blocked_share_of_decided_pct_peer"))
    check("allowlists carry no refused column",
          not any(is_refused(c) for cols in ALLOW.values() for c in cols))

    defs = load_definitions(ROOT)
    d = Definer(defs)
    first, again = d("decile"), d("decile")
    check("define: definition on first use only", first.startswith("decile (") and again == "decile")
    check("define: unknown term refused", raises(d, "nope"))
    check("definitions carry no digits",
          not any(DIGIT_RE.search(t["definition"]) for t in defs["terms"].values()))
    txt = f"The {d('calibrated_score')} is shown. Later the calibrated score again."
    check("first-use check passes defined text", not raises(check_first_use, txt, defs))
    check("first-use check fails undefined text",
          raises(check_first_use, "The calibrated score is shown.", defs))
    check("vocabulary refused", raises(check_text, "a big win"))
    check("em-dash refused", raises(check_text, "a — b"))
    check("no-rate: plain sentence passes",
          not raises(check_no_rate, "No enacted restriction is on record for Beta County, Georgia."))
    check("no-rate: a number fails", raises(check_no_rate, "No enacted restriction; rate 0%."))

    tmp = tempfile.mkdtemp(prefix="report_data_")
    sha = write_fixture(tmp)
    f = build("13001", tmp)
    check("enacted county: history rows from the label rule",
          f["history"]["enacted"] and [r["level"] for r in f["history"]["rows"]] == ["city"])
    check("approved without win is not enacted",
          all(r["status"] != "Approved" for r in f["history"]["rows"]))
    check("score formatted from the file", f["score"]["calibrated"] == "0.17"
          and f["score"]["interval_low"] == "0.18" and f["score"]["pct_national"] == "74th")
    check("multi-county cell reaches both counties", f["cases"]["count"] == "3")
    check("neighbors with enacted flag and decile",
          [(r["county"], r["enacted"], r["decile"]) for r in f["neighbors"]["rows"]]
          == [("Beta County", "No", "2"), ("Gamma County", "Yes", "10")])
    check("facts carry provenance", all({"file", "sha256", "column", "rule"} <= set(x)
                                        for x in f["facts"]))
    check("no fact uses a refused column", not any(is_refused(x["column"]) for x in f["facts"]))
    check("commit is the fixture HEAD", f["report"]["commit"] == sha)
    g = build("13003", tmp)
    check("no-enacted county has no history rows",
          not g["history"]["enacted"] and not g["history"]["rows"] and not g["history"]["census_rows"])
    h = build("13005", tmp)
    check("census rows matched by county name", [r["instrument"] for r in h["history"]["census_rows"]]
          == ["Moratorium"])
    check("unknown FIPS refused", raises(build, "99999", tmp))
    check("malformed FIPS refused", raises(build, "1300", tmp))

    png = write_plate(tmp, sha)
    p = load_plate(png, draft=False, root=tmp)
    check("plate caption and credits from the sidecar",
          p["title"].startswith("Alpha County") and len(p["credits"]) == 2)
    check("plate: missing sidecar refused",
          raises(load_plate, write_plate(tmp, sha, sidecar=False).replace("fixture.png", "nosuch.png"),
                 draft=False, root=tmp))
    os.remove(os.path.join(tmp, "outputs", "plates", "fixture.json"))
    check("plate: sidecar removed is refused", raises(load_plate, png, draft=False, root=tmp))
    write_plate(tmp, "0" * 40)
    check("plate: non-ancestor commit refused", raises(load_plate, png, draft=False, root=tmp))
    write_plate(tmp, sha + "-dirty")
    check("plate: dirty commit refused", raises(load_plate, png, draft=False, root=tmp))
    prev = write_plate(tmp, sha, preview=True)
    check("plate: preview refused on a final report", raises(load_plate, prev, draft=False, root=tmp))
    check("plate: preview allowed on a draft", not raises(load_plate, prev, draft=True, root=tmp))
    write_plate(tmp, sha, credit="Elevation: someone else's DEM")
    check("plate: credit must match DATA_NOTICES.md", raises(load_plate, png, draft=False, root=tmp))

    good = (f'<w:document xmlns:w="{W}" xmlns:w14="{W14}"><w:body><w:p w14:paraId="1A2B3C4D">'
            f'<w:hyperlink><w:r><w:t>x</w:t></w:r></w:hyperlink></w:p>'
            f'<w:tbl><w:tblGrid/></w:tbl></w:body></w:document>').encode()
    check("FR-002 XML passes a good document", not raises(check_docx_xml, good))
    check("FR-002 paraId limit", raises(check_docx_xml, good.replace(b"1A2B3C4D", b"8A2B3C4D")))
    check("FR-002 explicit tblGrid", raises(check_docx_xml, good.replace(b"<w:tblGrid/>", b"")))
    check("FR-002 hyperlink beside runs",
          raises(check_docx_xml, good.replace(b"<w:hyperlink><w:r><w:t>x</w:t></w:r></w:hyperlink>",
                                              b"<w:r><w:hyperlink/></w:r>")))

    fails = 0
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    print(f"{len(checks) - fails}/{len(checks)} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fips")
    ap.add_argument("--allow-uncommitted", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.fips:
        ap.error("--fips or --selftest is required")
    try:
        facts = build(args.fips, allow_uncommitted=args.allow_uncommitted)
    except (ReportError, eg.ExportError) as exc:
        print(f"report_data: {exc}", file=sys.stderr)
        return 1
    facts.pop("definitions")
    print(json.dumps(facts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
