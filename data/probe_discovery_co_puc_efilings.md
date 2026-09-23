# Endpoint probe: co_puc_efilings

Probed 2026-09-22T12:38:11Z  
**1 of 4 probes landed on a machine-readable route (csv)**

Colorado PUC E-Filings, searchable from 1994 forward. Registered 2026-09-09 for item 7. Colorado matters for this project beyond its size: ten Colorado counties in data/county_aggregate.csv carry at least one recorded opposition event, and eight of the ten carry exactly one -- Denver at 3 and Logan at 2 are the only exceptions. A docket route here would deepen counties already visible at the thinnest possible margin rather than only adding new ones.

This report describes what each URL is. It never resolves a fetchable url: `fetch_permits.py` reads only `arcgis_discovery_*` and `socrata_discovery_*`, so nothing here can start a fetch on its own.

## https://www.dora.state.co.us/PUC/DocketsDecisions/DocketsDataArchive.htm

_Titled 'Index of Major Docket Activity and Index of Dockets and Decisions Tracking Data'. The words 'tracking data' and 'archive' are why this is probed first: a published index file would beat querying the filings system row by row._

- Family: **html_form** — HTML containing a form, no viewstate
- HTTP 200 `text/html; charset=UTF-8`
- Keywords present: proceeding, docket, public utilities commission

An HTML search form with no viewstate. Check whether its results are reachable by GET query string; if they are, a tabular or HTML adapter is feasible.

## https://www.dora.state.co.us/pls/efi/efi.show_document?p_dms_document_id=1027836&p_session_id=

_The E-Filings document route. The /pls/ path is the Oracle PL/SQL Web Toolkit, which means plain GET parameters and no viewstate — an unusually tractable shape for a filings system. Probed with a document id from a public citation._

- Family: **csv** — non-HTML text body; check whether it is delimited
- HTTP 200 `application/pdf`
- Keywords present: _none_

Returns tabular data directly. This is the cheapest possible case: the tabular adapter in fetch_permits.py may take it with a config and no new code.

## https://www.dora.state.co.us/pls/efi/EFI.Show_Filing?p_fil=G_828577&p_session_id=

_The filing-level route from the same toolkit. Confirms whether the empty p_session_id is accepted, which decides whether anything here is reachable without establishing a session first._

- Family: **unreachable** — TimeoutError: The read operation timed out
- HTTP 0 `no content-type`
- Keywords present: _none_

No response. Could be the network this ran on rather than the source; re-run in CI before concluding anything.

## https://puc.colorado.gov/puc-decisions

_The modern decisions page, in case the current route has moved off the dora.state.co.us host._

- Family: **error** — HTTP 403
- HTTP 403 `text/html`
- Keywords present: _none_

The host answered with an error status. Note the status: a 403 or 405 often means the path is real but the method or client is wrong.

