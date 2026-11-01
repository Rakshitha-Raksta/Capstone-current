"""
Controlled experiment harness (see docs/research/EVALUATION_PROTOCOL.md).

For each seed: generate a synthetic cohort with ground truth, build an
ISOLATED SQLite database (rules seeded + cohort imported), then for every
arm copy that database and run:

  pass 1  - initial processing of every case
  pass 2  - correction loop: a simulated reviewer works the review queue.
            For a task whose code matches a recorded ground-truth correction
            it applies that correction through the normal correction path
            (apply_correction -> targeted or full rerun). Otherwise it records
            the decision a reviewer would have to make (ACCEPT_WITH_JUSTIFICATION
            when allowed, else CONFIRM_BLOCKED). This is an ORACLE reviewer:
            it isolates pipeline behaviour from human error and is stated as
            such in the protocol.

Ground truth is read here, offline, and never written into the runtime
tables used for decisions.

Arms
  B_sequential_full      sequential execution, every stage re-executed on every run, route roots
  C_parallel_targeted    dependency-aware parallel execution + targeted rerun, route roots  (treatment)
  C_minus_targeted       parallel, full rerun (ablation: no targeted rerun)
  C_minus_root_routing   parallel, targeted, route ALL findings incl. consequences (ablation)
  A_manual_model         no software; modelled minutes from documented ASSUMED durations
"""
import copy
import hashlib
import csv
import json
import os
import shutil
import statistics
import time
from collections import Counter, defaultdict

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clearclaim.context import build_context
from app.clearclaim.corrections import apply_correction
from app.clearclaim.models import (
    CCBase, ClaimCase, Finding, PipelineRun, ReviewDecision, ReviewTask, StageExecution,
)
from app.clearclaim.orchestrator import Orchestrator, audit
from app.datasets.error_types import ERROR_TYPES
from app.datasets.synthetic import generate_synthetic_dataset

ARMS = {
    "B_sequential_full": dict(execution_mode="sequential", rerun_policy="full", routing_policy="root"),
    "C_parallel_targeted": dict(execution_mode="parallel", rerun_policy="targeted", routing_policy="root"),
    "C_minus_targeted": dict(execution_mode="parallel", rerun_policy="full", routing_policy="root"),
    "C_minus_root_routing": dict(execution_mode="parallel", rerun_policy="targeted", routing_policy="all_findings"),
}

# ASSUMED manual effort per stage per encounter, in minutes. These are NOT
# measurements: no time-and-motion study was performed. They exist only to
# make the modelled comparison explicit and are varied in a sensitivity
# analysis (x0.5 and x2). See docs/research/EVALUATION_PROTOCOL.md.
ASSUMED_MANUAL_MINUTES = {
    "ingestion": 3, "data_validation": 10, "discharge_readiness": 8, "billing": 20, "eligibility": 12,
    "adjudication": 15, "historical": 0, "reconciliation": 8, "root_blocker": 10, "review_routing": 3,
    "claim_generation": 12,
}
ASSUMED_MINUTES_PER_REVIEW_ACTION = 6   # one human decision or correction
MAX_LOOP_ITERATIONS = 12


def _gt(data_dir):
    issues = defaultdict(set)
    with open(os.path.join(data_dir, "ground_truth", "injected_errors.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            issues[int(r["hadm_id"])].add(r["error_type"])
    labels = {}
    with open(os.path.join(data_dir, "ground_truth", "encounter_labels.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            labels[int(r["hadm_id"])] = r
    corr = {int(k): v for k, v in json.load(open(os.path.join(data_dir, "ground_truth", "corrections.json"))).items()}
    return issues, labels, corr


def _make_base_db(path, data_dir, clinical_root=None, mode="demo", label="SYNTHETIC"):
    from app.database import seed as seed_module
    from app.clearclaim.etl import import_dataset
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    S = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    old = seed_module.engine, seed_module.SessionLocal
    seed_module.engine, seed_module.SessionLocal = eng, S
    try:
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            seed_module.run()
    finally:
        seed_module.engine, seed_module.SessionLocal = old
    CCBase.metadata.create_all(eng)
    db = S()
    imp = import_dataset(db, clinical_root or data_dir, data_dir, mode, label, actor="experiment")
    assert imp.status == "COMPLETED", imp.validation_report
    db.close()
    eng.dispose()


def _payload(op):
    return {k: v for k, v in op.items() if k not in ("error_type", "op")}


def _percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def _summ(xs):
    return dict(n=len(xs), mean=round(statistics.mean(xs), 3) if xs else None,
                median=round(statistics.median(xs), 3) if xs else None, p95=_percentile(xs, 0.95) and round(_percentile(xs, 0.95), 3))


def run_arm(base_db, arm_db, arm_name, arm_cfg, gt_issues, gt_corr):
    shutil.copyfile(base_db, arm_db)  # identical starting state for every arm (hash recorded per seed)
    eng = create_engine(f"sqlite:///{arm_db}", connect_args={"check_same_thread": False})
    db = sessionmaker(bind=eng, autoflush=False, autocommit=False)()
    ctx = build_context(db)
    ctx.settings.update(execution_mode=arm_cfg["execution_mode"], rerun_policy=arm_cfg["rerun_policy"],
                        routing_policy=arm_cfg["routing_policy"])
    orch = Orchestrator(db, arm_cfg["execution_mode"], arm_cfg["rerun_policy"], actor="experiment", ctx=ctx)

    cases = db.query(ClaimCase).order_by(ClaimCase.id).all()
    per_case = {}
    t_first = []
    for c in cases:
        run = orch.process(c, trigger="INITIAL")
        t_first.append(run.wall_ms)
        hid = c.encounter.hadm_id
        cur = db.query(Finding).filter_by(case_id=c.id, is_current=True).all()
        tasks = db.query(ReviewTask).filter_by(case_id=c.id, status="OPEN").all()
        per_case[hid] = dict(
            case_id=c.id, first_status=c.status, first_wall_ms=run.wall_ms, first_executed=run.stages_executed,
            findings=sorted({f.code for f in cur}),
            roots=sorted({f.code for f in cur if f.role == "ROOT"}),
            consequences=sorted({f.code for f in cur if f.role == "CONSEQUENCE"}),
            first_tasks=sorted(t.code for t in tasks), n_first_tasks=len(tasks),
        )

    # ---------------- correction loop (oracle reviewer) ----------------
    for c in cases:
        hid = c.encounter.hadm_id
        rec = per_case[hid]
        pending_ops = copy.deepcopy(gt_corr.get(hid, []))
        actions = corrections = decisions = 0
        loop_runs, loop_exec, loop_reused, unchanged_exec, loop_ms = 0, 0, 0, 0, []
        for _ in range(MAX_LOOP_ITERATIONS):
            db.refresh(c)
            tasks = (db.query(ReviewTask).filter_by(case_id=c.id, status="OPEN")
                     .order_by((ReviewTask.severity == "BLOCKING").desc(), ReviewTask.id).all())
            if not tasks:
                break
            t = tasks[0]
            op = next((o for o in pending_ops if o["error_type"] == t.code), None)
            prev_runs = {r.id for r in db.query(PipelineRun.id).filter_by(case_id=c.id)}
            if op is not None and op["op"] != "review_decision" and "CORRECT_DATA" in (t.allowed_decisions or []):
                pending_ops.remove(op)
                apply_correction(db, c, op["op"], _payload(op), actor="experiment.oracle",
                                 reason=f"oracle correction for {t.code}", review_task_id=t.id, orchestrator=orch)
                corrections += 1
            else:
                if op is not None and op["op"] == "review_decision" and op["decision"] in (t.allowed_decisions or []):
                    decision = op["decision"]
                    pending_ops.remove(op)
                elif "ACCEPT_WITH_JUSTIFICATION" in (t.allowed_decisions or []):
                    decision = "ACCEPT_WITH_JUSTIFICATION"
                elif "CONFIRM_BLOCKED" in (t.allowed_decisions or []):
                    decision = "CONFIRM_BLOCKED"
                else:  # correctable BLOCKING task with no recorded fix: reviewer cannot resolve it
                    break
                db.add(ReviewDecision(case_id=c.id, task_id=t.id, code=t.code, task_key=t.task_key, decision=decision,
                                      note="experiment oracle decision", actor="experiment.oracle"))
                audit(db, "REVIEW_DECISION", f"{decision} on {t.code}", "experiment.oracle", c.id, "review_task", t.id)
                db.flush()
                orch.process(c, trigger="REVIEW_DECISION", changed_domains=["reviews"])
                decisions += 1
            actions += 1
            for r in db.query(PipelineRun).filter_by(case_id=c.id).all():
                if r.id in prev_runs:
                    continue
                loop_runs += 1
                loop_exec += r.stages_executed or 0
                loop_reused += r.stages_reused or 0
                loop_ms.append(r.wall_ms)
                # an execution is "unnecessary" if it reproduced the stage's previous output exactly
                for se in db.query(StageExecution).filter_by(run_id=r.id, status="SUCCEEDED").all():
                    prior = (db.query(StageExecution)
                             .filter(StageExecution.case_id == c.id, StageExecution.stage == se.stage,
                                     StageExecution.status == "SUCCEEDED", StageExecution.id < se.id)
                             .order_by(StageExecution.id.desc()).first())
                    if prior is not None and prior.output_hash == se.output_hash:
                        unchanged_exec += 1
        db.refresh(c)
        rec.update(final_status=c.status, review_actions=actions, corrections=corrections, decisions=decisions,
                   loop_runs=loop_runs, loop_stage_executions=loop_exec, loop_stage_reused=loop_reused,
                   loop_unnecessary_executions=unchanged_exec, loop_wall_ms=loop_ms,
                   tasks_created_total=db.query(ReviewTask).filter_by(case_id=c.id).count())
    db.close()
    eng.dispose()
    return per_case, t_first


def evaluate_arm(per_case, gt_issues, labels):
    reg = set(ERROR_TYPES)
    tp = fp = fn = 0
    tp_root = fp_root = 0
    by_type = defaultdict(lambda: Counter())
    root_exact = root_total = 0
    covered = total_issues = 0
    false_clear = gt_dirty = 0
    unmatched_tasks = 0
    outlier_blocked = n_outliers = 0
    for hid, r in per_case.items():
        truth = gt_issues.get(hid, set())
        found = set(r["findings"]) & reg
        roots = set(r["roots"]) & reg
        for e in reg:
            if e in truth and e in found:
                tp += 1; by_type[e]["tp"] += 1
            elif e in truth:
                fn += 1; by_type[e]["fn"] += 1
            elif e in found:
                fp += 1; by_type[e]["fp"] += 1
            if e in roots and e in truth:
                tp_root += 1
            elif e in roots:
                fp_root += 1
        if truth:
            gt_dirty += 1
            root_total += 1
            root_exact += int(roots == truth)
            false_clear += int(r["first_status"] == "PREPARATION_COMPLETE")
        total_issues += len(truth)
        covered += len(truth & set(r["first_tasks"]))
        unmatched_tasks += sum(1 for t in r["first_tasks"] if t not in truth)
        if labels[hid]["legitimate_outlier"] == "1":
            n_outliers += 1
            outlier_blocked += int(r["first_status"] == "BLOCKED")

    def prf(t, p_, n):
        p = t / (t + p_) if t + p_ else None
        rc = t / (t + n) if t + n else None
        f1 = 2 * p * rc / (p + rc) if p and rc else None
        return dict(precision=p and round(p, 4), recall=rc and round(rc, 4), f1=f1 and round(f1, 4))

    first = Counter(r["first_status"] for r in per_case.values())
    final = Counter(r["final_status"] for r in per_case.values())
    n = len(per_case)
    clean = [h for h in per_case if not gt_issues.get(h)]
    return dict(
        n_cases=n,
        detection_all_findings=dict(tp=tp, fp=fp, fn=fn, **prf(tp, fp, fn)),
        detection_root_findings=dict(tp=tp_root, fp=fp_root, fn=fn, **prf(tp_root, fp_root, fn)),
        detection_by_type={k: dict(v, **prf(v["tp"], v["fp"], v["fn"])) for k, v in sorted(by_type.items())},
        root_blocker_exact_match=dict(correct=root_exact, total=root_total,
                                      accuracy=round(root_exact / root_total, 4) if root_total else None),
        first_pass_status=dict(first), final_status=dict(final),
        first_pass_yield=round(first.get("PREPARATION_COMPLETE", 0) / n, 4),
        first_pass_yield_clean_cases=round(sum(per_case[h]["first_status"] == "PREPARATION_COMPLETE" for h in clean)
                                           / len(clean), 4) if clean else None,
        review_rate=round(sum(1 for r in per_case.values() if r["n_first_tasks"] > 0) / n, 4),
        first_pass_tasks=sum(r["n_first_tasks"] for r in per_case.values()),
        tasks_created_total=sum(r["tasks_created_total"] for r in per_case.values()),
        tasks_without_injected_issue=unmatched_tasks,
        issue_coverage_by_tasks=dict(covered=covered, total=total_issues,
                                     recall=round(covered / total_issues, 4) if total_issues else None),
        false_clear_rate=round(false_clear / gt_dirty, 4) if gt_dirty else None,
        legitimate_outliers=dict(n=n_outliers, blocked=outlier_blocked),
        review_actions=sum(r["review_actions"] for r in per_case.values()),
        corrections=sum(r["corrections"] for r in per_case.values()),
        decisions=sum(r["decisions"] for r in per_case.values()),
        first_pass_stage_executions=sum(r["first_executed"] for r in per_case.values()),
        loop_runs=sum(r["loop_runs"] for r in per_case.values()),
        loop_stage_executions=sum(r["loop_stage_executions"] for r in per_case.values()),
        loop_stage_reused=sum(r["loop_stage_reused"] for r in per_case.values()),
        loop_unnecessary_executions=sum(r["loop_unnecessary_executions"] for r in per_case.values()),
        first_pass_latency_ms=_summ([r["first_wall_ms"] for r in per_case.values()]),
        rerun_latency_ms=_summ([x for r in per_case.values() for x in r["loop_wall_ms"]]),
    )


def manual_model(per_case_ref, factor=1.0):
    """Modelled minutes for a manual process on the same cohort (ASSUMED durations, full redo per fix)."""
    full = sum(ASSUMED_MANUAL_MINUTES.values()) * factor
    total = 0.0
    for r in per_case_ref.values():
        total += full + r["corrections"] * full + r["decisions"] * ASSUMED_MINUTES_PER_REVIEW_ACTION * factor
    return round(total / len(per_case_ref), 2)


def automated_model(per_case, factor=1.0):
    """Modelled human minutes for an automated arm: only review actions cost human time (ASSUMED)."""
    total = sum(r["review_actions"] * ASSUMED_MINUTES_PER_REVIEW_ACTION * factor for r in per_case.values())
    return round(total / len(per_case), 2)


def run_experiment(out_dir, seeds=(1, 2, 3), n_encounters=200, error_rate=0.35, arms=None, log=print,
                   mimic_root=None, mimic_label="MIMIC_IV_DEMO_2.2"):
    """mimic_root: run on REAL MIMIC-format encounters; only the synthetic admin layer (bills, documents,
    checklist, derived notes, policies) is generated per seed, and only it receives injected faults."""
    arms = arms or list(ARMS)
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    results = dict(config=dict(seeds=list(seeds), n_encounters=n_encounters if not mimic_root else None,
                               error_rate=error_rate, arms=arms, clinical_source=mimic_label if mimic_root else "SYNTHETIC",
                               assumed_manual_minutes=ASSUMED_MANUAL_MINUTES,
                               assumed_minutes_per_review_action=ASSUMED_MINUTES_PER_REVIEW_ACTION),
                   seeds={})
    for seed in seeds:
        data_dir = os.path.join(out_dir, f"data_seed{seed}")
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)
        if mimic_root:
            from app.datasets.synthetic import generate_admin_layer_for_mimic
            manifest = generate_admin_layer_for_mimic(mimic_root, data_dir, seed=seed, error_rate=error_rate,
                                                      source_label=mimic_label)
        else:
            manifest = generate_synthetic_dataset(data_dir, seed=seed, n_encounters=n_encounters, error_rate=error_rate)
        gt_issues, labels, gt_corr = _gt(data_dir)
        base_db = os.path.join(out_dir, f"base_seed{seed}.db")
        if os.path.exists(base_db):
            os.remove(base_db)
        if mimic_root:
            _make_base_db(base_db, data_dir, clinical_root=mimic_root, mode="mimic", label=mimic_label)
        else:
            _make_base_db(base_db, data_dir)
        base_db_sha = hashlib.sha256(open(base_db, "rb").read()).hexdigest()  # every arm starts from this file
        seed_res = dict(base_db_sha256=base_db_sha, dataset_files={k: v["sha256"] for k, v in manifest["files"].items()},
                        scenario_classes=dict(Counter(l["scenario_class"] for l in labels.values())), arms={})
        per_arm = {}
        for arm in arms:
            log(f"seed {seed}: arm {arm}")
            arm_db = os.path.join(out_dir, f"arm_{arm}_seed{seed}.db")
            per_case, _ = run_arm(base_db, arm_db, arm, ARMS[arm], gt_issues, gt_corr)
            per_arm[arm] = per_case
            ev = evaluate_arm(per_case, gt_issues, labels)
            ev["modelled_human_minutes_per_case"] = automated_model(per_case)
            seed_res["arms"][arm] = ev
            with open(os.path.join(out_dir, f"raw_{arm}_seed{seed}.json"), "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in per_case.items()}, f, indent=1)
            os.remove(arm_db)
        ref = per_arm.get("C_parallel_targeted") or next(iter(per_arm.values()))
        seed_res["A_manual_model"] = dict(
            note="MODELLED from ASSUMED durations; no detection metrics (no manual process was observed).",
            minutes_per_case=manual_model(ref), minutes_per_case_x0_5=manual_model(ref, 0.5),
            minutes_per_case_x2=manual_model(ref, 2.0))
        results["seeds"][seed] = seed_res
        os.remove(base_db)
    results["elapsed_s"] = round(time.time() - started, 1)
    results["aggregate"] = aggregate(results)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return results


def aggregate(results):
    """Mean and standard deviation across seeds for headline metrics."""
    keys = [("detection_all_findings", "f1"), ("detection_all_findings", "recall"), ("detection_all_findings", "precision"),
            ("detection_root_findings", "precision"), ("root_blocker_exact_match", "accuracy"),
            ("issue_coverage_by_tasks", "recall"), ("first_pass_yield", None), ("review_rate", None),
            ("first_pass_tasks", None), ("tasks_created_total", None), ("review_actions", None),
            ("loop_stage_executions", None), ("loop_unnecessary_executions", None), ("loop_stage_reused", None),
            ("first_pass_stage_executions", None), ("false_clear_rate", None), ("tasks_without_injected_issue", None),
            ("first_pass_latency_ms", "median"), ("first_pass_latency_ms", "p95"), ("rerun_latency_ms", "median"),
            ("modelled_human_minutes_per_case", None)]
    out = {}
    arms = results["config"]["arms"]
    for arm in arms:
        out[arm] = {}
        for k, sub in keys:
            vals = []
            for s in results["seeds"].values():
                v = s["arms"][arm][k]
                v = v.get(sub) if sub else v
                if v is not None:
                    vals.append(v)
            name = f"{k}.{sub}" if sub else k
            out[arm][name] = dict(mean=round(statistics.mean(vals), 4) if vals else None,
                                  sd=round(statistics.stdev(vals), 4) if len(vals) > 1 else None)
    out["A_manual_model"] = dict(minutes_per_case=dict(
        mean=round(statistics.mean(s["A_manual_model"]["minutes_per_case"] for s in results["seeds"].values()), 2)))
    return out
