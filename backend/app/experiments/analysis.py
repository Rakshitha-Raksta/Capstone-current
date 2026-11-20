"""
Read-only statistical analysis of a recorded experiment (raw_<arm>_seed<k>.json + ground truth).
Nothing here writes to the experiment folder except the CLI's new, timestamped analysis files.

Unit and pairing
  The unit of observation is one encounter in one seed; every arm processes the same encounters and the
  oracle reviewer performs the same actions in every arm, so arms are compared PAIRED per encounter.
  Encounters of the same patient are not independent, so all uncertainty is computed at PATIENT level:
    synthetic cohorts   patient = (seed, subject_id)   (synthetic subject ids restart in every seed)
    MIMIC cohorts       patient = subject_id            (the same real patient recurs across seeds)
  subject_id comes from the raw case record (newer runs) or from ground_truth/encounter_labels.csv.

Outcome
  Correction-loop stage executions per encounter: all loop runs ("overall"), and the CORRECTION and
  REVIEW_DECISION runs separately (per-trigger values exist only in runs with per-run records; older
  runs report them as N/A). Reduction = 1 - sum(treatment) / sum(baseline) over the pooled units.

Confidence intervals
  Patient-cluster paired bootstrap: resample patients with replacement (each drawn patient contributes
  all its encounters in both arms), recompute the pooled reduction; 95% percentile interval.
  B = 10,000 resamples, numpy default_rng(BOOTSTRAP_SEED). Resamples whose baseline sum is 0 are
  undefined and reported (not silently dropped from the count).

Tests
  Wilcoxon signed-rank on per-PATIENT summed paired differences (baseline - treatment), two-sided,
  zero_method="wilcox" (zero differences are excluded and counted). If every difference is zero the
  test is undefined and reported as N/A. scipy method="auto": exact for n <= 50 without ties, otherwise
  the normal approximation with tie correction.
  Families (Holm-adjusted separately):
    primary    C_parallel_targeted vs C_decision_aware_full: overall, CORRECTION, REVIEW_DECISION
    secondary  C_parallel_targeted vs B_sequential_full and vs C_minus_targeted (each: overall + triggers)
  Timing (wall_ms, harness_action_ms) is EXPLORATORY: paired per action, geometric-mean ratio with a
  patient bootstrap CI, no hypothesis test, never a latency claim.
"""
import csv
import glob
import json
import math
import os
import re
import statistics
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np

BOOTSTRAP_SEED = 20261120
BOOTSTRAP_B = 10_000
TREATMENT = "C_parallel_targeted"
PRIMARY_BASELINE = "C_decision_aware_full"
SECONDARY_BASELINES = ("B_sequential_full", "C_minus_targeted")
OUTCOMES = ("overall", "CORRECTION", "REVIEW_DECISION")


# ---------------------------------------------------------------------------
# statistics (pure)
# ---------------------------------------------------------------------------

def pooled_reduction(t_sum: float, b_sum: float) -> Optional[float]:
    return None if b_sum == 0 else 1.0 - t_sum / b_sum


def cluster_bootstrap_reduction(t_by_cluster: Dict[str, float], b_by_cluster: Dict[str, float],
                                B: int = BOOTSTRAP_B, seed: int = BOOTSTRAP_SEED) -> dict:
    """Paired patient-cluster bootstrap of 1 - sum(t)/sum(b). Inputs are per-cluster sums over units."""
    clusters = sorted(set(t_by_cluster) | set(b_by_cluster))
    t = np.array([t_by_cluster.get(c, 0.0) for c in clusters], dtype=float)
    b = np.array([b_by_cluster.get(c, 0.0) for c in clusters], dtype=float)
    est = pooled_reduction(t.sum(), b.sum())
    out = dict(estimate=None if est is None else round(est, 6), ci95=None, n_clusters=len(clusters), B=B,
               rng_seed=seed, undefined_resamples=0,
               method="patient-cluster paired bootstrap, percentile 95% interval")
    if est is None or len(clusters) < 2:
        out["note"] = "baseline sum is zero" if est is None else "fewer than 2 patients: no interval"
        return out
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(clusters), size=(B, len(clusters)))
    bs, ts = b[idx].sum(axis=1), t[idx].sum(axis=1)
    ok = bs > 0
    vals = 1.0 - ts[ok] / bs[ok]
    out["undefined_resamples"] = int((~ok).sum())
    if vals.size:
        lo, hi = np.percentile(vals, [2.5, 97.5])
        out["ci95"] = [round(float(lo), 6), round(float(hi), 6)]
    return out


def wilcoxon_paired(diffs: List[float]) -> dict:
    """Two-sided Wilcoxon signed-rank on paired differences; zeros excluded and counted; N/A if all zero."""
    from scipy.stats import wilcoxon
    d = [float(x) for x in diffs]
    nonzero = [x for x in d if x != 0]
    base = dict(n=len(d), n_nonzero=len(nonzero), n_zero=len(d) - len(nonzero),
                ties_among_nonzero=len(nonzero) - len({abs(x) for x in nonzero}),
                method='scipy.stats.wilcoxon(zero_method="wilcox", alternative="two-sided", method="auto")')
    if not nonzero:
        return dict(base, statistic=None, p_value=None, status="N/A",
                    note="every paired difference is zero; the test is undefined")
    method = "exact" if len(nonzero) <= 50 and base["ties_among_nonzero"] == 0 else "approx"
    r = wilcoxon(nonzero, zero_method="wilcox", alternative="two-sided", method=method)
    p = float(r.pvalue)
    return dict(base, statistic=float(r.statistic), p_value=None if math.isnan(p) else p, status="tested",
                method_used=method, method=f'scipy.stats.wilcoxon(zeros removed first, two-sided, method="{method}"): '
                                           f'exact if n_nonzero <= 50 and no ties, else normal approximation')


def holm(pvalues: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Holm step-down adjustment. None (not tested) stays None and does not count towards m."""
    tested = sorted(((p, k) for k, p in pvalues.items() if p is not None), key=lambda x: (x[0], x[1]))
    m = len(tested)
    adj, running = {}, 0.0
    for i, (p, k) in enumerate(tested):
        running = max(running, min(1.0, (m - i) * p))
        adj[k] = running
    return {k: adj.get(k) for k in pvalues}


def mean_sd(values: List[float]) -> dict:
    v = [x for x in values if x is not None]
    return dict(n=len(v), mean=round(statistics.mean(v), 6) if v else None,
                sd=round(statistics.stdev(v), 6) if len(v) > 1 else None,
                note=None if len(v) > 1 else "sample SD needs at least 2 seeds")


def geometric_ratio_bootstrap(pairs_by_cluster: Dict[str, List[tuple]], B=BOOTSTRAP_B, seed=BOOTSTRAP_SEED) -> dict:
    """Exploratory timing: exp(mean log(t/b)) over paired actions; patient-cluster bootstrap CI."""
    clusters = sorted(c for c, v in pairs_by_cluster.items() if v)
    logs = {c: [math.log(t / b) for t, b in pairs_by_cluster[c] if t and b and t > 0 and b > 0] for c in clusters}
    clusters = [c for c in clusters if logs[c]]
    allv = [x for c in clusters for x in logs[c]]
    if not allv:
        return dict(estimate=None, ci95=None, n_pairs=0, note="no paired timing measurements")
    out = dict(estimate=round(math.exp(statistics.mean(allv)), 6), ci95=None, n_pairs=len(allv), n_clusters=len(clusters),
               B=B, rng_seed=seed, meaning="geometric mean of treatment/baseline time per paired action; <1 = faster")
    if len(clusters) >= 2:
        rng = np.random.default_rng(seed)
        sums = np.array([sum(logs[c]) for c in clusters])
        cnts = np.array([len(logs[c]) for c in clusters])
        idx = rng.integers(0, len(clusters), size=(B, len(clusters)))
        vals = np.exp(sums[idx].sum(axis=1) / cnts[idx].sum(axis=1))
        lo, hi = np.percentile(vals, [2.5, 97.5])
        out["ci95"] = [round(float(lo), 6), round(float(hi), 6)]
    return out


# ---------------------------------------------------------------------------
# loading (read-only)
# ---------------------------------------------------------------------------

def _labels(d, seed):
    p = os.path.join(d, f"data_seed{seed}", "ground_truth", "encounter_labels.csv")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return {r["hadm_id"]: r.get("subject_id") for r in csv.DictReader(f)}


class AlignmentError(ValueError):
    """Paired data are incomplete or inconsistent; no comparison is computed."""


def _no_duplicate_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise AlignmentError(f"duplicate key {k!r} in a raw JSON object (duplicated record)")
        seen[k] = v
    return seen


def load_run(d: str) -> dict:
    with open(os.path.join(d, "summary.json"), encoding="utf-8") as f:
        summary = json.load(f, object_pairs_hook=_no_duplicate_keys)
    raws = defaultdict(dict)
    for p in glob.glob(os.path.join(d, "raw_*_seed*.json")):
        arm, seed = re.match(r".*raw_(.+)_seed(\d+)\.json$", p).groups()
        with open(p, encoding="utf-8") as f:
            raws[arm][seed] = json.load(f, object_pairs_hook=_no_duplicate_keys)
    return dict(summary=summary, raws=dict(raws), dir=d)


def action_sequence(case: dict):
    """Ordered oracle actions of an encounter: (action, correction op, decision) per loop run. Older raw files
    without per-run records only allow comparing the correction / decision counts."""
    runs = case.get("runs")
    if runs is None:
        return ("counts_only", case.get("corrections"), case.get("decisions"))
    return tuple((x["action"], x.get("correction_op"), x.get("decision")) for x in runs if x["action"] != "INITIAL")


def validate_alignment(run: dict, treatment: str, baseline: str) -> dict:
    """Strict completeness and alignment for a paired comparison. Raises AlignmentError on any problem;
    returns per-seed expected / actual counts when everything matches."""
    raws, summary = run["raws"], run["summary"]
    cfg_seeds = sorted({str(x) for x in (summary.get("config") or {}).get("seeds", [])}, key=int)
    st, sb = set(raws[treatment]), set(raws[baseline])
    expected_seeds = set(cfg_seeds) if cfg_seeds else (st | sb)
    if st != expected_seeds or sb != expected_seeds:
        raise AlignmentError(f"seed sets differ: expected {sorted(expected_seeds, key=int)}, {treatment} has "
                             f"{sorted(st, key=int)}, {baseline} has {sorted(sb, key=int)}")
    counts = {}
    for seed in sorted(expected_seeds, key=int):
        rt, rb = raws[treatment][seed], raws[baseline][seed]
        labels = _labels(run["dir"], seed)
        expected = set(labels) if labels else None
        counts[seed] = {"expected": len(expected) if expected is not None else None, treatment: len(rt),
                        baseline: len(rb), "cohort_source": "ground_truth/encounter_labels.csv" if labels
                        else "UNAVAILABLE (arms compared with each other only)"}
        if set(rt) != set(rb) or (expected is not None and set(rt) != expected):
            ref = expected if expected is not None else set(rt) | set(rb)
            raise AlignmentError(
                f"seed {seed}: encounter sets differ (expected {len(ref)}; {treatment} {len(rt)}, {baseline} {len(rb)}); "
                f"missing in {treatment}: {sorted(ref - set(rt))[:10]}, missing in {baseline}: {sorted(ref - set(rb))[:10]}, "
                f"not in cohort: {sorted((set(rt) | set(rb)) - ref)[:10]}")
        for h in sorted(rt):
            ids = {str(x) for x in (rt[h].get("subject_id"), rb[h].get("subject_id"), labels.get(h)) if x not in (None, "")}
            if len(ids) > 1:
                raise AlignmentError(f"seed {seed}, encounter {h}: subject_id disagrees between {treatment}, {baseline} "
                                     f"and ground truth: {sorted(ids)}")
            if not ids:
                raise AlignmentError(f"seed {seed}, encounter {h}: no subject_id in either arm or the ground truth")
            at, ab = action_sequence(rt[h]), action_sequence(rb[h])
            if at != ab:
                raise AlignmentError(f"seed {seed}, encounter {h}: oracle action sequences differ between {treatment} "
                                     f"and {baseline}: {at} vs {ab}")
    return counts


def unit_values(case: dict) -> dict:
    """Per-encounter outcome values; per-trigger values only when per-run records exist."""
    runs = case.get("runs")
    vals = dict(overall=case.get("loop_stage_executions"))
    if runs is None:
        vals.update(CORRECTION=None, REVIEW_DECISION=None)
        return vals
    loop = [x for x in runs if x["action"] != "INITIAL"]
    vals["overall"] = sum(len(x["stages_executed"]) for x in loop)
    for a in ("CORRECTION", "REVIEW_DECISION"):
        vals[a] = sum(len(x["stages_executed"]) for x in loop if x["action"] == a)
    return vals


def paired_units(run: dict, treatment: str, baseline: str):
    """[(seed, hadm, cluster, t_case, b_case)] over encounters present in both arms, or (None, reason)."""
    raws, summary = run["raws"], run["summary"]
    if treatment not in raws or baseline not in raws:
        missing = [a for a in (treatment, baseline) if a not in raws]
        return None, f"arm(s) not in this run: {missing}"
    validate_alignment(run, treatment, baseline)          # raises AlignmentError; never drops records silently
    mimic = str(summary.get("config", {}).get("clinical_source", "SYNTHETIC")).upper().startswith("MIMIC")
    units = []
    for seed in sorted(raws[treatment], key=int):
        labels = _labels(run["dir"], seed)
        rt, rb = raws[treatment][seed], raws[baseline][seed]
        for h in sorted(rt):
            subj = rt[h].get("subject_id") or rb[h].get("subject_id") or labels.get(h)
            cluster = str(subj) if mimic else f"{seed}:{subj}"
            units.append((seed, h, cluster, rt[h], rb[h]))
    return units, None


def compare(run: dict, treatment: str, baseline: str, B=BOOTSTRAP_B, seed=BOOTSTRAP_SEED) -> dict:
    units, why = paired_units(run, treatment, baseline)
    if units is None:
        return dict(status="N/A", reason=why)
    res = dict(treatment=treatment, baseline=baseline, n_units=len(units), n_seeds=len({u[0] for u in units}),
               n_patients=len({u[2] for u in units}), completeness=validate_alignment(run, treatment, baseline),
               alignment="seeds, encounter sets, subject_ids and oracle action sequences verified identical",
               outcomes={})
    for o in OUTCOMES:
        tv = [(u, unit_values(u[3])[o], unit_values(u[4])[o]) for u in units]
        if any(t is None or b is None for _, t, b in tv):
            res["outcomes"][o] = dict(status="N/A", reason="per-trigger values need per-run records (not in this run)"
                                      if o != "overall" else "loop_stage_executions missing")
            continue
        t_cl, b_cl, per_seed = defaultdict(float), defaultdict(float), defaultdict(lambda: [0.0, 0.0])
        for u, t, b in tv:
            t_cl[u[2]] += t
            b_cl[u[2]] += b
            per_seed[u[0]][0] += t
            per_seed[u[0]][1] += b
        ps = {s: dict(treatment=v[0], baseline=v[1], reduction=pooled_reduction(v[0], v[1])) for s, v in sorted(per_seed.items(), key=lambda x: int(x[0]))}
        res["outcomes"][o] = dict(
            status="estimated", treatment_total=sum(t_cl.values()), baseline_total=sum(b_cl.values()),
            pooled=cluster_bootstrap_reduction(t_cl, b_cl, B, seed),
            per_seed=ps, across_seeds=mean_sd([v["reduction"] for v in ps.values()]),
            wilcoxon=wilcoxon_paired([b_cl[c] - t_cl[c] for c in sorted(b_cl)]),
            treatment_never_worse_units=sum(1 for _, t, b in tv if t <= b), n_units=len(tv))
    return res


def timing(run: dict, treatment: str, baseline: str, B=BOOTSTRAP_B, seed=BOOTSTRAP_SEED) -> dict:
    """Exploratory paired timing per loop action, matched by (seed, encounter, action index)."""
    units, why = paired_units(run, treatment, baseline)
    if units is None:
        return dict(status="N/A", reason=why)
    out = {}
    for field in ("wall_ms", "harness_action_ms"):
        pairs, mism = defaultdict(list), 0
        for s, h, cl, t, b in units:
            rt, rb = t.get("runs"), b.get("runs")
            if rt is None or rb is None:
                return dict(status="N/A", reason="per-run records not in this run")
            lt, lb = [x for x in rt if x["action"] != "INITIAL"], [x for x in rb if x["action"] != "INITIAL"]
            if [x["action"] for x in lt] != [x["action"] for x in lb]:
                mism += 1
                continue
            pairs[cl] += [(x[field], y[field]) for x, y in zip(lt, lb) if x[field] is not None and y[field] is not None]
        out[field] = dict(geometric_ratio_bootstrap(pairs, B, seed), encounters_with_different_action_sequences=mism)
    out["status"] = "exploratory (not a latency claim)"
    return out


def analyse(d: str, B=BOOTSTRAP_B, seed=BOOTSTRAP_SEED) -> dict:
    run = load_run(d)
    s = run["summary"]
    primary = compare(run, TREATMENT, PRIMARY_BASELINE, B, seed)
    secondary = {b: compare(run, TREATMENT, b, B, seed) for b in SECONDARY_BASELINES}

    def pvals(res, prefix):
        return {f"{prefix}.{o}": ((res.get("outcomes") or {}).get(o, {}).get("wilcoxon") or {}).get("p_value")
                for o in OUTCOMES} if res.get("status") != "N/A" else {}
    prim_p = pvals(primary, f"{TREATMENT}_vs_{PRIMARY_BASELINE}")
    sec_p = {}
    for b, r in secondary.items():
        sec_p.update(pvals(r, f"{TREATMENT}_vs_{b}"))
    return dict(
        directory=os.path.relpath(d).replace(os.sep, "/"), config=s.get("config"),
        arm_order=s.get("arm_order"), timing_definitions=s.get("timing_definitions"),
        method=dict(unit="encounter (paired across arms)",
                    cluster="patient: (seed, subject_id) for synthetic; subject_id for MIMIC",
                    bootstrap=dict(B=B, rng="numpy.random.default_rng", seed=seed, interval="percentile 95%"),
                    test="Wilcoxon signed-rank on per-patient summed differences; zeros excluded and counted",
                    multiplicity="Holm within the primary family and within the secondary family"),
        primary=primary, secondary=secondary,
        holm=dict(primary=dict(raw=prim_p, adjusted=holm(prim_p)), secondary=dict(raw=sec_p, adjusted=holm(sec_p))),
        exploratory_timing={b: timing(run, TREATMENT, b, B, seed) for b in (PRIMARY_BASELINE,) + SECONDARY_BASELINES},
        notes=["Reductions are pooled ratios 1 - sum(treatment)/sum(baseline); across_seeds gives mean and sample SD "
               "of the per-seed reductions (needs >= 2 seeds).",
               "N/A means the raw data does not contain what the metric needs; nothing is imputed.",
               "Timing is exploratory and comes from one machine; no latency claim is made."])


def to_markdown(a: dict) -> str:
    def f(x, pct=True):
        if x is None:
            return "N/A"
        return f"{x * 100:.2f}%" if pct else (f"{x:.4g}" if isinstance(x, float) else str(x))
    lines = [f"# Analysis of `{a['directory']}`", "",
             f"Seeds {a['config'].get('seeds')}, arms {a['config'].get('arms')}. Bootstrap B={a['method']['bootstrap']['B']}, "
             f"seed {a['method']['bootstrap']['seed']}, patient clusters. Timing is exploratory.", ""]
    for title, res in [("Primary: C vs decision-aware baseline", a["primary"])] + \
            [(f"Secondary: C vs {b}", r) for b, r in a["secondary"].items()]:
        lines += [f"## {title}", ""]
        if res.get("status") == "N/A":
            lines += [f"N/A: {res['reason']}", ""]
            continue
        lines += [f"Units {res['n_units']}, patients {res['n_patients']}, seeds {res['n_seeds']}.", "",
                  "| Outcome | Treatment / baseline executions | Reduction | 95% CI | Wilcoxon p (raw) | Seeds mean ± SD |",
                  "|---|---|---|---|---|---|"]
        for o, r in res["outcomes"].items():
            if r.get("status") == "N/A":
                lines.append(f"| {o} | N/A | N/A | N/A | N/A | {r['reason']} |")
                continue
            ci = r["pooled"]["ci95"]
            ms = r["across_seeds"]
            lines.append(f"| {o} | {r['treatment_total']:.0f} / {r['baseline_total']:.0f} | {f(r['pooled']['estimate'])} | "
                         f"{'N/A' if not ci else f(ci[0]) + ' – ' + f(ci[1])} | "
                         f"{f(r['wilcoxon']['p_value'], False)} ({r['wilcoxon']['status']}) | "
                         f"{f(ms['mean'])} ± {f(ms['sd'])} (n={ms['n']}) |")
        lines.append("")
    lines += ["## Holm-adjusted p-values", ""]
    for fam in ("primary", "secondary"):
        for k, v in a["holm"][fam]["adjusted"].items():
            lines.append(f"- {fam}: `{k}` raw {f(a['holm'][fam]['raw'][k], False)}, Holm {f(v, False)}")
    lines += ["", "## Notes", ""] + [f"- {n}" for n in a["notes"]]
    return "\n".join(lines) + "\n"
