# Pending: wire gated census promotion into CI

`docs/pending_ci_wiring.patch` is a two-file change to `.github/workflows/`
that is **written, tested and verified to apply cleanly**, and deliberately not
committed. This file explains what it does, why it is sitting here, and what to
check after applying it.

It is stored in the repository rather than left in a chat transcript because a
change nobody can find is a change that does not exist. Applying it is one
command.

## Why it is not applied

The agent sessions that wrote it are blocked from committing anything under
`.github/workflows/` by a harness permission control, across repeated attempts
and on a fresh branch. That control is not a bug to work around: this patch is
precisely the change that makes a third-party dataset reach county labels on a
schedule with nobody reading it, and a person authorizing that consciously is
the correct shape.

The same change could have been pushed through a different transport. It was
not, because that would bypass the intent of the control rather than satisfy
it.

## What it does

Three things, and the third is the one worth reading twice.

1. **Adds a `--promote` step** to `.github/workflows/refresh-external-census.yml`,
   after the existing `--refresh`. `refresh_external_census.py --promote`
   appends gate-passing upstream rows to `data/external_restriction_census.csv`
   and writes every promote and hold decision to
   `data/census_promotion_report.csv`.

2. **Raises the cadence from monthly to weekly.** A monthly cadence matched how
   often a person would sit down with the delta. Now that promotion is
   automatic, the cadence should match how fast the instruments move, and
   county moratoria are adopted and extended on a timescale of weeks.

3. **Drops `[skip ci]` from that workflow's commit, and adds the census to
   `pipeline.yml`'s trigger paths.** Together these mean a promotion rebuilds
   the feed immediately instead of waiting for the nightly run: the coverage
   audit sees the new counties, `census_gap_candidates.py --promote` turns the
   gate-passing ones into tracker records, and `county_aggregator.py` labels
   them.

   This cannot loop. `pipeline.yml` does not write the census, and it commits
   its own artifacts with `[skip ci]`.

## What changes about the data, stated plainly

Before: `refresh_external_census.py` could not write the census, and promoting
a row into it was a review decision.

After: the module is the census's sole writer, appending only, on a weekly
schedule. **A third-party dataset reaches county restriction labels with no
human in the loop.**

What does *not* change is the path from a census row to a label. A promoted row
carries its upstream citation and still has to pass `census_gap_candidates.py`'s
own gate (complete, dated, http source URL, dedup-guarded, still an open
coverage gap) before it becomes a tracker record. This patch widens what the
census covers; it does not shorten that path.

`ARCHITECTURE.md` already records the posture change under "The census
maintains itself", so the documentation is correct either way. Only the
schedule is missing.

## The gates this turns loose

Two of them, answering different questions. Added 2026-09-16 in response to
exactly the concern this file records: an unattended promotion is only as
trustworthy as what notices when it goes wrong.

### Per run: `batch_qc()`, a circuit breaker

Per-row validation passing says nothing about the shape of a run. If upstream
ships four hundred rows in a week, or its row count collapses because a fetch
truncated, or every row for one state changes at once, each row passes and the
batch is still wrong. So the batch gate refuses the whole append rather than
writing a bad batch one good-looking row at a time:

| Check | Trips when |
|---|---|
| volume | promotions exceed 4x the trailing median (needs 3 runs of history first) |
| upstream shrinkage | upstream returns under 60 pct of the rows it did last run |
| hold-rate collapse | the share of rows held falls far below its trailing norm |
| state concentration | over 70 pct of promotions are one state |
| thin volume | over 150 thin promotions in one run |

**Hold-rate collapse is the one worth understanding.** The per-row gate leans
on upstream's own uncertainty markers, so if upstream stops populating
`has_verify_tags` the gate quietly stops holding anything and promotes
everything. On the report that reads as a sudden quality improvement. It is the
opposite: the brake came off. Nothing else in the module would notice, which is
why it is checked explicitly.

Thresholds are deliberately loose. This is a circuit breaker for a run that has
gone wrong, not a quality score. A gate that trips on ordinary weeks gets
disabled, and a disabled gate protects nothing.

A batch hold appends nothing and exits nonzero, but still writes every decision
to `data/census_promotion_report.csv`, so a refused run is reviewable rather
than invisible. `--force` overrides it when a batch is legitimately large.

### Per promotion: `confidence_tier()`, the flag on what could not be fully verified

Every promotion is tiered in the report:

- **corroborated** - the tracker already holds a restrictive record for this
  county, so upstream is a second independent reading rather than the only one
- **single_source** - upstream is the only assertion, but promoting it does not
  move the county's label
- **thin** - upstream is the only assertion, promoting it flips the label from
  0 to 1, and there is no primary-source URL behind it

On the 2026-09 upstream that is **33 corroborated and 35 thin**, and the 35
thin ones are exactly the promotions that would flip a county label. They are
the rows to read if you read any.

`--hold-thin` refuses them outright instead of flagging them, which is the
stricter posture available without editing code.

### Per row: `gate_row()`, unchanged

Holds on upstream's own uncertainty markers rather than second-guessing them,
plus three structural checks:

| Hold reason | Meaning |
|---|---|
| `upstream verify tag` | `has_verify_tags` is upstream saying it has not confirmed the row |
| `date unverified` | `date_enacted_uncertainty` is upstream saying it could not pin the date |
| `pending, not enacted` | an instrument sought and not adopted, which cannot corroborate an enacted label |
| `no ISO date` | a dateless row would enter the census and then be held out of the clean feed as incomplete |
| `does not join county frame` | a name defect, not a coverage result |
| `already in census` | dedup, keyed on the county |

Measured against live upstream on 2026-09-15: **68 promotions across 25 states,
50 held** (36 of them on upstream's own verify tag). 35 of the promotions would
flip a county label from 0 to 1.

## Applying it

```sh
git apply docs/pending_ci_wiring.patch
git add .github/workflows/
git commit -m "Wire gated census promotion into CI"
```

Verified to apply cleanly against `b0671d3`. If main has moved far enough that
it no longer does, the three changes above are small enough to make by hand;
nothing in it is subtle.

## What to check on the first run

1. The `Refresh external restriction census` workflow goes green. It had never
   once been green before 2026-09-09 (it invoked a `--merge` flag the module
   has never accepted), so a green run is itself new.
2. `data/census_promotion_report.csv` gains rows, and the hold reasons look
   like the table above rather than being dominated by one unexpected reason.
   Sort by `confidence_tier` and read the `thin` rows: those are the
   promotions that moved a county label on one uncorroborated source.
3. `batch_verdict` on the run reads `batch_ok`. If it reads `batch_held`,
   nothing was appended and the reason is printed in the job log; the run is
   still fully recorded in the report.
3. The census commit triggers `Build Clean Feed` rather than sitting until the
   nightly run. That is the point of dropping `[skip ci]`.
4. `data/restriction_evidence_conflicts.csv` should shrink on the
   `label_negative_upstream_hit` count as promoted rows become reviewed census
   rows.

## Backing it out

Revert the commit. The census is append-only, so rows already promoted stay;
remove them by hand from `data/external_restriction_census.csv` if that is
wanted, using `data/census_promotion_report.csv` to identify which rows came
from which run.
