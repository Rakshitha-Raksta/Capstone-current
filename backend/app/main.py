"""
FastAPI app.

Two route sets are exposed, both backed by the SAME handler functions (no
duplicated logic):
  - /api/*        (original routes, kept so the 18 existing pytest tests
                    that reference them keep passing unmodified)
  - /health, /policies, /claims, ...  (root-level routes matching the
                    exact contract requested: nested source.document /
                    source.page in every validation result)

Product description (per instruction): "Explainable Pre-Submission Health
Insurance Claim Rule Validator" -- NOT an insurer approval/rejection
predictor, NOT connected to NHCX. Checks a claim against documented
policy rules and returns PASS / WARNING / PARTIAL_DEDUCTION / FAIL /
(overall) HUMAN_REVIEW_NEEDED, each individual rule result carrying its
own source document and page citation.
"""
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
import os

from app.database.db import get_db
from app.models.models import (
    PolicyVersion, PolicyRule, Claim, Policy, Document,
    ValidationRun, ValidationResult,
)
from app.schemas.schemas import ClaimCreate, ValidationRunOut
from app.rules.engine import validate_claim
from app.api.qa import router as qa_router

# Maps document_id (as stored in source_documents.csv / Document.document_id)
# to the real extracted-text file this session actually produced. Used only
# by GET /documents/{document_id}/text (screen 8 / rule-evidence viewer) --
# reads the file that already exists on disk, never fabricates text.
_DATA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_DOCUMENT_TEXT_FILES = {
    "STAR_ASSURE_2026_CIS": "raw/policies/star_health/star_assure_2026_CIS_extracted_text.txt",
    "HDFC_OPTIMA_SECURE_2026_POLICY_WORDING": "raw/policies/hdfc_ergo/hdfc_optima_secure_2026_policy_wording_extracted_text.txt",
    "HDFC_OPTIMA_SECURE_2026_CIS": "raw/policies/hdfc_ergo/hdfc_optima_secure_2026_CIS_extracted_text.txt",
    "HDFC_OPTIMA_SECURE_2021_HISTORICAL": "raw/policies/hdfc_ergo/hdfc_optima_secure_2021_HISTORICAL_extracted_text.txt",
    "IRDAI_MASTER_CIRCULAR_2024": "raw/regulatory/irdai/irdai_master_circular_2024_extracted_text.txt",
}

app = FastAPI(
    title="Explainable Pre-Submission Health Insurance Claim Rule Validator",
    description=(
        "Checks a claim against documented, source-cited policy rules and "
        "produces PASS / WARNING / PARTIAL_DEDUCTION / FAIL per rule, with "
        "an overall SUBMISSION_READY / HUMAN_REVIEW_NEEDED / "
        "FIX_BEFORE_SUBMISSION recommendation. This is NOT an insurer "
        "approval/rejection predictor and is NOT connected to NHCX. "
        "Financial output is a rule-based estimate, not a guaranteed "
        "insurer payout. Test claims used against this API are SYNTHETIC "
        "records derived from real policy rules -- never real-world claims."
    ),
)


def _nest_source(results):
    """Convert engine.py's flat source_document/source_page keys into the
    nested {document, page} shape required by the API contract, without
    modifying engine.py itself (no rule-engine bug was found -- this is
    purely a response-shaping concern)."""
    out = []
    for r in results:
        out.append({
            "rule_id": r["rule_id"],
            "category": r["category"],
            "severity": r["severity"],
            "reason": r["reason"],
            "expected": r["expected"],
            "actual": r["actual"],
            "source": {"document": r.get("source_document"), "page": r.get("source_page")},
            "provenance": r.get("provenance"),
        })
    return out


# ---------------------------------------------------------------------------
# Shared handler logic (called by both /api/* and root-level routes)
# ---------------------------------------------------------------------------

def _health():
    return {"status": "ok"}


def _list_policies(db: Session):
    out = []
    for p in db.query(Policy).all():
        out.append({
            "policy_id": p.id,
            "insurer": p.insurer.name,
            "product": p.product_name,
            "versions": [
                {"policy_version_id": v.policy_version_id, "uin": v.uin,
                 "status": v.status, "uin_conflict_flag": v.uin_conflict_flag}
                for v in p.versions
            ],
        })
    return out


def _get_policy(policy_id: int, db: Session):
    p = db.query(Policy).get(policy_id)
    if not p:
        raise HTTPException(404, "policy not found")
    return {
        "policy_id": p.id,
        "insurer": p.insurer.name,
        "product": p.product_name,
        "versions": [
            {"policy_version_id": v.policy_version_id, "uin": v.uin,
             "status": v.status, "uin_conflict_flag": v.uin_conflict_flag}
            for v in p.versions
        ],
    }


def _policy_rules(policy_id: int, db: Session):
    p = db.query(Policy).get(policy_id)
    if not p:
        raise HTTPException(404, "policy not found")
    out = []
    for v in p.versions:
        rules = db.query(PolicyRule).filter_by(policy_version_db_id=v.id).all()
        out.append({
            "policy_version_id": v.policy_version_id,
            "rules": [
                {"candidate_id": r.candidate_id, "rule_type": r.rule_type,
                 "rule_name": r.rule_name, "value": r.value, "unit": r.unit,
                 "condition": r.condition, "exception": r.exception,
                 "source_document": r.source_document, "source_page": r.source_page,
                 "source_text": r.source_text, "provenance": r.provenance,
                 "review_status": r.review_status}
                for r in rules
            ],
        })
    return out


def _policy_sources(policy_id: int, db: Session):
    p = db.query(Policy).get(policy_id)
    if not p:
        raise HTTPException(404, "policy not found")
    doc_ids = set()
    for v in p.versions:
        for r in db.query(PolicyRule).filter_by(policy_version_db_id=v.id).all():
            doc_ids.add(r.source_document)
    docs = db.query(Document).filter(Document.document_id.in_(doc_ids)).all()
    return [
        {"document_id": d.document_id, "source_url": d.source_url,
         "hash_type": d.hash_type, "sha256": d.sha256, "page_count": d.page_count}
        for d in docs
    ]


def _list_claims(db: Session):
    claims = db.query(Claim).order_by(Claim.id.desc()).all()
    out = []
    for c in claims:
        pv = db.query(PolicyVersion).get(c.policy_version_db_id)
        policy = pv.policy if pv else None
        latest_run = (
            db.query(ValidationRun)
            .filter_by(claim_id=c.id)
            .order_by(ValidationRun.id.desc())
            .first()
        )
        out.append({
            "claim_id": c.id,
            "claim_ref": c.claim_ref,
            "insurer": policy.insurer.name if policy else None,
            "product": policy.product_name if policy else None,
            "policy_version_id": pv.policy_version_id if pv else None,
            "admission_date": str(c.admission_date) if c.admission_date else None,
            "billed_amount": str(c.billed_amount) if c.billed_amount is not None else None,
            "claim_provenance": c.claim_provenance,
            "last_validation_result": latest_run.overall_result if latest_run else None,
        })
    return out


def _create_claim(claim_in: ClaimCreate, db: Session):
    pv = db.query(PolicyVersion).filter_by(policy_version_id=claim_in.policy_version_id).first()
    if not pv:
        raise HTTPException(400, f"Unknown policy_version_id: {claim_in.policy_version_id}")
    data = claim_in.model_dump(exclude={"policy_version_id"})
    claim = Claim(policy_version_db_id=pv.id, **data)
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return {"claim_id": claim.id, "claim_ref": claim.claim_ref}


def _get_claim(claim_id: int, db: Session):
    claim = db.query(Claim).get(claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    return {
        "claim_id": claim.id, "claim_ref": claim.claim_ref,
        "policy_version_db_id": claim.policy_version_db_id,
        "policy_start_date": str(claim.policy_start_date),
        "admission_date": str(claim.admission_date),
        "claim_provenance": claim.claim_provenance,
    }


def _validate(claim_id: int, db: Session):
    claim = db.query(Claim).get(claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    run, results, financials = validate_claim(db, claim)
    return {"overall_result": run.overall_result, "results": _nest_source(results), "financials": financials}


def _get_latest_validation(claim_id: int, db: Session):
    claim = db.query(Claim).get(claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    run = (
        db.query(ValidationRun)
        .filter_by(claim_id=claim_id)
        .order_by(ValidationRun.id.desc())
        .first()
    )
    if not run:
        raise HTTPException(404, "no validation run yet for this claim; POST /validate first")
    results = db.query(ValidationResult).filter_by(validation_run_id=run.id).all()
    flat = [
        {"rule_id": r.rule_id, "category": r.category, "severity": r.severity,
         "reason": r.reason, "expected": r.expected, "actual": r.actual,
         "source_document": r.source_document, "source_page": r.source_page,
         "provenance": r.provenance}
        for r in results
    ]
    return {"overall_result": run.overall_result, "results": _nest_source(flat), "financials": None}


# ---------------------------------------------------------------------------
# Legacy /api/* routes (kept for the existing 18 pytest tests)
# ---------------------------------------------------------------------------

@app.get("/api/health")
def api_health():
    return _health()


@app.get("/api/policies")
def api_list_policies(db: Session = Depends(get_db)):
    return _list_policies(db)


@app.get("/api/policies/{policy_id}/versions")
def api_policy_versions(policy_id: int, db: Session = Depends(get_db)):
    return _get_policy(policy_id, db)["versions"]


@app.get("/api/policies/{policy_id}/rules")
def api_policy_rules(policy_id: int, db: Session = Depends(get_db)):
    return _policy_rules(policy_id, db)


@app.get("/api/policies/{policy_id}/sources")
def api_policy_sources(policy_id: int, db: Session = Depends(get_db)):
    return _policy_sources(policy_id, db)


@app.post("/api/claims")
def api_create_claim(claim_in: ClaimCreate, db: Session = Depends(get_db)):
    return _create_claim(claim_in, db)


@app.get("/api/claims/{claim_id}")
def api_get_claim(claim_id: int, db: Session = Depends(get_db)):
    return _get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/validate")
def api_validate(claim_id: int, db: Session = Depends(get_db)):
    # NOTE: legacy route intentionally keeps the OLD flat source_document/
    # source_page shape (no response_model) so it stays byte-compatible
    # with the existing pytest assertions written against it.
    claim = db.query(Claim).get(claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    run, results, financials = validate_claim(db, claim)
    return {"overall_result": run.overall_result, "results": results, "financials": financials}


@app.get("/api/claims/{claim_id}/validation")
def api_get_latest_validation(claim_id: int, db: Session = Depends(get_db)):
    claim = db.query(Claim).get(claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    run = (
        db.query(ValidationRun)
        .filter_by(claim_id=claim_id)
        .order_by(ValidationRun.id.desc())
        .first()
    )
    if not run:
        raise HTTPException(404, "no validation run yet for this claim; POST /validate first")
    results = db.query(ValidationResult).filter_by(validation_run_id=run.id).all()
    return {
        "overall_result": run.overall_result,
        "results": [
            {"rule_id": r.rule_id, "category": r.category, "severity": r.severity,
             "reason": r.reason, "expected": r.expected, "actual": r.actual,
             "source_document": r.source_document, "source_page": r.source_page,
             "source_section": r.source_section, "provenance": r.provenance}
            for r in results
        ],
        "financials": None,
    }


# ---------------------------------------------------------------------------
# Root-level routes (new contract: nested source.document / source.page)
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return _health()


@app.get("/policies")
def list_policies(db: Session = Depends(get_db)):
    return _list_policies(db)


@app.get("/policies/{policy_id}")
def get_policy(policy_id: int, db: Session = Depends(get_db)):
    return _get_policy(policy_id, db)


@app.get("/policies/{policy_id}/rules")
def policy_rules(policy_id: int, db: Session = Depends(get_db)):
    return _policy_rules(policy_id, db)


@app.get("/policies/{policy_id}/sources")
def policy_sources(policy_id: int, db: Session = Depends(get_db)):
    return _policy_sources(policy_id, db)


@app.get("/documents/{document_id}/text")
def get_document_text(document_id: str, db: Session = Depends(get_db)):
    """Serves the REAL extracted-text file for a source document (screen 8 /
    provenance viewer). Reads from disk; never fabricates or paraphrases
    the text. Returns 404 with a clear reason if the document_id is
    unknown or its file is genuinely missing from this session's dataset."""
    doc = db.query(Document).filter_by(document_id=document_id).first()
    if not doc:
        raise HTTPException(404, f"Unknown document_id: {document_id}")
    rel_path = _DOCUMENT_TEXT_FILES.get(document_id)
    if not rel_path:
        raise HTTPException(404, f"No extracted-text file is mapped for document_id: {document_id}")
    abs_path = os.path.join(_DATA_ROOT, rel_path)
    if not os.path.exists(abs_path):
        raise HTTPException(404, f"Extracted-text file not found on disk: {rel_path}")
    with open(abs_path, "r", encoding="utf-8") as f:
        text = f.read()
    return {
        "document_id": document_id,
        "source_url": doc.source_url,
        "hash_type": doc.hash_type,
        "page_count": doc.page_count,
        "text": text,
    }


@app.get("/rules/{candidate_id}")
def get_rule_by_candidate_id(candidate_id: str, db: Session = Depends(get_db)):
    """Looks up a single rule by its candidate_id (e.g. 'SA26-002'). Needed
    because a validation result only carries rule_id -- this lets the
    frontend's evidence screen fetch the full source_text for that rule
    without re-deriving it."""
    r = db.query(PolicyRule).filter_by(candidate_id=candidate_id).first()
    if not r:
        raise HTTPException(404, f"Unknown candidate_id: {candidate_id}")
    return {
        "candidate_id": r.candidate_id, "rule_type": r.rule_type, "rule_name": r.rule_name,
        "condition": r.condition, "value": r.value, "unit": r.unit,
        "applies_to": r.applies_to, "exception": r.exception,
        "source_document": r.source_document, "source_page": r.source_page,
        "source_section": r.source_section, "source_text": r.source_text,
        "extraction_method": r.extraction_method, "confidence": r.confidence,
        "review_status": r.review_status, "provenance": r.provenance,
    }


@app.post("/claims")
def create_claim(claim_in: ClaimCreate, db: Session = Depends(get_db)):
    return _create_claim(claim_in, db)


@app.get("/claims")
def list_claims(db: Session = Depends(get_db)):
    return _list_claims(db)


@app.get("/claims/{claim_id}")
def get_claim(claim_id: int, db: Session = Depends(get_db)):
    return _get_claim(claim_id, db)


@app.post("/claims/{claim_id}/validate", response_model=ValidationRunOut)
def validate(claim_id: int, db: Session = Depends(get_db)):
    return _validate(claim_id, db)


@app.get("/claims/{claim_id}/validation", response_model=ValidationRunOut)
def get_latest_validation(claim_id: int, db: Session = Depends(get_db)):
    return _get_latest_validation(claim_id, db)


app.include_router(qa_router)

# ClearClaim product layer (/api/v1, CORS, request limits, logging). Added
# without changing any route above; see app/clearclaim/server.py.
from app.clearclaim.server import install as _install_clearclaim  # noqa: E402
_install_clearclaim(app)
