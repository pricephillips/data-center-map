#!/usr/bin/env python3
"""
layer_audit.py

Enforces the dataset barriers declared in ARCHITECTURE.md.

The layering in this repository was real but implicit: facilities, proposed
projects, opposition events, policy instruments and derived analytics have
always been distinct kinds of thing with distinct keys, and nothing said so or
checked it. Implicit layering is cheap while a repository is small and
expensive the first time a module quietly writes into someone else's file,
which is how the master_opposition corruption happened and what the sync
ownership rule was written to stop. This generalizes that one rule to the
whole tree.

Two rules, both mechanical:

  One writer per file.   A file written by two processes has no owner, and the
                         last run decides what it contains.
  No writer crosses a layer boundary undeclared. A module that writes into two
                         layers is a place where two kinds of record can be
                         merged by accident. Where a crossing is real and
                         intended (a gated promotion, a harvester that routes
                         what it sees), it is declared with a reason and
                         reported as declared rather than silently allowed.

Write targets are resolved from the source with an AST walk rather than by
grepping for filenames, because a module that only reads a path mentions it
exactly the same way a module that writes it does.

Reads
  configs/layers.json    layer definitions, file patterns, declared exceptions
  *.py, qc/*.py, scripts/*.py

Writes
  data/layer_audit.csv
  data/layer_audit_summary.json

Usage
  python layer_audit.py
  python layer_audit.py --selftest
  python layer_audit.py --strict     exit nonzero on any undeclared finding
"""

from __future__ import annotations

import argparse
import ast
import csv
import fnmatch
import glob
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "configs", "layers.json")
OUT_CSV = os.path.join(HERE, "data", "layer_audit.csv")
OUT_JSON = os.path.join(HERE, "data", "layer_audit_summary.json")

MODULE_GLOBS = ("*.py", "qc/*.py", "scripts/*.py")
WRITE_CALLS = {"to_csv", "to_json", "write_text", "write_bytes"}
PATH_HELPERS = {"P"}                       # repo-wide join-from-root idiom


# --------------------------------------------------------------------------
# resolving what a module writes
# --------------------------------------------------------------------------

def _resolve(node: ast.AST, env: dict) -> str | None:
    """Best-effort constant folding of a path expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _resolve(node.left, env), _resolve(node.right, env)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append(_resolve(value, env) or "*")
        return "".join(parts)
    if isinstance(node, ast.Call):
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name in ("dirname", "abspath", "realpath"):
            return ""                      # repository root
        if name == "join" or name in PATH_HELPERS:
            parts = [_resolve(a, env) for a in node.args]
            if parts and all(p is not None for p in parts):
                return "/".join(p for p in parts if p)
    return None


def _const_env(tree: ast.AST) -> dict:
    env: dict = {}
    for _ in range(2):                     # one extra pass resolves chains
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                value = _resolve(node.value, env)
                if value is not None:
                    env[node.targets[0].id] = value
    return env


def _writer_helpers(tree: ast.AST) -> dict:
    """Local functions that open one of their own parameters for writing."""
    out: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in node.args.args]
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or not sub.args:
                continue
            if getattr(sub.func, "attr", getattr(sub.func, "id", "")) != "open":
                continue
            mode = None
            if len(sub.args) >= 2 and isinstance(sub.args[1], ast.Constant):
                mode = sub.args[1].value
            for kw in sub.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if not mode or ("w" not in mode and "a" not in mode):
                continue
            target = sub.args[0]
            if isinstance(target, ast.Name) and target.id in params:
                out[node.name] = params.index(target.id)
    return out


def writes(source: str) -> set[str]:
    """Paths a module writes, repo-relative where resolvable."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    env = _const_env(tree)
    helpers = _writer_helpers(tree)
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name == "open" and node.args:
            mode = None
            if len(node.args) >= 2:
                mode = _resolve(node.args[1], env)
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = _resolve(kw.value, env)
            if mode and ("w" in mode or "a" in mode):
                target = _resolve(node.args[0], env)
                if target:
                    out.add(target)
        elif name in WRITE_CALLS and node.args:
            target = _resolve(node.args[0], env)
            if target:
                out.add(target)
        elif name in helpers:
            i = helpers[name]
            if len(node.args) > i:
                target = _resolve(node.args[i], env)
                if target:
                    out.add(target)
    return {t.lstrip("./") for t in out if t and not t.startswith(("/", "http"))}


def write_map(root: str = HERE) -> dict[str, list[str]]:
    """file -> modules that write it, both repo-relative."""
    out: dict[str, list[str]] = defaultdict(list)
    for pattern in MODULE_GLOBS:
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            module = os.path.relpath(path, root)
            with open(path, encoding="utf-8", errors="ignore") as fh:
                source = fh.read()
            for target in writes(source):
                out[target].append(module)
    return {k: sorted(set(v)) for k, v in sorted(out.items())}


# --------------------------------------------------------------------------
# layering
# --------------------------------------------------------------------------

def layer_of(path: str, config: dict) -> str | None:
    for code, layer in config["layers"].items():
        for pattern in layer.get("files", []):
            if fnmatch.fnmatch(path, pattern):
                return code
    return None


def audit(config: dict, wmap: dict[str, list[str]],
          inventory: list[str] | None = None) -> list[dict]:
    findings: list[dict] = []
    exempt = config.get("exempt_multi_writer", {})
    crossings = config.get("declared_crossings", {})
    ignore = config.get("not_a_layer", [])

    def ignored(path: str) -> bool:
        return any(fnmatch.fnmatch(path, p) for p in ignore)

    # rule 1: one writer per file
    for path, modules in wmap.items():
        if ignored(path):
            continue
        if len(modules) > 1:
            reason = exempt.get(path)
            findings.append({
                "finding": "multi_writer" if not reason else "multi_writer_declared",
                "subject": path,
                "layer": layer_of(path, config) or "",
                "detail": ", ".join(modules),
                "reason": reason or "",
            })

    # rule 2: a writer stays inside one layer
    module_layers: dict[str, set] = defaultdict(set)
    for path, modules in wmap.items():
        if ignored(path):
            continue
        code = layer_of(path, config)
        for module in modules:
            module_layers[module].add(code)
    for module, codes in sorted(module_layers.items()):
        real = {c for c in codes if c}
        if len(real) > 1:
            reason = crossings.get(module)
            findings.append({
                "finding": "cross_layer_write" if not reason
                           else "cross_layer_declared",
                "subject": module,
                "layer": " + ".join(sorted(real)),
                "detail": ", ".join(sorted(p for p, m in wmap.items()
                                           if module in m and not ignored(p))),
                "reason": reason or "",
            })

    # rule 3: every written file belongs to a layer
    for path, modules in wmap.items():
        if ignored(path) or layer_of(path, config):
            continue
        findings.append({
            "finding": "undeclared_file", "subject": path, "layer": "",
            "detail": ", ".join(modules),
            "reason": "",
        })

    # rule 4: every committed data file belongs to a layer, written or not
    for path in inventory or []:
        if ignored(path) or layer_of(path, config):
            continue
        findings.append({
            "finding": "undeclared_file", "subject": path, "layer": "",
            "detail": "no writing module found; hand maintained or retired",
            "reason": "",
        })

    order = {"multi_writer": 0, "cross_layer_write": 1, "undeclared_file": 2,
             "multi_writer_declared": 3, "cross_layer_declared": 4}
    findings.sort(key=lambda f: (order.get(f["finding"], 9), f["subject"]))
    # de-duplicate: a written file can reach rule 3 and rule 4 both
    seen = set()
    unique = []
    for f in findings:
        key = (f["finding"], f["subject"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique


def inventory(root: str = HERE) -> list[str]:
    out = []
    data = os.path.join(root, "data")
    if os.path.isdir(data):
        out += ["data/" + n for n in sorted(os.listdir(data))
                if n.endswith((".csv", ".json", ".md", ".flag"))]
    out += [n for n in sorted(os.listdir(root))
            if os.path.isfile(os.path.join(root, n))
            and n.endswith((".csv", ".json"))]
    return out


def summarize(findings: list[dict], config: dict,
              wmap: dict[str, list[str]]) -> dict:
    counts = Counter(f["finding"] for f in findings)
    undeclared = (counts["multi_writer"] + counts["cross_layer_write"]
                  + counts["undeclared_file"])
    return {
        "files_written": len(wmap),
        "layers": {code: layer.get("name", code)
                   for code, layer in config["layers"].items()},
        "findings": dict(sorted(counts.items())),
        "undeclared_findings": undeclared,
        "clean": undeclared == 0,
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    src = '''
import os
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "written.csv")
IN = os.path.join(DATA, "only_read.csv")
def helper(rows, path):
    with open(path, "w") as fh:
        fh.write("x")
def go():
    with open(OUT, "w") as fh:
        fh.write("y")
    with open(IN) as fh:
        fh.read()
    helper([], os.path.join(DATA, "via_helper.csv"))
    with open(os.path.join(DATA, "appended.csv"), "a") as fh:
        fh.write("z")
'''
    w = writes(src)
    check("resolves a joined constant path", "data/written.csv" in w)
    check("a read-only path is not a write", "data/only_read.csv" not in w)
    check("follows a write helper", "data/via_helper.csv" in w)
    check("append mode counts as a write", "data/appended.csv" in w)

    check("P() helper resolves",
          "data/p.csv" in writes('P = lambda *a: "/".join(a)\n'
                                 'OUT = P("data", "p.csv")\n'
                                 'open(OUT, "w").write("x")\n'))

    config = {
        "layers": {
            "A": {"name": "Facilities", "files": ["data/fac_*.csv"]},
            "C": {"name": "Opposition", "files": ["master.csv", "data/opp_*.csv"]},
        },
        "exempt_multi_writer": {"master.csv": "gated append, ownership rule"},
        "declared_crossings": {"router.py": "routes, never merges"},
        "not_a_layer": ["configs/*", "signals/*"],
    }
    wmap = {
        "data/fac_a.csv": ["router.py"],
        "data/opp_a.csv": ["router.py"],
        "master.csv": ["one.py", "two.py"],
        "data/opp_b.csv": ["one.py", "three.py"],
        "data/mystery.csv": ["four.py"],
        "configs/thing.json": ["five.py"],
    }
    f = audit(config, wmap)
    by = {(x["finding"], x["subject"]) for x in f}
    check("undeclared multi-writer reported",
          ("multi_writer", "data/opp_b.csv") in by)
    check("declared multi-writer is separated",
          ("multi_writer_declared", "master.csv") in by
          and ("multi_writer", "master.csv") not in by)
    check("declared crossing is separated",
          ("cross_layer_declared", "router.py") in by
          and ("cross_layer_write", "router.py") not in by)
    check("file in no layer reported",
          ("undeclared_file", "data/mystery.csv") in by)
    check("not_a_layer paths are skipped",
          not any(x["subject"] == "configs/thing.json" for x in f))

    s = summarize(f, config, wmap)
    check("summary counts only undeclared findings",
          s["undeclared_findings"] == 2 and s["clean"] is False)

    config2 = dict(config, declared_crossings={})
    by2 = {(x["finding"], x["subject"]) for x in audit(config2, wmap)}
    check("removing the declaration turns it into a finding",
          ("cross_layer_write", "router.py") in by2)

    clean = audit(config, {"data/fac_a.csv": ["a.py"], "master.csv": ["b.py"]})
    check("a clean tree yields no findings", clean == [])

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
                    help="exit nonzero on any undeclared finding")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    with open(CONFIG, encoding="utf-8") as fh:
        config = json.load(fh)
    wmap = write_map()
    findings = audit(config, wmap, inventory())
    summary = summarize(findings, config, wmap)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["finding", "subject", "layer",
                                           "detail", "reason"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(findings)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")

    print(f"layer audit: {summary['files_written']} written files, "
          f"{len(findings)} findings "
          f"({summary['undeclared_findings']} undeclared)")
    for name, count in summary["findings"].items():
        print(f"  {name}: {count}")
    for f in findings:
        if f["finding"] in ("multi_writer", "cross_layer_write",
                            "undeclared_file"):
            print(f"  UNDECLARED  {f['finding']}: {f['subject']} "
                  f"[{f['detail']}]")
    if args.strict and not summary["clean"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
