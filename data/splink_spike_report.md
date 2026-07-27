# Splink spike: scored entity resolution

Run over 1443 countable opposition events and 333 projects. Splink 4.0.16, DuckDB backend, unsupervised EM, no training labels used to fit the model. Deterministic across runs.

## Verdict

**NO-GO on adoption**

Criteria G1, G2, G3, G4 not met. Splink separates the easy population well (stratum A AUC 0.947) but that population is the one the existing rules already resolve. On the adjudicated pairs it reaches AUC 0.632 against the incumbent corroboration count at 0.568, an improvement that is real but far short of the level that would let a score replace a human decision. On the contested pairs, which are the reason the spike was run, it reaches AUC 0.605 against a base rate of 0.517 and is confidently incorrect on 21 of them. The limitation is structural rather than a tuning problem: on a contested pair the structured fields agree by construction, so a field-comparison model has nothing left to discriminate on. Recommendation: keep the rule cascade and human adjudication as the confirmation path, and keep the score only as the disagreement audit described below.

## Pre-registered criteria

| # | Criterion | Target | Observed | Met |
| :-- | :-- | :-- | :-- | :-- |
| G1 | blocking recall on adjudicated pairs | >= 0.95 | 0.918 | no |
| G2 | AUC on adjudicated pairs | >= 0.8 | 0.632 | no |
| G3 | precision >= 0.9 at recall >= 0.5 | exists | best precision 0.614 | no |
| G4 | AUC on contested pairs | >= 0.75 | 0.605 | no |
| A1 | disagreement surface non-empty | >= 5 | 14 | yes |

## Stratum A: broad separation (upper bound, not a criterion)

Positives are the 300 rule-confirmed links plus manual confirms; negatives are every other generated pair. Negatives are presumed rather than verified, so this number describes how well the model reproduces the rules, not how well it finds truth.

- AUC 0.947 over 295 presumed positives and 3093 presumed negatives
- Top-1 recovery of confirmed links: 263/300 (0.877)

## Stratum B: adjudicated pairs (the decision set)

97 human decisions in data/project_links_manual.csv, 53 confirm and 44 reject. Blocking generated 89 of them.

- Splink AUC 0.632
- Incumbent corroboration count AUC 0.568

| Threshold | Predicted confirm | Precision | Recall |
| :-- | :-- | :-- | :-- |
| 0.5 | 78 | 0.538 | 0.875 |
| 0.9 | 75 | 0.533 | 0.833 |
| 0.99 | 68 | 0.588 | 0.833 |
| 0.999 | 57 | 0.614 | 0.729 |

Base rate on this stratum is 0.539, so precision below that is worse than accepting every candidate.

## Stratum C: contested pairs (the motivating case)

Derived from the adjudication file, not hardcoded: a pair is contested when its project carries both confirm and reject decisions against different events, or its event was adjudicated against more than one project. These are the cases where several projects share a developer and a region and the question is which one an opposition record concerns.

- Contested projects: prj_121, prj_137, prj_165, prj_276, prj_277, prj_323, prj_329, prj_44, prj_74, prj_90, prj_96
- 58 pairs generated, 30 confirm, base rate 0.517
- Splink AUC 0.605
- Non-contested remainder of stratum B: AUC 0.709 over 31 pairs

| Threshold | Predicted confirm | Precision |
| :-- | :-- | :-- |
| 0.5 | 49 | 0.510 |
| 0.9 | 49 | 0.510 |
| 0.99 | 46 | 0.543 |

## Why the contested cases resist this method

Fellegi-Sunter compares fields. On a contested pair every structured field agrees, because the candidate projects share a developer, a state, a county, and often a coordinate cluster. The evidence that separates them sits in the narrative and in the specific site a hearing concerned, which is what the human adjudications record.

Ablation, run in the same script: dropping the cross-field narrative comparison and scoring on structured fields alone moves stratum B AUC from 0.632 to 0.592 over 88 pairs. The narrative comparison is doing what little separation there is, and a coarse containment test is a poor proxy for reading the source. The model assigns 21 of the contested rejects a probability at or above 0.99, so the errors are confident rather than marginal.

## Blocking recall

89 of 97 adjudicated pairs were generated (0.918). Missed pairs and their cause:

| Project | Human decision | Cause |
| :-- | :-- | :-- |
| prj_46 | reject | different county, event clarke against adair |
| prj_44 | reject | event has no state and no shared company token |
| prj_174 | reject | event has no state and no shared company token |
| prj_277 | confirm | cross-state pair, event MS against project TN |

## Secondary check: disagreement audit

Regardless of the adoption verdict, the score surfaces disagreements the rule cascade cannot express. 14 rule-confirmed links score below 0.5 and 202 unlinked pairs score at or above 0.99. Those two lists are in data/splink_spike_scores.csv under rule_status and are worth a review pass on their own terms. Criterion A1 (met) covers only whether the disagreement surface is non-empty; it does not imply either side is correct.

Lowest-scoring rule-confirmed links:

| opp_id | project | probability |
| :-- | :-- | :-- |
| opp_3f0af36b5412 | prj_323 | 0.0031 |
| opp_97c6b1c4ce0e | prj_323 | 0.0156 |
| opp_2446724c6c23 | prj_323 | 0.0204 |
| opp_502722eed9cd | prj_323 | 0.0204 |
| opp_606f388b9a84 | prj_277 | 0.0407 |
| opp_112e9c2fa247 | prj_277 | 0.0407 |
| opp_db2ce51b1acd | prj_277 | 0.0407 |
| opp_039ab1e1e6fd | prj_264 | 0.0934 |
| opp_ea7d8304fed3 | prj_327 | 0.1828 |
| opp_eb2497aac72f | prj_125 | 0.2870 |

## Reproducibility and scope

- Not imported by project_resolution.py, not called by any workflow, not added to any CI dependency list. Running it creates and removes no links.
- The three output files are review-only. Verbatim incident text is carried in the CSVs for reviewer context and may contain source wording that the leak audit flags; this report contains none.
- u probabilities are estimated over the full cartesian product rather than a sample, so the run is deterministic without a seed.
- Splink is MIT licensed, screened in docs/tooling_scan.md.

