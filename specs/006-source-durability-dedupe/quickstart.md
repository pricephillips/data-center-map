# Quickstart: validating spec 006

Run everything from the repo root. Nothing below needs network access except
the "CI only" rows.

## Prerequisites

```bash
uv pip install --system -c requirements/ci.txt trafilatura datasketch   # for the full selftests
```

Without the two packages, the selftests still pass and print `SKIP` lines.
That is the configuration of the blocking discovery run in `pipeline.yml`.

## Scenarios

| # | Story | Command | Expected |
|---|---|---|---|
| 1 | US1 | `python source_archive.py --selftest` | PASS lines for: 3 found and 2 requested from the five-URL fixture; a non-200 capture is not archived; Google News is `unresolved_redirect`; 429 backs off, stops, records `stop_at_url`, and the next run resumes; CSV LF-only. |
| 2 | US1 | `python source_archive.py --dry-run` | Prints queue size (5,625 URLs on first run) and the capped batch. No writes. |
| 3 | US2 | `python article_extract.py --selftest` | Dates from the three HTML fixtures equal their published dates; a paywall stub is `thin_text=yes`; future and pre-2010 dates are blank. |
| 4 | US2 | `python promote_signal_candidates.py --selftest` | A candidate with `date_hint` 2026-01-02 and `seen_date` 2026-01-05 promotes with `Date` 2026-01-05; a promoted cluster representative writes `cluster_member` report rows. |
| 5 | US2 | `python untagged_triage.py --selftest` | The `date_hint` column is appended last; a cached hint fills it without a fetch. |
| 6 | US3 | `python event_dedupe.py --selftest` | Three fixture articles give 2 clusters at 0.7. Recurring same-domain titles on different days stay apart. Short titles do not merge on title alone. |
| 7 | US3 | `python signal_harvest.py --selftest` | Syndicated copies collapse to one row with `cluster_members`; `known_urls` includes `cluster_member` URLs. |
| 8 | US3 | `python status_resolution.py --selftest` | Syndicated copies in master are proposed as `supersede` with `cluster_id`; master is unchanged. |
| 9 | SC-002 | `python status_resolution.py` | Prints 64 syndicated copies proposed as superseded (2026-09-29 data; research D6). |
| 10 | All | `python -m pytest tests/test_selftests.py -q` | Green with and without the packages. |
| 11 | Gates | `python leak_audit.py --tier blocking`; `python layer_audit.py --strict --no-write`; `python layer_audit.py --check-gitattributes`; `pre-commit run --all-files`; `actionlint` | All clean. |
| 12 | SC-001 (CI only) | `source-archive.yml` nightly | `coverage` in `data/source_archive_manifest.json` rises each run toward 0.90 by run 30. |
| 13 | SC-003 (CI only) | `update-opposition-csv.yml` runs `article_extract.py --measure` | `data/date_hint_agreement.md` shows the verdict under the rule fixed in research D10. |

## SC-002 replay on the live queue

```bash
python - <<'PY'
import csv, event_dedupe as ed
q = list(csv.DictReader(open("data/signal_candidates.csv", encoding="utf-8")))
items = [dict(id=i, title=r["title"], date=r["seen_date"], domain=r["domain"],
              state=r["state"]) for i, r in enumerate(q)]
cl = ed.cluster(items)
print(len(q), "->", len(cl))   # 82 -> 48 on 2026-09-29
PY
```
