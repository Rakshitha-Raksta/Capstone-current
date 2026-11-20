"""
Independently recompute experiment metrics from RAW outputs and compare them
with summary.json. Does not import the harness's evaluation code.

    python scripts/verify_results.py results/experiments/main
    python scripts/verify_results.py results/experiments/mimic_demo

Inputs per seed k:  raw_<arm>_seed<k>.json  (per-case pipeline outputs)
                    data_seed<k>/ground_truth/injected_errors.csv (answer key)
Writes <dir>/verification.json and prints a table. Exit 1 on any mismatch or completeness problem.

A MIMIC case that ends BLOCKED only because of a genuine source-data issue listed in
data/mimic_iv/known_source_anomalies.json is reported separately as an expected outcome, but only
if the anomaly is re-confirmed from the MIMIC files on this run (--mimic-root, default
data/mimic_iv/demo) and every other root of the case is an injected fault.

Formulas (all sums over cases of one seed, then mean/SD across seeds):
  loop_stage_executions        = sum loop_stage_executions
  loop_unnecessary_executions  = sum loop_unnecessary_executions
  reduction_vs_baseline        = 1 - treatment / baseline   (baseline = B_sequential_full)
  first_pass_tasks             = sum n_first_tasks
  issue coverage               = |injected issue types with a same-code task| / |injected issues|
  detection (all findings)     : TP = injected & found ; FP = found registry codes not injected ; FN = injected - found
  root precision               : registry codes found as ROOT that were injected / all registry ROOT codes
  root exact match             = cases with injected issues whose ROOT registry-code set == injected set
  misattributed                = injected issues found only as CONSEQUENCE (not as ROOT)
"""
import csv
import glob
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.datasets.error_types import ERROR_TYPES  # registry of injectable codes only  # noqa: E402

REG = set(ERROR_TYPES)


def gt_for(d, seed):
    out = {}
    with open(os.path.join(d, f"data_seed{seed}", "ground_truth", "injected_errors.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["hadm_id"], set()).add(r["error_type"])
    return out


KNOWN_ANOMALIES = os.path.join(ROOT, "data", "mimic_iv", "known_source_anomalies.json")


def load_known_anomalies(clinical_source):
    if not os.path.exists(KNOWN_ANOMALIES):
        return {}
    with open(KNOWN_ANOMALIES, encoding="utf-8") as f:
        items = json.load(f)["anomalies"]
    return {a["hadm_id"]: a for a in items if a["clinical_source"] == clinical_source}


def confirm_in_source(mimic_root, anomaly):
    """Re-derive the anomaly from the source files. Returns (confirmed, detail)."""
    from app.datasets.mimic import read_table, table_path
    if anomaly["check"] != "procedure_chartdate_outside_admission":
        return False, f"unknown check {anomaly['check']}"
    adm_p, proc_p = table_path(mimic_root, "hosp", "admissions"), table_path(mimic_root, "hosp", "procedures_icd")
    if not (adm_p and proc_p):
        return False, f"source files not found under {mimic_root}"
    h = anomaly["hadm_id"]
    adm = next((r for r in read_table(adm_p) if r["hadm_id"] == h), None)
    if adm is None:
        return False, "admission not in source"
    start = min(adm["admittime"][:10], (adm.get("edregtime") or adm["admittime"])[:10])
    end = adm["dischtime"][:10]
    outside = [r for r in read_table(proc_p) if r["hadm_id"] == h and not (start <= r["chartdate"][:10] <= end)]
    return bool(outside), f"{len(outside)} procedure(s) dated outside {start}..{end}"


def classify_blocked(stuck, raw, gt, known, confirmed):
    """Split BLOCKED-without-policy-blocker cases into (unexpected, expected_source_anomaly).
    Like every other check here, only answer-key (registry) codes are compared: every other
    registry ROOT of the case must be an injected fault."""
    unexpected, expected = [], []
    for h in stuck:
        a = known.get(h)
        other_roots = (set(raw[h]["roots"]) & REG) - {a["finding"]} if a else set()
        if (a and confirmed.get(h) and a["finding"] in raw[h]["roots"]
                and other_roots <= gt.get(h, set())):
            expected.append(h)
        else:
            unexpected.append(h)
    return unexpected, expected


def recompute(raw, gt):
    m = dict(cases=len(raw), loop_stage_executions=0, loop_unnecessary_executions=0, first_pass_tasks=0,
             covered=0, issues=0, tp=0, fp=0, fn=0, root_tp=0, root_fp=0, exact=0, dirty=0, misattributed=0,
             false_clear=0, review_actions=0)
    for h, r in raw.items():
        truth = gt.get(h, set())
        found = set(r["findings"]) & REG
        roots = set(r["roots"]) & REG
        cons = set(r["consequences"]) & REG
        m["loop_stage_executions"] += r["loop_stage_executions"]
        m["loop_unnecessary_executions"] += r["loop_unnecessary_executions"]
        m["first_pass_tasks"] += r["n_first_tasks"]
        m["review_actions"] += r["review_actions"]
        m["issues"] += len(truth)
        m["covered"] += len(truth & set(r["first_tasks"]))
        m["tp"] += len(truth & found)
        m["fn"] += len(truth - found)
        m["fp"] += len(found - truth)
        m["root_tp"] += len(roots & truth)
        m["root_fp"] += len(roots - truth)
        m["misattributed"] += len((truth & cons) - roots)
        if truth:
            m["dirty"] += 1
            m["exact"] += int(roots == truth)
            m["false_clear"] += int(r["first_status"] == "PREPARATION_COMPLETE")
    m["recall"] = m["tp"] / (m["tp"] + m["fn"]) if m["tp"] + m["fn"] else None
    m["precision"] = m["tp"] / (m["tp"] + m["fp"]) if m["tp"] + m["fp"] else None
    m["root_precision"] = m["root_tp"] / (m["root_tp"] + m["root_fp"]) if m["root_tp"] + m["root_fp"] else None
    m["root_exact_match"] = m["exact"] / m["dirty"] if m["dirty"] else None
    m["coverage"] = m["covered"] / m["issues"] if m["issues"] else None
    return m


# ---------------------------------------------------------------------------
# Checks for runs recorded with per-run trigger records, root attribution and full outputs.
# Independent re-implementations over the RAW records: nothing from app.experiments is imported.
# The stage list and the dependency plan come from the workflow definition (app.clearclaim.dag),
# which is the specification the policies are checked against, not evaluation code.
# ---------------------------------------------------------------------------

ACTIONS = ("INITIAL", "CORRECTION", "REVIEW_DECISION")
ARM_POLICY = {"B_sequential_full": "full", "C_minus_targeted": "full", "C_parallel_targeted": "targeted",
              "C_minus_root_routing": "targeted", "C_decision_aware_full": "decision_aware"}
ALIGNMENT_EXCLUDED = {"C_minus_root_routing"}
VOLATILE = {"id", "case_id", "stage_execution_id", "created_at", "updated_at", "resolved_at", "created_run_id",
            "run_id", "issue_id", "ts", "started_at", "finished_at"}
TOL = 1e-3


def _close(a, b, tol=TOL):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def _ratio(n, d):
    return None if d == 0 else n / d


def check_run_records(raw, arm):
    """Returns (problems, per-trigger totals, loop wall times) or None when the run has no per-run records."""
    from app.clearclaim import dag
    stages = list(dag.STAGE_ORDER)
    if not raw or any("runs" not in r for r in raw.values()):
        return None
    policy = ARM_POLICY.get(arm)
    problems, totals, walls = [], {a: dict(runs=0, stage_executions=0, unnecessary_executions=0) for a in ACTIONS[1:]}, []
    for h, r in raw.items():
        runs = r["runs"]
        initial = [x for x in runs if x["action"] == "INITIAL"]
        loop = [x for x in runs if x["action"] != "INITIAL"]
        if len(initial) != 1:
            problems.append(f"{arm} {h}: {len(initial)} INITIAL run records (expected 1)")
        for x in runs:
            if x["action"] not in ACTIONS:
                problems.append(f"{arm} {h} run {x.get('run_id')}: unknown action {x['action']}")
            if x["trigger"] != x["action"]:
                problems.append(f"{arm} {h} run {x['run_id']}: harness action {x['action']} != recorded trigger {x['trigger']}")
            parts = [x["stages_executed"], x["stages_reused"], x["stages_failed"], x["stages_skipped_dependency_failed"],
                     x["stages_not_rerun"]]
            flat = [s for p in parts for s in p]
            if sorted(flat) != sorted(stages):
                problems.append(f"{arm} {h} run {x['run_id']}: stage lists are not a partition of the 11 stages")
            if len(x["stages_executed"]) != x["n_executed"] or len(x["stages_reused"]) != x["n_reused"]:
                problems.append(f"{arm} {h} run {x['run_id']}: stage counts disagree with stage lists")
            if not set(x["unnecessary_stages"]) <= set(x["stages_executed"]):
                problems.append(f"{arm} {h} run {x['run_id']}: unnecessary stages not among executed stages")
            ran = set(x["stages_executed"]) | set(x["stages_failed"]) | set(x["stages_skipped_dependency_failed"])
            if policy == "full" or (policy == "decision_aware" and x["action"] in ("INITIAL", "CORRECTION")):
                if ran != set(stages):
                    problems.append(f"{arm} {h} run {x['run_id']}: full revalidation expected, {len(ran)} stages ran")
            if policy == "decision_aware" and x["action"] == "REVIEW_DECISION":
                expected = set(dag.invalidated_by(x["changed_domains"]))
                if ran != expected or x["stages_reused"]:
                    problems.append(f"{arm} {h} run {x['run_id']}: decision-aware run executed {sorted(ran)}, "
                                    f"dependency plan is {sorted(expected)}")
            if policy == "targeted" and x["action"] == "REVIEW_DECISION":
                if not (ran | set(x["stages_reused"])) <= set(dag.invalidated_by(x["changed_domains"])):
                    problems.append(f"{arm} {h} run {x['run_id']}: targeted decision run touched stages outside its plan")
        # case-level totals must equal the sums of the per-run records
        checks = [("loop_runs", len(loop)), ("loop_stage_executions", sum(len(x["stages_executed"]) for x in loop)),
                  ("loop_stage_reused", sum(len(x["stages_reused"]) for x in loop)),
                  ("loop_unnecessary_executions", sum(len(x["unnecessary_stages"]) for x in loop)),
                  ("corrections", sum(1 for x in loop if x["action"] == "CORRECTION")),
                  ("decisions", sum(1 for x in loop if x["action"] == "REVIEW_DECISION"))]
        for k, v in checks:
            if r.get(k) != v:
                problems.append(f"{arm} {h}: {k}={r.get(k)} but per-run records give {v}")
        if [x["wall_ms"] for x in loop] != r.get("loop_wall_ms"):
            problems.append(f"{arm} {h}: loop_wall_ms differs from per-run wall times")
        for x in loop:
            t = totals[x["action"]]
            t["runs"] += 1
            t["stage_executions"] += len(x["stages_executed"])
            t["unnecessary_executions"] += len(x["unnecessary_stages"])
            if x["wall_ms"] is not None:
                walls.append(x["wall_ms"])
    return problems, totals, walls


def root_counts(raw, gt):
    """Root TP/FP/FN from predicted ROOT codes and injected codes (registry codes only)."""
    tp = fp = fn = 0
    for h, r in raw.items():
        truth = gt.get(h, set()) & REG
        roots = set(r["roots"]) & REG
        tp += len(roots & truth)
        fp += len(roots - truth)
        fn += len(truth - roots)
    p, rc = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = None if p is None or rc is None else (0.0 if p + rc == 0 else 2 * p * rc / (p + rc))
    return dict(tp=tp, fp=fp, fn=fn, precision=p, recall=rc, f1=f1)


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if k not in VOLATILE}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def _phase_canon(ph):
    ph = _clean(ph or {})
    for k in ("findings", "tasks"):
        ph[k] = sorted(json.dumps(x, sort_keys=True, default=str) for x in ph.get(k, []))
    return json.dumps(ph, sort_keys=True, default=str)


def phase_differences(raw_a, raw_b, phases=("first_pass", "final")):
    """Whole-phase canonical comparison (multisets, volatile fields dropped). Returns (n compared, {phase: set(hadm)})
    or None when outputs were not recorded."""
    common = sorted(set(raw_a) & set(raw_b))
    if not common or any("outputs" not in raw_a[h] or "outputs" not in raw_b[h] for h in common):
        return None
    diff = {p: set() for p in phases}
    for h in common:
        for p in phases:
            if _phase_canon(raw_a[h]["outputs"].get(p)) != _phase_canon(raw_b[h]["outputs"].get(p)):
                diff[p].add(h)
    return len(common), diff


def verify_new_records(d, summary, cfg_arms, cfg_seeds):
    """All checks for the newer raw format. Returns (mismatches, problems, report)."""
    mismatches, problems = [], []
    report = dict(run_records={}, root_attribution={}, equivalence={}, latency={}, derived={})
    per_seed_exec = {}
    for seed in cfg_seeds:
        gt = gt_for(d, seed)
        raws = {}
        for arm in cfg_arms:
            f = os.path.join(d, f"raw_{arm}_seed{seed}.json")
            if os.path.exists(f):
                raws[arm] = json.load(open(f, encoding="utf-8"))
        for arm, raw in raws.items():
            s = summary["seeds"][seed]["arms"][arm]
            res = check_run_records(raw, arm)
            key = f"{arm}/seed{seed}"
            if res is None:
                report["run_records"][key] = "not available (recorded before per-run trigger records)"
            else:
                probs, totals, walls = res
                problems += probs
                report["run_records"][key] = dict(problems=len(probs), by_trigger=totals)
                per_seed_exec.setdefault(arm, {})[seed] = dict(
                    total=sum(t["stage_executions"] for t in totals.values()),
                    **{a: t["stage_executions"] for a, t in totals.items()})
                lbt = s.get("loop_by_trigger")
                if lbt is None:
                    mismatches.append(dict(arm=arm, seed=seed, metric="loop_by_trigger", recomputed=totals, summary=None))
                else:
                    for a, t in totals.items():
                        for k in ("runs", "stage_executions", "unnecessary_executions"):
                            if lbt.get(a, {}).get(k) != t[k]:
                                mismatches.append(dict(arm=arm, seed=seed, metric=f"loop_by_trigger.{a}.{k}",
                                                       recomputed=t[k], summary=lbt.get(a, {}).get(k)))
                med = round(statistics.median(walls), 3) if walls else None
                if not _close(med, (s.get("rerun_latency_ms") or {}).get("median")):
                    mismatches.append(dict(arm=arm, seed=seed, metric="rerun_latency_ms.median", recomputed=med,
                                           summary=(s.get("rerun_latency_ms") or {}).get("median")))
                report["latency"][key] = dict(rerun_median_ms=med, n_runs=len(walls))
            ra = s.get("root_attribution")
            mine = root_counts(raw, gt)
            if ra is None:
                report["root_attribution"][key] = "not available (recorded before corrected root metrics)"
            else:
                report["root_attribution"][key] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in mine.items()}
                for k in ("tp", "fp", "fn"):
                    if ra.get(k) != mine[k]:
                        mismatches.append(dict(arm=arm, seed=seed, metric=f"root_attribution.{k}", recomputed=mine[k],
                                               summary=ra.get(k)))
                for k in ("precision", "recall", "f1"):
                    if not _close(ra.get(k), mine[k]):
                        mismatches.append(dict(arm=arm, seed=seed, metric=f"root_attribution.{k}", recomputed=mine[k],
                                               summary=ra.get(k)))
        for name, eq in (summary["seeds"][seed].get("equivalence") or {}).items():
            a, b = eq["reference_arm"], eq["other_arm"]
            res = phase_differences(raws.get(a, {}), raws.get(b, {}))
            if res is None:
                report["equivalence"][f"{name}/seed{seed}"] = "not available (outputs not recorded)"
                continue
            n, diff = res
            report["equivalence"][f"{name}/seed{seed}"] = dict(n_cases=n, cases_with_differences={p: len(v) for p, v in diff.items()},
                                                               differing_cases={p: sorted(v)[:20] for p, v in diff.items()})
            if eq["n_cases_compared"] != n:
                mismatches.append(dict(seed=seed, metric=f"equivalence.{name}.n_cases_compared", recomputed=n,
                                       summary=eq["n_cases_compared"]))
            for p, v in diff.items():
                if eq["cases_with_differences"].get(p) != len(v):
                    mismatches.append(dict(seed=seed, metric=f"equivalence.{name}.cases_with_differences.{p}",
                                           recomputed=len(v), summary=eq["cases_with_differences"].get(p)))
            if not eq.get("mismatches_truncated"):
                listed = {m["hadm_id"] for m in eq.get("mismatches", [])}
                if listed != set().union(*diff.values()):
                    mismatches.append(dict(seed=seed, metric=f"equivalence.{name}.differing_cases",
                                           recomputed=sorted(set().union(*diff.values()))[:20], summary=sorted(listed)[:20]))
            if eq.get("equivalent") != (n > 0 and not any(diff.values())):
                mismatches.append(dict(seed=seed, metric=f"equivalence.{name}.equivalent",
                                       recomputed=not any(diff.values()), summary=eq.get("equivalent")))
    # arm order: the order recorded in the summary must match the order/position recorded in every raw case
    report["arm_order"] = {}
    for seed in cfg_seeds:
        order = summary["seeds"][seed].get("arm_order")
        if order is None:
            report["arm_order"][seed] = "not available (recorded before arm-order balancing)"
            continue
        bad = 0
        for pos, arm in enumerate(order):
            f = os.path.join(d, f"raw_{arm}_seed{seed}.json")
            if not os.path.exists(f):
                continue
            for h, r in json.load(open(f, encoding="utf-8")).items():
                ae = r.get("arm_execution") or {}
                if ae.get("arm_order") != order or ae.get("position") != pos:
                    bad += 1
        report["arm_order"][seed] = dict(order=order, inconsistent_case_records=bad)
        if bad:
            problems.append(f"seed {seed}: {bad} raw case records disagree with the recorded arm order")
        if sorted(order) != sorted(a for a in cfg_arms if os.path.exists(os.path.join(d, f"raw_{a}_seed{seed}.json"))):
            problems.append(f"seed {seed}: recorded arm order {order} does not match the arms that produced raw files")
    # oracle action-sequence alignment across arms: arms that route root blockers only must perform the identical
    # ordered sequence of oracle actions per encounter, otherwise execution counts are not comparable.
    # C_minus_root_routing is excluded on purpose (routing all findings can legitimately change the oracle's path).
    report["action_alignment"] = {}
    for seed in cfg_seeds:
        seqs = {}
        for arm in cfg_arms:
            f = os.path.join(d, f"raw_{arm}_seed{seed}.json")
            if arm in ALIGNMENT_EXCLUDED or not os.path.exists(f):
                continue
            raw = json.load(open(f, encoding="utf-8"))
            if any("runs" not in r for r in raw.values()):
                seqs = None
                break
            seqs[arm] = {h: [(x["action"], x.get("correction_op"), x.get("decision")) for x in r["runs"]
                             if x["action"] != "INITIAL"] for h, r in raw.items()}
        if seqs is None:
            report["action_alignment"][seed] = "not available (recorded before per-run trigger records)"
            continue
        arms_here = sorted(seqs)
        bad = []
        for a in arms_here[1:]:
            ref = arms_here[0]
            for h in sorted(set(seqs[ref]) | set(seqs[a])):
                if seqs[ref].get(h) != seqs[a].get(h):
                    bad.append(dict(arm_pair=[ref, a], hadm_id=h))
        report["action_alignment"][seed] = dict(arms=arms_here, mismatches=len(bad), first=bad[:10])
        for m in bad[:10]:
            problems.append(f"seed {seed} encounter {m['hadm_id']}: oracle action sequence differs between "
                            f"{m['arm_pair'][0]} and {m['arm_pair'][1]}")
        if len(bad) > 10:
            problems.append(f"seed {seed}: {len(bad) - 10} further action-sequence mismatches")
    # reductions recomputed from per-run records (sum over seeds = ratio of seed means, equal seeds per arm)
    C = "C_parallel_targeted"
    der = summary.get("derived") or {}
    if C in per_seed_exec:
        for base in ("B_sequential_full", "C_minus_targeted", "C_decision_aware_full"):
            if base not in per_seed_exec:
                continue
            seeds = sorted(set(per_seed_exec[C]) & set(per_seed_exec[base]))
            for part, key in (("total", f"loop_execution_reduction_C_vs_{base}"),
                              ("CORRECTION", f"loop_execution_reduction_CORRECTION_C_vs_{base}"),
                              ("REVIEW_DECISION", f"loop_execution_reduction_REVIEW_DECISION_C_vs_{base}")):
                cs = sum(per_seed_exec[C][s][part] for s in seeds)
                bs = sum(per_seed_exec[base][s][part] for s in seeds)
                val = None if bs == 0 else round(1 - cs / bs, 4)
                report["derived"][key] = val
                if key in der and not _close(der[key], val, 1e-4):
                    mismatches.append(dict(metric=f"derived.{key}", recomputed=val, summary=der[key]))
    return mismatches, problems, report


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    mimic_root = os.path.join(ROOT, "data", "mimic_iv", "demo")
    if "--mimic-root" in args:
        i = args.index("--mimic-root")
        mimic_root = os.path.abspath(args[i + 1])
        del args[i:i + 2]
    d = os.path.abspath(args[0] if args else os.path.join(ROOT, "results", "experiments", "main"))
    summary = json.load(open(os.path.join(d, "summary.json"), encoding="utf-8"))
    files = glob.glob(os.path.join(d, "raw_*_seed*.json"))
    per = {}
    for f in files:
        arm, seed = re.match(r".*raw_(.+)_seed(\d+)\.json$", f).groups()
        raw = json.load(open(f, encoding="utf-8"))
        per.setdefault(arm, {})[seed] = recompute(raw, gt_for(d, seed))
    mismatches = []
    for arm, seeds in per.items():
        for seed, m in seeds.items():
            s = summary["seeds"][seed]["arms"][arm]
            checks = [("loop_stage_executions", m["loop_stage_executions"], s["loop_stage_executions"]),
                      ("loop_unnecessary_executions", m["loop_unnecessary_executions"], s["loop_unnecessary_executions"]),
                      ("first_pass_tasks", m["first_pass_tasks"], s["first_pass_tasks"]),
                      ("review_actions", m["review_actions"], s["review_actions"]),
                      ("tp", m["tp"], s["detection_all_findings"]["tp"]), ("fp", m["fp"], s["detection_all_findings"]["fp"]),
                      ("fn", m["fn"], s["detection_all_findings"]["fn"]),
                      ("root_exact_correct", m["exact"], s["root_blocker_exact_match"]["correct"]),
                      ("coverage_covered", m["covered"], s["issue_coverage_by_tasks"]["covered"])]
            for name, mine, theirs in checks:
                if mine != theirs:
                    mismatches.append(dict(arm=arm, seed=seed, metric=name, recomputed=mine, summary=theirs))

    def agg(arm, key):
        vals = [per[arm][s][key] for s in sorted(per[arm])]
        return dict(per_seed=vals, mean=round(statistics.mean(vals), 4),
                    sd=round(statistics.stdev(vals), 4) if len(vals) > 1 else None)

    B, C = "B_sequential_full", "C_parallel_targeted"
    table = {}
    for arm in sorted(per):
        table[arm] = {k: agg(arm, k) for k in ("cases", "loop_stage_executions", "loop_unnecessary_executions",
                                                "first_pass_tasks", "review_actions", "issues", "covered", "tp", "fp",
                                                "fn", "misattributed", "exact", "dirty", "false_clear")}
    derived = {}
    if B in per and C in per:
        b, c = table[B], table[C]
        derived["loop_execution_reduction_C_vs_B"] = round(1 - c["loop_stage_executions"]["mean"] / b["loop_stage_executions"]["mean"], 4)
        derived["unnecessary_reduction_C_vs_B"] = round(1 - c["loop_unnecessary_executions"]["mean"] / b["loop_unnecessary_executions"]["mean"], 4)
    if "C_minus_root_routing" in per and C in per:
        derived["task_reduction_root_vs_all_findings"] = round(
            1 - table[C]["first_pass_tasks"]["mean"] / table["C_minus_root_routing"]["first_pass_tasks"]["mean"], 4)
        derived["coverage_root_routing"] = round(sum(table[C]["covered"]["per_seed"]) / sum(table[C]["issues"]["per_seed"]), 4)
        derived["coverage_all_findings"] = round(sum(table["C_minus_root_routing"]["covered"]["per_seed"])
                                                 / sum(table["C_minus_root_routing"]["issues"]["per_seed"]), 4)
    if C in per:
        t = table[C]
        derived["issues_total"] = sum(t["issues"]["per_seed"])
        derived["misattributed_issues_total"] = sum(t["misattributed"]["per_seed"])
        derived["detection_recall_pooled"] = round(sum(t["tp"]["per_seed"]) / (sum(t["tp"]["per_seed"]) + sum(t["fn"]["per_seed"])), 4)
        derived["detection_precision_pooled"] = round(sum(t["tp"]["per_seed"]) / (sum(t["tp"]["per_seed"]) + sum(t["fp"]["per_seed"])), 4)
        derived["false_clear_total"] = sum(t["false_clear"]["per_seed"])
        derived["faulty_cases_total"] = sum(t["dirty"]["per_seed"])
    base_hashes = {s: v.get("base_db_sha256") for s, v in summary["seeds"].items()}

    # ---- completeness: every configured seed x arm present, full cohorts, loops terminated ----
    problems = []
    cfg_arms, cfg_seeds = summary["config"]["arms"], [str(s) for s in summary["config"]["seeds"]]
    raws = {}
    for arm in cfg_arms:
        for seed in cfg_seeds:
            f = os.path.join(d, f"raw_{arm}_seed{seed}.json")
            if not os.path.exists(f):
                problems.append(f"missing raw output for arm {arm} seed {seed}")
                continue
            raws[(arm, seed)] = json.load(open(f, encoding="utf-8"))
    POLICY_BLOCKERS = {"WAITING_PERIOD_NOT_MET", "POLICY_INACTIVE"}
    clinical_source = summary["config"].get("clinical_source", "SYNTHETIC")
    known = load_known_anomalies(clinical_source)
    source_checks = {h: confirm_in_source(mimic_root, a) for h, a in known.items()}
    confirmed = {h: ok for h, (ok, _) in source_checks.items()}
    loop_issues, expected_anomalies = {}, []
    for (arm, seed), raw in raws.items():
        labels = os.path.join(d, f"data_seed{seed}", "ground_truth", "encounter_labels.csv")
        with open(labels, encoding="utf-8") as fh:
            cohort = {r["hadm_id"] for r in csv.DictReader(fh)}
        if set(raw) != cohort:
            problems.append(f"{arm} seed {seed}: {len(raw)} cases vs cohort {len(cohort)}")
        gt = gt_for(d, seed)
        capped = [h for h, r in raw.items() if r["review_actions"] >= 12]
        open_end = [h for h, r in raw.items() if r["final_status"] == "NEEDS_REVIEW"]
        stuck = [h for h, r in raw.items() if r["final_status"] == "BLOCKED" and not (gt.get(h, set()) & POLICY_BLOCKERS)]
        errored = [h for h, r in raw.items() if r["final_status"] == "ERROR" or r["first_status"] == "ERROR"]
        unexplained, documented = classify_blocked(stuck, raw, gt, known, confirmed)
        expected_anomalies += [f"{arm} seed {seed}: {h}" for h in documented]
        loop_issues[f"{arm}/seed{seed}"] = dict(capped=capped, ended_needs_review=open_end,
                                                blocked_without_policy_blocker=stuck,
                                                blocked_by_documented_source_anomaly=documented, error_status=errored)
        for name, lst in (("loop hit 12-action cap", capped), ("ended NEEDS_REVIEW", open_end),
                          ("ended BLOCKED without a non-correctable policy issue or a confirmed documented "
                           "source anomaly", unexplained), ("ERROR status", errored)):
            if lst:
                problems.append(f"{arm} seed {seed}: {len(lst)} case(s) {name}: {lst[:5]}")

    # ---- cohort equivalence across arms: identical cases and first-pass findings/roots per seed ----
    equivalence = {}
    for seed in cfg_seeds:
        present = [a for a in cfg_arms if (a, seed) in raws]
        ref = raws[(present[0], seed)] if present else {}
        diffs = []
        for a in present[1:]:
            other = raws[(a, seed)]
            for h in ref:
                for key in ("first_status", "findings", "roots", "consequences"):
                    if ref[h].get(key) != other.get(h, {}).get(key):
                        diffs.append(dict(arm=a, hadm_id=h, field=key))
        equivalence[seed] = dict(reference_arm=present[0] if present else None, differences=diffs[:20],
                                 n_differences=len(diffs))
        if diffs:
            problems.append(f"seed {seed}: {len(diffs)} first-pass differences between arms (expected 0)")

    # ---- root-blocker error audit (treatment arm) ----
    rb_errors = []
    for seed in cfg_seeds:
        raw = raws.get(("C_parallel_targeted", seed))
        if raw is None:
            continue
        gt = gt_for(d, seed)
        for h, r in raw.items():
            truth = gt.get(h, set())
            if not truth:
                continue
            roots = set(r["roots"]) & REG
            if roots == truth:
                continue
            cons = set(r["consequences"]) & REG
            found = set(r["findings"]) & REG
            rb_errors.append(dict(seed=seed, hadm_id=h, injected=sorted(truth), roots=sorted(roots),
                                  missed_entirely=sorted(truth - found),
                                  found_only_as_consequence=sorted((truth & cons) - roots),
                                  spurious_roots=sorted(roots - truth), first_status=r["first_status"]))

    # ---- newer raw format: per-run trigger records, corrected root attribution, full output equivalence ----
    new_mm, new_problems, new_report = verify_new_records(d, summary, cfg_arms, cfg_seeds)
    mismatches += new_mm
    problems += new_problems

    out = dict(directory=os.path.relpath(d, ROOT).replace(os.sep, "/"), seeds=sorted(summary["seeds"]),
               arms=sorted(per), base_db_sha256_per_seed=base_hashes, mismatches=mismatches,
               completeness_problems=problems, loop_outcomes=loop_issues,
               documented_source_anomalies=dict(
                   registry=os.path.relpath(KNOWN_ANOMALIES, ROOT).replace(os.sep, "/"),
                   source_checks={h: dict(confirmed=ok, detail=det) for h, (ok, det) in source_checks.items()},
                   expected_outcomes=expected_anomalies),
               cohort_equivalence=equivalence,
               root_blocker_errors=rb_errors, recomputed=table, derived=derived,
               run_records=new_report["run_records"], root_attribution=new_report["root_attribution"],
               equivalence=new_report["equivalence"], latency_recomputed=new_report["latency"],
               derived_from_run_records=new_report["derived"], arm_order=new_report.get("arm_order"),
               action_alignment=new_report.get("action_alignment"),
               notes=["loop outcomes are inferred from final status and action counts (the raw output does "
                      "not record tasks left open)",
                      "run_records / root_attribution / equivalence checks apply only to runs recorded with "
                      "per-run trigger records; older runs report them as not available"])
    with open(os.path.join(d, "verification.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"{out['directory']}: seeds {out['seeds']}, arms {len(out['arms'])}, raw files {len(files)}")
    for k, v in derived.items():
        print(f"  {k:40s} {v}")
    print(f"  mismatches vs summary.json: {len(mismatches)}")
    for m in mismatches[:10]:
        print("   ", m)
    print(f"  base DB sha256 per seed: { {s: (h or '')[:12] for s, h in base_hashes.items()} }")
    print(f"  cohort equivalence differences: { {s: e['n_differences'] for s, e in equivalence.items()} }")
    print(f"  completeness problems: {len(problems)}")
    for p_ in problems:
        print("   ", p_)
    for h, (ok, det) in source_checks.items():
        print(f"  documented source anomaly {h}: {'CONFIRMED in source' if ok else 'NOT confirmed'} ({det})")
    print(f"  cases BLOCKED by a confirmed documented source anomaly (expected; still reported): "
          f"{len(expected_anomalies)}")
    for e in expected_anomalies:
        print("   ", e)
    print(f"  root-blocker errors (treatment arm): {len(rb_errors)}")
    for e in rb_errors:
        print("   ", e)
    for k, v in new_report["derived"].items():
        print(f"  {k:55s} {v}  (from per-run records)")
    return 1 if (mismatches or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
