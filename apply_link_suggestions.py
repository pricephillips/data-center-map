"""
apply_link_suggestions.py — auto-apply triage suggestions to the manual link file.

2026-08-12 decision: triage_accelerator's suggest_confirm / suggest_reject
rows (high AND medium confidence) no longer wait for a human to copy them;
they are applied directly to data/project_links_manual.csv. needs_review rows
remain manual — that bucket is exactly the set the accelerator could not
decide on evidence.

Defensibility rules:
  - Reviewer decisions always win: a pair already present in the manual file
    is never overwritten or duplicated.
  - Every auto-applied row carries its full evidence string in the note
    column, prefixed "auto_applied", so applied decisions stay auditable and
    distinguishable from human ones.

Runs BEFORE project_resolution.py in pipeline.yml, consuming the draft the
PREVIOUS run committed, so a suggestion becomes a confirmed/rejected link on
the next pipeline pass and the resolver picks it up in the same run.

Run from repo root:  python3 apply_link_suggestions.py
Self-test:           python3 apply_link_suggestions.py --selftest
"""

from __future__ import annotations

import csv
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DRAFT_CSV = os.path.join(ROOT, "data", "project_links_manual_draft.csv")
MANUAL_CSV = os.path.join(ROOT, "data", "project_links_manual.csv")

MANUAL_FIELDS = ["opp_id", "project_id", "action", "note"]
_ACTION_MAP = {"suggest_confirm": "confirm", "suggest_reject": "reject"}


def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_applied_rows(draft: list[dict], existing_pairs: set[tuple[str, str]]) -> list[dict]:
    out = []
    seen = set(existing_pairs)
    for r in draft:
        action = _ACTION_MAP.get((r.get("action") or "").strip())
        if action is None:
            continue  # needs_review stays manual
        pair = ((r.get("opp_id") or "").strip(), (r.get("project_id") or "").strip())
        if not pair[0] or not pair[1] or pair in seen:
            continue
        seen.add(pair)
        evidence = (r.get("corroborations") if action == "confirm"
                    else r.get("red_flags")) or ""
        conf = (r.get("suggestion_confidence") or "").strip()
        out.append({"opp_id": pair[0], "project_id": pair[1], "action": action,
                    "note": f"auto_applied ({conf}): {evidence}".strip()})
    return out


def main() -> int:
    draft = load_csv(DRAFT_CSV)
    if not draft:
        print("link auto-apply: no draft, nothing to do")
        return 0
    manual = load_csv(MANUAL_CSV)
    existing = {((r.get("opp_id") or "").strip(), (r.get("project_id") or "").strip())
                for r in manual}
    applied = build_applied_rows(draft, existing)
    if applied:
        new_file = not os.path.exists(MANUAL_CSV)
        with open(MANUAL_CSV, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=MANUAL_FIELDS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerows(applied)
    n_confirm = sum(1 for r in applied if r["action"] == "confirm")
    print(f"link auto-apply: {len(draft)} drafted -> {len(applied)} applied "
          f"({n_confirm} confirm, {len(applied) - n_confirm} reject); "
          f"needs_review and already-decided pairs untouched")
    return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    failures = []

    def check(name, ok):
        print(("  PASS  " if ok else "  FAIL  ") + name)
        if not ok:
            failures.append(name)

    draft = [
        {"opp_id": "opp_a", "project_id": "prj_1", "action": "suggest_confirm",
         "suggestion_confidence": "high", "corroborations": "summary_names_project(2/2)",
         "red_flags": ""},
        {"opp_id": "opp_b", "project_id": "prj_2", "action": "suggest_confirm",
         "suggestion_confidence": "medium", "corroborations": "city_in_project_towns",
         "red_flags": ""},
        {"opp_id": "opp_c", "project_id": "prj_3", "action": "suggest_reject",
         "suggestion_confidence": "high", "corroborations": "",
         "red_flags": "opposition_predates_announcement_by_200d"},
        {"opp_id": "opp_d", "project_id": "prj_4", "action": "needs_review",
         "suggestion_confidence": "", "corroborations": "", "red_flags": ""},
        {"opp_id": "opp_e", "project_id": "prj_5", "action": "suggest_confirm",
         "suggestion_confidence": "high", "corroborations": "x", "red_flags": ""},
    ]
    existing = {("opp_e", "prj_5")}

    rows = build_applied_rows(draft, existing)
    got = {(r["opp_id"], r["project_id"]): r for r in rows}

    check("high-confidence confirm is applied",
          got.get(("opp_a", "prj_1"), {}).get("action") == "confirm")
    check("medium-confidence confirm is applied",
          got.get(("opp_b", "prj_2"), {}).get("action") == "confirm")
    check("reject suggestion is applied as reject",
          got.get(("opp_c", "prj_3"), {}).get("action") == "reject")
    check("needs_review is NOT applied", ("opp_d", "prj_4") not in got)
    check("existing reviewer decision is never overwritten",
          ("opp_e", "prj_5") not in got)
    check("note carries confidence and evidence",
          "auto_applied (high)" in got.get(("opp_a", "prj_1"), {}).get("note", "")
          and "summary_names_project" in got.get(("opp_a", "prj_1"), {}).get("note", ""))
    check("reject note carries the red flag",
          "opposition_predates" in got.get(("opp_c", "prj_3"), {}).get("note", ""))

    print(f"selftest: {'OK' if not failures else f'{len(failures)} FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
