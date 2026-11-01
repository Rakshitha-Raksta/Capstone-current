"""
Installs the ClearClaim layer on the existing FastAPI app:
  * /api/v1 routers (cases, review, workflow, data, settings, experiments, explain)
  * CORS restricted to configured origins
  * request body size limit
  * structured JSON logging with request ids
  * a generic 500 handler that never leaks stack traces to clients
  * optional automatic Alembic migration at startup (AUTO_MIGRATE, default on)
"""
import json
import logging
import os
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.clearclaim.config import settings
from app.database.db import get_db

log = logging.getLogger("clearclaim")


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        d = dict(ts=self.formatTime(record), level=record.levelname, logger=record.name, msg=record.getMessage())
        for k in ("request_id", "path", "method", "status", "ms"):
            if hasattr(record, k):
                d[k] = getattr(record, k)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d)


def _setup_logging():
    root = logging.getLogger("clearclaim")
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(_JsonFormatter())
        root.addHandler(h)
        root.setLevel(settings.log_level)
        root.propagate = False


def migrate():
    from alembic import command
    from alembic.config import Config
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    command.upgrade(cfg, "head")


explain_router = __import__("fastapi").APIRouter(prefix="/api/v1", tags=["policy-explainer"])


class QuestionIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


@explain_router.post("/cases/{case_id}/explain")
def explain(case_id: int, body: QuestionIn, db: Session = Depends(get_db)):
    """Read-only policy explanation via the existing RAG layer. Cannot change any result."""
    from app.clearclaim.models import ClaimCase, MemberPolicy
    from app.rag.service import answer_question
    c = db.get(ClaimCase, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    mp = db.query(MemberPolicy).filter_by(subject_id=c.encounter.subject_id).first()
    if mp is None:
        raise HTTPException(404, "no policy linked to this case")
    try:
        res = answer_question(body.question, mp.policy_version_id)
    except Exception as e:  # e.g. RAG index not built
        raise HTTPException(503, f"policy explainer unavailable: {type(e).__name__}. Run scripts/build_rag_index.py")
    return dict(policy_version_id=mp.policy_version_id, question=body.question,
                notice=("Explanation retrieved from policy text. It is NOT the authoritative result; "
                        "deterministic rule results on the case are authoritative."), **res)


def install(app: FastAPI):
    _setup_logging()
    from app.clearclaim.api import router as cc_router
    from app.clearclaim.experiments_api import router as exp_router
    from app.clearclaim.studio import router as studio_router
    from app.clearclaim.control_room import router as control_room_router
    from app.clearclaim.workspace import router as workspace_router
    from app.clearclaim.intel import router as intel_router

    app.title = "ClearClaim API"
    app.version = settings.app_version
    app.description = (
        "Evidence-preserving, dependency-aware claim preparation. **/api/v1** is the ClearClaim API; the "
        "root-level routes (/policies, /claims, /rules, /documents, /health) are the original 59-rule validator, kept "
        "unchanged.\n\n"
        "* Statuses (PREPARATION_COMPLETE / NEEDS_REVIEW / BLOCKED) are internal claim-preparation states, **not "
        "insurer decisions**. Financial figures are rule-based estimates.\n"
        "* Demo data is **synthetic**; in MIMIC mode clinical encounters are real de-identified MIMIC-IV demo records "
        "and everything financial or administrative is synthetic.\n"
        "* Mutating endpoints require `Authorization: Bearer <CLEARCLAIM_WRITE_TOKEN>` when a token is configured "
        "(in APP_ENV=production they are disabled without one).\n"
        "* Health: `GET /api/v1/live` (process), `GET /api/v1/health` (database), `GET /api/v1/audit-events/verify` "
        "(audit hash chain)."
    )
    app.openapi_tags = [
        dict(name="clearclaim", description="Cases, pipeline runs, corrections, review queue, workflow, data and settings"),
        dict(name="experiments", description="Controlled experiments on isolated databases (aggregate results only)"),
        dict(name="policy-explainer", description="Read-only retrieval over policy text; never changes a result"),
    ]
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def limits_and_logging(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > settings.max_request_bytes:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        if (request.method in ("POST", "PUT", "PATCH") and not cl
                and "chunked" in request.headers.get("transfer-encoding", "").lower()):
            # bodies of unknown length could bypass the size limit
            return JSONResponse({"detail": "Content-Length required"}, status_code=411)
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra=dict(request_id=rid, path=request.url.path, method=request.method))
            return JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        resp.headers["x-request-id"] = rid
        if request.url.path.startswith("/api/v1"):
            log.info("request", extra=dict(request_id=rid, path=request.url.path, method=request.method,
                                           status=resp.status_code, ms=ms))
        return resp

    app.include_router(cc_router)
    app.include_router(exp_router)
    app.include_router(studio_router)
    app.include_router(control_room_router)
    app.include_router(workspace_router)
    app.include_router(intel_router)
    app.include_router(explain_router)

    @app.on_event("startup")
    def _startup():
        if os.environ.get("AUTO_MIGRATE", "true").lower() in ("1", "true", "yes"):
            try:
                migrate()
            except Exception:
                log.exception("automatic migration failed; run `alembic upgrade head` manually")
