#!/usr/bin/env python3
"""
integration_audit.py

Validates configs/integrations.json, the registry of external tools the
platform adopts, defers, or has ruled out. The registry exists so that a tool
enters the codebase through one reviewed decision rather than through whichever
session happened to reach for it, and so the reasons a tool was ruled out
survive the session that ruled it out.

Rules enforced:
  selected   license on the allowlist and not on the blocklist; a version
             constraint; a spec and session that exist; a source URL; at least
             one target; a runs_in value from the fixed set.
  deferred   a license that is not blocked, a trigger condition, and a reason.
  eliminated a category from the fixed set and a reason.
  all        unique ids; `products` values, where present, from the declared
             product_lines.
  copyleft   a license ending in "-cli" (for example GPL-2.0-cli) is accepted
             only for kind "cli": the program runs as a separate process and is
             never imported, so its license does not attach to repo code. The
             repo has no LICENSE file and client products may ship privately.
  boundaries repo code (*.py, *.js, *.html, *.yml, *.sh) never references the
             public GeoLibre sharing host (entry geolibre-share), and never
             calls a forge3d Pro API (entry forge3d-pro): the license-key
             setter, MapPlate, SVG/PDF vector export, building import, or
             scene bundles. Specs, docs and configs may name them; code may
             not. Specs 012 US1 and 013 US2.

Reads
  configs/integrations.json
  repo code files, for the boundary checks
Writes
  nothing (prints a report; exit 1 on any violation)

Usage
  python3 integration_audit.py              validate and print the summary
  python3 integration_audit.py --session 3  list the tools scheduled for session 3
  python3 integration_audit.py --selftest   run built-in checks, no file I/O
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REGISTRY = os.path.join("configs", "integrations.json")
STATUSES = {"selected", "deferred", "eliminated"}
RUNS_IN = {"ci", "local", "browser", "agent", "reference"}
CATEGORIES = {
    "redundant-existing", "redundant-selected", "license-risk",
    "maintenance-risk", "principle-conflict", "defensibility-risk",
    "cost-infra", "out-of-scope", "already-built",
}


CLI_SUFFIX = "-cli"

# Boundary checks. Each rule is (registry id, pattern, needs_forge3d). A rule
# with needs_forge3d only fires in a file that also mentions forge3d, because
# names like export_pdf are generic and spec 010 renders PDFs with WeasyPrint.
# The patterns are split so this file never matches itself.
BOUNDARY_RULES = [
    ("geolibre-share", re.compile(r"share\.geolibre" r"\.app", re.I), False),
    ("forge3d-pro", re.compile(r"\bset_license" r"_key\b"), False),
    ("forge3d-pro", re.compile(r"\bMap" r"Plate\w*"), False),
    ("forge3d-pro", re.compile(r"\bexport_(?:svg|pdf)\b|forge3d\.ex" r"port\b"), True),
    ("forge3d-pro", re.compile(r"\badd_build" r"ings\w*|forge3d\.build" r"ings\b|"
                               r"\bimport_osm_build" r"ings\w*"), True),
    ("forge3d-pro", re.compile(r"\b(?:save|load)_bun" r"dle\b"), True),
]
FORGE3D_RE = re.compile(r"forge3d")
CODE_EXT = {".py", ".js", ".html", ".yml", ".yaml", ".sh"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".cache", "outputs", "specs", "docs",
             ".specify", ".claude"}


def _blocked(lic: str, blocked: list[str], kind: str = "") -> bool:
    low = (lic or "").lower()
    if low.endswith(CLI_SUFFIX) and kind == "cli":
        return False
    return any(b.lower() in low for b in blocked)


def validate(reg: dict) -> list[str]:
    errs: list[str] = []
    policy = reg.get("license_policy", {})
    allowed = set(policy.get("allowed", []))
    blocked = list(policy.get("blocked", []))
    sessions = set(str(k) for k in reg.get("sessions", {}))
    lines = set(reg.get("product_lines", {}))
    seen: set[str] = set()
    for i, t in enumerate(reg.get("tools", [])):
        tid = t.get("id") or f"<row {i}>"
        if tid in seen:
            errs.append(f"{tid}: duplicate id")
        seen.add(tid)
        st = t.get("status")
        if st not in STATUSES:
            errs.append(f"{tid}: status {st!r} not in {sorted(STATUSES)}")
            continue
        if not t.get("reason"):
            errs.append(f"{tid}: missing reason")
        for pl in t.get("products", []) or []:
            if lines and pl not in lines:
                errs.append(f"{tid}: product line {pl!r} undeclared")
        if st == "selected":
            lic = t.get("license", "")
            if lic not in allowed:
                errs.append(f"{tid}: license {lic!r} not on allowlist")
            if _blocked(lic, blocked, t.get("kind", "")):
                errs.append(f"{tid}: license {lic!r} is blocked")
            if lic.lower().endswith(CLI_SUFFIX) and t.get("kind") != "cli":
                errs.append(f"{tid}: {lic!r} is only valid for kind 'cli'")
            if not t.get("version"):
                errs.append(f"{tid}: missing version constraint")
            if str(t.get("session")) not in sessions:
                errs.append(f"{tid}: session {t.get('session')!r} undefined")
            if not t.get("spec"):
                errs.append(f"{tid}: missing spec")
            if not t.get("source_url"):
                errs.append(f"{tid}: missing source_url")
            if not t.get("target"):
                errs.append(f"{tid}: missing target")
            if t.get("runs_in") not in RUNS_IN:
                errs.append(f"{tid}: runs_in {t.get('runs_in')!r} not in {sorted(RUNS_IN)}")
        elif st == "deferred":
            if _blocked(t.get("license", ""), blocked, t.get("kind", "")):
                errs.append(f"{tid}: deferred tool carries a blocked license")
            if not t.get("trigger"):
                errs.append(f"{tid}: deferred without a trigger condition")
        else:
            if t.get("category") not in CATEGORIES:
                errs.append(f"{tid}: category {t.get('category')!r} not in fixed set")
    return errs


def scan_boundaries(root: str) -> list[str]:
    """Boundary violations in repo code under root, as 'path:line: id: text'."""
    errs: list[str] = []
    me = os.path.abspath(__file__)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            if os.path.splitext(fn)[1] not in CODE_EXT or os.path.abspath(path) == me:
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            uses_forge3d = any(FORGE3D_RE.search(ln) for ln in lines)
            rel = os.path.relpath(path, root)
            for n, ln in enumerate(lines, 1):
                for tid, rx, needs in BOUNDARY_RULES:
                    if needs and not uses_forge3d:
                        continue
                    if rx.search(ln):
                        errs.append(f"{rel}:{n}: {tid}: {ln.strip()[:120]}")
    return errs


def summary(reg: dict) -> str:
    tools = reg.get("tools", [])
    lines = ["Integration registry " + str(reg.get("version", "?"))]
    for st in ("selected", "deferred", "eliminated"):
        lines.append(f"  {st}: {sum(1 for t in tools if t.get('status') == st)}")
    for k, v in sorted(reg.get("sessions", {}).items(), key=lambda kv: int(kv[0])):
        n = sum(1 for t in tools if t.get("status") == "selected" and str(t.get("session")) == k)
        lines.append(f"  session {k} ({v}): {n} tools")
    return "\n".join(lines)


def session_list(reg: dict, session: str) -> str:
    rows = [t for t in reg.get("tools", [])
            if t.get("status") == "selected" and str(t.get("session")) == session]
    out = [f"Session {session}: {reg.get('sessions', {}).get(session, '?')}"]
    for t in rows:
        out.append(f"  {t['name']} [{t.get('version')}, {t.get('license')}, {t.get('runs_in')}]"
                   f" -> {', '.join(t.get('target', []))}")
    return "\n".join(out)


def _selftest() -> int:
    base = {
        "license_policy": {"allowed": ["MIT"], "blocked": ["GPL-3.0", "AGPL-3.0"]},
        "sessions": {"1": "s1"},
        "tools": [],
    }
    good = {"id": "a", "name": "A", "status": "selected", "license": "MIT", "version": ">=1",
            "session": 1, "spec": "004", "source_url": "u", "target": ["x.py"],
            "runs_in": "ci", "reason": "r"}
    checks = []

    def case(name, tools, expect_ok):
        reg = dict(base, tools=tools)
        ok = not validate(reg)
        checks.append((name, ok == expect_ok))

    case("valid selected passes", [good], True)
    case("disallowed license fails", [dict(good, license="GPL-3.0")], False)
    case("missing version fails", [dict(good, version="")], False)
    case("undefined session fails", [dict(good, session=9)], False)
    case("bad runs_in fails", [dict(good, runs_in="server")], False)
    case("duplicate id fails", [good, dict(good)], False)
    case("deferred needs trigger", [{"id": "d", "status": "deferred", "license": "MIT", "reason": "r"}], False)
    case("deferred blocked license fails",
         [{"id": "d", "status": "deferred", "license": "AGPL-3.0", "trigger": "t", "reason": "r"}], False)
    case("eliminated needs known category",
         [{"id": "e", "status": "eliminated", "category": "vibes", "reason": "r"}], False)
    case("eliminated valid passes",
         [{"id": "e", "status": "eliminated", "category": "license-risk", "reason": "r"}], True)
    case("unknown status fails", [dict(good, status="maybe")], False)
    base["license_policy"]["allowed"].append("GPL-3.0-cli")
    case("copyleft CLI accepted for kind cli",
         [dict(good, license="GPL-3.0-cli", kind="cli")], True)
    case("copyleft CLI rejected for a library",
         [dict(good, license="GPL-3.0-cli", kind="python")], False)
    base["product_lines"] = {"platform": "p"}
    case("undeclared product line fails", [dict(good, products=["vibes"])], False)
    case("declared product line passes", [dict(good, products=["platform"])], True)
    reg = dict(base, tools=[good])
    checks.append(("session list names tool", "A [" in session_list(reg, "1")))

    import tempfile

    def planted(name: str, text: str) -> list[str]:
        root = tempfile.mkdtemp(prefix="integration_audit_")
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        return scan_boundaries(root)

    key = "set_license" + "_key"
    checks.append(("planted forge3d license key fails",
                   bool(planted("plate.py", f'import forge3d\nforge3d.{key}("x")\n'))))
    checks.append(("planted Map" "Plate fails",
                   bool(planted("plate.py", "from forge3d import Map" "Plate\n"))))
    checks.append(("planted forge3d vector export fails",
                   bool(planted("plate.py", "import forge3d\nforge3d.export_" "svg(s, 'a.svg')\n"))))
    checks.append(("planted building import fails",
                   bool(planted("plate.py", "import forge3d\nforge3d.add_build" "ings(s, g)\n"))))
    checks.append(("planted sharing host fails",
                   bool(planted("export.js", "fetch('https://share.geolibre" ".app/p/1')\n"))))
    checks.append(("WeasyPrint export_pdf without forge3d passes",
                   not planted("render_county_pdf.py", "def export_" "pdf(html):\n    pass\n")))
    checks.append(("clean forge3d code passes",
                   not planted("plate.py", "import forge3d\nforge3d.device_probe()\n")))
    checks.append(("summary counts selected", "selected: 1" in summary(reg)))
    fails = 0
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    print(f"{len(checks) - fails}/{len(checks)} checks passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--registry", default=REGISTRY)
    ap.add_argument("--root", default=os.path.dirname(os.path.abspath(__file__)),
                    help="tree scanned by the boundary checks")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    with open(a.registry, encoding="utf-8") as f:
        reg = json.load(f)
    errs = validate(reg)
    if a.registry == REGISTRY:
        errs += [f"boundary {e}" for e in scan_boundaries(a.root)]
    if a.session is not None:
        print(session_list(reg, a.session))
    else:
        print(summary(reg))
    for e in errs:
        print("VIOLATION " + e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
