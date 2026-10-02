# Deliverables

Client-facing markdown lives here so that the spec 004 prose rules apply. Vale
(`.vale.ini`, Hawthorn style) and the em-dash pre-commit hook cover
`deliverables/**/*.md`, and `leak_audit.py` treats `deliverables/*` as
generated output in its blocking tier.

- `location_reports/` holds the markdown twin of each Word location report.
  `scripts/render_location_report.py` writes it beside the Word file, so the
  report's prose is linted exactly as rendered. Re-render rather than edit:
  every number comes from platform files at run time.
- Hand-written briefs can sit in their own subfolder.
  `scripts/md_to_docx.sh` turns any of them into Word with the Hawthorn
  reference document.

A brief is final only when Vale, the em-dash check and the leak audit pass.
For a rendered report, the re-derivation check
(`render_location_report.py --verify`) must also report zero mismatches.
