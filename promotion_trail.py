#!/usr/bin/env python3
"""
promotion_trail.py

Keeps an append-only decision trail a record of what happened, rather than a
log of what kept not happening.

Every gated promoter in this repository writes a trail: census gap candidates,
facility candidates, harvested signals. They run on a schedule, and most runs
re-decide the same candidates the same way, so appending every decision on
every run buries the events under repetitions of the non-events. The facility
trail reached 40 rows describing 10 decisions inside a single day, and the
census gap trail had recorded "Cherokee County held" three times for one hold.

That is not only untidy. Both trails are now shown on the Data Operations page
as evidence that the platform maintains itself, and a decision count inflated
by re-statement overstates that evidence, which is the one thing this
repository is not allowed to do.

This module holds the decision and nothing else. Reading and writing stay with
the module that owns the file, so ownership under ARCHITECTURE.md is
unchanged and the layer audit still attributes each trail to one writer.

Usage
  from promotion_trail import new_decisions
  fresh, suppressed = new_decisions(existing, candidates,
                                    key_fields=("state", "county"),
                                    state_fields=("action", "reason"))

  python promotion_trail.py --selftest
"""

from __future__ import annotations

import argparse
import sys


def decision_key(row: dict, key_fields) -> tuple:
    """What identifies the thing being decided about, not the decision."""
    return tuple((row.get(f) or "").strip() for f in key_fields)


def decision_state(row: dict, state_fields) -> tuple:
    """The decision itself. A change here is the event worth recording."""
    return tuple((row.get(f) or "").strip() for f in state_fields)


def new_decisions(existing: list, candidates: list, key_fields,
                  state_fields) -> tuple[list, int]:
    """(rows to append, count suppressed as unchanged).

    A candidate is appended when it has no prior decision on record, or when
    its decision differs from the most recent one recorded for it. Re-deciding
    a candidate the same way is not an event and is not recorded.

    Order is preserved, and duplicates inside a single batch collapse to the
    first occurrence, so one run cannot record the same decision twice even if
    two streams produce it.
    """
    last: dict = {}
    for row in existing:
        last[decision_key(row, key_fields)] = decision_state(row, state_fields)

    fresh, suppressed = [], 0
    for row in candidates:
        key = decision_key(row, key_fields)
        state = decision_state(row, state_fields)
        if last.get(key) == state:
            suppressed += 1
            continue
        last[key] = state
        fresh.append(row)
    return fresh, suppressed


def selftest() -> int:
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    KEY = ("state", "county")
    STATE = ("action", "reason")

    held = {"state": "NC", "county": "Cherokee", "action": "held",
            "reason": "no usable date"}
    existing = [held]

    fresh, suppressed = new_decisions(existing, [dict(held)], KEY, STATE)
    check("re-deciding the same way records nothing",
          fresh == [] and suppressed == 1)

    promoted = dict(held, action="promoted", reason="")
    fresh, suppressed = new_decisions(existing, [promoted], KEY, STATE)
    check("a changed decision is recorded",
          len(fresh) == 1 and fresh[0]["action"] == "promoted")

    same_action_new_reason = dict(held, reason="source found but undated")
    fresh, _ = new_decisions(existing, [same_action_new_reason], KEY, STATE)
    check("the same action for a new reason is still an event",
          len(fresh) == 1)

    other = {"state": "IA", "county": "Story", "action": "held",
             "reason": "no usable date"}
    fresh, suppressed = new_decisions(existing, [dict(held), other], KEY, STATE)
    check("an unseen candidate is recorded even when others repeat",
          len(fresh) == 1 and fresh[0]["state"] == "IA" and suppressed == 1)

    fresh, suppressed = new_decisions([], [dict(held), dict(held)], KEY, STATE)
    check("a duplicate inside one batch collapses",
          len(fresh) == 1 and suppressed == 1)

    fresh, _ = new_decisions([], [dict(held)], KEY, STATE)
    check("an empty trail records everything", len(fresh) == 1)

    # The most recent decision is what counts, not the first: a candidate that
    # was held, promoted, then held again has genuinely changed twice, and the
    # third decision matches the second-to-last but not the last.
    history = [held, dict(held, action="promoted", reason="")]
    fresh, _ = new_decisions(history, [dict(held)], KEY, STATE)
    check("the latest decision is the one compared against", len(fresh) == 1)

    fresh, _ = new_decisions(history, [dict(held, action="promoted",
                                           reason="")], KEY, STATE)
    check("matching the latest decision records nothing", fresh == [])

    check("a missing field is treated as empty, not an error",
          new_decisions([{"state": "NC"}], [{"state": "NC"}], KEY, STATE)
          == ([], 1))

    ordered = [dict(other, county=f"C{i}") for i in range(3)]
    fresh, _ = new_decisions([], ordered, KEY, STATE)
    check("order is preserved",
          [r["county"] for r in fresh] == ["C0", "C1", "C2"])

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    sys.exit(selftest() if args.selftest else
             (print(__doc__.strip()) or 0))
