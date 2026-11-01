"""
ClearClaim tests: stage formulas, DAG, orchestrator (targeted reruns,
parallel vs sequential equivalence, failure handling), corrections, review
decisions, audit trail and API validation.

Uses the session test database from conftest.py (legacy rules seeded there)
and imports a small synthetic cohort into the ClearClaim tables.
"""
import json
import os
import shutil

import pytest

from app.clearclaim import dag
from app.clearclaim.context import ReferenceContext, DEFAULT_SETTINGS, invalidate_context
from app.clearclaim.models import (
    AuditEvent, CCBase, ClaimCase, Correction, Finding, ReviewTask, StageExecution,
)
from app.clearclaim.orchestrator import Orchestrator
from app.clearclaim.stages.billing import run_billing
from app.clearclaim.stages.clinical import extract_note_diagnoses, phrase_matches_title, sex_specificity
from app.clearclaim.stages.insurance import run_adjudication, run_eligibility
from app.database.db import engine, SessionLocal
from app.datasets.synthetic import generate_synthetic_dataset

N = 40


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cohort(tmp_path_factory):
    d = tmp_path_factory.mktemp("cc_cohort")
    generate_synthetic_dataset(str(d), seed=21, n_encounters=N, error_rate=0.6)
    return d


@pytest.fixture(scope="module")
def loaded(cohort):
    from app.clearclaim.etl import import_dataset
    CCBase.metadata.drop_all(engine)
    CCBase.metadata.create_all(engine)
    invalidate_context()
    db = SessionLocal()
    # import from a copy WITHOUT ground_truth/ to prove the runtime never needs it
    nogt = str(cohort) + "_nogt"
    shutil.copytree(cohort, nogt, dirs_exist_ok=True)
    shutil.rmtree(os.path.join(nogt, "ground_truth"))
    imp = import_dataset(db, nogt, nogt, "demo", "SYNTHETIC")
    assert imp.status == "COMPLETED", imp.validation_report
    orch = Orchestrator(db, "parallel", "targeted")
    for c in db.query(ClaimCase).order_by(ClaimCase.id):
        orch.process(c)
    db.close()
    yield cohort
    CCBase.metadata.drop_all(engine)
    invalidate_context()


def _ctx(**overrides):
    rc = {"ROOM-GW": dict(unit_price=2500, category="ROOM", description=""),
          "ICU-DAY": dict(unit_price=18000, category="ICU", description=""),
          "LAB-ADMIT": dict(unit_price=2500, category="LAB", description=""),
          "PROC-0DTJ4ZZ": dict(unit_price=65000, category="PROCEDURE", description=""),
          "PROC-GENERIC": dict(unit_price=25000, category="PROCEDURE", description="")}
    s = dict(DEFAULT_SETTINGS)
    s.update(overrides)
    return ReferenceContext(rate_card=rc, historical=[], icd10={}, settings=s)


def _line(n, item, qty, price, amount=None, date="2026-02-01", icd=None):
    return dict(line_no=n, item_code=item, category="", description=item, quantity=qty, unit_price=price,
                amount=qty * price if amount is None else amount, service_date=date, linked_icd_code=icd)


# ---------------------------------------------------------------------------
# stage unit tests
# ---------------------------------------------------------------------------

def test_billing_formulas_and_disputed_amount():
    inp = dict(bill=dict(header_total=None, lines=[
        _line(1, "ROOM-GW", 5, 2500),                         # 5 bed-days vs LOS 3 -> +2 x 2500 disputed
        _line(2, "LAB-ADMIT", 1, 2500, amount=7500),          # arithmetic: +5000 disputed
        _line(3, "LAB-ADMIT", 1, 3000),                       # above tariff: +500 disputed
        _line(4, "PROC-0FT44ZZ", 1, 25000, icd="0FT44ZZ"),    # not documented: +25000 disputed
        _line(5, "LAB-ADMIT", 1, 3000),                       # duplicate of line 3 (+3000) and above tariff (+500)
    ]), procedures=[dict(seq_num=1, icd_code="0DTJ4ZZ", long_title="Appendectomy")])
    out = run_billing(inp, {"data_validation": {"los_days": 3}}, _ctx())
    codes = sorted(f["code"] for f in out["findings"])
    assert codes == sorted(["LOS_BILLING_MISMATCH", "BILL_ARITHMETIC_ERROR", "UNIT_PRICE_ABOVE_TARIFF",
                            "UNIT_PRICE_ABOVE_TARIFF", "BILLED_PROCEDURE_NOT_DOCUMENTED", "DUPLICATE_BILL_ITEM",
                            "MISSING_CHARGE"])
    d = out["data"]
    assert d["lines_total"] == 12500 + 7500 + 3000 + 25000 + 3000
    assert d["disputed_amount"] == 5000 + 5000 + 500 + 25000 + 3000 + 500
    assert d["verified_total"] == round(d["lines_total"] - d["disputed_amount"], 2)
    assert d["missing_charge_amount"] == 65000


def test_billing_los_unknown_is_review_not_guess():
    inp = dict(bill=dict(header_total=None, lines=[_line(1, "ROOM-GW", 4, 2500)]), procedures=[])
    out = run_billing(inp, {"data_validation": {"los_days": None}}, _ctx())
    assert [f["code"] for f in out["findings"]] == ["LOS_NOT_VERIFIABLE"]
    assert out["findings"][0]["severity"] == "REVIEW"


def test_sex_specificity_blocks():
    assert sex_specificity("N400") == "M" and sex_specificity("C61") == "M"
    assert sex_specificity("N979") == "F" and sex_specificity("O80") == "F" and sex_specificity("Z370") == "F"
    assert sex_specificity("I10") is None and sex_specificity("N390") is None


def test_note_extraction_and_matching():
    note = "Chief Complaint:\nx\n\nDischarge Diagnosis:\nPrimary:\n- NSTEMI\n\nSecondary:\n- HTN\n- AKI\n\nDischarge Condition:\nok"
    assert extract_note_diagnoses(note) == ["NSTEMI", "HTN", "AKI"]
    assert phrase_matches_title("NSTEMI", "Non-ST elevation (NSTEMI) myocardial infarction")
    assert phrase_matches_title("HTN", "Essential (primary) hypertension")
    assert phrase_matches_title("AKI", "Acute kidney failure, unspecified")
    assert phrase_matches_title("Community acquired pneumonia", "Pneumonia, unspecified organism")
    assert not phrase_matches_title("Hypokalemia", "Essential (primary) hypertension")


def _policy(**kw):
    import csv
    rules = [r for r in csv.DictReader(open(os.path.join(os.path.dirname(__file__), "..", "..", "data", "structured",
                                                         "policy_rule_candidates.csv"), encoding="utf-8"))
             if r["policy_version"] == kw.get("pv", "star_assure_2026_v1")]
    base = dict(policy_version_id=kw.get("pv", "star_assure_2026_v1"), policy_version_known=True, uin="X",
                sum_insured=500000, policy_start_date="2020-01-01", policy_end_date=None, policy_cancelled_date=None,
                insured_age_at_entry=40, deductible_opted=False, deductible_amount=None,
                rules=[{k: r[k] for k in ("candidate_id", "rule_type", "rule_name", "condition", "value", "unit",
                                          "applies_to", "exception", "source_document", "source_page",
                                          "source_section", "review_status", "provenance")} for r in rules])
    base.update({k: v for k, v in kw.items() if k != "pv"})
    return base


def _enc(admit="2026-03-01T10:00:00", disch="2026-03-04T12:00:00"):
    return dict(admittime=admit, dischtime=disch, gender="M")


def test_eligibility_reuses_existing_waiting_period_rule_with_citation():
    inp = dict(policy=_policy(policy_start_date="2026-02-20"), encounter=_enc(), diagnoses=[],
               claim_admin=dict(claim_type="PLANNED", preauth_request_date="2026-02-20", notification_date="2026-02-20",
                                claim_filed_date="2026-03-06"))
    out = run_eligibility(inp, {"data_validation": {"dx_description": "Pneumonia"}}, _ctx())
    wp = [f for f in out["findings"] if f["code"] == "WAITING_PERIOD_NOT_MET"]
    assert {f["rule_id"] for f in wp} >= {"SA26-001"}           # 30-day initial waiting period
    sa001 = next(f for f in wp if f["rule_id"] == "SA26-001")
    assert sa001["source_document"] == "STAR_ASSURE_2026_CIS" and sa001["source_page"]
    assert out["data"]["blocked"] is True


def test_eligibility_quarantine_for_needs_review_policy():
    inp = dict(policy=_policy(pv="hdfc_optima_secure_2026_v1"), encounter=_enc(), diagnoses=[],
               claim_admin=dict(claim_type="EMERGENCY", preauth_request_date="2026-03-01",
                                notification_date="2026-03-01", claim_filed_date="2026-03-06"))
    out = run_eligibility(inp, {"data_validation": {"dx_description": "x"}}, _ctx())
    q = [f for f in out["findings"] if f["code"] == "RULES_QUARANTINED"]
    assert len(q) == 1 and len(q[0]["evidence"]["rule_ids"]) == 17
    assert out["data"]["n_active_rules"] == 0


def test_adjudication_waterfall_balances_and_copay_on_remaining_base():
    pol = _policy(insured_age_at_entry=65, deductible_opted=True, deductible_amount=50000, sum_insured=500000)
    inp = dict(policy=pol, encounter=_enc(), claim_admin=dict(claim_type="EMERGENCY"))
    up = dict(billing=dict(lines_total=200000.0, disputed_amount=10000.0, room_rate=7500.0, ward_days=3.0),
              eligibility=dict(blocked=False), discharge_readiness=dict(documents=["claim_form"]),
              data_validation=dict(dx_description="x"))
    out = run_adjudication(inp, up, _ctx())
    wf = {s["step"]: s["amount"] for s in out["data"]["waterfall"]}
    assert wf["billing_disputed"] == -10000
    # SA26-005: 1% of 5L = 5000/day; billed 7500 x 3 ward days -> excess 7500
    assert wf["room_rent_excess"] == -7500
    assert wf["deductible"] == -50000                       # SA26-009 opted
    remaining_before_copay = 200000 - 10000 - 7500 - 50000
    assert wf["co_payment"] == -round(remaining_before_copay * 0.10, 2)   # SA26-008 on remaining base
    total = sum(s["amount"] for s in out["data"]["waterfall"] if s["step"] != "estimated_eligible")
    assert round(total, 2) == out["data"]["payable_if_eligible"]


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------

def test_dag_waves_allow_billing_and_eligibility_in_parallel():
    waves = dag.waves()
    assert ["billing", "eligibility"] in waves
    assert waves.index(["billing", "eligibility"]) < waves.index(["adjudication"])


def test_invalidation_is_targeted():
    bill = dag.invalidated_by(["bill"])
    assert "eligibility" not in bill and "data_validation" not in bill and "discharge_readiness" not in bill
    assert {"billing", "adjudication", "reconciliation", "claim_generation"} <= set(bill)
    docs = dag.invalidated_by(["documents"])
    assert "billing" in docs  # descendant of readiness (re-checked by hash, see orchestrator test)
    assert dag.invalidated_by(["reviews"]) == ["review_routing", "claim_generation"]


# ---------------------------------------------------------------------------
# orchestrator, corrections, review (integration on the imported cohort)
# ---------------------------------------------------------------------------

def test_all_cases_processed_with_internal_statuses(loaded):
    db = SessionLocal()
    statuses = {c.status for c in db.query(ClaimCase)}
    assert statuses <= {"PREPARATION_COMPLETE", "NEEDS_REVIEW", "BLOCKED"}
    assert db.query(StageExecution).filter_by(status="FAILED").count() == 0
    # every current finding was assigned a role by the root-blocker stage
    assert db.query(Finding).filter_by(is_current=True, role=None).count() == 0
    db.close()


def _case_with(db, code):
    f = (db.query(Finding).filter_by(code=code, is_current=True, role="ROOT").order_by(Finding.case_id).first())
    return db.get(ClaimCase, f.case_id) if f else None


def test_bill_correction_reruns_only_affected_stages_and_keeps_history(loaded):
    from app.clearclaim.corrections import apply_correction
    db = SessionLocal()
    c = _case_with(db, "DUPLICATE_BILL_ITEM") or _case_with(db, "BILL_ARITHMETIC_ERROR")
    assert c is not None, "cohort should contain a billing fault"
    f = db.query(Finding).filter_by(case_id=c.id, is_current=True, role="ROOT").filter(
        Finding.code.in_(["DUPLICATE_BILL_ITEM", "BILL_ARITHMETIC_ERROR"])).first()
    line_no = f.evidence["line_no"]
    if f.code == "DUPLICATE_BILL_ITEM":
        corr, run = apply_correction(db, c, "delete_bill_line", {"line_no": line_no}, "tester", "remove duplicate")
    else:
        l = next(x for x in c.encounter.bill_lines if x.line_no == line_no)
        corr, run = apply_correction(db, c, "update_bill_line",
                                     {"line_no": line_no, "fields": {"amount": l.quantity * l.unit_price}},
                                     "tester", "fix arithmetic")
    executed = {s.stage for s in db.query(StageExecution).filter_by(run_id=run.id, status="SUCCEEDED")}
    assert "billing" in executed
    assert not ({"data_validation", "discharge_readiness", "eligibility"} & executed)
    assert f.code not in {x.code for x in db.query(Finding).filter_by(case_id=c.id, is_current=True)}
    # old billing output preserved, exactly one current
    billing_rows = db.query(StageExecution).filter_by(case_id=c.id, stage="billing", status="SUCCEEDED").all()
    assert len(billing_rows) >= 2 and sum(r.is_current for r in billing_rows) == 1
    assert db.query(Correction).filter_by(case_id=c.id).count() >= 1
    task = db.query(ReviewTask).filter_by(case_id=c.id, code=f.code).first()
    assert task.status == "RESOLVED_BY_CORRECTION"
    types = {e.event_type for e in db.query(AuditEvent).filter_by(case_id=c.id)}
    assert {"CORRECTION_APPLIED", "PIPELINE_FINISHED", "REVIEW_TASK_RESOLVED"} <= types
    db.close()


def test_document_correction_reuses_unchanged_stages(loaded):
    from app.clearclaim.corrections import apply_correction
    db = SessionLocal()
    c = _case_with(db, "MISSING_CLAIM_DOCUMENT")
    if c is None:
        pytest.skip("no MISSING_CLAIM_DOCUMENT case in this cohort")
    doc = db.query(Finding).filter_by(case_id=c.id, code="MISSING_CLAIM_DOCUMENT", is_current=True).first().evidence["document"]
    _, run = apply_correction(db, c, "add_document", {"document": doc}, "tester", "attach document")
    rows = db.query(StageExecution).filter_by(run_id=run.id).all()
    assert {"billing", "eligibility"} <= {r.stage for r in rows if r.status == "REUSED"}
    assert run.stages_executed < len(dag.STAGE_ORDER)
    db.close()


def test_full_policy_executes_every_stage(loaded):
    db = SessionLocal()
    c = db.query(ClaimCase).order_by(ClaimCase.id).first()
    run = Orchestrator(db, "sequential", "full").process(c, trigger="MANUAL_RERUN", changed_domains=["bill"])
    assert run.stages_executed == len(dag.STAGE_ORDER) and run.stages_reused == 0
    db.close()


def test_parallel_and_sequential_produce_identical_outputs(loaded):
    db = SessionLocal()
    c = db.query(ClaimCase).order_by(ClaimCase.id.desc()).first()
    r1 = Orchestrator(db, "sequential", "full").process(c, trigger="MANUAL_RERUN")
    h1 = {s.stage: s.output_hash for s in db.query(StageExecution).filter_by(run_id=r1.id)}
    r2 = Orchestrator(db, "parallel", "full").process(c, trigger="MANUAL_RERUN")
    h2 = {s.stage: s.output_hash for s in db.query(StageExecution).filter_by(run_id=r2.id)}
    assert h1 == h2 and len(h1) == len(dag.STAGE_ORDER)
    db.close()


def test_stage_failure_is_isolated_and_retried(loaded, monkeypatch):
    from app.clearclaim import stages as stages_pkg
    calls = {"n": 0}

    def boom(inp, up, ctx):
        calls["n"] += 1
        raise RuntimeError("simulated outage")
    monkeypatch.setitem(stages_pkg.STAGE_FUNCTIONS, "historical", boom)
    db = SessionLocal()
    c = db.query(ClaimCase).order_by(ClaimCase.id).first()
    prev_current = db.query(StageExecution).filter_by(case_id=c.id, stage="historical", is_current=True).one().id
    run = Orchestrator(db, "parallel", "full").process(c, trigger="RETRY")
    assert run.status == "FAILED" and c.status == "ERROR"
    assert calls["n"] == 1 + DEFAULT_SETTINGS["max_stage_retries"]
    skipped = {s.stage for s in db.query(StageExecution).filter_by(run_id=run.id, status="SKIPPED_DEPENDENCY_FAILED")}
    assert skipped == {"reconciliation", "root_blocker", "review_routing", "claim_generation"}
    assert db.query(StageExecution).filter_by(case_id=c.id, stage="historical", is_current=True).one().id == prev_current
    monkeypatch.undo()
    run2 = Orchestrator(db, "parallel", "targeted").process(c, trigger="RETRY", changed_domains=["bill"])
    assert run2.status == "SUCCEEDED" and c.status != "ERROR"
    db.close()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_cases_list_detail_and_validation(loaded, client):
    r = client.get("/api/v1/cases", params=dict(page_size=5, sort="priority", order="desc"))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == N and len(body["items"]) == 5
    pr = [i["priority"] for i in body["items"]]
    assert pr == sorted(pr, reverse=True)
    cid = body["items"][0]["id"]
    d = client.get(f"/api/v1/cases/{cid}").json()
    assert d["case"]["id"] == cid and d["billing"]["line_results"] and d["adjudication"]["waterfall"]
    assert "ground_truth" not in json.dumps(d).lower()
    bad = client.post(f"/api/v1/cases/{cid}/corrections", json=dict(op="drop_table", payload={}, reason="xxx"))
    assert bad.status_code == 422
    neg = client.post(f"/api/v1/cases/{cid}/corrections",
                      json=dict(op="update_bill_line", payload={"line_no": 1, "fields": {"quantity": -2}}, reason="bad"))
    assert neg.status_code == 422
    assert client.get("/api/v1/cases/999999").status_code == 404
    assert client.get("/api/v1/workflow/graph").json()["waves"]
    assert client.get("/api/v1/overview").json()["total_cases"] == N


def test_api_review_decision_flow(loaded, client):
    items = client.get("/api/v1/review-tasks", params=dict(status="OPEN", page_size=100)).json()["items"]
    t = next(i for i in items if "ACCEPT_WITH_JUSTIFICATION" in i["allowed_decisions"])
    r = client.post(f"/api/v1/review-tasks/{t['id']}/decision",
                    json=dict(decision="ACCEPT_WITH_JUSTIFICATION", note="delay condoned per insurer email"))
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "ACCEPTED_WITH_JUSTIFICATION"
    executed = {s["stage"] for s in r.json()["run"]["stages"] if s["status"] == "SUCCEEDED"}
    assert executed <= {"review_routing", "claim_generation"}
    again = client.post(f"/api/v1/review-tasks/{t['id']}/decision",
                        json=dict(decision="ACCEPT_WITH_JUSTIFICATION", note="second time"))
    assert again.status_code == 409
    blocking = next((i for i in items if i["allowed_decisions"] == ["CORRECT_DATA"]), None)
    if blocking:
        r = client.post(f"/api/v1/review-tasks/{blocking['id']}/decision",
                        json=dict(decision="ACCEPT_WITH_JUSTIFICATION", note="trying to skip a data fix"))
        assert r.status_code == 422


def test_write_token_enforced_when_configured(loaded, client, monkeypatch):
    from app.clearclaim.config import settings
    monkeypatch.setattr(settings, "write_token", "s3cret")
    cid = client.get("/api/v1/cases", params=dict(page_size=1)).json()["items"][0]["id"]
    assert client.post(f"/api/v1/cases/{cid}/process").status_code == 401
    ok = client.post(f"/api/v1/cases/{cid}/process", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert client.get("/api/v1/cases").status_code == 200   # reads stay open


def test_settings_validation(loaded, client):
    assert client.put("/api/v1/settings", json=dict(values=dict(routing_policy="everything"))).status_code == 422
    r = client.put("/api/v1/settings", json=dict(values=dict(historical_z_threshold=4.0)))
    assert r.status_code == 200 and r.json()["values"]["historical_z_threshold"] == 4.0
    client.put("/api/v1/settings", json=dict(values=dict(historical_z_threshold=3.5)))


def test_request_size_limit(client):
    big = "x" * (300 * 1024)
    r = client.post("/api/v1/cases/1/corrections", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
