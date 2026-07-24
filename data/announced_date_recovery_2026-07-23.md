# Announced-Date Recovery Pass, 2026-07-23

Six projects from the negative-span worklist researched against primary municipal records and developer filings. All corrections are delivered as durable rows in `data/proposals_manual_overlay.csv`; they take effect when the nightly scraper next applies the overlay and pushes `data/proposals.csv`, which then fires the pipeline. No hand edits to `proposals.csv` or `master_opposition.csv`.

## Corrections

**prj_74 Project Mitten / The Barn (Saline Township, MI).** Announced moves 2025-10 (month) to 2025-07-10 (day). Related Digital submitted its rezoning application to Saline Township on July 10, 2025, per the township's official project summary document (last updated 2026-01-28). The prior scraper date reflected the OpenAI/Oracle Stargate announcement of Oct 30, 2025, months after the local proposal, the Aug 5, 2025 Planning Commission public hearing, and the Sept 10, 2025 board denial. Span moves -50 to +33.
Source: https://salinetownship.org/go.php?id=731&table=page_uploads (corroboration: https://thesuntimesnews.com/saline-township-related-digital-rezoning/)

**prj_160 Project Accelerate (Matthews, NC; blocked arm).** Announced confirmed at 2025-07 (month), replacing the scraper's 2025-10. The rezoning petition was filed with the Town of Matthews in July 2025; the Board of Commissioners initiated the rezoning process the week of July 14-21, 2025. Raw span moves -30 to positive. This project is in the blocked arm, so the fix matters for the timing-asymmetry finding.
Sources: https://www.matthewsnc.gov/newsview.aspx?nid=7118 ; https://www.charlottestories.com/matthews-joining-charlottes-tech-boom-with-massive-new-123-acre-data-center-development/

**prj_158 Natelli New Hill Digital Campus (Apex, NC; blocked arm).** Announced set to 2025-09-02 (day), the application receipt date: Natelli filed annexation, rezoning, and UDO amendment applications with the Town of Apex on September 2, 2025, per the developer's own FAQ; WRAL reported the filing September 3. Note: a residual -32 raw span remains because the first linked opposition event (2025-08-01, month precision) reflects genuine pre-application opposition. The proposal was publicly known in August 2025 through the town's required pre-application neighborhood meeting and press coverage (e.g. Holly Springs Update, Aug 27, 2025). This is real pre-filing opposition, not a date error; it should be interpreted as opposition beginning during the pre-application phase.
Sources: https://newhilldigitalcampus.com/faqs/ ; https://www.wral.com/news/local/developer-plans-data-storage-facility-apex-sep-2025/

**prj_297 Jailhouse Studios (Hamilton County, TN).** Announced moves 2026-03 (month) to 2026-01-07 (day). The proposal was presented to the Hamilton County Commission at its January 7, 2026 meeting per commission minutes cited by DCD; the developer's press release followed January 9, 2026. The scraper date reflected final lease approval (March 18, 2026). The residual -6 raw span is a month-floor artifact: the linked opposition event carries month precision (2026-01-01) while the documented first opposition rally occurred January 14-15, 2026, after the announcement.
Sources: https://www.datacenterdynamics.com/en/news/former-jail-in-chattanooga-tennessee-targeted-for-production-space-and-data-center/ ; https://urbanstoryventures.com/citys-new-jailhouse-studios-the-worlds-first-quantum-ready-sovereign-data-processing-and-ai-center-with-an-integrated-creator-campus/

**prj_101 Wildwood Ranch AI Data Center (Joplin, MO).** Announced moves 2026 (year) to 2025-12 (month). The annexation/rezoning proposal first became public via a legal ad in the Joplin Globe in late December 2025; the Globe wrote in mid-January 2026 that the proposal "became public little more than two weeks ago in a legal ad." First documented public opposition was at the January 12, 2026 Planning and Zoning Commission meeting.
Source: https://www.yahoo.com/news/articles/data-center-proposal-expected-draw-233100952.html (Joplin Globe syndication)

**prj_113 Project Mica (Clay County, MO).** Announced moves 2026-02 (month) to 2024-03 (month). The scraper date reflected Google's official confirmation of Feb 12, 2026, which postdates both Port KC's July 29, 2025 bond authorization and construction start, producing the approval-precedes-announcement inconsistency. The project was first publicly reported as a Google data center campus in March 2024 (KCTV, land purchase reporting). No opposition events are linked; the fix restores announced-to-decision ordering only.
Sources: https://www.kctv5.com/2025/07/29/10b-500-acre-tech-campus-gets-stamp-approval-near-smithville/ ; https://www.kshb.com/news/local-news/missouri/kansas-city/google-announces-construction-underway-for-second-data-center-campus-in-kansas-city

## Flagged, not fixed: prj_101 opposition event date

The single opposition event linked to prj_101 (opp_f60e4a1ca6fa, master_opposition row for Joplin / Jimmer Pinjuv, dated 2025-10-01) predates public knowledge of the project (late-Dec 2025 legal ad). Every source attached to that row is from January-March 2026 (P&Z Jan 12, council Jan 20 and Feb 17, referendum certification Mar 17). The 2025-10-01 date is a source-side default, and the correct first-opposition date is on or about 2026-01-12 (P&Z meeting, neighbors in opposition per Joplin Globe). Correcting the event date in the raw feed will rotate the opp_id (content hash) and orphan the manual link row for prj_101 in `data/project_links_manual.csv` if one exists; the link row must be re-keyed in the same pass, per the established rotation procedure. Until then the model clamps the raw -61 span to zero.

## Simulated post-scraper state (local overlay application, full pipeline rerun)

- Negative-span worklist drops 5 to 3: prj_25 and prj_74 and prj_160 resolve; remaining are prj_101 (opposition-side, flagged above), prj_158 (genuine pre-application opposition), prj_297 (month-floor artifact).
- prj_141 leaves the decided frame (voided approval overlay row applies), so the modeling frame moves n=85 to n=84 (26 blocked). This is the intended effect of the existing prj_141 overlay row, not a regression.
- Outcome model after retrain: AUC 0.85 [0.73-0.93], Brier 0.161 vs 0.214 base. Stable against the pre-fix 0.84 [0.70-0.94]; intervals overlap.
- days_to_first_opposition: blocked median 62d vs not-blocked 138d at month-or-better precision, p=0.201 (previously 61 vs 138, p=0.226). Direction unchanged; still reported as suggestive and underpowered only.
- Leak audit: clean on all regenerated outputs.

## Verify after next scraper run

1. prj_74 announced shows 2025-07-10 day precision, span +33.
2. prj_25 span resolves to 0; prj_323 announced precision moves day to month (pre-existing overlay rows).
3. prj_141 phase shows proposed; decided count drops by one; CI retrain reflects n=84.
4. Asymmetry worklist shows exactly prj_101, prj_158, prj_297 with the interpretations above.
