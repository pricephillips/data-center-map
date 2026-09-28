# Contract: `data/master_diff_summary.md`

Layer E. The sole writer is `master_diff.py`. The file is regenerated on every
pipeline run and is never hand-edited.

```markdown
# Source-of-truth diff

Compared `<base short sha>` to `<head short sha or "working tree">`, generated <YYYY-MM-DD HH:MM> UTC.

## Row counts (master_opposition.csv)

| Added | Removed | Modified |
|---|---|---|
| <n> | <n> | <n> |

## Outcome field changes (clean feed)

| Row | Column | Before | After |
|---|---|---|---|
| <Incident> (<State>, <Date>) | Community Outcome | <old> | <new> |

<or the single line "No changes to Community Outcome, Status, or outcome_defensible.">

## Detail

<daff highlighter rows as a markdown table, at most 200; then
"<k> further rows not shown." when truncated>
```

Rules:

- LF line endings, and no em-dashes. The module writes ASCII punctuation only.
- Cell values are escaped for markdown tables (`|` becomes `\|`, and newlines
  become spaces).
- There is no scorekeeping vocabulary in composed text. Outcome values are
  transported record content, and their vocabulary is the outcome ladder.
- In the no-prior-revision and no-changes states, the file contains the header
  plus one line saying which state applies.
