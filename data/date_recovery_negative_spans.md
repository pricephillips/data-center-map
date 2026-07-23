# Negative-Span Date Recovery: Research Pass 1

Generated 2026-07-22. Covers the seven projects flagged by
`feature_asymmetry_check.py` where the recorded first opposition date
precedes the recorded announced date, plus Project Mica.

## Headline finding: negative spans have two distinct causes

The asymmetry check treats every negative span as an announced-date error.
Research shows that is only sometimes true. Two failure modes exist and they
require opposite fixes:

- **Type A, announced-date too late.** The scraper captured a later
  procedural milestone (a vote, a filing) rather than the first public
  disclosure. Fix by correcting the announced date via the overlay.
- **Type B, opposition record attached to the wrong clock.** The announced
  date is right; the opposition event either belongs to a different project
  entity (usually an earlier sibling campus by the same operator) or carries
  a placeholder date. Fix by re-examining the event link, never by moving
  the announced date.

Type B matters for the model beyond data hygiene. Silently clamping a Type B
span to zero manufactures a fast-opposition signal on projects where
opposition actually arrived after announcement. Since
`days_to_first_opposition` is negatively associated with blocking, Type B
contamination pushes in the direction of the finding we already flagged as
underpowered. Treat this as a reason to keep that feature's interpretation
conservative until the linkage review is done.

## Dispositions

### Corrected this pass (overlay rows supplied)

**prj_25, Project Zodiac (Google), Allen County IN.** Recorded announced
2023-12-19, first opposition 2023-10-01, raw span -79 days. The December
date is the Allen County commissioners' annexation approval, not the
announcement. The project became public in October 2023, when Fort Wayne
released preliminary plans for an unnamed Fortune 100 tenant on an
892-acre parcel; Allen County Plan Commission documents carry October 2023
dates, and residents were already raising objections at the November 16
plan commission meeting. Corrected to 2023-10. Type A.
Sources: WANE 15 (Google confirmation, references October 2023 announcement);
Datacenter Dynamics ("first announced in October 2023"); WPTA 21Alive
coverage of the November 16, 2023 plan commission meeting.

**prj_323, Meta Hyperion, Richland Parish LA.** Recorded announced
2024-12-01 with day precision, first opposition 2024-07-01, raw span -153
days. Meta's own data center site states the Richland Parish announcement
was December 2024, and construction began that month. The announced date is
correct; precision should be month, not day. The July 2024 opposition record
predates any public disclosure of this project and cannot be opposition to
it. Type B, referred to linkage review.
Source: datacenters.atmeta.com Richland Parish page.

### Type B, referred to opposition-link review (do not move announced date)

**prj_277, Colossus 2 (xAI), Shelby County TN.** Recorded announced 2025
(year precision), first opposition 2024-08-26. Colossus 2 at Tulane Road was
disclosed by the Greater Memphis Chamber as a second facility, with
groundbreaking in mid-February 2025. The August 2024 opposition record
belongs to the original Colossus campus, where the turbine and air-permit
fight was already underway by mid-2024. This is a sibling-campus linkage
error. Recommended action: confirm whether the linked event references
Colossus 1, and if so re-point it; separately, tighten the announced date to
the Chamber disclosure once a dated source is located.

**prj_96, xAI MACROHARDRR, DeSoto County MS.** Recorded announced 2026-01
(the January 8, 2026 Reeves announcement, correct for the formal
announcement), first opposition 2025-11-24. Opposition in Southaven predates
MACROHARDRR and attaches to xAI's existing Southaven power plant and
adjacent operations; the Safe and Sound Coalition petition was running
before the project was named. Note also that Musk revealed the project on X
in December 2025, roughly a month before the state announcement, which is a
defensible earlier announced date if a dated capture of that post can be
sourced. Mixed A/B: linkage review first, then consider a December 2025
announced date with a citable source.

### Unresolved, needs a dated primary source

**prj_101, Wildwood Ranch, Jasper County MO.** Recorded announced 2026
(year precision), first opposition 2025-10-01. Reporting indicates the
rezoning and annexation proposal became public through a legal advertisement
roughly two weeks before the January 2026 Joplin Planning and Zoning
Commission meeting, which places first disclosure in late December 2025. The
October 2025 opposition date is not explained by any source found and may be
a placeholder. Needs the legal ad date or the P&Z agenda posting date, plus
verification of the opposition record's date.

**prj_158, Natelli New Hill Digital Campus, Wake County NC.** Recorded
announced 2025-09 (already an overlay correction citing INDY Week), first
opposition 2025-08-01, raw span -31 days. INDY Week reported the Apex filing
in early September 2025. WRAL reported speaking with Jordan Pointe residents
shortly after plans first became public, and neighbors were organizing at
that point, so a public disclosure in August 2025 through rezoning notice is
plausible but was not confirmed by a dated source in this pass. Needs the
Town of Apex application receipt date or the first dated community-meeting
notice. This is the only blocked-arm project in the set, so it carries more
weight per event than the others.

**prj_297, Jailhouse Studios, Hamilton County TN.** Recorded announced
2026-03, first opposition 2026-01-01. Not researched this pass.

**prj_113, Project Mica (Google / Diode Ventures), Clay County MO.**
Recorded announced 2026-02. Carried over from the prior session: the
recorded approval predates the announced date, producing a negative
decision span. Not researched this pass. Announced-date correction still
open.

## Suggested next actions

1. Run the opposition-link review for prj_323, prj_277, prj_96 before any
   further modeling pass that uses `days_to_first_opposition`.
2. Source the four unresolved announced dates (prj_101, prj_158, prj_297,
   prj_113) from primary municipal records rather than news coverage where
   possible: agenda postings, application receipt stamps, legal notices.
3. Re-run `feature_asymmetry_check.py` after corrections land, and re-run
   `outcome_model.py`; expect the coverage and precision tables to shift and
   report any change in the arrival-speed comparison honestly.
