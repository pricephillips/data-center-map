"""
triage_accelerator.py — pre-triage for the link-review worklist.

Re-scores every LINK_CANDIDATE in data/project_link_review.csv using
corroborating evidence the first-pass matcher does not consult (event summary
text, city/town alignment, date plausibility), then writes a ready-to-edit
draft of the manual override file, sorted most-confident-first.

Additive only. Reads existing files, writes one NEW file:

  data/project_links_manual_draft.csv

Defensibility rules honored:
  - NOTHING here is auto-applied. The resolver only reads
    data/project_links_manual.csv; this draft is a separate file. A candidate
    becomes a confirmed link only when a human moves its row into the manual
    file. The draft's `action` column holds SUGGESTIONS
    (suggest_confirm / suggest_reject / needs_review), which are invalid
    values for the manual file by construction — a copied row must have its
    action edited to `confirm` or `reject`, forcing a deliberate decision.
  - Every suggestion carries its full evidence string for audit.

Suggestion logic:
  suggest_confirm  original signal + >=2 independent corroborations and no
                   red flags
  suggest_reject   date implausibility (opposition predates announcement by
                   more than DATE_SLACK_DAYS with day-precision announced
                   date) or distant geo with zero text corroboration
  needs_review     everything else (human judgment required)

Run from repo root:  python3 triage_accelerator.py
Depends on project_resolution.py outputs (run it first).
"""

from __future__ import annotations

import csv
import os
import re
import sys
from datetime import date

import project_resolution as pr

ROOT = os.path.dirname(os.path.abspath(__file__))
REVIEW_CSV = os.path.join(ROOT, "data", "project_link_review.csv")
OUT_DRAFT = os.path.join(ROOT, "data", "project_links_manual_draft.csv")

DATE_SLACK_DAYS = 90         # opposition earlier than announced - slack => implausible
DISTANT_KM = 40.0            # geo beyond this with no text corroboration => reject hint


def norm_tokens_in(text: str, toks: frozenset[str]) -> int:
    """How many of the given tokens appear in the text (word-boundary)."""
    if not toks or not text:
        return 0
    low = text.lower()
    return sum(1 for t in toks if re.search(rf"\b{re.escape(t)}\b", low))


GEO_GENERIC = frozenset({"township", "county", "city", "town", "village",
                         "charter", "north", "south", "east", "west"})


def main() -> int:
    if not os.path.exists(REVIEW_CSV):
        print("ERROR: data/project_link_review.csv missing — run project_resolution.py first")
        return 1

    events = {e["opp_id"]: e
              for e in (pr.prep_event(r) for r in pr.load_csv(pr.OPPOSITION_CSV))}
    projects = {p["project_id"]: p
                for p in pr.prep_projects(pr.load_csv(pr.PROPOSALS_CSV))}

    candidates = [r for r in pr.load_csv(REVIEW_CSV)
                  if r["review_type"] == "LINK_CANDIDATE"]

    rows = []
    for c in candidates:
        ev, proj = events.get(c["opp_id"]), projects.get(c["project_id"])
        if ev is None or proj is None:
            continue
        raw = ev["raw"]
        summary = " ".join([raw.get("Summary", ""), raw.get("Incident", ""),
                            raw.get("Entity", "")])
        towns = (proj["raw"].get("towns", "") or "") + " " + (proj["raw"].get("address", "") or "")

        corroborations, flags = [], []

        name_toks = proj["name_toks"] - GEO_GENERIC
        n_name = norm_tokens_in(summary, name_toks)
        if name_toks and n_name >= max(1, (len(name_toks) + 1) // 2):
            corroborations.append(f"summary_names_project({n_name}/{len(name_toks)})")
        n_co = norm_tokens_in(summary, proj["co_toks"])
        if n_co:
            corroborations.append(f"summary_names_company({n_co})")

        city = pr.norm_county(raw.get("City", ""))
        if city and re.search(rf"\b{re.escape(city)}\b", towns.lower()):
            corroborations.append("city_in_project_towns")

        # date plausibility (only judged with day-precision announced dates)
        date_note = ""
        if (proj["announced_precision"] == "day"
                and re.match(r"^\d{4}-\d{2}-\d{2}$", ev["date"])):
            gap = (date.fromisoformat(ev["date"])
                   - date.fromisoformat(proj["announced_date"])).days
            if gap < -DATE_SLACK_DAYS:
                flags.append(f"opposition_predates_announcement_by_{-gap}d")
            else:
                date_note = f"date_plausible(gap {gap}d)"
                corroborations.append(date_note)

        dist = pr.parse_float(c.get("distance_km", ""))
        if dist is not None and dist > DISTANT_KM and not corroborations:
            flags.append(f"distant_{dist:.0f}km_no_text_corroboration")

        if flags and not corroborations:
            action, conf = "suggest_reject", "high" if len(flags) > 1 else "medium"
        elif len([x for x in corroborations if x != date_note]) >= 2 and not flags:
            action, conf = "suggest_confirm", "high"
        elif len(corroborations) >= 2 and not flags:
            action, conf = "suggest_confirm", "medium"
        else:
            action, conf = "needs_review", ""

        rows.append({
            "opp_id": c["opp_id"],
            "project_id": c["project_id"],
            "action": action,
            "note": "",
            "suggestion_confidence": conf,
            "corroborations": "; ".join(corroborations),
            "red_flags": "; ".join(flags),
            "first_pass_signals": c.get("signals", ""),
            "distance_km": c.get("distance_km", ""),
            "opp_incident": c.get("opp_incident", ""),
            "opp_date": c.get("opp_date", ""),
            "project_name": c.get("project_name", ""),
            "opp_state": c.get("opp_state", ""),
        })

    order = {"suggest_confirm": 0, "suggest_reject": 1, "needs_review": 2}
    conf_order = {"high": 0, "medium": 1, "": 2}
    rows.sort(key=lambda r: (order[r["action"]], conf_order[r["suggestion_confidence"]]))

    with open(OUT_DRAFT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    counts = Counter((r["action"], r["suggestion_confidence"]) for r in rows)
    print(f"drafted {len(rows)} candidates -> {os.path.relpath(OUT_DRAFT, ROOT)}")
    for (a, cf), n in sorted(counts.items()):
        print(f"  {a}{f' ({cf})' if cf else ''}: {n}")
    print("Workflow: review the draft top-down; copy accepted rows into "
          "data/project_links_manual.csv and set action to confirm/reject.")

    pat = re.compile(r'\b(win|wins|loss|losses|lost)\b', re.IGNORECASE)
    hits = [f"line {i}" for i, l in enumerate(open(OUT_DRAFT, encoding="utf-8"), 1)
            if pat.search(l)]
    if hits:
        print(f"LEAK AUDIT: scorekeeping terms at {', '.join(hits[:10])} "
              "(may originate in source incident text — inspect before publishing)")
    else:
        print("leak audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
