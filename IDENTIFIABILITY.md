# Identifiability Findings: Causal Opposition-Attributable Delay and Cost

Status: settled negative result. Recorded 2026-07-21. Do not re-litigate without new data types, not merely more rows of the same data.

## Conclusion

The causal quantity "delay or cost attributable to opposition, versus the counterfactual of no opposition" is not identifiable from this platform's data. The gated module `attributable_delay.py` remains WITHHELD as its correct terminal state. The platform's defensible products are listed at the end of this document.

## The five findings

Each finding is independently sufficient to block causal attribution. All were confirmed empirically on the live dataset.

1. Selection artifact in block rates. The crude opposed-versus-control block-rate gap (about +16 percentage points) collapses to approximately zero after matching. Opposition arises disproportionately at projects that were already contested for other reasons. The tracker is a selection-biased sample by construction.

2. Timing runs the wrong way. Blocked decisions arrive faster than advances. The Cox model discriminates at chance. This reflects a competing-risks structure: a denial is a single discrete event, while an advance is the slow absence of denial. Time-to-decision does not measure opposition-imposed delay.

3. Structural datable-outcome asymmetry. Blocked projects generate decision dates (a vote, a denial letter) at far higher rates than advanced projects, which often advance without any datable terminal event. This is a property of how land-use processes produce records, on both arms, and cannot be fixed by more collection effort.

4. Heterogeneous control clocks. Control-side decision dates are different milestone types (air permit, zoning vote, master plan adoption). These are not one clock, so opposed-versus-control span comparisons compare non-commensurable intervals. This finding is fatal on its own.

5. Effective-n inflation in the gate. The attributable-delay gate's "7/10 control events" resolves to 3 unique dates, with one date (prj_218, Homer City) reused across 6 matched sets. Bootstrap confidence intervals on such data would be overconfident.

## What the platform defensibly produces instead

- Block-risk prediction. Outcome model on opposed projects, honest cross-validated AUC with uncertainty stated, decided cases only as labels.
- Descriptive timeline benchmarks. Kaplan-Meier curves on opposed projects, censoring-aware, described as descriptive.
- Case-level observed-cost accounting. Cost layer applied to actual observed spans of individual projects, with documented anchors and assumptions, and no counterfactual attribution language.

## Standing language rules that follow

- Never state or imply that a dollar or delay figure is "caused by" or "attributable to" opposition.
- Client-facing text describes associations and observed histories, never counterfactuals.
- Feature importance in the outcome model is predictive, not causal.

## Reopening criteria

This result could only be revisited if a genuinely new data type arrives, for example a homogeneous single-milestone clock observed on both arms across a large sample (identical permit type, identical jurisdictional process), or an exogenous source of variation in opposition exposure. Growth of the existing tables does not qualify.
