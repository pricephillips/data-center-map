# Opposed vs. Control — Time to Terminal Decision (first iteration)

Generated 2026-07-24 by `dated_comparison.py`. **Internal diagnostic — NOT client-facing.** Descriptive comparison only; the two arms carry different censoring structures, so no significance test is valid or reported.

## Arms

- **Opposed** (n=161): 26 verified decision events; rest right-censored at last known activity. Standard Kaplan-Meier.
- **Control / unopposed** (n=120): zero verified decision dates exist on the control side. 45 controls are decided but undated — treated as interval-censored (decision occurred between announcement and last status update); the rest are right-censored pending. Nonparametric MLE (Turnbull) estimator.

## Median time to decision

- Opposed: **not reached** (curve does not cross 0.5)
- Control: **780-794 days** (interval-censored NPMLE band)

## Matched-subset comparison

Same estimators, restricted to matched sets (opposed project + its state/capacity/margin-matched controls, both with usable spans): **153** opposed (25 events) vs **358** matched control spans (150 interval-censored).

- Matched opposed median: **not reached**
- Matched control median: **780-795 days** (interval-censored NPMLE band)

Matching narrows the selection gap but the censoring asymmetry between arms remains; treat any difference as descriptive.

## How to read this (binding)

- This compares raw time-to-decision BY OPPOSITION STATUS. It is not opposition-attributable delay: arms are unmatched here, and opposed projects differ systematically from controls (siting, scale, political geography).
- The control arm's information comes almost entirely from interval bounds, which is weak evidence about timing. Every verified control-side decision date (external ingest with source_url) directly sharpens this comparison.
- The opposed arm's events skew blocked (blocked decisions are datable far more often than advances — see the survival model's asymmetry finding), so its curve partly reflects *blocked* timing.
- Next iteration: run this comparison within matched sets (data/matched_controls.csv) once enough matched controls carry usable spans.
