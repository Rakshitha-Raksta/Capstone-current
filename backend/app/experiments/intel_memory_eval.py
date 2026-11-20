"""
Evaluation of the two extensions (historical-case intelligence, persistent issue memory).

Separate from harness.py and its recorded results: it READS the existing experiment datasets
(results/experiments/<run>/data_seed<N>/, verified against their MANIFEST.json hashes), builds
isolated temporary SQLite databases, and writes only to a new output folder.

Per seed
  1. base DB: rules seeded + cohort imported (harness._make_base_db), i.e. the intake state.
  2. history: harness.run_arm(C_parallel_targeted) -> initial processing + the harness's ORACLE reviewer
     correction loop. Every correction/decision in the history therefore comes from the oracle (ground-truth
     corrections), not from human reviewers. Stated as such in the output.
  3. issue memory on that database:
       a. lifecycle consistency after the loop (resolved only with a correction, etc.)
       b. repeated detection: every case re-run 3x (full) -> tasks created, duplicates, linked detections
       c. genuine reappearance: invert each recorded update_* correction that resolved an issue
       d. dismissal + material evidence change, with and without decision fingerprints
          (without = the routing behaviour before this change, which ignored evidence)
       e. merge check: distinct issue identities among current findings vs distinct issues
  4. historical intelligence, leave-one-out over all encounters:
       query features = intake state (base DB); neighbour features = intake state; neighbour issues and
       outcomes = what the deterministic pipeline found and what the oracle reviewer recorded.
       Issue prediction vs injected ground truth, with a random-neighbour baseline; retrieval
       precision@K against a stated proxy relevance; recommendation coverage and agreement with the
       ground-truth correction operation; insufficient-data rates.
"""
import copy
import hashlib
import json
import os
import random
import shutil
import statistics
import tempfile
import time
from collections import Counter, defaultdict

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clearclaim import case_intel as CI
from app.clearclaim import dag
from app.clearclaim.context import build_context
from app.clearclaim.corrections import apply_correction
from app.clearclaim.identity import issue_key
from app.clearclaim.issues import record_decision_on_issue, task_fingerprint
from app.clearclaim.models import (
    AuditEvent, ClaimCase, Correction, Encounter, Finding, IssueEvent, RateCardItem, ReviewDecision, ReviewTask,
    TrackedIssue,
)
from app.clearclaim.orchestrator import Orchestrator, verify_audit_chain
from app.datasets.error_types import ERROR_TYPES
from app.experiments import harness

ACTIONABLE = ("BLOCKING", "REVIEW")
ALL = list(dag.ALL_INPUT_DOMAINS)


def _session(path):
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})
    return eng, sessionmaker(bind=eng, autoflush=False, autocommit=False)()


def verify_manifest(data_dir):
    man = json.load(open(os.path.join(data_dir, "MANIFEST.json"), encoding="utf-8"))
    bad = []
    for rel, meta in man["files"].items():
        h = hashlib.sha256(open(os.path.join(data_dir, rel), "rb").read()).hexdigest()
        if h != meta["sha256"]:
            bad.append(rel)
    return dict(files=len(man["files"]), mismatched=bad)


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return dict(tp=tp, fp=fp, fn=fn, precision=None if p is None else round(p, 4),
                recall=None if r is None else round(r, 4), f1=None if f is None else round(f, 4))


# ---------------------------------------------------------------------------
# issue memory
# ---------------------------------------------------------------------------

def lifecycle_consistency(db):
    out = Counter(dict.fromkeys(["resolved_without_correction", "not_detected_marked_resolved", "closed_but_present"], 0))
    for i in db.query(TrackedIssue):
        out[f"status_{i.status}"] += 1
        ra = i.resolution_action or {}
        if i.status == "RESOLVED" and ra.get("kind") != "CORRECTION":
            out["resolved_without_correction"] += 1            # must stay 0
        if i.status == "NO_LONGER_DETECTED" and i.resolved_at is not None:
            out["not_detected_marked_resolved"] += 1          # must stay 0
        if i.status in ("RESOLVED", "NO_LONGER_DETECTED") and i.is_present:
            out["closed_but_present"] += 1                    # must stay 0
    tasks = db.query(ReviewTask).all()
    out["tasks_total"] = len(tasks)
    out["tasks_without_issue"] = sum(1 for t in tasks if t.issue_id is None)
    open_keys = Counter((t.case_id, t.task_key) for t in tasks if t.status == "OPEN")
    out["open_task_duplicates"] = sum(n - 1 for n in open_keys.values() if n > 1)
    return dict(out)


def false_suppression(db):
    """Routed issues (current review_routing output, state OPEN) that do not have an OPEN task."""
    from app.clearclaim.models import StageExecution
    n = checked = 0
    for se in db.query(StageExecution).filter_by(stage="review_routing", is_current=True):
        tasks = {(t.task_key) for t in db.query(ReviewTask).filter_by(case_id=se.case_id, status="OPEN")}
        for t in (se.output or {}).get("data", {}).get("tasks", []):
            if t["state"] == "OPEN":
                checked += 1
                n += t["task_key"] not in tasks
    return dict(routed_open=checked, without_open_task=n)


def merge_check(db):
    """Distinct issue identities among current actionable findings vs distinct tracked issues."""
    identities, merged, multi = 0, 0, 0
    by_case = defaultdict(set)
    for f in db.query(Finding).filter(Finding.is_current.is_(True), Finding.severity.in_(ACTIONABLE)):
        by_case[f.case_id].add((f.code, issue_key(f.code, f.evidence or {})))
    for cid, ids in by_case.items():
        identities += len(ids)
        codes = Counter(c for c, _ in ids)
        multi += sum(1 for v in codes.values() if v > 1)
        tracked = {i.issue_key: i for i in db.query(TrackedIssue).filter_by(case_id=cid)}
        # a merge would be two identities resolving to one issue row; issue_key IS the identity, so we check
        # that every routed identity has its own row and no row's identity covers two finding identities
        keys = [k for _, k in ids if k in tracked]
        merged += len(keys) - len(set(keys))
    return dict(current_identities=identities, same_code_multi_identity_groups=multi, identities_merged=merged)


def repeated_detection(db, orch, reps=3):
    t0 = db.query(ReviewTask).count()
    det0 = sum(i.detection_count or 0 for i in db.query(TrackedIssue))
    dedup0 = db.query(AuditEvent).filter_by(event_type="REVIEW_TASK_DEDUPLICATED").count()
    routed_per_pass = 0
    for c in db.query(ClaimCase).order_by(ClaimCase.id):
        for _ in range(reps):
            orch.process(c, trigger="MANUAL_RERUN", changed_domains=ALL)
    for (det,) in db.query(AuditEvent.details).filter(AuditEvent.event_type == "REVIEW_TASK_DEDUPLICATED").offset(dedup0):
        routed_per_pass += len((det or {}).get("items") or [])
    open_keys = Counter((t.case_id, t.task_key) for t in db.query(ReviewTask).filter_by(status="OPEN"))
    return dict(reruns=reps * db.query(ClaimCase).count(), tasks_created_during_reruns=db.query(ReviewTask).count() - t0,
                open_task_duplicates=sum(n - 1 for n in open_keys.values() if n > 1),
                repeat_detections_linked=sum(i.detection_count or 0 for i in db.query(TrackedIssue)) - det0,
                duplicate_tasks_prevented=routed_per_pass,
                note="duplicate_tasks_prevented = re-detections where the existing review task was retained, i.e. "
                     "tasks a router that inserts one task per detection would have created. The pre-change "
                     "task_key sync also created none on identical reruns (see before_after probe).")


def reappearance(db, orch):
    """Invert recorded update_* corrections that resolved an issue; the same issue must be reopened."""
    res = Counter(dict.fromkeys(["attempted", "reopened_same_issue", "did_not_reappear", "extra_issue_rows_for_same_code",
                                 "skipped_not_invertible"], 0))
    for i in db.query(TrackedIssue).filter_by(status="RESOLVED").order_by(TrackedIssue.id).all():
        ra = i.resolution_action or {}
        corr = db.get(Correction, ra.get("correction_id")) if ra.get("correction_id") else None
        if corr is None or corr.op not in ("update_bill_line", "update_diagnosis") or not corr.before:
            res["skipped_not_invertible"] += 1
            continue
        c = db.get(ClaimCase, i.case_id)
        if corr.op == "update_bill_line":
            b = corr.before
            fields = {k: b[k] for k in ("quantity", "unit_price", "amount", "item_code") if k in b}
            payload = dict(line_no=b["line_no"], fields=fields)
        else:
            payload = dict(seq_num=corr.before["seq_num"], icd_code=corr.before["icd_code"])
        n_issues = db.query(TrackedIssue).filter_by(case_id=c.id, code=i.code).count()
        try:
            apply_correction(db, c, corr.op, payload, "experiment.reappear", "re-enter the original faulty value",
                             orchestrator=orch)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            res[f"correction_rejected_{type(e).__name__}"] += 1
            continue
        db.refresh(i)
        res["attempted"] += 1
        if i.status in ("PENDING_REVIEW", "OPEN") and i.reopen_count >= 1:
            res["reopened_same_issue"] += 1
        elif i.status == "RESOLVED":
            res["did_not_reappear"] += 1          # e.g. another fix in the case removed the cause
        else:
            res[f"other_{i.status}"] += 1
        res["extra_issue_rows_for_same_code"] += db.query(TrackedIssue).filter_by(case_id=c.id, code=i.code).count() - n_issues
    return dict(res)


def dismissal_injection(db, orch, n_cases=40, fingerprint=True):
    """On clean cases: add an unknown bill item (REVIEW issue), dismiss it, re-run (control), make an
    immaterial change (control), then a material evidence change on the same line."""
    res = Counter(dict.fromkeys(["injection_not_detected", "dismissed", "false_reopen_after_rerun",
                                 "false_reopen_after_unrelated_change", "material_changes", "reopened_on_material_change",
                                 "false_suppression", "case_complete_despite_new_evidence"], 0))
    cases = db.query(ClaimCase).filter_by(status="PREPARATION_COMPLETE").order_by(ClaimCase.id).limit(n_cases).all()
    for k, c in enumerate(cases):
        adm = c.encounter.admittime.date().isoformat()
        corr, _ = apply_correction(db, c, "add_bill_line", {"line": dict(item_code=f"ZZ-EVAL-{k}-A", quantity=1,
                                   unit_price=750.0, service_date=adm, category="CONSUMABLE")}, "experiment", "inject",
                                   orchestrator=orch)
        line = corr.after["line_no"]
        issue = next((i for i in db.query(TrackedIssue).filter_by(case_id=c.id, code="UNKNOWN_BILL_ITEM")
                      if (i.identity or {}).get("line_no") == str(line)), None)
        if issue is None:
            res["injection_not_detected"] += 1
            continue
        t = db.get(ReviewTask, issue.task_id)
        kw = dict(evidence_fingerprint=task_fingerprint(t)) if fingerprint else {}
        if fingerprint:
            record_decision_on_issue(db, t, "ACCEPT_WITH_JUSTIFICATION", "experiment dismissal", "experiment")
        db.add(ReviewDecision(case_id=c.id, task_id=t.id, code=t.code, task_key=t.task_key,
                              decision="ACCEPT_WITH_JUSTIFICATION", note="experiment dismissal", actor="experiment", **kw))
        db.flush()
        orch.process(c, trigger="REVIEW_DECISION", changed_domains=["reviews"])
        db.refresh(issue)
        res["dismissed"] += issue.status == "DISMISSED"
        orch.process(c, trigger="MANUAL_RERUN", changed_domains=ALL)                       # control 1: same data
        db.refresh(issue)
        res["false_reopen_after_rerun"] += issue.status != "DISMISSED"
        apply_correction(db, c, "add_document", {"document": f"eval_note_{k}"}, "experiment", "immaterial change",
                         orchestrator=orch)                                                 # control 2: unrelated data
        db.refresh(issue)
        res["false_reopen_after_unrelated_change"] += issue.status != "DISMISSED"
        apply_correction(db, c, "update_bill_line", {"line_no": line, "fields": {"item_code": f"ZZ-EVAL-{k}-B"}},
                         "experiment", "material change", orchestrator=orch)               # material change
        db.refresh(issue)
        res["material_changes"] += 1
        if issue.status == "PENDING_REVIEW" and issue.reopen_count >= 1:
            res["reopened_on_material_change"] += 1
        elif issue.status == "DISMISSED":
            res["false_suppression"] += 1
        res["case_complete_despite_new_evidence"] += db.get(ClaimCase, c.id).status == "PREPARATION_COMPLETE"
    return dict(res)


def multi_line_injection(db, orch, n_cases=20):
    """Two bill lines with the SAME finding code on one encounter must stay two issues / two tasks; fixing one
    must leave the other pending."""
    res = Counter(dict.fromkeys(["cases", "two_separate_issues", "merged", "fix_one_other_still_pending"], 0))
    item = (db.query(RateCardItem).filter(~RateCardItem.category.in_(["ROOM", "ICU", "PROCEDURE"]))
            .order_by(RateCardItem.item_code).first())
    for c in db.query(ClaimCase).filter_by(status="PREPARATION_COMPLETE").order_by(ClaimCase.id.desc()).limit(n_cases).all():
        adm = c.encounter.admittime.date().isoformat()
        lines = []
        for mult, qty in ((2, 1.0), (3, 2.0)):
            corr, _ = apply_correction(db, c, "add_bill_line", {"line": dict(item_code=item.item_code, quantity=qty,
                                       unit_price=item.unit_price * mult, service_date=adm, category=item.category)},
                                       "experiment", "inject over-tariff line", orchestrator=orch)
            lines.append(corr.after["line_no"])
        issues = {(i.identity or {}).get("line_no"): i for i in db.query(TrackedIssue).filter_by(
            case_id=c.id, code="UNIT_PRICE_ABOVE_TARIFF")}
        res["cases"] += 1
        a, b = issues.get(str(lines[0])), issues.get(str(lines[1]))
        if a is not None and b is not None and a.id != b.id and a.task_id != b.task_id:
            res["two_separate_issues"] += 1
        else:
            res["merged"] += 1
            continue
        apply_correction(db, c, "update_bill_line", {"line_no": lines[0], "fields": {"unit_price": item.unit_price,
                         "amount": item.unit_price}}, "experiment", "fix first line", review_task_id=a.task_id,
                         orchestrator=orch)
        db.refresh(a), db.refresh(b)
        res["fix_one_other_still_pending"] += (a.status == "RESOLVED" and b.status == "PENDING_REVIEW"
                                               and db.get(ReviewTask, b.task_id).status == "OPEN")
    return dict(res)


# ---------------------------------------------------------------------------
# historical intelligence
# ---------------------------------------------------------------------------

def intake_features(base_db_path):
    eng, db = _session(base_db_path)
    try:
        corpus = CI.build_corpus(db, use_cache=False)
        return {f["hadm_id"]: f for f in corpus["encounters"].values()}
    finally:
        db.close()
        eng.dispose()


def evaluate_intel(db, intake, gt_issues, gt_corr, ks=(5, 10), k_pred=25, seed=0):
    reg = set(ERROR_TYPES)
    corpus = CI.build_corpus(db, use_cache=False)
    feature_keys = [k for k in next(iter(intake.values())) if k not in ("hadm_id",)]
    hadm_of = {}
    for cid, h in corpus["encounters"].items():
        f = intake[h["hadm_id"]]
        for k in feature_keys:
            if k in ("case_id", "case_ref", "processed", "status", "observed", "occurrences"):
                continue
            h[k] = f[k]                       # neighbour features at intake, outcomes from the history
        hadm_of[cid] = h["hadm_id"]
    gt_of = {cid: set(gt_issues.get(hadm_of[cid], set())) & reg for cid in corpus["encounters"]}
    rng = random.Random(seed)
    tp = fp = fn = 0
    rtp = rfp = rfn = 0
    pred_items = Counter()
    prec_at = {k: [] for k in ks}
    rand_at = {k: [] for k in ks}
    rec_status, rec_agree, rec_total = Counter(), Counter(), 0
    det_agree = Counter()
    ids = sorted(corpus["encounters"])
    first_codes = {}
    for cid in ids:
        first = {f.code for f in db.query(Finding).filter_by(case_id=cid).filter(Finding.severity.in_(ACTIONABLE))}
        first_codes[cid] = first
    for cid in ids:
        q = dict(corpus["encounters"][cid])
        truth = gt_of[cid]
        out = CI.predict(corpus, q, exclude=[cid], k_pred=k_pred, current_codes=frozenset())
        predicted = set(out.get("predicted_codes") or []) & reg
        tp += len(predicted & truth); fp += len(predicted - truth); fn += len(truth - predicted)
        for it in out.get("items", []):
            pred_items[it["status"]] += 1
        # random-neighbour baseline: same decision rule, k random processed encounters
        others = [x for x in ids if x != cid]
        sample = rng.sample(others, min(k_pred, len(others)))
        base_n = len(others)
        prevalence = Counter()
        for x in others:
            prevalence.update(corpus["encounters"][x]["observed"])
        rpred = set()
        for code in set().union(*(corpus["encounters"][x]["observed"] for x in sample)):
            hits = sum(1 for x in sample if code in corpus["encounters"][x]["observed"])
            lo, _ = CI.wilson(hits, len(sample))
            if lo > prevalence[code] / base_n:
                rpred.add(code)
        rpred &= reg
        rtp += len(rpred & truth); rfp += len(rpred - truth); rfn += len(truth - rpred)
        # retrieval precision@k with proxy relevance: neighbour shares >= 1 injected issue type with the query
        if truth:
            for k in ks:
                nbs = CI.neighbours(corpus, q, "general", k, exclude=[cid])
                prec_at[k].append(sum(1 for x in nbs if gt_of[x["h"]["case_id"]] & truth) / len(nbs))
                rs = rng.sample(others, k)
                rand_at[k].append(sum(1 for x in rs if gt_of[x] & truth) / k)
        # recommendations for the issues the deterministic pipeline found on this case
        codes = sorted(first_codes[cid] & reg)
        for r in CI.recommend(corpus, q, codes, exclude=[cid], k_rec=10):
            rec_total += 1
            rec_status[r["status"]] += 1
            if r["status"] == "RECOMMENDED":
                gt_ops = {o["op"] for o in gt_corr.get(hadm_of[cid], []) if o.get("error_type") == r["code"]}
                if gt_ops:
                    rec_agree["with_gt_op"] += 1
                    rec_agree["agree"] += r["action"]["op"] in gt_ops
        det = first_codes[cid] & reg
        det_agree["predicted_also_found_by_rules"] += len(predicted & det)
        det_agree["predicted_total"] += len(predicted)
    n_truth = sum(1 for c in ids if gt_of[c])
    return dict(
        n_queries=len(ids), queries_with_injected_issue=n_truth, k_pred=k_pred,
        issue_prediction=dict(similar_neighbours=_prf(tp, fp, fn), random_neighbours_baseline=_prf(rtp, rfp, rfn),
                              rule="predicted iff Wilson lower bound among k neighbours > base rate (pre-specified)"),
        prediction_item_status=dict(pred_items),
        insufficient_data_rate_items=round(pred_items["INSUFFICIENT_DATA"] / sum(pred_items.values()), 4) if pred_items else None,
        retrieval_precision_at_k={str(k): dict(similar=round(statistics.mean(v), 4) if v else None,
                                               random_baseline=round(statistics.mean(rand_at[k]), 4) if rand_at[k] else None,
                                               n_queries=len(v)) for k, v in prec_at.items()},
        retrieval_precision_proxy="a neighbour is counted relevant if it shares >= 1 injected (ground-truth) issue "
                                  "type with the query; there is no ground-truth similarity set",
        retrieval_recall_at_k=None,
        retrieval_recall_reason="not computable: no ground-truth set of 'truly similar' encounters exists",
        recommendations=dict(total=rec_total, by_status=dict(rec_status),
                             evidence_coverage=round(rec_status["RECOMMENDED"] / rec_total, 4) if rec_total else None,
                             insufficient_data_rate=round(rec_status["INSUFFICIENT_DATA"] / rec_total, 4) if rec_total else None,
                             agreement_with_ground_truth_op=dict(rec_agree,
                                                                 rate=round(rec_agree["agree"] / rec_agree["with_gt_op"], 4)
                                                                 if rec_agree["with_gt_op"] else None)),
        prediction_vs_rules=dict(det_agree),
        history_provenance="outcomes in the history were recorded by the experiment harness's ORACLE reviewer "
                           "(ground-truth corrections), not by human reviewers",
    )


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run_seed(data_dir, seed, workdir, log=print, k_pred=25):
    man = verify_manifest(data_dir)
    gt_issues, labels, gt_corr = harness._gt(data_dir)
    base = os.path.join(workdir, f"base_seed{seed}.db")
    hist = os.path.join(workdir, f"hist_seed{seed}.db")
    t0 = time.time()
    harness._make_base_db(base, data_dir)
    log(f"seed {seed}: base db ready ({time.time() - t0:.0f}s); running oracle history")
    harness.run_arm(base, hist, "C_parallel_targeted", harness.ARMS["C_parallel_targeted"], gt_issues, gt_corr)
    log(f"seed {seed}: history ready ({time.time() - t0:.0f}s)")
    intake = intake_features(base)
    root = os.path.abspath(os.path.join(os.path.dirname(harness.__file__), "..", "..", ".."))
    out = dict(seed=seed, data_dir=os.path.relpath(data_dir, root).replace(os.sep, "/"), manifest_check=man)
    eng, db = _session(hist)
    try:
        ctx = build_context(db)
        orch = Orchestrator(db, "parallel", "targeted", actor="experiment", ctx=ctx)
        out["history"] = dict(corrections=db.query(Correction).count(), decisions=db.query(ReviewDecision).count(),
                              tasks=db.query(ReviewTask).count(), issues=db.query(TrackedIssue).count())
        out["intel"] = evaluate_intel(db, intake, gt_issues, gt_corr, k_pred=k_pred, seed=seed)
        log(f"seed {seed}: intel evaluated ({time.time() - t0:.0f}s)")
        mem = dict(lifecycle_after_oracle_loop=lifecycle_consistency(db), false_suppression=false_suppression(db),
                   merge_check=merge_check(db))
        mem["repeated_detection"] = repeated_detection(db, orch)
        log(f"seed {seed}: repeated detection done ({time.time() - t0:.0f}s)")
        mem["reappearance"] = reappearance(db, orch)
        mem["false_suppression_after_reruns"] = false_suppression(db)
        mem["merge_check_after"] = merge_check(db)
        mem["audit_chain_ok"] = verify_audit_chain(db)["ok"]
        out["issue_memory"] = mem
    finally:
        db.close()
        eng.dispose()
    # dismissal + material evidence: same starting point, decisions with vs without evidence fingerprints
    for label, fp in (("with_fingerprint", True), ("without_fingerprint_pre_change_behaviour", False)):
        p = os.path.join(workdir, f"dismiss_{label}_seed{seed}.db")
        shutil.copyfile(hist, p)
        eng, db = _session(p)
        try:
            orch = Orchestrator(db, "parallel", "targeted", actor="experiment", ctx=build_context(db))
            out["issue_memory"].setdefault("dismissal_injection", {})[label] = dismissal_injection(db, orch, fingerprint=fp)
            if fp:
                out["issue_memory"]["multi_line_injection"] = multi_line_injection(db, orch)
        finally:
            db.close()
            eng.dispose()
        os.remove(p)
    out["manifest_check_after"] = verify_manifest(data_dir)
    out["elapsed_s"] = round(time.time() - t0, 1)
    for p in (base, hist):
        os.remove(p)
    return out


def aggregate(seeds):
    def vals(path):
        out = []
        for s in seeds:
            v = s
            for k in path:
                v = (v or {}).get(k) if isinstance(v, dict) else None
            if isinstance(v, (int, float)):
                out.append(v)
        return dict(mean=round(statistics.mean(out), 4) if out else None,
                    sd=round(statistics.stdev(out), 4) if len(out) > 1 else None, n=len(out))
    keys = {
        "intel.prediction.f1": ("intel", "issue_prediction", "similar_neighbours", "f1"),
        "intel.prediction.precision": ("intel", "issue_prediction", "similar_neighbours", "precision"),
        "intel.prediction.recall": ("intel", "issue_prediction", "similar_neighbours", "recall"),
        "intel.prediction.random_baseline.f1": ("intel", "issue_prediction", "random_neighbours_baseline", "f1"),
        "intel.precision_at_5.similar": ("intel", "retrieval_precision_at_k", "5", "similar"),
        "intel.precision_at_5.random": ("intel", "retrieval_precision_at_k", "5", "random_baseline"),
        "intel.precision_at_10.similar": ("intel", "retrieval_precision_at_k", "10", "similar"),
        "intel.precision_at_10.random": ("intel", "retrieval_precision_at_k", "10", "random_baseline"),
        "intel.recommendation.evidence_coverage": ("intel", "recommendations", "evidence_coverage"),
        "intel.recommendation.insufficient_data_rate": ("intel", "recommendations", "insufficient_data_rate"),
        "intel.recommendation.agreement_with_gt_op": ("intel", "recommendations", "agreement_with_ground_truth_op", "rate"),
        "intel.prediction_items.insufficient_data_rate": ("intel", "insufficient_data_rate_items"),
        "memory.reruns": ("issue_memory", "repeated_detection", "reruns"),
        "memory.tasks_created_during_reruns": ("issue_memory", "repeated_detection", "tasks_created_during_reruns"),
        "memory.open_task_duplicates": ("issue_memory", "repeated_detection", "open_task_duplicates"),
        "memory.duplicate_tasks_prevented": ("issue_memory", "repeated_detection", "duplicate_tasks_prevented"),
        "memory.repeat_detections_linked": ("issue_memory", "repeated_detection", "repeat_detections_linked"),
        "memory.reappearance.attempted": ("issue_memory", "reappearance", "attempted"),
        "memory.reappearance.reopened_same_issue": ("issue_memory", "reappearance", "reopened_same_issue"),
        "memory.reappearance.extra_issue_rows": ("issue_memory", "reappearance", "extra_issue_rows_for_same_code"),
        "memory.false_suppression_after_reruns": ("issue_memory", "false_suppression_after_reruns", "without_open_task"),
        "memory.identities_merged": ("issue_memory", "merge_check_after", "identities_merged"),
        "memory.resolved_without_correction": ("issue_memory", "lifecycle_after_oracle_loop", "resolved_without_correction"),
        "memory.dismissal.with_fp.reopened": ("issue_memory", "dismissal_injection", "with_fingerprint", "reopened_on_material_change"),
        "memory.dismissal.with_fp.false_suppression": ("issue_memory", "dismissal_injection", "with_fingerprint", "false_suppression"),
        "memory.dismissal.with_fp.false_reopen_rerun": ("issue_memory", "dismissal_injection", "with_fingerprint", "false_reopen_after_rerun"),
        "memory.dismissal.with_fp.false_reopen_unrelated": ("issue_memory", "dismissal_injection", "with_fingerprint", "false_reopen_after_unrelated_change"),
        "memory.dismissal.without_fp.false_suppression": ("issue_memory", "dismissal_injection", "without_fingerprint_pre_change_behaviour", "false_suppression"),
        "memory.dismissal.without_fp.case_complete_despite_new_evidence": ("issue_memory", "dismissal_injection", "without_fingerprint_pre_change_behaviour", "case_complete_despite_new_evidence"),
        "memory.dismissal.with_fp.case_complete_despite_new_evidence": ("issue_memory", "dismissal_injection", "with_fingerprint", "case_complete_despite_new_evidence"),
        "memory.multi_line.cases": ("issue_memory", "multi_line_injection", "cases"),
        "memory.multi_line.merged": ("issue_memory", "multi_line_injection", "merged"),
        "memory.multi_line.fix_one_other_still_pending": ("issue_memory", "multi_line_injection", "fix_one_other_still_pending"),
        "intel.history.corrections": ("history", "corrections"),
        "intel.history.decisions": ("history", "decisions"),
    }
    return {name: vals(path) for name, path in keys.items()}


def run(out_dir, runs, log=print, k_pred=25):
    """runs: [(results_subfolder, seed), ...] of EXISTING datasets."""
    root = os.path.abspath(os.path.join(os.path.dirname(harness.__file__), "..", "..", ".."))
    if os.path.exists(os.path.join(out_dir, "summary.json")):
        raise FileExistsError(f"{out_dir}/summary.json exists; choose a new output folder (results are never overwritten)")
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    seeds = []
    with tempfile.TemporaryDirectory(prefix="cc_intel_mem_") as work:
        for sub, seed in runs:
            data_dir = os.path.join(root, "results", "experiments", sub, f"data_seed{seed}")
            res = run_seed(data_dir, seed, work, log=log, k_pred=k_pred)
            res["group"] = sub
            seeds.append(res)
            with open(os.path.join(out_dir, f"seed{seed}.json"), "w", encoding="utf-8") as f:
                json.dump(res, f, indent=1, default=str)
    groups = sorted({s["group"] for s in seeds})
    summary = dict(
        config=dict(runs=runs, k_pred=k_pred, ks=[5, 10], min_corpus=CI.MIN_CORPUS, min_comparable=CI.MIN_COMPARABLE,
                    min_reviewed=CI.MIN_REVIEWED, profiles=CI.PROFILES),
        aggregate={g: aggregate([s for s in seeds if s["group"] == g]) for g in groups} | {"all": aggregate(seeds)},
        elapsed_s=round(time.time() - started, 1),
        notes=[
            "Datasets are the existing synthetic experiment cohorts (read only; MANIFEST hashes verified before and after).",
            "Historical outcomes come from the harness's oracle reviewer (ground-truth corrections), not humans.",
            "Similarity weights were fixed before evaluation (no tuning); seeds 4-6 are a held-out repetition.",
            "Synthetic errors are injected at random with respect to clinical features, so little predictive signal "
            "from similarity is expected; the random-neighbour baseline is reported alongside.",
            "Retrieval recall@K is not reported: no ground-truth similarity set exists.",
            "Recommendation agreement with the ground-truth correction op is near-tautological on this data: each "
            "synthetic error type has one canonical correction op in the generator, and the oracle history applied "
            "exactly those ops. It shows the recommendation cites the recorded action correctly, not clinical insight.",
        ])
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    return summary
