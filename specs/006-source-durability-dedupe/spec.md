# Feature Specification: Source Durability and Event-Level Dedupe

**Feature Branch**: `006-source-durability-dedupe`

**Created**: 2026-09-28

**Status**: Draft

**Input**: Session 3 of the tool integration plan: Internet Archive Save Page Now 2 and CDX APIs, trafilatura, datasketch.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every cited source has an archived snapshot (Priority: P1)

A new `source_archive.py` reads every `Source URL` and `Sources` entry in the clean feed, checks the CDX API for an existing snapshot, requests Save Page Now for any URL without one, and records `url, archived_url, archived_at, http_status, method` in `data/source_archive.csv`. It runs nightly with a capped batch and resumes where it stopped.

**Why this priority**: Principle I requires every external claim to be traceable. Local news links rot, and a client asking for the source a year later gets a 404 today.

**Independent Test**: Run against a fixture of five URLs with a mocked CDX response; confirm three found, two requested, and the CSV written with LF endings.

**Acceptance Scenarios**:

1. **Given** a URL already archived, **When** the job runs, **Then** it records the existing snapshot and makes no save request.
2. **Given** the archive returns rate-limit responses, **When** the job runs, **Then** it backs off, stops the batch, and records the stop point.
3. **Given** a `news.google.com` redirect URL, **When** the job runs, **Then** it skips the URL and marks it `unresolved_redirect`.

### User Story 2 - Harvest candidates carry article text and a date hint (Priority: P2)

`signal_harvest.py` and `untagged_triage.py` call trafilatura on each resolved candidate URL to extract main text and publication date. The date goes to a new `date_hint` column in the worklist, labeled as a hint. It never enters `master_opposition.csv` without review.

**Why this priority**: 369 held rows lack a date. The decision-date and announced-date worklists gate the landmark retrain.

**Independent Test**: Run trafilatura on three saved HTML fixtures and confirm extracted dates match the fixtures' published dates.

### User Story 3 - Syndicated coverage collapses to one candidate event (Priority: P2)

`signal_harvest.py` computes MinHash signatures (datasketch, 128 permutations, 5-word shingles) on title plus lead text and assigns a `cluster_id` to candidates above a Jaccard threshold of 0.7. Reviewers see one row per cluster with the member URLs listed.

**Why this priority**: The July tooling scan noted that the harvester dedupes by URL only, so several articles about one hearing appear as several candidates.

**Independent Test**: Three fixture articles (two syndicated copies, one unrelated) produce two clusters.

### Edge Cases

- Paywalled pages: trafilatura returns little text; the row keeps its headline and is flagged `thin_text`.
- An archive snapshot captures an error page: `http_status` is recorded, and a non-200 snapshot is not treated as archived.
- Cluster threshold too loose: the threshold is a config value, and the selftest pins behavior at 0.7.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `source_archive.py` MUST use only stdlib HTTP, honor a per-run cap from config, and write only `data/source_archive.csv` and its manifest.
- **FR-002**: Archive credentials, if used, MUST come from repository secrets; the job MUST run without them at the anonymous rate.
- **FR-003**: trafilatura and datasketch MUST be pinned per `configs/integrations.json` and installed only in the workflows that run harvest and triage.
- **FR-004**: New columns (`date_hint`, `cluster_id`, `cluster_members`, `archived_url`) MUST be appended; existing columns and order are unchanged.
- **FR-005**: Every new or touched module MUST ship or extend `--selftest` with no network use.
- **FR-006**: Deliverable templates MAY cite `archived_url` alongside the original URL, never in place of it.

## Success Criteria *(mandatory)*

- **SC-001**: Within 30 nightly runs, at least 90 percent of resolvable source URLs have an archived snapshot recorded.
- **SC-002**: The harvest worklist row count drops by the number of syndicated duplicates, with no cluster merging two distinct events in a 50-row manual spot check.
- **SC-003**: Date hints agree with the verified date on at least 80 percent of a 30-row sample from the decision-date worklist.

## Assumptions

- archive.org Save Page Now stays free for this volume; if it restricts access, the CDX lookup half still delivers value.
- No existing module writes an archive column; `layer_audit.py` confirms this during planning.

## Changes on main since this spec (2026-09-29)

- **URL-level re-promotion fixed.** PR #47 made `signal_harvest.known_urls()` read the raw file as well as the clean feed, which stopped exact duplicates being appended each run. The 4,099 already in `master_opposition.csv` remain (spec 005 note).
- **Event-level grouping already exists for one case.** `status_resolution.py` groups same-county pending rows within 120 days and proposes earlier-stage coverage as `supersede`. `hold_superseded()` then keeps them out of the clean feed. The MinHash `cluster_id` in User Story 3 must feed or reuse that path, not build a second supersede mechanism.
- **A Google News consumer now exists.** `status_followup.py` searches Google News RSS for later coverage of pending rows and excludes aggregators. If trafilatura is added (User Story 2), it applies there too, and its install goes in the workflow that runs the follow-up check.
