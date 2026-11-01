"""
Agent Control Room API: run the real workflow sequentially or in parallel, compare the two with
repeated measured runs, and compare full vs targeted re-execution after a correction.

Nothing here re-implements the workflow. Every run is Orchestrator.process (or apply_correction)
with the orchestrator's own event observer; all timings are measured inside the orchestrator with
time.perf_counter() (see app/clearclaim/timing.py for the metric definitions).

Where runs happen
- "Single run" (sequential or parallel): on the live database, exactly like the Execution studio.
  Full rerun policy, so all 11 stages execute and the modes are comparable.
- Comparisons (sequential vs parallel; full vs targeted correction): on an ISOLATED copy of the
  SQLite database taken under the processing lock, discarded afterwards. The live data is never
  changed by a comparison. Other database engines are refused with a clear message.

Records are stored in the existing cc_experiment_runs table (config.kind = cr_run | cr_comparison |
cr_correction), so no schema migration is needed; the Experiments page does not list them.
"""
import csv
import datetime as dt
import io
import os
import shutil
import sqlite3
import statistics
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.clearclaim import dag
from app.clearclaim.api import _process_lock, require_writer
from app.clearclaim.context import build_context
from app.clearclaim.corrections import ConflictError, CorrectionError, apply_correction
from app.clearclaim.models import ClaimCase, ExperimentRun, PipelineRun, StageExecution
from app.clearclaim.orchestrator import Orchestrator, canonical_hash
from app.clearclaim.snapshot import build_snapshot
from app.clearclaim.studio import CorrectionSpec, _case_for, _parse_hadm, _stage_registry
from app.clearclaim.timing import run_metrics
from app.database.db import SessionLocal, engine, get_db

router = APIRouter(prefix="/api/v1/control-room", tags=["agent control room"])

KINDS = ("cr_run", "cr_comparison", "cr_correction")
_JOBS: "OrderedDict[str, dict]" = OrderedDict()
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 30

PURPOSE = {
    "ingestion": "Loads the encounter and checks that every source record the workflow needs is present (bill lines, policy, notes).",
    "data_validation": "Checks the clinical record: dates and length of stay, ICD codes against the reference, sex-specific codes, "
                       "procedure dates inside the admission, and coded diagnoses versus the discharge note.",
    "discharge_readiness": "Checks administrative discharge readiness: required claim documents submitted and checklist items complete. "
                           "It does not assess clinical fitness for discharge.",
    "billing": "Reconciles every bill line against the rate card: arithmetic, price above tariff, duplicate lines, bed-days versus "
               "length of stay, and procedures billed versus documented.",
    "eligibility": "Applies the policy's eligibility rules using the original rule evaluators: policy active, waiting periods, "
                   "exclusions, notification and filing deadlines, pre-authorisation.",
    "adjudication": "Computes the rule-based coverage estimate: disputed amounts, sub-limits, room rent, deductible, co-payment "
                    "and sum insured, each step citing its rule. Not an insurer decision.",
    "historical": "Compares the bill and length of stay with peer claims in the same MS-DRG (modified z-score). Advisory only.",
    "reconciliation": "Checks that bill header, bill lines and the coverage waterfall agree, and flags coverage computed on a disputed bill.",
    "root_blocker": "Applies documented causal rules to all findings to separate root blockers from downstream consequences.",
    "review_routing": "Turns root blockers into grouped review tasks with an owner, allowed decisions and the stages a fix will re-run.",
    "claim_generation": "Sets the internal claim-preparation status and builds the FHIR-shaped claim packet.",
}
DISCLOSURE = ("ClearClaim orchestrates 11 rule-based workflow stages. The visualization reflects actual backend execution. "
              "The stages are not independent autonomous AI models.")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now():
    return dt.datetime.utcnow().isoformat() + "Z"


def final_fingerprint(db, case_id: int) -> dict:
    """Current output hash of every stage plus the case status: equal fingerprints = equal final outputs."""
    rows = db.query(StageExecution).filter_by(case_id=case_id, is_current=True).all()
    per_stage = {r.stage: r.output_hash for r in rows}
    case = db.get(ClaimCase, case_id)
    body = dict(stages={s: per_stage.get(s) for s in dag.STAGE_ORDER}, status=case.status)
    return dict(hash=canonical_hash(body), per_stage=body["stages"], status=case.status)


def _isolated_copy():
    if engine.url.get_backend_name() != "sqlite":
        raise HTTPException(409, "Isolated comparisons need the SQLite development database (they copy it so live data is "
                                 "never changed). This server uses another database engine.")
    src_path = os.path.abspath(engine.url.database)
    tmp = tempfile.mkdtemp(prefix="clearclaim_cr_")
    dst_path = os.path.join(tmp, "isolated.db")
    with _process_lock:                                   # never copy in the middle of a write
        src, dst = sqlite3.connect(src_path), sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    eng = create_engine(f"sqlite:///{dst_path}", connect_args={"check_same_thread": False})
    return tmp, eng, sessionmaker(bind=eng, autoflush=False, autocommit=False)


class Cancelled(Exception):
    """Raised between runs when the user cancelled the job (a run that has started always completes)."""


def _check_cancel(token):
    if _JOBS.get(token, {}).get("cancel"):
        raise Cancelled()


def _drop_copy(tmp, eng):
    eng.dispose()
    shutil.rmtree(tmp, ignore_errors=True)


def _job(kind, hadm, case_id):
    token = uuid.uuid4().hex
    with _JOBS_LOCK:
        while len(_JOBS) >= _MAX_JOBS:
            _JOBS.popitem(last=False)
        _JOBS[token] = dict(kind=kind, hadm_id=hadm, case_id=case_id, status="queued", events=[], current=None,
                            record_id=None, error=None, http_status=None)
    return token


def _collector(token, label, mode):
    def on_event(ev):
        with _JOBS_LOCK:
            j = _JOBS[token]
            j["current"] = label
            j["events"].append(dict(ev, seq=len(j["events"]), label=label, mode=mode))
    return on_event


def _one_run(db, case, mode, policy, label, token, actor, trigger=None, correction=None):
    """Run the real orchestrator once and return a self-contained, measured record."""
    events = []
    live = _collector(token, label, mode)

    def on_event(ev):
        events.append(ev)
        live(ev)
    orch = Orchestrator(db, execution_mode=mode, rerun_policy=policy, actor=actor, ctx=build_context(db) if db.bind is not engine else None,
                        on_event=on_event)
    snap = canonical_hash(build_snapshot(db, case))
    started = _now()
    t0 = time.perf_counter()
    if correction is not None:
        corr, run = apply_correction(db, case, correction.op, correction.payload, actor=actor, reason=correction.reason,
                                     review_task_id=correction.review_task_id, expected_version=correction.expected_version,
                                     orchestrator=orch)
    else:
        has_runs = db.query(PipelineRun).filter_by(case_id=case.id).count() > 0
        run = orch.process(case, trigger=trigger or ("MANUAL_RERUN" if has_runs else "INITIAL"),
                           changed_domains=list(dag.ALL_INPUT_DOMAINS) if has_runs else [])
    call_ms = (time.perf_counter() - t0) * 1000
    m = run_metrics(events)
    m["call_ms"] = round(call_ms, 4)                       # includes snapshot reload by the caller's session
    return dict(label=label, mode=mode, rerun_policy=policy, pipeline_run_id=run.id, started_at=started, finished_at=_now(),
                input_snapshot_hash=snap, metrics=m, fingerprint=final_fingerprint(db, case.id), events=events)


def _stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return dict(n=len(vals), median=round(statistics.median(vals), 4), min=round(min(vals), 4), max=round(max(vals), 4),
                mean=round(statistics.mean(vals), 4), sd=round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0)


def _persist(kind, hadm, config, summary, status, error=None):
    db = SessionLocal()
    try:
        r = ExperimentRun(name=f"control room {kind[3:]} {hadm}", config=dict(config, kind=kind, hadm_id=hadm), status=status,
                          summary=summary, error=error, finished_at=dt.datetime.utcnow())
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def _finish(token, record_id=None, status="finished", error=None, http_status=None):
    with _JOBS_LOCK:
        _JOBS[token].update(status=status, record_id=record_id, error=error, http_status=http_status, current=None)


# ---------------------------------------------------------------------------
# workers
# ---------------------------------------------------------------------------

def _w_single(token, case_id, hadm, mode, actor):
    db = SessionLocal()
    try:
        _JOBS[token]["status"] = "running"
        with _process_lock:
            case = db.get(ClaimCase, case_id)
            rec = _one_run(db, case, mode, "full", f"{mode} run", token, actor)
        status = "COMPLETED" if rec["metrics"]["status"] == "SUCCEEDED" else "FAILED"
        rid = _persist("cr_run", hadm, dict(mode=mode, isolated=False), dict(runs=[rec], live=True), status)
        _finish(token, rid)
    except Exception as e:
        db.rollback()
        _finish(token, status="error", error=f"{type(e).__name__}: {e}", http_status=500)
    finally:
        db.close()


def _w_compare(token, case_id, hadm, reps, actor):
    tmp = eng = None
    runs = []
    try:
        _JOBS[token]["status"] = "running"
        tmp, eng, S = _isolated_copy()
        db = S()
        case = db.get(ClaimCase, case_id)
        # one discarded warm-up pair, then alternating order (ABBA) so neither mode always runs first
        for rep in range(reps + 1):
            order = ["sequential", "parallel"] if rep % 2 == 0 else ["parallel", "sequential"]
            for mode in order:
                label = "warm-up" if rep == 0 else f"rep {rep}"
                _check_cancel(token)
                r = _one_run(db, case, mode, "full", f"{label} · {mode}", token, actor)
                r.update(rep=rep, warmup=rep == 0)
                runs.append(r)
        db.close()
        measured = [r for r in runs if not r["warmup"]]
        ok = [r for r in measured if r["metrics"]["status"] == "SUCCEEDED"]
        by = {m: [r for r in ok if r["mode"] == m] for m in ("sequential", "parallel")}
        stats = {m: {k: _stats([r["metrics"][k] for r in rs]) for k in
                     ("wall_ms", "end_to_end_ms", "stage_sum_ms", "busy_ms", "overhead_ms", "critical_path_ms", "max_concurrency")}
                 for m, rs in by.items()}
        fps = {r["fingerprint"]["hash"] for r in runs}
        snaps = {r["input_snapshot_hash"] for r in runs}
        seq_med = stats["sequential"]["wall_ms"]["median"] if stats["sequential"]["wall_ms"] else None
        par_med = stats["parallel"]["wall_ms"]["median"] if stats["parallel"]["wall_ms"] else None
        reduction = round((seq_med - par_med) / seq_med * 100, 2) if seq_med and seq_med > 0 and par_med is not None else None
        pairs = sorted({tuple(p) for r in by["parallel"] for p in r["metrics"]["overlapping_pairs"]})
        summary = dict(
            runs=runs, repetitions=reps, isolated=True, stats=stats,
            successful=dict(sequential=len(by["sequential"]), parallel=len(by["parallel"])),
            failed=len(measured) - len(ok),
            wall_reduction_pct=reduction,
            outputs_match=len(fps) == 1, distinct_output_fingerprints=len(fps),
            same_input_snapshot=len(snaps) == 1,
            parallel_overlapping_pairs=[list(p) for p in pairs],
            method="Isolated SQLite copy; full rerun policy; 1 warm-up pair excluded; alternating order; medians over repetitions.")
        status = "COMPLETED" if not summary["failed"] else "FAILED"
        rid = _persist("cr_comparison", hadm, dict(repetitions=reps, isolated=True), summary, status)
        _finish(token, rid)
    except Cancelled:
        rid = _persist("cr_comparison", hadm, dict(repetitions=reps, isolated=True),
                       dict(runs=runs, repetitions=reps, isolated=True, cancelled=True, stats=None,
                            successful=None, failed=None, wall_reduction_pct=None, outputs_match=None),
                       "CANCELLED", error="cancelled by the user; completed runs kept, no statistics computed")
        _finish(token, rid, status="cancelled", error="Cancelled. Completed runs were kept in the history; no comparison was computed.")
    except HTTPException as e:
        _finish(token, status="rejected", error=e.detail, http_status=e.status_code)
    except Exception as e:
        _finish(token, status="error", error=f"{type(e).__name__}: {e}", http_status=500)
    finally:
        if tmp:
            _drop_copy(tmp, eng)


def _w_correction(token, case_id, hadm, corr: CorrectionSpec, actor):
    copies = []
    try:
        _JOBS[token]["status"] = "running"
        results = {}
        for policy in ("full", "targeted"):
            _check_cancel(token)
            tmp, eng, S = _isolated_copy()
            copies.append((tmp, eng))
            db = S()
            case = db.get(ClaimCase, case_id)
            before = dict(status=case.status, fingerprint=final_fingerprint(db, case.id))
            try:
                rec = _one_run(db, case, "parallel", policy, f"{policy} re-run after correction", token, actor, correction=corr)
            except (ConflictError, CorrectionError) as e:
                db.rollback()
                db.close()
                raise HTTPException(409 if isinstance(e, ConflictError) else 422, str(e))
            rec["before"] = before
            results[policy] = rec
            db.close()
        f, t = results["full"], results["targeted"]
        summary = dict(
            runs=[f, t], isolated=True, correction=dict(op=corr.op, payload=corr.payload, reason=corr.reason),
            outputs_match=f["fingerprint"]["hash"] == t["fingerprint"]["hash"],
            mismatched_stages=[s for s in dag.STAGE_ORDER if f["fingerprint"]["per_stage"].get(s) != t["fingerprint"]["per_stage"].get(s)],
            executions_avoided=f["metrics"]["stages_executed"] - t["metrics"]["stages_executed"],
            method="Two isolated SQLite copies of the same database state; the same correction applied in each; "
                   "one re-run with the full policy, one with the targeted policy.")
        status = "COMPLETED" if all(r["metrics"]["status"] == "SUCCEEDED" for r in (f, t)) else "FAILED"
        rid = _persist("cr_correction", hadm, dict(isolated=True, op=corr.op), summary, status)
        _finish(token, rid)
    except Cancelled:
        _finish(token, status="cancelled", error="Cancelled before both arms completed; nothing was compared or recorded.")
    except HTTPException as e:
        _finish(token, status="rejected", error=e.detail, http_status=e.status_code)
    except Exception as e:
        _finish(token, status="error", error=f"{type(e).__name__}: {e}", http_status=500)
    finally:
        for tmp, eng in copies:
            _drop_copy(tmp, eng)


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.get("/stages")
def stages():
    reg = _stage_registry()
    for s in reg:
        s["purpose"] = PURPOSE[s["id"]]
        s["downstream"] = sorted(t for t, spec in dag.STAGES.items() if s["id"] in spec["deps"])
    return dict(stages=reg, waves=dag.waves(), disclosure=DISCLOSURE,
                isolated_comparisons_available=engine.url.get_backend_name() == "sqlite")


class RunIn(BaseModel):
    hadm_id: str
    mode: Literal["sequential", "parallel"]


class CompareIn(BaseModel):
    hadm_id: str
    repetitions: int = Field(5, ge=1, le=10)


class CorrectionCompareIn(BaseModel):
    hadm_id: str
    correction: CorrectionSpec


def _start(target, args):
    threading.Thread(target=target, args=args, daemon=True, name=f"control-room-{args[0][:8]}").start()


@router.post("/runs", status_code=202)
def start_run(body: RunIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    hadm = _parse_hadm(body.hadm_id)
    case = _case_for(db, hadm)
    token = _job("cr_run", hadm, case.id)
    _start(_w_single, (token, case.id, hadm, body.mode, actor))
    return dict(token=token)


@router.post("/comparisons", status_code=202)
def start_comparison(body: CompareIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    hadm = _parse_hadm(body.hadm_id)
    case = _case_for(db, hadm)
    if engine.url.get_backend_name() != "sqlite":
        raise HTTPException(409, "Isolated comparisons need the SQLite development database.")
    token = _job("cr_comparison", hadm, case.id)
    _start(_w_compare, (token, case.id, hadm, body.repetitions, actor))
    return dict(token=token)


@router.post("/correction-comparisons", status_code=202)
def start_correction(body: CorrectionCompareIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    hadm = _parse_hadm(body.hadm_id)
    case = _case_for(db, hadm)
    if engine.url.get_backend_name() != "sqlite":
        raise HTTPException(409, "Isolated comparisons need the SQLite development database.")
    token = _job("cr_correction", hadm, case.id)
    _start(_w_correction, (token, case.id, hadm, body.correction, actor))
    return dict(token=token)


@router.post("/jobs/{token}/cancel")
def cancel(token: str, actor: str = Depends(require_writer)):
    j = _JOBS.get(token)
    if j is None:
        raise HTTPException(404, "Unknown or expired job.")
    if j["status"] not in ("queued", "running"):
        return dict(status=j["status"], note="already finished")
    j["cancel"] = True
    return dict(status="cancelling", note="The run in progress completes; no further runs start.")


@router.get("/jobs/{token}")
def job(token: str, after: int = 0):
    j = _JOBS.get(token)
    if j is None:
        raise HTTPException(404, "Unknown or expired job (only the last 30 are kept in memory; finished results are in the run history).")
    with _JOBS_LOCK:
        ev = j["events"][max(0, after):]
        return dict(kind=j["kind"], status=j["status"], current=j["current"], record_id=j["record_id"], error=j["error"],
                    http_status=j["http_status"], events=ev, next_after=after + len(ev))


def _record_row(r):
    s = r.summary or {}
    runs = s.get("runs") or []
    first = runs[0]["metrics"] if runs else {}
    return dict(id=r.id, kind=r.config.get("kind"), hadm_id=r.config.get("hadm_id"), mode=r.config.get("mode"),
                repetitions=r.config.get("repetitions"), status=r.status, isolated=r.config.get("isolated"),
                started_at=r.started_at and r.started_at.isoformat(), wall_ms=first.get("wall_ms"),
                stages_executed=first.get("stages_executed"), outputs_match=s.get("outputs_match"),
                wall_reduction_pct=s.get("wall_reduction_pct"), n_runs=len(runs))


def _records(db):
    rows = db.query(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(500).all()
    return [r for r in rows if isinstance(r.config, dict) and r.config.get("kind") in KINDS]


@router.get("/history")
def history(db: Session = Depends(get_db)):
    return [_record_row(r) for r in _records(db)[:100]]


@router.get("/history/{rid}")
def record(rid: int, db: Session = Depends(get_db)):
    r = db.get(ExperimentRun, rid)
    if r is None or not isinstance(r.config, dict) or r.config.get("kind") not in KINDS:
        raise HTTPException(404, "control-room record not found")
    return dict(_record_row(r), config=r.config, summary=r.summary, error=r.error)


@router.get("/history/{rid}/export")
def export(rid: int, format: Literal["csv", "json"] = "csv", db: Session = Depends(get_db)):
    rec = record(rid, db)
    if format == "json":
        import json
        return Response(json.dumps(rec, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="clearclaim-control-room-{rid}.json"'})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["record_id", "run_label", "mode", "rerun_policy", "warmup", "stage", "status", "start_ms", "end_ms", "duration_ms",
                "run_wall_ms", "run_end_to_end_ms", "run_stage_sum_ms", "run_critical_path_ms", "run_max_concurrency", "output_fingerprint"])
    for run in rec["summary"].get("runs", []):
        m = run["metrics"]
        for e in run["events"]:
            if e["type"] == "stage_finished":
                w.writerow([rid, run["label"], run["mode"], run["rerun_policy"], run.get("warmup", False), e["stage"], e["status"],
                            e.get("start_ms"), e.get("end_ms"), e.get("duration_ms"), m["wall_ms"], m["end_to_end_ms"],
                            m["stage_sum_ms"], m["critical_path_ms"], m["max_concurrency"], run["fingerprint"]["hash"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="clearclaim-control-room-{rid}.csv"'})
