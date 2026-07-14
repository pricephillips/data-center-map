# Data Center Opposition Intelligence Platform

A national dataset, pipeline, and set of models tracking community opposition
to data center development across the United States - moratoriums,
legislative actions, zoning fights, and project-level outcomes - built toward
a predictive tool that estimates the expected cost of opposition for a given
project with calibrated, defensible accuracy.

This repository is the national platform. `pricephillips/iowa-dc-tracker` is a
downstream Iowa view, triggered by a repository dispatch when the national
feed rebuilds.

---

## Non-negotiable principles

These constrain every artifact the pipeline produces.

1. **Defensibility above all.** Every external-facing claim is traceable to a
   source and never overstated. Outcomes that cannot be verified are not
   inferred. "Decided cases" means terminal outcomes only (advanced or
   blocked); pending and mixed cases are excluded from decided-case
   statistics.
2. **No scorekeeping vocabulary.** Generated artifacts use activity-descriptive
   outcome terms - `advanced_confirmed`, `restricted_conditional`,
   `blocked_confirmed`, `pending` - never competitive/scorekeeping framing. Every module
   runs a leak audit before writing.
3. **Legislative outcome discipline.** A bill that passed one chamber or
   committee is pending, not enacted. Terminal dispositions outrank
   in-progress milestones.
4. **Statistic reproducibility.** All numeric claims in client-facing
   deliverables are re-derived from the current CSV before publication. Small
   drift between an older snapshot and a newer CSV is expected data growth,
   not a discrepancy.
5. **Models predict observables.** The models estimate probability of block,
   time-to-decision, and condition tier. Dollar figures come from a separate,
   documented cost-translation layer with explicit assumptions. Feature
   importance is predictive, not causal, and is labeled as such.

---

## Architecture

**Data flow.** The pipeline reads `master_opposition.csv` (raw opposition
events) and `data/proposals.csv` (project registry), then writes the linked,
enriched, and analyzed feed back into `data/`. `master_opposition.csv` is the
single source of truth for the map and dashboards and its filename never
changes.

**Unit of analysis.** Opposition is recorded at the *event* level but modeled
at the *project* level. Entity resolution links every event to a project and
constructs a lifecycle timeline (announced -> decided) per project.

**Continuous integration.** GitHub Actions regenerates the feed on every push
that touches `master_opposition.csv` or `data/proposals.csv`. A blocking
self-test gate runs first; a failure stops the build.

### Current scale

| Dataset | Rows |
|---|---|
| Opposition events (`master_opposition.csv`) | ~1,660 |
| Project registry (`data/proposals.csv`) | 331 |
| Confirmed event->project links | 294 |
| Project lifecycles | 331 |
| Projects with a verified decision date | 19 |
| Baseline universe (unopposed comparables) | ~1,840 |
| Matched controls | ~530 |

---

## Predictive modeling roadmap

Work proceeds in phases, in order. Phases 1-2 are the bulk of the difficulty.

| Phase | Scope | Status |
|---|---|---|
| 1 | Project-level entity resolution + lifecycle dates | **Complete, in CI** |
| 2 | Control-group construction (matched unopposed comparables) | **Complete** |
| 3 | Outcome model + time-to-decision (survival) model | **Complete (first iterations)** |
| 4 | Cost-translation layer (observables -> dollar ranges) | **Scaffolded** |
| 5 | Continuous retraining with calibration gating | Not started |

**Phase 3 detail.** The outcome classifier (`outcome_model.py`) is an
L2-regularized logistic regression on decided + opposed projects, validated
with repeated stratified cross-validation. The survival model
(`survival_model.py`) estimates time-to-decision using right-censoring so that
still-pending projects contribute information rather than being discarded.
Both are internal diagnostics, framed with wide intervals and honest
small-sample limitations, and neither is client-facing.

The gating input for further modeling is the count of opposed projects with
**verified decision dates**. The survival model's Cox layer unlocks at ~25
events (currently 17); every terminal decision that resolves moves that
forward. The outcome model, survival model, and cost layer all improve from
the same input.

---

## Repository layout

### Pipeline modules (run in CI)

| Module | Role |
|---|---|
| `clean_opposition_data.py` | Normalizes the raw opposition CSV |
| `enrichment.py` | Adds derived fields to opposition events |
| `build_clean_feed.py` | Produces the cleaned public feed |
| `outcome_defensibility.py` | Enforces outcome-vocabulary and finality rules |
| `legislative_outcome.py` | Legislative stage ladder (chamber/committee vs enacted) |
| `schema_adapter.py` / `qc_pipeline.py` | QC gate: schema validation and logical checks |
| `review_worklists.py` | Generates human-review worklists |
| `group_registry.py` | Canonical opposition-group registry |
| `date_recovery.py` | Flags projects needing decision-date recovery |
| `proximity_analysis.py` | Spatial relationships between projects and events |
| `metrics.py` | Headline metrics for the dashboards |
| `project_resolution.py` | **Entity resolution**: links events to projects, builds lifecycles, applies manual overrides and verified dates, runs the leak audit |
| `control_group.py` | Builds the baseline universe and matched controls |
| `control_comparison.py` | Balance diagnostics + outcome/delay distributions |
| `triage_accelerator.py` | Re-scores review candidates with corroborating evidence (suggestions only; never auto-applied) |

### Models (run manually, outside CI)

These require `scikit-learn`, `lifelines`, and `pandas`, and are intentionally
excluded from CI until Phase 5's calibration gate.

| Module | Output |
|---|---|
| `outcome_model.py` | `data/outcome_model_report.md`, `outcome_model_features.csv`, `outcome_model_metrics.json` |
| `survival_model.py` | `data/survival_model_report.md`, `survival_km_curve.csv`, `survival_model_metrics.json` |
| `cost_translation.py` | `data/cost_anchors.csv`, `cost_translation_methodology.md`, `cost_translation_demo.csv` |

### Key data files

| File | Contents |
|---|---|
| `master_opposition.csv` | Raw opposition events (single source of truth for map/dashboards) |
| `data/proposals.csv` | Project registry; includes optional `outcome_detail` column for conditional approvals |
| `data/project_decision_dates.csv` | Manually verified decision dates - each row carries a source, URL, and note |
| `data/project_links.csv` | Auto-confirmed event->project links |
| `data/project_links_manual.csv` | Human adjudications (confirm/reject with evidence) |
| `data/project_lifecycles.csv` | Per-project timeline, outcome tier, capacity, delay |
| `data/baseline_universe.csv` | Unopposed comparables for matching |
| `data/matched_controls.csv` | Matched opposed/unopposed pairs |
| `county_votes.json` | 2024 presidential county results (political-geography layer) |

`.md` files in `data/` are generated documentation that travels with its
CSVs - the methodology and limitations layer for each dataset. They are not
hand-edited.

### Frontends

`index.html`, `opposition-tracker.html`, `opposition-dashboard.html`,
`developments-dashboard.html`, and `project-lifecycles.html`. All loaders are
fetch-based with visible error banners. `raw.githubusercontent.com` URLs come
first in the fallback chain because GitHub Pages sends no CORS headers, which
would otherwise break embedded iframes in Notion / Simple.ink.

---

## Outcome vocabulary

The lifecycle outcome ladder is activity-descriptive by design.

| Tier | Meaning |
|---|---|
| `advanced_confirmed` | Terminal advance - the project may proceed (unconditioned) |
| `restricted_conditional` | Terminal advance carrying binding conditions (conditional-use approval, negotiated concessions, reverting rezoning) |
| `blocked_confirmed` | Terminal stop - denied or withdrawn |
| `pending` | No terminal disposition yet |

Both advanced tiers count as decided and on the advanced side of any
advanced-versus-blocked split; `restricted_conditional` is tracked separately
because its conditions can carry material cost or delay. A conditional outcome
is flagged explicitly via `outcome_detail` in `proposals.csv`, never inferred
from prose.

---

## Running the pipeline

CI runs automatically on push. To run locally from the repository root:

```bash
# CI chain (regenerates the feed and all data/ outputs)
python3 project_resolution.py
python3 control_group.py
python3 control_comparison.py

# Models (manual; require scikit-learn, lifelines, pandas)
python3 outcome_model.py
python3 survival_model.py
python3 cost_translation.py
```

Pushing `data/proposals.csv` or `master_opposition.csv` triggers the CI
pipeline; no manual dispatch is needed. The Iowa downstream view is notified
by repository dispatch when the feed rebuilds.

---

## Contributing conventions

- **Additive and backward-compatible.** Never break existing functionality or
  rename stable filenames (`master_opposition.csv` especially). New columns
  are appended; absent them, behavior is unchanged.
- **Verified dates only.** A decision date enters `project_decision_dates.csv`
  only with a source and URL. Month-precision announcement dates carry up to
  ~30 days of error and are flagged; year-precision dates are too coarse for a
  delay value and are excluded from the time axis rather than floored.
- **Finality gating.** A voided or appealed approval is corrected immediately;
  litigation watches are maintained for cases that could flip.
- **Suggestions never auto-apply.** Triage output is a draft for human review.
