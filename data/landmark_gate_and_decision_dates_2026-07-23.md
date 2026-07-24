# Landmark Retrain Gate + Blocked-Arm Decision-Date Recovery, 2026-07-23

Second pass of the session, follows announced_date_recovery_2026-07-23.md. Where numbers differ from that memo, this one is newer (the prj_322 correction below changes the frame).

## 1. Landmark module delivered: gate closed, criterion locked

`landmark_model.py` (repo root, new, not wired into CI) implements the landmark formulation: t0 = first opposition, features only from events in [t0, t0+W], training conditioned on being undecided at t0+W, so the model answers the only question a pending project can be asked. The window-selection criterion is pre-registered in the module docstring, dated 2026-07-23, locked before any data existed to fit it: candidate W in {30, 60, 90, 120, 180}; floors n>=40, blocked>=12, not_blocked>=12; among feasible windows highest median CV AUC with median Brier below base required; ties within 0.01 AUC resolve to the shortest window. Outcome-typed events (project_withdrawal, permit_denial) never enter features. Month-precision event dates floor to the 1st. Petition-signature and full-history span features are excluded (values are as-of-scrape, not as-of-window). Decided projects without a verified day-precision decision date are excluded from every frame.

Current gate status: CLOSED at every window. Frames are W=30: 7 (5 blocked), W=60: 4 (3 blocked), thinner beyond. Two constraints bind, in order:

1. **Decision-date coverage.** 52 of the decided+opposed projects have no verified decision date and are excluded from every frame. `data/decision_date_worklist.csv` (generated) lists them, blocked arm first, then by event count.
2. **Event-date density.** Even among dated projects, the median gap between first opposition and decision is near zero, because for many projects the only dated opposition event is the decision-adjacent record itself. This is the previously recorded structural asymmetry surfacing again. The four blocked-arm dates recovered below illustrate it: all four projects' first opposition sits 0 to 19 days from decision, so none survives even the 30-day landmark. Opening the gate requires dating pre-decision opposition events, not only decisions, and coverage work must sample both arms.

The module runs today, writes `data/landmark_feasibility.csv` and `data/landmark_model_report.md` with the gate status, and will apply the locked criterion unchanged when the floors are met. No model was fit; nothing here is promotable or client-facing.

## 2. Blocked-arm decision dates recovered (4 of 6)

Appended to `data/project_decision_dates.csv`, all day precision, all sourced:

- **prj_38 Pike Township (IN)**: 2026-02-02. American Tower withdrew its rezoning petition, confirmed Monday Feb 2 (IBJ 2026-02-02; Fox59; Mirror Indy).
- **prj_62 Bates Mill No. 3 (ME)**: 2025-12-16. Lewiston City Council 7-0 denial of the joint development agreement (Maine Public; News Center Maine; Mainebiz). Tracker phase says withdrawn; the terminal disposition was a denial vote, noted in the row.
- **prj_80 Rocklocker Kalkaska (MI)**: 2025-11-20. Developer statement ending pursuit, two days after the hostile Nov 18 forum (The Ticker; 9and10 News; Interlochen Public Radio).
- **prj_119 New Brunswick (NJ)**: 2026-02-18. Council voted unanimously Wednesday Feb 18 to remove data centers as a permitted use from the Jersey-Sandford plan (Jersey Vindicator, explicit date; Food and Water Watch; TAPinto). Some secondary coverage says Feb 19; those are next-day reports, noted in the row.

## 3. prj_232 Dickson City: flagged, no date supplied (mid-litigation)

The tracker codes prj_232 blocked_confirmed, but the disposition is contested and live: council adopted the restrictive zoning ordinance 2026-02-12; the developer filed a land use appeal in Lackawanna County Common Pleas 2026-03-13; the Zoning Hearing Board upheld the ordinance 2026-05-20; the Common Pleas case continues (WVIA 2026-05-21). Per the entangled-cases rule, no decision date is supplied, and the blocked_confirmed coding should go to defensibility review: a court could void the ordinance. Recommend outcome_defensibility treatment as mid-litigation.
Sources: https://www.wvia.org/news/local/2026-05-21/dickson-city-zoning-board-upholds-ordinance-governing-data-centers-in-the-borough ; https://www.aol.com/articles/developer-challenges-dickson-city-exclusionary-000300927.html

## 4. prj_322 Province Group Perry Village: coding error, phase corrected

The tracker codes prj_322 withdrawn / blocked_confirmed, but the project is active: April 2026 protests at Perry Village council, the village awaiting the developer's PILOT/financing request, Perry BOE confirming "no new update" on 2026-06-29, and a Protect Perry and Lake Erie ballot initiative with an August 5 petition deadline. No withdrawal is on record. Added overlay row `322,phase,proposed` (same mechanism as prj_141's voided approval), which removes it from the decided frame at the next scraper run. This subtracts one blocked case: the modeling frame moves to n=83 (25 blocked).
Sources: https://www.news5cleveland.com/news/local-news/crowd-packs-perry-village-hall-to-protest-data-center-project-as-tensions-rise-across-ohio ; https://www.theohioregister.com/perry-boe-confirms-no-new-update-on-data-center/

## 5. Simulated post-scraper state (full local rerun)

- Frame: n=83, 25 blocked (prj_141 and prj_322 out via overlay; both are coding corrections, not sample attrition).
- Outcome model: AUC 0.80 [0.69-0.95], Brier 0.176 vs 0.210 base. Median down from 0.84 after removing prj_322's blocked case; intervals overlap heavily and the frame is more defensible, which is the point.
- Asymmetry worklist unchanged at 3 (prj_101 opposition-side error, prj_158 pre-application opposition, prj_297 precision artifact); timing direction unchanged, still suggestive and underpowered only.
- Decision-date worklist: 52 (was 57; 4 dated, prj_322 out).
- Leak audit clean on every generated file.

## 6. Push set and placement

New: `landmark_model.py` (root); `data/landmark_model_report.md`; `data/landmark_feasibility.csv`; `data/decision_date_worklist.csv`.
Replace: `data/proposals_manual_overlay.csv` (six date corrections from pass 1 + prj_322 phase row); `data/project_decision_dates.csv` (+4 rows, LF endings per standing rule).

Generated artifacts in this delivery reflect the post-scraper simulated state. After the next nightly scraper run applies the overlay, regenerate in order: `python3 project_resolution.py && python3 outcome_model.py && python3 feature_asymmetry_check.py && python3 landmark_model.py`. Note the ordering: feature_asymmetry_check reads the outcome model's features file, so it runs after outcome_model.

## 7. Verify after next scraper run

1. prj_322 phase shows proposed; decided frame at n=83 (25 blocked).
2. Pass 1 items: prj_74 at 2025-07-10 day, prj_25 span 0, prj_323 month precision, prj_141 proposed.
3. Landmark gate report regenerates with the same frames (the four new dates do not create survivors; see section 1).
4. prj_232 remains pending defensibility review; do not add a decision date for it without a terminal court disposition.
