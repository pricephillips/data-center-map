# Link disagreement audit

Generated 2026-09-12 by `link_disagreement_audit.py`.

Where the live rule cascade and the frozen model scores from `splink_spike.py`
disagree about the same pair. The spike's NO-GO verdict on adoption stands and
is not reopened here: the score is used only as a second opinion that can be
read against the rules, never as a decision.

| | count |
|---|---|
| rule-confirmed, model below 0.5 | 7 |
| unlinked, model at or above 0.99 | 171 |
| **open disagreements** | **178** |

Rows are in `data/link_disagreement_worklist.csv`, most disagreeable first.

## How to read a row

A `rule_confirmed_low_score` row is a link the rules made and the model finds
implausible. Check whether the corroborating signals are as strong as the tier
implies, or whether one strong field is carrying the match alone.

An `unlinked_high_score` row is a pair the rules never proposed and the model
finds near-certain. Check whether a real link is being missed because no rule
covers its shape.

Neither is evidence on its own. The model was measured at AUC 0.632 on
adjudicated pairs and 0.605 on contested ones, so it is a prompt to look, not a
verdict. Resolve a row by adjudicating the pair in
`data/project_links_manual.csv`; adjudicated pairs are excluded from the next
run, so this worklist shrinks as it is worked.

## Coverage, and the limit of this audit

The scores are frozen at the registered spike run. Splink is not a dependency
of this repository and this module does not re-score.

| | count |
|---|---|
| current rule links | 311 |
| of those, carrying a model score | 281 (90.4 pct) |
| of those, with no score | 30 |

A link created after the spike ran cannot be audited here and is counted above
rather than passed over. As that number grows the audit covers less of the
live frame, and the answer then is to re-run the spike, not to read a shrinking
sample as though it were the whole.

Unscored current links (first 20):

- opp_0d9643b0e958 -> prj_277
- opp_1228ae833969 -> prj_1003
- opp_15719bc3d5d4 -> prj_1012
- opp_2446724c6c23 -> prj_1003
- opp_2be4ae43a667 -> prj_1012
- opp_2dc8ce944836 -> prj_326
- opp_3c2069dc2adf -> prj_1003
- opp_3d94fe3695b1 -> prj_1002
- opp_3f0af36b5412 -> prj_1003
- opp_4916d5cd8858 -> prj_1003
- opp_497fd5c532a7 -> prj_277
- opp_4bd47c1c0837 -> prj_277
- opp_502722eed9cd -> prj_1003
- opp_51814228f383 -> prj_1003
- opp_660d40c453a6 -> prj_1009
- opp_83a1f99c8cf9 -> prj_58
- opp_87e445fe8cc4 -> prj_1004
- opp_8be8b626b0e2 -> prj_1003
- opp_97c6b1c4ce0e -> prj_1003
- opp_98cfd4441962 -> prj_280
