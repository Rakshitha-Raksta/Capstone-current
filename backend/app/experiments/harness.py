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

Research arm (RESEARCH_ARMS; not part of the default arm list, select it explicitly)
  C_decision_aware_full  decision-aware FULL-revalidation baseline, same scheduling as C (parallel):
                         - initial run and every data correction: all 11 stages executed (full);
                         - reviewer decision: the stages the dependency graph invalidates for the
                           changed input domain (dag.invalidated_by(["reviews"]), derived at run time,
                           not hard-coded) are ALL executed, never reused by hash.
                         It differs from C only in hash reuse / targeted planning for corrections, so
                         it is the fair baseline for "how much does targeted revalidation save".

Per-run records (per_case[...]["runs"]): every pipeline run is recorded by the harness at the moment
it performs the action that caused it: action (INITIAL / CORRECTION / REVIEW_DECISION, set by the
harness), trigger and changed domains (as stored by the orchestrator), planned / executed / reused /
failed / dependency-skipped / not-rerun stages, unnecessary executions, run wall_ms and the harness
call time. Nothing is inferred afterwards.
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

from app.clearclaim import dag
from app.clearclaim.context import build_context
from app.clearclaim.corrections import apply_correction
from app.clearclaim.models import (
    CCBase, ClaimCase, Finding, PipelineRun, ReviewDecision, ReviewTask, StageExecution,
)
from app.clearclaim.orchestrator import Orchestrator, audit
from app.datasets.error_types import ERROR_TYPES
from app.datasets.synthetic import generate_synthetic_dataset
from app.experiments.arm_order import canonical as canonical_arms, order_for_seed, position_balance
from app.experiments.equivalence import capture_case, compare_arms
from app.experiments.metrics import root_attribution

ARMS = {
    "B_sequential_full": dict(execution_mode="sequential", rerun_policy="full", routing_policy="root"),
    "C_parallel_targeted": dict(execution_mode="parallel", rerun_policy="targeted", routing_policy="root"),
    "C_minus_targeted": dict(execution_mode="parallel", rerun_policy="full", routing_policy="root"),
    "C_minus_root_routing": dict(execution_mode="parallel", rerun_policy="targeted", routing_policy="all_findings"),
}
# Opt-in research arms. Kept out of ARMS so the default arm list (CLI default, application experiments
# page) and every recorded result stay exactly as they were.
RESEARCH_ARMS = {
    "C_decision_aware_full": dict(execution_mode="parallel", rerun_policy="decision_aware", routing_policy="root"),
}
ALL_ARMS = {**ARMS, **RESEARCH_ARMS}
# Full-revalidation arms compared with the targeted treatment for output equivalence (reference, other).
EQUIVALENCE_PAIRS = [("B_sequential_full", "C_parallel_targeted"), ("C_minus_targeted", "C_parallel_targeted"),
                     ("C_decision_aware_full", "C_parallel_targeted")]
ACTIONS = ("INITIAL", "CORRECTION", "REVIEW_DECISION")
TIMING_DEFINITIONS = dict(
    wall_ms="pipeline only: orchestrator run start (after the run row is inserted) to the end of the last stage "
            "wave; excludes outcome application, issue-memory synchronisation and the commit",
    harness_action_ms="complete harness call that produced the run: pipeline + outcome application + issue-memory "
                      "synchronisation + commit, and for loop runs also applying the correction or recording the "
                      "decision; None when one call produced several runs",
    warmup="one warm-up encounter per arm on the arm's own engine before measurement; its database changes are "
           "discarded (file restored from the base copy, SHA-256 checked); reported under seeds.<s>.warmup only",
    status="exploratory: single machine, SQLite; not a latency claim")

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


def _orchestrators(db, arm_cfg, ctx):
    """(initial, correction, decision) orchestrators for an arm. Only the decision-aware baseline uses
    different ones: full revalidation for the initial run and corrections; for reviewer decisions the
    dependency-planned stages are all executed (no hash reuse)."""
    mode, policy = arm_cfg["execution_mode"], arm_cfg["rerun_policy"]
    if policy == "decision_aware":
        full = Orchestrator(db, mode, "full", actor="experiment", ctx=ctx)
        dec = Orchestrator(db, mode, "targeted", actor="experiment", ctx=ctx, reuse_unchanged=False)
        return full, full, dec
    o = Orchestrator(db, mode, policy, actor="experiment", ctx=ctx)
    return o, o, o


def _run_record(db, case_id, r, hid, arm_name, action, harness_ms, op=None, decision=None):
    """One pipeline run, as recorded at the time it happened. The stage partition is exhaustive."""
    rows = db.query(StageExecution).filter_by(run_id=r.id).order_by(StageExecution.id).all()
    by = defaultdict(list)
    unnecessary = []
    for se in rows:
        by[se.status].append(se.stage)
        if se.status == "SUCCEEDED":
            # an execution is "unnecessary" if it reproduced the stage's previous output exactly
            prior = (db.query(StageExecution)
                     .filter(StageExecution.case_id == case_id, StageExecution.stage == se.stage,
                             StageExecution.status == "SUCCEEDED", StageExecution.id < se.id)
                     .order_by(StageExecution.id.desc()).first())
            if prior is not None and prior.output_hash == se.output_hash:
                unnecessary.append(se.stage)
    seen = {s for lst in by.values() for s in lst}

    def order(xs):
        xs = set(xs)
        return [s for s in dag.STAGE_ORDER if s in xs]
    return dict(
        run_id=r.id, hadm_id=hid, arm=arm_name, action=action, trigger=r.trigger,
        changed_domains=list(r.changed_domains or []), planned_stages=list(r.invalidated_stages or []),
        correction_op=op, decision=decision, rerun_policy=r.rerun_policy, execution_mode=r.execution_mode,
        status=r.status, stages_executed=order(by["SUCCEEDED"]), stages_reused=order(by["REUSED"]),
        stages_failed=order(by["FAILED"]), stages_skipped_dependency_failed=order(by["SKIPPED_DEPENDENCY_FAILED"]),
        stages_not_rerun=[s for s in dag.STAGE_ORDER if s not in seen], unnecessary_stages=order(unnecessary),
        n_executed=r.stages_executed or 0, n_reused=r.stages_reused or 0,
        wall_ms=r.wall_ms,
        # wall time of the harness call that produced this run: includes applying the correction or the
        # decision, outcome application and the commit. None when one call produced several runs.
        harness_action_ms=harness_ms,
    )


def _new_runs(db, case_id, before_ids):
    return [r for r in db.query(PipelineRun).filter_by(case_id=case_id).order_by(PipelineRun.id)
            if r.id not in before_ids]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _arm_context(db, arm_cfg):
    ctx = build_context(db)
    ctx.settings.update(execution_mode=arm_cfg["execution_mode"],
                        rerun_policy="full" if arm_cfg["rerun_policy"] == "decision_aware" else arm_cfg["rerun_policy"],
                        routing_policy=arm_cfg["routing_policy"])
    return ctx


def _warm_up(eng, S, base_db, arm_db, arm_cfg):
    """Process the first encounter once on the arm's own engine (statement caches, thread pool, file cache),
    then dispose the connections and restore the arm database from the base copy. The restore is checked
    by SHA-256, so the warm-up cannot leave any state behind in the counted experiment."""
    db = S()
    try:
        ctx = _arm_context(db, arm_cfg)
        orch_initial, _, _ = _orchestrators(db, arm_cfg, ctx)
        c = db.query(ClaimCase).order_by(ClaimCase.id).first()
        t0 = time.perf_counter()
        run = orch_initial.process(c, trigger="INITIAL")
        call_ms = (time.perf_counter() - t0) * 1000
        info = dict(hadm_id=c.encounter.hadm_id, wall_ms=run.wall_ms, harness_action_ms=call_ms,
                    stages_executed=run.stages_executed)
    finally:
        db.close()
        eng.dispose()                       # closes every pooled connection; compiled caches stay on the engine
    shutil.copyfile(base_db, arm_db)
    base_sha, arm_sha = _sha256(base_db), _sha256(arm_db)
    if base_sha != arm_sha:
        raise RuntimeError("warm-up restore failed: arm database differs from the base database")
    info.update(db_restored_from_base=True, restored_sha256=arm_sha,
                note="excluded from every counted metric and timing summary; the database was restored afterwards")
    return info


def run_arm(base_db, arm_db, arm_name, arm_cfg, gt_issues, gt_corr, warmup=False, arm_execution=None):
    """warmup=True: one warm-up encounter on this arm's engine before measurement (see _warm_up).
    arm_execution: metadata copied into every raw case record (arm order of the seed, position)."""
    shutil.copyfile(base_db, arm_db)  # identical starting state for every arm (hash recorded per seed)
    eng = create_engine(f"sqlite:///{arm_db}", connect_args={"check_same_thread": False})
    S = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    warm = _warm_up(eng, S, base_db, arm_db, arm_cfg) if warmup else None
    meta = dict(arm_execution or {}, warmup=warm)
    db = S()
    ctx = _arm_context(db, arm_cfg)
    orch_initial, orch_corr, orch_dec = _orchestrators(db, arm_cfg, ctx)

    cases = db.query(ClaimCase).order_by(ClaimCase.id).all()
    per_case = {}
    t_first = []
    for c in cases:
        hid = c.encounter.hadm_id
        before = {r.id for r in db.query(PipelineRun.id).filter_by(case_id=c.id)}
        t0 = time.perf_counter()
        run = orch_initial.process(c, trigger="INITIAL")
        call_ms = (time.perf_counter() - t0) * 1000
        t_first.append(run.wall_ms)
        new = _new_runs(db, c.id, before)
        cur = db.query(Finding).filter_by(case_id=c.id, is_current=True).all()
        tasks = db.query(ReviewTask).filter_by(case_id=c.id, status="OPEN").all()
        per_case[hid] = dict(
            case_id=c.id, subject_id=c.encounter.subject_id, arm_execution=meta, first_status=c.status, first_wall_ms=run.wall_ms, first_executed=run.stages_executed,
            findings=sorted({f.code for f in cur}),
            roots=sorted({f.code for f in cur if f.role == "ROOT"}),
            consequences=sorted({f.code for f in cur if f.role == "CONSEQUENCE"}),
            first_tasks=sorted(t.code for t in tasks), n_first_tasks=len(tasks),
            runs=[_run_record(db, c.id, r, hid, arm_name, "INITIAL", call_ms if len(new) == 1 else None) for r in new],
            outputs=dict(first_pass=capture_case(db, c)),
        )

    # ---------------- correction loop (oracle reviewer) ----------------
    for c in cases:
        hid = c.encounter.hadm_id
        rec = per_case[hid]
        pending_ops = copy.deepcopy(gt_corr.get(hid, []))
        actions = corrections = decisions = 0
        loop_records = []
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
                t0 = time.perf_counter()
                apply_correction(db, c, op["op"], _payload(op), actor="experiment.oracle",
                                 reason=f"oracle correction for {t.code}", review_task_id=t.id, orchestrator=orch_corr)
                call_ms = (time.perf_counter() - t0) * 1000
                corrections += 1
                action, op_name, decision = "CORRECTION", op["op"], None
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
                t0 = time.perf_counter()
                db.add(ReviewDecision(case_id=c.id, task_id=t.id, code=t.code, task_key=t.task_key, decision=decision,
                                      note="experiment oracle decision", actor="experiment.oracle"))
                audit(db, "REVIEW_DECISION", f"{decision} on {t.code}", "experiment.oracle", c.id, "review_task", t.id)
                db.flush()
                orch_dec.process(c, trigger="REVIEW_DECISION", changed_domains=["reviews"])
                call_ms = (time.perf_counter() - t0) * 1000
                decisions += 1
                action, op_name = "REVIEW_DECISION", None
            actions += 1
            new = _new_runs(db, c.id, prev_runs)
            for r in new:
                loop_records.append(_run_record(db, c.id, r, hid, arm_name, action, call_ms if len(new) == 1 else None,
                                                op=op_name, decision=decision))
        db.refresh(c)
        rec["runs"] += loop_records
        rec.update(final_status=c.status, review_actions=actions, corrections=corrections, decisions=decisions,
                   loop_runs=len(loop_records),
                   loop_stage_executions=sum(x["n_executed"] for x in loop_records),
                   loop_stage_reused=sum(x["n_reused"] for x in loop_records),
                   loop_unnecessary_executions=sum(len(x["unnecessary_stages"]) for x in loop_records),
                   loop_wall_ms=[x["wall_ms"] for x in loop_records],
                   tasks_created_total=db.query(ReviewTask).filter_by(case_id=c.id).count())
        rec["outputs"]["final"] = capture_case(db, c)
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
        # LEGACY (kept only for comparison with recorded runs): fn here is the ALL-FINDINGS false-negative
        # count, so its recall/F1 are NOT root metrics. Use root_attribution instead.
        detection_root_findings=dict(tp=tp_root, fp=fp_root, fn=fn, **prf(tp_root, fp_root, fn),
                                     legacy_note="fn is the all-findings FN; recall/F1 are not valid root metrics"),
        root_attribution=root_attribution(
            [dict(hadm_id=h, findings=r["findings"], roots=r["roots"]) for h, r in per_case.items()],
            {str(h): v for h, v in gt_issues.items()}, reg),
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
        # complete harness-call time (pipeline + outcome application + issue-memory sync + commit [+ applying
        # the correction / decision for loop runs]); exploratory, never a latency claim
        first_pass_harness_ms=_summ([x["harness_action_ms"] for r in per_case.values() for x in r.get("runs", [])
                                     if x["action"] == "INITIAL" and x["harness_action_ms"] is not None]),
        rerun_harness_ms=_summ([x["harness_action_ms"] for r in per_case.values() for x in r.get("runs", [])
                                if x["action"] != "INITIAL" and x["harness_action_ms"] is not None]),
        rerun_latency_ms=_summ([x for r in per_case.values() for x in r["loop_wall_ms"]]),
        loop_by_trigger=loop_by_trigger(per_case),
    )


def loop_by_trigger(per_case):
    """Correction-loop work split by the action that caused each run (recorded per run, not inferred)."""
    out = {}
    for action in ACTIONS[1:]:
        runs = [x for r in per_case.values() for x in r.get("runs", []) if x["action"] == action]
        out[action] = dict(runs=len(runs), stage_executions=sum(x["n_executed"] for x in runs),
                           stage_reused=sum(x["n_reused"] for x in runs),
                           unnecessary_executions=sum(len(x["unnecessary_stages"]) for x in runs),
                           wall_ms=_summ([x["wall_ms"] for x in runs if x["wall_ms"] is not None]),
                           harness_action_ms=_summ([x["harness_action_ms"] for x in runs
                                                    if x["harness_action_ms"] is not None]))
    return out


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
                   mimic_root=None, mimic_label="MIMIC_IV_DEMO_2.2", warmup=True, balance_order=True):
    """mimic_root: run on REAL MIMIC-format encounters; only the synthetic admin layer (bills, documents,
    checklist, derived notes, policies) is generated per seed, and only it receives injected faults.
    balance_order: arms run in a Williams Latin-square order per seed (arm_order.py); False = the given order.
    warmup: one warm-up encounter per arm before measurement, excluded from every metric (see _warm_up)."""
    arms = arms or list(ARMS)
    unknown = [a for a in arms if a not in ALL_ARMS]
    if unknown:
        raise ValueError(f"unknown arm(s) {unknown}; choose from {sorted(ALL_ARMS)}")
    arms = canonical_arms(arms, list(ALL_ARMS))
    orders = {seed: (order_for_seed(seed, arms, list(ALL_ARMS)) if balance_order else list(arms)) for seed in seeds}
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
        seed_res["arm_order"] = orders[seed]
        seed_res["warmup"] = {}
        for position, arm in enumerate(orders[seed]):
            log(f"seed {seed}: arm {arm} (position {position + 1}/{len(arms)})")
            arm_db = os.path.join(out_dir, f"arm_{arm}_seed{seed}.db")
            per_case, _ = run_arm(base_db, arm_db, arm, ALL_ARMS[arm], gt_issues, gt_corr, warmup=warmup,
                                  arm_execution=dict(seed=seed, position=position, arm_order=orders[seed]))
            per_arm[arm] = per_case
            seed_res["warmup"][arm] = next(iter(per_case.values()))["arm_execution"]["warmup"] if per_case else None
            ev = evaluate_arm(per_case, gt_issues, labels)
            ev["modelled_human_minutes_per_case"] = automated_model(per_case)
            seed_res["arms"][arm] = ev
            with open(os.path.join(out_dir, f"raw_{arm}_seed{seed}.json"), "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in per_case.items()}, f, indent=1)
            os.remove(arm_db)
        # full output equivalence: each full-revalidation arm vs the targeted treatment
        seed_res["equivalence"] = {f"{a}__vs__{b}": compare_arms(per_arm[a], per_arm[b], a, b)
                                   for a, b in EQUIVALENCE_PAIRS if a in per_arm and b in per_arm}
        ref = per_arm.get("C_parallel_targeted") or next(iter(per_arm.values()))
        seed_res["A_manual_model"] = dict(
            note="MODELLED from ASSUMED durations; no detection metrics (no manual process was observed).",
            minutes_per_case=manual_model(ref), minutes_per_case_x0_5=manual_model(ref, 0.5),
            minutes_per_case_x2=manual_model(ref, 2.0))
        results["seeds"][seed] = seed_res
        os.remove(base_db)
    results["elapsed_s"] = round(time.time() - started, 1)
    results["aggregate"] = aggregate(results)
    results["derived"] = derived(results)
    results["arm_order"] = dict(
        design="Williams Latin square over the canonical arm order; seed s uses row s mod rows" if balance_order
        else "fixed order as given", per_seed={str(k): v for k, v in orders.items()},
        position_balance=position_balance(orders), warmup=warmup)
    results["timing_definitions"] = TIMING_DEFINITIONS
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
    # metrics added with per-run trigger records and corrected root attribution (absent in older runs)
    paths = [("root_attribution", "precision"), ("root_attribution", "recall"), ("root_attribution", "f1")]
    for action in ACTIONS[1:]:
        paths += [("loop_by_trigger", action, "runs"), ("loop_by_trigger", action, "stage_executions"),
                  ("loop_by_trigger", action, "unnecessary_executions"), ("loop_by_trigger", action, "wall_ms", "median"),
                  ("loop_by_trigger", action, "harness_action_ms", "median")]
    paths += [("first_pass_harness_ms", "median"), ("rerun_harness_ms", "median")]
    out = {}
    arms = results["config"]["arms"]
    for arm in arms:
        out[arm] = {}
        for path in [tuple(x for x in k if x is not None) for k in keys] + paths:
            vals = []
            for s in results["seeds"].values():
                v = s["arms"][arm]
                for p_ in path:
                    v = v.get(p_) if isinstance(v, dict) else None
                if v is not None:
                    vals.append(v)
            out[arm][".".join(path)] = dict(mean=round(statistics.mean(vals), 4) if vals else None,
                                            sd=round(statistics.stdev(vals), 4) if len(vals) > 1 else None,
                                            n_seeds=len(vals))
    out["A_manual_model"] = dict(minutes_per_case=dict(
        mean=round(statistics.mean(s["A_manual_model"]["minutes_per_case"] for s in results["seeds"].values()), 2)))
    return out


def derived(results):
    """Reductions on seed means (= pooled ratios, equal seeds per arm). None when a denominator is 0 or
    an arm is absent. Recomputed independently from raw per-run records by scripts/verify_results.py."""
    agg = results["aggregate"]

    def mean(arm, key):
        return (agg.get(arm, {}).get(key) or {}).get("mean")

    def red(t, b, key):
        tv, bv = mean(t, key), mean(b, key)
        return None if tv is None or not bv else round(1 - tv / bv, 4)
    C, B, CT, DA = "C_parallel_targeted", "B_sequential_full", "C_minus_targeted", "C_decision_aware_full"
    out = {}
    for base in (B, CT, DA):
        out[f"loop_execution_reduction_C_vs_{base}"] = red(C, base, "loop_stage_executions")
        for action in ACTIONS[1:]:
            out[f"loop_execution_reduction_{action}_C_vs_{base}"] = red(
                C, base, f"loop_by_trigger.{action}.stage_executions")
        out.setdefault("EXPLORATORY_latency", dict(
            status="EXPLORATORY: single machine, SQLite, pipeline-only wall_ms medians; not a latency claim"))[
            f"rerun_median_wall_ms_reduction_C_vs_{base}"] = red(C, base, "rerun_latency_ms.median")
    out["note"] = ("C_vs_C_minus_targeted and C_vs_C_decision_aware_full hold scheduling constant (all parallel); "
                   "C_vs_B_sequential_full also changes sequential->parallel and is confounded for latency.")
    return out
