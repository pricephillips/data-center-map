# Contract: template fields

Every location report template binds to these top-level fields, the base
template and every client template alike. A template may not reference any
other top-level name. Required fields are listed in `templates/manifest.json`.
Every value is a string unless a type is given, and every number is already
formatted (Principle VI).

| Field | Shape | Required |
|---|---|---|
| `report` | `title`, `county_name`, `state_name`, `state`, `fips`, `as_of`, `commit_short`, `draft` (bool), `client` | yes |
| `define` | callable: `define("calibrated_score")` returns "calibrated score (definition)" on first use and "calibrated score" after | yes |
| `plate` | `None`, or `image` (InlineImage), `title` (the sidecar title, closed with a period if it has none), `subtitle`, `credits` (list) | yes (the slot must exist, so `--plate` works with every template) |
| `history` | `enacted` (bool), `unitemized` (bool), `rows` (`date`, `place`, `level`, `type`, `status`, `source` RichText), `census_rows` (`date`, `instrument`, `status`, `source`) | yes |
| `score` | `calibrated`, `interval_low`, `interval_high`, `decile`, `pct_national`, `pct_state`, `peer_median`, `peer_n` | yes |
| `charts` | `None`, or `score_distribution` (InlineImage), `score_distribution_title` | no |
| `cases` | `count`, `rows` (`date`, `place`, `type`, `status`, `outcome`, `project`, `source` RichText) | yes |
| `neighbors` | `count`, `n_enacted`, `rows` (`county`, `state`, `enacted`, `decile`, `cases`) | yes |
| `sources` | `files` (`path`, `sha`), `notice`, `interval_notice`, `unitemized_notice` | yes |

Rules for template authors:

- Use `{%p if ... %}`, `{%tr for ... %}` and `{{r field }}`. Fields with
  RichText values (`source`) must use the `r` form.
- Type no digits. The builder's and the renderer's selftests fail a template
  whose literal text contains a digit outside a tag.
- When `history.enacted` is false, the history section prints only the
  sentence the base template uses ("No enacted restriction is on record for
  ..."), with no count and no rate.
- Do not name a refused column (data-model.md). The renderer checks every
  attribute a template reads.
