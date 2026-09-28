# Daily research routine

A scheduled Claude Code routine runs this procedure every morning after the
nightly pipeline (06:40 UTC). It does the research step that the pipeline
cannot do by itself: confirming outcomes against published sources. It opens
one pull request per run; merging it is the only human step. Every row it
writes carries an evidence URL and a note, so each can be checked from the PR.

The routine starts from `main` of `pricephillips/data-center-map` and pushes
to its own session branch.

## 0. Setup

```
git fetch origin main && git checkout -B <session branch> origin/main
pip install -q pandas "scikit-learn>=1.2" mapie
```

Direct article fetches are often blocked by the session's egress proxy. Use
web search; result snippets from local news, government sites and meeting
minutes are enough when they state the body, the date and the action.

## 1. Pending adoptions (`data/status_resolution_worklist.csv`)

Run `python status_resolution.py` to refresh the worklist. For each row not
already attempted in the last 14 days (see `data/research_routine_log.csv`):

1. Identify the jurisdiction and instrument from the incident, URL and county.
   The harvest geocoder often mis-tags rows (a generic "Iowa county" headline,
   a town tagged to the wrong state), so confirm the real place first.
2. Search for the outcome, including coverage published after the row's date:
   a row that says "considers" or "recommends" may have been adopted later.
3. Write a row to `data/status_resolutions.csv` only when a source states that
   the governing body (or voters) adopted, passed, enacted or extended the
   restriction, with the date:
   - `resolve`: status `passed` (or `extended`), opposition_type one of
     `moratorium`, `ban`, `zoning_restriction`, `ordinance`, authority_level
     one of `county_commission`, `city_council`, `township_board`,
     `village_board`, `planning_commission`, `voters`, correct `state` and
     `county` (with the word County), `evidence_url`, `confirmed_on` today,
     and a `note` naming the body, the date, the tally if stated, and the
     instrument's length and scope.
   - `supersede`: an earlier-stage row (first reading, initial approval) of
     an instrument whose final vote is already on another row. Set
     `superseded_by` to that row's Source URL.
   - `correct_geo`: only the geography was wrong.
   - `source_url` must be the master row's Source URL exactly as stored
     (copy it from `master_opposition.csv`, even when it is malformed).
4. Write nothing when the evidence is only an aggregator or tracker site
   (dmnews, savrn, datacenterbans, interconnectedcapital, gizmowarehouse,
   programs.com), when the only vote was a committee or first reading, or when
   the restriction was rejected, tabled or is still proposed. Log the attempt
   instead.

## 2. County evidence (`data/restriction_verification_worklist.csv`)

Take the first 25 rows with an empty `result`, lowest `priority` first,
skipping fips attempted in the last 14 days. Use `search_hint` and
`mn_legal_basis` to find the adopted instrument in an official record:
minutes, agenda packet, ordinance, resolution, municipal code, or a Legistar
page (`webapi.legistar.com/v1/<client>/matters` works for some counties).

Fill the row only from an official record, never from news:

| column | value |
|---|---|
| evidence_family | `meeting_portal` (minutes, agendas, Legistar), `municipal_code` (code library, adopted ordinance text), `state_aggregator` (state register) |
| source_id | short slug, e.g. `county_site`, `legistar`, `municode` |
| result | `hit` (restriction adopted), `clear` (official record shows none was adopted, or it was rejected), `pending_instrument` (only proposed so far), `unreachable`, `not_covered` |
| evidence_url | the official record |
| observed_at | today |
| in_force_as_of | adoption or effective date, when stated |
| detail | body, date, motion and tally, instrument number |

If the basis record is not an adopted restriction at all (a false-positive
county label), record `clear` with the reason in `detail`.

## 3. Log, validate, publish

1. Append one line per attempt to `data/research_routine_log.csv`
   (`date,queue,key,outcome,note`; queue is `status` or `county`, key is the
   Source URL or fips, outcome is `written` or `not_confirmed`).
2. Validate:
   ```
   python status_resolution.py --selftest
   python restriction_verification_worklist.py --selftest
   python status_resolution.py --apply
   python restriction_verification_worklist.py --offline
   ```
   `--apply` must report 0 unmatched rows. Revert the working copy of
   `master_opposition.csv` and every generated file afterwards; commit only
   `data/status_resolutions.csv`, `data/restriction_verification_worklist.csv`
   and `data/research_routine_log.csv`. The pipeline rebuilds everything else
   on push.
3. No em dashes, LF line endings.
4. Commit with a message summarizing counts written per queue, run
   `git pull --rebase origin main`, push the session branch, and open a pull
   request to `main` titled `Daily research: <date>`. The body lists each row
   written (place, action, evidence URL) and the attempts not confirmed.
5. If nothing was written, do not open a PR; the log waits for the next run
   that writes something.

Stop after the batch. Do not edit code, workflows or any other file.
