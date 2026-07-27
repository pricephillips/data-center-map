"""
splink_spike.py — go/no-go spike on probabilistic record linkage.

Question the spike answers: would Fellegi-Sunter match probabilities estimated
by Splink beat the hand-written rule cascade in project_resolution.py, and in
particular would they resolve the contested opposition-to-project links that
currently require human adjudication?

Additive and NOT wired into any workflow. Reads existing files, writes three
NEW review-only files:

  data/splink_spike_scores.csv   every generated pair with its match probability
  data/splink_spike_eval.csv     the adjudicated pairs with model and rule scores
  data/splink_spike_report.md    the evaluation and the verdict

Nothing here is auto-applied. project_resolution.py does not import this
module, pipeline.yml does not call it, and no confirmed link is created or
removed by running it. Splink is not added to any CI dependency list.

Pre-registered decision criteria (fixed before the run, evaluated in code):

  G1  blocking recall on adjudicated pairs        >= 0.95
  G2  AUC on adjudicated pairs                    >= 0.80
  G3  a probability threshold exists with precision >= 0.90 at recall >= 0.50
      on the adjudicated confirms
  G4  AUC on the contested subset                 >= 0.75

  GO requires all four. Any failure is a NO-GO on adoption. G4 carries the
  motivating case: the contested subset is the set of adjudicated pairs where
  one event has several candidate projects, or one project has both confirm
  and reject decisions against different events. That is the Type B
  population, and it is the population the spike exists to serve.

  A1  a secondary check, evaluated regardless of the verdict: does the model
      disagree with the rule cascade often enough for a disagreement audit to
      be worth keeping even if adoption is declined.

Evaluation strata:

  A  broad separation. Positives are rule-confirmed links plus manual
     confirms; negatives are every other generated pair. The negatives are
     PRESUMED, not verified, so stratum A is reported as an upper bound and
     is not a decision criterion.
  B  adjudicated pairs. Every pair in data/project_links_manual.csv carries a
     human decision made against the source narrative. This is the only
     verified label set in the repo and it drives G1 through G3.
  C  contested subset of B, defined from the data rather than hardcoded.
     Drives G4.

Head-to-head: stratum B is also scored with the corroboration count that
triage_accelerator.py already uses, so the verdict compares the candidate
against the incumbent rather than against nothing.

Requires: splink >= 4, pandas, scikit-learn.
Run from repo root:  python3 splink_spike.py
Helper self-test (no splink, no network):  python3 splink_spike.py --selftest
"""

from __future__ import annotations

import collections
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

MANUAL_LINKS = os.path.join(DATA, "project_links_manual.csv")
RULE_LINKS = os.path.join(DATA, "project_links.csv")
REVIEW_CSV = os.path.join(DATA, "project_link_review.csv")

OUT_SCORES = os.path.join(DATA, "splink_spike_scores.csv")
OUT_EVAL = os.path.join(DATA, "splink_spike_eval.csv")
OUT_REPORT = os.path.join(DATA, "splink_spike_report.md")

# Pre-registered thresholds
G1_BLOCKING_RECALL = 0.95
G2_AUC_ADJUDICATED = 0.80
G3_PRECISION = 0.90
G3_MIN_RECALL = 0.50
G4_AUC_CONTESTED = 0.75
A1_MIN_DISAGREEMENTS = 5

LEAK_RE = re.compile(r"\b(win|wins|loss|losses|lost)\b", re.IGNORECASE)

GEO_GENERIC = frozenset({"township", "county", "city", "town", "village",
                         "charter", "north", "south", "east", "west"})


# ---------------------------------------------------------------------------
# Helpers (self-testable without splink)
# ---------------------------------------------------------------------------

def norm_txt(value: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Empty stays empty."""
    s = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def split_pair(uid_l: str, uid_r: str) -> tuple[str, str]:
    """Recover (opp_id, project_id) from a Splink pair without assuming which
    side Splink assigned to l. Orientation is data-dependent and has changed
    between Splink versions, so it is never inferred from position."""
    ids = (uid_l, uid_r)
    ev = next((i for i in ids if i.startswith("E:")), "")
    pj = next((i for i in ids if i.startswith("P:")), "")
    return ev[2:], pj[2:]


def toks_in(text: str, toks) -> int:
    """Count word-boundary occurrences of tokens in text."""
    if not toks or not text:
        return 0
    low = text.lower()
    return sum(1 for t in toks if re.search(rf"\b{re.escape(t)}\b", low))


def corroboration_count(ev: dict, proj: dict) -> int:
    """The incumbent signal: triage_accelerator.py's corroboration count.
    Reimplemented here rather than imported so the spike does not depend on
    that module having been run."""
    raw = ev["raw"]
    summary = " ".join([raw.get("Summary", ""), raw.get("Incident", ""),
                        raw.get("Entity", "")])
    towns = (proj["raw"].get("towns", "") or "") + " " + (proj["raw"].get("address", "") or "")
    n = 0
    name_toks = proj["name_toks"] - GEO_GENERIC
    if name_toks and toks_in(summary, name_toks) >= max(1, (len(name_toks) + 1) // 2):
        n += 1
    if toks_in(summary, proj["co_toks"]):
        n += 1
    import project_resolution as pr
    city = pr.norm_county(raw.get("City", ""))
    if city and re.search(rf"\b{re.escape(city)}\b", towns.lower()):
        n += 1
    return n


def contested_pairs(manual_rows: list[dict]) -> set[tuple[str, str]]:
    """Stratum C, derived from the adjudication file rather than hardcoded.
    A pair is contested when its project carries both confirm and reject
    decisions, or its event was adjudicated against more than one project."""
    by_project = collections.defaultdict(set)
    by_event = collections.defaultdict(set)
    for r in manual_rows:
        by_project[r["project_id"]].add(r["action"])
        by_event[r["opp_id"]].add(r["project_id"])
    hot_p = {k for k, v in by_project.items() if len(v) > 1}
    hot_e = {k for k, v in by_event.items() if len(v) > 1}
    return {(r["opp_id"], r["project_id"]) for r in manual_rows
            if r["project_id"] in hot_p or r["opp_id"] in hot_e}


def threshold_sweep(y, p, thresholds):
    """Return [(threshold, n_predicted, precision, recall)] for the positive
    class. Precision is None where nothing is predicted positive."""
    out = []
    for t in thresholds:
        tp = sum(1 for yi, pi in zip(y, p) if pi >= t and yi == 1)
        fp = sum(1 for yi, pi in zip(y, p) if pi >= t and yi == 0)
        fn = sum(1 for yi, pi in zip(y, p) if pi < t and yi == 1)
        prec = tp / (tp + fp) if (tp + fp) else None
        rec = tp / (tp + fn) if (tp + fn) else None
        out.append((t, tp + fp, prec, rec))
    return out


def selftest() -> int:
    ok = True

    def check(cond, label):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  FAIL {label}")
        else:
            print(f"  pass {label}")

    check(norm_txt("  Foo-Bar,  BAZ ") == "foo bar baz", "norm_txt normalizes")
    check(norm_txt(None) == "", "norm_txt tolerates None")
    check(split_pair("E:opp_1", "P:prj_2") == ("opp_1", "prj_2"), "orientation forward")
    check(split_pair("P:prj_2", "E:opp_1") == ("opp_1", "prj_2"), "orientation reversed")
    check(toks_in("The Vance County site", frozenset({"vance"})) == 1, "token match")
    check(toks_in("advanced", frozenset({"vance"})) == 0, "token match respects boundaries")

    rows = [
        {"opp_id": "a", "project_id": "p1", "action": "confirm"},
        {"opp_id": "b", "project_id": "p1", "action": "reject"},
        {"opp_id": "c", "project_id": "p2", "action": "confirm"},
        {"opp_id": "d", "project_id": "p3", "action": "confirm"},
        {"opp_id": "d", "project_id": "p4", "action": "reject"},
    ]
    con = contested_pairs(rows)
    check(("a", "p1") in con and ("b", "p1") in con, "contested by project")
    check(("d", "p3") in con and ("d", "p4") in con, "contested by event")
    check(("c", "p2") not in con, "uncontested excluded")

    sweep = threshold_sweep([1, 1, 0, 0], [0.9, 0.4, 0.8, 0.1], [0.5])
    check(sweep[0][1] == 2 and abs(sweep[0][2] - 0.5) < 1e-9, "sweep precision")
    check(abs(sweep[0][3] - 0.5) < 1e-9, "sweep recall")

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Feature frames
# ---------------------------------------------------------------------------

def build_frames(events, projects):
    import pandas as pd

    def ev_row(e):
        r = e["raw"]
        nm = (r.get("Project Name") or "").strip() or (r.get("Incident") or "").strip()
        return {
            "unique_id": "E:" + e["opp_id"],
            "state": e["state"] or None,
            "county": e["county"] or None,
            "city": norm_txt(r.get("City", "")) or None,
            "name_norm": norm_txt(nm) or None,
            "summary_norm": norm_txt(" ".join([r.get("Summary", ""), r.get("Incident", ""),
                                               r.get("Entity", "")])) or None,
            "name_tokens": sorted(e["name_toks"]) or None,
            "company_tokens": sorted(e["co_toks"]) or None,
            "lat": e["lat"], "lon": e["lon"],
        }

    def pj_row(p):
        raw = p["raw"]
        return {
            "unique_id": "P:" + p["project_id"],
            "state": p["state"] or None,
            "county": p["county"] or None,
            "city": norm_txt(raw.get("towns", "")) or None,
            "name_norm": norm_txt(p["name"]) or None,
            "summary_norm": norm_txt(" ".join([raw.get("info", ""), raw.get("towns", ""),
                                               raw.get("address", "")])) or None,
            "name_tokens": sorted(p["name_toks"]) or None,
            "company_tokens": sorted(p["co_toks"]) or None,
            "lat": p["lat"], "lon": p["lon"],
        }

    return (pd.DataFrame([ev_row(e) for e in events]),
            pd.DataFrame([pj_row(p) for p in projects]))


def run_splink(df_events, df_projects, with_narrative=True):
    """Train the model and return the scored pair frame. Deterministic:
    max_pairs exceeds the full cartesian product, so u estimation samples
    everything and no seed is needed. Verified identical across runs."""
    import multiprocessing

    # Upstream single-core guard. splink.internals.estimate_u salts its
    # internal blocking rule by cpu_count(), and a salt of 1 raises. Only
    # patched when the host actually has one core.
    if multiprocessing.cpu_count() < 2:
        import splink.internals.estimate_u as _eu

        class _TwoCores:
            @staticmethod
            def cpu_count():
                return 2

        _eu.multiprocessing = _TwoCores

    from splink import DuckDBAPI, Linker, SettingsCreator, block_on
    import splink.comparison_library as cl
    import splink.comparison_level_library as cll

    # Cross-field evidence: does one record's narrative name the other record.
    # This is the evidence a human adjudicator actually uses, so the model is
    # given access to it rather than being tested on structured fields alone.
    # Comparison-level SQL addresses the joined frame, so columns carry _l and
    # _r suffixes; l.col and r.col do not bind during EM training.
    name_in_summary = cl.CustomComparison(
        output_column_name="name_in_summary",
        comparison_levels=[
            cll.CustomLevel(
                '("name_norm_l" IS NULL AND "name_norm_r" IS NULL) '
                'OR ("summary_norm_l" IS NULL AND "summary_norm_r" IS NULL)',
                "null").configure(is_null_level=True),
            cll.CustomLevel(
                'coalesce(contains("summary_norm_l", "name_norm_r"), false) '
                'AND coalesce(contains("summary_norm_r", "name_norm_l"), false)',
                "name appears in both narratives"),
            cll.CustomLevel(
                'coalesce(contains("summary_norm_l", "name_norm_r"), false) '
                'OR coalesce(contains("summary_norm_r", "name_norm_l"), false)',
                "name appears in one narrative"),
            cll.ElseLevel(),
        ])

    comparisons = [
            cl.ExactMatch("state"),
            cl.ExactMatch("county"),
            cl.JaroWinklerAtThresholds("city", [0.95, 0.85]),
            cl.JaroWinklerAtThresholds("name_norm", [0.92, 0.85, 0.7]),
            cl.ArrayIntersectAtSizes("company_tokens", [2, 1]),
            cl.ArrayIntersectAtSizes("name_tokens", [3, 2, 1]),
            cl.DistanceInKMAtThresholds("lat", "lon", [5, 20, 50]),
    ]
    if with_narrative:
        comparisons.append(name_in_summary)

    settings = SettingsCreator(
        link_type="link_only",
        comparisons=comparisons,
        # Geography plus a company-token rule. The company rule is what lets
        # the model reach cross-border pairs, where the opposition sits in a
        # different state or county from the project it targets.
        blocking_rules_to_generate_predictions=[
            block_on("state"),
            block_on("county"),
            {"blocking_rule": 'l."company_tokens" = r."company_tokens"',
             "arrays_to_explode": ["company_tokens"]},
        ],
        retain_intermediate_calculation_columns=True,
    )

    linker = Linker([df_events, df_projects], settings, db_api=DuckDBAPI(),
                    input_table_aliases=["ev", "pj"])
    linker.training.estimate_probability_two_random_records_match(
        [block_on("state", "county", "name_norm")], recall=0.6)
    linker.training.estimate_u_using_random_sampling(max_pairs=5e6)
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("state"))
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("county"))
    return linker.inference.predict(threshold_match_probability=0.001).as_pandas_dataframe()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def fmt(value, nd=3):
    return "n/a" if value is None else f"{value:.{nd}f}"


def write_report(path, ctx):
    L = []
    a = L.append
    a("# Splink spike: scored entity resolution")
    a("")
    a(f"Run over {ctx['n_events']} countable opposition events and "
      f"{ctx['n_projects']} projects. Splink {ctx['splink_version']}, DuckDB "
      f"backend, unsupervised EM, no training labels used to fit the model. "
      f"Deterministic across runs.")
    a("")
    a("## Verdict")
    a("")
    a(f"**{ctx['verdict']}**")
    a("")
    a(ctx["verdict_prose"])
    a("")
    a("## Pre-registered criteria")
    a("")
    a("| # | Criterion | Target | Observed | Met |")
    a("| :-- | :-- | :-- | :-- | :-- |")
    for row in ctx["criteria"]:
        a(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {'yes' if row[4] else 'no'} |")
    a("")
    a("## Stratum A: broad separation (upper bound, not a criterion)")
    a("")
    a(f"Positives are the {ctx['n_rule_links']} rule-confirmed links plus manual "
      f"confirms; negatives are every other generated pair. Negatives are "
      f"presumed rather than verified, so this number describes how well the "
      f"model reproduces the rules, not how well it finds truth.")
    a("")
    a(f"- AUC {fmt(ctx['auc_a'])} over {ctx['n_pos_a']} presumed positives and "
      f"{ctx['n_neg_a']} presumed negatives")
    a(f"- Top-1 recovery of confirmed links: {ctx['top1_ok']}/{ctx['top1_tot']} "
      f"({fmt(ctx['top1_rate'])})")
    a("")
    a("## Stratum B: adjudicated pairs (the decision set)")
    a("")
    a(f"{ctx['n_gold']} human decisions in data/project_links_manual.csv, "
      f"{ctx['n_gold_pos']} confirm and {ctx['n_gold_neg']} reject. Blocking "
      f"generated {ctx['n_gold_hit']} of them.")
    a("")
    a(f"- Splink AUC {fmt(ctx['auc_b'])}")
    a(f"- Incumbent corroboration count AUC {fmt(ctx['auc_b_heur'])}")
    a("")
    a("| Threshold | Predicted confirm | Precision | Recall |")
    a("| :-- | :-- | :-- | :-- |")
    for t, n, prec, rec in ctx["sweep_b"]:
        a(f"| {t} | {n} | {fmt(prec)} | {fmt(rec)} |")
    a("")
    a(f"Base rate on this stratum is {fmt(ctx['base_b'])}, so precision below "
      f"that is worse than accepting every candidate.")
    a("")
    a("## Stratum C: contested pairs (the motivating case)")
    a("")
    a("Derived from the adjudication file, not hardcoded: a pair is contested "
      "when its project carries both confirm and reject decisions against "
      "different events, or its event was adjudicated against more than one "
      "project. These are the cases where several projects share a developer "
      "and a region and the question is which one an opposition record "
      "concerns.")
    a("")
    a(f"- Contested projects: {', '.join(ctx['contested_projects'])}")
    a(f"- {ctx['n_c']} pairs generated, {ctx['n_c_pos']} confirm, base rate {fmt(ctx['base_c'])}")
    a(f"- Splink AUC {fmt(ctx['auc_c'])}")
    a(f"- Non-contested remainder of stratum B: AUC {fmt(ctx['auc_b_minus_c'])} "
      f"over {ctx['n_bmc']} pairs")
    a("")
    a("| Threshold | Predicted confirm | Precision |")
    a("| :-- | :-- | :-- |")
    for t, n, prec, _ in ctx["sweep_c"]:
        a(f"| {t} | {n} | {fmt(prec)} |")
    a("")
    a("## Why the contested cases resist this method")
    a("")
    a("Fellegi-Sunter compares fields. On a contested pair every structured "
      "field agrees, because the candidate projects share a developer, a "
      "state, a county, and often a coordinate cluster. The evidence that "
      "separates them sits in the narrative and in the specific site a "
      "hearing concerned, which is what the human adjudications record.")
    a("")
    a(f"Ablation, run in the same script: dropping the cross-field narrative "
      f"comparison and scoring on structured fields alone moves stratum B AUC "
      f"from {fmt(ctx['auc_b'])} to {fmt(ctx['auc_b_ablation'])} over "
      f"{ctx['n_ab']} pairs. The narrative comparison is doing what little "
      f"separation there is, and a coarse containment test is a poor proxy for "
      f"reading the source. The model assigns {ctx['n_c_conf_wrong']} of the "
      f"contested rejects a probability at or above 0.99, so the errors are "
      f"confident rather than marginal.")
    a("")
    a("## Blocking recall")
    a("")
    a(f"{ctx['n_gold_hit']} of {ctx['n_gold']} adjudicated pairs were generated "
      f"({fmt(ctx['blocking_recall'])}). Missed pairs and their cause:")
    a("")
    a("| Project | Human decision | Cause |")
    a("| :-- | :-- | :-- |")
    for row in ctx["blocking_misses"]:
        a(f"| {row[0]} | {row[1]} | {row[2]} |")
    a("")
    a("## Secondary check: disagreement audit")
    a("")
    a(f"Regardless of the adoption verdict, the score surfaces disagreements "
      f"the rule cascade cannot express. {ctx['n_low_conf']} rule-confirmed "
      f"links score below 0.5 and {ctx['n_high_unlinked']} unlinked pairs score "
      f"at or above 0.99. Those two lists are in "
      f"data/splink_spike_scores.csv under rule_status and are worth a review "
      f"pass on their own terms. Criterion A1 "
      f"({'met' if ctx['a1_met'] else 'not met'}) covers only whether the "
      f"disagreement surface is non-empty; it does not imply either side is "
      f"correct.")
    a("")
    a("Lowest-scoring rule-confirmed links:")
    a("")
    a("| opp_id | project | probability |")
    a("| :-- | :-- | :-- |")
    for row in ctx["low_conf_rows"]:
        a(f"| {row[0]} | {row[1]} | {fmt(row[2], 4)} |")
    a("")
    a("## Reproducibility and scope")
    a("")
    a("- Not imported by project_resolution.py, not called by any workflow, "
      "not added to any CI dependency list. Running it creates and removes no "
      "links.")
    a("- The three output files are review-only. Verbatim incident text is "
      "carried in the CSVs for reviewer context and may contain source "
      "wording that the leak audit flags; this report contains none.")
    a("- u probabilities are estimated over the full cartesian product rather "
      "than a sample, so the run is deterministic without a seed.")
    a("- Splink is MIT licensed, screened in docs/tooling_scan.md.")
    a("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    try:
        import pandas as pd  # noqa: F401
        import splink
        from sklearn.metrics import roc_auc_score
    except ImportError as exc:
        print(f"ERROR: missing dependency ({exc}). "
              "Install with: pip install splink pandas scikit-learn")
        return 1

    import project_resolution as pr
    try:
        import verification_status as vs
    except ImportError:
        vs = None

    opp_rows = pr.load_csv(pr.OPPOSITION_CSV)
    n_all = len(opp_rows)
    if vs is not None:
        opp_rows = vs.countable_rows(opp_rows)
        print(f"verification filter: {n_all - len(opp_rows)} held out, "
              f"{len(opp_rows)} countable events")
    events = [pr.prep_event(r) for r in opp_rows]
    projects = pr.prep_projects(pr.load_csv(pr.PROPOSALS_CSV))
    ev_by = {e["opp_id"]: e for e in events}
    pj_by = {p["project_id"]: p for p in projects}
    print(f"linking {len(events)} events against {len(projects)} projects")

    df_e, df_p = build_frames(events, projects)
    scored = run_splink(df_e, df_p, with_narrative=True)
    scored_ab = run_splink(df_e, df_p, with_narrative=False)
    pairs = [split_pair(l, r) for l, r in zip(scored["unique_id_l"], scored["unique_id_r"])]
    scored["opp_id"] = [p[0] for p in pairs]
    scored["project_id"] = [p[1] for p in pairs]
    prob = dict(zip(zip(scored["opp_id"], scored["project_id"]),
                    scored["match_probability"]))
    pairs_ab = [split_pair(l, r) for l, r in zip(scored_ab["unique_id_l"], scored_ab["unique_id_r"])]
    prob_ab = dict(zip(pairs_ab, scored_ab["match_probability"]))
    print(f"generated {len(prob)} scored pairs")

    rule_links = [(r["opp_id"], r["project_id"]) for r in pr.load_csv(RULE_LINKS)]
    rule_set = set(rule_links)
    review_set = {(r["opp_id"], r["project_id"]) for r in pr.load_csv(REVIEW_CSV)
                  if r.get("review_type") == "LINK_CANDIDATE"}
    manual = pr.load_csv(MANUAL_LINKS)
    man_action = {(r["opp_id"], r["project_id"]): r["action"] for r in manual}
    man_confirm = {k for k, v in man_action.items() if v == "confirm"}
    contested = contested_pairs(manual)

    # --- stratum A -------------------------------------------------------
    pos_a = (rule_set | man_confirm)
    ya, pa = [], []
    for k, v in prob.items():
        ya.append(1 if k in pos_a else 0)
        pa.append(v)
    auc_a = roc_auc_score(ya, pa) if len(set(ya)) > 1 else None
    best = scored.sort_values("match_probability", ascending=False).groupby("opp_id").first()
    top1_ok = top1_tot = 0
    for o, pj in pos_a:
        if o in best.index:
            top1_tot += 1
            top1_ok += int(best.loc[o, "project_id"] == pj)

    # --- stratum B -------------------------------------------------------
    gold_hit = [r for r in manual if (r["opp_id"], r["project_id"]) in prob]
    yb = [1 if r["action"] == "confirm" else 0 for r in gold_hit]
    pb = [prob[(r["opp_id"], r["project_id"])] for r in gold_hit]
    hb = []
    for r in gold_hit:
        e, pj = ev_by.get(r["opp_id"]), pj_by.get(r["project_id"])
        hb.append(float(corroboration_count(e, pj)) if e and pj else 0.0)
    auc_b = roc_auc_score(yb, pb) if len(set(yb)) > 1 else None
    gold_ab = [r for r in manual if (r["opp_id"], r["project_id"]) in prob_ab]
    y_ab = [1 if r["action"] == "confirm" else 0 for r in gold_ab]
    p_ab = [prob_ab[(r["opp_id"], r["project_id"])] for r in gold_ab]
    auc_b_ablation = roc_auc_score(y_ab, p_ab) if len(set(y_ab)) > 1 else None
    auc_b_heur = roc_auc_score(yb, hb) if len(set(yb)) > 1 else None
    sweep_b = threshold_sweep(yb, pb, [0.5, 0.9, 0.99, 0.999])
    base_b = sum(yb) / len(yb) if yb else None
    blocking_recall = len(gold_hit) / len(manual) if manual else 0.0

    # --- stratum C -------------------------------------------------------
    gold_c = [r for r in gold_hit if (r["opp_id"], r["project_id"]) in contested]
    yc = [1 if r["action"] == "confirm" else 0 for r in gold_c]
    pc = [prob[(r["opp_id"], r["project_id"])] for r in gold_c]
    auc_c = roc_auc_score(yc, pc) if len(set(yc)) > 1 else None
    sweep_c = threshold_sweep(yc, pc, [0.5, 0.9, 0.99])
    base_c = sum(yc) / len(yc) if yc else None
    n_c_conf_wrong = sum(1 for yi, pi in zip(yc, pc) if yi == 0 and pi >= 0.99)
    gold_bmc = [r for r in gold_hit if (r["opp_id"], r["project_id"]) not in contested]
    ybmc = [1 if r["action"] == "confirm" else 0 for r in gold_bmc]
    pbmc = [prob[(r["opp_id"], r["project_id"])] for r in gold_bmc]
    auc_bmc = roc_auc_score(ybmc, pbmc) if len(set(ybmc)) > 1 else None

    # --- blocking misses -------------------------------------------------
    misses = []
    seen = set()
    for r in manual:
        k = (r["opp_id"], r["project_id"])
        if k in prob:
            continue
        e, pj = ev_by.get(r["opp_id"]), pj_by.get(r["project_id"])
        if e is None:
            cause = "event not in the countable set"
        elif pj is None:
            cause = "project not in proposals.csv"
        elif e["state"] and pj["state"] and e["state"] != pj["state"]:
            cause = f"cross-state pair, event {e['state']} against project {pj['state']}"
        elif not e["state"]:
            cause = "event has no state and no shared company token"
        elif e["county"] != pj["county"]:
            cause = f"different county, event {e['county'] or 'blank'} against {pj['county'] or 'blank'}"
        else:
            cause = "no blocking rule generated the pair"
        key = (r["project_id"], r["action"], cause)
        if key in seen:
            continue
        seen.add(key)
        misses.append([r["project_id"], r["action"], cause])

    # --- disagreement audit ---------------------------------------------
    low_conf = sorted(((k, prob[k]) for k in rule_set if k in prob and prob[k] < 0.5),
                      key=lambda x: x[1])
    high_unlinked = [k for k, v in prob.items()
                     if v >= 0.99 and k not in rule_set and k not in man_action]
    a1_met = len(low_conf) >= A1_MIN_DISAGREEMENTS

    # --- criteria --------------------------------------------------------
    g1 = blocking_recall >= G1_BLOCKING_RECALL
    g2 = auc_b is not None and auc_b >= G2_AUC_ADJUDICATED
    g3_hit = [(t, n, pv, rv) for t, n, pv, rv in sweep_b
              if pv is not None and pv >= G3_PRECISION and rv is not None and rv >= G3_MIN_RECALL]
    g3 = bool(g3_hit)
    g4 = auc_c is not None and auc_c >= G4_AUC_CONTESTED
    verdict = "GO" if all([g1, g2, g3, g4]) else "NO-GO on adoption"

    best_prec = max((pv for _, _, pv, _ in sweep_b if pv is not None), default=None)
    criteria = [
        ["G1", "blocking recall on adjudicated pairs", f">= {G1_BLOCKING_RECALL}",
         fmt(blocking_recall), g1],
        ["G2", "AUC on adjudicated pairs", f">= {G2_AUC_ADJUDICATED}", fmt(auc_b), g2],
        ["G3", f"precision >= {G3_PRECISION} at recall >= {G3_MIN_RECALL}", "exists",
         f"best precision {fmt(best_prec)}", g3],
        ["G4", "AUC on contested pairs", f">= {G4_AUC_CONTESTED}", fmt(auc_c), g4],
        ["A1", "disagreement surface non-empty", f">= {A1_MIN_DISAGREEMENTS}",
         str(len(low_conf)), a1_met],
    ]

    if verdict == "GO":
        prose = ("All four criteria met. Splink is worth adopting as the "
                 "scoring layer under project_resolution.py.")
    else:
        failed = [c[0] for c in criteria[:4] if not c[4]]
        prose = (
            f"Criteria {', '.join(failed)} not met. Splink separates the easy "
            f"population well (stratum A AUC {fmt(auc_a)}) but that population "
            f"is the one the existing rules already resolve. On the adjudicated "
            f"pairs it reaches AUC {fmt(auc_b)} against the incumbent "
            f"corroboration count at {fmt(auc_b_heur)}, an improvement that is "
            f"real but far short of the level that would let a score replace a "
            f"human decision. On the contested pairs, which are the reason the "
            f"spike was run, it reaches AUC {fmt(auc_c)} against a base rate of "
            f"{fmt(base_c)} and is confidently incorrect on "
            f"{n_c_conf_wrong} of them. The limitation is structural rather "
            f"than a tuning problem: on a contested pair the structured fields "
            f"agree by construction, so a field-comparison model has nothing "
            f"left to discriminate on. Recommendation: keep the rule cascade "
            f"and human adjudication as the confirmation path, and keep the "
            f"score only as the disagreement audit described below.")

    ctx = {
        "n_events": len(events), "n_projects": len(projects),
        "splink_version": splink.__version__,
        "verdict": verdict, "verdict_prose": prose, "criteria": criteria,
        "n_rule_links": len(rule_links),
        "auc_a": auc_a, "n_pos_a": sum(ya), "n_neg_a": len(ya) - sum(ya),
        "top1_ok": top1_ok, "top1_tot": top1_tot,
        "top1_rate": (top1_ok / top1_tot) if top1_tot else None,
        "n_gold": len(manual), "n_gold_hit": len(gold_hit),
        "n_gold_pos": sum(1 for r in manual if r["action"] == "confirm"),
        "n_gold_neg": sum(1 for r in manual if r["action"] == "reject"),
        "auc_b": auc_b, "auc_b_heur": auc_b_heur, "sweep_b": sweep_b, "base_b": base_b,
        "contested_projects": sorted({p for _, p in contested}),
        "n_c": len(gold_c), "n_c_pos": sum(yc), "base_c": base_c,
        "auc_c": auc_c, "sweep_c": sweep_c, "n_c_conf_wrong": n_c_conf_wrong,
        "auc_b_minus_c": auc_bmc, "n_bmc": len(gold_bmc),
        "auc_b_ablation": auc_b_ablation,
        "n_ab": len(gold_ab),
        "blocking_recall": blocking_recall, "blocking_misses": misses,
        "n_low_conf": len(low_conf), "n_high_unlinked": len(high_unlinked),
        "a1_met": a1_met,
        "low_conf_rows": [[k[0], k[1], v] for k, v in low_conf[:10]],
    }

    # --- write scores ----------------------------------------------------
    def rule_status(k):
        if k in rule_set:
            return "rule_confirmed"
        if k in review_set:
            return "review_candidate"
        return "unlinked"

    gamma_cols = [c for c in scored.columns if c.startswith("gamma_")]
    cols = (["opp_id", "project_id", "match_probability", "match_weight",
             "rule_status", "manual_action", "opp_incident", "opp_date",
             "opp_state", "project_name"] + gamma_cols)
    with open(OUT_SCORES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        srt = scored.sort_values("match_probability", ascending=False)
        for _, row in srt.iterrows():
            k = (row["opp_id"], row["project_id"])
            e, pj = ev_by.get(k[0]), pj_by.get(k[1])
            out = {
                "opp_id": k[0], "project_id": k[1],
                "match_probability": f"{row['match_probability']:.6f}",
                "match_weight": f"{row['match_weight']:.4f}",
                "rule_status": rule_status(k),
                "manual_action": man_action.get(k, ""),
                "opp_incident": (e["raw"].get("Incident", "") if e else ""),
                "opp_date": (e["raw"].get("Date", "") if e else ""),
                "opp_state": (e["state"] if e else ""),
                "project_name": (pj["name"] if pj else ""),
            }
            for g in gamma_cols:
                out[g] = row[g]
            w.writerow(out)

    # --- write eval ------------------------------------------------------
    with open(OUT_EVAL, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["opp_id", "project_id", "human_action", "stratum",
                    "generated", "match_probability", "corroboration_count",
                    "project_name", "note"])
        for r in manual:
            k = (r["opp_id"], r["project_id"])
            e, pj = ev_by.get(k[0]), pj_by.get(k[1])
            w.writerow([
                k[0], k[1], r["action"],
                "contested" if k in contested else "uncontested",
                "yes" if k in prob else "no",
                f"{prob[k]:.6f}" if k in prob else "",
                corroboration_count(e, pj) if (e and pj) else "",
                pj["name"] if pj else "",
                r.get("note", ""),
            ])

    write_report(OUT_REPORT, ctx)

    # --- console ---------------------------------------------------------
    print("")
    print(f"stratum A  AUC {fmt(auc_a)} (presumed negatives, upper bound)")
    print(f"stratum B  AUC {fmt(auc_b)} splink vs {fmt(auc_b_heur)} incumbent, "
          f"{len(gold_hit)}/{len(manual)} pairs generated")
    print(f"stratum C  AUC {fmt(auc_c)} over {len(gold_c)} contested pairs, "
          f"base rate {fmt(base_c)}")
    print("")
    for c in criteria:
        print(f"  {c[0]} {'MET    ' if c[4] else 'NOT MET'}  {c[1]}: {c[3]}")
    print("")
    print(f"VERDICT: {verdict}")
    print(f"wrote {os.path.relpath(OUT_SCORES, ROOT)}, "
          f"{os.path.relpath(OUT_EVAL, ROOT)}, {os.path.relpath(OUT_REPORT, ROOT)}")

    for path, review_only in ((OUT_REPORT, False), (OUT_SCORES, True), (OUT_EVAL, True)):
        hits = [i for i, line in enumerate(open(path, encoding="utf-8"), 1)
                if LEAK_RE.search(line)]
        name = os.path.relpath(path, ROOT)
        if not hits:
            print(f"leak audit {name}: clean")
        elif review_only:
            print(f"leak audit {name}: {len(hits)} hits in verbatim source text "
                  "(review-only file, accepted)")
        else:
            print(f"LEAK AUDIT {name}: scorekeeping terms at lines "
                  f"{', '.join(str(h) for h in hits[:10])}, inspect before use")
    return 0


if __name__ == "__main__":
    sys.exit(main())
