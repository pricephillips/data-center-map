# Config Contract: `configs/source_durability.json`

This file is edited by hand; no process writes it. It is read by
`source_archive.py`, `article_extract.py`, `event_dedupe.py`,
`signal_harvest.py` and `untagged_triage.py`. A missing key falls back to the
default shown.

```json
{
  "archive": {
    "max_lookups": 400,
    "max_saves": 150,
    "cdx_sleep_s": 1.0,
    "save_sleep_s": 6.0,
    "backoff_s": [10, 30, 90],
    "timeout_s": 30,
    "recheck_after_days": 1,
    "max_attempts": 3,
    "checkpoint_every": 25,
    "skip_hosts": ["news.google.com"],
    "max_runtime_s": 1800,
    "cdx_fail_streak": 3
  },
  "extract": {
    "timeout_s": 10,
    "max_bytes": 2000000,
    "max_fetch_per_run": 150,
    "triage_hint_limit": 20,
    "triage_timeout_s": 8,
    "thin_text_chars": 500,
    "lead_words": 60,
    "min_date": "2010-01-01"
  },
  "dedupe": {
    "threshold": 0.7,
    "num_perm": 128,
    "shingle_words": 5,
    "min_title_tokens": 6,
    "max_days_apart": 3,
    "seed": 1
  },
  "measure": {
    "sample_size": 30,
    "agree_days": 1,
    "target_rate": 0.8
  }
}
```

`dedupe.threshold` is the config value from the spec's edge case. The
selftest pins behavior at 0.7 whatever the file says: it passes its own
config.

`archive.max_runtime_s` (added 2026-10-02) is a wall-clock budget, checked
between URLs. It stops the batch with `stop_reason=time_budget` and still
writes the outputs, so the job reaches its commit step inside
`source-archive.yml`'s 45-minute timeout.

`archive.cdx_fail_streak` (added 2026-10-02): after this many consecutive CDX
failures, the run stops calling CDX and asks the Wayback availability API
directly for every remaining URL. The manifest records `cdx_skipped`.
