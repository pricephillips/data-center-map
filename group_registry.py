"""
group_registry.py
========================================================================
Canonicalizes the free-text 'Opposition Groups' field into a registry so the
same organization stops hiding behind spelling variants.

Outputs (via build_registry / write_registry):
  data/group_registry.csv   canonical_id, canonical_name, n_variants,
                            variants, n_incidents, n_states, states,
                            first_seen, last_seen, decided,
                            confirmed_blocks, blocked_share
  adds per-record column:   qc_groups_canonical  (semicolon-joined)

INTERNAL-ONLY FENCE (ruled 2026-07-30): the per-group outcome columns
(decided, confirmed_blocks, blocked_share) are internal and must never
appear in any client-facing artifact, at any sample size. They attribute
project outcomes to named organizations from observational data with no
control group, the same non-identifiability wall already ruled for cost
and delay attribution. The fence applies to GROUP-level rates only.
County-level and project-level outcome statistics in four-tier vocabulary
remain client-eligible under their existing rules.

Matching: exact match on a normalized key, then conservative fuzzy merge
(difflib ratio >= 0.90 on normalized names of similar length). "Stop X" /
"No X" prefixes are identity-bearing and are never stripped.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from difflib import SequenceMatcher

from entity_split import STRICT, split_entities

# Delimiter handling now lives in entity_split.py, shared with
# outcome_model.py and landmark_model.py so the three cannot drift again.
# Opposition Groups uses STRICT: semicolon and pipe only. Audited against
# the full file, of the cells using a comma and no semicolon, none was a
# genuine multi-group list. Every one was a single organization name or a
# parenthetical listing organizers.

_DROP_SUFFIX = re.compile(
    r"\b(inc|llc|coalition|committee|alliance|association|group|organization|"
    r"org|network|initiative|project|team|coa)\b\.?", re.I)
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")

GENERIC = {"residents", "local residents", "citizens", "community members",
           "neighbors", "community", "local officials", "n a", "na", "none",
           "unknown", "various", "multiple groups", "local farmers"}


def split_groups(cell: str) -> list[str]:
    """Thin alias over the canonical splitter, kept so existing call sites
    and any external importer continue to work unchanged."""
    return split_entities(cell, STRICT)


def norm_key(name: str) -> str:
    k = _PUNCT.sub(" ", str(name).lower())
    k = _DROP_SUFFIX.sub(" ", k)
    k = _WS.sub(" ", k).strip()
    return k


def norm_key_keep_form(name: str) -> str:
    """norm_key() without dropping the organizational-form word."""
    k = _PUNCT.sub(" ", str(name).lower())
    k = _WS.sub(" ", k).strip()
    return k


def _form_retained_match(entry_a: dict, entry_b: dict,
                         threshold: float = 0.90) -> bool:
    """True when some variant pair across the two entries still clears the
    fuzzy threshold once the organizational-form word is retained."""
    for va in entry_a["variants"]:
        ka = norm_key_keep_form(va)
        for vb in entry_b["variants"]:
            if SequenceMatcher(None, ka, norm_key_keep_form(vb)).ratio() \
                    >= threshold:
                return True
    return False


def build_registry(records: list[dict]) -> tuple[dict, dict]:
    """Returns (registry, variant_to_canonical). registry maps canonical_key ->
    {name, variants:set, rows:[record indices]}."""
    registry: dict[str, dict] = {}
    variant_map: dict[str, str] = {}

    # pass 1: exact normalized-key grouping
    for i, r in enumerate(records):
        for v in split_groups(r.get("Opposition Groups", "")):
            k = norm_key(v)
            if not k or k in GENERIC:
                continue
            e = registry.setdefault(k, {"variants": {}, "rows": []})
            e["variants"][v] = e["variants"].get(v, 0) + 1
            e["rows"].append(i)

    # pass 2: conservative fuzzy merge of near-identical keys
    keys = sorted(registry, key=len)
    merged: dict[str, str] = {}
    for a_i, a in enumerate(keys):
        if a in merged:
            continue
        for b in keys[a_i + 1:]:
            if b in merged or abs(len(a) - len(b)) > max(3, int(0.15 * len(a))):
                continue
            if SequenceMatcher(None, a, b).ratio() < 0.90:
                continue
            # Guard: norm_key() strips the organizational-form word
            # (coalition, group, alliance, ...). Two names must still be
            # near-identical WITH that word retained, otherwise distinct
            # organizations merge ("Citizens Action Coalition" in IN with
            # "WV Citizens Action Group" in WV).
            if not _form_retained_match(registry[a], registry[b]):
                continue
            merged[b] = a
    for src, dst in merged.items():
        registry[dst]["variants"].update(registry[src]["variants"])
        registry[dst]["rows"].extend(registry[src]["rows"])
        del registry[src]

    # canonical display name = most frequent original variant
    for k, e in registry.items():
        e["name"] = max(e["variants"].items(), key=lambda t: (t[1], -len(t[0])))[0]
        for v in e["variants"]:
            variant_map[norm_key(v)] = k
        # merged keys must also resolve
    for src, dst in merged.items():
        variant_map[src] = dst

    return registry, variant_map


def annotate(records: list[dict], variant_map: dict) -> None:
    for r in records:
        canon = []
        for v in split_groups(r.get("Opposition Groups", "")):
            k = variant_map.get(norm_key(v))
            if k and k not in canon:
                canon.append(k)
        r["qc_groups_canonical"] = "; ".join(canon)


def write_registry(records: list[dict], registry: dict,
                   path: str = "data/group_registry.csv") -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows_out = []
    for i, (k, e) in enumerate(sorted(registry.items(),
                                      key=lambda t: -len(t[1]["rows"]))):
        idx = sorted(set(e["rows"]))
        recs = [records[j] for j in idx]
        states = sorted({str(r.get("State", "")).strip() for r in recs} - {""})
        dates = sorted(str(r.get("Date", ""))[:10] for r in recs
                       if str(r.get("Date", "")).strip())
        dec = [r for r in recs if str(r.get("outcome_defensible", ""))
               in ("blocked_confirmed", "advanced_confirmed")]
        blocks = sum(1 for r in dec
                     if r.get("outcome_defensible") == "blocked_confirmed")
        rows_out.append({
            "canonical_id": f"G{i+1:04d}", "canonical_name": e["name"],
            "n_variants": len(e["variants"]),
            "variants": "; ".join(sorted(e["variants"])),
            "n_incidents": len(idx), "n_states": len(states),
            "states": "; ".join(states),
            "first_seen": dates[0] if dates else "",
            "last_seen": dates[-1] if dates else "",
            "decided": len(dec), "confirmed_blocks": blocks,
            "blocked_share": round(blocks / len(dec), 3) if dec else "",
        })
    fieldnames = list(rows_out[0].keys()) if rows_out else [
        "canonical_id", "canonical_name", "n_variants", "variants",
        "n_incidents", "n_states", "states", "first_seen", "last_seen",
        "decided", "confirmed_blocks", "blocked_share"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_out)
    return len(rows_out)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "master_opposition_clean.csv"
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
    reg, vmap = build_registry(rows)
    annotate(rows, vmap)
    n = write_registry(rows, reg)
    multi = sum(1 for e in reg.values() if len(e["variants"]) > 1)
    print(f"canonical groups: {n} (from {sum(len(e['variants']) for e in reg.values())} "
          f"variants; {multi} merged multi-variant entries) -> data/group_registry.csv")
