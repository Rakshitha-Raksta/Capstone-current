"""
Agent Execution Studio API: run the real workflow for one encounter and watch it happen.

Nothing here re-implements the workflow. A studio run calls the same Orchestrator.process (or the
same apply_correction) that the rest of the application uses, under the same processing lock, in a
background thread with its own database session. The orchestrator's optional `on_event` observer
records what it actually decides and does (stage started / finished / reused / not re-run /
skipped), with timings measured by the orchestrator itself. Clients poll those events while the
run is in progress and then read the persisted stage executions, which remain the source of truth.

Limitations (documented, not hidden):
- the event buffer lives in this process's memory (last 50 studio runs); with several server
  workers, poll the worker that accepted the run. Persisted executions are unaffected.
- execution is never paused: pausing mid-run would hold the database write transaction open.
  "Teaching mode" in the UI steps through the recorded events afterwards.
"""
import inspect
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.clearclaim import dag
from app.clearclaim.api import _process_lock, case_row, require_writer, run_row
from app.clearclaim.corrections import OP_DOMAINS, ConflictError, CorrectionError, apply_correction
from app.clearclaim.findings import TITLES
from app.clearclaim.models import ClaimCase, Encounter, PipelineRun, StageExecution
from app.clearclaim.orchestrator import Orchestrator
from app.clearclaim.stages import STAGE_FUNCTIONS
from app.database.db import SessionLocal, get_db

router = APIRouter(prefix="/api/v1/studio", tags=["execution studio"])

_RUNS: "OrderedDict[str, dict]" = OrderedDict()
_RUNS_LOCK = threading.Lock()
_MAX_RUNS = 50

# Patient-level fields that the studio never shows (the case page remains the place for the full record).
_HIDDEN_KEYS = {"subject_id"}
_SUMMARISED_KEYS = {"packet": "FHIR-shaped claim packet hidden in the studio; open the case page → Claim packet"}


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _HIDDEN_KEYS:
                out[k] = "[hidden in studio]"
            elif k in _SUMMARISED_KEYS:
                out[k] = f"[{_SUMMARISED_KEYS[k]}]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _checks_for(fn) -> list:
    """Finding codes a stage can raise, read from its own source (not a hand-maintained list)."""
    try:
        src = inspect.getsource(fn)
    except OSError:
        return []
    codes = sorted(set(re.findall(r'finding\(\s*"([A-Z_]+)"', src)))
    return [dict(code=c, title=TITLES.get(c, c)) for c in codes]


_ANALYSIS_NOTES = {
    "root_blocker": "Applies the documented conditional causal rules (docs/ROOT_BLOCKER_RULES.md) to all upstream "
                    "findings, separating root blockers from downstream consequences. Raises no findings of its own.",
    "review_routing": "Turns root blockers (or, in the ablation, all findings) into grouped review tasks with "
                      "owner, allowed decisions and required re-runs. Raises no findings of its own.",
    "claim_generation": "Builds the internal claim-preparation status and the FHIR-shaped packet. Blocking "
                        "consequences that were not routed keep the case blocked. Raises no findings of its own.",
}


def _stage_registry():
    wave_of = {s: i for i, w in enumerate(dag.waves()) for s in w}
    out = []
    for s in dag.STAGE_ORDER:
        spec, fn = dag.STAGES[s], STAGE_FUNCTIONS[s]
        out.append(dict(
            id=s, label=spec["label"], owner=spec["owner"], wave=wave_of[s], deps=spec["deps"],
            input_domains=spec["domains"],
            upstream_inputs={k: v for k, v in spec["consumes"].items()},
            implementation=f"{fn.__module__}.{fn.__name__}",
            kind="DETERMINISTIC_RULES", uses_llm=False,
            checks=_checks_for(fn), note=_ANALYSIS_NOTES.get(s)))
    return out


@router.get("/stages")
def stages():
    from app.rag.answer import get_provider
    provider = type(get_provider()).__name__
    return dict(
        stages=_stage_registry(), waves=dag.waves(),
        llm=dict(workflow_uses_llm=False,
                 note="All 11 workflow stages are deterministic Python rules; none calls a language model.",
                 policy_explainer_provider=provider,
                 policy_explainer_note="The separate policy explainer (case page) is not part of the workflow and cannot "
                                       "change any result. It is extractive unless an LLM API key is configured."))


def _parse_hadm(hadm_id: str) -> int:
    if not re.fullmatch(r"\d{1,12}", hadm_id or ""):
        raise HTTPException(422, "An Encounter ID is digits only, for example 98000056.")
    return int(hadm_id)


def _case_for(db, hadm: int) -> ClaimCase:
    enc = db.query(Encounter).filter_by(hadm_id=hadm).first()
    case = enc and db.query(ClaimCase).filter_by(encounter_id=enc.id).first()
    if case is None:
        sample = [r[0] for r in db.query(Encounter.hadm_id).order_by(Encounter.hadm_id).limit(3)]
        n = db.query(Encounter).count()
        hint = f" The loaded dataset has {n} encounters, for example {', '.join(map(str, sample))}." if n else \
            " No encounters are loaded; run scripts/setup_demo.py first."
        raise HTTPException(404, f"No encounter with ID {hadm} is loaded.{hint}")
    return case


@router.get("/encounters/{hadm_id}")
def encounter(hadm_id: str, db: Session = Depends(get_db)):
    case = _case_for(db, _parse_hadm(hadm_id))
    last = db.query(PipelineRun).filter_by(case_id=case.id).order_by(PipelineRun.id.desc()).first()
    return dict(case=case_row(case), last_run=last and run_row(last))


class CorrectionSpec(BaseModel):
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


class StudioRunIn(BaseModel):
    hadm_id: str
    mode: Literal["full", "recheck", "correction"] = "full"
    correction: Optional[CorrectionSpec] = None


def _worker(token: str, case_id: int, body: StudioRunIn, actor: str):
    rec = _RUNS[token]

    def on_event(ev):
        with _RUNS_LOCK:
            rec["events"].append(dict(ev, seq=len(rec["events"])))

    db = SessionLocal()
    try:
        with _process_lock:
            rec["status"] = "running"
            case = db.get(ClaimCase, case_id)
            has_runs = db.query(PipelineRun).filter_by(case_id=case.id).count() > 0
            if body.mode == "correction":
                c = body.correction
                orch = Orchestrator(db, actor=actor, on_event=on_event)
                _, run = apply_correction(db, case, c.op, c.payload, actor=actor, reason=c.reason,
                                          review_task_id=c.review_task_id, expected_version=c.expected_version,
                                          orchestrator=orch)
            else:
                policy = "full" if body.mode == "full" else "targeted"
                orch = Orchestrator(db, rerun_policy=policy, actor=actor, on_event=on_event)
                run = orch.process(case, trigger="MANUAL_RERUN" if has_runs else "INITIAL",
                                   changed_domains=list(dag.ALL_INPUT_DOMAINS) if has_runs else [])
        rec.update(status="finished", run_id=run.id, run_status=run.status, case_status=case.status)
    except ConflictError as e:
        db.rollback()
        rec.update(status="rejected", http_status=409, error=str(e))
    except CorrectionError as e:
        db.rollback()
        rec.update(status="rejected", http_status=422, error=str(e))
    except Exception as e:  # recorded and shown; never reported as success
        db.rollback()
        rec.update(status="error", http_status=500, error=f"{type(e).__name__}: {e}")
    finally:
        rec["finished_at"] = time.time()
        db.close()


@router.post("/runs", status_code=202)
def start_run(body: StudioRunIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    case = _case_for(db, _parse_hadm(body.hadm_id))
    if body.mode == "correction" and body.correction is None:
        raise HTTPException(422, "A correction run needs a correction (operation, payload and reason).")
    token = uuid.uuid4().hex
    with _RUNS_LOCK:
        while len(_RUNS) >= _MAX_RUNS:
            _RUNS.popitem(last=False)
        _RUNS[token] = dict(status="queued", events=[], case_id=case.id, hadm_id=int(body.hadm_id), mode=body.mode,
                            started_at=time.time(), run_id=None, error=None)
    threading.Thread(target=_worker, args=(token, case.id, body, actor), daemon=True, name=f"studio-{token[:8]}").start()
    return dict(token=token, case_id=case.id, status="queued")


@router.get("/runs/{token}")
def run_events(token: str, after: int = 0):
    rec = _RUNS.get(token)
    if rec is None:
        raise HTTPException(404, "Unknown or expired studio run (only the last 50 are kept in memory).")
    with _RUNS_LOCK:
        events = rec["events"][max(0, after):]
        return dict(status=rec["status"], case_id=rec["case_id"], hadm_id=rec["hadm_id"], mode=rec["mode"],
                    run_id=rec.get("run_id"), run_status=rec.get("run_status"), case_status=rec.get("case_status"),
                    error=rec.get("error"), http_status=rec.get("http_status"), events=events,
                    next_after=after + len(events))


@router.get("/pipeline-runs/{run_id}")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    """Persisted record of one run, stage by stage, with outputs (redacted) and evidence."""
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(404, "pipeline run not found")
    rows = {s.stage: s for s in db.query(StageExecution).filter_by(run_id=run.id).order_by(StageExecution.id)}
    reg = {s["id"]: s for s in _stage_registry()}
    stages = []
    for s in dag.STAGE_ORDER:
        se = rows.get(s)
        source, source_note = None, None
        if se is not None and se.status == "SUCCEEDED":
            source = se
        elif se is not None and se.status == "REUSED" and se.reused_from_id:
            source, source_note = db.get(StageExecution, se.reused_from_id), f"reused output of execution #{se.reused_from_id}"
        elif se is None:
            source = (db.query(StageExecution).filter(StageExecution.case_id == run.case_id, StageExecution.stage == s,
                                                      StageExecution.status == "SUCCEEDED", StageExecution.run_id < run.id)
                      .order_by(StageExecution.id.desc()).first())
            source_note = f"not part of this run; previous result from execution #{source.id}" if source else \
                "not part of this run and no earlier result exists"
        out = source.output if source is not None and source.output else None
        stages.append(dict(
            stage=s, label=reg[s]["label"], deps=reg[s]["deps"], input_domains=reg[s]["input_domains"],
            upstream_inputs=reg[s]["upstream_inputs"],
            execution=se and dict(id=se.id, status=se.status, stage_status=se.stage_status, attempts=se.attempts,
                                  duration_ms=se.duration_ms, input_hash=se.input_hash, output_hash=se.output_hash,
                                  reused_from_id=se.reused_from_id, error=se.error),
            output_source=source_note or (f"output of execution #{source.id}" if source is not None else None),
            output=_redact(out["data"]) if out else None,
            findings=_redact(out["findings"]) if out else []))
    case = db.get(ClaimCase, run.case_id)
    return dict(run=run_row(run), case=case_row(case), stages=stages)
