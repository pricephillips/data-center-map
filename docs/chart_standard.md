# Chart standard

**Spec**: 010 US5 | **Date**: 2026-10-02 | **Applies to**: `scripts/charts.py` and every chart in a deliverable

The platform adopts five rules from the Urban Institute Data Visualization Style
Guide (https://urbaninstitute.github.io/graphics-styleguide/). The guide is a
review reference only. `viz-palette.js` stays the canonical color source, and
`viz-palette.json` is its mirror for Python, rewritten by
`scripts/charts.py --sync-palette` and checked for drift by the charts selftest.

## Adopted rules

| Rule | What it means here | Where it is enforced |
|---|---|---|
| The title states the finding | "Spalding County sits in decile 8 of the national score distribution", not "Score distribution". The numbers in the title are facts read at run time. | Each chart function in `charts.py`. Terrain plates follow the same rule (spec 013). |
| Label series directly | The county is named on its marker, and outcome grades are named in the legend with their platform labels. Readers never match colors to a key alone. | `charts.py` marks and text layers |
| Every chart has a source line | It names the files, the data-as-of date and the commit. | `charts._source_line` |
| Show uncertainty as a range | Scores are drawn with their intervals (a band or a line), never as a bare point. | `score_distribution`, `peer_comparison` |
| No chart junk | No gridlines, no view border, no 3D, no gradients. | `charts._base` |

## Palette review against the guide

The palette was reviewed on 2026-10-02 against the guide's color advice
(categorical colors distinguishable, sequential ramps ordered by lightness,
emphasis through one contrasting hue). It needs no change:

- **Outcome colors** (`OUTCOME_COLOR`): the unverified grades are lighter tints
  of their confirmed hues. The distinction stays visible without implying a
  separate category.
- **Sequential ramp** (`INFERNO`): it is perceptually ordered, and the
  `SEQ_FLOOR` keeps the lowest values off pure black.
- **Diverging ends** (`DIV_NEG`, `DIV_POS`): the orange end marks the focus
  county in report charts, and the slate midpoint draws comparison counties.

## Not adopted

- The guide's house typeface. Reports use Georgia (Hawthorn styles).
- Its own color palette. `viz-palette.js` is canonical.
