# Link disagreement audit

Generated 2026-09-23 by `link_disagreement_audit.py`.

Where the live rule cascade and the frozen model scores from `splink_spike.py`
disagree about the same pair. The spike's NO-GO verdict on adoption stands and
is not reopened here: the score is used only as a second opinion that can be
read against the rules, never as a decision.

| | count |
|---|---|
| rule-confirmed, model below 0.5 | 10 |
| unlinked, model at or above 0.99 | 429 |
| **open disagreements** | **439** |

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
| current rule links | 431 |
| of those, carrying a model score | 64 (14.8 pct) |
| of those, with no score | 367 |

A link created after the spike ran cannot be audited here and is counted above
rather than passed over. As that number grows the audit covers less of the
live frame, and the answer then is to re-run the spike, not to read a shrinking
sample as though it were the whole.

Unscored current links (first 20):

- opp_007362ac6379 -> prj_212
- opp_021c2c472697 -> prj_339
- opp_021c2c472697 -> prj_357
- opp_039ab1e1e6fd -> prj_292
- opp_03cd61627883 -> prj_106
- opp_03cd61627883 -> prj_119
- opp_04092e1aeef4 -> prj_290
- opp_0453f3eb092f -> prj_138
- opp_0453f3eb092f -> prj_151
- opp_062ff8f63413 -> prj_138
- opp_062ff8f63413 -> prj_151
- opp_079ba18862cf -> prj_102
- opp_08ec2eb3c157 -> prj_210
- opp_0c2b8287973b -> prj_273
- opp_0c8866392928 -> prj_93
- opp_0d9643b0e958 -> prj_123
- opp_0d9643b0e958 -> prj_277
- opp_0dd5fa8d7b11 -> prj_122
- opp_0dd5fa8d7b11 -> prj_135
- opp_0e4c335737e1 -> prj_334
