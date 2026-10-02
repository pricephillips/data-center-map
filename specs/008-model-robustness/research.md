# Research: Model Robustness and Governance

All measurements were taken in the session sandbox on 2026-10-01, on
CPython 3.11.15, against the data on `main` at `8e661e7`.

## Baselines measured

- **Outcome model**: 80 decided and opposed projects, 30 blocked, 20
  features. CV AUC median 0.85 (p10 0.75, p90 0.97). The last three gate
  runs gave PROMOTE, with ECE 0.059 to 0.076.
- **Landmark model**: GATE CLOSED. No window clears the feasibility floors,
  so no model is fit and `landmark_model_metrics.json` does not exist.
- **County model**: 3,124 counties scored in `county_policy_scores.csv`.
  - Moran's I on `has_enacted_restrictive - calibrated_score`, with
    row-standardized contiguity weights from `county_adjacency.csv`
    (17,924 rows, symmetric, 20 counties without a scored neighbour):
    I = 0.118, E[I] = -0.0003, pseudo p = 0.001 (999 permutations),
    z = 10.6.
  - So the residuals cluster spatially, and a state-grouped AUC below the
    standard AUC is expected.
- **Feature search, `restrict_profile`**: 3,144 counties, 42 candidates after
  the leakage exclusions.
  - An EBM with `interactions=0` and `outer_bags=8` fits in about 2.6 s per
    fold.
  - A single 5-fold pass gave median held-out AUC 0.80.

## D1. Stay on Python 3.11

**Decision**: every workflow stays on 3.11. The session 5 packages are pinned
at releases that support 3.11, and they are the current releases.

**Evidence**: `uv pip install --system -c requirements/ci.txt firthmodels esda
libpysal interpret-core skops` on 3.11.15 resolved esda 2.9.0, libpysal
4.14.1, firthmodels 0.8.2, interpret-core 0.7.8 and skops 0.16.0. No
existing pin moved. esda 2.9.0 declares `Requires-Python >=3.11`, so the
registry's note ("current releases need Python 3.12") no longer holds.

**Consequences**:

- `requirements/ci.txt` keeps serving one interpreter, so it does not need
  to be split.
- The Dependabot ignores on numpy >=2.5 and scipy >=1.18 stay. They come off
  when the repo moves to 3.12 as a whole, which is its own change with its
  own constraints recompile. This spec does not need it.

**Rejected**:

- Moving only the county job to 3.12. That would make one constraints file
  serve two interpreters, which the spec's "Changes on main" note rules out.
- Moving everything to 3.12 now. It would widen this spec into a repo-wide
  migration with nothing in session 5 requiring it.

## D2. How Firth enters "the registered specification set" (FR-001)

**Decision**: Firth is scored as a challenger next to the registered
estimator, on identical splits. The registered rule stays as written, so
Firth is reported and never selected.

**Why**:

- `county_policy_model.py` is the only module with a variable-specification
  grid.
- `outcome_model.py` registers a single estimator (L2 logistic, C = 0.5,
  balanced).
- `landmark_model.py` registers a rule over windows: floors, then AUC, then
  the shortest tied window. Its estimator is fixed.
- Neither rule has an estimator dimension. Adding Firth as something the
  rule can pick would mean editing the rule, which FR-001 forbids.

**What this delivers**:

- Firth appears in a selection table, with AUC, Brier and coefficient size
  for every CV repeat (SC-001).
- It uses the same `RepeatedStratifiedKFold` seed and the same sign screen:
  a coefficient is sign-stable when its sign holds across every fold (the
  same test as `county_policy_model.is_sign_stable`).
- Production is unchanged (Acceptance 2).
- The report states that adopting Firth needs a new registration entry, dated
  in the module docstring, as the landmark window rule requires for any
  change.

**Estimator settings**:

- `FirthLogisticRegression(max_iter=100)` behind the same imputer and scaler.
  The default `max_iter=25` raised a ConvergenceWarning on the separable
  fixture.
- Identified columns only. On the real outcome frame the first run failed
  with "Weighted design matrix is rank deficient": in some training folds a
  rare indicator is constant, and some columns alias others. The wrapper
  `FirthOnIdentified` picks a linearly independent column subset per fold
  (pivoted QR) and fits the aliased columns at zero. That keeps `coef_`
  aligned with the caller's columns, which is the same contract
  `keep_empty_features=True` keeps for the registered pipeline.
- No class weighting. Firth's penalty is the small-sample remedy; reweighting
  would shift its intercept and defeat the comparison of calibrated
  probabilities.

**Shared module**: the Firth pipeline and the comparison live in
`estimator_candidates.py`, so both models run the same code and one selftest
covers it.

**Separable fixture (Independent Test)**: on 30 points separated by
`x0 > 0`, unpenalized `LogisticRegression` returned a coefficient of about
102. Firth returned about 7.1, which is finite and bounded.

## D3. Calibration metrics built in-house, netcal eliminated

**Decision**: compute ECE, MCE, equal-mass ECE and MCE, and reliability bins
in `calibration_gate.py`. Change the netcal registry entry to `eliminated`,
category `cost-infra`.

**Evidence**:

- `from netcal.metrics import ECE` imports `netcal.AbstractCalibration`, which
  imports torch at module load. So even the metrics need torch.
- A dry-run install of netcal 1.4.0 adds 57 packages, including torch 2.14,
  pyro-ppl, gpytorch and tensorboard.
- The gate already computes equal-width ECE. MCE and the equal-mass variants
  add about 40 lines, well under docs/tool_selection.md rule 6
  ("Make versus take": under about 150 lines and removes a dependency, so
  build it).

**Equivalence**: read from netcal 1.4.0's source (`Miscalibration._prepare_input`
and `binning`). For binary labels, netcal compares the positive-class
probability with the observed frequency in equal-width bins, skips empty
bins, and weights by bin count. MCE is the largest bin gap. Those are the
definitions implemented here. A numeric cross-check was not possible: the
sandbox proxy blocks download.pytorch.org, and netcal will not import
without torch. The selftest pins the definitions against hand-computed
values instead.

**Columns added** (FR-002, appended after `reason`): `mce`, `ece_equal_mass`,
`mce_equal_mass` and `reliability_bins`. `reliability_bins` is compact JSON:
a list of `[lo, hi, n, mean_pred, observed]`. The existing `ece` column keeps
its definition: 5 equal-width bins, used by the gate.

**Proposed threshold** (report only, for Price to adopt): MCE <= 0.30. With
n of about 80 in 5 bins, one sparse bin can carry a large gap, so a tighter
ceiling would hold on noise.

## D4. County calibration is a report section, not a gate row

**Decision**: the gate report gains "Production county model (report only)",
with ECE, MCE and a reliability table computed from
`county_policy_scores.csv`. It uses `calibrated_score`, which is cross-fitted,
so no county is scored by a recalibration fit on itself.

**Why not a history row**: `operations_summary.py` takes `last_verdict`
across all history rows, and `county-profile.html` loads the same file. A
county row would change what both surfaces report as the latest gate verdict
without anyone deciding that. SC-002 asks only that the report show these
figures.

## D5. Spatial diagnostics

**Moran's I**:

- Residual = label minus `calibrated_score`, the score that ships.
- Weights: `libpysal.weights.W` from `county_adjacency.csv`, restricted to
  scored counties, with self-pairs dropped, row-standardized. Islands are
  reported, not imputed.
- `esda.Moran(permutations=999)`, seeded through `np.random.seed(SEED)`
  before the call. esda uses the global generator.
- Reported: I, E[I], pseudo p, z (simulated), number of islands.

**State-grouped CV**:

- `StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED + r)` for
  r in `range(N_REPEATS)`, with groups set to the state FIPS (first two
  digits).
- Run on the selected variables and C, with the same pipeline.
- Reported: median AUC with p10 and p90, beside the standard figures. The gap
  is reported as `state_grouped_gap`.
- Never used for selection, and never a veto.

**Synthetic check (Independent Test)**: on a 20 x 20 rook grid, values
planted high on the left half give significant I; shuffled values do not.

## D6. EBM challenger

- Library: `interpret-core`, which holds the glassbox models without the
  dashboard stack. It imports as `interpret.glassbox`.
- Settings in `configs/feature_search.json` under a new `ebm` block, so a
  change is recorded in `config_hash`: `target` set to `restrict_profile`,
  `interactions: 0` (main effects only, so every shape is one readable
  curve), `outer_bags: 8`, `max_bins: 32`, and the seed from `cv.seed`.
- Why 32 bins: at the default of 1,024, adjacent bins carry noise. On the
  monotone fixture, steps fell by up to 0.88 log-odds between neighbours.
  5-fold AUC on `restrict_profile` was 0.787 at 16 bins, 0.798 at 32 and
  0.799 at 1,024. So 32 bins keeps the accuracy, and gives a curve a client
  chart can draw.
- Adding the block changes `config_hash`, so the first weekly run after
  merge counts as changed inputs and updates the filtered weights once.
  That is the registered behaviour for a config change.
- Held-out AUC uses the target's existing `RepeatedStratifiedKFold` splits.
- Shapes come from one refit on the full frame through
  `explain_global().data(i)`. Each variable stores its bin edges or
  categories, scores, lower and upper bounds, and importance.
- The shapes file carries `"role": "challenger; never read by
  choose_promotions()"`.
- The "inputs unchanged" early exit also requires the shapes file to exist,
  but only when interpret is importable. Otherwise a run without interpret
  would never be able to skip.
- **Monotone check (Independent Test)**: with a logistic target in one
  uniform variable and two noise variables (n = 4,000), run on the
  production `ebm` settings, the fitted shape's scores are non-decreasing
  across bins within a 0.05 tolerance. That is under 1 percent of the
  shape's range of about 7. Their Spearman correlation with the bin
  midpoints is above 0.95. Across 8 seeds, the largest step down was 0.044.

## D7. Model cards

- `skops.card.Card(model=None, template=None)`, with sections added by
  `card.add(...)` and saved as Markdown. No model object is passed, so
  nothing is serialized, and the spec's "skops format, not pickle"
  assumption holds trivially.
- Path: `models/cards/outcome_model_<YYYY-MM-DD>.md`. A second promotion on
  the same day overwrites that day's card.
- Inputs:
  - from `data/outcome_model_metrics.json`: n, positives, CV AUC and Brier
    p10/p50/p90, feature list, generated date;
  - the gate's own record;
  - the registered estimator line.
- No row-level or group-level field is read (Principle VI).
- Language: descriptive. `models/cards/*.md` joins the Vale scope in
  `.vale.ini` (FR-004).

## D8. Where each package is installed

| Workflow | Adds | Why |
|----------|------|-----|
| `pipeline.yml` (selftest step) | firthmodels, esda, libpysal, interpret-core, skops | Selftests run for real in CI; the county diagnostics run in the same job. |
| `retrain.yml` | firthmodels, skops | Outcome-model challenger; cards on PROMOTE. |
| `feature-search.yml` | interpret-core | EBM challenger, weekly. |
