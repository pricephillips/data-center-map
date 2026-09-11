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
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        # `path or DEFAULT` is the standard override idiom, and a file
        # written behind one was invisible to this audit: the one-writer rule
        # cannot govern a path it never sees. Resolve to the first operand
        # that is knowable, which is the default.
        for operand in node.values:
            resolved = _resolve(operand, env)
            if resolved is not None:
                return resolved
        return None
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
            # A component that cannot be folded is usually a function
            # parameter -- os.path.join(outdir, "rows.csv") with outdir passed
            # in. Bailing to None here made the whole write invisible, which
            # cost more than it saved: a file with no resolvable writer falls
            # out of the one-writer rule entirely, so nothing would notice a
            # second module starting to write it. Substitute "*" for the part
            # that will not fold, the same way the f-string branch above
            # already does, and let bind_computed_writes() settle it against
            # the files actually committed. At least one part must resolve, or
            # the result is a bare "*" that matches the whole tree.
            if parts and any(p for p in parts):
                return "/".join((p if p is not None else "*") for p in parts if p != "")
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


def has_unknown_dir(path: str) -> bool:
    """True when the DIRECTORY of a write target could not be folded.

    Two different things produce a "*" in a resolved target and they are not
    the same problem:

      data/subframe_audit_*.csv   an f-string over a known directory. The
                                  basename varies per run; the pattern is
                                  exact about where the file lands and has
                                  always matched declared layer patterns
                                  correctly. Leave it alone.

      */contagion_rows.csv        os.path.join(outdir, ...) with outdir a
                                  parameter. The directory is unknown, so
                                  this matches no declared pattern and the
                                  file it really writes is attributed to
                                  nobody.

    Only the second kind needs settling against the committed tree.
    """
    return path.startswith("*/") or "/" not in path.split("*")[0]


def bind_computed_writes(wmap: dict, inventory_paths) -> tuple[dict, dict]:
    """Attribute writes whose directory could not be folded.

    Additive: every existing entry is preserved untouched, because the
    f-string patterns already resolve against declared layer patterns and
    breaking that would trade one blind spot for another.

    A target with an unknown directory that matches exactly one committed
    file is bound to it, and every rule then applies to that file normally --
    which is the point, since an unattributed file is one the one-writer rule
    cannot protect. A target matching several files is NOT guessed at:
    attributing a write to the wrong file would invent a multi-writer finding
    out of nothing, which is worse than the gap it closes. Those come back
    separately and are reported as unresolved.
    """
    # Unknown-directory targets are not paths, they are unfinished ones, and
    # auditing them as if they were real invents undeclared findings for files
    # that do not exist (a selftest writing to a temp dir, say). They are
    # dropped from the map and used only to attribute a real file below. The
    # f-string patterns stay exactly as they were.
    bound = {p: list(m) for p, m in wmap.items()
             if not ("*" in p and has_unknown_dir(p))}
    ambiguous: dict = {}
    for pattern, modules in wmap.items():
        if "*" not in pattern or not has_unknown_dir(pattern):
            continue
        hits = [f for f in inventory_paths if fnmatch.fnmatch(f, pattern)]
        if len(hits) == 1:
            bound.setdefault(hits[0], [])
            bound[hits[0]] = sorted(set(bound[hits[0]]) | set(modules))
        elif len(hits) > 1:
            for f in hits:
                ambiguous.setdefault(f, [])
                ambiguous[f] = sorted(set(ambiguous[f]) | set(modules))
    return bound, ambiguous


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
          inventory: list[str] | None = None,
          ambiguous: dict | None = None) -> list[dict]:
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
        # "no writing module found" used to cover two different states: a file
        # nothing writes, and a file whose writer builds its path at runtime.
        # They need different answers from a reader, so they get different
        # sentences.
        maybe = (ambiguous or {}).get(path)
        detail = (f"writer not resolvable (path computed at runtime); "
                  f"candidates: {', '.join(maybe)}" if maybe
                  else "no writing module found; hand maintained or retired")
        findings.append({
            "finding": "undeclared_file", "subject": path, "layer": "",
            "detail": detail,
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

    check("an override idiom resolves to its default",
          "data/hist.csv" in writes('DEFAULT = "data/hist.csv"\n'
                                    'def w(rows, path=None):\n'
                                    '    target = path or DEFAULT\n'
                                    '    open(target, "a").write("x")\n'))
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

    # --- computed write targets -------------------------------------------
    # A module that builds its output path from a parameter was invisible to
    # the whole audit, which meant the one-writer rule did not cover it.
    computed_src = """
import os
def main(path, outdir="data"):
    with open(os.path.join(outdir, "rows.csv"), "w") as fh:
        fh.write("x")
"""
    cw = writes(computed_src)
    check("a path built from a parameter resolves to a pattern",
          "*/rows.csv" in cw)
    check("an unknown directory is recognised as unknown",
          has_unknown_dir("*/rows.csv"))
    check("a known directory with a varying basename is not",
          not has_unknown_dir("data/subframe_audit_*.csv"))
    check("a bare name with no directory counts as unknown",
          has_unknown_dir("*.csv"))

    wm = {"*/rows.csv": ["a.py"], "data/known_*.csv": ["b.py"],
          "data/plain.csv": ["c.py"]}
    bound, amb = bind_computed_writes(
        wm, ["data/rows.csv", "data/known_1.csv", "data/known_2.csv",
             "data/plain.csv"])
    check("a unique match binds the real file to its writer",
          bound.get("data/rows.csv") == ["a.py"])
    check("the unknown-directory pseudo-path is not left in the map",
          "*/rows.csv" not in bound)
    check("an f-string pattern over a known directory is left untouched",
          bound.get("data/known_*.csv") == ["b.py"])
    check("an ordinary resolved path is left untouched",
          bound.get("data/plain.csv") == ["c.py"])
    check("nothing is reported ambiguous when every match is unique",
          amb == {})

    # Two files match one unknown-directory pattern: guessing which one the
    # module writes would invent a multi-writer finding out of nothing.
    bound2, amb2 = bind_computed_writes(
        {"*/report.md": ["d.py"]}, ["data/report.md", "qc/report.md"])
    check("an ambiguous match binds nothing",
          "data/report.md" not in bound2 and "qc/report.md" not in bound2)
    check("an ambiguous match is reported with its candidates",
          amb2.get("data/report.md") == ["d.py"])
    check("a pattern matching no committed file is simply dropped",
          bind_computed_writes({"*/gone.csv": ["e.py"]}, ["data/here.csv"])
          == ({}, {}))

    # rule 4 has to tell the two states apart, because a reader cannot.
    cfg = {"layers": {}, "not_a_layer": [], "multi_writer_exempt": {},
           "cross_layer_exempt": {}}
    f_amb = audit(cfg, {}, ["data/x.md"], {"data/x.md": ["m.py"]})
    check("a computed writer is named rather than called hand-maintained",
          any("not resolvable" in f["detail"] for f in f_amb))
    f_none = audit(cfg, {}, ["data/y.md"], {})
    check("a file nothing writes still reads as hand-maintained",
          any("hand maintained" in f["detail"] for f in f_none))

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
    inv = inventory()
    # Settle computed write targets against the files actually committed
    # before auditing, so a module that builds its paths at runtime is still
    # covered by the one-writer rule rather than silently exempt from it.
    wmap, ambiguous = bind_computed_writes(wmap, inv)
    findings = audit(config, wmap, inv, ambiguous)
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
