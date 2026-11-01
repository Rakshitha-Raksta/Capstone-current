"""ClearClaim One correction preview: must equal the orchestrator's own plan, and run nothing."""
from fastapi.testclient import TestClient

from app.clearclaim import dag
from app.clearclaim.corrections import OP_DOMAINS
from app.clearclaim.models import PipelineRun
from app.clearclaim.workspace import correction_preview
from app.main import app


def test_preview_matches_dependency_model_for_every_operation():
    for op, domain in OP_DOMAINS.items():
        p = correction_preview(op)
        assert p["domain"] == domain
        assert p["planned"] == dag.invalidated_by([domain])
        assert set(p["planned"]) | set(p["kept"]) == set(dag.STAGE_ORDER)
        assert not set(p["planned"]) & set(p["kept"])
        assert set(p["reads_domain"]) == dag.stages_reading([domain])
        assert set(p["why"]) == set(p["planned"])


def test_bill_preview_is_the_documented_plan():
    p = correction_preview("delete_bill_line")
    assert p["planned"] == ["ingestion", "billing", "adjudication", "historical", "reconciliation",
                            "root_blocker", "review_routing", "claim_generation"]
    assert p["kept"] == ["data_validation", "discharge_readiness", "eligibility"]


def test_preview_api_validates_and_executes_nothing(db):
    c = TestClient(app)
    before = db.query(PipelineRun).count()
    r = c.get("/api/v1/workspace/correction-preview", params={"op": "add_document"})
    assert r.status_code == 200 and r.json()["domain"] == "documents"
    assert c.get("/api/v1/workspace/correction-preview", params={"op": "drop_table"}).status_code == 422
    assert db.query(PipelineRun).count() == before
