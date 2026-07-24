# Verified-Negative Audit: Design + Batch 1, 2026-07-23

Third pass of the session. Follows the landmark gate memo; numbers here supersede where they differ.

## Design (registered 2026-07-23, in module docstring)

`negative_audit.py` (repo root, new, not in CI) implements the audit. Key design decisions, all recorded:

**Census, not sample.** The modern-proposal portion of the baseline universe is 176 rows (149 proposals_unopposed + 27 ai_centers), inside the 150-200 budget, so the audit is a census. No sampling error to defend in any downstream claim.

**Atlas rows excluded on detectability grounds.** The 1,479 non-opposed atlas rows are built/legacy facilities whose approval processes predate reliable digital coverage; an active search finding nothing there cannot distinguish no-opposition from no-record. The exclusion is a detectability decision, not a claim about those facilities, and the report states so.

**Coding rules are fixed.** verified_opposition requires a sourced evidence_url. verified_none requires BOTH a full protocol run finding nothing AND a detectability floor: an independent source documenting the project's approval/development process (detectability_url), so absence of evidence only counts when the project is visible in the record. Everything else is undeterminable. Control-group exclusion flags do not exclude rows from the audit.

**Worklist order.** Seeded shuffle (seed 20260723), with the 10 blocked_confirmed-with-no-recorded-opposition rows sorted first as a purposive cell (most anomalous, most likely coding errors in either direction). Batches after that cell are random subsets; the purposive cell's mix must not be extrapolated to the frame.

**Files.** `data/negative_audit_worklist.csv` (frame + per-project search protocol), `data/negative_audit_codings.csv` (append-only, validated on ingest: URLs required per coding type, coder and date required), `data/negative_audit_report.md` (coverage, mix, interpretation rules). Emergence-rate statements use verified_opposition / (verified_opposition + verified_none) with the undeterminable count always reported alongside; no emergence model trains until frame coverage is complete.

## Batch 1 executed: the blocked-without-opposition cell (10 projects)

Headline: **7 of 10 "unopposed" blocked projects actually faced heavy, well-documented opposition.** These are tracker detection gaps, not true negatives. One is a genuine true negative, two are undeterminable, and the batch surfaced two data-quality problems beyond the audit itself.

verified_opposition (7): prj_295 Nottingham NH (14,747-signature petition, withdrawal under outcry 2026-05-27); prj_279 CleanSpark Mountain City TN (1,700+ petition, rezoning nixed Aug 2025); prj_201 Slavic Village OH (outcry, rally, responsive moratorium, permit rejected 2026-05-14); prj_76 Project Splitrock MI (2AM meetings, 2,200-signature petition, unanimous denial, withdrawal); prj_86 Pavilion Township MI (EDRA-documented resident fight against Franklin Partners); prj_16 Vilonia AR (mayor: "400 or 500 against," unanimous council denial May 2025); prj_104 Cross Creek MO (standing-room commission meetings, county restrictive ordinance, city moratorium, annexation withdrawn).

verified_none (1): prj_64 Millinocket ME. The analytically precious case: announcement celebrated by both senators and the governor, residents wanted it, and it died commercially in 2025 (no AI customer, insufficient power). A blocked-without-opposition true negative whose failure mode is commercial cancellation, not opposition. Mechanism flag recorded (matters for outcome-mechanism pairing).

undeterminable (2): prj_61 "Project Pulse KY" (no such Kentucky project found; strongly matches Clark County ARKANSAS, Arkadelphia mega site, which is advancing; probable state miscoding, flagged); prj_266 Newton Township PA (administrative not-an-allowed-use denial, now mid-appeal in ZHB and Common Pleas via the same attorney as prj_232; resident intervention of unclear direction; blocked_confirmed coding flagged for defensibility review, same entangled posture as prj_232).

## What batch 1 means

1. **The frame's blocked cell is mostly detection failure.** Do not read the 70% as a frame-wide rate (purposive cell), but it establishes that trackdatacenters "unopposed" status is unreliable exactly where outcomes are worst. Every emergence claim must rest on audit codings, never on the raw unopposed flag. This was the hypothesis behind the audit; batch 1 confirms it on the first 10 rows.
2. **Seven projects need opposition events collected into master_opposition.** Each coding row carries sources. Once events are added and linked, these projects enter the outcome-model frame with real opposition histories, growing the decided sample. That is a separate collection pass; the audit coding stands on its own regardless.
3. **Two new tracker corrections queued** (not applied this session; both need their own verification pass): prj_61 probable state/outcome miscoding; prj_76/prj_331 duplicate (same project, same developer, same county, same announce month; prj_331 carries the event and decision date). Project resolution needs a small duplicate-suppression mechanism, e.g. `data/project_duplicates.csv` (keep_id, drop_id, reason, source); recommend building it next session rather than improvising via the phase overlay.

## Push set and placement

New, repo root: `negative_audit.py`.
New, data/: `negative_audit_worklist.csv`, `negative_audit_codings.csv`, `negative_audit_report.md`, and this memo.

Nothing here touches CI, proposals.csv, or master_opposition. The audit is manual-run: `python3 negative_audit.py` regenerates the worklist and report and validates codings. Future batches append rows to `data/negative_audit_codings.csv` (the most recent row per universe_id takes precedence) and rerun the module.

## Next session priorities (updated queue)

1. Continue audit batches (rows 11+ are random draws from the frame; ~15-20 per session is sustainable). The frame-wide emergence rate becomes quotable, with caveats, as coverage grows.
2. Build the duplicate-suppression mechanism in project_resolution; apply to prj_76/prj_331; investigate prj_61.
3. Collect opposition events for the seven batch-1 verified_opposition projects into master_opposition (grows the outcome-model frame).
4. Decision-date recovery continues (52 remaining; blocked arm done except mid-litigation cases).
5. Phase 4 cost layer remains queued behind the above.
