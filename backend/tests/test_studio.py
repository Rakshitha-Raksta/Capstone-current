"""
Agent Execution Studio: the observer sees the real orchestrator's decisions, the API validates
encounter IDs, persisted executions match the events, failures are reported as failures, and
targeted re-runs explain why each stage executed, was reused or was not re-run.
"""
import time

from app.clearclaim import dag
from app.clearclaim.models import ClaimCase, StageExecution
from app.clearclaim.orchestrator import Orchestrator
from tests.test_isolation import _fresh_cases


def _wait(client, token, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        body = client.get(f"/api/v1/studio/runs/{token}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError("studio run did not finish")


def test_observer_does_not_change_the_run_and_reports_real_order():
    db = _fresh_cases(n=6, seed=52)
    c = db.query(ClaimCase).first()
    events = []
    run = Orchestrator(db, "sequential", "full", on_event=events.append).process(c, trigger="MANUAL_RERUN",
                                                                                  changed_domains=["bill"])
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_finished"
    started = [e["stage"] for e in events if e["type"] == "stage_started"]
    assert started == dag.STAGE_ORDER                                    # topological execution order
    persisted = db.query(StageExecution).filter_by(run_id=run.id).order_by(StageExecution.id).all()
    fin = {e["stage"]: e for e in events if e["type"] == "stage_finished"}
    for se in persisted:                                                 # every timing/status is the persisted one
        assert fin[se.stage]["status"] == se.status
        assert abs(fin[se.stage]["duration_ms"] - se.duration_ms) < 0.01
        assert fin[se.stage]["stage_status"] == se.stage_status
    ts = [e["t_ms"] for e in events]
    assert ts == sorted(ts)
    db.close()


def test_failed_stage_is_reported_as_failure_and_dependents_skipped(monkeypatch):
    from app.clearclaim import orchestrator as orch_mod
    db = _fresh_cases(n=4, seed=53)
    c = db.query(ClaimCase).first()

    def boom(*a, **k):
        raise RuntimeError("injected test failure")
    monkeypatch.setitem(orch_mod.STAGE_FUNCTIONS, "billing", boom)
    events = []
    run = Orchestrator(db, "sequential", "full", on_event=events.append).process(c, trigger="MANUAL_RERUN")
    fin = [e for e in events if e["type"] == "stage_finished" and e["stage"] == "billing"][0]
    assert fin["status"] == "FAILED" and "injected test failure" in fin["error"]
    skipped = {e["stage"] for e in events if e["type"] == "stage_skipped"}
    assert "adjudication" in skipped and "claim_generation" in skipped
    end = events[-1]
    assert end["type"] == "run_finished" and end["status"] == "FAILED" and run.status == "FAILED"
    db.close()


def test_studio_api_validates_encounter_ids(client):
    db = _fresh_cases(n=4, seed=54)
    hadm = db.query(ClaimCase).first().encounter.hadm_id
    assert client.get("/api/v1/studio/encounters/abc").status_code == 422
    r = client.get("/api/v1/studio/encounters/12345")
    assert r.status_code == 404 and "No encounter with ID 12345" in r.json()["detail"] and str(hadm)[:4] in r.json()["detail"]
    ok = client.get(f"/api/v1/studio/encounters/{hadm}")
    assert ok.status_code == 200 and ok.json()["case"]["hadm_id"] == hadm
    assert client.post("/api/v1/studio/runs", json={"hadm_id": "99999999"}).status_code == 404
    reg = client.get("/api/v1/studio/stages").json()
    assert [s["id"] for s in reg["stages"]] == dag.STAGE_ORDER
    assert all(s["kind"] == "DETERMINISTIC_RULES" and s["uses_llm"] is False for s in reg["stages"])
    assert any(ch["code"] == "DUPLICATE_BILL_ITEM" for ch in next(s for s in reg["stages"] if s["id"] == "billing")["checks"])
    db.close()


def test_studio_full_run_events_match_persisted_record_and_hide_patient_ids(client):
    db = _fresh_cases(n=4, seed=55)
    hadm = db.query(ClaimCase).first().encounter.hadm_id
    db.close()
    start = client.post("/api/v1/studio/runs", json={"hadm_id": str(hadm), "mode": "full"})
    assert start.status_code == 202
    res = _wait(client, start.json()["token"])
    assert res["status"] == "finished" and res["run_status"] == "SUCCEEDED"
    detail = client.get(f"/api/v1/studio/pipeline-runs/{res['run_id']}").json()
    assert [s["stage"] for s in detail["stages"]] == dag.STAGE_ORDER
    assert all(s["execution"]["status"] == "SUCCEEDED" for s in detail["stages"])      # full policy executes all 11
    ing = next(s for s in detail["stages"] if s["stage"] == "ingestion")
    assert ing["output"]["subject_id"] == "[hidden in studio]"
    cg = next(s for s in detail["stages"] if s["stage"] == "claim_generation")
    assert str(cg["output"]["packet"]).startswith("[")
    started = [e["stage"] for e in res["events"] if e["type"] == "stage_started"]
    assert started == dag.STAGE_ORDER


def test_studio_correction_run_explains_executed_reused_and_not_rerun(client):
    db = _fresh_cases(n=12, seed=41)
    c = next(c for c in db.query(ClaimCase) if c.encounter.claim_admin)
    hadm, version = c.encounter.hadm_id, c.version
    db.close()
    body = {"hadm_id": str(hadm), "mode": "correction",
            "correction": {"op": "add_document", "payload": {"document": "extra_note"}, "reason": "studio test",
                           "expected_version": version}}
    res = _wait(client, client.post("/api/v1/studio/runs", json=body).json()["token"])
    assert res["status"] == "finished", res
    ev = res["events"]
    kinds = {}
    for e in ev:
        if "stage" in e:
            kinds.setdefault(e["stage"], set()).add(e["type"])
    assert kinds["data_validation"] == {"stage_not_rerun"}               # documents are not read by data validation
    assert {"stage_started", "stage_finished"} <= kinds["discharge_readiness"]   # reads documents
    assert any(e["type"] == "stage_started" and "documents" in (e["reason"] or "") for e in ev)
    for e in ev:
        if e["type"] == "stage_reused":
            assert "input hash identical" in e["reason"] and e["reused_from_execution"]
    run = client.get(f"/api/v1/studio/pipeline-runs/{res['run_id']}").json()["run"]
    assert run["trigger"] == "CORRECTION" and run["changed_domains"] == ["documents"]
    # stale correction is rejected, recorded as rejected, and nothing new is persisted
    again = _wait(client, client.post("/api/v1/studio/runs", json=body).json()["token"])
    assert again["status"] == "rejected" and again["http_status"] == 409 and again["run_id"] is None
