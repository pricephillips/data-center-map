# Implementation Plan: Source Durability and Event-Level Dedupe

**Branch**: `006-source-durability-dedupe` (worked on `claude/blissful-johnson-p8zxsz`) | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/006-source-durability-dedupe/spec.md`, including its "Changes on main since this spec (2026-09-29)" section.

## Summary

Three additions, all producing worklists, hints or reference files. None of them
changes a value in `master_opposition.csv` (Principle VII).

1. **Archive snapshots (US1).** A new module, `source_archive.py`, uses the
   standard library only. It reads every URL in the clean feed's `Source URL`
   and `Sources` columns (5,625 unique URLs today). For each one it:
   - asks the Wayback CDX API for a snapshot with status 200;
   - sends a Save Page Now request when there is none;
   - records the result in `data/source_archive.csv`.

   A Save Page Now request is never polled. The next run confirms it through
   CDX, so the job can resume after any stop.

   Other rules:
   - Caps come from `configs/source_durability.json`.
   - HTTP 429 or 503 triggers the configured backoff. If the error persists,
     the job stops the batch and writes the stop point and reason to
     `data/source_archive_manifest.json`.
   - `news.google.com` URLs are marked `unresolved_redirect`.
   - A snapshot whose status is not 200 does not count as archived.
   - Archive keys come from repository secrets when set. Without them the job
     runs at the anonymous rate (FR-002).
   - A new nightly workflow, `source-archive.yml`, runs the job and commits
     only those two files.
2. **Article text and date hints (US2).** A new module, `article_extract.py`,
   is the only code that imports trafilatura. It fetches a page with
   `urllib`, then extracts:
   - the main text;
   - the publication date, as `date_hint`;
   - a `thin_text` flag.

   Where it runs:
   - `signal_harvest.py` calls it for every opposition candidate, up to a cap.
     `date_hint` and `thin_text` are appended to `data/signal_candidates.csv`.
   - `untagged_triage.py --date-hints` calls it for held rows that have a
     resolved publisher URL. Results are cached in
     `data/untagged_date_hints.csv`, and `date_hint` and `thin_text` are
     appended to `data/untagged_triage.csv`.

   The hint is never copied into `master_opposition.csv`:
   `promote_signal_candidates.build_master_row` does not read it, and a
   selftest asserts this. `article_extract.py --measure` writes
   `data/date_hint_agreement.csv` and `.md` for SC-003.
3. **Event clusters (US3).** A new module, `event_dedupe.py`, is the only code
   that imports datasketch. It builds MinHash signatures (128 permutations,
   5-word shingles) and uses LSH to find pairs with Jaccard similarity of at
   least 0.7. It then groups rows into clusters one row at a time, in a fixed
   order, and applies three guards measured on real data (research D6):
   - title-only matches need at least 6 tokens;
   - cluster members are at most 3 days apart;
   - two rows from the same domain match only when they share a date.

   Two consumers use it:
   - **`signal_harvest.py`**, for tonight's candidates. It collapses each
     cluster to one worklist row and lists the other URLs in `cluster_members`.
     When `promote_signal_candidates.py` promotes that row, it writes one
     `cluster_member` row per copy to `data/signal_promotion_report.csv`.
     `known_urls()` reads those rows, so a copy is not promoted the next
     night.
   - **`status_resolution.py`**, for syndicated duplicates already in master.
     Its existing scan gains a second grouping pass that proposes each copy as
     `supersede` in `data/status_resolution_worklist.csv`, with an appended
     `cluster_id`. A reviewer confirms each one in
     `data/status_resolutions.csv`, and the existing `hold_superseded()` keeps
     confirmed copies out of the clean feed. This is the supersede path the
     "Changes on main" note requires, not a second mechanism.

Workflow changes are committed directly, as the session brief allows:

- `pipeline.yml`
  - Installs trafilatura and datasketch in a new step after the blocking
    selftest gate. The discovery run therefore does not have them, and each
    module's selftest shows a visible `SKIP` line.
  - Runs `untagged_triage.py --date-hints`.
  - Stages the hint cache.
- `update-opposition-csv.yml` (the harvest workflow)
  - Installs both packages.
  - Runs the SC-003 measurement and stages its outputs.
- `source-archive.yml` is new.

Three findings from measuring on real data are written up in research.md:

- **SC-002 is met on the live queue.** `data/signal_candidates.csv` drops from
  82 rows to 48. The 34 removed rows are syndicated copies in 15 clusters, and
  all 49 clustered rows are the same article, checked by hand. Against master,
  the scan proposes 64 syndicated copies in 48 clusters as `supersede`. All
  112 rows involved are the same article under different mastheads, checked
  by hand. No cluster merges two distinct events. (A pre-implementation
  estimate of 98 also counted 51 pending rows that repeat a URL already in
  master. Those are URL repeats, not syndication, and the scan correctly
  skips them.)
- **Title-only MinHash at 0.7 without guards merges distinct items.** Without
  the guards, it merged 8 daily "Capitol Fax" roundups into one cluster,
  "Mid-Day Digest" with "Mid-Ohio Valley Climate Corner", and 7 "Utility Dive"
  index pages. The guards come from those failures. The 0.7 threshold was not
  changed.
- **SC-001 and SC-003 cannot be measured from this sandbox.** The proxy
  refuses archive.org, GDELT, Google News and every publisher host tested. The
  code paths are tested with fixtures and mocks. The manifest records SC-001
  coverage on every run. `--measure` produces the SC-003 number on the first
  CI run of the harvest workflow. The agreement rule (hint within 1 day of the
  verified date) was fixed before any measurement.

## Technical Context

**Language/Version**: Python 3.11 (CI 3.11.16; sandbox 3.11.15).

**Primary Dependencies**:

- New: trafilatura `>=2.2,<3` (Apache-2.0), resolved at 2.2.0, and datasketch
  `>=2.0,<3` (MIT), resolved at 2.0.0, per `configs/integrations.json`.
  trafilatura is imported only in `article_extract.py`, and datasketch only in
  `event_dedupe.py`.
- The Wayback CDX and Save Page Now 2 APIs, called from the standard library
  only (FR-001).
- Existing pins in `requirements/ci.txt` stay unchanged. The compile only adds
  lines.

**Storage**: CSV, JSON and Markdown files in the repo.

**Testing**:

- Every new or touched module gets a `--selftest`, which
  `tests/test_selftests.py` discovers.
- Fixtures live in `tests/fixtures/source_durability/`:
  - three saved HTML pages with known published dates;
  - three dedupe articles;
  - CDX and Save Page Now response bodies.
- No selftest touches the network (FR-005). The HTTP layer is injected as a
  callable.

**Target Platform**: GitHub Actions `ubuntu-latest`, and local runs.

**Project Type**: Script-based data pipeline: root-level modules, CI workflows.

**Performance Goals**:

- Archive run: at most 400 CDX lookups and 150 save requests, with 1 s and 6 s
  spacing. That is about 22 minutes worst case, under a 45-minute job timeout.
- Harvest extraction: at most 150 fetches at a 10 s timeout. It is usually
  under 3 minutes.
- Triage hints: at most 20 fetches at an 8 s timeout inside the 20-minute
  pipeline job. That is 160 s worst case, and 0 today because no held row has
  a resolved URL.
- Clustering 2,364 pending master rows takes under 3 s.

**Constraints**:

- `master_opposition.csv` bytes are unchanged by every new path (Principle VII).
- `date_hint` never enters master without review (US2, Principle VII).
- Supersede proposals are proposals only. Only rows a reviewer writes to
  `data/status_resolutions.csv` take effect.
- The blocking selftest discovery in `pipeline.yml` runs without trafilatura
  or datasketch. Each module's selftest prints `SKIP (<pkg> not installed)`
  for the checks that need the package and exits 0, following
  `qc/schemas.py`.
- At runtime, a missing package means no hints and no clusters. Every output
  keeps its current rows, and the new columns are blank (Principle VII,
  "absent them, behavior is unchanged").
- archive.org is unreachable from the sandbox (proxy 403). The first live run
  is in CI.

**Scale/Scope**:

- Three new modules: `source_archive.py`, `article_extract.py` and
  `event_dedupe.py`.
- Edits to `signal_harvest.py`, `promote_signal_candidates.py`,
  `untagged_triage.py`, `status_resolution.py`, `configs/*`,
  `ARCHITECTURE.md`, `requirements/ci.*`, two workflows, and one new
  workflow.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| 1. `leak_audit.py --tier blocking` = 0 | **PASS (verify at end)** | New CSV text columns are headlines (`title`, which is inherited) and URLs. `data/date_hint_agreement.md` and the manifest carry counts and dates only. No lead text is stored, only a count of text characters. |
| 2. `layer_audit.py` = 0 undeclared | **REQUIRES ACTION** | Declare as Layer E: `data/source_archive.csv` and `data/source_archive_manifest.json` (writer `source_archive.py`), and `data/date_hint_agreement.csv` and `.md` (writer `article_extract.py`). `data/untagged_date_hints.csv` is already covered by Layer C's `data/untagged_*.csv`. `source_archive.csv` goes in `not_regenerable` because it holds resume state. Add ARCHITECTURE.md lines, then run `layer_audit.py --write-gitattributes`. The layer audit reports 0 files with an archive column today, which confirms the spec's assumption. |
| 3. `--selftest` on touched/new modules | **REQUIRES ACTION** | New: `source_archive.py`, `article_extract.py`, `event_dedupe.py`. Extended: `signal_harvest.py`, `promote_signal_candidates.py`, `untagged_triage.py`, `status_resolution.py`. |
| 4. `node --check` on touched JS | **N/A** | No JS touched. |
| 5. No em-dashes, no CRLF | **PASS (verify at end)** | Every writer uses `lineterminator="\n"`. `master_opposition.csv` is not written. |
| 6. docx `--original` validation | **N/A** | No docx. |

Principle checks:

- **I (defensibility).** An archive snapshot makes every cited source durable.
  `archived_url` sits beside the original URL and never replaces it (FR-006).
  A non-200 snapshot does not count as archived. Syndicated copies are one
  source, not several.
- **II.** No generated prose besides counts. The agreement report uses dates
  and percentages only.
- **VI.** SC-001 coverage is re-derived from the CSV on every run.
- **VII.** All new columns are appended. Hints and supersede proposals never
  auto-apply. Promotion is unchanged except that syndicated copies of a
  promoted row are logged and not promoted.
- **VIII.** Every writer is declared. `status_resolution.py` keeps its
  declared C/E crossing, and the scan still writes only Layer E.
- **IX.** Every module ships a selftest, and discovery wires it into the
  blocking step.

**Post-design re-check**: every gate resolves within scope, and there are no
unjustified violations.

**Implementation status (2026-09-29)**: T001-T022 are done. Gates:

- The selftest discovery run passes (89 modules), both with the packages
  installed and with them made unimportable.
- `leak_audit.py --tier blocking` reports 0 hits.
- `layer_audit.py --strict` reports 0 undeclared findings.
- `pre-commit run --all-files` is green.
- actionlint is clean on the three workflows.

SC-002 is met on current data. SC-001 and SC-003 are pending the first CI
runs.

Deferred, with numbers:

- **Coverage-delta gate for `data/source_archive.csv`.**
  `qc/coverage_delta.py` exits 2 on a declared file that does not exist, so
  declaring it before the first archive run would break the pipeline step.
  Add `"data/source_archive.csv": {"url": 0.2, "archived_url": 0.2}` to
  `configs/data_quality.json` once the file is on main.
- **`.gitattributes` entries for the manifest and agreement files.**
  `layer_audit.py --write-gitattributes` lists only files that exist. Rerun it
  after the first CI runs commit them. Items for Price's decision, written up and not acted
on:

1. **Syndicated duplicates in master.** Confirm the 64 `supersede` proposals
   in `data/status_resolution_worklist.csv` (`signal` = `syndicated copy`) by
   copying them to `data/status_resolutions.csv`. Nothing is held out until
   then.
2. **Archive keys.** Add archive.org S3-style keys as `IA_S3_ACCESS_KEY` and
   `IA_S3_SECRET_KEY` repository secrets if the anonymous rate falls short of
   SC-001.
3. **`archived_url` in deliverables.** Citing it in client deliverable
   templates is optional (FR-006, MAY) and left to spec 010.

## Project Structure

### Documentation (this feature)

```text
specs/006-source-durability-dedupe/
├── plan.md              # This file
├── research.md          # Phase 0: decisions D1-D10, measured baselines
├── data-model.md        # Phase 1: archive record, extraction, cluster, new columns
├── quickstart.md        # Phase 1: validation scenarios per user story and SC
├── contracts/
│   ├── cli.md           # command lines, flags, exit codes
│   ├── config.md        # configs/source_durability.json structure
│   └── outputs.md       # file formats and appended columns
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
source_archive.py                   # NEW  CDX lookup + SPN request, resumable, stdlib; --selftest
article_extract.py                  # NEW  fetch + trafilatura text/date/thin_text; --measure; --selftest
event_dedupe.py                     # NEW  MinHash LSH clustering with guards; --selftest
signal_harvest.py                   # EDIT extraction, clustering, appended columns, known_urls reads cluster_member
promote_signal_candidates.py        # EDIT cluster_member report rows; selftest: date_hint never reaches master
untagged_triage.py                  # EDIT --date-hints, cache, appended date_hint/thin_text
status_resolution.py                # EDIT syndicated supersede pass, appended cluster_id/archived_url
configs/source_durability.json      # NEW  caps, backoff, extraction, dedupe, measure settings
configs/layers.json                 # EDIT declare new outputs
configs/integrations.json           # EDIT targets: article_extract.py, event_dedupe.py
ARCHITECTURE.md                     # EDIT writer lines
.gitattributes                      # REGEN via layer_audit.py --write-gitattributes
requirements/ci.in, ci.txt          # EDIT add trafilatura, datasketch
tests/fixtures/source_durability/   # NEW  HTML, dedupe, CDX and SPN fixtures
.github/workflows/source-archive.yml        # NEW  nightly archive job
.github/workflows/pipeline.yml              # EDIT install step, --date-hints, stage cache
.github/workflows/update-opposition-csv.yml # EDIT install, measure, stage outputs
```

**Structure Decision**: Follow the existing flat layout. The two
package-dependent capabilities each get one small module, so each third-party
import lives in exactly one place. That keeps the SKIP logic in one spot and
lets harvest, triage and status resolution share a single tested
implementation.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `article_extract.py`, `event_dedupe.py` and the touched consumers skip package-dependent selftest checks (exit 0, visible `SKIP`) when trafilatura or datasketch is absent | The blocking discovery run in `pipeline.yml` installs neither package. FR-003 limits them to the harvest, triage and follow-up steps. | Installing them in the discovery step would break FR-003. Failing without them would turn the blocking gate red. The same pattern is already accepted for `qc/schemas.py`. |
| Clustering guards (minimum tokens, 3-day window, same-domain rule) beyond the spec's "Jaccard above 0.7" | Without them, title-only MinHash merged distinct recurring items on real data (research D6). That would break SC-002's "no cluster merging two distinct events". | Raising the threshold does not help: the false merges have Jaccard 1.0 (identical titles). |
