"""
ClearClaim REST API (/api/v1). Legacy routes in app/main.py are unchanged.

Never exposes ground-truth labels: no endpoint reads data/.../ground_truth/
except the experiments endpoints, which return aggregate metrics computed
offline on an isolated database.
"""
import datetime as dt
import hmac
import statistics
import threading
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.clearclaim import dag
from app.clearclaim.config import settings
from app.clearclaim.context import DEFAULT_SETTINGS, get_context, invalidate_context
from app.clearclaim.corrections import OP_DOMAINS, ConflictError, CorrectionError, apply_correction
from app.clearclaim.findings import TITLES
from app.clearclaim.stages.analysis import NEXT_ACTIONS
from app.clearclaim.models import (
    AppSetting, AuditEvent, ClaimCase, ClaimPacket, Correction, DatasetImport, Encounter, Finding,
    MemberPolicy, PipelineRun, ReviewDecision, ReviewTask, StageExecution,
)
from app.clearclaim.orchestrator import Orchestrator, audit, verify_audit_chain
from app.database.db import get_db

router = APIRouter(prefix="/api/v1", tags=["clearclaim"])
# Validation statistics that are derived from (or would reveal) evaluation labels; never served.
GT_DERIVED_STATS = {"ground_truth_issues", "icd10_codes_not_in_reference"}
_process_lock = threading.Lock()


# ---------------------------------------------------------------------------
# auth (practical demo protection for mutating endpoints)
# ---------------------------------------------------------------------------

def require_writer(authorization: Optional[str] = Header(default=None)) -> str:
    if not settings.write_token:
        if settings.app_env == "production":
            # Safe default: a production deployment without a token is read-only.
            raise HTTPException(503, "Writes are disabled: CLEARCLAIM_WRITE_TOKEN is not configured on this server.")
        return "demo.user"
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), settings.write_token.encode()):
        raise HTTPException(401, "This action requires the ClearClaim write token (Settings > Access).")
    return "authorised.user"


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------

def _iso(v):
    return v.isoformat() if v is not None else None


def case_row(c: ClaimCase) -> dict:
    e = c.encounter
    return dict(id=c.id, case_ref=c.case_ref, hadm_id=e.hadm_id, subject_id=e.subject_id, data_source=e.data_source,
                status=c.status, priority=c.priority, root_blocker_code=c.root_blocker_code,
                root_blocker_title=TITLES.get(c.root_blocker_code) if c.root_blocker_code else None,
                root_blocker_stage=c.root_blocker_stage,
                next_action=NEXT_ACTIONS.get(c.root_blocker_code) if c.root_blocker_code else None,
                amount_billed=c.amount_billed, amount_at_risk=c.amount_at_risk,
                estimated_eligible=c.estimated_eligible, open_tasks=c.open_tasks, admittime=_iso(e.admittime),
                dischtime=_iso(e.dischtime), admission_type=e.admission_type, drg_code=e.drg_code,
                drg_description=e.drg_description, updated_at=_iso(c.updated_at), version=c.version)


def finding_row(f: Finding) -> dict:
    return dict(id=f.id, stage=f.stage, code=f.code, severity=f.severity, title=f.title, message=f.message,
                rule_id=f.rule_id, source_document=f.source_document, source_page=f.source_page,
                expected=f.expected, actual=f.actual, amount=f.amount, evidence=f.evidence, role=f.role,
                caused_by=f.caused_by)


def task_row(t: ReviewTask, case: Optional[ClaimCase] = None) -> dict:
    d = dict(id=t.id, case_id=t.case_id, code=t.code, severity=t.severity, owner_stage=t.owner_stage,
             input_domain=t.input_domain, title=t.title, reason=t.reason, evidence=t.evidence,
             recommended_action=t.recommended_action, required_reruns=t.required_reruns,
             allowed_decisions=t.allowed_decisions, status=t.status, assignee=t.assignee,
             created_at=_iso(t.created_at), resolved_at=_iso(t.resolved_at), resolved_by=t.resolved_by,
             resolution_note=t.resolution_note, task_key=t.task_key)
    if case is not None:
        d["case_ref"] = case.case_ref
        d["case_status"] = case.status
    return d


def run_row(r: PipelineRun, stages=None) -> dict:
    d = dict(id=r.id, trigger=r.trigger, execution_mode=r.execution_mode, rerun_policy=r.rerun_policy,
             changed_domains=r.changed_domains, invalidated_stages=r.invalidated_stages, status=r.status,
             outcome_status=r.outcome_status, stages_executed=r.stages_executed, stages_reused=r.stages_reused,
             started_at=_iso(r.started_at), finished_at=_iso(r.finished_at), wall_ms=r.wall_ms, error=r.error)
    if stages is not None:
        d["stages"] = [dict(id=s.id, stage=s.stage, status=s.status, stage_status=s.stage_status, attempts=s.attempts,
                            duration_ms=s.duration_ms, input_hash=(s.input_hash or "")[:12],
                            output_hash=(s.output_hash or "")[:12], error=s.error, reused_from_id=s.reused_from_id)
                       for s in stages]
    return d


def event_row(e: AuditEvent) -> dict:
    return dict(id=e.id, ts=_iso(e.ts), actor=e.actor, case_id=e.case_id, event_type=e.event_type,
                entity_type=e.entity_type, entity_id=e.entity_id, summary=e.summary, details=e.details)


def _case_or_404(db, case_id) -> ClaimCase:
    c = db.get(ClaimCase, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return c


# ---------------------------------------------------------------------------
# health / meta
# ---------------------------------------------------------------------------

@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        n = db.query(func.count(ClaimCase.id)).scalar()
        ok = True
    except Exception as e:  # pragma: no cover
        return dict(status="degraded", database="error", detail=type(e).__name__)
    return dict(status="ok" if ok else "degraded", database="ok", cases=n, version=settings.app_version)


@router.get("/meta")
def meta(db: Session = Depends(get_db)):
    imp = db.query(DatasetImport).filter_by(status="COMPLETED").order_by(DatasetImport.id.desc()).first()
    ctx = get_context(db)
    return dict(
        product="ClearClaim", version=settings.app_version, environment=settings.app_env, data_mode=settings.data_mode,
        active_dataset=imp and dict(id=imp.id, mode=imp.mode, source_label=imp.source_label,
                                    manifest_mode=imp.manifest_mode, generator_version=imp.generator_version,
                                    imported_at=_iso(imp.imported_at), n_encounters=imp.n_encounters),
        write_protected=bool(settings.write_token), execution_mode=ctx.settings["execution_mode"],
        rerun_policy=ctx.settings["rerun_policy"], routing_policy=ctx.settings["routing_policy"],
        database_engine=settings.database_url.split(":")[0],
        disclaimer=("Demo data is SYNTHETIC. Statuses are internal claim-preparation states, not insurer decisions. "
                    "Financial figures are rule-based estimates."),
    )


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------

@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    by_status = dict(db.query(ClaimCase.status, func.count(ClaimCase.id)).group_by(ClaimCase.status).all())
    attention = (db.query(ClaimCase).filter(ClaimCase.status.in_(["BLOCKED", "NEEDS_REVIEW", "ERROR"]))
                 .order_by(ClaimCase.priority.desc(), ClaimCase.id).limit(8).all())
    queues = (db.query(ReviewTask.owner_stage, ReviewTask.severity, func.count(ReviewTask.id))
              .filter(ReviewTask.status == "OPEN").group_by(ReviewTask.owner_stage, ReviewTask.severity).all())
    roots = (db.query(ClaimCase.root_blocker_code, func.count(ClaimCase.id), func.sum(ClaimCase.amount_at_risk))
             .filter(ClaimCase.root_blocker_code.isnot(None)).group_by(ClaimCase.root_blocker_code)
             .order_by(func.count(ClaimCase.id).desc()).limit(10).all())
    recent_runs = db.query(PipelineRun).order_by(PipelineRun.id.desc()).limit(200).all()
    lat = sorted(r.wall_ms for r in recent_runs if r.wall_ms is not None)
    stage_rows = (db.query(StageExecution.stage, func.avg(StageExecution.duration_ms), func.count(StageExecution.id))
                  .filter(StageExecution.status == "SUCCEEDED").group_by(StageExecution.stage).all())
    totals = db.query(func.sum(ClaimCase.amount_billed), func.sum(ClaimCase.amount_at_risk),
                      func.sum(ClaimCase.estimated_eligible)).one()
    events = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(12).all()
    return dict(
        status_counts=by_status, total_cases=sum(by_status.values()),
        attention=[case_row(c) for c in attention],
        work_queues=[dict(owner_stage=o, severity=s, open=n) for o, s, n in queues],
        top_root_blockers=[dict(code=c, title=TITLES.get(c, c), cases=n, amount_at_risk=round(a or 0, 2))
                           for c, n, a in roots],
        processing=dict(
            recent_runs=len(recent_runs), failed=sum(1 for r in recent_runs if r.status == "FAILED"),
            median_ms=round(statistics.median(lat), 2) if lat else None,
            p95_ms=round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2) if lat else None,
            mean_stages_executed=round(statistics.mean(r.stages_executed or 0 for r in recent_runs), 2) if recent_runs else None,
            stage_avg_ms={s: round(a or 0, 3) for s, a, _ in stage_rows},
        ),
        amounts=dict(billed=round(totals[0] or 0, 2), at_risk=round(totals[1] or 0, 2),
                     estimated_eligible=round(totals[2] or 0, 2)),
        recent_events=[event_row(e) for e in events],
    )


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------

SORTABLE = {"priority": ClaimCase.priority, "amount_at_risk": ClaimCase.amount_at_risk,
            "amount_billed": ClaimCase.amount_billed, "updated_at": ClaimCase.updated_at, "case_ref": ClaimCase.case_ref,
            "open_tasks": ClaimCase.open_tasks}


@router.get("/cases")
def list_cases(db: Session = Depends(get_db), status: Optional[str] = None, q: Optional[str] = Query(None, max_length=64),
               root: Optional[str] = None, stage: Optional[str] = None,
               sort: Literal["priority", "amount_at_risk", "amount_billed", "updated_at", "case_ref", "open_tasks"] = "priority",
               order: Literal["asc", "desc"] = "desc", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    qry = db.query(ClaimCase).join(Encounter)
    if status:
        qry = qry.filter(ClaimCase.status.in_(status.split(",")))
    if root:
        qry = qry.filter(ClaimCase.root_blocker_code == root)
    if stage:
        qry = qry.filter(ClaimCase.root_blocker_stage == stage)
    if q:
        like = f"%{q.strip()}%"
        # case_ref embeds the hadm_id (CC-<hadm_id>), so it also covers encounter-id search
        qry = qry.filter(or_(ClaimCase.case_ref.ilike(like), Encounter.drg_description.ilike(like),
                             ClaimCase.root_blocker_code.ilike(like)))
    total = qry.count()
    col = SORTABLE[sort]
    qry = qry.order_by(col.desc() if order == "desc" else col.asc(), ClaimCase.id)
    rows = qry.offset((page - 1) * page_size).limit(page_size).all()
    return dict(total=total, page=page, page_size=page_size, items=[case_row(c) for c in rows])


@router.get("/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db)):
    c = _case_or_404(db, case_id)
    e = c.encounter
    cur = {s.stage: s for s in db.query(StageExecution).filter_by(case_id=c.id, is_current=True)}
    out = lambda s: (cur[s].output or {}).get("data") if s in cur else None  # noqa: E731
    mp = db.query(MemberPolicy).filter_by(subject_id=e.subject_id).first()
    findings = db.query(Finding).filter_by(case_id=c.id, is_current=True).order_by(Finding.id).all()
    tasks = db.query(ReviewTask).filter_by(case_id=c.id).order_by(ReviewTask.id).all()
    corrections = db.query(Correction).filter_by(case_id=c.id).order_by(Correction.id).all()
    packet = db.query(ClaimPacket).filter_by(case_id=c.id, is_current=True).first()
    ca = e.claim_admin
    return dict(
        case=case_row(c),
        encounter=dict(hadm_id=e.hadm_id, subject_id=e.subject_id, data_source=e.data_source, gender=e.gender,
                       anchor_age=e.anchor_age, admittime=_iso(e.admittime), dischtime=_iso(e.dischtime),
                       admission_type=e.admission_type, admission_location=e.admission_location,
                       discharge_location=e.discharge_location, drg_code=e.drg_code, drg_description=e.drg_description,
                       diagnoses=[dict(seq_num=d.seq_num, icd_code=d.icd_code, icd_version=d.icd_version,
                                       long_title=d.long_title) for d in e.diagnoses],
                       procedures=[dict(seq_num=p.seq_num, icd_code=p.icd_code, chartdate=_iso(p.chartdate),
                                        long_title=p.long_title) for p in e.procedures],
                       transfers=[dict(careunit=t.careunit, intime=_iso(t.intime), outtime=_iso(t.outtime),
                                       eventtype=t.eventtype) for t in e.transfers],
                       note=e.note and dict(text=e.note.text, provenance=e.note.provenance),
                       checklist=[dict(item=x.item, status=x.status) for x in e.checklist],
                       documents=[x for x in ((ca and ca.documents) or "").split(",") if x],
                       claim_admin=ca and dict(claim_type=ca.claim_type, room_category=ca.room_category,
                                               notification_date=_iso(ca.notification_date),
                                               preauth_request_date=_iso(ca.preauth_request_date),
                                               claim_filed_date=_iso(ca.claim_filed_date),
                                               bill_header_total=ca.bill_header_total, provenance=ca.provenance)),
        policy=mp and dict(member_id=mp.member_id, policy_version_id=mp.policy_version_id, sum_insured=mp.sum_insured,
                           policy_start_date=_iso(mp.policy_start_date), policy_end_date=_iso(mp.policy_end_date),
                           policy_cancelled_date=_iso(mp.policy_cancelled_date),
                           insured_age_at_entry=mp.insured_age_at_entry, deductible_opted=mp.deductible_opted,
                           deductible_amount=mp.deductible_amount, provenance=mp.provenance),
        billing=out("billing"), adjudication=out("adjudication"), eligibility=out("eligibility"),
        historical=out("historical"), reconciliation=out("reconciliation"), root_blocker=out("root_blocker"),
        data_validation=out("data_validation"),
        stages={s: dict(status=x.status, stage_status=x.stage_status, duration_ms=x.duration_ms, run_id=x.run_id)
                for s, x in cur.items()},
        findings=[finding_row(f) for f in findings], tasks=[task_row(t) for t in tasks],
        corrections=[dict(id=x.id, op=x.op, domain=x.domain, payload=x.payload, before=x.before, after=x.after,
                          actor=x.actor, reason=x.reason, run_id=x.run_id, created_at=_iso(x.created_at))
                     for x in corrections],
        packet=packet and dict(status=packet.status, run_id=packet.run_id, summary=packet.packet.get("summary")),
        correction_ops=sorted(OP_DOMAINS),
    )


class ProcessIn(BaseModel):
    execution_mode: Optional[Literal["parallel", "sequential"]] = None
    rerun_policy: Optional[Literal["targeted", "full"]] = None


@router.post("/cases/{case_id}/process")
def process_case(case_id: int, body: ProcessIn = ProcessIn(), db: Session = Depends(get_db),
                 actor: str = Depends(require_writer)):
    c = _case_or_404(db, case_id)
    with _process_lock:
        orch = Orchestrator(db, body.execution_mode, body.rerun_policy, actor=actor)
        has_runs = db.query(PipelineRun).filter_by(case_id=c.id).count() > 0
        run = orch.process(c, trigger="MANUAL_RERUN" if has_runs else "INITIAL",
                           changed_domains=list(dag.ALL_INPUT_DOMAINS) if has_runs else [])
    stages = db.query(StageExecution).filter_by(run_id=run.id).order_by(StageExecution.id).all()
    return dict(run=run_row(run, stages), case=case_row(c))


class RerunIn(BaseModel):
    domains: List[Literal["encounter", "diagnoses", "procedures", "discharge_note", "documents", "checklist", "bill",
                          "claim_admin", "policy", "reviews"]] = Field(..., min_length=1)
    execution_mode: Optional[Literal["parallel", "sequential"]] = None
    rerun_policy: Optional[Literal["targeted", "full"]] = None


@router.post("/cases/{case_id}/rerun")
def rerun_case(case_id: int, body: RerunIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    """Retry / targeted rerun: treat the given input domains as changed. Stages whose inputs
    are unchanged are reused (verified by input hash), so this is safe to call repeatedly."""
    c = _case_or_404(db, case_id)
    with _process_lock:
        run = Orchestrator(db, body.execution_mode, body.rerun_policy, actor=actor).process(
            c, trigger="RETRY" if c.status == "ERROR" else "MANUAL_RERUN", changed_domains=body.domains)
    stages = db.query(StageExecution).filter_by(run_id=run.id).order_by(StageExecution.id).all()
    return dict(run=run_row(run, stages), case=case_row(c))


class ProcessPendingIn(BaseModel):
    limit: int = Field(500, ge=1, le=5000)
    execution_mode: Optional[Literal["parallel", "sequential"]] = None


@router.post("/cases/process-pending")
def process_pending(body: ProcessPendingIn = ProcessPendingIn(), db: Session = Depends(get_db),
                    actor: str = Depends(require_writer)):
    cases = db.query(ClaimCase).filter_by(status="NEW").order_by(ClaimCase.id).limit(body.limit).all()
    done = failed = 0
    with _process_lock:
        orch = Orchestrator(db, body.execution_mode, actor=actor)
        for c in cases:
            run = orch.process(c, trigger="INITIAL")
            done += 1
            failed += run.status == "FAILED"
    return dict(processed=done, failed=failed)


@router.get("/cases/{case_id}/runs")
def case_runs(case_id: int, db: Session = Depends(get_db)):
    _case_or_404(db, case_id)
    runs = db.query(PipelineRun).filter_by(case_id=case_id).order_by(PipelineRun.id.desc()).all()
    out = []
    for r in runs:
        st = db.query(StageExecution).filter_by(run_id=r.id).order_by(StageExecution.id).all()
        out.append(run_row(r, st))
    return out


@router.get("/cases/{case_id}/stage-history/{stage}")
def stage_history(case_id: int, stage: str, db: Session = Depends(get_db)):
    if stage not in dag.STAGES:
        raise HTTPException(404, "unknown stage")
    rows = (db.query(StageExecution).filter_by(case_id=case_id, stage=stage)
            .filter(StageExecution.status == "SUCCEEDED").order_by(StageExecution.id.desc()).limit(10).all())
    return [dict(id=r.id, run_id=r.run_id, started_at=_iso(r.started_at), is_current=r.is_current,
                 input_hash=r.input_hash, output_hash=r.output_hash, output=r.output) for r in rows]


@router.get("/cases/{case_id}/events")
def case_events(case_id: int, db: Session = Depends(get_db)):
    _case_or_404(db, case_id)
    return [event_row(e) for e in db.query(AuditEvent).filter_by(case_id=case_id).order_by(AuditEvent.id.desc()).limit(200)]


@router.get("/cases/{case_id}/packet")
def case_packet(case_id: int, db: Session = Depends(get_db)):
    _case_or_404(db, case_id)
    p = db.query(ClaimPacket).filter_by(case_id=case_id, is_current=True).first()
    if not p:
        raise HTTPException(404, "no claim packet yet; process the case first")
    return dict(status=p.status, run_id=p.run_id, created_at=_iso(p.created_at), **p.packet)


class CorrectionIn(BaseModel):
    op: str
    payload: Dict[str, Any]
    reason: str = Field(..., min_length=3, max_length=500)
    review_task_id: Optional[int] = None
    expected_version: Optional[int] = None

    @field_validator("op")
    @classmethod
    def known_op(cls, v):
        if v not in OP_DOMAINS:
            raise ValueError(f"op must be one of {sorted(OP_DOMAINS)}")
        return v


@router.post("/cases/{case_id}/corrections")
def correct_case(case_id: int, body: CorrectionIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    c = _case_or_404(db, case_id)
    before_status = c.status
    with _process_lock:
        try:
            corr, run = apply_correction(db, c, body.op, body.payload, actor=actor, reason=body.reason,
                                         review_task_id=body.review_task_id, expected_version=body.expected_version)
        except ConflictError as e:
            db.rollback()
            raise HTTPException(409, str(e))
        except (CorrectionError, KeyError, ValueError, TypeError) as e:
            db.rollback()
            raise HTTPException(422, f"correction rejected: {e}")
    stages = db.query(StageExecution).filter_by(run_id=run.id).order_by(StageExecution.id).all()
    return dict(correction=dict(id=corr.id, op=corr.op, domain=corr.domain, before=corr.before, after=corr.after),
                run=run_row(run, stages), status_before=before_status, case=case_row(c))


# ---------------------------------------------------------------------------
# review queue
# ---------------------------------------------------------------------------

@router.get("/review-tasks")
def list_tasks(db: Session = Depends(get_db), status: str = "OPEN", severity: Optional[str] = None,
               owner_stage: Optional[str] = None, code: Optional[str] = None, page: int = Query(1, ge=1),
               page_size: int = Query(25, ge=1, le=100)):
    qry = db.query(ReviewTask, ClaimCase).join(ClaimCase, ClaimCase.id == ReviewTask.case_id)
    if status != "ALL":
        qry = qry.filter(ReviewTask.status.in_(status.split(",")))
    if severity:
        qry = qry.filter(ReviewTask.severity == severity)
    if owner_stage:
        qry = qry.filter(ReviewTask.owner_stage == owner_stage)
    if code:
        qry = qry.filter(ReviewTask.code == code)
    total = qry.count()
    rows = (qry.order_by((ReviewTask.severity == "BLOCKING").desc(), ClaimCase.priority.desc(), ReviewTask.id)
            .offset((page - 1) * page_size).limit(page_size).all())
    return dict(total=total, page=page, page_size=page_size, items=[task_row(t, c) for t, c in rows])


class DecisionIn(BaseModel):
    decision: Literal["ACCEPT_WITH_JUSTIFICATION", "CONFIRM_BLOCKED"]
    note: str = Field(..., min_length=5, max_length=1000)


@router.post("/review-tasks/{task_id}/decision")
def decide(task_id: int, body: DecisionIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    t = db.get(ReviewTask, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    if t.status != "OPEN":
        raise HTTPException(409, f"task is already {t.status}")
    if body.decision not in (t.allowed_decisions or []):
        raise HTTPException(422, f"decision {body.decision} not allowed for this task; allowed: {t.allowed_decisions}")
    c = _case_or_404(db, t.case_id)
    with _process_lock:
        db.add(ReviewDecision(case_id=c.id, task_id=t.id, code=t.code, task_key=t.task_key, decision=body.decision,
                              note=body.note, actor=actor))
        t.resolved_at, t.resolved_by, t.resolution_note = dt.datetime.utcnow(), actor, body.note
        audit(db, "REVIEW_DECISION", f"{body.decision} on {t.code}", actor, c.id, "review_task", t.id, note=body.note)
        db.flush()
        run = Orchestrator(db, actor=actor).process(c, trigger="REVIEW_DECISION", changed_domains=["reviews"])
    stages = db.query(StageExecution).filter_by(run_id=run.id).order_by(StageExecution.id).all()
    db.refresh(t)
    return dict(task=task_row(t, c), run=run_row(run, stages), case=case_row(c))


# ---------------------------------------------------------------------------
# workflow and audit
# ---------------------------------------------------------------------------

@router.get("/workflow/graph")
def workflow_graph():
    return dict(
        stages=[dict(id=s, label=spec["label"], owner=spec["owner"], deps=spec["deps"], domains=spec["domains"],
                     consumes={k: v for k, v in spec["consumes"].items()}) for s, spec in dag.STAGES.items()],
        waves=dag.waves(), domain_owner=dag.DOMAIN_OWNER,
        rerun_plans={d: dag.rerun_plan_for_domain(d) for d in dag.DOMAIN_OWNER},
    )


@router.get("/audit-events/verify")
def audit_verify(db: Session = Depends(get_db)):
    """Recompute the audit hash chain. ok=false identifies the first event that was altered or removed."""
    return verify_audit_chain(db)


@router.get("/live")
def live():
    """Liveness probe (no database access)."""
    return dict(status="alive")


@router.get("/audit-events")
def audit_events(db: Session = Depends(get_db), case_id: Optional[int] = None, event_type: Optional[str] = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    qry = db.query(AuditEvent)
    if case_id:
        qry = qry.filter_by(case_id=case_id)
    if event_type:
        qry = qry.filter_by(event_type=event_type)
    total = qry.count()
    rows = qry.order_by(AuditEvent.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return dict(total=total, items=[event_row(e) for e in rows])


# ---------------------------------------------------------------------------
# data laboratory (no ground truth exposed)
# ---------------------------------------------------------------------------

@router.get("/data/status")
def data_status(db: Session = Depends(get_db)):
    imports = db.query(DatasetImport).order_by(DatasetImport.id.desc()).all()
    from app.datasets.mimic import table_path
    mimic_present = table_path(settings.mimic_clinical_dir, "hosp", "admissions") is not None
    by_source = dict(db.query(Encounter.data_source, func.count(Encounter.id)).group_by(Encounter.data_source).all())
    dq = (db.query(Finding.code, Finding.severity, func.count(Finding.id))
          .filter(Finding.is_current.is_(True), Finding.stage.in_(["ingestion", "data_validation", "discharge_readiness"]))
          .group_by(Finding.code, Finding.severity).all())
    return dict(
        data_mode=settings.data_mode, mimic_files_present=mimic_present,
        mimic_instructions="README.md (section: MIMIC-IV Clinical Database Demo v2.2)",
        encounters_by_source=by_source,
        imports=[dict(id=i.id, mode=i.mode, source_label=i.source_label, status=i.status, manifest_mode=i.manifest_mode,
                      generator_version=i.generator_version, parameters=i.parameters, n_encounters=i.n_encounters,
                      imported_at=_iso(i.imported_at), manifest_sha256=i.manifest_sha256,
                      validation=dict(errors=(i.validation_report or {}).get("errors", []),
                                      warnings=(i.validation_report or {}).get("warnings", []),
                                      stats={k: v for k, v in ((i.validation_report or {}).get("stats") or {}).items()
                                             if k not in GT_DERIVED_STATS}))
                 for i in imports],
        live_data_quality=[dict(code=c, severity=s, count=n) for c, s, n in dq],
    )


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

SETTING_VALIDATORS = {
    "historical_z_threshold": lambda v: isinstance(v, (int, float)) and 1.0 <= v <= 10.0,
    "historical_min_peers": lambda v: isinstance(v, int) and 3 <= v <= 500,
    "routing_policy": lambda v: v in ("root", "all_findings"),
    "execution_mode": lambda v: v in ("parallel", "sequential"),
    "rerun_policy": lambda v: v in ("targeted", "full"),
    "max_stage_retries": lambda v: isinstance(v, int) and 0 <= v <= 5,
    "billing_price_tolerance_pct": lambda v: isinstance(v, (int, float)) and 0 <= v <= 0.5,
    "billing_amount_tolerance_inr": lambda v: isinstance(v, (int, float)) and 0 <= v <= 100,
    "readiness_required_documents": lambda v: isinstance(v, list) and all(isinstance(x, str) and x for x in v),
    "readiness_required_checklist": lambda v: isinstance(v, list) and all(isinstance(x, str) and x for x in v),
}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    ctx = get_context(db)
    return dict(values=ctx.settings, defaults=DEFAULT_SETTINGS,
                environment=dict(app_env=settings.app_env, data_mode=settings.data_mode, version=settings.app_version,
                                 database_engine=settings.database_url.split(":")[0],
                                 write_protected=bool(settings.write_token), cors_origins=settings.cors_origins,
                                 experiments_enabled=settings.allow_experiments))


class SettingsIn(BaseModel):
    values: Dict[str, Any]


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    bad = [k for k, v in body.values.items() if k not in SETTING_VALIDATORS or not SETTING_VALIDATORS[k](v)]
    if bad:
        raise HTTPException(422, f"invalid setting(s): {bad}")
    for k, v in body.values.items():
        db.merge(AppSetting(key=k, value=v))
    audit(db, "SETTINGS_CHANGED", f"updated {sorted(body.values)}", actor, entity_type="settings", values=body.values)
    db.commit()
    invalidate_context()
    return get_settings(db)
