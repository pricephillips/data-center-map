#!/usr/bin/env python3
"""agenda_text.py -- text for every agenda PDF the local meeting feed links,
with OCR for the scanned ones, and the keyword pass over that text.

Spec 007, US2. local_meeting_feed.py records where each meeting's agenda lives
but nothing read the documents, and a large share of small-county agendas are
scanned images with no text layer at all: a keyword search over them finds
nothing, which reads exactly like a meeting that never mentioned a data center.

For each document_url in data/local_meeting_feed.csv this module:

  1. downloads it (capped per run and per file) and keeps it only if the bytes
     are a PDF;
  2. hashes the bytes with SHA-256. The hash, not the URL, is the cache key,
     so an agenda re-posted under a new link is never processed twice;
  3. reads the text layer page by page with pdfminer.six;
  4. when any page has no usable text, runs
       ocrmypdf --skip-text -l eng --sidecar <txt> <in.pdf> <out.pdf>
     which OCRs only the pages without text, and merges the OCR text into
     those pages;
  5. caches the text at .cache/agenda_text/<sha256>.txt (gitignored; CI
     persists it with actions/cache) and runs the keyword pass;
  6. upserts one row per document into data/agenda_text_index.csv.

OCR runs only where tesseract and ghostscript are installed (FR-002). Without
them a scanned document is recorded as ocr_unavailable and retried on the next
run; it is never recorded as containing nothing. The selftest also fails any
workflow that runs this module without installing both packages.

The index carries counts and matched terms from a fixed list, never agenda
text, so nothing unreviewed enters the repository.

Only this module imports ocrmypdf and pdfminer.six.

Usage:
  python agenda_text.py --run [--max-docs 40] [--max-mb 25]
  python agenda_text.py --file some_agenda.pdf
  python agenda_text.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(ROOT, "data", "local_meeting_feed.csv")
INDEX = os.path.join(ROOT, "data", "agenda_text_index.csv")
CACHE_DIR = os.path.join(ROOT, ".cache", "agenda_text")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "coverage_expansion")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

INDEX_COLS = ["document_url", "sha256", "jurisdiction", "state", "text_source",
              "pages", "text_chars", "keyword_hits", "matched_terms",
              "processed_at", "resolved_url"]

# A page with fewer non-space characters than this has no usable text layer.
# Scanned pages come back empty or with a stray form feed; a real page of
# agenda text has hundreds.
MIN_PAGE_CHARS = 20
THROTTLE_S = 1.0
TIMEOUT_S = 30
USER_AGENT = "data-center-map-agenda-text/1.0 (+github.com/pricephillips/data-center-map)"

# Statuses retried on the next run. Everything else is final for that URL.
RETRY_STATUSES = {"ocr_unavailable", "fetch_error", "extractor_unavailable"}

# The fixed keyword list. Activity-descriptive terms only.
TERMS = {
    "data center": re.compile(r"\bdata\s*cent(?:er|re)s?\b", re.IGNORECASE),
    "hyperscale": re.compile(r"\bhyperscal\w*", re.IGNORECASE),
    "moratorium": re.compile(r"\bmoratori(?:um|ums|a)\b", re.IGNORECASE),
    "rezoning": re.compile(r"\brezon(?:e|es|ed|ing)\b", re.IGNORECASE),
    "special use permit": re.compile(r"\bspecial\s+use\s+permits?\b", re.IGNORECASE),
    "conditional use": re.compile(r"\bconditional\s+use\b", re.IGNORECASE),
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nonspace(s: str) -> int:
    return len(re.sub(r"\s+", "", s or ""))


def keyword_hits(text: str) -> tuple[int, list[str]]:
    total, matched = 0, []
    for term, rx in TERMS.items():
        n = len(rx.findall(text or ""))
        if n:
            total += n
            matched.append(term)
    return total, matched


def pages_needing_ocr(pages: list[str]) -> list[int]:
    return [i for i, p in enumerate(pages) if nonspace(p) < MIN_PAGE_CHARS]


def is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(b"%PDF")


# A landing page, not a document: CivicPlus RSS items link to
# AgendaCenter/PreviousVersions/<id>, an HTML page whose agenda PDF sits behind
# an AgendaCenter/ViewFile/... link. The first run indexed all 40 such links
# as not_pdf. One hop, same host, agenda links before minutes.
HREF_RX = re.compile(rb"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
DOC_LINK_RX = re.compile(r"(ViewFile/|\.pdf(?:$|\?))", re.IGNORECASE)


def pdf_links(html: bytes, page_url: str) -> list[str]:
    """Same-host links on a landing page that look like documents.

    Ordered agendas first, then anything else, then minutes; deduplicated.
    """
    from urllib.parse import urljoin, urlparse
    host = urlparse(page_url).netloc.lower()
    seen, links = set(), []
    for m in HREF_RX.finditer(html or b""):
        href = m.group(1).decode("utf-8", "replace").replace("&amp;", "&").strip()
        url = urljoin(page_url, href)
        if (urlparse(url).netloc.lower() != host or url in seen
                or not DOC_LINK_RX.search(url)):
            continue
        seen.add(url)
        links.append(url)
    def rank(u: str) -> int:
        # Not the whole URL: every CivicPlus link contains "AgendaCenter".
        low = u.lower()
        tail = (low.split("viewfile/", 1)[1] if "viewfile/" in low
                else low.rsplit("/", 1)[-1]).split("?")[0]
        return 0 if "agenda" in tail else 2 if "minute" in tail else 1
    return sorted(links, key=rank)


# ---------------------------------------------------------------------------
# Text layer and OCR
# ---------------------------------------------------------------------------

def extract_text_layer(data: bytes) -> list[str] | None:
    """Text per page, or None when pdfminer.six is not installed."""
    try:
        from pdfminer.high_level import extract_text  # noqa: PLC0415
        from pdfminer.pdfpage import PDFPage  # noqa: PLC0415
    except ImportError:
        return None
    n = sum(1 for _ in PDFPage.get_pages(io.BytesIO(data)))
    return [extract_text(io.BytesIO(data), page_numbers=[i]) for i in range(n)]


def ocr_tools_available() -> bool:
    """FR-002: OCR only where tesseract and ghostscript are installed."""
    if not (shutil.which("tesseract") and shutil.which("gs")):
        return False
    try:
        import ocrmypdf  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def run_ocr(data: bytes) -> list[str]:
    """OCR the pages without a text layer. Returns sidecar text per page
    (pages OCRmyPDF skipped come back as its skip marker)."""
    with tempfile.TemporaryDirectory() as td:
        src, out, side = (os.path.join(td, n) for n in ("in.pdf", "out.pdf", "side.txt"))
        with open(src, "wb") as fh:
            fh.write(data)
        subprocess.run([sys.executable, "-m", "ocrmypdf", "--skip-text", "-l", "eng",
                        "--sidecar", side, "--output-type", "pdf", "-q", src, out],
                       check=True, capture_output=True, timeout=600)
        with open(side, encoding="utf-8", errors="replace") as fh:
            return fh.read().split("\f")


def cache_paths(sha: str, cache_dir: str) -> tuple[str, str]:
    return os.path.join(cache_dir, f"{sha}.txt"), os.path.join(cache_dir, f"{sha}.src")


def process_pdf(data: bytes, cache_dir: str = CACHE_DIR, ocr_runner=None,
                ocr_available=None) -> dict:
    """Index fields for one PDF. A cached hash is never reprocessed."""
    sha = sha256_bytes(data)
    txt_path, src_path = cache_paths(sha, cache_dir)
    if os.path.exists(txt_path) and os.path.exists(src_path):
        with open(txt_path, encoding="utf-8") as fh:
            text = fh.read()
        with open(src_path, encoding="utf-8") as fh:
            source, pages = (fh.read().strip().split(",") + ["0"])[:2]
        return _row(sha, source, int(pages or 0), text, cached=True)

    pages = extract_text_layer(data)
    if pages is None:
        return _row(sha, "extractor_unavailable", 0, "")
    missing = pages_needing_ocr(pages)
    source = "text_layer"
    if missing:
        available = ocr_tools_available() if ocr_available is None else ocr_available
        if not available:
            return _row(sha, "ocr_unavailable", len(pages), "\n".join(pages))
        try:
            ocr_pages = (ocr_runner or run_ocr)(data)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            print(f"  ocr failed for {sha[:12]}: {type(e).__name__}")
            return _row(sha, "ocr_error", len(pages), "\n".join(pages))
        for i in missing:
            if i < len(ocr_pages):
                pages[i] = ocr_pages[i]
        source = "ocr"
    text = "\n\f".join(pages)
    os.makedirs(cache_dir, exist_ok=True)
    with open(txt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    with open(src_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{source},{len(pages)}\n")
    return _row(sha, source, len(pages), text)


def _row(sha: str, source: str, pages: int, text: str, cached: bool = False) -> dict:
    hits, terms = keyword_hits(text)
    return {"sha256": sha, "text_source": source, "pages": pages,
            "text_chars": nonspace(text), "keyword_hits": hits,
            "matched_terms": "; ".join(terms), "cached": cached}


# ---------------------------------------------------------------------------
# Feed run
# ---------------------------------------------------------------------------

def fetch_bytes(url: str, max_bytes: int) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, b""


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_index(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = sorted(rows, key=lambda r: (r.get("state", ""), r.get("jurisdiction", ""),
                                       r.get("document_url", "")))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX_COLS, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in INDEX_COLS})


def run(max_docs: int, max_mb: float, feed: str = FEED, index: str = INDEX,
        cache_dir: str = CACHE_DIR, fetcher=fetch_bytes, sleep=time.sleep) -> dict:
    existing = {r["document_url"]: r for r in read_csv(index)}
    todo, seen = [], set()
    for r in read_csv(feed):
        url = (r.get("document_url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        prev = existing.get(url)
        # A not_pdf row from before landing-page resolution (blank
        # resolved_url) is retried once; after that not_pdf is final.
        legacy_not_pdf = (prev and prev.get("text_source") == "not_pdf"
                          and not prev.get("resolved_url"))
        if prev and prev.get("text_source") not in RETRY_STATUSES and not legacy_not_pdf:
            continue
        todo.append(r)
    max_bytes = int(max_mb * 1024 * 1024)
    stats: dict[str, int] = {}
    today = datetime.now(timezone.utc).date().isoformat()
    for r in todo[:max_docs]:
        url = r["document_url"].strip()
        sleep(THROTTLE_S)
        status, data = fetcher(url, max_bytes)
        base = {"document_url": url, "jurisdiction": r.get("jurisdiction", ""),
                "state": r.get("state", ""), "processed_at": today}
        if status != 200 or not data:
            row = {**base, "text_source": "fetch_error"}
        elif len(data) > max_bytes:
            row = {**base, "sha256": "", "text_source": "too_large"}
        elif not is_pdf(data):
            row = {**base, "sha256": sha256_bytes(data), "text_source": "not_pdf",
                   "resolved_url": "none"}
            for link in pdf_links(data, url)[:1]:
                sleep(THROTTLE_S)
                st2, doc = fetcher(link, max_bytes)
                if st2 == 200 and doc and len(doc) <= max_bytes and is_pdf(doc):
                    row = {**base, **process_pdf(doc, cache_dir), "resolved_url": link}
                else:
                    row["resolved_url"] = f"{link} (not a PDF or HTTP {st2})"
        else:
            row = {**base, **process_pdf(data, cache_dir)}
        existing[url] = row
        stats[row["text_source"]] = stats.get(row["text_source"], 0) + 1
    write_index(index, list(existing.values()))
    stats["deferred"] = max(0, len(todo) - max_docs)
    return stats


# ---------------------------------------------------------------------------
# Workflow guard (FR-002)
# ---------------------------------------------------------------------------

def workflow_guard_violations(workflows_dir: str = WORKFLOWS) -> list[str]:
    """Workflows that run agenda_text.py for real (--run or --file) without
    installing both tesseract-ocr and ghostscript."""
    bad = []
    for path in sorted(glob.glob(os.path.join(workflows_dir, "*.yml"))):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if not re.search(r"agenda_text\.py\s+--(?:run|file)\b", text):
            continue
        if "tesseract-ocr" not in text or "ghostscript" not in text:
            bad.append(os.path.basename(path))
    return bad


# ---------------------------------------------------------------------------
# Selftest (network-free)
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        print(f"  {'pass' if cond else 'FAIL'} {label}")
        ok = ok and bool(cond)

    check(keyword_hits("A DATA CENTER and two datacenters; rezoning.")
          == (3, ["data center", "rezoning"]), "keyword pass counts terms, case-insensitive")
    check(keyword_hits("the center of data")[0] == 0, "word order matters")
    check(pages_needing_ocr(["x" * 400, "\f", "  "]) == [1, 2],
          "pages without a usable text layer are the ones sent to OCR")
    check(is_pdf(b"%PDF-1.7\n...") and not is_pdf(b"<html>"), "PDF sniffing")
    check(workflow_guard_violations() == [],
          "every workflow that runs agenda_text.py installs tesseract-ocr and ghostscript")
    with tempfile.TemporaryDirectory() as td:
        wf = os.path.join(td, "bad.yml")
        with open(wf, "w", encoding="utf-8") as fh:
            fh.write("steps:\n  - run: python agenda_text.py --run\n")
        check(workflow_guard_violations(td) == ["bad.yml"],
              "the guard catches a workflow that runs OCR without the system packages")

    with open(os.path.join(FIXTURES, "text.pdf"), "rb") as fh:
        text_pdf = fh.read()
    with open(os.path.join(FIXTURES, "scanned.pdf"), "rb") as fh:
        scanned_pdf = fh.read()

    try:
        import pdfminer  # noqa: F401, PLC0415
        have_pdfminer = True
    except ImportError:
        have_pdfminer = False

    if not have_pdfminer:
        print("  SKIP pdf fixtures (pdfminer.six not installed)")
        with tempfile.TemporaryDirectory() as td:
            r = process_pdf(text_pdf, td)
            check(r["text_source"] == "extractor_unavailable",
                  "without pdfminer.six a PDF is retried later, not recorded as empty")
    else:
        with tempfile.TemporaryDirectory() as td:
            calls = []

            def stub_ocr(data):
                calls.append(sha256_bytes(data))
                return ["Planning Commission Minutes\nItem 7. Data center rezoning approved."]

            t = process_pdf(text_pdf, td, ocr_runner=stub_ocr, ocr_available=True)
            s = process_pdf(scanned_pdf, td, ocr_runner=stub_ocr, ocr_available=True)
            check(t["text_source"] == "text_layer" and s["text_source"] == "ocr",
                  "only the scanned PDF is OCR'd")
            check(calls == [sha256_bytes(scanned_pdf)], "OCR ran once, on the scanned PDF")
            check("data center" in t["matched_terms"] and "data center" in s["matched_terms"],
                  "the keyword pass finds 'data center' in both")
            again = process_pdf(scanned_pdf, td, ocr_runner=stub_ocr, ocr_available=True)
            check(again["cached"] and len(calls) == 1 and again["text_source"] == "ocr",
                  "a second pass is a SHA-256 cache hit and does not OCR again")
            u = process_pdf(scanned_pdf, os.path.join(td, "fresh"), ocr_available=False)
            check(u["text_source"] == "ocr_unavailable" and u["text_source"] in RETRY_STATUSES,
                  "without tesseract and gs a scanned PDF is ocr_unavailable and retried")

            feed = os.path.join(td, "feed.csv")
            with open(feed, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(["jurisdiction", "state", "document_url"])
                w.writerow(["Test County, VA", "VA", "https://example.gov/a.pdf"])
                w.writerow(["Test County, VA", "VA", "https://example.gov/page"])
                w.writerow(["Test County, VA", "VA", "https://example.gov/gone.pdf"])
                w.writerow(["Test County, VA", "VA",
                            "https://example.gov/AgendaCenter/PreviousVersions/7"])
            landing = (b'<html><a href="https://other.example/x.pdf">x</a>'
                       b'<a href="/AgendaCenter/ViewFile/Minutes/_0101-7">m</a>'
                       b'<a href="/AgendaCenter/ViewFile/Agenda/_0101-7?html=false&amp;x=1">a</a></html>')
            answers = {"https://example.gov/a.pdf": (200, text_pdf),
                       "https://example.gov/page": (200, b"<html></html>"),
                       "https://example.gov/gone.pdf": (404, b""),
                       "https://example.gov/AgendaCenter/PreviousVersions/7": (200, landing),
                       "https://example.gov/AgendaCenter/ViewFile/Agenda/_0101-7?html=false&x=1":
                           (200, scanned_pdf)}
            check(pdf_links(landing, "https://example.gov/AgendaCenter/PreviousVersions/7")
                  == ["https://example.gov/AgendaCenter/ViewFile/Agenda/_0101-7?html=false&x=1",
                      "https://example.gov/AgendaCenter/ViewFile/Minutes/_0101-7"],
                  "landing page: same-host document links, agenda before minutes")
            index = os.path.join(td, "index.csv")
            st = run(10, 25, feed, index, os.path.join(td, "c2"),
                     fetcher=lambda u, m: answers[u], sleep=lambda s: None)
            rows = {r["document_url"]: r for r in read_csv(index)}
            check(st.get("text_layer") == 1 and st.get("not_pdf") == 1
                  and st.get("fetch_error") == 1, "run records one status per document")
            lp = rows["https://example.gov/AgendaCenter/PreviousVersions/7"]
            check(lp["text_source"] in ("ocr", "ocr_unavailable")
                  and lp["resolved_url"].endswith("_0101-7?html=false&x=1"),
                  "a CivicPlus landing page resolves one hop to its agenda PDF")
            check(rows["https://example.gov/page"]["resolved_url"] == "none",
                  "a page with no document link stays not_pdf, marked resolved")
            # A pre-resolution not_pdf row (blank resolved_url) is retried once.
            legacy = read_csv(index)
            for r in legacy:
                if r["document_url"] == "https://example.gov/page":
                    r["resolved_url"] = ""
            write_index(index, legacy)
            check(rows["https://example.gov/a.pdf"]["sha256"] == sha256_bytes(text_pdf),
                  "the index row carries the content hash")
            fetched = []
            run(10, 25, feed, index, os.path.join(td, "c2"),
                fetcher=lambda u, m: (fetched.append(u), answers[u])[1],
                sleep=lambda s: None)
            check(sorted(fetched) == ["https://example.gov/gone.pdf", "https://example.gov/page"],
                  "final documents are skipped; the fetch error and a legacy not_pdf are retried")
            with open(index, "rb") as fh:
                check(b"\r\n" not in fh.read(), "index is LF")

        if ocr_tools_available():
            with tempfile.TemporaryDirectory() as td:
                real = process_pdf(scanned_pdf, td)
                check(real["text_source"] == "ocr" and "data center" in real["matched_terms"],
                      "real OCRmyPDF round trip reads 'data center' from the scanned fixture")
        else:
            print("  SKIP real OCR round trip (ocrmypdf, tesseract or gs not installed)")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true",
                    help="process agenda links from data/local_meeting_feed.csv")
    ap.add_argument("--max-docs", type=int, default=40)
    ap.add_argument("--max-mb", type=float, default=25.0)
    ap.add_argument("--file", help="process one local PDF and print its row")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.file:
        with open(args.file, "rb") as fh:
            row = process_pdf(fh.read())
        for k in INDEX_COLS:
            if k in row:
                print(f"{k}: {row[k]}")
        return 0
    if args.run:
        print(f"OCR available: {ocr_tools_available()}")
        stats = run(args.max_docs, args.max_mb)
        print("agenda text: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
        print(f"-> {os.path.relpath(INDEX, ROOT)}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
