# Endpoint probe: wa_utc_dockets

Probed 2026-09-15T12:42:18Z  
**1 of 4 probes landed on a machine-readable route (csv)**

Washington UTC. Registered 2026-09-09 for item 7 of docs/western_coverage_sources.md. Finding 4 of that memo establishes why western commissions matter: in non-ISO territory there is no regional load interconnection queue at all, so a large load connects through a state-approved retail tariff, and the utility filing that tariff or a special contract names the customer and the load. This is the most promising of the four western commissions because it is the only one where a public citation exposes an API-shaped host rather than a search form. Washington shows the same under-observation pattern as Oregon: Grant County carries 39 tracked facilities against 1 recorded opposition event, and Douglas 13 against 1.

This report describes what each URL is. It never resolves a fetchable url: `fetch_permits.py` reads only `arcgis_discovery_*` and `socrata_discovery_*`, so nothing here can start a fetch on its own.

## https://apiproxy.utc.wa.gov/cases/GetDocument?docID=685&year=2021&docketNumber=210755

_The strongest programmatic candidate found in any western commission: a host literally named apiproxy, taking docID/year/docketNumber as query-string parameters. Probed with the exact triple seen in a public citation, so a 200 here means the pattern is real and the question becomes what else /cases/ exposes._

- Family: **csv** — non-HTML text body; check whether it is delimited
- HTTP 200 `application/pdf`
- Keywords present: _none_

Returns tabular data directly. This is the cheapest possible case: the tabular adapter in fetch_permits.py may take it with a config and no new code.

## https://apiproxy.utc.wa.gov/cases/

_The collection root behind that document endpoint. A JSON index or a route listing here would turn Washington from a scrape into an adapter._

- Family: **error** — HTTP 404
- HTTP 404 `application/problem+json; charset=utf-8`
- Keywords present: _none_
- Top-level keys: `type`, `title`, `status`, `traceId`

The host answered with an error status. Note the status: a 403 or 405 often means the path is real but the method or client is wrong.

## https://www.utc.wa.gov/documents-and-proceedings/dockets

_The human dockets list. Probed for whether results are reachable by GET query string, and for a link to whatever the apiproxy host is proxying._

- Family: **html_form** — HTML containing a form, no viewstate
- HTTP 200 `text/html; charset=UTF-8`
- Keywords present: docket, tariff

An HTML search form with no viewstate. Check whether its results are reachable by GET query string; if they are, a tabular or HTML adapter is feasible.

## https://www.utc.wa.gov/casedocket/2022/220242

_A single case page in its canonical /casedocket/<year>/<number> form. A predictable path is worth knowing about even when there is no index: it makes a docket number from any other source directly resolvable._

- Family: **html_form** — HTML containing a form, no viewstate
- HTTP 200 `text/html; charset=UTF-8`
- Keywords present: docket, tariff

An HTML search form with no viewstate. Check whether its results are reachable by GET query string; if they are, a tabular or HTML adapter is feasible.

