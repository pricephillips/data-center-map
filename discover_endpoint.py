#!/usr/bin/env python3
"""
discover_endpoint.py — find out what a candidate source URL actually *is*,
for sources whose machine-readable route is unknown.

Why this exists

The registry already self-completes two families of source. An ArcGIS tracker
registers as `"url": null` plus a discovery block and discover_arcgis_layer.py
resolves the service URL in CI; a Socrata dataset does the same through
discover_socrata_dataset.py. Both work because the family is known in advance:
"this is an ArcGIS service, find which one".

The western sources in docs/western_coverage_sources.md are stuck one rung
below that. CEQAnet and Oregon's PAPA register are named, their host is known,
and every scoping note about them says the same thing — "API and bulk-export
path are unconfirmed and need a pin". That pin has stayed unset because it
needs someone on an unrestricted network to open the site and look, which is
a manual step that recurs once per source and therefore never happens.

This answers that question with evidence instead. Given a config's candidate
URLs it fetches each one from wherever it runs, classifies the response into a
family, and writes a report saying what the endpoint is and which tool should
take it from here. The reconnaissance becomes a scheduled job whose output is
committed, rather than a task in a memo.

What it deliberately does NOT do

It never resolves a fetchable url. discover_arcgis_layer.py and
discover_socrata_dataset.py may write a `resolved` block because a layer id or
a four-by-four *is* a complete endpoint for an adapter that already exists;
"this URL returned JSON" is not. So the report carries findings and a next
step, never a query_url, and fetch_permits.py cannot consume it — it reads
`arcgis_discovery_*` and `socrata_discovery_*` only, and a probe source
registers with `"adapter": null` so list_sources() does not enumerate it at
all. Both properties are selftested, because the whole value of a probe is
that it reports without promoting.

Nor does it re-implement the two discoverers. When a probe lands on an ArcGIS
root or a Socrata portal, the finding is "hand this to that tool, with this
domain" — the existing resolver stays the only thing that resolves.

Usage
  python discover_endpoint.py --config configs/ca_ceqanet.json
  python discover_endpoint.py --list-probe-sources
  python discover_endpoint.py --selftest

Stdlib only. Selftest is fully offline: every classification case runs against
a captured response body, so the classifier is tested without a network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

# The audit's own pattern, imported rather than copied so the two cannot
# drift. See emit_json_keys() for why a probe needs it at all.
from leak_audit import LEAK_RE

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
DATA = os.path.join(HERE, "data")
CONFIGS = os.path.join(HERE, "configs")

USER_AGENT = "hawthorn-baseline/1.0 (source endpoint reconnaissance)"

# Only enough of the body to tell one family from another. A probe that pulled
# whole documents would be a scraper, and this is explicitly not one.
BODY_SNIFF_BYTES = 65536
DEFAULT_TIMEOUT = 30

# How the report names the tool that should take each family onward. Keeping
# these as data rather than prose in the writer means a new family cannot be
# added without stating who handles it.
NEXT_STEP = {
    "arcgis_rest": (
        "Register as an ArcGIS source: add this URL to discovery.rest_roots "
        "in a config with \"adapter\": \"arcgis\", and let "
        "discover_arcgis_layer.py resolve the layer."),
    "socrata": (
        "Register as a Socrata source: set discovery.kind to \"socrata\" and "
        "discovery.domain to this host, and let discover_socrata_dataset.py "
        "resolve the four-by-four."),
    "ckan": (
        "CKAN portal. package_search on this host will name the dataset and "
        "its resources; a CKAN discoverer does not exist yet, so this is the "
        "point at which writing one becomes worth it."),
    "odata": (
        "OData service. $metadata describes the entity sets; a small adapter "
        "in fetch_permits.py can page it with $top/$skip."),
    "json": (
        "Returns JSON but matches no known portal family. The top-level keys "
        "are listed below; read the shape before deciding whether an adapter "
        "is warranted."),
    "csv": (
        "Returns tabular data directly. This is the cheapest possible case: "
        "the tabular adapter in fetch_permits.py may take it with a config "
        "and no new code."),
    "feed": (
        "RSS or Atom feed. Good for change detection even when the full "
        "record needs a second fetch."),
    "aspnet_postback": (
        "ASP.NET WebForms driven by __VIEWSTATE postbacks. There is no cheap "
        "adapter here: every query is a stateful form round-trip, so treat "
        "this as a negative finding and look for a report, subscription or "
        "export route instead of scraping the form."),
    "html_form": (
        "An HTML search form with no viewstate. Check whether its results are "
        "reachable by GET query string; if they are, a tabular or HTML "
        "adapter is feasible."),
    "html": (
        "Plain HTML with no form. Likely a landing page rather than the data "
        "route; look for a link to a search, report or download page and add "
        "it as another probe."),
    "unreachable": (
        "No response. Could be the network this ran on rather than the "
        "source; re-run in CI before concluding anything."),
    "error": (
        "The host answered with an error status. Note the status: a 403 or "
        "405 often means the path is real but the method or client is wrong."),
}


def http_probe(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch enough of a URL to classify it. Never raises: an unreachable
    endpoint is a finding, not a failure, and one dead probe must not abandon
    the others in the same config."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(BODY_SNIFF_BYTES)
            return {
                "status": int(getattr(resp, "status", 0) or 0),
                "content_type": str(resp.headers.get("Content-Type") or ""),
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "body": raw.decode("utf-8", errors="replace"),
                "final_url": resp.geturl(),
                "error": "",
            }
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(BODY_SNIFF_BYTES)
        except Exception:
            raw = b""
        return {
            "status": int(e.code),
            "content_type": str(e.headers.get("Content-Type") or "")
            if e.headers else "",
            "headers": {k.lower(): v for k, v in (e.headers or {}).items()},
            "body": raw.decode("utf-8", errors="replace"),
            "final_url": url,
            "error": f"HTTP {e.code}",
        }
    except Exception as e:
        return {"status": 0, "content_type": "", "headers": {}, "body": "",
                "final_url": url, "error": f"{type(e).__name__}: {e}"}


def _json_or_none(body: str):
    body = body.strip()
    if not body or body[0] not in "{[":
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def classify(resp: dict) -> tuple[str, str]:
    """Map one response to (family, evidence).

    Pure: takes the dict http_probe returns and nothing else, so every branch
    is reachable in the offline selftest. Order matters — the specific portal
    families are tested before the generic "it is JSON" fallback, because
    every one of them is also JSON.
    """
    if resp.get("error") and not resp.get("status"):
        return "unreachable", resp["error"]

    status = int(resp.get("status") or 0)
    ctype = (resp.get("content_type") or "").lower()
    headers = resp.get("headers") or {}
    body = resp.get("body") or ""
    low = body.lower()

    doc = _json_or_none(body)
    if isinstance(doc, dict):
        if "currentVersion" in doc and any(
                k in doc for k in ("services", "layers", "folders", "tables")):
            return "arcgis_rest", "JSON carries currentVersion and a service listing"
        if "resultSetSize" in doc and "results" in doc:
            return "socrata", "JSON matches the Socrata catalog envelope"
        if "help" in doc and "/api/3/action" in str(doc.get("help") or ""):
            return "ckan", "JSON help URL names the CKAN action API"
        if set(doc) >= {"success", "result"} and isinstance(doc.get("success"), bool):
            return "ckan", "JSON matches the CKAN success/result envelope"

    if any(k.startswith("x-soda2") for k in headers):
        return "socrata", "response carries an X-SODA2 header"

    if status >= 400:
        return "error", f"HTTP {status}"

    head = low[:4000]
    if "edmx" in head or ("<service" in head and "app:" in head):
        return "odata", "body declares an OData/Edmx service document"

    if doc is not None:
        return "json", f"parses as JSON ({ctype or 'no content-type'})"

    if "csv" in ctype or "spreadsheet" in ctype or "excel" in ctype:
        return "csv", f"content-type {ctype}"

    if "<rss" in low[:2000] or "<feed" in low[:2000]:
        return "feed", "body opens an RSS or Atom document"

    if "__viewstate" in low or "__eventvalidation" in low:
        return "aspnet_postback", "body carries __VIEWSTATE/__EVENTVALIDATION"

    if "<form" in low:
        return "html_form", "HTML containing a form, no viewstate"

    if "html" in ctype or "<html" in low[:2000]:
        return "html", f"HTML page ({status})"

    if body.strip():
        return "csv", "non-HTML text body; check whether it is delimited"

    return "error", f"empty body (HTTP {status})"


MAX_JSON_KEYS = 40


def emit_json_keys(body: str) -> list:
    """Top-level key names of a JSON body, bounded and vocabulary-safe.

    A probe report is a generated artifact, so leak_audit scans it, and its
    blocking tier rejects scorekeeping vocabulary. An earlier draft of this
    module put 600 characters of the fetched page into the report as a
    `sample` field: the first probe landing on a commission news page reading
    "applicant wins approval" would have taken the nightly pipeline red, which
    is exactly how the Census gazetteer took main red for four nights.

    So no raw body text reaches the report at all. Structured key names do,
    because they are the one part of a response that answers "what shape is
    this" without carrying page prose -- and even those are filtered through
    the audit's own pattern, so this cannot emit something the audit will
    reject even if a portal does ship a column named for it.
    """
    doc = _json_or_none(body)
    if isinstance(doc, list):
        doc = next((d for d in doc if isinstance(d, dict)), None)
    if not isinstance(doc, dict):
        return []
    out = []
    for k in list(doc)[:MAX_JSON_KEYS]:
        k = str(k)
        out.append("[redacted]" if LEAK_RE.search(k) else k)
    return out


def keyword_hits(body: str, keywords: list[str]) -> list[str]:
    """Which of the config's keywords appear. Says whether the probe landed on
    the right subject at all — an ArcGIS root that mentions none of them is a
    different agency's server, not this source."""
    low = (body or "").lower()
    return [k for k in keywords if k and k.lower() in low]


def probe_config(cfg: dict, timeout: int = DEFAULT_TIMEOUT,
                 fetcher=http_probe) -> dict:
    """Run every probe in a config and return the report body.

    `fetcher` is injected so the selftest exercises the whole assembly against
    canned responses rather than only the classifier.
    """
    disc = cfg.get("discovery") or {}
    keywords = [k for k in disc.get("keywords", []) if k]
    findings = []
    for probe in disc.get("probes", []):
        url = (probe or {}).get("url")
        if not url:
            continue
        resp = fetcher(url, timeout)
        family, evidence = classify(resp)
        body = resp.get("body") or ""
        findings.append({
            "url": url,
            "note": str(probe.get("note") or ""),
            "status": resp.get("status") or 0,
            "content_type": resp.get("content_type") or "",
            "final_url": resp.get("final_url") or url,
            "family": family,
            "evidence": evidence,
            "keyword_hits": keyword_hits(body, keywords),
            "next_step": NEXT_STEP.get(family, ""),
            "json_keys": emit_json_keys(body),
        })
    return {
        "source": cfg.get("source") or "unknown",
        "probed_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "keywords": keywords,
        "note": str(disc.get("note") or ""),
        "findings": findings,
    }


# Families that mean "an existing tool can take this from here", used by the
# report to say whether the probe actually got anywhere.
ACTIONABLE = ("arcgis_rest", "socrata", "ckan", "odata", "csv", "feed")


def summarize(report: dict) -> str:
    fams = [f["family"] for f in report["findings"]]
    hits = [f for f in report["findings"] if f["family"] in ACTIONABLE]
    if hits:
        return (f"{len(hits)} of {len(fams)} probes landed on a machine-"
                f"readable route ({', '.join(sorted({h['family'] for h in hits}))})")
    if fams and all(f in ("unreachable", "error") for f in fams):
        return "no probe reached the host; re-run where the network is open"
    return "no machine-readable route found; the site answers as HTML only"


def write_report(report: dict, outdir: str = DATA) -> tuple[str, str]:
    os.makedirs(outdir, exist_ok=True)
    source = report["source"]
    jpath = os.path.join(outdir, f"probe_discovery_{source}.json")
    mpath = os.path.join(outdir, f"probe_discovery_{source}.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")

    lines = [f"# Endpoint probe: {source}", "",
             f"Probed {report['probed_utc']}  ",
             f"**{summarize(report)}**", ""]
    if report.get("note"):
        lines += [report["note"], ""]
    lines += ["This report describes what each URL is. It never resolves a "
              "fetchable url: `fetch_permits.py` reads only "
              "`arcgis_discovery_*` and `socrata_discovery_*`, so nothing "
              "here can start a fetch on its own.", ""]
    for f in report["findings"]:
        head = f"## {f['url']}"
        lines += [head, ""]
        if f["note"]:
            lines += [f"_{f['note']}_", ""]
        lines += [f"- Family: **{f['family']}** — {f['evidence']}",
                  f"- HTTP {f['status']} `{f['content_type'] or 'no content-type'}`"]
        if f["final_url"] != f["url"]:
            lines.append(f"- Redirected to `{f['final_url']}`")
        lines.append("- Keywords present: " +
                     (", ".join(f["keyword_hits"]) if f["keyword_hits"]
                      else "_none_"))
        if f.get("json_keys"):
            lines.append("- Top-level keys: "
                         + ", ".join(f"`{k}`" for k in f["json_keys"]))
        if f["next_step"]:
            lines += ["", f"{f['next_step']}"]
        lines.append("")
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return jpath, mpath


def list_probe_sources(cfg_dir: str = CONFIGS) -> list[str]:
    """Config filenames registering a probe source, i.e. discovery.kind ==
    "probe". Mirrors fetch_permits.list_sources() so the workflow enumerates
    probe sources the same way it enumerates fetchable ones: adding one stays
    a config drop, never a workflow edit."""
    out = []
    for name in sorted(os.listdir(cfg_dir) if os.path.isdir(cfg_dir) else []):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(cfg_dir, name), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            continue
        if not isinstance(cfg, dict):
            continue
        if (cfg.get("discovery") or {}).get("kind") == "probe":
            out.append(name)
    return out


# ---------------------------------------------------------------- selftest

def _resp(body="", status=200, ctype="", headers=None, error=""):
    return {"status": status, "content_type": ctype,
            "headers": headers or {}, "body": body,
            "final_url": "https://example.test/", "error": error}


def selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    # --- classifier: one case per family -------------------------------
    ck("arcgis root is arcgis_rest",
       classify(_resp(json.dumps(
           {"currentVersion": 11.1, "folders": [], "services": []})))[0]
       == "arcgis_rest")
    ck("socrata catalog is socrata",
       classify(_resp(json.dumps(
           {"results": [], "resultSetSize": 0})))[0] == "socrata")
    ck("X-SODA2 header is socrata",
       classify(_resp("<html></html>", ctype="text/html",
                      headers={"x-soda2-fields": "a,b"}))[0] == "socrata")
    ck("CKAN help URL is ckan",
       classify(_resp(json.dumps(
           {"help": "https://x/api/3/action/help_show",
            "success": True, "result": {}})))[0] == "ckan")
    ck("CKAN success/result is ckan",
       classify(_resp(json.dumps({"success": True, "result": {"a": 1}})))[0]
       == "ckan")
    ck("edmx is odata",
       classify(_resp('<edmx:Edmx xmlns:edmx="..."></edmx:Edmx>',
                      ctype="application/xml"))[0] == "odata")
    ck("bare JSON is json",
       classify(_resp(json.dumps({"rows": [1, 2]}),
                      ctype="application/json"))[0] == "json")
    ck("csv content-type is csv",
       classify(_resp("a,b\n1,2\n", ctype="text/csv"))[0] == "csv")
    ck("rss body is feed",
       classify(_resp('<?xml version="1.0"?><rss version="2.0"></rss>',
                      ctype="application/xml"))[0] == "feed")
    ck("viewstate form is aspnet_postback",
       classify(_resp('<html><form><input name="__VIEWSTATE" value="x"/>'
                      '</form></html>', ctype="text/html"))[0]
       == "aspnet_postback")
    ck("plain form is html_form",
       classify(_resp('<html><form action="/s"></form></html>',
                      ctype="text/html"))[0] == "html_form")
    ck("plain page is html",
       classify(_resp("<html><body>hello</body></html>",
                      ctype="text/html"))[0] == "html")
    ck("403 is error",
       classify(_resp("denied", status=403, ctype="text/plain"))[0] == "error")
    ck("connection failure is unreachable",
       classify(_resp(status=0, error="URLError: no route"))[0]
       == "unreachable")

    # Portal families are also valid JSON, so ordering inside classify() is
    # load-bearing: if the generic branch ever moved up, every ArcGIS and
    # Socrata probe would report as an anonymous JSON endpoint and the report
    # would stop naming the tool that can resolve it.
    ck("portal families beat the generic JSON branch",
       classify(_resp(json.dumps(
           {"currentVersion": 10.9, "layers": [{"id": 0}]}),
           ctype="application/json"))[0] == "arcgis_rest")

    # An error status must not mask a portal family: an ArcGIS instance that
    # answers 400 to a bare root still identifies itself in the body.
    ck("portal family is read before the error status",
       classify(_resp(json.dumps({"currentVersion": 10.9, "services": []}),
                      status=400))[0] == "arcgis_rest")

    # --- every family the classifier can return has a next step ---------
    families = set()
    for r in (_resp(json.dumps({"currentVersion": 1, "services": []})),
              _resp(json.dumps({"results": [], "resultSetSize": 0})),
              _resp(json.dumps({"success": True, "result": {}})),
              _resp("<edmx:Edmx/>", ctype="application/xml"),
              _resp(json.dumps({"a": 1})),
              _resp("a,b\n", ctype="text/csv"),
              _resp("<rss></rss>", ctype="application/xml"),
              _resp('<input name="__VIEWSTATE"/>', ctype="text/html"),
              _resp("<form></form>", ctype="text/html"),
              _resp("<html>x</html>", ctype="text/html"),
              _resp("no", status=500, ctype="text/plain"),
              _resp(status=0, error="boom")):
        families.add(classify(r)[0])
    ck("every reachable family carries a next step",
       families and all(NEXT_STEP.get(f) for f in families))

    # --- keyword hits ---------------------------------------------------
    ck("keyword hits are case-insensitive and reported",
       keyword_hits("The State CLEARINGHOUSE database",
                    ["state clearinghouse", "papa"]) == ["state clearinghouse"])
    ck("no keywords is an empty list, not a failure",
       keyword_hits("<html/>", ["ceqa"]) == [])

    # --- assembly against canned responses ------------------------------
    canned = {
        "https://a.test/rest/services": _resp(json.dumps(
            {"currentVersion": 11.1, "services": [], "folders": []})),
        "https://a.test/Search.aspx": _resp(
            '<html><form><input name="__VIEWSTATE" value="q"/></form></html>',
            ctype="text/html"),
    }
    cfg = {"source": "probe_demo", "adapter": None, "url": None,
           "discovery": {"kind": "probe", "keywords": ["services"],
                         "note": "demo",
                         "probes": [{"url": u, "note": ""} for u in canned]}}
    rep = probe_config(cfg, fetcher=lambda u, t: canned[u])
    ck("every probe produces one finding",
       len(rep["findings"]) == len(canned))
    ck("assembly classifies each probe independently",
       {f["family"] for f in rep["findings"]}
       == {"arcgis_rest", "aspnet_postback"})
    ck("a probe with no url is skipped rather than fatal",
       len(probe_config(
           {"source": "x", "discovery": {"probes": [{"note": "no url"}]}},
           fetcher=lambda u, t: _resp())["findings"]) == 0)

    # --- no raw body text reaches the report -----------------------------
    # This is the defect that took main red for four nights, in a different
    # file: a generated artifact carrying text nobody vetted, scanned by an
    # audit whose blocking tier stops the pipeline. Asserted directly rather
    # than trusted, and asserted against a body that actually contains the
    # vocabulary rather than against a stand-in that does not.
    hostile = ("<html><body>Commission news: the applicant wins approval and "
               "the appeal was lost on procedural grounds.</body></html>")
    hcfg = {"source": "hostile", "discovery": {"kind": "probe", "keywords": [],
            "probes": [{"url": "https://h.test/", "note": ""}]}}
    hrep = probe_config(hcfg, fetcher=lambda u, t: _resp(
        hostile, ctype="text/html"))
    ck("no finding carries raw body text",
       all("sample" not in f for f in hrep["findings"]))
    ck("a report built from vocabulary-bearing HTML is audit-clean",
       not LEAK_RE.search(json.dumps(hrep)))

    # JSON keys are the one structured thing that does reach the report, and
    # they are filtered too, so a portal shipping such a column cannot leak.
    ck("json keys are reported for a JSON body",
       emit_json_keys(json.dumps({"permit_id": 1, "status": "open"}))
       == ["permit_id", "status"]),
    ck("a key carrying the vocabulary is redacted, not emitted",
       emit_json_keys(json.dumps({"wins": 1, "county": "X"}))
       == ["[redacted]", "county"])
    ck("a JSON array reports the first object's keys",
       emit_json_keys(json.dumps([{"a": 1, "b": 2}])) == ["a", "b"])
    ck("a non-JSON body reports no keys", emit_json_keys("<html/>") == [])
    ck("key count is bounded",
       len(emit_json_keys(json.dumps({str(i): i for i in range(200)})))
       == MAX_JSON_KEYS)

    # --- the anti-promotion properties ----------------------------------
    # These are the point of a probe, so they are asserted rather than
    # documented. A probe reports; only a discoverer resolves.
    blob = json.dumps(rep)
    ck("report carries no resolved block", "resolved" not in rep)
    ck("report carries no query_url anywhere", "query_url" not in blob)

    # fetch_permits.py looks for exactly two filename prefixes when a config
    # has "url": null. The probe's must be neither, or a reconnaissance
    # finding could silently become a fetch target.
    src = rep["source"]
    consumable = {f"arcgis_discovery_{src}.json", f"socrata_discovery_{src}.json"}
    ck("probe report filename is not one fetch_permits consumes",
       f"probe_discovery_{src}.json" not in consumable)

    # And the live cross-module invariant: a registered probe source must not
    # appear in the fetch loop at all. It has "adapter": null, and
    # list_sources() keys on a truthy adapter.
    try:
        import fetch_permits
        fetchable = set(fetch_permits.list_sources())
        probes = set(list_probe_sources())
        ck("registered probe sources are invisible to the fetch loop",
           not (fetchable & probes))
        ck("probe sources are actually registered", len(probes) > 0)
    except Exception as e:  # pragma: no cover - import guard only
        ck(f"fetch_permits import for cross-check ({e})", False)

    # --- determinism -----------------------------------------------------
    again = probe_config(cfg, fetcher=lambda u, t: canned[u])
    ck("re-probing unchanged endpoints reports identically",
       rep["findings"] == again["findings"])

    # --- summary ---------------------------------------------------------
    ck("an actionable family is summarized as such",
       "machine-readable route" in summarize(rep))
    ck("an all-error probe says to re-run on an open network",
       "network is open" in summarize(
           {"findings": [{"family": "unreachable"}, {"family": "error"}]}))
    ck("html-only probes are summarized honestly",
       "HTML only" in summarize({"findings": [{"family": "html"}]}))

    ok = sum(1 for _, c in checks if c)
    for name, cond in checks:
        if not cond:
            print(f"  FAIL {name}")
    print(f"discover_endpoint selftest: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--outdir", default=DATA)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--list-probe-sources", action="store_true",
                    help="print configs under configs/ whose discovery.kind "
                         "is \"probe\", one per line, and exit")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.list_probe_sources:
        for name in list_probe_sources():
            print(name)
        return 0
    if not args.config:
        print("ERROR: --config required (or --list-probe-sources)")
        return 1

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    disc = cfg.get("discovery") or {}
    if disc.get("kind") != "probe":
        print(f"ERROR: {args.config} is not a probe source "
              f"(discovery.kind is {disc.get('kind')!r})")
        return 1
    if not disc.get("probes"):
        print(f"ERROR: {args.config} registers no discovery.probes")
        return 1

    report = probe_config(cfg, timeout=args.timeout)
    jpath, mpath = write_report(report, args.outdir)
    print(f"{report['source']}: {summarize(report)}")
    for f in report["findings"]:
        print(f"  {f['family']:>16}  HTTP {f['status']:>3}  {f['url']}")
    print(f"-> {os.path.relpath(jpath, ROOT)}, {os.path.relpath(mpath, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
