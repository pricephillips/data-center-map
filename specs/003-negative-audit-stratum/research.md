# Research: Negative Audit Random Stratum Tooling

**Feature**: `specs/003-negative-audit-stratum/` | **Date**: 2026-09-28

## Decision 1: How to add argparse without breaking the default (regenerate) behavior

**Decision**: Replace `if __name__ == "__main__": sys.exit(main())` with an argparse block. `main()` keeps its current signature and behavior (called by default when no flags are given). `--next N` and `--selftest` are new paths that call new functions. `argparse` is added to imports.

**Rationale**: `negative_audit.py` currently has a bare `sys.exit(main())` entrypoint with no argument parsing. Adding argparse is the cleanest extension. `main()` is kept intact so the report generation path doesn't change.

**Alternatives considered**:
- `sys.argv` inspection: fragile, doesn't scale to two new flags.
- New separate script: rejected per constitution (no new files without justification; all audit logic belongs together).

---

## Decision 2: How --next N identifies the random stratum

**Decision**: The random stratum is defined as all worklist rows where `lifecycle_outcome != "blocked_confirmed"`. This matches the spec ("rows with `audit_order` greater than the last `blocked_confirmed` row") and is derived from the worklist file at runtime. The implementation reads `data/negative_audit_worklist.csv` directly (not calling `build_frame()`, which would re-read and re-sort the universe CSV). Preserving worklist row order from file is essential — the worklist is the canonical seeded-shuffle order.

**Current data**: 191 rows total; 22 `blocked_confirmed` (audit_order 1–22); 169 random stratum (audit_order 23–191). The last blocked row always has audit_order 22 in the current data, but the implementation uses `lifecycle_outcome` not a hardcoded offset.

**Rationale**: Reading the already-written worklist CSV is simpler and faster than re-running `build_frame()` (which requires `data/baseline_universe.csv` and re-shuffles). The worklist is the stable ordering artifact.

---

## Decision 3: How --next handles the codings file (missing file case)

**Decision**: If `data/negative_audit_codings.csv` does not exist, all random-stratum rows are uncoded. `--next N` silently treats the empty-codings case as "0 coded". No error is raised.

**Rationale**: FR-002 says "If the codings file does not exist, all random-stratum rows are uncoded." This is the expected early-run state and should not block the researcher.

---

## Decision 4: How the coverage gate interacts with the existing report structure

**Decision**: Add `COVERAGE_THRESHOLD = 0.80` as a module-level constant immediately after `RANDOM_STATE`. In `main()`, after computing codings, compute `n_random_total` (count of random-stratum rows) and `n_random_coded` (count of coded rows whose `universe_id` is in the random stratum). If `n_random_coded / n_random_total < COVERAGE_THRESHOLD`:
- Label the coding-mix table heading as `"## Coding mix (interim descriptives — {pct}% of random stratum coded; {needed} rows remaining)"`
- Omit the emergence rate entirely
- Add a note after the table: "Emergence rate withheld: random-stratum coverage at {pct}% (threshold: {int(COVERAGE_THRESHOLD*100)}%)"

If `>= COVERAGE_THRESHOLD`, print the emergence rate in the same paragraph as the undeterminable count, per the existing docstring rule.

**80% threshold**: Per user instruction, keep 80%. This matches the spec (FR-004, SC-006) and there is no statistical reason to deviate.

**Rationale**: The docstring already says "No emergence model trains until coverage of the frame is complete; partial-coverage rates are interim descriptives only." The gate makes this machine-enforceable. The existing report text structure is preserved; only the heading and the presence/absence of the emergence rate change.

---

## Decision 5: --next output format

**Decision**: Each row printed by `--next N` is a formatted block:

```
[audit_order] project_name (state, county) [lifecycle_outcome]
Search: search_protocol
```

One blank line between rows. No CSV — this is a human-readable queue for a researcher working top-down through paper coding.

**Rationale**: The spec says FR-001 requires `name`, `state`, `county`, `lifecycle_outcome`, and `search_protocol`. The format must be readable when printed. A researcher prints this, works through it, and enters results into the codings CSV. No machine-parsing is needed.

---

## Phase 0 Unknowns — All Resolved

| Unknown | Resolution |
|---------|-----------|
| Is `negative_audit.py` called anywhere in pipeline.yml? | No — completely absent from CI. Adding `--selftest` to the selftest block is the full CI change needed. |
| Are `negative_audit_*.csv` and `negative_audit_*.md` declared in Layer E? | Yes — `"data/negative_audit_*.csv"` and `"data/negative_audit_*.md"` are in Layer E's files list. No new layer declarations needed. |
| What `check()` helper pattern to use? | Same as `date_recovery.py` (just added) and `scripts/scrape-trackdatacenters-proposals.py` — `checks = []` list, `check(label, ok)` appender, `n_ok / len(checks)` summary, return 0 if all pass else 1. |
| Does the coverage gate need to handle the blocked_confirmed cell separately? | Yes — per spec US2 acceptance scenario 3: the gate is based on random-stratum coverage only, not total frame coverage. Blocked_confirmed rows are excluded from both numerator and denominator of the coverage fraction. |
