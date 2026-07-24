# Addendum: Duplicate Suppression Mechanism, 2026-07-23

Closes item 2 from the negative-audit memo's next-session queue (pulled forward as a small mechanical task).

## Mechanism

`data/project_duplicates.csv` (keep_id, drop_id, reason, source) is a new manual file read by `project_resolution.py`, alongside the existing manual links and decision dates files. The drop_id project is removed from the frame BEFORE event resolution, so any events re-resolve against surviving projects and the suppression propagates automatically: lifecycles, links, baseline universe (control_group iterates lifecycles), the negative-audit frame, and every model downstream. Validation on load: both ids prj_-prefixed and distinct, reason and source required; invalid rows are reported and ignored, never guessed. Absent file is a no-op, so the change is fully backward-compatible.

First registry entry: keep prj_331, drop prj_76 (Project Splitrock / Howell Township; prj_331 carries the linked event and the verified 2025-12-08 decision date; prj_76 was the scraped duplicate with no links).

## Verified locally, full chain rerun

- projects 333 to 332; prj_76 absent from lifecycles, baseline universe, and the audit frame (176 to 175); prj_331 intact.
- Outcome-model frame UNCHANGED (n=83, 25 blocked): prj_76 had no linked events so it never sat in the frame; the duplicate was inflating counts only in the unopposed/universe side.
- Landmark frames and decision-date worklist (52) unchanged, as expected.
- The prj_76 audit coding row is retained as an audit trail, annotated RESOLVED AS DUPLICATE; the audit report will list it as out-of-frame, which is correct and now self-explaining. Coded tally reads 9 of 175.
- Leak audit clean; the two regex hits in project_resolution.py are pre-existing (a capacity-merge comment and the audit regex literal itself), verified against the unmodified file.

## Push set and placement

Replace: `project_resolution.py` (repo root; +64 lines, additive).
New: `data/project_duplicates.csv`.
Replace (regenerated): `data/negative_audit_codings.csv`, `data/negative_audit_worklist.csv`, `data/negative_audit_report.md`.
This memo: `data/`.

prj_61 (probable Arkansas miscoding) is NOT entered in the duplicates file; it is a misattribution, not a duplicate, and needs its own verification pass before any correction.
