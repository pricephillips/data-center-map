# Endpoint probe: az_acc_edocket

Probed 2026-09-15T12:41:36Z  
**no machine-readable route found; the site answers as HTML only**

Arizona Corporation Commission eDocket. Registered 2026-09-09 for item 7. Arizona has the densest existing western coverage to build on: five counties carry recorded opposition events, 24 in total, led by Maricopa at 14 against 63 tracked facilities. So a docket route here is depth and corroboration rather than discovery -- which also makes it the best of the four for checking a new source against what is already known.

This report describes what each URL is. It never resolves a fetchable url: `fetch_permits.py` reads only `arcgis_discovery_*` and `socrata_discovery_*`, so nothing here can start a fetch on its own.

## https://edocket.azcc.gov/search/docket-search

_Docket search. The /search/<thing>/item-detail/<numeric id> URL shape elsewhere on this host is characteristic of a single-page app talking to a JSON backend, which would be the thing to find rather than the page itself._

- Family: **html** — HTML page (200)
- HTTP 200 `text/html`
- Keywords present: docket, corporation commission

Plain HTML with no form. Likely a landing page rather than the data route; look for a link to a search, report or download page and add it as another probe.

## https://edocket.azcc.gov/search/document-search

_Document search, which the site describes as filterable by company name, docket number, document code, decision number and filed date range. That filter set is close to what a large-load tariff query needs._

- Family: **html** — HTML page (200)
- HTTP 200 `text/html`
- Keywords present: docket, corporation commission

Plain HTML with no form. Likely a landing page rather than the data route; look for a link to a search, report or download page and add it as another probe.

## https://edocket.azcc.gov/search/docket-search/item-detail/29465

_One docket detail page by numeric id, from a public citation. Confirms whether detail pages render server-side or arrive empty and hydrate from an API — the answer decides whether this is a scrape or an adapter._

- Family: **html** — HTML page (200)
- HTTP 200 `text/html`
- Keywords present: docket, corporation commission

Plain HTML with no form. Likely a landing page rather than the data route; look for a link to a search, report or download page and add it as another probe.

## https://edocket.azcc.gov/

_Portal root, for whatever route listing or client bundle reference it exposes._

- Family: **html** — HTML page (200)
- HTTP 200 `text/html`
- Keywords present: docket, corporation commission

Plain HTML with no form. Likely a landing page rather than the data route; look for a link to a search, report or download page and add it as another probe.

