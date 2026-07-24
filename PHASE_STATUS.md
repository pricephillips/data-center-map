# Roadmap Phase Status

Standing document. Update at the end of any session that changes a phase's position; keep the history log at the bottom append-only. Numbers below are as of the stated date and reflect the post-scraper simulated state (they become the live repo state after the next nightly run applies the overlay).

Last updated: 2026-07-23. Frame: 332 projects, 145 decided, 83 decided+opposed (25 blocked).

## The five phases

| Phase | What it is | Status | Gate to next step |
|---|---|---|---|
| 1. Entity resolution + lifecycle dates | Project entities, event links, announced/decision dates | Infrastructure COMPLETE; data quality OPEN, permanently | Decision dates: 52 missing. Pre-decision event dating: sparse (median first-opp-to-decision gap near zero among dated projects) |
| 2. Control group / baseline universe | Matched unopposed comparables; now extended by the verified-negative audit | Matching COMPLETE; audit 9/175 coded | Audit coverage; emergence claims rest on codings, never the raw unopposed flag |
| 3. Outcome + survival models | Retrospective P(blocked) and time-to-decision on decided+opposed | Retrospective COMPLETE (AUC 0.80 [0.69-0.95], n=83); landmark retrain GATE CLOSED | Landmark floors (n>=40, blocked>=12 per window); binding constraint is Phase 1 date coverage, criterion pre-registered 2026-07-23 |
| 4. Cost translation layer | Documented conversion of predicted observables to dollar ranges with published anchors | Scaffolded in CI, NOT implemented; deliberately deferred | A promotable model to translate; building it on contaminated inputs would violate defensibility |
| 5. Continuous retraining + calibration gate | calibration_gate.py + retrain.yml; promotion only on holding calibration | BUILT, waiting | A model that passes the gate; both landmark and emergence models are written to promote only through it |

Parallel track, outside the numbered phases: the county product line (county_aggregator.py, county_policy_model.py, restriction-model.html), live and client-presentable, framed as resemblance, never as project risk.

## Where this leaves the client-usable score

The path is unchanged: landmark retrain gives P(blocked) risk bands for pending projects; the negative audit unlocks P(opposition emerges); the cost layer translates; the two-mode map delivers. What changed in 2026-07 is the understanding of the bottleneck: it is not modeling, it is Phase 1/2 data quality. The phases went recursive, and that is by design of the defensibility principle, not a stall.

## Known data-quality ledger (open items)

- prj_61: probable Clark County ARKANSAS miscoding (state and outcome both suspect); needs its own verification pass; NOT a duplicates-file entry.
- prj_232, prj_266: mid-litigation, blocked_confirmed coding under defensibility review; no decision dates supplied.
- prj_101: opposition event dated 2025-10-01 predates public knowledge (late-Dec 2025 legal ad); fix rotates the opp_id, re-key manual rows in the same pass.
- Seven batch-1 verified_opposition projects (prj_295, 279, 201, 331 via 76, 86, 16, 104) need opposition events collected into master_opposition; each coding row carries sources.
- Asymmetry worklist: prj_101 (above), prj_158 (genuine pre-application opposition, not an error), prj_297 (month-floor artifact).

## Update log (append-only)

- 2026-07-23: Announced-date recovery (6 projects, overlay rows). Landmark module built, criterion pre-registered, gate reported CLOSED; 4 blocked-arm decision dates recovered. Negative audit designed (census of 176, now 175) and batch 1 executed: 7 of 10 blocked-without-recorded-opposition rows were detection gaps, 1 true negative (Millinocket, commercial failure), 2 undeterminable. Coding corrections: prj_141 voided approval and prj_322 active-not-withdrawn (overlay), prj_76/331 duplicate (new suppression mechanism in project_resolution). Frame moved n=85 to n=83 (25 blocked); both removals are corrections, not attrition.
