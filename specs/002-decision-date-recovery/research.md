# Research: Decision-Date Recovery

**Feature**: `specs/002-decision-date-recovery/` | **Date**: 2026-09-28

## Decision 1: argparse strategy — how to add --decision-dates without breaking the existing positional `sys.argv[1]` entrypoint

**Decision**: Replace the `if __name__ == "__main__"` block's bare `sys.argv[1]` call with `argparse`. The existing positional-arg behavior (`python date_recovery.py master_opposition_clean.csv`) becomes `--input FILE` (optional, defaults to `master_opposition_clean.csv`); `--decision-dates` and `--selftest` are added as new boolean flags. `apply_recovery()` is not touched — only `__main__` changes.

**Rationale**: The current entrypoint is 3 lines; argparse replaces it with ~15 clean lines. Callers in `pipeline.yml` can be migrated from positional to `--input` with no behavior change. No existing scripts pass the path directly in a way that breaks, and the spec explicitly says `apply_recovery()` must stay backward-compatible (FR-009), not the CLI entrypoint.

**Alternatives considered**:
- Keep `sys.argv[1]` detection and add `--decision-dates` as `sys.argv[1] == "--decision-dates"` — rejected; fragile, doesn't scale to `--selftest`.
- Add a separate script `date_recovery_decision.py` — rejected; spec says same file, and constitution prefers no new modules without justification.

---

## Decision 2: Join strategy — how to get source URLs for worklist projects

**Decision**: Join `data/decision_date_worklist.csv` to `master_opposition.csv` on `project_name` (worklist) == `Project Name` (master, lowercased, stripped). For each worklist project, collect the first non-blank `Source URL` found in any matching row. 

**Rationale**: `decision_date_worklist.csv` has `project_id` (prj_XXX format), `project_name`. `master_opposition.csv` has `Project Name` and `Source URL` but no `project_id`. No other shared key exists.

**Coverage finding (critical)**: Only 9 of 48 worklist projects have a match in `master_opposition.csv` via this join. 39 projects have no opposition rows and thus no source URL. These 39 will appear in the candidates output with `source_url=""`, `method="no_source_url"`, `recovered_date=""`. This is expected behavior per FR-007 and must be documented in the candidates file.

**Alternatives considered**:
- Join via `project_lifecycles.csv` — has project_ids but no source URLs; ruled out.
- Cross-reference `proposals.csv` — has different integer IDs; no URL fields; ruled out.

---

## Decision 3: year_only method handling

**Decision**: When `recover_from_url()` returns method `year_only_midyear`, the candidates row must set `year_only=true`. All other methods set `year_only=false`. This flag is a key output for human review, since a year-precision date (e.g., 2024-07-01) is much less reliable than a full YMD date. The human reviewer uses this flag to decide whether to adopt or discard the candidate.

**Rationale**: The `recover_from_url()` function already uses `kind + "_midyear"` to signal this case. We simply map that to the `year_only` boolean at write time. The alternative (not flagging it) would obscure a material precision difference from the reviewer.

---

## Decision 4: Output columns for decision_date_recovery_candidates.csv

**Decision**: The candidates file has exactly 7 columns in this order:
`project_id, project_name, state, lifecycle_outcome, recovered_date, method, source_url, year_only`

All columns are present for every row, including the 39 no-match rows (blank `recovered_date`, `method="no_source_url"`, blank `source_url`, `year_only=false`).

**Rationale**: Keeps the reviewer workflow simple: sort by `recovered_date != ""` to see hits first, then check `year_only` for precision, then `source_url` to verify. Including all 48 rows (not just hits) means the reviewer can use this file as a complete tally — "48 total, N recovered, M year-only, K no source".

---

## Decision 5: pipeline.yml integration

**Decision**: Add one step to the selftest block (around line 119 of `.github/workflows/pipeline.yml`):
```yaml
- name: Selftest date_recovery
  run: python date_recovery.py --selftest
```

The `--decision-dates` pass is NOT added as a pipeline step — it requires manual invocation. The output `data/decision_date_recovery_candidates.csv` is also NOT committed by pipeline; it is a local work product. The reviewer appends accepted rows to `project_decision_dates.csv` by hand.

**Rationale**: `--decision-dates` reads `master_opposition.csv` which is large and the join may change as new opposition rows are added. Running it on every push would produce a stale candidates file that nobody triggered. The value is in running it on-demand before a review session. The `--selftest`, however, is purely offline/inline-fixture and belongs in CI exactly like every other module.

---

## Phase 0 Unknowns — All Resolved

| Unknown | Resolution |
|---------|-----------|
| Does `master_opposition.csv` have a "Source URL" column? | Yes — verified by reading `apply_recovery()`, which uses `r.get("Source URL", "")` |
| Does the join by project_name give enough coverage? | 9/48 (19%). Not enough to close the landmark gate alone, but all recoverable candidates are captured. Manual sourcing of the 39 remaining is a subsequent maintainer task. |
| Is `date_recovery.py` called anywhere in `pipeline.yml`? | No call to `--selftest` exists; `date_recovery_report.csv` is committed in the git-add step (~line 459) but the module itself is not explicitly called. The positional invocation must have been removed at some point. Adding `--selftest` to CI is safe. |
| Does `project_decision_dates.csv` have a writer in pipeline.yml? | No automated writer. It is Layer B, hand-maintained. Confirmed safe. |
