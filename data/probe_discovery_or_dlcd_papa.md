# Endpoint probe: or_dlcd_papa

Probed 2026-09-15T12:42:13Z  
**1 of 4 probes landed on a machine-readable route (csv)**

Oregon requires every city and county to file a Post-Acknowledgement Plan Amendment notice with DLCD for any comprehensive plan amendment or zone change, and DLCD must publish proposals and adoptions weekly — precisely the instrument an Oregon data center rezoning uses, captured statewide before the vote. The 2026-09-02 scan called this 'not Socrata, so a small adapter'; that was an assumption, and the first probe here tests it directly. Registered 2026-09-09.

This report describes what each URL is. It never resolves a fetchable url: `fetch_permits.py` reads only `arcgis_discovery_*` and `socrata_discovery_*`, so nothing here can start a fetch on its own.

## https://data.oregon.gov/api/catalog/v1?q=plan%20amendment&limit=25

_data.oregon.gov is a Socrata portal. If PAPA notices are published here the whole source collapses to a config like configs/wa_sepa.json with no new code, so this is probed first._

- Family: **csv** — non-HTML text body; check whether it is delimited
- HTTP 200 `application/json;charset=utf-8`
- Keywords present: plan amendment

Returns tabular data directly. This is the cheapest possible case: the tabular adapter in fetch_permits.py may take it with a config and no new code.

## https://db.lcd.state.or.us/PAPA_Subscription/Default.aspx

_The public reporting tool for proposed and adopted PAPAs. Expected to be ASP.NET WebForms; a viewstate finding here is the evidence that the 'small adapter' in the source scan is the wrong shape._

- Family: **aspnet_postback** — body carries __VIEWSTATE/__EVENTVALIDATION
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: papa, plan amendment, dlcd, land conservation

ASP.NET WebForms driven by __VIEWSTATE postbacks. There is no cheap adapter here: every query is a stateful form round-trip, so treat this as a negative finding and look for a report, subscription or export route instead of scraping the form.

## https://www.oregon.gov/lcd/nn/pages/papa-notices.aspx

_The notices page. Probed for a download link, feed, or report route rather than for the notices themselves._

- Family: **aspnet_postback** — body carries __VIEWSTATE/__EVENTVALIDATION
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: papa, plan amendment, post-acknowledgement, dlcd, land conservation

ASP.NET WebForms driven by __VIEWSTATE postbacks. There is no cheap adapter here: every query is a stateful form round-trip, so treat this as a negative finding and look for a report, subscription or export route instead of scraping the form.

## https://www.oregon.gov/lcd/cpu/pages/proposed-plan-amendments.aspx

_Proposed amendments page, the pre-decision half of the register and the half that matters for opposition timing._

- Family: **aspnet_postback** — body carries __VIEWSTATE/__EVENTVALIDATION
- HTTP 200 `text/html; charset=utf-8`
- Keywords present: papa, plan amendment, post-acknowledgement, dlcd, land conservation

ASP.NET WebForms driven by __VIEWSTATE postbacks. There is no cheap adapter here: every query is a stateful form round-trip, so treat this as a negative finding and look for a report, subscription or export route instead of scraping the form.

