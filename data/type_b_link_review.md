# Type B Opposition-Link Review: prj_323, prj_277, prj_96

Completed 2026-07-23. Follows the referral in `data/date_recovery_negative_spans.md`.
All changes are made at the link level via `data/project_links_manual.csv`
(reject plus confirm rows implement a repoint). No announced dates were moved
and no rows in `master_opposition.csv` were altered. Every disposition below is
supported by the event record's own source coding (the `Project Name` and
`Summary` fields, each carrying source URLs).

## Facility rule adopted

The xAI Memphis-area cluster is three tracked projects plus shared
infrastructure. Events are attributed by the facility they concern:

- Paul R. Lowry Rd campus (Colossus 1): prj_276
- Tulane Rd campus (Colossus 2) and the Stanton Rd, Southaven turbine plant
  that powers it: prj_277. The sourced event summaries attribute the Stanton
  Rd plant's load to Colossus 2 ("41 gas turbines powering xAI's Colossus 2";
  "27 unpermitted methane gas turbines at the Colossus 2 site"). Precedent:
  power-supply infrastructure opposition attaches to the project it serves
  (opp_3c2069dc2adf, Entergy units serving Hyperion).
- Stateline Rd W MACROHARDRR data center: prj_96. Only events about the
  MACROHARDRR project itself (announcement, incentives) attach here.

## Dispositions

### prj_277 -> prj_276 (13 events repointed)

All thirteen carry source coding `Project Name: Colossus 1` and concern the
Lowry Rd turbine and air-permit fight: opp_64bdcdf8bd65 (2024-08-26 SCHD
enforcement letter, the flagged Type B event), opp_ff2f842fb285 (EPA Region 4
inquiry), opp_7989cc6d9520 (TVA 150 MW), opp_d8acbb28f73c (15-turbine permit
application), opp_e049359a3277 (County Commission hearing resolution),
opp_d3a1043dd652 (SELC second letter), opp_0e4c335737e1 (Fairley High
hearing), opp_9901ad833120 (EPA ozone petition), opp_694458bf138a (NAACP NOI,
explicitly "Colossus 1"), opp_7455a1e39a1e (permit issuance),
opp_f5b1e160deb4 (appeal filing), opp_6b8a649077ec (appeal dismissed),
opp_3d338028f9c3 (TVA additional 150 MW).

### prj_96 -> prj_277 (6 events repointed)

All six concern the Stanton Rd turbine plant, not the MACROHARDRR data
center: opp_497fd5c532a7 (2025-11-24 Safe and Sound Coalition launch, the
flagged Type B event, predates MACROHARDRR's naming), opp_4bd47c1c0837
(Feb 2026 MDEQ hearing), opp_e3b11071a95a (MDEQ permit approval),
opp_7082d0e9f88f (federal Clean Air Act suit), opp_0d9643b0e958 (preliminary
injunction motion; note this event's id changed from opp_771128642427 after
its date was updated to 2026-07-14 in the source data), opp_f863ea20d366
(MDEQ turbine-count disclosure). The last of these overrides a prior manual
confirm to prj_96; the prior note predates the facility rule and the override
is recorded in the manual file.

### prj_323 (1 link rejected, no repoint)

opp_51faef25a654 (2024-07-01 Richland Parish Police Jury PILOT approval) is a
procedural approval record, not an opposition event: the sourced summary
states the agreement passed "with no opposition present." Its pre-disclosure
date would manufacture a negative span. Rejected from the linked set;
the record remains untouched in `master_opposition.csv`.

### Left in place, noted

- Multi-scope records on prj_277 whose sourced coverage explicitly includes
  Colossus 2 (opp_2e7b25d60ea2 CBO, opp_163684cd2c66 TN legislation,
  opp_db2ce51b1acd Cohen letter, opp_112e9c2fa247 Whitehouse probe,
  opp_606f388b9a84 Markey letter, opp_9821df442512 overview record). The
  linker assigns one project per event; prj_277 is a defensible single
  attribution for each.
- Colossus Water Recycle Plant records (opp_8ec0ed3f2568, opp_eebe9464f38a,
  opp_35da1240ccc5) stay on prj_277 per prior manual confirms. Flag for a
  future pass: the plant sits near the Lowry Rd campus and serves the wider
  operation, and opp_eebe9464f38a is typed project_withdrawal, which
  describes the water plant's pause, not Colossus 2. Outcomes derive from
  proposals phase, not event types, so no outcome contamination occurs today.

## Results after rerun (project_resolution, feature_asymmetry_check, outcome_model)

- prj_276: 13 events, first opposition 2024-08-26 (announced 2024, year
  precision). prj_277: 18 events, first 2025-03-04 (announced 2025, year).
  prj_96: 1 event, first 2026-01-08 (announced 2026-01). prj_323: 10 events,
  first 2025-04-04, span +124 days. All three negative spans resolved.
- Negative-span worklist is now 5: prj_25 (resolves when the overlay is
  applied by the next scraper run), prj_101, prj_297 (both already on the
  date-recovery list), and two NEW flags not in the pass-1 memo: prj_74
  Project Mitten (-50, month) and prj_160 Project Accelerate (-30, month,
  blocked arm). Add both to the date-recovery queue. prj_158 no longer
  produces a negative span in current data.
- Arrival-speed comparison: direction unchanged (blocked arm faster), all
  precisions 88 vs 224 median days (p=0.184), month-or-better 61 vs 138
  (p=0.226). Still suggestive and underpowered; the repoints neither created
  nor removed the association.
- Outcome model: n=85 (26 blocked), AUC 0.84 [0.70-0.94], Brier 0.166 (base
  0.212). Prior main: n=82, AUC 0.87 [0.73-0.97]. The small AUC decline is
  consistent with removing manufactured fast-opposition signal plus normal
  sample change; intervals overlap heavily. days_to_first_opposition remains
  a modest negative-direction coefficient. Leak audits clean throughout.
