# Output Contracts

All CSVs are UTF-8, LF line endings, header row first. New columns go at the
end (FR-004).

| File | Layer | Writer | Rewrite policy |
|---|---|---|---|
| `data/source_archive.csv` | E (not_regenerable: resume state) | `source_archive.py` | Rewritten in full each run, sorted by `url`. Rows are never dropped. |
| `data/source_archive_manifest.json` | E | `source_archive.py` | Rewritten each run. `history` is capped at 60 entries. |
| `data/date_hint_agreement.csv` | E | `article_extract.py --measure` | Rewritten; fetched rows are kept. |
| `data/date_hint_agreement.md` | E | `article_extract.py --measure` | Rewritten |
| `data/untagged_date_hints.csv` | C (`data/untagged_*.csv`) | `untagged_triage.py` | Append-only |
| `data/signal_candidates.csv` | C (existing handoff) | `signal_harvest.py`, then `promote_signal_candidates.py` | Existing; 4 appended columns |
| `data/signal_promotion_report.csv` | C (existing) | `promote_signal_candidates.py` | Append-only; new action `cluster_member` |
| `data/untagged_triage.csv` | C (existing) | `untagged_triage.py` | Existing; 2 appended columns |
| `data/status_resolution_worklist.csv` | E (existing) | `status_resolution.py` | Existing; 2 appended columns |

Column definitions: data-model.md.

## `data/date_hint_agreement.md` shape

```text
# Date hint agreement (SC-003)

Sample: first 30 rows of data/project_decision_dates.csv by project_id.
Rule: a hint agrees when it is within 1 day of the verified decision date.

| Measure | Rows |
|---|---|
| Sampled | 30 |
| Fetched | n |
| With a hint | n |
| Exact day | n |
| Within 1 day | n |
| Within 3 days | n |
| No hint | n |

SC-003: met / not met / not yet measured (k of 30 fetched)
```

The report is counts and dates only, with no outcome prose (Principle II).
