"""
Experiment endpoints. Experiments run in a background thread on ISOLATED
SQLite databases under results/experiments/<id>/ and never touch live cases.
Only aggregate metrics are returned; per-case ground truth is not exposed.
"""
import datetime as dt
import json
import os
import threading
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.clearclaim.api import require_writer
from app.clearclaim.config import REPO_ROOT, settings
from app.clearclaim.models import ExperimentRun
from app.clearclaim.orchestrator import audit
from app.database.db import SessionLocal, get_db

router = APIRouter(prefix="/api/v1", tags=["experiments"])
RESULTS_ROOT = os.path.join(REPO_ROOT, "results", "experiments")
_running = threading.Lock()


class ExperimentIn(BaseModel):
    name: str = Field("adhoc", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    seeds: List[int] = Field(default_factory=lambda: [1], min_length=1, max_length=5)
    n_encounters: int = Field(60, ge=10, le=400)
    error_rate: float = Field(0.35, ge=0.0, le=0.9)
    arms: Optional[List[str]] = None

    @field_validator("arms")
    @classmethod
    def known_arms(cls, v):
        from app.experiments.harness import ARMS
        if v is not None and (not v or set(v) - set(ARMS)):
            raise ValueError(f"arms must be a non-empty subset of {sorted(ARMS)}")
        return v


def _worker(exp_id, cfg):
    from app.experiments.harness import run_experiment
    db = SessionLocal()
    try:
        out = os.path.join(RESULTS_ROOT, f"run_{exp_id}")
        res = run_experiment(out, seeds=cfg["seeds"], n_encounters=cfg["n_encounters"],
                             error_rate=cfg["error_rate"], arms=cfg.get("arms"), log=lambda *_: None)
        e = db.get(ExperimentRun, exp_id)
        e.status, e.finished_at = "COMPLETED", dt.datetime.utcnow()
        e.summary = dict(aggregate=res["aggregate"], elapsed_s=res["elapsed_s"],
                         per_seed={str(k): dict(scenario_classes=v["scenario_classes"], A_manual_model=v["A_manual_model"],
                                                arms={a: {kk: vv for kk, vv in m.items() if kk != "detection_by_type"}
                                                      for a, m in v["arms"].items()},
                                                detection_by_type={a: m["detection_by_type"] for a, m in v["arms"].items()})
                                   for k, v in res["seeds"].items()},
                         config=res["config"])
        e.raw_results_path = os.path.relpath(out, REPO_ROOT).replace(os.sep, "/")
        audit(db, "EXPERIMENT_COMPLETED", f"experiment {exp_id} completed in {res['elapsed_s']}s", "system",
              entity_type="experiment", entity_id=exp_id)
        db.commit()
    except Exception as ex:  # recorded, not swallowed
        db.rollback()
        e = db.get(ExperimentRun, exp_id)
        e.status, e.error, e.finished_at = "FAILED", f"{type(ex).__name__}: {ex}", dt.datetime.utcnow()
        db.commit()
    finally:
        db.close()
        _running.release()


@router.get("/experiments")
def list_experiments(db: Session = Depends(get_db)):
    rows = db.query(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(500).all()
    # Agent Control Room records share this table (config.kind cr_*); they have their own history view
    rows = [r for r in rows if not (isinstance(r.config, dict) and str(r.config.get("kind", "")).startswith("cr_"))][:50]
    return [dict(id=r.id, name=r.name, status=r.status, config=r.config, started_at=r.started_at and r.started_at.isoformat(),
                 finished_at=r.finished_at and r.finished_at.isoformat(), error=r.error,
                 raw_results_path=r.raw_results_path, has_summary=r.summary is not None) for r in rows]


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int, db: Session = Depends(get_db)):
    r = db.get(ExperimentRun, exp_id)
    if r is None:
        raise HTTPException(404, "experiment not found")
    return dict(id=r.id, name=r.name, status=r.status, config=r.config, summary=r.summary, error=r.error,
                started_at=r.started_at and r.started_at.isoformat(),
                finished_at=r.finished_at and r.finished_at.isoformat(), raw_results_path=r.raw_results_path)


@router.post("/experiments", status_code=202)
def start_experiment(body: ExperimentIn, db: Session = Depends(get_db), actor: str = Depends(require_writer)):
    if not settings.allow_experiments:
        raise HTTPException(403, "experiments are disabled in this deployment (ALLOW_EXPERIMENTS=false)")
    if not _running.acquire(blocking=False):
        raise HTTPException(409, "an experiment is already running")
    cfg = body.model_dump()
    e = ExperimentRun(name=body.name, config=cfg, status="RUNNING")
    db.add(e)
    audit(db, "EXPERIMENT_STARTED", f"experiment '{body.name}' started", actor, entity_type="experiment", config=cfg)
    db.commit()
    threading.Thread(target=_worker, args=(e.id, cfg), daemon=True, name=f"experiment-{e.id}").start()
    return dict(id=e.id, status=e.status)


@router.post("/experiments/import-summary", status_code=201)
def import_summary(path: str = "results/experiments/main/summary.json", db: Session = Depends(get_db),
                   actor: str = Depends(require_writer)):
    """Register a CLI-produced summary (scripts/run_experiments.py) so it shows in the UI."""
    full = os.path.abspath(os.path.join(REPO_ROOT, path))
    root = os.path.abspath(RESULTS_ROOT)
    try:
        inside = os.path.commonpath([full, root]) == root
    except ValueError:  # different drives on Windows
        inside = False
    if not inside or not full.endswith("summary.json") or not os.path.isfile(full):
        raise HTTPException(404, "summary file not found under results/experiments/")
    res = json.load(open(full, encoding="utf-8"))
    e = ExperimentRun(name=os.path.basename(os.path.dirname(full)), config=res["config"], status="COMPLETED",
                      finished_at=dt.datetime.utcnow(), raw_results_path=os.path.relpath(os.path.dirname(full), REPO_ROOT).replace(os.sep, "/"),
                      summary=dict(aggregate=res["aggregate"], elapsed_s=res["elapsed_s"], config=res["config"],
                                   per_seed={str(k): dict(scenario_classes=v["scenario_classes"],
                                                          A_manual_model=v["A_manual_model"],
                                                          arms={a: {kk: vv for kk, vv in m.items() if kk != "detection_by_type"}
                                                                for a, m in v["arms"].items()},
                                                          detection_by_type={a: m["detection_by_type"] for a, m in v["arms"].items()})
                                             for k, v in res["seeds"].items()}))
    db.add(e)
    db.commit()
    return dict(id=e.id)
