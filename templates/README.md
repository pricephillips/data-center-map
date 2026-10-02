# Templates

| File | What it is | Made by |
|---|---|---|
| `location_report.docx` | Client-neutral location report, Hawthorn styles, tag `location_report-v1` | `scripts/build_report_templates.py` (do not edit in Word) |
| `reference.docx` | Pandoc reference document, same styles | `scripts/build_report_templates.py` |
| `county_profile.html.j2` | HTML template for county profile PDFs | hand-edited |
| `manifest.json` | Section order (confirmed 2026-10-02), required and optional fields | hand-edited |

## Client templates

A client template is optional. It lives at
`templates/clients/<client>/location_report.docx` and is selected with
`render_location_report.py --client <client>`. The client name may use only
`a-z`, `0-9`, `-` and `_`.

A client template may change wording, styles, branding and section order. It
binds the same fields as the base template
(`specs/010-deliverable-generation/contracts/template_fields.md`), so data
binding never changes per client. The renderer refuses a client template in
any of these cases:

- it omits a required field from `manifest.json`;
- it names a field outside the contract;
- it reads a refused column (group-level outcome columns or county rates);
- it types a number into its text;
- its rendered text fails the vocabulary, em-dash or first-use definition
  checks.

Practical rules for building one in Word:

- Start from a copy of `location_report.docx`.
- Keep each `{{ ... }}` tag inside one run: type it in one go, or paste it as
  plain text.
- Use `{{r row.source }}` for link cells.
- Keep the `{%p if plate %}` block, so `--plate` works with every template.

The 2026-09-23 Vantage location report becomes such a template
(`templates/clients/vantage/`) when that engagement needs it. It is not the
base.
