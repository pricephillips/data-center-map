# Incentive Agreement Registry

`incentive_agreement_registry.csv` is a net-new data-collection effort, not a
script output. Nothing in the repo currently tracks agreement-level
disposition (whether a specific incentive deal was honored, renegotiated, or
clawed back). This file is the header-only skeleton; rows are added by
analyst research, one row per canonical agreement.

## Why this exists

`vantage_scorecard.py`'s Incentive Execution Risk category and
`incentive_durability_proxy.py`'s interim proxy both currently read only the
public legislative record (bill_sync.py's stage ladder). Neither can see
whether a specific incentive agreement was actually delivered on. This
registry is the input a future model needs to close that gap. See
`vantage_ppd_scoring_spec.md` section 1 for the full rationale.

## Columns

| Column | Type | Notes |
|---|---|---|
| `agreement_id` | string | `INC0001` style, zero-padded, sequential |
| `fips` | string | joins to `county_aggregate.csv` |
| `state` | string | two-letter code |
| `jurisdiction_level` | enum | `state`, `county`, `local_edo`, `utility` |
| `program_name` | string | canonicalized, e.g. "Virginia Data Center Sales & Use Tax Exemption" |
| `mechanism` | enum | `sales_tax_exemption`, `property_tax_abatement`, `grant`, `utility_rate_incentive`, `discretionary_local` |
| `enacted_date` | date | when the incentive itself became law or agreement |
| `sunset_or_review_date` | date, nullable | built-in expiration or legislative review trigger, if any |
| `clawback_terms_on_file` | bool | whether clawback language is documented (yes/no, not the terms themselves at this stage) |
| `repeal_or_reform_activity` | enum | `none`, `pending_bill`, `passed_reform`, `repealed` — sourced from `bill_sync.py`'s stage ladder, joined by state |
| `disposition` | enum | `active_undisturbed`, `renegotiated`, `clawed_back`, `defaulted`, `unknown` |
| `source_url` | string | required, same rule as everywhere else in the pipeline |
| `notes` | string | free text |

## Adding a row

1. Find a canonical incentive agreement (not a bill; an actual granted
   agreement) tied to a data center project already in
   `master_opposition_clean.csv` or `county_aggregate.csv`.
2. Assign the next sequential `agreement_id`.
3. Fill every column. `disposition` defaults to `unknown` until there is a
   sourced reason to say otherwise; never guess.
4. Every row requires a `source_url`. No source, no row.

## Realistic sources

State EDO annual incentive reports, the Good Jobs First subsidy tracker,
state comptroller or auditor filings, local news coverage of clawback
disputes.

## Graduation path

Once this file has enough rows, a `disposition`-outcome model can be built
with the same validation stack as `county_policy_model.py` (repeated
stratified k-fold CV, out-of-fold calibrated scores, coefficient
sign-stability gating, Venn-Abers-style interval reporting), predicting
`disposition in {clawed_back, defaulted, renegotiated}` vs.
`active_undisturbed`. Do not build that model on a thin registry; per
`PHASE_STATUS.md`'s existing rule for the cost-translation layer, a model
built on contaminated or sparse inputs violates defensibility.
