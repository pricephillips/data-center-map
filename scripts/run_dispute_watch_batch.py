"""Run dispute_watch.county_hits for one state and append rows to the
shared output CSV, since dispute_watch.py --out overwrites on each run.
The cache file already dedupes network calls across repeated invocations.
Usage: python3 scripts/run_dispute_watch_batch.py STATE [STATE ...]
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dispute_watch as dw

OUT = os.path.join(dw.ROOT, "data", "dispute_watch.csv")


def main() -> int:
    states = sys.argv[1:]
    if not states:
        print("usage: run_dispute_watch_batch.py STATE [STATE ...]")
        return 1

    all_rows = []
    if os.path.exists(OUT):
        with open(OUT, newline="", encoding="utf-8") as fh:
            all_rows = list(csv.DictReader(fh))

    cache = dw.Cache(dw.CACHE_PATH)
    for state in states:
        pairs = dw.jurisdictions_from_feed(dw.FEED, state.strip().upper())
        for i, (st, county) in enumerate(pairs):
            try:
                all_rows.extend(dw.county_hits(st, county, cache))
            except dw.ThrottledError as e:
                cache.save()
                dw.write_csv(OUT, all_rows, dw.OUT_COLS)
                print(f"stopping early: rate limit reached at {st} {county} ({e}). "
                      f"Set COURTLISTENER_API_TOKEN to raise the daily ceiling.")
                print(f"partial rows in {OUT}: {len(all_rows)}")
                return 2
            if i % 5 == 0:
                cache.save()
                dw.write_csv(OUT, all_rows, dw.OUT_COLS)  # incremental: survives interruption
        cache.save()
        dw.write_csv(OUT, all_rows, dw.OUT_COLS)
        print(f"{state}: {len(pairs)} jurisdictions done, {len(all_rows)} total rows so far")

    print(f"total rows in {OUT}: {len(all_rows)}")
    dw.leak_audit([OUT])
    return 0


if __name__ == "__main__":
    sys.exit(main())
