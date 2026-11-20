"""
Persistent issue memory and idempotent review-task routing.

Called by the orchestrator at the end of every successful run (in place of the former
_sync_tasks), inside the run's transaction, AFTER the deterministic stages have produced their
findings. It never changes a finding, a root/consequence role or a case status: it only decides
whether the review work those findings require already exists.

Per routed task (one per distinct issue identity, see identity.py):

  issue not seen before             -> create issue + review task (or adopt a pre-existing task
                                       with the same key, for data created before issue memory)
  issue OPEN / PENDING_REVIEW       -> update last-detected time and detection count, refresh the
                                       evidence, KEEP the task, its owner, notes and history
                                       (deduplication, recorded in the audit trail)
  issue DISMISSED                   -> dismissal kept while the evidence is unchanged; if the
                                       routing stage no longer applies the decision (material
                                       evidence changed) the issue is reopened with a new task
  issue RESOLVED / NO_LONGER_DETECTED -> genuinely detected again: reopened, new task, the
                                       previous resolution kept in the issue history

Issues that are no longer routed:
  still among the current findings  -> OPEN (present, explained as a consequence), task superseded
  gone after a CORRECTION run       -> RESOLVED, resolution_action = that correction
  gone after any other run          -> NO_LONGER_DETECTED (never claimed resolved without a correction)
  DISMISSED                         -> dismissal preserved, marked not present

Idempotency under concurrency: the orchestrator locks the case row before reading any state; in
addition cc_issues has UNIQUE(case_id, issue_key) and cc_review_tasks has partial unique indexes
(one OPEN task per case+task_key and per issue). Inserts run inside a SAVEPOINT; if a concurrent
writer won the race, the existing row is adopted instead of failing the run.
"""
import datetime as dt
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clearclaim import dag
from app.database.db import get_db
from app.clearclaim.identity import evidence_fingerprint, fingerprint_diff, identity_of, issue_key
from app.clearclaim.models import (
    ClaimCase, Correction, IssueEvent, PipelineRun, ReviewDecision, ReviewTask, TrackedIssue,
)

ACTIVE_TASK = ("OPEN", "ACCEPTED_WITH_JUSTIFICATION", "CONFIRMED_BLOCKED")
ACTIVE_ISSUE = ("OPEN", "PENDING_REVIEW")
ISSUE_STATUSES = ("OPEN", "PENDING_REVIEW", "RESOLVED", "DISMISSED", "NO_LONGER_DETECTED")
STATE_TO_ISSUE = {"OPEN": "PENDING_REVIEW", "ACCEPTED_WITH_JUSTIFICATION": "DISMISSED", "CONFIRMED_BLOCKED": "OPEN"}


def _now():
    return dt.datetime.utcnow()


def department(owner_stage: Optional[str]) -> Optional[str]:
    spec = dag.STAGES.get(owner_stage or "")
    return spec["owner"] if spec else owner_stage


def _append(lst, item, cap=50):
    out = list(lst or [])
    out.append(item)
    return out[-cap:]


def _event(db, issue, event, run_id=None, task_id=None, from_status=None, to_status=None, actor="system", **details):
    db.add(IssueEvent(issue_id=issue.id, case_id=issue.case_id, run_id=run_id, task_id=task_id, event=event,
                      from_status=from_status, to_status=to_status, actor=actor, details=details or None, ts=_now()))


class IssueTracker:
    def __init__(self, db, case: ClaimCase, run: PipelineRun, trigger: str, actor: str = "system",
                 correction: Optional[Correction] = None, audit=None):
        self.db, self.case, self.run, self.trigger, self.actor = db, case, run, trigger, actor
        self.correction = correction
        self.audit = audit
        self.report = Counter()
        self.retained = []

    # ------------------------------------------------------------------ inserts (race-safe)
    def _insert(self, obj, lookup):
        """Insert inside a SAVEPOINT; on a uniqueness conflict (a concurrent run inserted the same
        identity first) return the existing row instead. Returns (row, created)."""
        try:
            with self.db.begin_nested():
                self.db.add(obj)
                self.db.flush()
            return obj, True
        except IntegrityError:
            existing = lookup()
            if existing is None:
                raise
            self.report["insert_conflicts_adopted"] += 1
            return existing, False

    def _new_task(self, issue, t):
        row = ReviewTask(case_id=self.case.id, task_key=t["task_key"], code=t["code"], severity=t["severity"],
                         owner_stage=t["owner_stage"], input_domain=t["input_domain"], title=t["title"],
                         reason=t["reason"], evidence=t["evidence"], recommended_action=t["recommended_action"],
                         required_reruns=t["required_reruns"], allowed_decisions=t["allowed_decisions"],
                         status="OPEN" if t["state"] == "OPEN" else t["state"], created_run_id=self.run.id,
                         issue_id=issue.id)
        if row.status == "OPEN":
            lookup = lambda: (self.db.query(ReviewTask)  # noqa: E731
                              .filter(ReviewTask.case_id == self.case.id, ReviewTask.status == "OPEN",
                                      (ReviewTask.task_key == t["task_key"]) | (ReviewTask.issue_id == issue.id))
                              .first())
            row, created = self._insert(row, lookup)
        else:
            self.db.add(row)
            self.db.flush()
            created = True
        if not created:
            row.issue_id = issue.id
            return row
        self.report["tasks_created"] += 1
        if self.audit:
            self.audit(self.db, "REVIEW_TASK_CREATED", f"{t['code']}: {t['title']}", "system", self.case.id,
                       "review_task", row.id, severity=t["severity"], owner_stage=t["owner_stage"], issue_id=issue.id,
                       issue_key=issue.issue_key)
        return row

    # ------------------------------------------------------------------ main entry
    def sync(self, tasks, current_keys=frozenset()):
        db, case, run = self.db, self.case, self.run
        now = _now()
        issues = {i.issue_key: i for i in db.query(TrackedIssue).filter_by(case_id=case.id)}
        rows = {r.id: r for r in db.query(ReviewTask).filter_by(case_id=case.id)}
        legacy_active = {r.task_key: r for r in rows.values() if r.issue_id is None and r.status in ACTIVE_TASK}
        seen_issue, seen_task_keys = set(), set()

        for t in tasks:
            key = issue_key(t["code"], t["evidence"])
            fp = evidence_fingerprint(t)
            seen_issue.add(key)
            seen_task_keys.add(t["task_key"])
            issue = issues.get(key)
            if issue is None:
                issue = self._create(t, key, fp, now, legacy_active, rows)
                issues[key] = issue
            else:
                self._redetect(issue, t, fp, now, rows)

        for key, issue in issues.items():
            if key not in seen_issue:
                self._not_routed(issue, key in current_keys, rows)

        # tasks created before issue memory that no run has adopted: original behaviour
        for key, row in legacy_active.items():
            if row.issue_id is None and key not in seen_task_keys and row.status == "OPEN":
                row.status = "RESOLVED_BY_CORRECTION" if self.trigger == "CORRECTION" else "SUPERSEDED"
                row.resolved_at, row.resolved_by = now, self.actor
                if self.audit:
                    self.audit(db, "REVIEW_TASK_RESOLVED", f"{row.code} no longer raised after {self.trigger.lower()} run",
                               self.actor, case.id, "review_task", row.id, status=row.status)

        if self.retained and self.audit:
            self.audit(db, "REVIEW_TASK_DEDUPLICATED",
                       f"{len(self.retained)} issue(s) detected again; existing review task(s) retained, "
                       f"no duplicate task created", "system", case.id, "pipeline_run", run.id,
                       items=self.retained[:50])
        return dict(self.report, retained=len(self.retained))

    # ------------------------------------------------------------------ transitions
    def _create(self, t, key, fp, now, legacy_active, rows):
        db = self.db
        status = STATE_TO_ISSUE[t["state"]]
        issue = TrackedIssue(
            case_id=self.case.id, encounter_id=self.case.encounter_id, issue_key=key, code=t["code"],
            issue_type=t.get("stage"), title=t["title"], identity=identity_of(t["code"], t["evidence"]),
            evidence_fingerprint=fp, evidence=t["evidence"], severity=t["severity"], status=status,
            review_outcome=None if t["state"] == "OPEN" else t["state"], is_present=True,
            owner_stage=t["owner_stage"], input_domain=t["input_domain"], first_detected_at=now,
            last_detected_at=now, first_run_id=self.run.id, last_run_id=self.run.id, detection_count=1)
        issue, created = self._insert(issue, lambda: db.query(TrackedIssue).filter_by(
            case_id=self.case.id, issue_key=key).first())
        if not created:                      # a concurrent run created it first: treat as a re-detection
            self._redetect(issue, t, fp, now, rows)
            return issue
        self.report["issues_created"] += 1
        row = legacy_active.get(t["task_key"])
        if row is not None and row.issue_id is None:
            row.issue_id = issue.id
            row.reason, row.evidence = t["reason"], t["evidence"]
            if t["state"] != "OPEN" and row.status == "OPEN":
                row.status = t["state"]
            issue.status = STATE_TO_ISSUE.get(row.status, status)
            issue.first_detected_at = row.created_at or now
            _event(db, issue, "ADOPTED_EXISTING_TASK", self.run.id, row.id, None, issue.status,
                   note="review task created before issue memory existed; linked, not duplicated")
            self.report["legacy_tasks_adopted"] += 1
        else:
            row = self._new_task(issue, t)
            rows[row.id] = row
            _event(db, issue, "DETECTED", self.run.id, row.id, None, issue.status, severity=t["severity"],
                   department=department(t["owner_stage"]), evidence_fingerprint=fp[:16])
        issue.task_id = row.id
        return issue

    def _redetect(self, issue, t, fp, now, rows):
        db = self.db
        prev_fp, prev_ev, prev_status = issue.evidence_fingerprint, issue.evidence, issue.status
        issue.detection_count = (issue.detection_count or 0) + 1
        issue.last_detected_at, issue.last_run_id = now, self.run.id
        issue.severity, issue.title = t["severity"], t["title"]
        issue.owner_stage, issue.input_domain = t["owner_stage"], t["input_domain"]
        was_present = issue.is_present
        issue.is_present = True
        fp_changed = prev_fp is not None and prev_fp != fp
        task = rows.get(issue.task_id) if issue.task_id else None
        if task is None and issue.task_id:
            task = db.get(ReviewTask, issue.task_id)
        self.report["redetections"] += 1

        if prev_status in ACTIVE_ISSUE:
            if task is not None and task.status in ACTIVE_TASK:
                if t["state"] == "OPEN" and task.status != "OPEN":
                    # a recorded decision (e.g. CONFIRM_BLOCKED) no longer applies: evidence changed
                    self._reopen(issue, t, fp, prev_ev, prev_status, task,
                                 reason="the reviewer's decision no longer applies because the material evidence changed")
                else:
                    if task.task_key != t["task_key"]:
                        _event(db, issue, "TASK_REKEYED", self.run.id, task.id, prev_status, prev_status,
                               old_task_key=task.task_key, new_task_key=t["task_key"],
                               reason="the set of cited rules changed; same underlying issue, same task kept")
                        task.task_key = t["task_key"]
                    task.reason, task.evidence = t["reason"], t["evidence"]
                    task.severity = t["severity"]
                    if t["state"] != "OPEN" and task.status == "OPEN":
                        task.status = t["state"]
                        issue.status = STATE_TO_ISSUE[t["state"]]
                        issue.review_outcome = t["state"]
                        _event(db, issue, "DECISION_APPLIED", self.run.id, task.id, prev_status, issue.status,
                               decision=t["state"])
                    else:
                        issue.status = STATE_TO_ISSUE.get(task.status, prev_status)
                        issue.last_dedup_run_id = self.run.id
                        self.retained.append(dict(issue_id=issue.id, task_id=task.id, code=issue.code,
                                                  department=department(issue.owner_stage),
                                                  task_status=task.status, detection_count=issue.detection_count))
                        self.report["duplicate_tasks_prevented"] += 1
                    if fp_changed:
                        _event(db, issue, "EVIDENCE_UPDATED", self.run.id, task.id, prev_status, issue.status,
                               changes=fingerprint_diff(prev_ev, t["evidence"]),
                               note="pending task kept; its evidence was refreshed in place")
            else:
                row = self._new_task(issue, t)
                rows[row.id] = row
                issue.task_id = row.id
                issue.status = STATE_TO_ISSUE[t["state"]]
                _event(db, issue, "ROUTED", self.run.id, row.id, prev_status, issue.status,
                       reason="present and routed to review again" if was_present else "detected again")
        elif prev_status == "DISMISSED":
            if t["state"] == "ACCEPTED_WITH_JUSTIFICATION":
                self.report["dismissals_kept"] += 1
                if not was_present:
                    _event(db, issue, "REDETECTED_DISMISSAL_KEPT", self.run.id, issue.task_id, prev_status, prev_status,
                           note="same evidence as when it was dismissed; dismissal and reason preserved")
            else:
                self._reopen(issue, t, fp, prev_ev, prev_status, task,
                             reason=("materially new evidence after the dismissal" if fp_changed or t.get("prior_decision")
                                     else "the dismissal no longer applies to the routed task"))
        else:  # RESOLVED / NO_LONGER_DETECTED -> genuinely detected again
            self._reopen(issue, t, fp, prev_ev, prev_status, task,
                         reason=("detected again after it was resolved by a correction" if prev_status == "RESOLVED"
                                 else "detected again after it was no longer detected"))
        issue.evidence_fingerprint, issue.evidence = fp, t["evidence"]

    def _reopen(self, issue, t, fp, prev_ev, prev_status, old_task, reason):
        db = self.db
        previous = dict(status=prev_status, review_outcome=issue.review_outcome, resolution_action=issue.resolution_action,
                        resolved_at=issue.resolved_at.isoformat() if issue.resolved_at else None,
                        resolved_by=issue.resolved_by, task_id=issue.task_id,
                        task_status=old_task.status if old_task is not None else None)
        if old_task is not None and old_task.status == "CONFIRMED_BLOCKED":
            old_task.status = "SUPERSEDED"      # the confirmation was for different evidence; history kept in events
            old_task.resolution_note = (old_task.resolution_note or "") + " [superseded: evidence changed]"
        issue.reopen_count = (issue.reopen_count or 0) + 1
        issue.resolution_action, issue.resolved_at, issue.resolved_by, issue.review_outcome = None, None, None, None
        row = self._new_task(issue, t)
        issue.task_id = row.id
        issue.status = STATE_TO_ISSUE[t["state"]]
        if issue.status != "PENDING_REVIEW":
            issue.review_outcome = t["state"]
        _event(db, issue, "REOPENED", self.run.id, row.id, prev_status, issue.status, reason=reason,
               occurrence=issue.reopen_count + 1, previous=previous,
               evidence_changes=fingerprint_diff(prev_ev, t["evidence"]), prior_decision=t.get("prior_decision"))
        self.report["issues_reopened"] += 1
        if self.audit:
            self.audit(db, "ISSUE_REOPENED", f"{issue.code} reopened ({prev_status.lower()} -> {issue.status.lower()}): {reason}",
                       "system", self.case.id, "issue", issue.id, task_id=row.id, previous=previous,
                       reopen_count=issue.reopen_count)

    def _not_routed(self, issue, still_detected, rows):
        db, now = self.db, _now()
        task = rows.get(issue.task_id) if issue.task_id else None
        prev = issue.status
        if prev in ACTIVE_ISSUE:
            if still_detected:
                changed = prev != "OPEN"
                if task is not None and task.status == "OPEN":
                    task.status, task.resolved_at, task.resolved_by = "SUPERSEDED", now, self.actor
                    task.resolution_note = "still present but now explained as a consequence of another root; not routed separately"
                    changed = True
                issue.status = "OPEN"
                issue.is_present = True
                if changed:
                    _event(db, issue, "NOT_ROUTED_STILL_PRESENT", self.run.id, issue.task_id, prev, "OPEN",
                           note="finding still present; explained as a consequence, so no separate task")
                return
            issue.is_present = False
            corr = self.correction
            if self.trigger == "CORRECTION" and corr is None:
                corr = (db.query(Correction).filter_by(case_id=self.case.id, run_id=None)
                        .order_by(Correction.id.desc()).first())
            if self.trigger == "CORRECTION" and corr is not None:
                issue.status = "RESOLVED"
                issue.resolution_action = dict(kind="CORRECTION", correction_id=corr.id, op=corr.op, domain=corr.domain,
                                               reason=corr.reason, run_id=self.run.id,
                                               linked_to_this_issue=bool(corr.review_task_id and corr.review_task_id == issue.task_id))
                issue.resolved_at, issue.resolved_by = now, corr.actor
                new_task_status = "RESOLVED_BY_CORRECTION"
                summary = f"{issue.code} no longer detected after correction #{corr.id} ({corr.op})"
                self.report["issues_resolved"] += 1
            else:
                issue.status = "NO_LONGER_DETECTED"
                issue.resolution_action = dict(kind="NOT_DETECTED", trigger=self.trigger, run_id=self.run.id,
                                               note="not detected in this run, and no data correction was recorded, "
                                                    "so it is not claimed as resolved")
                issue.resolved_at, issue.resolved_by = None, None
                new_task_status = "SUPERSEDED"
                summary = f"{issue.code} no longer detected after a {self.trigger.lower()} run (not claimed resolved)"
                self.report["issues_no_longer_detected"] += 1
            if task is not None and task.status == "OPEN":
                task.status, task.resolved_at, task.resolved_by = new_task_status, now, self.actor
                if self.audit:
                    self.audit(db, "REVIEW_TASK_RESOLVED", f"{task.code} no longer raised after {self.trigger.lower()} run",
                               self.actor, self.case.id, "review_task", task.id, status=task.status, issue_id=issue.id)
            _event(db, issue, "RESOLVED" if issue.status == "RESOLVED" else "NO_LONGER_DETECTED", self.run.id,
                   issue.task_id, prev, issue.status, resolution_action=issue.resolution_action)
            if self.audit:
                self.audit(db, "ISSUE_RESOLVED" if issue.status == "RESOLVED" else "ISSUE_NOT_DETECTED", summary,
                           self.actor, self.case.id, "issue", issue.id, resolution_action=issue.resolution_action)
        elif prev == "DISMISSED" and issue.is_present and not still_detected:
            issue.is_present = False
            _event(db, issue, "NOT_DETECTED", self.run.id, issue.task_id, prev, prev,
                   note="no longer detected; the dismissal and its reason are preserved")
        elif prev in ("RESOLVED", "NO_LONGER_DETECTED") and still_detected and not issue.is_present:
            # present again but only as a consequence of another root: visible, not re-routed
            issue.is_present = True
            _event(db, issue, "PRESENT_AS_CONSEQUENCE", self.run.id, None, prev, prev,
                   note="finding present again but explained as a consequence of another root")


# ---------------------------------------------------------------------------
# reviewer actions recorded against an issue (called by the API / corrections)
# ---------------------------------------------------------------------------

def issue_for_task(db, task: ReviewTask, ensure: bool = False) -> Optional[TrackedIssue]:
    """The issue a task belongs to. ensure=True links a task recorded before issue memory existed
    (reconstructing its case's issues exactly as the backfill does) so a reviewer action is never lost."""
    if task is None:
        return None
    if task.issue_id:
        return db.get(TrackedIssue, task.issue_id)
    if ensure:
        case = db.get(ClaimCase, task.case_id)
        if case is not None:
            backfill_case(db, case, actor="auto-link")
            db.flush()
            if task.issue_id:
                return db.get(TrackedIssue, task.issue_id)
    return (db.query(TrackedIssue).filter_by(case_id=task.case_id, issue_key=issue_key(task.code, task.evidence or {}))
            .first())


def task_fingerprint(task: ReviewTask) -> str:
    return evidence_fingerprint(dict(code=task.code, severity=task.severity, evidence=task.evidence or {}))


def record_decision_on_issue(db, task: ReviewTask, decision: str, note: str, actor: str):
    issue = issue_for_task(db, task, ensure=True)
    if issue is None:
        return None
    issue.review_attempts = (issue.review_attempts or 0) + 1
    issue.reviewer = actor
    issue.reviewer_notes = _append(issue.reviewer_notes, dict(ts=_now().isoformat(), actor=actor, note=note, kind=decision))
    issue.previous_actions = _append(issue.previous_actions, dict(ts=_now().isoformat(), actor=actor, kind="DECISION",
                                                                  decision=decision, task_id=task.id))
    if decision == "ACCEPT_WITH_JUSTIFICATION":
        issue.resolution_action = dict(kind="DECISION", decision=decision, task_id=task.id, actor=actor, note=note)
        issue.resolved_at, issue.resolved_by = _now(), actor
    _event(db, issue, "DECISION_RECORDED", None, task.id, issue.status, None, actor=actor, decision=decision, note=note)
    return issue


def record_correction_on_issue(db, task: Optional[ReviewTask], corr: Correction):
    issue = issue_for_task(db, task, ensure=True) if task is not None else None
    if issue is None:
        return None
    issue.review_attempts = (issue.review_attempts or 0) + 1
    issue.reviewer = corr.actor
    issue.previous_actions = _append(issue.previous_actions, dict(
        ts=_now().isoformat(), actor=corr.actor, kind="CORRECTION", correction_id=corr.id, op=corr.op,
        domain=corr.domain, reason=corr.reason, task_id=task.id))
    _event(db, issue, "CORRECTION_ATTEMPTED", None, task.id, issue.status, None, actor=corr.actor,
           correction_id=corr.id, op=corr.op, domain=corr.domain)
    return issue


# ---------------------------------------------------------------------------
# serialisation + lifecycle labels shown in the UI
# ---------------------------------------------------------------------------

LABELS = {
    "NEW": "New issue", "UNDER_REVIEW": "Already under review", "PREVIOUSLY_UNRESOLVED": "Previously unresolved",
    "RESOLVED": "Resolved", "REOPENED": "Reopened", "DISMISSED": "Dismissed", "NO_LONGER_DETECTED": "No longer detected",
}


def lifecycle(i: TrackedIssue, case_last_run_id=None) -> str:
    if i.status in ACTIVE_ISSUE:
        if (i.reopen_count or 0) > 0:
            return "REOPENED"
        if i.status == "PENDING_REVIEW":
            return "NEW" if (i.detection_count or 0) <= 1 else "UNDER_REVIEW"
        return "PREVIOUSLY_UNRESOLVED"
    return i.status if i.status in LABELS else i.status


def dedup_note(i: TrackedIssue, task: Optional[ReviewTask], case_last_run_id=None) -> Optional[str]:
    if i.status not in ACTIVE_ISSUE or task is None or task.id != i.task_id or task.status not in ACTIVE_TASK:
        return None
    if i.last_dedup_run_id is None or (case_last_run_id is not None and i.last_dedup_run_id != case_last_run_id):
        return None
    dept = department(i.owner_stage)
    if task.status == "OPEN":
        return (f"Previously routed to {dept} — still pending (task #{task.id}, detected {i.detection_count} times). "
                f"Existing task retained; no duplicate task created.")
    return (f"Reviewed by {dept} ({task.status.replace('_', ' ').lower()}) and still present. "
            f"Existing task #{task.id} retained; no duplicate task created.")


def _iso(v):
    return v.isoformat() if v is not None else None


def issue_row(i: TrackedIssue, task: Optional[ReviewTask] = None, case_last_run_id=None, events=None) -> dict:
    d = dict(id=i.id, case_id=i.case_id, encounter_id=i.encounter_id, issue_key=i.issue_key, code=i.code,
             issue_type=i.issue_type, title=i.title, identity=i.identity, severity=i.severity, status=i.status,
             review_outcome=i.review_outcome, is_present=i.is_present, owner_stage=i.owner_stage,
             department=department(i.owner_stage), input_domain=i.input_domain, reviewer=i.reviewer,
             reviewer_notes=i.reviewer_notes or [], previous_actions=i.previous_actions or [], task_id=i.task_id,
             task_status=task.status if task is not None else None, assignee=task.assignee if task is not None else None,
             first_detected_at=_iso(i.first_detected_at), last_detected_at=_iso(i.last_detected_at),
             first_run_id=i.first_run_id, last_run_id=i.last_run_id, detection_count=i.detection_count,
             review_attempts=i.review_attempts, reopen_count=i.reopen_count, resolution_action=i.resolution_action,
             resolved_at=_iso(i.resolved_at), resolved_by=i.resolved_by, evidence_fingerprint=(i.evidence_fingerprint or "")[:16],
             backfilled=i.backfilled, active_blocker=i.status in ACTIVE_ISSUE and i.is_present and i.severity == "BLOCKING",
             lifecycle=lifecycle(i, case_last_run_id))
    d["lifecycle_label"] = LABELS.get(d["lifecycle"], d["lifecycle"])
    d["dedup_note"] = dedup_note(i, task, case_last_run_id)
    if events is not None:
        d["history"] = [dict(id=e.id, ts=_iso(e.ts), event=e.event, from_status=e.from_status, to_status=e.to_status,
                             run_id=e.run_id, task_id=e.task_id, actor=e.actor, details=e.details) for e in events]
    return d


def case_issues(db, case: ClaimCase, with_history=True) -> list:
    issues = db.query(TrackedIssue).filter_by(case_id=case.id).order_by(TrackedIssue.id).all()
    tasks = {t.id: t for t in db.query(ReviewTask).filter_by(case_id=case.id)}
    evs = {}
    if with_history and issues:
        for e in db.query(IssueEvent).filter_by(case_id=case.id).order_by(IssueEvent.id):
            evs.setdefault(e.issue_id, []).append(e)
    return [issue_row(i, tasks.get(i.task_id), case.last_run_id, evs.get(i.id, []) if with_history else None)
            for i in issues]


def memory_summary(db) -> dict:
    by_status = dict(db.query(TrackedIssue.status, func.count(TrackedIssue.id)).group_by(TrackedIssue.status).all())
    agg = db.query(func.count(TrackedIssue.id), func.sum(TrackedIssue.detection_count), func.sum(TrackedIssue.reopen_count),
                   func.sum(TrackedIssue.review_attempts)).one()
    n_ev = dict(db.query(IssueEvent.event, func.count(IssueEvent.id)).group_by(IssueEvent.event).all())
    unlinked = db.query(func.count()).select_from(ReviewTask).filter(ReviewTask.issue_id.is_(None)).scalar()
    open_dups = sum(n - 1 for _, _, n in db.query(ReviewTask.case_id, ReviewTask.task_key, func.count(ReviewTask.id))
                    .filter(ReviewTask.status == "OPEN").group_by(ReviewTask.case_id, ReviewTask.task_key).all() if n > 1)
    from app.clearclaim.models import AuditEvent
    prevented = 0
    for (det,) in db.query(AuditEvent.details).filter(AuditEvent.event_type == "REVIEW_TASK_DEDUPLICATED"):
        prevented += len((det or {}).get("items") or [])
    total = agg[0] or 0
    return dict(
        tracked_issues=total, by_status=by_status, total_detections=int(agg[1] or 0),
        repeat_detections=int((agg[1] or 0) - total) if total else 0, reopened=int(agg[2] or 0),
        review_attempts=int(agg[3] or 0), duplicate_tasks_prevented=prevented, open_task_duplicates=open_dups,
        tasks_without_issue=unlinked, events=n_ev,
        pending_by_department=[dict(owner_stage=o, department=department(o), pending=n) for o, n in
                               db.query(TrackedIssue.owner_stage, func.count(TrackedIssue.id))
                               .filter(TrackedIssue.status == "PENDING_REVIEW").group_by(TrackedIssue.owner_stage).all()],
        notes=["duplicate_tasks_prevented counts re-detections where an existing review task was retained "
               "(from REVIEW_TASK_DEDUPLICATED audit events).",
               "tasks_without_issue are review tasks created before issue memory and not yet linked "
               "(run scripts/backfill_issue_memory.py or re-run the case)."],
    )


# ---------------------------------------------------------------------------
# backfill (pre-existing tasks -> issues), reconstructed only from recorded rows
# ---------------------------------------------------------------------------

def backfill_case(db, case: ClaimCase, actor="backfill") -> Counter:
    """Create issues for review tasks recorded before issue memory existed. Detection counts are
    reconstructed from recorded pipeline runs: an OPEN task was emitted by every successful run since
    the run that created it (otherwise the old sync would have closed it); a closed task by every
    successful run before it was closed. Nothing else is inferred."""
    rep = Counter()
    tasks = db.query(ReviewTask).filter_by(case_id=case.id, issue_id=None).order_by(ReviewTask.id).all()
    if not tasks:
        return rep
    runs = db.query(PipelineRun).filter_by(case_id=case.id, status="SUCCEEDED").order_by(PipelineRun.id).all()
    decisions = {d.task_id: d for d in db.query(ReviewDecision).filter_by(case_id=case.id)}
    corrections = db.query(Correction).filter_by(case_id=case.id).order_by(Correction.id).all()
    for t in tasks:
        key = issue_key(t.code, t.evidence or {})
        issue = db.query(TrackedIssue).filter_by(case_id=case.id, issue_key=key).first()
        created_run = t.created_run_id or (runs[0].id if runs else None)
        closing_run = None
        if t.status in ("RESOLVED_BY_CORRECTION", "SUPERSEDED") and t.resolved_at:
            # the run that closed the task is the last one that had started when resolved_at was stamped
            closing_run = next((r for r in reversed(runs) if r.started_at and r.started_at <= t.resolved_at), None)
        det_runs = [r for r in runs if created_run is None or r.id >= created_run]
        if closing_run is not None:
            det_runs = [r for r in det_runs if r.id < closing_run.id]
        status = {"OPEN": "PENDING_REVIEW", "ACCEPTED_WITH_JUSTIFICATION": "DISMISSED", "CONFIRMED_BLOCKED": "OPEN",
                  "RESOLVED_BY_CORRECTION": "RESOLVED", "SUPERSEDED": "NO_LONGER_DETECTED"}.get(t.status, "OPEN")
        resolution = None
        if status == "RESOLVED":
            corr = next((c for c in corrections if c.review_task_id == t.id), None) or next(
                (c for c in corrections if closing_run is not None and c.run_id == closing_run.id), None)
            resolution = dict(kind="CORRECTION", correction_id=corr and corr.id, op=corr and corr.op,
                              domain=corr and corr.domain, run_id=closing_run and closing_run.id, backfilled=True)
        elif status == "NO_LONGER_DETECTED":
            resolution = dict(kind="NOT_DETECTED", run_id=closing_run and closing_run.id, backfilled=True,
                              note="closed by the pre-issue-memory sync without a correction; not claimed resolved")
        dec = decisions.get(t.id)
        if issue is None:
            issue = TrackedIssue(
                case_id=case.id, encounter_id=case.encounter_id, issue_key=key, code=t.code, issue_type=None,
                title=t.title, identity=identity_of(t.code, t.evidence or {}),
                evidence_fingerprint=task_fingerprint(t), evidence=t.evidence, severity=t.severity, status=status,
                review_outcome=t.status if t.status in ("ACCEPTED_WITH_JUSTIFICATION", "CONFIRMED_BLOCKED") else None,
                is_present=status in ("OPEN", "PENDING_REVIEW", "DISMISSED"), owner_stage=t.owner_stage,
                input_domain=t.input_domain, task_id=t.id, first_detected_at=t.created_at,
                last_detected_at=(det_runs[-1].started_at if det_runs else t.created_at),
                first_run_id=created_run, last_run_id=det_runs[-1].id if det_runs else created_run,
                detection_count=max(1, len(det_runs)), review_attempts=1 if dec else 0, resolution_action=resolution,
                resolved_at=t.resolved_at if status in ("RESOLVED", "DISMISSED") else None,
                resolved_by=t.resolved_by if status in ("RESOLVED", "DISMISSED") else None,
                reviewer=dec.actor if dec else None,
                reviewer_notes=[dict(ts=_iso(dec.created_at), actor=dec.actor, note=dec.note, kind=dec.decision)] if dec else None,
                previous_actions=[dict(ts=_iso(dec.created_at), actor=dec.actor, kind="DECISION", decision=dec.decision,
                                       task_id=t.id)] if dec else None,
                backfilled=True)
            db.add(issue)
            db.flush()
            _event(db, issue, "BACKFILLED", created_run, t.id, None, status, actor=actor,
                   note="issue reconstructed from a review task recorded before issue memory existed",
                   detection_count_basis="successful pipeline runs while the task was active")
            rep["issues_created"] += 1
        else:
            prev = issue.status
            issue.reopen_count = (issue.reopen_count or 0) + 1
            issue.status, issue.task_id, issue.severity, issue.evidence = status, t.id, t.severity, t.evidence
            issue.evidence_fingerprint = task_fingerprint(t)
            issue.detection_count = (issue.detection_count or 0) + max(1, len(det_runs))
            issue.resolution_action = resolution
            issue.is_present = status in ("OPEN", "PENDING_REVIEW", "DISMISSED")
            if det_runs:
                issue.last_detected_at, issue.last_run_id = det_runs[-1].started_at, det_runs[-1].id
            _event(db, issue, "REOPENED", created_run, t.id, prev, status, actor=actor, backfilled=True,
                   reason="a later review task with the same issue identity was recorded before issue memory existed")
            rep["reopen_events"] += 1
        t.issue_id = issue.id
        rep["tasks_linked"] += 1
    return rep


def backfill_all(db, actor="backfill") -> dict:
    total = Counter()
    for c in db.query(ClaimCase).order_by(ClaimCase.id):
        total.update(backfill_case(db, c, actor))
    db.commit()
    return dict(total)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["clearclaim"])


@router.get("/cases/{case_id}/issues")
def api_case_issues(case_id: int, db: Session = Depends(get_db)):
    c = db.get(ClaimCase, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    items = case_issues(db, c)
    return dict(case_id=c.id, items=items, counts=dict(Counter(i["status"] for i in items)),
                active_blockers=sum(1 for i in items if i["active_blocker"]),
                statuses=dict(OPEN="present, no review pending (reviewed and confirmed, or explained as a consequence)",
                              PENDING_REVIEW="present, a review task is open",
                              RESOLVED="no longer detected after a recorded data correction",
                              DISMISSED="accepted by a reviewer with a justification",
                              NO_LONGER_DETECTED="no longer detected without a correction; not claimed as resolved"))


@router.get("/issues")
def api_issues(db: Session = Depends(get_db), status: Optional[str] = None, code: Optional[str] = None,
               owner_stage: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    q = db.query(TrackedIssue, ClaimCase).join(ClaimCase, ClaimCase.id == TrackedIssue.case_id)
    if status:
        q = q.filter(TrackedIssue.status.in_(status.split(",")))
    if code:
        q = q.filter(TrackedIssue.code == code)
    if owner_stage:
        q = q.filter(TrackedIssue.owner_stage == owner_stage)
    total = q.count()
    rows = q.order_by(TrackedIssue.last_detected_at.desc(), TrackedIssue.id).offset((page - 1) * page_size).limit(page_size).all()
    tids = [i.task_id for i, _ in rows if i.task_id]
    tasks = {t.id: t for t in db.query(ReviewTask).filter(ReviewTask.id.in_(tids))} if tids else {}
    return dict(total=total, items=[dict(issue_row(i, tasks.get(i.task_id), c.last_run_id), case_ref=c.case_ref)
                                    for i, c in rows])


@router.get("/issues/summary")
def api_issue_summary(db: Session = Depends(get_db)):
    return memory_summary(db)


def add_task_note(db, task: ReviewTask, note: str, actor: str, assignee: Optional[str] = None):
    """Reviewer note / assignment on a pending task. Stored on the task and its issue; preserved on rerun."""
    issue = issue_for_task(db, task, ensure=True)
    if assignee:
        task.assignee = assignee
    if issue is not None:
        issue.reviewer_notes = _append(issue.reviewer_notes, dict(ts=_now().isoformat(), actor=actor, note=note,
                                                                  kind="NOTE", assignee=assignee))
        if assignee:
            issue.reviewer = assignee
        _event(db, issue, "NOTE_ADDED", None, task.id, issue.status, issue.status, actor=actor, note=note,
               assignee=assignee)
    return issue
