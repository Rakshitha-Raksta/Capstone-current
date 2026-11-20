"""
Persistent issue memory and idempotent review routing.

Scenarios are built with real correction operations on a small synthetic cohort (no hand-written
rows), so every finding, task and issue below was produced by the actual pipeline.
"""
import os
import subprocess
import threading
from collections import Counter

import pytest
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.clearclaim import dag
from app.clearclaim.context import build_context, invalidate_context
from app.clearclaim.corrections import apply_correction
from app.clearclaim.issues import IssueTracker, add_task_note, backfill_all
from app.clearclaim.models import (
    AuditEvent, CCBase, ClaimCase, Correction, IssueEvent, PipelineRun, RateCardItem, ReviewDecision, ReviewTask,
    StageExecution, TrackedIssue,
)
from app.clearclaim.orchestrator import Orchestrator, verify_audit_chain
from app.database.db import SessionLocal, engine
from app.datasets.synthetic import generate_synthetic_dataset

N = 30
ALL = list(dag.ALL_INPUT_DOMAINS)


@pytest.fixture(scope="module")
def cohort(tmp_path_factory):
    from app.clearclaim.etl import import_dataset
    d = tmp_path_factory.mktemp("issue_mem")
    generate_synthetic_dataset(str(d), seed=31, n_encounters=N, error_rate=0.6)
    CCBase.metadata.drop_all(engine)
    CCBase.metadata.create_all(engine)
    invalidate_context()
    db = SessionLocal()
    imp = import_dataset(db, str(d), str(d), "demo", "SYNTHETIC")
    assert imp.status == "COMPLETED", imp.validation_report
    orch = Orchestrator(db, "parallel", "targeted")
    for c in db.query(ClaimCase).order_by(ClaimCase.id):
        orch.process(c)
    db.close()
    yield d
    CCBase.metadata.drop_all(engine)
    invalidate_context()


_used = set()


def _clean_case(db):
    """A PREPARATION_COMPLETE case not used by another test (scenarios are then unambiguous)."""
    for c in db.query(ClaimCase).filter_by(status="PREPARATION_COMPLETE").order_by(ClaimCase.id):
        if c.id not in _used:
            _used.add(c.id)
            return c
    pytest.skip("no unused clean case in the cohort")


def _priced_item(db):
    return (db.query(RateCardItem).filter(~RateCardItem.category.in_(["ROOM", "ICU", "PROCEDURE"]))
            .order_by(RateCardItem.item_code).first())


def _add_line(db, c, item_code, price, qty=1.0, reason="test line"):
    adm = c.encounter.admittime.date().isoformat()
    corr, run = apply_correction(db, c, "add_bill_line", {"line": dict(item_code=item_code, quantity=qty, unit_price=price,
                                                                         service_date=adm, category="LAB")},
                                 "tester", reason)
    return corr.after["line_no"]


def _issue(db, c, code, line_no=None):
    q = [i for i in db.query(TrackedIssue).filter_by(case_id=c.id, code=code)]
    if line_no is not None:
        q = [i for i in q if (i.identity or {}).get("line_no") == str(line_no)]
    assert len(q) == 1, q
    return q[0]


def _open_dups(db, case_id=None):
    q = db.query(ReviewTask.case_id, ReviewTask.task_key, func.count(ReviewTask.id)).filter(ReviewTask.status == "OPEN")
    if case_id:
        q = q.filter(ReviewTask.case_id == case_id)
    return sum(n - 1 for _, _, n in q.group_by(ReviewTask.case_id, ReviewTask.task_key).all() if n > 1)


# ---------------------------------------------------------------------------
# 6. repeated detections do not duplicate review tasks
# ---------------------------------------------------------------------------

def test_initial_runs_create_one_issue_per_task(cohort):
    db = SessionLocal()
    tasks = db.query(ReviewTask).all()
    assert tasks and all(t.issue_id is not None for t in tasks)
    assert db.query(TrackedIssue).count() == len({(t.case_id, t.issue_id) for t in tasks})
    assert _open_dups(db) == 0
    db.close()


def test_repeated_runs_do_not_duplicate_tasks(cohort):
    db = SessionLocal()
    c = (db.query(ClaimCase).join(ReviewTask, ReviewTask.case_id == ClaimCase.id).filter(ReviewTask.status == "OPEN")
         .order_by(ClaimCase.id).first())
    _used.add(c.id)
    before = {t.id for t in db.query(ReviewTask).filter_by(case_id=c.id, status="OPEN")}
    n_all = db.query(ReviewTask).filter_by(case_id=c.id).count()
    det0 = {i.id: i.detection_count for i in db.query(TrackedIssue).filter_by(case_id=c.id)}
    for policy in ("full", "full", "targeted", "targeted", "full"):
        Orchestrator(db, "parallel", policy).process(c, trigger="MANUAL_RERUN", changed_domains=ALL)
    after = {t.id for t in db.query(ReviewTask).filter_by(case_id=c.id, status="OPEN")}
    assert after == before                                     # same tasks, not recreated
    assert db.query(ReviewTask).filter_by(case_id=c.id).count() == n_all
    for i in db.query(TrackedIssue).filter_by(case_id=c.id, status="PENDING_REVIEW"):
        assert i.detection_count == det0[i.id] + 5
        assert i.last_dedup_run_id == c.last_run_id
    ev = db.query(AuditEvent).filter_by(case_id=c.id, event_type="REVIEW_TASK_DEDUPLICATED").all()
    assert len(ev) == 5 and all(e.details["items"] for e in ev)
    db.close()


# ---------------------------------------------------------------------------
# 7. pending tasks keep owner, assignee and reviewer notes
# ---------------------------------------------------------------------------

def test_pending_task_keeps_owner_and_notes(cohort):
    db = SessionLocal()
    t = db.query(ReviewTask).filter_by(status="OPEN").order_by(ReviewTask.id.desc()).first()
    c = db.get(ClaimCase, t.case_id)
    add_task_note(db, t, "called billing desk, awaiting revised bill", "reviewer.a", assignee="billing.lead")
    db.commit()
    created, owner = t.created_at, t.owner_stage
    for _ in range(3):
        Orchestrator(db, "parallel", "full").process(c, trigger="MANUAL_RERUN", changed_domains=ALL)
    db.refresh(t)
    issue = db.get(TrackedIssue, t.issue_id)
    assert t.status == "OPEN" and t.assignee == "billing.lead" and t.created_at == created and t.owner_stage == owner
    assert issue.task_id == t.id and issue.reviewer == "billing.lead"
    assert [n["note"] for n in issue.reviewer_notes] == ["called billing desk, awaiting revised bill"]
    assert db.query(ReviewTask).filter_by(case_id=c.id, issue_id=issue.id).count() == 1
    db.close()


def test_api_marks_pending_task_as_already_routed(cohort, client):
    db = SessionLocal()
    t = db.query(ReviewTask).filter_by(status="OPEN").order_by(ReviewTask.id).first()
    task_id, case_id, issue_id = t.id, t.case_id, t.issue_id
    Orchestrator(db, "parallel", "full").process(db.get(ClaimCase, case_id), trigger="MANUAL_RERUN", changed_domains=ALL)
    db.close()
    d = client.get(f"/api/v1/cases/{case_id}").json()
    row = next(x for x in d["tasks"] if x["id"] == task_id)
    assert row["issue"]["lifecycle"] in ("UNDER_REVIEW", "REOPENED")
    assert "still pending" in row["issue"]["dedup_note"] and "no duplicate task created" in row["issue"]["dedup_note"]
    iss = next(i for i in d["issues"] if i["id"] == issue_id)
    assert iss["status"] == "PENDING_REVIEW" and iss["department"]


# ---------------------------------------------------------------------------
# 8 + 9. resolved stays resolved while absent; genuine reappearance reopens the same issue
# ---------------------------------------------------------------------------

def test_resolved_issue_stays_resolved_then_reopens_on_reappearance(cohort):
    db = SessionLocal()
    c = _clean_case(db)
    item = _priced_item(db)
    line = _add_line(db, c, item.item_code, item.unit_price * 2)
    issue = _issue(db, c, "UNIT_PRICE_ABOVE_TARIFF", line)
    task = db.get(ReviewTask, issue.task_id)
    assert issue.status == "PENDING_REVIEW" and task.status == "OPEN"

    corr, _ = apply_correction(db, c, "update_bill_line", {"line_no": line, "fields": {"unit_price": item.unit_price,
                               "amount": item.unit_price}}, "coder.b", "reprice at tariff", review_task_id=task.id)
    db.refresh(issue), db.refresh(task)
    assert issue.status == "RESOLVED" and not issue.is_present
    assert issue.resolution_action["kind"] == "CORRECTION" and issue.resolution_action["correction_id"] == corr.id
    assert issue.resolution_action["linked_to_this_issue"] is True
    assert task.status == "RESOLVED_BY_CORRECTION" and issue.review_attempts == 1

    n_tasks = db.query(ReviewTask).filter_by(case_id=c.id).count()
    for _ in range(2):   # 8: still absent -> stays resolved, no task
        Orchestrator(db, "parallel", "full").process(c, trigger="MANUAL_RERUN", changed_domains=ALL)
    db.refresh(issue)
    assert issue.status == "RESOLVED" and db.query(ReviewTask).filter_by(case_id=c.id).count() == n_tasks

    # 9: the same line is priced above the rate card again -> SAME issue reopened, traceable
    apply_correction(db, c, "update_bill_line", {"line_no": line, "fields": {"unit_price": item.unit_price * 3,
                     "amount": item.unit_price * 3}}, "clerk.c", "price re-entered")
    db.refresh(issue), db.refresh(task)
    assert issue.status == "PENDING_REVIEW" and issue.reopen_count == 1 and issue.is_present
    assert task.status == "RESOLVED_BY_CORRECTION"                       # old occurrence keeps its outcome
    new = db.get(ReviewTask, issue.task_id)
    assert new.id != task.id and new.status == "OPEN" and new.issue_id == issue.id
    reopened = db.query(IssueEvent).filter_by(issue_id=issue.id, event="REOPENED").one()
    assert reopened.details["previous"]["status"] == "RESOLVED"
    assert reopened.details["previous"]["resolution_action"]["correction_id"] == corr.id
    assert "amount" in reopened.details["evidence_changes"] or "actual" in reopened.details["evidence_changes"]
    assert db.query(AuditEvent).filter_by(case_id=c.id, event_type="ISSUE_REOPENED").count() == 1
    assert db.query(TrackedIssue).filter_by(case_id=c.id, code="UNIT_PRICE_ABOVE_TARIFF").count() == 1
    db.close()


def test_disappearing_without_correction_is_not_claimed_resolved(cohort):
    db = SessionLocal()
    c = _clean_case(db)
    item = _priced_item(db)
    line = _add_line(db, c, item.item_code, round(item.unit_price * 1.10, 2))
    issue = _issue(db, c, "UNIT_PRICE_ABOVE_TARIFF", line)
    ctx = build_context(db)
    ctx.settings["billing_price_tolerance_pct"] = 0.25          # a settings change, not a data correction
    Orchestrator(db, "parallel", "full", ctx=ctx).process(c, trigger="MANUAL_RERUN", changed_domains=ALL)
    db.refresh(issue)
    task = db.get(ReviewTask, issue.task_id)
    assert issue.status == "NO_LONGER_DETECTED" and issue.resolved_at is None and issue.resolved_by is None
    assert issue.resolution_action["kind"] == "NOT_DETECTED"
    assert task.status == "SUPERSEDED"
    db.close()


# ---------------------------------------------------------------------------
# 10. dismissed issues: kept while unchanged, reopened on materially new evidence
# ---------------------------------------------------------------------------

def test_dismissed_issue_reopens_only_on_new_evidence(cohort, client):
    db = SessionLocal()
    c = _clean_case(db)
    line = _add_line(db, c, "ZZ-TEST-UNKNOWN-1", 1000.0)
    issue = _issue(db, c, "UNKNOWN_BILL_ITEM", line)
    task_id = issue.task_id
    db.close()
    r = client.post(f"/api/v1/review-tasks/{task_id}/decision",
                    json=dict(decision="ACCEPT_WITH_JUSTIFICATION", note="consumable billed under a local code, verified"))
    assert r.status_code == 200, r.text
    assert r.json()["case"]["status"] == "PREPARATION_COMPLETE"
    db = SessionLocal()
    c = db.get(ClaimCase, c.id)
    issue = db.get(TrackedIssue, issue.id)
    assert issue.status == "DISMISSED" and issue.review_outcome == "ACCEPTED_WITH_JUSTIFICATION"
    assert db.query(ReviewDecision).filter_by(task_id=task_id).one().evidence_fingerprint
    n_tasks = db.query(ReviewTask).filter_by(case_id=c.id).count()
    for _ in range(2):  # same evidence: dismissal preserved, nothing re-routed
        Orchestrator(db, "parallel", "full").process(c, trigger="MANUAL_RERUN", changed_domains=ALL)
    db.refresh(issue)
    assert issue.status == "DISMISSED" and db.query(ReviewTask).filter_by(case_id=c.id).count() == n_tasks
    assert c.status == "PREPARATION_COMPLETE"

    # materially new evidence on the SAME line: a different unknown item code
    apply_correction(db, c, "update_bill_line", {"line_no": line, "fields": {"item_code": "ZZ-TEST-UNKNOWN-2"}},
                     "clerk.d", "item code re-entered")
    db.refresh(issue)
    assert issue.status == "PENDING_REVIEW" and issue.reopen_count == 1
    new = db.get(ReviewTask, issue.task_id)
    assert new.id != task_id and new.status == "OPEN"
    assert db.get(ReviewTask, task_id).status == "ACCEPTED_WITH_JUSTIFICATION"   # the old decision stays on record
    ev = db.query(IssueEvent).filter_by(issue_id=issue.id, event="REOPENED").one()
    assert ev.details["previous"]["status"] == "DISMISSED"
    assert ev.details["evidence_changes"]["item_code"]["after"] == "ZZ-TEST-UNKNOWN-2"
    assert ev.details["prior_decision"]["status"] == "NOT_APPLIED_EVIDENCE_CHANGED"
    assert c.status == "NEEDS_REVIEW"                       # not silently left as complete
    case_id, new_id = c.id, new.id
    db.close()
    rows = {t["id"]: t for t in client.get(f"/api/v1/cases/{case_id}").json()["tasks"]}
    assert rows[task_id]["issue"]["is_current_task"] is False and rows[task_id]["issue"]["dedup_note"] is None
    assert rows[new_id]["issue"]["is_current_task"] is True and rows[new_id]["issue"]["lifecycle"] == "REOPENED"


def test_legacy_decisions_without_fingerprint_keep_original_behaviour(cohort):
    db = SessionLocal()
    c = _clean_case(db)
    line = _add_line(db, c, "ZZ-TEST-UNKNOWN-3", 500.0)
    t = db.get(ReviewTask, _issue(db, c, "UNKNOWN_BILL_ITEM", line).task_id)
    db.add(ReviewDecision(case_id=c.id, task_id=t.id, code=t.code, task_key=t.task_key,
                          decision="ACCEPT_WITH_JUSTIFICATION", note="recorded before fingerprints", actor="legacy"))
    db.flush()
    Orchestrator(db).process(c, trigger="REVIEW_DECISION", changed_domains=["reviews"])
    apply_correction(db, c, "update_bill_line", {"line_no": line, "fields": {"item_code": "ZZ-TEST-UNKNOWN-4"}},
                     "clerk", "item code re-entered")
    issue = _issue(db, c, "UNKNOWN_BILL_ITEM", line)
    assert issue.status == "DISMISSED"           # unchanged semantics for pre-existing decisions
    db.close()


# ---------------------------------------------------------------------------
# 11. different bill lines with the same code never merge
# ---------------------------------------------------------------------------

def test_different_bill_lines_are_separate_issues(cohort):
    db = SessionLocal()
    c = _clean_case(db)
    item = _priced_item(db)
    l1 = _add_line(db, c, item.item_code, item.unit_price * 2, qty=1.0)
    l2 = _add_line(db, c, item.item_code, item.unit_price * 4, qty=2.0)
    i1, i2 = _issue(db, c, "UNIT_PRICE_ABOVE_TARIFF", l1), _issue(db, c, "UNIT_PRICE_ABOVE_TARIFF", l2)
    assert i1.id != i2.id and i1.issue_key != i2.issue_key and i1.task_id != i2.task_id
    apply_correction(db, c, "update_bill_line", {"line_no": l1, "fields": {"unit_price": item.unit_price,
                     "amount": item.unit_price}}, "coder", "reprice line 1", review_task_id=i1.task_id)
    db.refresh(i1), db.refresh(i2)
    assert i1.status == "RESOLVED" and i2.status == "PENDING_REVIEW" and i2.is_present
    assert db.get(ReviewTask, i2.task_id).status == "OPEN"
    db.close()


def test_rule_set_change_rekeys_the_same_task(cohort):
    db = SessionLocal()
    with_late = {cid for (cid,) in db.query(TrackedIssue.case_id).filter_by(code="LATE_FILING")}
    c = next(x for x in db.query(ClaimCase).order_by(ClaimCase.id) if x.id not in with_late)
    run = PipelineRun(case_id=c.id, trigger="MANUAL_RERUN", execution_mode="parallel", rerun_policy="full", status="SUCCEEDED")
    try:
        db.add(run)
        db.flush()
        t1 = dict(task_key="k-rules-A:LATE_FILING", code="LATE_FILING", severity="REVIEW", title="Claim filed after deadline",
                  stage="eligibility", owner_stage="eligibility", input_domain="claim_admin", reason="late",
                  evidence=dict(rule_id="R-1", expected="<= 30 days", actual="45 days"), recommended_action="x",
                  required_reruns=[], allowed_decisions=["ACCEPT_WITH_JUSTIFICATION"], state="OPEN")
        IssueTracker(db, c, run, "MANUAL_RERUN").sync([t1])
        t2 = dict(t1, task_key="k-rules-AB:LATE_FILING",
                  evidence=dict(t1["evidence"], rules=[{"rule_id": "R-1"}, {"rule_id": "R-2"}]))
        IssueTracker(db, c, run, "MANUAL_RERUN").sync([t2])
        db.flush()
        issue = db.query(TrackedIssue).filter_by(case_id=c.id, code="LATE_FILING").one()
        rows = db.query(ReviewTask).filter_by(case_id=c.id, code="LATE_FILING").all()
        assert len(rows) == 1 and rows[0].task_key == "k-rules-AB:LATE_FILING" and rows[0].status == "OPEN"
        assert db.query(IssueEvent).filter_by(issue_id=issue.id, event="TASK_REKEYED").count() == 1
        assert issue.detection_count == 2 and issue.evidence_fingerprint is not None
    finally:
        db.rollback()                 # synthetic tasks only; leave the cohort untouched
        db.close()


# ---------------------------------------------------------------------------
# 12. concurrency: reruns from separate sessions, and the database-level guarantee
# ---------------------------------------------------------------------------

def test_concurrent_reruns_do_not_duplicate_tasks(cohort):
    db = SessionLocal()
    c = (db.query(ClaimCase).join(ReviewTask, ReviewTask.case_id == ClaimCase.id).filter(ReviewTask.status == "OPEN")
         .order_by(ClaimCase.id.desc()).first())
    cid = c.id
    open_before = {t.id for t in db.query(ReviewTask).filter_by(case_id=cid, status="OPEN")}
    db.close()
    errors = []

    def work():
        s = SessionLocal()
        try:
            for _ in range(2):
                Orchestrator(s, "parallel", "full").process(s.get(ClaimCase, cid), trigger="MANUAL_RERUN", changed_domains=ALL)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
            s.rollback()
        finally:
            s.close()

    threads = [threading.Thread(target=work) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    db = SessionLocal()
    assert _open_dups(db, cid) == 0
    assert {t.id for t in db.query(ReviewTask).filter_by(case_id=cid, status="OPEN")} == open_before
    keys = Counter(i.issue_key for i in db.query(TrackedIssue).filter_by(case_id=cid))
    assert all(v == 1 for v in keys.values())
    cur = Counter(s for (s,) in db.query(StageExecution.stage).filter_by(case_id=cid, is_current=True))
    assert all(v == 1 for v in cur.values()), cur          # the case lock also prevents stale planning
    assert db.query(PipelineRun).filter_by(case_id=cid, status="SUCCEEDED").count() >= 10
    db.close()


def test_database_rejects_a_second_open_task_for_the_same_issue(cohort):
    db = SessionLocal()
    try:
        t = db.query(ReviewTask).filter_by(status="OPEN").first()
        db.add(ReviewTask(case_id=t.case_id, task_key=t.task_key, code=t.code, severity=t.severity, status="OPEN"))
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_insert_race_adopts_the_existing_issue(cohort):
    db = SessionLocal()
    try:
        i = db.query(TrackedIssue).first()
        c = db.get(ClaimCase, i.case_id)
        tr = IssueTracker(db, c, PipelineRun(id=0), "MANUAL_RERUN")
        dup = TrackedIssue(case_id=i.case_id, encounter_id=i.encounter_id, issue_key=i.issue_key, code=i.code,
                           status="OPEN", is_present=True, detection_count=1, review_attempts=0, reopen_count=0,
                           backfilled=False)
        row, created = tr._insert(dup, lambda: db.query(TrackedIssue).filter_by(case_id=i.case_id,
                                                                                issue_key=i.issue_key).first())
        assert created is False and row.id == i.id and tr.report["insert_conflicts_adopted"] == 1
    finally:
        db.rollback()
        db.close()


# ---------------------------------------------------------------------------
# 13 + 14. correction/targeted rerun intact; audit chain, backfill, migration, recorded results
# ---------------------------------------------------------------------------

def test_targeted_correction_rerun_still_targeted(cohort):
    db = SessionLocal()
    c = _clean_case(db)
    item = _priced_item(db)
    _, run = apply_correction(db, c, "add_bill_line", {"line": dict(item_code=item.item_code, quantity=1,
                              unit_price=item.unit_price * 2, service_date=c.encounter.admittime.date().isoformat())},
                              "tester", "targeted check")
    assert run.rerun_policy == "targeted" and run.invalidated_stages == dag.invalidated_by(["bill"])
    executed = {s.stage for s in db.query(StageExecution).filter_by(run_id=run.id, status="SUCCEEDED")}
    assert "billing" in executed and not ({"data_validation", "discharge_readiness"} & executed)
    assert db.query(Correction).filter_by(case_id=c.id).count() >= 1
    db.close()


def test_audit_chain_intact_and_backfill_idempotent(cohort):
    db = SessionLocal()
    assert verify_audit_chain(db)["ok"] is True
    assert backfill_all(db) == {}                  # every task already linked; running it changes nothing
    db.close()


def test_backfill_links_pre_issue_memory_tasks(cohort):
    db = SessionLocal()
    c = db.query(ClaimCase).join(ReviewTask, ReviewTask.case_id == ClaimCase.id).order_by(ClaimCase.id).first()
    # simulate data recorded before issue memory: unlink this case's tasks and drop its issues
    ids = [i.id for i in db.query(TrackedIssue).filter_by(case_id=c.id)]
    db.query(ReviewTask).filter_by(case_id=c.id).update({"issue_id": None})
    db.query(IssueEvent).filter(IssueEvent.issue_id.in_(ids)).delete(synchronize_session=False)
    db.query(TrackedIssue).filter_by(case_id=c.id).delete()
    db.flush()
    rep = backfill_all(db)
    assert rep["tasks_linked"] == db.query(ReviewTask).filter_by(case_id=c.id).count()
    for i in db.query(TrackedIssue).filter_by(case_id=c.id):
        assert i.backfilled and i.detection_count >= 1
        if i.status == "NO_LONGER_DETECTED":
            assert i.resolved_at is None          # never claimed resolved without a correction
    db.close()


def test_note_on_a_pre_issue_memory_task_is_not_lost(cohort):
    db = SessionLocal()
    t = db.query(ReviewTask).filter_by(status="OPEN").order_by(ReviewTask.id.desc()).first()
    c = db.get(ClaimCase, t.case_id)
    ids = [i.id for i in db.query(TrackedIssue).filter_by(case_id=c.id)]
    db.query(ReviewTask).filter_by(case_id=c.id).update({"issue_id": None})          # as recorded before the feature
    db.query(IssueEvent).filter(IssueEvent.issue_id.in_(ids)).delete(synchronize_session=False)
    db.query(TrackedIssue).filter_by(case_id=c.id).delete()
    db.commit()
    db.refresh(t)
    issue = add_task_note(db, t, "legacy task: note must survive", "reviewer.e", assignee="coder.f")
    db.commit()
    assert issue is not None and t.issue_id == issue.id
    Orchestrator(db, "parallel", "full").process(c, trigger="MANUAL_RERUN", changed_domains=ALL)
    db.refresh(t), db.refresh(issue)
    assert t.status == "OPEN" and t.assignee == "coder.f"
    assert [n["note"] for n in issue.reviewer_notes] == ["legacy task: note must survive"]
    assert db.query(ReviewTask).filter_by(case_id=c.id, status="OPEN").count() == \
        db.query(TrackedIssue).filter_by(case_id=c.id, status="PENDING_REVIEW").count()
    db.close()


def test_migration_is_additive_on_a_previous_schema(tmp_path):
    import sqlite3
    from alembic import command
    from alembic.config import Config
    from app.clearclaim.config import settings
    path = tmp_path / "pre_issue_memory.db"
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    old = settings.database_url
    settings.database_url = f"sqlite:///{path}"
    try:
        command.upgrade(cfg, "6cddb53e533a")
        con = sqlite3.connect(path)
        con.execute("INSERT INTO cc_encounters (id, subject_id, hadm_id, data_source) VALUES (1, 7, 70, 'SYNTHETIC')")
        con.execute("INSERT INTO cc_cases (id, encounter_id, case_ref, status, version) VALUES (1, 1, 'CC-70', 'BLOCKED', 0)")
        con.execute("INSERT INTO cc_review_tasks (id, case_id, task_key, code, severity, status) "
                    "VALUES (1, 1, 'k:DUPLICATE_BILL_ITEM', 'DUPLICATE_BILL_ITEM', 'BLOCKING', 'OPEN')")
        con.execute("INSERT INTO cc_review_decisions (id, case_id, task_id, code, task_key, decision, note, actor) "
                    "VALUES (1, 1, 1, 'DUPLICATE_BILL_ITEM', 'k:DUPLICATE_BILL_ITEM', 'CONFIRM_BLOCKED', 'n', 'a')")
        con.commit()
        before = [con.execute(f"SELECT * FROM {t}").fetchall() for t in ("cc_cases", "cc_review_tasks", "cc_review_decisions")]
        con.close()
        command.upgrade(cfg, "head")
        con = sqlite3.connect(path)
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "c4e8a1f2b9d3"
        after = [con.execute(f"SELECT * FROM {t}").fetchall() for t in ("cc_cases", "cc_review_tasks", "cc_review_decisions")]
        assert after[0] == before[0]
        assert [r[:-1] for r in after[1]] == before[1] and after[1][0][-1] is None       # new issue_id column is NULL
        assert [r[:-1] for r in after[2]] == before[2] and after[2][0][-1] is None       # new fingerprint column is NULL
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO cc_review_tasks (case_id, task_key, code, severity, status) "
                        "VALUES (1, 'k:DUPLICATE_BILL_ITEM', 'DUPLICATE_BILL_ITEM', 'BLOCKING', 'OPEN')")
        con.close()
    finally:
        settings.database_url = old


def test_recorded_experiment_results_untouched():
    """The committed experiment summaries must be byte-identical to the repository version."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    try:
        listed = subprocess.run(["git", "ls-files", "-s", "results/experiments"], cwd=root, capture_output=True,
                                text=True, timeout=60, check=True).stdout.splitlines()
    except Exception:  # noqa: BLE001
        pytest.skip("git not available")
    files = [ln.split("\t", 1)[1] for ln in listed if ln.endswith(("summary.json", "verification.json"))]
    assert files
    changed = subprocess.run(["git", "diff", "--name-only", "--", *files], cwd=root, capture_output=True, text=True,
                             timeout=60).stdout.split()
    assert changed == []
    from app.clearclaim.intel import research_summary
    runs = research_summary()["runs"]
    assert all(r["available"] for r in runs)
