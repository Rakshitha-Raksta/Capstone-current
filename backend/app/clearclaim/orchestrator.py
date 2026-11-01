"""
Dependency-aware orchestrator with targeted re-execution.

Scheduling: topological waves from dag.waves(). In "parallel" mode the stages
of a wave (billing || eligibility) run concurrently in a thread pool; in
"sequential" mode one at a time. Both modes call the same stage functions.

Rebuild policy (cf. Mokhov et al., "Build systems a la carte", the
scheduler/rebuilder split):
  targeted: a stage is (re)considered if it reads a changed input domain or a
            dependency produced a changed output in this run. It is then
            REUSED if its input hash (declared domains + declared upstream
            keys + reference-context fingerprint) equals the hash of its
            current execution; otherwise it executes. Unaffected stages keep
            their current results untouched.
  full:     every stage executes on every run (baseline for experiments).

Stage functions are pure: (inputs, upstream, ctx) -> {data, findings}. All
database writes happen here, in the calling thread, inside one transaction
per run. Failures are retried up to max_stage_retries; a stage that still
fails marks its dependents SKIPPED_DEPENDENCY_FAILED and the case ERROR,
with the previous good results left current.
"""
import copy
import datetime as dt
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Optional

from app.clearclaim import dag
from app.clearclaim.context import get_context
from app.clearclaim.findings import stage_status
from app.clearclaim.models import (
    AuditEvent, ClaimCase, ClaimPacket, Finding, PipelineRun, ReviewTask, StageExecution,
)
from app.clearclaim.snapshot import build_snapshot
from app.clearclaim.stages import STAGE_FUNCTIONS

log = logging.getLogger("clearclaim.orchestrator")

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cc-stage")


def canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


_audit_lock = threading.Lock()


def audit_content_hash(prev_hash, e) -> str:
    body = dict(ts=e.ts.isoformat() if e.ts else None, actor=e.actor, case_id=e.case_id, event_type=e.event_type,
                entity_type=e.entity_type, entity_id=e.entity_id, summary=e.summary, details=e.details)
    return hashlib.sha256(((prev_hash or "") + json.dumps(body, sort_keys=True, default=str,
                                                          separators=(",", ":"))).encode()).hexdigest()


def audit(db, event_type, summary, actor="system", case_id=None, entity_type=None, entity_id=None, **details):
    """Append one hash-chained audit event (flushed immediately so the chain order equals id order)."""
    with _audit_lock:
        ev = AuditEvent(actor=actor, case_id=case_id, event_type=event_type, entity_type=entity_type,
                        entity_id=entity_id, summary=summary, details=details or None, ts=dt.datetime.utcnow())
        db.add(ev)
        db.flush()
        prev = (db.query(AuditEvent.hash).filter(AuditEvent.id < ev.id, AuditEvent.hash.isnot(None))
                .order_by(AuditEvent.id.desc()).first())
        ev.prev_hash = prev[0] if prev else None
        ev.hash = audit_content_hash(ev.prev_hash, ev)
        return ev


def verify_audit_chain(db) -> dict:
    prev, n, legacy = None, 0, 0
    for e in db.query(AuditEvent).order_by(AuditEvent.id).yield_per(500):
        if e.hash is None:
            legacy += 1           # written before the chain existed
            continue
        if e.prev_hash != prev or audit_content_hash(prev, e) != e.hash:
            return dict(ok=False, checked=n, unchained_legacy_events=legacy, first_broken_event_id=e.id)
        prev, n = e.hash, n + 1
    return dict(ok=True, checked=n, unchained_legacy_events=legacy, first_broken_event_id=None, head=prev)


# identifiers never included in event summaries (the encounter ID itself is what the user typed)
_SUMMARY_HIDDEN = {"subject_id", "packet", "packet_sha256"}


def safe_output_summary(data: dict) -> dict:
    """Short, non-identifying view of a stage's output for observers: scalars as-is, collections as sizes."""
    out = {}
    for k, v in (data or {}).items():
        if k in _SUMMARY_HIDDEN:
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v if len(v) <= 80 else v[:77] + "..."
        elif isinstance(v, list):
            out[k] = f"list of {len(v)}"
        elif isinstance(v, dict):
            out[k] = f"{len(v)} fields"
    return out


def _select_upstream(stage, outputs):
    """Build the upstream view a stage is allowed to see, from its declared `consumes`."""
    up = {}
    for key, spec in dag.STAGES[stage]["consumes"].items():
        src = key.split("#")[0]
        out = outputs.get(src)
        if out is None:
            continue
        if spec == "findings":
            up[key] = out["findings"]
        elif spec == "*":
            up[src] = out["data"]
        else:
            up.setdefault(src, {}).update({k: out["data"].get(k) for k in spec})
    return up


def _stage_inputs(stage, snapshot, outputs, ctx_fp):
    spec = dag.STAGES[stage]
    inputs = {d: snapshot[d] for d in spec["domains"]}
    upstream = _select_upstream(stage, outputs)
    h = canonical_hash(dict(stage=stage, inputs=inputs, upstream=upstream, ctx=ctx_fp))
    return inputs, upstream, h


def _call_stage(stage, inputs, upstream, ctx, max_retries):
    """Runs in a worker thread. Pure: no DB access."""
    attempts, last_err = 0, None
    t0 = time.perf_counter()        # monotonic, high resolution; t_start/t_end let observers place the
    while attempts <= max_retries:  # stage on the run's timeline and measure real overlap between stages
        attempts += 1
        try:
            # deep copies isolate concurrent stages from each other
            res = STAGE_FUNCTIONS[stage](copy.deepcopy(inputs), copy.deepcopy(upstream), ctx)
            t1 = time.perf_counter()
            return dict(ok=True, result=res, attempts=attempts, ms=(t1 - t0) * 1000, t_start=t0, t_end=t1)
        except Exception as e:  # bounded retry; the error is recorded, never swallowed silently
            last_err = f"{type(e).__name__}: {e}"
            log.warning("stage %s attempt %d failed: %s", stage, attempts, last_err)
    t1 = time.perf_counter()
    return dict(ok=False, error=last_err, attempts=attempts, ms=(t1 - t0) * 1000, t_start=t0, t_end=t1)


class Orchestrator:
    def __init__(self, db, execution_mode: Optional[str] = None, rerun_policy: Optional[str] = None,
                 actor: str = "system", ctx=None, on_event: Optional[Callable[[dict], None]] = None):
        self.db = db
        # Optional observer (used by the Execution Studio). It receives what the orchestrator actually
        # decides and does, as it happens; it can never change the run, and its errors are ignored.
        self.on_event = on_event
        self._t0 = None
        self.ctx = ctx or get_context(db)
        self.execution_mode = execution_mode or self.ctx.settings["execution_mode"]
        self.rerun_policy = rerun_policy or self.ctx.settings["rerun_policy"]
        self.actor = actor
        assert self.execution_mode in ("parallel", "sequential")
        assert self.rerun_policy in ("targeted", "full")

    # ------------------------------------------------------------------
    def _offset(self, t):
        """perf_counter value -> ms since this run started."""
        return round((t - self._t0) * 1000, 4) if t is not None and self._t0 is not None else None

    def _emit(self, kind, **data):
        if self.on_event is None:
            return
        try:
            t = round((time.perf_counter() - self._t0) * 1000, 3) if self._t0 is not None else 0.0
            self.on_event(dict(type=kind, t_ms=t, **data))
        except Exception:  # an observer must never break a run
            log.warning("execution observer failed on %s", kind, exc_info=True)

    def current_executions(self, case_id):
        rows = (self.db.query(StageExecution)
                .filter_by(case_id=case_id, is_current=True)
                .all())
        return {r.stage: r for r in rows}

    def process(self, case: ClaimCase, trigger: str = "INITIAL", changed_domains: Iterable[str] = ()) -> PipelineRun:
        db, ctx = self.db, self.ctx
        changed_domains = sorted(set(changed_domains))
        current = self.current_executions(case.id)
        first_run = not current
        force = self.rerun_policy == "full" or first_run
        planned = dag.STAGE_ORDER if force else dag.invalidated_by(changed_domains)

        run = PipelineRun(case_id=case.id, trigger=trigger, execution_mode=self.execution_mode,
                          rerun_policy=self.rerun_policy, changed_domains=changed_domains,
                          invalidated_stages=planned, status="RUNNING")
        db.add(run)
        db.flush()
        case.status = "PROCESSING"
        audit(db, "PIPELINE_STARTED", f"{trigger} run ({self.execution_mode}, {self.rerun_policy})", self.actor,
              case.id, "pipeline_run", run.id, changed_domains=changed_domains, planned_stages=planned)

        t_run = time.perf_counter()
        self._t0 = t_run
        self._emit("run_started", run_id=run.id, case_id=case.id, trigger=trigger, execution_mode=self.execution_mode,
                   rerun_policy=self.rerun_policy, changed_domains=changed_domains, planned_stages=list(planned),
                   force=force, first_run=first_run)
        snapshot = build_snapshot(db, case)
        ctx_fp = ctx.fingerprint
        outputs = {s: dict(data=e.output["data"], findings=e.output["findings"])
                   for s, e in current.items() if e.output}
        changed_outputs, failed = set(), set()
        executed = reused = 0
        planned_set = set(planned)
        max_retries = int(ctx.settings["max_stage_retries"])

        for wave_no, wave in enumerate(dag.waves()):
            to_eval, why = [], {}
            for s in wave:
                deps = dag.STAGES[s]["deps"]
                if any(d in failed for d in deps):
                    failed.add(s)
                    db.add(StageExecution(run_id=run.id, case_id=case.id, stage=s,
                                          status="SKIPPED_DEPENDENCY_FAILED", is_current=False, duration_ms=0))
                    self._emit("stage_skipped", stage=s, wave=wave_no,
                               reason=f"dependency failed: {sorted(d for d in deps if d in failed)}")
                    continue
                if force or s in planned_set or any(d in changed_outputs for d in deps):
                    to_eval.append(s)
                    self._emit("stage_ready", stage=s, wave=wave_no, deps=list(deps))
                    if first_run:
                        why[s] = "first run of this case: every stage executes"
                    elif self.rerun_policy == "full":
                        why[s] = "full rerun policy: every stage executes"
                    elif s in planned_set:
                        read = sorted(set(dag.STAGES[s]["domains"]) & set(changed_domains))
                        why[s] = (f"reads changed input: {', '.join(read)}" if read
                                  else "depends on a stage that reads a changed input")
                    else:
                        why[s] = f"upstream output changed: {', '.join(sorted(d for d in deps if d in changed_outputs))}"
                else:
                    self._emit("stage_not_rerun", stage=s, wave=wave_no,
                               reason="reads no changed input and no upstream output changed; previous result kept")
            jobs = {}
            for s in to_eval:
                inputs, upstream, h = _stage_inputs(s, snapshot, outputs, ctx_fp)
                prev = current.get(s)
                if not force and prev is not None and prev.input_hash == h and prev.status in ("SUCCEEDED", "REUSED"):
                    reused += 1
                    db.add(StageExecution(run_id=run.id, case_id=case.id, stage=s, status="REUSED", input_hash=h,
                                          output_hash=prev.output_hash, stage_status=prev.stage_status,
                                          is_current=False, reused_from_id=prev.id, duration_ms=0))
                    self._emit("stage_reused", stage=s, wave=wave_no, input_hash=h, reused_from_execution=prev.id,
                               stage_status=prev.stage_status, considered_because=why.get(s),
                               reason="input hash identical to the current execution, so its output is reused")
                    continue
                jobs[s] = (inputs, upstream, h)
                self._emit("stage_started", stage=s, wave=wave_no, input_hash=h, reason=why.get(s),
                           previous_input_hash=prev.input_hash if prev is not None else None)
            if not jobs:
                continue
            if self.execution_mode == "parallel" and len(jobs) > 1:
                futs = {s: _POOL.submit(_call_stage, s, j[0], j[1], ctx, max_retries) for s, j in jobs.items()}
                results = {s: f.result() for s, f in futs.items()}
            else:
                results = {s: _call_stage(s, j[0], j[1], ctx, max_retries) for s, j in jobs.items()}

            for s in wave:  # persist in deterministic order
                if s not in results:
                    continue
                r, h = results[s], jobs[s][2]
                prev = current.get(s)
                if not r["ok"]:
                    failed.add(s)
                    db.add(StageExecution(run_id=run.id, case_id=case.id, stage=s, status="FAILED", attempts=r["attempts"],
                                          input_hash=h, duration_ms=r["ms"], error=r["error"], is_current=False))
                    audit(db, "STAGE_FAILED", f"{s} failed after {r['attempts']} attempt(s): {r['error']}",
                          "system", case.id, "stage", None, stage=s)
                    self._emit("stage_finished", stage=s, wave=wave_no, status="FAILED", duration_ms=round(r["ms"], 4),
                               attempts=r["attempts"], error=r["error"],
                               start_ms=self._offset(r.get("t_start")), end_ms=self._offset(r.get("t_end")))
                    continue
                res = r["result"]
                out = dict(data=res["data"], findings=res["findings"])
                out_hash = canonical_hash(out)
                se = StageExecution(run_id=run.id, case_id=case.id, stage=s, status="SUCCEEDED", attempts=r["attempts"],
                                    input_hash=h, output_hash=out_hash, output=out, stage_status=stage_status(res["findings"]),
                                    duration_ms=r["ms"], is_current=True)
                db.add(se)
                db.flush()
                if prev is not None:
                    prev.is_current = False
                    db.query(Finding).filter_by(case_id=case.id, stage=s, is_current=True).update({"is_current": False})
                for fd in res["findings"]:
                    db.add(Finding(case_id=case.id, stage_execution_id=se.id, stage=s, code=fd["code"],
                                   severity=fd["severity"], title=fd["title"], message=fd["message"],
                                   rule_id=fd["rule_id"], source_document=fd["source_document"],
                                   source_page=fd["source_page"], expected=fd["expected"], actual=fd["actual"],
                                   amount=fd["amount"], evidence=fd["evidence"], is_current=True))
                executed += 1
                if prev is None or prev.output_hash != out_hash:
                    changed_outputs.add(s)
                outputs[s] = out
                current[s] = se
                self._emit("stage_finished", stage=s, wave=wave_no, status="SUCCEEDED", execution_id=se.id,
                           duration_ms=round(r["ms"], 4), attempts=r["attempts"], stage_status=se.stage_status,
                           start_ms=self._offset(r.get("t_start")), end_ms=self._offset(r.get("t_end")),
                           output_hash=out_hash, output_changed=prev is None or prev.output_hash != out_hash,
                           findings=[dict(code=f["code"], severity=f["severity"], title=f["title"], message=f["message"],
                                          expected=f["expected"], actual=f["actual"], rule_id=f["rule_id"],
                                          source_document=f["source_document"], source_page=f["source_page"])
                                     for f in res["findings"]],
                           output_summary=safe_output_summary(res["data"]))

        run.wall_ms = (time.perf_counter() - t_run) * 1000
        run.stages_executed, run.stages_reused = executed, reused
        run.finished_at = dt.datetime.utcnow()
        if failed:
            run.status, run.outcome_status = "FAILED", "ERROR"
            run.error = f"failed stages: {sorted(failed)}"
            case.status = "ERROR"
        else:
            run.status = "SUCCEEDED"
            self._apply_outcome(case, run, outputs, changed_outputs, trigger)
        case.last_run_id = run.id
        audit(db, "PIPELINE_FINISHED",
              f"{run.status}: {executed} stage(s) executed, {reused} reused, outcome {run.outcome_status}",
              self.actor, case.id, "pipeline_run", run.id, wall_ms=round(run.wall_ms, 2),
              changed_outputs=sorted(changed_outputs))
        db.commit()
        self._emit("run_finished", run_id=run.id, status=run.status, outcome_status=run.outcome_status,
                   stages_executed=executed, stages_reused=reused, wall_ms=round(run.wall_ms, 4), error=run.error,
                   commit_ms=self._offset(time.perf_counter()))
        return run

    # ------------------------------------------------------------------
    def _apply_outcome(self, case, run, outputs, changed_outputs, trigger):
        db = self.db
        rb = outputs["root_blocker"]["data"]
        rr = outputs["review_routing"]["data"]
        cg = outputs["claim_generation"]["data"]
        adj = outputs["adjudication"]["data"]

        # role annotation on current findings (ROOT / CONSEQUENCE / ADVISORY)
        roles = {}
        for group in ("roots", "consequences", "advisories"):
            for x in rb[group]:
                roles[(x["stage"], x["code"], json.dumps(x.get("evidence"), sort_keys=True, default=str))] = x
        for f in db.query(Finding).filter_by(case_id=case.id, is_current=True).all():
            x = roles.get((f.stage, f.code, json.dumps(f.evidence, sort_keys=True, default=str)))
            if x:
                f.role, f.caused_by = x["role"], x.get("caused_by")

        self._sync_tasks(case, run, rr["tasks"], trigger)

        if "claim_generation" in changed_outputs or db.query(ClaimPacket).filter_by(case_id=case.id).count() == 0:
            db.query(ClaimPacket).filter_by(case_id=case.id, is_current=True).update({"is_current": False})
            db.add(ClaimPacket(case_id=case.id, run_id=run.id, status=cg["status"],
                               packet=dict(fhir_claim=cg["packet"], summary=cg["summary"]), is_current=True))

        prev_status = case.status
        case.status = cg["status"]
        run.outcome_status = cg["status"]
        primary = rb["primary_root"]
        case.root_blocker_code = primary["code"] if primary else None
        case.root_blocker_stage = primary["stage"] if primary else None
        case.amount_billed = adj["gross"]
        case.amount_at_risk = rb["amount_at_risk"]
        case.estimated_eligible = adj["estimated_eligible"]
        case.open_tasks = rr["open_count"]
        n_block = sum(1 for r in rb["roots"] if r["severity"] == "BLOCKING")
        case.priority = round(100 * n_block + 10 * rr["open_count"] + min(50.0, (rb["amount_at_risk"] or 0) / 10000), 2)
        if prev_status != case.status:
            audit(db, "CASE_STATUS_CHANGED", f"{prev_status} -> {case.status}", self.actor, case.id, "case", case.id)

    def _sync_tasks(self, case, run, tasks, trigger):
        db = self.db
        existing = {t.task_key: t for t in db.query(ReviewTask).filter_by(case_id=case.id).all()
                    if t.status in ("OPEN", "ACCEPTED_WITH_JUSTIFICATION", "CONFIRMED_BLOCKED")}
        seen = set()
        for t in tasks:
            seen.add(t["task_key"])
            row = existing.get(t["task_key"])
            if row is None:
                row = ReviewTask(case_id=case.id, task_key=t["task_key"], code=t["code"], severity=t["severity"],
                                 owner_stage=t["owner_stage"], input_domain=t["input_domain"], title=t["title"],
                                 reason=t["reason"], evidence=t["evidence"], recommended_action=t["recommended_action"],
                                 required_reruns=t["required_reruns"], allowed_decisions=t["allowed_decisions"],
                                 status="OPEN" if t["state"] == "OPEN" else t["state"], created_run_id=run.id)
                db.add(row)
                db.flush()
                audit(db, "REVIEW_TASK_CREATED", f"{t['code']}: {t['title']}", "system", case.id, "review_task", row.id,
                      severity=t["severity"], owner_stage=t["owner_stage"])
            else:
                row.reason, row.evidence = t["reason"], t["evidence"]
                if t["state"] != "OPEN" and row.status == "OPEN":
                    row.status = t["state"]
        for key, row in existing.items():
            if key not in seen and row.status == "OPEN":
                row.status = "RESOLVED_BY_CORRECTION" if trigger == "CORRECTION" else "SUPERSEDED"
                row.resolved_at = dt.datetime.utcnow()
                row.resolved_by = self.actor
                audit(db, "REVIEW_TASK_RESOLVED", f"{row.code} no longer raised after {trigger.lower()} run",
                      self.actor, case.id, "review_task", row.id, status=row.status)
