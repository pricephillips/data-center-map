#!/usr/bin/env python3
"""agenda_extract.py -- grounded candidate rows from agenda or minutes text,
run with LangExtract on a local Ollama model (on a workstation, or in
agenda-extract.yml with an Ollama server inside the runner).

Spec 007, US5. Given agenda or minutes text (from agenda_text.py's cache, or
any .txt), a local model proposes candidate rows: jurisdiction, body, date,
item, action, and the vote if one is stated. A model can return a plausible
field that the document never says, so every field must carry character
offsets into the source text, and a field is kept only when those offsets
slice the source to exactly the extracted string. Anything else is dropped
and counted in the run summary. Each retained field can therefore be checked
against the exact passage it came from.

Rules this script enforces:

  * It refuses to run when the CI environment variable is set (exit 2),
    unless --allow-ci is passed. Only agenda-extract.yml passes it (owner
    decision 2026-10-01): that workflow runs Ollama inside the runner, so
    the model is still local and no agenda text leaves the job. --selftest
    uses a stubbed model and a fixture agenda, makes no network call, and is
    how Principle IX's blocking selftest step covers the grounding rule.
  * --from-index N reads the N cached agendas with the most keyword hits
    (agenda_text.py's index and .cache/agenda_text), and only the passages
    around those hits, so a CPU runner finishes. Window offsets are shifted
    back, so grounding is still checked against the full document.
  * It writes only a draft worklist: data/<name>_draft.csv. Any other output
    path is refused (exit 2), and so is anything naming master_opposition.
    Price reviews the draft and commits what survives; nothing here promotes
    a row.
  * Rows are assembled in offset order: jurisdiction, body and date are
    meeting-level and apply to every row; each item starts a row; action and
    vote attach to the most recent item. A row without a retained item is
    dropped.

Usage (on a machine running Ollama):
  python scripts/agenda_extract.py --from-index 20
  python scripts/agenda_extract.py --in agenda.txt [--in minutes.txt ...]
      [--model gemma2:2b] [--model-url http://localhost:11434]
      [--out data/agenda_extract_draft.csv]
  python scripts/agenda_extract.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import subprocess
import tempfile
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT_DRAFT = os.path.join(ROOT, "data", "agenda_extract_draft.csv")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "coverage_expansion", "agenda.txt")

DEFAULT_MODEL = "gemma2:2b"
DEFAULT_MODEL_URL = "http://localhost:11434"

MEETING_FIELDS = ("jurisdiction", "body", "date")
ITEM_FIELDS = ("item", "action", "vote")
FIELDS = MEETING_FIELDS + ITEM_FIELDS
DRAFT_COLS = (["source_path", "source_sha256", "row_n"]
              + [c for f in FIELDS for c in (f, f"{f}_start", f"{f}_end")]
              + ["model_id", "extracted_at", "review_status", "document_url"])

PROMPT = (
    "Extract, in order of appearance, the local government meeting facts in "
    "this agenda or minutes text: the jurisdiction, the body that met, the "
    "meeting date, each agenda item, the action taken on it, and the vote "
    "tally if one is stated. Use the exact text from the document for every "
    "extraction; do not paraphrase, normalize or infer. Leave out anything the "
    "text does not say.")

EXIT_REFUSED = 2


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def refuse_in_ci() -> bool:
    return bool(os.environ.get("CI"))


def draft_path_ok(path: str) -> bool:
    """Only data/<name>_draft.csv, and never anything naming master_opposition."""
    ap = os.path.abspath(path)
    name = os.path.basename(ap)
    return (os.path.dirname(ap) == os.path.abspath(DATA)
            and name.endswith("_draft.csv")
            and "master_opposition" not in name)


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------

def _interval(ext) -> tuple[int | None, int | None]:
    ci = getattr(ext, "char_interval", None)
    if ci is None:
        return None, None
    return getattr(ci, "start_pos", None), getattr(ci, "end_pos", None)


def ground(text: str, extractions) -> tuple[list[dict], dict[str, int]]:
    """Keep a field only when its offsets exist, are in bounds, and slice the
    source to exactly the extracted string. Returns (kept, drop counts)."""
    kept, drops = [], {"no_offset": 0, "out_of_bounds": 0, "offset_mismatch": 0,
                       "unknown_field": 0}
    for ext in extractions or []:
        field = (getattr(ext, "extraction_class", "") or "").strip().lower()
        value = getattr(ext, "extraction_text", "") or ""
        if field not in FIELDS:
            drops["unknown_field"] += 1
            continue
        start, end = _interval(ext)
        if start is None or end is None:
            drops["no_offset"] += 1
            continue
        if not (0 <= start < end <= len(text)):
            drops["out_of_bounds"] += 1
            continue
        if text[start:end] != value:
            drops["offset_mismatch"] += 1
            continue
        kept.append({"field": field, "value": value, "start": start, "end": end})
    return kept, drops


def assemble_rows(fields: list[dict]) -> tuple[list[dict], int]:
    """Candidate rows from grounded fields, in offset order. Returns
    (rows, number of item-less fields dropped)."""
    meeting: dict[str, dict] = {}
    rows: list[dict] = []
    orphans = 0
    for f in sorted(fields, key=lambda x: (x["start"], x["end"])):
        if f["field"] in MEETING_FIELDS:
            meeting.setdefault(f["field"], f)
        elif f["field"] == "item":
            rows.append({"item": f})
        elif rows and f["field"] not in rows[-1]:
            rows[-1][f["field"]] = f
        else:
            orphans += 1
    out = []
    for r in rows:
        full = {**meeting, **r}
        flat = {}
        for name in FIELDS:
            g = full.get(name)
            flat[name] = g["value"] if g else ""
            flat[f"{name}_start"] = g["start"] if g else ""
            flat[f"{name}_end"] = g["end"] if g else ""
        out.append(flat)
    return out, orphans


# ---------------------------------------------------------------------------
# Model call (LangExtract + Ollama), injectable for the selftest
# ---------------------------------------------------------------------------

def langextract_fn(model_id: str, model_url: str):
    """The real extractor. Imported lazily: LangExtract is a local-only
    dependency and is never installed in CI."""
    import langextract as lx  # noqa: PLC0415

    example_text = ("Town of Example Planning Board, March 3, 2026. Item 2. "
                    "Special use permit for a warehouse. Approved 4-1.")
    examples = [lx.data.ExampleData(text=example_text, extractions=[
        lx.data.Extraction(extraction_class="jurisdiction", extraction_text="Town of Example"),
        lx.data.Extraction(extraction_class="body", extraction_text="Planning Board"),
        lx.data.Extraction(extraction_class="date", extraction_text="March 3, 2026"),
        lx.data.Extraction(extraction_class="item",
                           extraction_text="Special use permit for a warehouse"),
        lx.data.Extraction(extraction_class="action", extraction_text="Approved"),
        lx.data.Extraction(extraction_class="vote", extraction_text="4-1"),
    ])]

    def run(text: str):
        doc = lx.extract(text_or_documents=text, prompt_description=PROMPT,
                         examples=examples, model_id=model_id, model_url=model_url,
                         fence_output=False, use_schema_constraints=False,
                         fetch_urls=False, show_progress=False)
        return doc.extractions or []
    return run


class _Shifted:
    """An extraction made on a window, with its offsets moved into the full
    document, so grounding checks it against the whole source text."""

    def __init__(self, ext, offset: int):
        self.extraction_class = getattr(ext, "extraction_class", "")
        self.extraction_text = getattr(ext, "extraction_text", "")
        start, end = _interval(ext)
        self.char_interval = (None if start is None or end is None
                              else _CI(start + offset, end + offset))


def keyword_windows(text: str, radius: int = 700, max_windows: int = 2) -> list[tuple[int, int]]:
    """Spans of the document around agenda_text.py's keyword hits, merged
    where they overlap, densest first. A CPU-only model cannot read a 60-page
    packet; the passages that name a data center are the ones worth reading."""
    sys.path.insert(0, ROOT)
    from agenda_text import TERMS  # noqa: PLC0415
    spans = sorted((max(0, m.start() - radius), min(len(text), m.end() + radius))
                   for rx in TERMS.values() for m in rx.finditer(text))
    merged: list[list[int]] = []
    for s0, e0 in spans:
        if merged and s0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e0)
        else:
            merged.append([s0, e0])
    hits = [(sum(1 for rx in TERMS.values() for _ in rx.finditer(text[a:b])), a, b)
            for a, b in merged]
    hits.sort(key=lambda h: (-h[0], h[1]))
    return sorted((a, b) for _, a, b in hits[:max_windows])


def extract_file(path: str, extract_fn, model_id: str, windowed: bool = False,
                 document_url: str = "") -> tuple[list[dict], dict[str, int]]:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if windowed:
        extractions = [_Shifted(e, a) for a, b in keyword_windows(text)
                       for e in (extract_fn(text[a:b]) or [])]
    else:
        extractions = extract_fn(text)
    kept, drops = ground(text, extractions)
    rows, orphans = assemble_rows(kept)
    drops["no_item"] = orphans
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, r in enumerate(rows, 1):
        r.update({"source_path": os.path.relpath(os.path.abspath(path), ROOT),
                  "source_sha256": sha, "row_n": i, "model_id": model_id,
                  "extracted_at": now, "review_status": "draft",
                  "document_url": document_url})
    drops["retained_fields"] = len(kept)
    return rows, drops


def inputs_from_index(n: int, index: str = os.path.join(ROOT, "data", "agenda_text_index.csv"),
                      cache_dir: str = os.path.join(ROOT, ".cache", "agenda_text")
                      ) -> list[tuple[str, str]]:
    """(cached text path, document url) for the n agendas with the most
    keyword hits whose text agenda_text.py has cached."""
    if not os.path.exists(index):
        return []
    with open(index, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out, seen = [], set()
    for r in sorted(rows, key=lambda r: -int(r.get("keyword_hits") or 0)):
        sha = r.get("sha256") or ""
        path = os.path.join(cache_dir, f"{sha}.txt")
        if (int(r.get("keyword_hits") or 0) > 0 and sha not in seen
                and r.get("text_source") in ("text_layer", "ocr") and os.path.exists(path)):
            seen.add(sha)
            out.append((path, r.get("document_url", "")))
        if len(out) >= n:
            break
    return out


def write_draft(path: str, rows: list[dict]) -> None:
    if not draft_path_ok(path):
        raise ValueError(f"refusing to write {path}: drafts go to data/*_draft.csv only")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DRAFT_COLS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in DRAFT_COLS})


# ---------------------------------------------------------------------------
# Selftest: stubbed model, fixture agenda
# ---------------------------------------------------------------------------

class _CI:
    def __init__(self, s, e):
        self.start_pos, self.end_pos = s, e


class _Ext:
    def __init__(self, cls, text, interval):
        self.extraction_class, self.extraction_text = cls, text
        self.char_interval = _CI(*interval) if interval else None


def stub_model(text: str):
    """What a model might return: grounded fields located in the text, plus
    one ungrounded field, one out-of-bounds and one misaligned span."""
    def at(cls, s):
        i = text.index(s)
        return _Ext(cls, s, (i, i + len(s)))
    return [
        at("jurisdiction", "Fayette County"),
        at("body", "Board of Commissioners"),
        at("date", "September 10, 2026"),
        at("item", "Ordinance amending the zoning code to establish a data center overlay district"),
        at("action", "The motion carried"),
        at("vote", "5-2"),
        at("item", "Consent agenda"),
        at("action", "approved without discussion"),
        _Ext("vote", "7-0", None),                           # no offsets
        _Ext("action", "tabled", (len(text) + 5, len(text) + 11)),  # out of bounds
        _Ext("body", "Planning Commission", (0, 19)),         # misaligned
    ]


def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        print(f"  {'pass' if cond else 'FAIL'} {label}")
        ok = ok and bool(cond)

    with open(FIXTURE, encoding="utf-8") as fh:
        text = fh.read()
    rows, drops = extract_file(FIXTURE, stub_model, "stub")
    check(len(rows) == 2, "two agenda items become two candidate rows")
    vote = rows[0]
    check(vote["vote"] == "5-2" and text[vote["vote_start"]:vote["vote_end"]] == "5-2",
          "the vote tally's offsets slice the source text exactly")
    all_fields = [(r[f], r[f"{f}_start"], r[f"{f}_end"]) for r in rows for f in FIELDS if r[f]]
    check(all(text[s:e] == v for v, s, e in all_fields),
          "100 percent of retained fields have valid offsets")
    check(drops["no_offset"] == 1 and drops["out_of_bounds"] == 1
          and drops["offset_mismatch"] == 1,
          "ungrounded, out-of-bounds and misaligned fields are dropped and counted")
    check(rows[0]["jurisdiction"] == "Fayette County" and rows[1]["body"] == "Board of Commissioners",
          "meeting-level fields apply to every row")
    check(rows[1]["vote"] == "" and rows[1]["action"] == "approved without discussion",
          "a vote is left blank when none is stated")
    check(all(r["review_status"] == "draft" for r in rows), "every row is a draft")

    orphan_rows, orphans = assemble_rows([{"field": "vote", "value": "3-0", "start": 0, "end": 3}])
    check(orphan_rows == [] and orphans == 1, "a vote with no item is dropped, not attached")

    check(draft_path_ok(OUT_DRAFT), "the default output is a draft under data/")
    check(not draft_path_ok(os.path.join(ROOT, "master_opposition.csv"))
          and not draft_path_ok(os.path.join(DATA, "master_opposition_draft.csv"))
          and not draft_path_ok(os.path.join(DATA, "agenda_extract.csv"))
          and not draft_path_ok(os.path.join(ROOT, "agenda_extract_draft.csv")),
          "any path that is not data/*_draft.csv, or names master_opposition, is refused")

    env = {**os.environ, "CI": "true"}
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--in", FIXTURE],
                       env=env, capture_output=True, text=True, timeout=60)
    check(r.returncode == EXIT_REFUSED and "refusing" in r.stdout.lower(),
          "an extraction run refuses when CI is set")
    env.pop("CI")
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--in", FIXTURE,
                        "--out", os.path.join(ROOT, "master_opposition.csv")],
                       env=env, capture_output=True, text=True, timeout=60)
    check(r.returncode == EXIT_REFUSED, "a non-draft output path is refused before any work")

    # Keyword windows (CI runs read passages, not whole packets).
    long_text = ("Preamble. " * 300) + text + (" Closing remarks." * 300)
    wins = keyword_windows(long_text, radius=200)
    check(wins and all(b - a < len(long_text) for a, b in wins)
          and any(a <= long_text.index("data center") < b for a, b in wins),
          "keyword windows cover the data center passage, not the whole packet")
    with tempfile.TemporaryDirectory() as td:
        lp = os.path.join(td, "long.txt")
        with open(lp, "w", encoding="utf-8") as fh:
            fh.write(long_text)
        wrows, wdrops = extract_file(lp, stub_model, "stub", windowed=True,
                                     document_url="https://example.gov/a.pdf")
        check(wrows and all(long_text[r[f"{f}_start"]:r[f"{f}_end"]] == r[f]
                            for r in wrows for f in FIELDS if r[f]),
              "window offsets are shifted back and slice the full document exactly")
        check(all(r["document_url"] == "https://example.gov/a.pdf" for r in wrows),
              "draft rows carry the agenda's document URL")

        idx = os.path.join(td, "index.csv")
        cache = os.path.join(td, "cache")
        os.makedirs(cache)
        for sha in ("a" * 64, "b" * 64):
            with open(os.path.join(cache, f"{sha}.txt"), "w", encoding="utf-8") as fh:
                fh.write("x")
        with open(idx, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["document_url", "sha256", "text_source", "keyword_hits"])
            w.writerow(["u1", "a" * 64, "ocr", "2"])
            w.writerow(["u2", "b" * 64, "text_layer", "9"])
            w.writerow(["u3", "c" * 64, "text_layer", "50"])     # text not cached
            w.writerow(["u4", "a" * 64, "ocr", "0"])
        picked = inputs_from_index(5, idx, cache)
        check([u for _, u in picked] == ["u2", "u1"],
              "--from-index takes cached agendas with keyword hits, most hits first")

    env = {**os.environ, "CI": "true"}
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--allow-ci",
                        "--out", OUT_DRAFT],
                       env=env, capture_output=True, text=True, timeout=60, cwd=ROOT)
    check(r.returncode == 0 and "refusing" not in r.stdout.lower(),
          "--allow-ci (agenda-extract.yml only) lets a CI run proceed")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="inputs", action="append", default=[],
                    help="agenda or minutes text file (repeatable)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--model-url", default=DEFAULT_MODEL_URL)
    ap.add_argument("--out", default=OUT_DRAFT)
    ap.add_argument("--from-index", type=int, metavar="N", default=0,
                    help="the N cached agendas with the most keyword hits "
                         "(data/agenda_text_index.csv); reads keyword windows only")
    ap.add_argument("--allow-ci", action="store_true",
                    help="run although CI is set; only agenda-extract.yml passes "
                         "this (owner decision 2026-10-01)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if refuse_in_ci() and not args.allow_ci:
        print("Refusing to run: CI is set. agenda_extract.py runs a local model "
              "and writes a draft for review. Only agenda-extract.yml opts in, "
              "with --allow-ci.")
        return EXIT_REFUSED
    if not draft_path_ok(args.out):
        print(f"Refusing to write {args.out}: drafts go to data/*_draft.csv only.")
        return EXIT_REFUSED
    jobs = [(p, "", False) for p in args.inputs]
    jobs += [(p, url, True) for p, url in inputs_from_index(args.from_index)]
    if not jobs:
        if args.from_index:
            print("No cached agenda text with keyword hits yet; nothing to extract.")
            return 0
        ap.print_help()
        return 0

    extract_fn = langextract_fn(args.model, args.model_url)
    all_rows, totals = [], {}
    for path, url, windowed in jobs:
        rows, drops = extract_file(path, extract_fn, args.model, windowed, url)
        all_rows.extend(rows)
        for k, v in drops.items():
            totals[k] = totals.get(k, 0) + v
        print(f"{path}: {len(rows)} candidate rows, "
              + ", ".join(f"{k} {v}" for k, v in sorted(drops.items())))
    write_draft(args.out, all_rows)
    dropped = sum(v for k, v in totals.items() if k != "retained_fields")
    print(f"total: {len(all_rows)} candidate rows from {len(jobs)} files; "
          f"{totals.get('retained_fields', 0)} fields retained, all with offsets that "
          f"slice the source exactly; {dropped} dropped ("
          + ", ".join(f"{k} {v}" for k, v in sorted(totals.items())
                      if k != "retained_fields") + ")")
    print(f"draft -> {os.path.relpath(args.out, ROOT)} (review before committing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
