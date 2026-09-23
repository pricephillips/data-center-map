# Endpoint probe: or_puc_edockets

Probed 2026-09-22T12:38:16Z  
**1 of 4 probes landed on a machine-readable route (csv)**

Oregon PUC eDockets. Registered 2026-09-09 for item 7. Two reasons Oregon is the strongest case of the four. configs/or_dlcd_papa.json covers the land-use half of the same decisions, so the two sources corroborate rather than duplicate. And Oregon holds the clearest under-observation signal in the aggregate: Morrow County carries 28 tracked facilities against 1 recorded opposition event, Umatilla 31 against 1. A county hosting thirty data centers and generating one recorded event is far more likely to be under-observed than quiet, and a tariff docket naming the customer is exactly the instrument that would show it.

This report describes what each URL is. It never resolves a fetchable url: `fetch_permits.py` reads only `arcgis_discovery_*` and `socrata_discovery_*`, so nothing here can start a fetch on its own.

## https://apps.puc.state.or.us/edocketsSearch/

_The current search application, which supersedes the classic eDockets front end. Probed first because whatever shape this has is the shape any Oregon adapter has to target._

- Family: **html_form** — HTML containing a form, no viewstate
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: docket, tariff, advice, public utility commission

An HTML search form with no viewstate. Check whether its results are reachable by GET query string; if they are, a tabular or HTML adapter is feasible.

## https://apps.puc.state.or.us/edockets/edocs.asp?FileType=HNA&FileName=CPNotice1028.txt&DocketID=17285&numSequence=2

_The classic document route in its full parameterised form, taken from a public citation. Classic ASP with plain GET parameters is tractable; the question this settles is whether the route still answers now that the search front end has moved._

- Family: **csv** — non-HTML text body; check whether it is delimited
- HTTP 200 `text/plain`
- Redirected to `https://edocs.puc.state.or.us/efdocs/HNA/CPNotice1028.txt`
- Keywords present: public utility commission

Returns tabular data directly. This is the cheapest possible case: the tabular adapter in fetch_permits.py may take it with a config and no new code.

## https://www.oregon.gov/puc/utilities/pages/edockets-ediscovery-replacement-project.aspx

_Oregon is actively replacing eDockets and eDiscovery. Probed deliberately: building an adapter against a system with a published replacement project is how you get a working adapter that stops working, and the replacement's shape is the thing worth knowing before spending the effort._

- Family: **aspnet_postback** — body carries __VIEWSTATE/__EVENTVALIDATION
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: docket, public utility commission

ASP.NET WebForms driven by __VIEWSTATE postbacks. There is no cheap adapter here: every query is a stateful form round-trip, so treat this as a negative finding and look for a report, subscription or export route instead of scraping the form.

## https://www.oregon.gov/puc/filing-center/pages/key-cases.aspx

_The commission's own curated list of key cases. A short, human-maintained list of what matters is sometimes a better starting point than a complete index nobody can filter._

- Family: **aspnet_postback** — body carries __VIEWSTATE/__EVENTVALIDATION
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: docket, public utility commission

ASP.NET WebForms driven by __VIEWSTATE postbacks. There is no cheap adapter here: every query is a stateful form round-trip, so treat this as a negative finding and look for a report, subscription or export route instead of scraping the form.

