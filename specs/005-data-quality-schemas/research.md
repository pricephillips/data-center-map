# Research: Data Quality Schemas and Regression Gates

Phase 0 for [plan.md](plan.md). Every figure below was measured on `main` at
`84cd475` (2026-09-29) unless a commit is named.

## D1. Where the workflow changes go

- **Decision**: All `.github/workflows/` edits go in
  `docs/pending_data_quality.patch`, with `docs/pending_data_quality.md`
  beside it.
- **Rationale**: This is the constitution's rule for CI changes that cannot be
  pushed from a sandbox, and it is spec 004's precedent (research D1 there).
  The patch covers:
  - adding `pandera` to the pipeline install line;
  - three new pipeline steps: schema, coverage delta, label disagreement;
  - their surface-failure steps;
  - staging the new outputs;
  - the `fetch-features.yml` staging for `political*`.
- **Alternatives considered**: Committing the workflows directly. That is
  rejected by precedent, and the session token is not expected to carry the
  workflow scope.

## D2. Pandera placement and pin

- **Decision**: Pin `pandera==0.33.1` in `requirements/ci.in` and `ci.txt`,
  inside the `>=0.33,<0.34` range in `configs/integrations.json`. Import it
  only in `qc/schemas.py`, and narrow the integration `target` list to that
  file (FR-001).
- **Rationale**: 0.33.1 installs and validates cleanly on pandas 3.0.6 in the
  sandbox, using the `pandera.pandas` namespace and lazy validation that
  returns `failure_cases` with row index. The registry currently lists
  `qc/qc_pipeline.py` as a second target, which contradicts FR-001. The gate
  calls the schema module as a script rather than importing Pandera.
- **Alternatives considered**:
  - Hand-written checks without Pandera: rejected, because FR-001 names
    Pandera.
  - Wiring the schema into `qc_pipeline.py`: rejected, because it would make
    the whole gate depend on a new package.

## D3. Report-only window and the single flag (FR-003)

- **Decision**: `configs/data_quality.json` holds `schema.mode`, which takes
  one of three values:
  - `report_only`: never blocks;
  - `window`: the default;
  - `blocking`.

  It also holds `schema.window_clean_runs` (default 7) and
  `schema.switch_date` (null until a person flips the flag by hand). In
  `window` mode, the effective mode is blocking once the last N rows of
  `data/schema_run_history.csv` are all clean. The first blocking run's date
  is written to the history and shown in the report as the switch date.
- **Rationale**: The spec asks for "report-only for seven consecutive clean
  runs, then blocking" without a manual step, and for one flag with the switch
  date recorded. No process writes to `configs/`; they are hand-edited per the
  registry's own rule. So the automatic switch date lives in the run history,
  and the config's `switch_date` records a manual flip.
- **Alternatives considered**:
  - A calendar week: rejected, because a week with one run is not seven clean
    runs.
  - Having the module rewrite the config: rejected, because configs are
    hand-edited only.

## D4. Where the outcome ladder and state set come from (FR-002)

- **Decision**:
  - **Outcome grades.** Add `OUTCOME_GRADES` to `outcome_defensibility.py`,
    the module that assigns `outcome_defensible`. It holds the four README
    tiers plus `blocked_unverified`, `advanced_unverified` and `mixed`. A new
    selftest check asserts that every grade the module can emit is in the
    constant. The schema imports it.
  - **State set.** The keys of `STATE_ABBREV` in `qc/schema_adapter.py`: 50
    states plus DC.
- **Rationale**: The README ladder is prose. The grades the feed actually
  carries are `pending` 795, `blocked_confirmed` 293, `blocked_unverified`
  275, `advanced_confirmed` 247, `mixed` 124, `restricted_conditional` 117 and
  `advanced_unverified` 45. They are defined in that module's docstring, which
  is not importable. One constant in the producer, closed over by its own
  selftest, is the single source.
- **Alternatives considered**:
  - Parsing README.md: rejected as fragile, and it misses the internal
    variants.
  - A list in `configs/`: rejected, because it would duplicate the producer.

## D5. `normalize_state()` contract

- **Decision**: `normalize_state(value, review=None) -> str`.
  - It returns a USPS code, or `""` when the value cannot be placed.
  - It accepts two-letter codes in any case, with trailing periods allowed
    (`va.`), full names, `Commonwealth of`, `State of` and `District of
    Columbia` prefixes, and AP-style abbreviations (`Va.`, `Penn.`, `Calif.`).
  - `US`, `USA`, `United States`, `National`, `Federal` and unknown values
    return `""`. They are appended to `review` as `(value, reason)` when a
    list is passed. It never guesses.
- **Rationale**: `state_bounds.normalize_state` already exists. It handles
  codes and exact full names only, and it serves the geographic box check.
  The adapter's function is the wider front door, and it leaves
  `state_bounds.in_state` untouched. So the prj_61 row (now `prj_78`) still
  normalizes to KY and still fails the box check, as US2 requires, because
  normalization only changes the string and never consults coordinates.
- **Alternatives considered**:
  - The `us` package: rejected in `configs/integrations.json`.
  - Extending `state_bounds.normalize_state`: rejected, because it would
    change a blocking gate's input handling.

## D6. Current failure list on `master_opposition_clean.csv` (SC-001)

Measured on 1,896 rows:

| Check | Rows | Disposition |
|-------|-----:|-------------|
| State blank | 131 | All `data_source = signal_harvest_auto`, `Scope` blank, `is_statewide = False`. The headline backfill could not place them. Declared as an allowed-exception rule (`State == ""` and `data_source == signal_harvest_auto`) and listed in the report. Removing the rule makes them blocking. **Price to confirm.** |
| State `US` | 55 | All `Scope = federal`, `data_source = datacentertracker.org`. These are national records. Declared as an allowed-exception rule (`State == "US"` and `Scope == "federal"`). |
| State full name | 9 | `Washington`, `Texas`, `North Dakota` (2 each); `Delaware`, `South Dakota`, `Nevada` (1 each). Source: `clean_opposition_data.py` step 6b fills blank State from `schema_adapter.extract_state()`, which returns full names. **Fixed at source**: the backfill passes the result through `normalize_state()`. |
| Outcome outside grades | 0 | None. |
| lat/lon outside U.S. or 0,0 | 0 | 321 rows have no coordinates, which is allowed (nullable). No 0,0. |
| `is_*` token outside true/false/1/0 | 0 | All 18 `is_*` columns hold `True`/`False`. |

`data/county_policy_scores.csv`: 3,144 rows, all FIPS five digits and in the
frame, scores in [0, 1], and no retired Connecticut county FIPS (09001-09015).
The frame uses the nine planning regions 09110-09190. Zero failures.

`data/proposals.csv`: 396 rows, 32 columns, ids unique, all 396 resolve to an
active `pk` in `data/project_key_map.csv`. State values are full names, all
normalizable. 2 rows have no coordinates. `capacity_mw` is numeric and
non-negative on 163 rows. Zero failures.

Duplicate rows on `master_opposition.csv`: counted and shown as information
only. There is no uniqueness check, per the "Changes on main" note.

## D7. FIPS on the clean feed

- **Decision**: The FIPS check is declared per schema, where the column
  exists: county scores and political features. It applies to the clean feed
  only if a `fips` column is ever added.
- **Rationale**: The clean feed has 106 columns and none is `fips`.
  `county_aggregator.py` resolves (County, State) to FIPS in memory. Adding a
  column to the feed would be a feed change that this spec does not call for.
- **Alternatives considered**: Resolving FIPS inside the schema. Rejected,
  because it duplicates the aggregator's resolver, and a schema should
  describe data rather than derive it.

## D8. Coverage delta baseline store

- **Decision**: `qc/coverage_delta.py` writes one profile per (file, sha256)
  to `snapshots/coverage_profiles.csv`, with columns `date, sha256, file,
  rows, column, non_null`.
  - **Clean feed.** "The last snapshot" is the most recent row in
    `snapshots/manifest.csv` whose sha differs from the current file and has
    a stored profile. The report names that snapshot's date and sha.
  - **Other declared files** (`data/proposals.csv`). The most recent stored
    profile with a different sha.
  - **Missing profile.** The most recent available one is used, and the
    report says which (spec edge case).
  - **First run.** The current profile is recorded, and the report says there
    is no baseline.
- **Rationale**: The manifest stores date, sha, row count and file name only,
  with no per-column counts. The snapshot CSVs themselves are not kept
  (`snapshots/` holds only the manifest). `snapshots/*` is `not_a_layer`, so
  a second file there needs no layer declaration.
- **Alternatives considered**:
  - Reading history with `git show`: rejected, because CI checkouts are
    shallow.
  - Storing profiles inside `manifest.csv`: rejected, because it would change
    a stable file's format.

## D9. SC-002 replay

- **Finding**: The 2026-09-10 regression is in `data/proposals.csv` at commit
  `e71c8d9`. Its parent version, last changed at `da8f0af` on 2026-09-03, has
  338 rows with 123 non-null `capacity_mw`. `e71c8d9` has 338 rows with 3.
  That is a 97.6 percent drop against a 20 percent threshold.
- **Decision**: The two real profiles are stored as
  `tests/fixtures/coverage_delta/proposals_2026-09-10.json`, extracted with
  `git show`. The selftest replays them and must fail on `capacity_mw`,
  naming 123 and 3. `--before/--after` also accepts two CSVs for a live
  replay (quickstart).

## D10. Robust z-score

- **Decision**: For each value `x`, compute `z = 0.6745 * (x - median) / MAD`
  over the parsed numeric values of the column. Values with `|z| > 6` are
  flagged for review only and never fail the run. Values are parsed with
  commas stripped, and a trailing unit token (`MW`, `acres`) is tolerated.
  When MAD is 0, use the mean absolute deviation times 1.2533. When that is
  also 0, flag nothing.
- **Rationale**: This is the standard Iglewicz-Hoaglin modified z-score. Each
  of the three columns has at least 70 parsed values, enough for a stable
  median.

## D11. Label disagreement rule and the SC-003 finding

- **Decision (as specified)**: The audit reads
  `data/county_policy_scores.csv` (`raw_oof_score`, `calibrated_score`,
  `has_enacted_restrictive`) and applies two rules:
  - **Positive and bottom decile.** The label is 1 and `calibrated_score` is
    at or below the 10th percentile of all counties.
  - **Negative and top 1 percent.** The label is 0 and `calibrated_score` is
    at or above the 99th percentile.

  The ranking key is `|label - calibrated_score|`, descending. Evidence rows
  are the master rows that `county_aggregator.py` counts toward
  `enacted_restrictive`. They are built with the aggregator's own
  `load_frame`, `norm_county`, `norm_state`, `_type_tokens`,
  `RESTRICTIVE_TYPES`, `ENACTED_STATUSES` and
  `DIRECTION_AMBIGUOUS_STATUSES`, imported rather than copied. For positives
  the worklist lists the rows that made the label. For negatives it lists
  none, and says so. When the scores are missing or lack the columns, the
  audit prints a skip message and exits 0.
- **SC-003 replay (measured)**:
  - Pre-fix scores: `git show aaf6cb8:data/county_policy_scores.csv`, the
    last build before 2026-08-21, with 327 positives. Post-fix: `57547fc`,
    with 323 positives. 28 counties went from 1 to 0; that is the "28".
  - Their pre-fix calibrated deciles: 10 (14 counties), 9 (7), 8 (3), 7 (1),
    6 (1), 5 (1), 2 (1). **None is in decile 1**, so the specified rule
    surfaces **0 of 28**.
  - Looser rules do no better than chance. The bottom 33, 65 and 100
    positives by raw score contain 2, 4 and 6 of the 28, against a base rate
    of 28/327 = 8.6 percent. Positives below the positive-class mean score
    number 211, of which 16 are among the 28.
  - Interpretation: each of the 28 was mislabeled because a data center
    project was approved there over recorded opposition. Examples: Santa
    Clara CA, Prince William VA, Ellis TX, Lancaster PA, Racine WI. The
    model's predictors (opposition and project activity) score exactly that
    profile high, so the false labels looked typical to the model, not
    anomalous. The model's own probabilities could not have pointed to this
    defect.
- **Consequence**: SC-003 is **not met**. The module ships, because it is
  still a cheap check for isolated flips, and the synthetic test proves it
  ranks one first. The report and PR state the shortfall. The spec premise
  ("the model's own probabilities could have pointed to") is scoped down per
  Principle I. The rule is **not** retuned to the 28 after the fact.
- **Alternatives considered**: cleanlab's confident-learning thresholds.
  Rejected on license (AGPL), and the per-class-threshold variant measured
  above does no better.

## D12. MEDSL political source

- **Decision**:
  - **Access.** Dataverse native API. `GET
    /api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/VOQCHQ` gives
    the latest version number, license name and URI, terms of use and the
    file list. The file named like `countypres_2000-2024` is chosen, and `GET
    /api/access/datafile/{id}?format=original` downloads the CSV.
  - **Manifest.** `info` records `doi`, `dataset_version`, `file_id`,
    `file_name`, `md5`, `license_name`, `license_uri` and `terms_of_use`, the
    last cut to 800 characters (FR-008).
  - **Parsing.** Years come from config (2016, 2020, 2024). Per county-year,
    use the `TOTAL` mode rows when present, and otherwise sum all modes. `D`
    and `R` are `party` DEMOCRAT and REPUBLICAN. Total votes are the maximum
    `totalvotes` across the county-year's rows.
  - **Recodes.**
    - Kansas City MO (`county_fips 2938000`) is added into Jackson County
      (29095), since the frame has no separate Kansas City.
    - `46113` is recoded to `46102` (Oglala Lakota).
    - `51515` (Bedford city) is added into `51019`.
  - **Geography fallback.** For each state-year: if every frame county in the
    state matched, `geo_basis = county`. If the state is declared in
    `state_fallback` (`AK`, `CT`), every county gets the statewide totals with
    `geo_basis = state`. Otherwise unmatched counties stay blank.
    - Alaska reports by state house district, which does not nest in
      boroughs.
    - Connecticut reports 2016/2020 on the eight retired counties (09001-09015),
      while the frame uses planning regions (09110-09190). If MEDSL 2024 is on
      planning regions, CT 2024 is county basis automatically.
  - **Columns**: `dem_share_2p_{Y}` (D / (D + R)), `margin_dr_{Y}` ((D - R) /
    total, the sign convention of `county_votes.json`), `total_votes_{Y}`,
    `votes_per_pop_{Y}` (total votes / ACS population, a turnout proxy, not a
    share of eligible voters), `geo_basis_{Y}`, the changes
    `dem_share_2p_chg_2016_2020`, `dem_share_2p_chg_2020_2024`,
    `total_votes_pct_chg_2016_2020` and `total_votes_pct_chg_2020_2024`, and
    `source`.
  - **Parity.** `data/features/political_parity.csv` holds, per county and
    year, the MEDSL `margin_dr` against `county_votes.json`, the absolute
    difference, and a flag above 0.02. `political_parity.md` gives per-year
    counts, median and p95 absolute difference, and the 20 largest gaps.
  - **Not registered as a plugin**, so nothing reads it until parity is
    reviewed.
- **Rationale**: This matches the existing source discipline: a failure
  writes nothing and is recorded in the manifest. The sign convention was
  checked against `county_votes.json`: Fairfield CT 2016 is +0.2029, and
  Clinton won it by about 20 points, so the margin is D minus R.
- **Constraint**: The sandbox proxy returns 403 for `dataverse.harvard.edu`,
  so the source is built against the fixture only. The license terms are
  verified at the first CI pull (risk note in the registry). A `license_name`
  other than CC0 is written to the manifest and printed as a warning. It is
  not treated as a failure, because the registry lists the source as
  `CC0-or-verify`.
