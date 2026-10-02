# Implementation Plan notes: Deliverable Generation (spec 010)

This file is not yet the spec 010 plan; `/speckit-plan` for spec 010 replaces
the template sections around it. It holds one contract another spec needs
spec 010 to honor, recorded here (not in `spec.md`) per spec 013 US3.

## Note from spec 013 (terrain plates): the plate image slot

`scripts/render_location_report.py` and `scripts/render_county_pdf.py` accept
an optional `--plate outputs/plates/<id>.png`. When it is given:

1. **Slot.** The plate goes in one fixed image slot: full text width, directly
   under the report title block, aspect ratio preserved (plates render at
   3:2 by default, 3000x2000 px at 300 dpi = 10x6.67 in). The renderer never
   crops, recolors or annotates the image (spec 013 SC-002: zero hand edits).
2. **Caption.** Read from the sibling sidecar `outputs/plates/<id>.json`:
   `title` is the caption's first sentence (it already states the finding);
   `subtitle` follows. No caption text is composed by the report renderer.
3. **Credit.** The sidecar's `credits` lines (USGS 3DEP with product and access
   date; the cases line with the commit SHA) print under the caption in the
   report's credit style. They must match `DATA_NOTICES.md`.
4. **Refusals.** The renderer fails if the sidecar is missing, if its
   `commit_sha` is not an ancestor of the report's own commit, or if
   `preview` is true and the report is not itself a draft.
5. **Absent flag.** Without `--plate` the layout is unchanged; both variants
   must pass `validate.py --original` (spec 013 US3 independent test).
