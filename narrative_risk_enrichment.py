"""
narrative_risk_enrichment.py

Extends site_screener.py's Community & Narrative Risk output with four
additions that use data already in master_opposition_clean.csv and
group_registry.csv. No new data collection. See vantage_ppd_scoring_spec.md
section 3 for the rationale behind each addition.

  1. narrative_trajectory   - recency-weighted concern tags (rising vs legacy)
  2. escalation_ladder       - highest activation-channel rung reached
  3. spillover_check         - in-county groups also active regionally
  4. override_counterfactual - pre-override component score, when the
                                community override rule fires

Designed to be called from site_screener.py / a Vantage scorecard renderer
with the same `nearby` list (list of (weight, distance, event) tuples) and
`opposition` records it already builds. Stdlib only.

Integration point for site briefs: render_narrative_detail() returns a
markdown block that site_screener.py's render_brief() inserts before the
Basis and Limits section. The override counterfactual is not part of that
block because the screener has no override rule; it exists for the Vantage
scorecard renderer, where the community override lives.

Usage (selftest against real data):
  python narrative_risk_enrichment.py --selftest
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Concern tags. Mirrors the qc_concerns vocabulary in CODEBOOK.md.
# ---------------------------------------------------------------------------

CONCERN_COLUMNS = {
    "noise": "is_noise",
    "water": "is_water",
    "energy_cost": "is_ratepayer",
    "grid_strain": "is_grid_energy",
    "environment": "is_environmental",
    "land_use": "is_farmland",
    "property_value": "is_property_values",
    "traffic": "is_traffic",
    "air_quality": "is_air_quality",
    "transparency": "is_transparency",
    "design_standards": "is_design_standards",
    "tax_incentive": "is_tax_incentive",
    "anti_ai": "is_anti_ai",
}

HALF_LIFE_MONTHS = 24.0
UNDATED_WEIGHT = 0.25
READING_THRESHOLD = 0.02

# Escalation ladder, low to high. First matching keyword set wins per event;
# an event can register on multiple rungs (e.g. a petition that also drew
# public comment), the ladder reports the highest rung reached across ALL
# events, not per-event classification.
ESCALATION_LADDER = [
    ("public_comment", ("public_comment", "public comment")),
    ("petitions", ("petition",)),
    ("elected_official_engagement", ("elected official", "board of supervisors",
                                     "county commission", "city council")),
    ("litigation", ("lawsuit", "litigation", "court", "injunction", "appeal")),
    ("standing_campaign_infrastructure", ("website", "facebook", "instagram",
                                          "campaign")),
]


def _recency_weight(d, today=None):
    if d is None:
        return UNDATED_WEIGHT
    today = today or date.today()
    months = max(0.0, (today - d).days / 30.44)
    return 0.5 ** (months / HALF_LIFE_MONTHS)


def _parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%m/%d/%Y", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y")


# ---------------------------------------------------------------------------
# 1. Narrative trajectory
# ---------------------------------------------------------------------------

def narrative_trajectory(county_events, today=None, top_n=2):
    """county_events: list of raw dict rows (Opposition Groups schema) for
    one county's in-county records. Returns ranked list of
    {concern, weighted_share, alltime_share, reading} for the top_n concerns
    by weighted share. reading is 'rising' when the recency-weighted share
    exceeds the all-time share by more than READING_THRESHOLD, 'fading' when
    it trails by more than the threshold, and 'steady' in between. The
    threshold keeps sub-point differences from being narrated as movement."""
    today = today or date.today()
    weighted = {c: 0.0 for c in CONCERN_COLUMNS}
    counts = {c: 0 for c in CONCERN_COLUMNS}
    total_weight = 0.0
    total_count = 0
    for r in county_events:
        d = _parse_date(r.get("Date") or r.get("date"))
        w = _recency_weight(d, today)
        hit_any = False
        for concern, col in CONCERN_COLUMNS.items():
            if _truthy(r.get(col)):
                weighted[concern] += w
                counts[concern] += 1
                hit_any = True
        if hit_any:
            total_weight += w
            total_count += 1
    if total_weight == 0 or total_count == 0:
        return []
    ranked = sorted(CONCERN_COLUMNS,
                     key=lambda c: -weighted[c])[:top_n]
    out = []
    for c in ranked:
        if counts[c] == 0:
            continue
        w_share = weighted[c] / total_weight
        a_share = counts[c] / total_count
        delta = w_share - a_share
        if delta > READING_THRESHOLD:
            reading = "rising"
        elif delta < -READING_THRESHOLD:
            reading = "fading"
        else:
            reading = "steady"
        out.append({
            "concern": c,
            "weighted_share": round(w_share, 3),
            "alltime_share": round(a_share, 3),
            "reading": reading,
        })
    return out


def format_narrative_trajectory(ranked):
    if not ranked:
        return "Narrative trajectory: insufficient tagged records to rank."
    lines = ["Narrative trajectory (recency-weighted):"]
    for row in ranked:
        lines.append(
            f"  {row['concern']:<16} {row['reading']:<7}  "
            f"({row['weighted_share']:.0%} of weighted mentions vs "
            f"{row['alltime_share']:.0%} of all-time)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Escalation ladder
# ---------------------------------------------------------------------------

def escalation_ladder(county_events):
    """Returns (highest_rung, rungs_present) using free-text scan over
    Opposition Type, Opposition Website/Facebook/Instagram, Petition URL,
    and Summary fields already on each record."""
    present = []
    for rung, keywords in ESCALATION_LADDER:
        hit = False
        for r in county_events:
            haystack = " ".join(str(r.get(f, "") or "") for f in (
                "Opposition Type", "Summary", "Petition URL",
                "Opposition Website", "Opposition Facebook",
                "Opposition Instagram")).lower()
            if any(k in haystack for k in keywords):
                hit = True
                break
        if hit:
            present.append(rung)
    highest = present[-1] if present else None
    return highest, present


def format_escalation_ladder(highest, present):
    if not present:
        return "Escalation ladder: no activation channel on record."
    channel_labels = {
        "public_comment": "public hearings",
        "petitions": "petitions",
        "elected_official_engagement": "elected official engagement",
        "litigation": "litigation",
        "standing_campaign_infrastructure": "standing campaign website",
    }
    full = ", ".join(channel_labels[p] for p in present)
    return (f"Escalation ladder reached: {channel_labels[highest]} "
            f"(highest rung on record). Full channel set: {full}.")


# ---------------------------------------------------------------------------
# 3. Spillover check
# ---------------------------------------------------------------------------

def spillover_check(in_county_groups, regional_groups):
    """in_county_groups, regional_groups: sets of canonicalized group name
    strings (lowercase). Returns list of {group, reading}."""
    out = []
    for g in sorted(in_county_groups):
        reading = ("active local capacity" if g in regional_groups
                   else "in-county only")
        out.append({"group": g, "reading": reading})
    regional_only = sorted(regional_groups - in_county_groups)
    return out, regional_only


def format_spillover_check(overlap_rows, regional_only):
    if not overlap_rows and not regional_only:
        return "Spillover check: no named groups on record."
    lines = ["Spillover check:"]
    for row in overlap_rows:
        if row["reading"] == "active local capacity":
            lines.append(
                f"  {row['group'].title()} is named on an in-county record "
                f"AND within the regional radius elsewhere - treat as active "
                f"local capacity, not adjacent-county context.")
    if regional_only:
        shown = ", ".join(g.title() for g in regional_only[:5])
        more = f" and {len(regional_only) - 5} more" if len(regional_only) > 5 else ""
        lines.append(f"  Regional-only (context, not evidence of influence "
                     f"over this site): {shown}{more}.")
    if len(lines) == 1:
        lines.append("  No in-county group also appears regionally.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Override counterfactual
# ---------------------------------------------------------------------------

def override_counterfactual(pre_override_score, pre_override_label,
                            override_applied):
    if not override_applied:
        return None
    return (f"Community & Narrative Risk: 1 (override applied)\n"
            f"  Pre-override component score, for reference: "
            f"{pre_override_score} (would have been \"{pre_override_label}\" "
            f"absent the override rule).")


# ---------------------------------------------------------------------------
# Site-brief markdown block (integration point for site_screener.py)
# ---------------------------------------------------------------------------

def _bare_county(name):
    n = (name or "").strip().lower()
    for suf in (" county", " parish", " borough"):
        if n.endswith(suf):
            n = n[: -len(suf)]
            break
    return n.strip()


def _groups_of(recs):
    out = set()
    for r in recs:
        raw = r.get("qc_groups_canonical") or r.get("Opposition Groups") or ""
        for g in re.split(r"[;|]", raw):
            if g.strip():
                out.add(g.strip().lower())
    return out


def render_narrative_detail(county, state_abbrev, nearby_recs, context_recs,
                            today=None):
    """Markdown block for site briefs. nearby_recs: raw opposition rows within
    the screener's 25 mi activity radius. context_recs: raw rows within the
    50 mi context radius (superset of nearby_recs). Returns a list of markdown
    lines with no trailing blank line. Everything here is descriptive; the
    disclosure in the brief covers it."""
    lines = ["## Community and Narrative Detail", ""]

    if not nearby_recs:
        lines.append("No opposition records within the 25 mi activity radius; "
                     "narrative trajectory and escalation ladder are not "
                     "computed. Regional group context below still reflects "
                     "the 50 mi radius.")
    else:
        ranked = narrative_trajectory(nearby_recs, today)
        if ranked:
            lines.append("Narrative trajectory, 25 mi radius. Share of "
                         "recency-weighted concern mentions vs share of "
                         "all-time mentions; rising means the recent share "
                         "exceeds the all-time share by more than 2 points, "
                         "fading means it trails by more than 2 points, "
                         "steady means neither:")
            lines.append("")
            for row in ranked:
                lines.append(f"- {row['concern']}: {row['reading']} "
                             f"({row['weighted_share']:.0%} weighted vs "
                             f"{row['alltime_share']:.0%} all-time)")
            lines.append("")
        else:
            lines.append("- Narrative trajectory: records within 25 mi carry "
                         "no concern tags, so no trajectory is computed.")
            lines.append("")
        highest, present = escalation_ladder(nearby_recs)
        lines.append("- " + format_escalation_ladder(highest, present))

    bare = _bare_county(county)
    st = (state_abbrev or "").strip().upper()

    def _is_site_county(r):
        return (bare and _bare_county(r.get("County")) == bare
                and (r.get("State") or "").strip().upper() == st)

    in_county_groups = _groups_of([r for r in context_recs if _is_site_county(r)])
    regional_groups = _groups_of([r for r in context_recs if not _is_site_county(r)])
    active = sorted(in_county_groups & regional_groups)
    local_only = sorted(in_county_groups - regional_groups)
    regional_only = sorted(regional_groups - in_county_groups)

    lines.append("")
    if not bare:
        lines.append("- Spillover check: no county resolved for this site, so "
                     "in-county vs regional group activity cannot be separated.")
    elif active:
        shown = ", ".join(g.title() for g in active)
        lines.append(f"- Spillover check: {shown} appears on in-county records "
                     f"AND elsewhere within 50 mi. Treat as active local "
                     f"capacity, not adjacent-county context.")
    elif in_county_groups:
        shown = ", ".join(g.title() for g in local_only)
        lines.append(f"- Spillover check: named in-county groups ({shown}) "
                     f"appear only in-county on current records.")
    else:
        lines.append("- Spillover check: no named groups on in-county records.")
    if regional_only:
        shown = ", ".join(g.title() for g in regional_only[:5])
        more = (f" and {len(regional_only) - 5} more"
                if len(regional_only) > 5 else "")
        lines.append(f"- Regional groups within 50 mi (context only, not "
                     f"evidence of influence over this site): {shown}{more}.")
    return lines


# ---------------------------------------------------------------------------
# Selftest against real repo data
# ---------------------------------------------------------------------------

def _load_master(path="master_opposition_clean.csv"):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def selftest():
    rows = _load_master()

    def county_rows(county, state):
        bare = county.lower().replace(" county", "").strip()
        return [r for r in rows
                if (r.get("County") or "").strip().lower().replace(" county", "") == bare
                and (r.get("State") or "").strip().upper() == state.upper()]

    print("=" * 70)
    print("Arapahoe County, CO")
    print("=" * 70)
    arapahoe = county_rows("Arapahoe", "CO")
    print(f"in-county records: {len(arapahoe)}")
    traj = narrative_trajectory(arapahoe)
    print(format_narrative_trajectory(traj))
    print()
    highest, present = escalation_ladder(arapahoe)
    print(format_escalation_ladder(highest, present))
    print()

    print("=" * 70)
    print("Powhatan County, VA  (spillover check vs regional 50mi groups)")
    print("=" * 70)
    powhatan = county_rows("Powhatan", "VA")
    print(f"in-county records: {len(powhatan)}")
    in_county_groups = set()
    for r in powhatan:
        for g in re.split(r"[;|]", r.get("qc_groups_canonical") or
                          r.get("Opposition Groups") or ""):
            if g.strip():
                in_county_groups.add(g.strip().lower())
    regional_states = ["Chesterfield", "Henrico", "Hanover", "Goochland",
                       "Fluvanna", "Louisa", "Albemarle", "Spotsylvania",
                       "Charles City"]
    regional_groups = set()
    for c in regional_states:
        for r in county_rows(c, "VA"):
            for g in re.split(r"[;|]", r.get("qc_groups_canonical") or
                              r.get("Opposition Groups") or ""):
                if g.strip():
                    regional_groups.add(g.strip().lower())
    print(f"in-county named groups: {sorted(in_county_groups)}")
    print(f"regional named groups: {len(regional_groups)}")
    overlap_rows, regional_only = spillover_check(in_county_groups, regional_groups)
    print(format_spillover_check(overlap_rows, regional_only))
    print()

    print("=" * 70)
    print("Wyandotte County, KS  (override counterfactual)")
    print("=" * 70)
    print(override_counterfactual(3, "emerging opposition or a mixed narrative",
                                  override_applied=True))


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
