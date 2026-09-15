# Endpoint probe: ca_ceqanet

Probed 2026-09-15T12:41:37Z  
**1 of 5 probes landed on a machine-readable route (csv)**

California is the largest western state by tracked facilities (112) and by recorded events (25), and every discretionary data center there files a CEQA notice, so CEQAnet is the highest-value unresolved western source. It has stayed unresolved because its API and bulk-export path are unconfirmed and confirming them needs a look from an unrestricted network. Registered 2026-09-09 as a probe rather than a fetch: nothing here can start a download, and the report only says what each URL is and which tool could take it onward.

This report describes what each URL is. It never resolves a fetchable url: `fetch_permits.py` reads only `arcgis_discovery_*` and `socrata_discovery_*`, so nothing here can start a fetch on its own.

## https://ceqanet.lci.ca.gov/

_Current site root. CEQAnet moved host when OPR became the Office of Land Use and Climate Innovation; the 2026-09-02 scan recorded the old ceqanet.opr.ca.gov address._

- Family: **html_form** — HTML containing a form, no viewstate
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: ceqa, state clearinghouse, environmental

An HTML search form with no viewstate. Check whether its results are reachable by GET query string; if they are, a tabular or HTML adapter is feasible.

## https://ceqanet.opr.ca.gov/

_Former host, probed to confirm whether it redirects or is retired — the answer decides whether anything else in the repo still citing it needs changing._

- Family: **html_form** — HTML containing a form, no viewstate
- HTTP 200 `text/html; charset=utf-8`
- Redirected to `https://ceqanet.lci.ca.gov/`
- Keywords present: ceqa, state clearinghouse, environmental

An HTML search form with no viewstate. Check whether its results are reachable by GET query string; if they are, a tabular or HTML adapter is feasible.

## https://ceqanet.lci.ca.gov/Search

_Search page. If results are reachable by GET query string this becomes a tabular or HTML adapter; if it is a viewstate postback it is not worth scraping._

- Family: **html_form** — HTML containing a form, no viewstate
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: ceqa, environmental, negative declaration

An HTML search form with no viewstate. Check whether its results are reachable by GET query string; if they are, a tabular or HTML adapter is feasible.

## https://data.ca.gov/api/3/action/package_search?q=CEQA&rows=25

_California's open-data portal runs CKAN. A CEQA dataset published here would be a far cheaper route than the site itself._

- Family: **csv** — non-HTML text body; check whether it is delimited
- HTTP 200 `application/json;charset=utf-8`
- Keywords present: ceqa, environmental, lead agency

Returns tabular data directly. This is the cheapest possible case: the tabular adapter in fetch_permits.py may take it with a config and no new code.

## https://catalog.data.gov/api/3/action/package_search?q=CEQAnet&rows=25

_Federal catalog, as a second CKAN route: state datasets are frequently harvested here even when the state portal does not surface them._

- Family: **error** — HTTP 404
- HTTP 404 `application/json`
- Keywords present: _none_
- Top-level keys: `detail`, `message`

The host answered with an error status. Note the status: a 403 or 405 often means the path is real but the method or client is wrong.

