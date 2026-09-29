# Research: Source Durability and Event-Level Dedupe

Date: 2026-09-29. All counts are from `main` at `647678c` unless stated.

## D1. Network reachability from the sandbox

- **Finding**: `curl -sS "$HTTPS_PROXY/__agentproxy/status"` shows the proxy
  enabled. CONNECT is refused (403) for `web.archive.org`, `archive.org`,
  `api.gdeltproject.org`, `news.google.com`, `apnews.com` and `wral.com`.
  PyPI is reachable, so trafilatura 2.2.0 and datasketch 2.0.0 install
  locally.
- **Decision**: Every selftest uses fixtures or an injected HTTP callable
  (FR-005). SC-001 and SC-003 are measured by CI runs, and the code that
  measures them is built and tested here. SC-002 needs no network and is
  measured below.
- **Alternatives**: Recording live responses is impossible from here.
  Hand-built fixtures follow the documented response shapes (D2, D3).

## D2. Wayback CDX lookup

- **Decision**: For each URL, call
  `GET https://web.archive.org/cdx/search/cdx?url=<url>&output=json&fl=timestamp,original,statuscode&limit=-10`.
  `limit=-10` returns the 10 most recent captures in ascending order.
  - When a capture has `statuscode` 200, the URL is archived. The newest such
    capture gives `archived_url` = `https://web.archive.org/web/<timestamp>/<original>`
    and `archived_at` = the timestamp as ISO 8601 UTC.
  - When there are captures but none is 200, the newest status is recorded in
    `http_status`. The URL is not archived and a save is requested (spec edge
    case).
  - When there are no captures, a save is requested.
- **Rationale**: One call answers both questions: whether a snapshot exists
  and whether it is good. The JSON form's first row is a header. The parser
  skips it by content, not by position, so an empty body (`[]`) is handled.
- **Alternatives**: `archive.org/wayback/available` returns only the closest
  snapshot, without its status reliably, so it cannot enforce the non-200
  rule. Rejected.

## D3. Save Page Now 2 request, and no polling

- **Decision**: Anonymous requests use
  `GET https://web.archive.org/save/<url>`. With keys, the request is
  `POST https://web.archive.org/save`, body `url=<url>&skip_first_archive=1`,
  and headers `Accept: application/json` and
  `Authorization: LOW <access>:<secret>`. The keys come from the environment
  variables `IA_S3_ACCESS_KEY` and `IA_S3_SECRET_KEY`, which the workflow maps
  from repository secrets (FR-002). A 2xx response means `status=requested`,
  with `requested_on` set.
- The module never polls the job. The next run due for a recheck (after
  `recheck_after_days`, default 1) looks the URL up in CDX again:
  - a new 200 capture sets `method=spn`, `status=archived`;
  - after `max_attempts` requests without a capture (default 3), the URL is
    marked `status=failed` and not retried.
- **Rationale**: Resuming comes from state already in the CSV. A job killed
  mid-batch loses at most the in-memory rows since the last checkpoint, and
  the module checkpoints every 25 URLs. Polling would double the call count
  against the same rate limit.
- **Alternatives**: Polling `/save/status/<job_id>` needs keys and more calls.
  Rejected.

## D4. Rate limits, backoff and stop point

- **Decision**:
  - Wait `cdx_sleep_s` (default 1.0) between CDX calls and `save_sleep_s`
    (default 6.0) between save calls.
  - On HTTP 429 or 503, or on a Save Page Now body that reports a rate or
    daily limit, retry after each delay in `backoff_s` (default [10, 30, 90]).
  - If the error survives the last delay, stop the batch. The manifest records
    `stop_reason=rate_limited` and `stop_at_url`, and the CSV is written with
    everything done so far. The workflow exits 0, because a stop is expected
    behavior, and the next run resumes.
  - Other HTTP or network errors count against the URL (`attempts`) and do not
    stop the batch.
- **Queue order** is deterministic:
  1. `requested` rows due for a recheck;
  2. URLs not yet in the CSV;
  3. `not_archived` rows.

  Within each group, rows are sorted by URL. `max_lookups` (400) caps CDX
  calls and `max_saves` (150) caps save calls.
- **SC-001 arithmetic**: 5,625 URLs, less the Google News ones (0 in the clean
  feed today), over 30 runs is 188 lookups a run. The cap is 400. Saves are
  the binding limit: 150 a run is 4,500 in 30 runs, so SC-001 holds as long
  as at least about 20 percent of URLs already have a CDX capture. Local news
  URLs are usually captured. The manifest reports the live number every run.

## D5. trafilatura extraction and the date hint

- **Decision**: `article_extract.py` fetches pages with `urllib`, then calls
  `trafilatura.bare_extraction(html, url=url, with_metadata=True,
  include_comments=False, date_extraction_params={"original_date": True,
  "extensive_search": False})`.
  - The fetch sets a User-Agent, a timeout, a 2 MB cap, and accepts text/html
    only.
  - `date_hint` is the extracted date when it parses as ISO, falls between
    2010-01-01 and the fetch date, and has day precision. Otherwise it is
    blank.
  - `thin_text` is `yes` when the main text is under `thin_text_chars` (500)
    characters. The row keeps its headline (spec edge case).
  - The first `lead_words` (60) words of the text go to clustering in memory
    only. Text is never written to disk.
- **Rationale**: `original_date=True` prefers the published date over the
  modified date. `extensive_search=False` stops htmldate guessing from
  free-text dates in the body. A body date is often the date of the meeting
  the article previews, which is the wrong date for a publication hint.
- **Alternatives**:
  - Calling htmldate directly skips the main-text extraction that `thin_text`
    and clustering need.
  - Storing lead text in a cache would make clustering stable across nights,
    but it puts third-party article text in the repo and grows without bound.
    Rejected: each night's worklist is rebuilt from tonight's fetches.

## D6. MinHash clustering and the guards (SC-002 baseline)

- **Decision**: `event_dedupe.cluster(items)` works as follows.
  - Each item has an id, title, optional lead, date, domain and state.
  - Normalization:
    - strip a trailing outlet suffix (` - Outlet`, ` | Outlet`, ` – Outlet`)
      only when at least 4 words remain before it;
    - lowercase;
    - split into `[a-z0-9]+` tokens.
  - Shingles are 5-word shingles. A title shorter than 5 tokens is one
    shingle.
  - Each item gets two signatures: title, and title+lead when a lead exists.
    Each signature is a `MinHash(num_perm=128, seed=1)` in its own
    `MinHashLSH(threshold=0.7)`.
  - Matching items i and j:
    - when both have a lead, the title+lead estimate must be at least 0.7;
    - otherwise the title estimate must be at least 0.7, and both titles need
      at least `min_title_tokens` (6) tokens.
  - Compatibility, checked against every member already in the cluster:
    - dates at most `max_days_apart` (3) apart when both are known;
    - same domain only when the date is the same;
    - states equal or one blank.
  - Grouping is greedy in (date, id) order. An item joins the first cluster
    it matches and is compatible with. The result is deterministic, with no
    chaining across the window.
- **Baseline without guards** (title-only, threshold 0.7). On master's 2,172
  `signal_harvest_auto` rows it formed 92 clusters. These include false
  merges:
  - 8 daily "Capitol Fax ... afternoon roundup/morning briefing" posts over
    four weeks;
  - "Mid - Day Digest" with "Mid - Ohio Valley Climate Corner : Root of all
    evil", after suffix stripping left "mid";
  - 7 "Utility and Energy Transmission & Distribution News" index pages;
  - 3 "Latest Articles" pages;
  - 3 "Targeted News Service" pages.

  Every one has Jaccard 1.0 on identical text, so no threshold separates
  them. The guards come from these failures. The threshold stays 0.7, as the
  spec pins.
- **Measured with guards (SC-002)**:
  - `data/signal_candidates.csv` (the live queue): 82 rows form 15 multi-row
    clusters holding 49 rows. The queue drops to 48 rows (-34). Examples:
    - 18 public-radio copies of "The data center backlash is reshaping
      American politics";
    - KCBD and Fox34 on the Lubbock resolution;
    - NBC16, KPIC and KATU on the Salem moratorium.
  - Master, pending rows with a Source URL (2,364, of which 2,313 have
    distinct normalized URLs): the implemented scan proposes 64 syndicated
    copies in 48 clusters. A pre-implementation replay counted 98. The extra
    34 were rows that repeat a URL already in master (the same link with two
    different State tags), which are URL repeats, not syndication.
- **Spot check**: I read all 49 clustered queue rows and all 112 rows in the
  48 master clusters (keepers plus the 64 proposals), which is more than the
  50 rows SC-002 asks for. No cluster merges two distinct events. Every one
  is a single article republished across outlets: newspaper-group mastheads,
  public radio, Telemundo stations, or Yahoo Finance copies. Result: SC-002
  is met on current data. The live queue replay through
  `signal_harvest.enrich_and_cluster` also gives 82 rows reduced to 48.
- **Alternatives**:
  - Union-find over LSH pairs chains A-B-C across the date window. Rejected.
  - Exact-title match misses copies whose outlet suffix differs.
  - A stdlib Jaccard would work at this scale, but the spec and the
    integrations registry name datasketch.

## D7. Where the cluster goes (no second supersede mechanism)

- **Decision**: There are two paths, and each already has an owner.
  1. **Tonight's queue** (`signal_harvest.py`). One row per cluster stays in
     the queue: the highest priority, then the earliest `seen_date`, then the
     URL. Its `cluster_id` is `evt_` plus the first 10 hex characters of the
     SHA-1 of the normalized representative URL, and `cluster_members` lists
     the other URLs, `; `-joined and sorted. Singletons leave both blank.
     When `promote_signal_candidates.py` promotes the representative, it
     appends one `cluster_member` row per member to
     `data/signal_promotion_report.csv`. That file is already its append-only
     audit trail. `signal_harvest.known_urls()` reads those URLs, so a copy
     is known the next night and is not promoted.
  2. **Duplicates already in master** (`status_resolution.py`). A new pass in
     `scan()` clusters pending rows that have a Source URL, using Incident as
     the title and no lead. The keeper is the earliest date, then the first
     in file order. Each other member not already in `status_resolutions.csv`
     and not already proposed becomes a `supersede` proposal:
     - `signal` = `syndicated copy`;
     - `group_key` = `cluster|<id>`;
     - `group_final_url` = the keeper's URL;
     - `cluster_id` appended.

     Confirmation and hold-out are the existing path: `status_resolutions.csv`
     with `action=supersede`, then `hold_superseded()`.
- **Rationale**: This uses the supersede path the "Changes on main" note
  requires. The queue collapse needs no master change, because copies never
  reach master. It also stops copies of promoted events arriving the next
  night.
- **Alternatives**:
  - Writing members into the promoted row's `Sources` would change what
    auto-promotion writes to a client-facing file. That needs Price's
    approval, so it was not done.
  - Clustering tonight's candidates against master titles would duplicate
    path 2.

## D8. status_followup.py and trafilatura

- **Finding**: Follow-up leads carry Google News RSS links
  (`news.google.com/rss/articles/...`) and an RSS `pubDate`. The links need a
  live redirect resolution before any page can be fetched, and the date is
  already in the feed.
- **Decision**: trafilatura adds nothing there today. The follow-up check
  runs in the same `pipeline.yml` job as triage, so the one install step
  covers it if a later change resolves the links.
- **Possible follow-up**: count corroborating outlets by event cluster. Two
  syndicated copies are one source, not two. This is out of scope for this
  spec.

## D9. Triage date hints

- **Finding**: `data/untagged_triage.csv` has 6,106 rows. None has a
  `resolved_url`: `data/untagged_resolved.csv` does not exist, because CI
  never runs `--resolve`.
- **Decision**:
  - `--date-hints` fetches only rows with a `resolved_url` that are not
    already in `data/untagged_date_hints.csv`, up to `--hint-limit` (20).
  - The cache is append-only and keyed by `row_key`.
  - A plain run fills `date_hint` from the cache without any network call.
  - The pipeline passes `--date-hints`. Today it makes 0 fetches, and hints
    appear as rows get resolved.
- **Consequence**: US2's triage half starts delivering only once redirect
  resolution runs. That work is spec 002's, and it is noted, not widened into
  this spec.

## D10. SC-003 sample definition, fixed before measuring

- **Finding**: `data/decision_date_worklist.csv` lists the 48 projects that
  lack a decision date, so it has nothing to compare a hint against. The
  verified dates, each with a `source_url`, are in
  `data/project_decision_dates.csv` (31 rows).
- **Decision**:
  - The sample is the first 30 rows of `project_decision_dates.csv` sorted by
    `project_id`. The hint is extracted from each row's `source_url`.
  - A hint agrees when it is within 1 day of `decision_date`. Coverage
    reporting normally runs the same day or the next.
  - The report also shows exact-day agreement, 3-day agreement, and the count
    with no hint.
  - SC-003 passes when agreeing hints number at least 80 percent of the rows
    that have a hint and at least 24 of the 30 rows.
  - The rule is written here before any number exists, so it cannot be tuned
    to the result.
- **Where it runs**: `article_extract.py --measure` runs in
  `update-opposition-csv.yml`. Fetched rows are cached in
  `data/date_hint_agreement.csv`, so it reaches the network once per URL.

## Dependency pins

`uv pip compile requirements/ci.in --python-version 3.11 -o requirements/ci.txt`
adds trafilatura 2.2.0 and datasketch 2.0.0 with their dependencies:

- courlan, htmldate, justext, lxml, lxml_html_clean, dateparser, babel, tld,
  regex, tzlocal, charset-normalizer;
- scipy is already present.

Every existing pin is unchanged. The implementation step checks the diff.
