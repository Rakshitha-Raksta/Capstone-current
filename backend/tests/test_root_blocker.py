"""
Phase-5 regression tests for the core contribution: explicit root/consequence
rules, multiple simultaneous faults, misattribution, missing evidence, safety
(uncertain -> human review, never silent approval), dependent-stage reruns,
cache invalidation, decision provenance, and frozen original evaluators.
"""
import hashlib
import os

import pytest

from app.clearclaim import dag
from app.clearclaim.context import DEFAULT_SETTINGS, ReferenceContext, invalidate_context
from app.clearclaim.findings import finding
from app.clearclaim.models import (
    AuditEvent, CCBase, ClaimCase, Finding, ReviewDecision, ReviewTask, StageExecution,
)
from app.clearclaim.orchestrator import Orchestrator
from app.clearclaim.stages.analysis import (
    CAUSAL_RULES, run_claim_generation, run_review_routing, run_root_blocker,
)
from app.clearclaim.stages.clinical import run_data_validation
from app.database.db import SessionLocal, engine
from app.datasets.synthetic import generate_synthetic_dataset
from app.datasets.validate import load_icd10_reference

REF = load_icd10_reference()


def ctx(**over):
    s = dict(DEFAULT_SETTINGS)
    s.update(over)
    return ReferenceContext(rate_card={}, historical=[], icd10=REF, settings=s)


def rb(**stage_findings):
    up = {s: [] for s in dag.STAGE_ORDER if s not in ("root_blocker", "review_routing", "claim_generation")}
    up.update(stage_findings)
    return run_root_blocker({}, up, ctx())["data"]


def roles(out):
    return {x["code"]: x["role"] for grp in ("roots", "consequences", "advisories") for x in out[grp]}


# ---------------------------------------------------------------------------
# explicit causal rules
# ---------------------------------------------------------------------------

def _note(phrase, invalid_codes):
    """Run data_validation on a minimal encounter so possible_cause_codes is computed for real."""
    dx = [dict(seq_num=1, icd_code="I10", icd_version=10, long_title=REF["I10"])]
    for i, c in enumerate(invalid_codes, start=2):
        dx.append(dict(seq_num=i, icd_code=c, icd_version=10, long_title=None))
    note = f"Discharge Diagnosis:\nPrimary:\n- Hypertension\n\nSecondary:\n- {phrase}\n\nDischarge Condition:\nok"
    inp = dict(encounter=dict(admittime="2026-03-01T10:00:00", dischtime="2026-03-03T10:00:00", gender="M",
                              admission_type="URGENT"),
               diagnoses=dx, procedures=[], discharge_note=dict(text=note, provenance="T"))
    return run_data_validation(inp, {}, ctx())["findings"]


def test_note_gap_explained_by_invalid_code_of_same_category_is_consequence():
    f = _note("Hyperlipidemia", ["E78"])          # E785 'Hyperlipidemia, unspecified' lives in E78
    nd = next(x for x in f if x["code"] == "NOTE_DX_NOT_CODED")
    assert nd["evidence"]["possible_cause_codes"] == ["E78"]
    assert roles(rb(data_validation=f))["NOTE_DX_NOT_CODED"] == "CONSEQUENCE"


def test_independent_note_gap_is_not_misattributed_to_unrelated_invalid_code():
    # regression for the seed-1 misattribution reported in docs/research/EXPERIMENT_RESULTS.md
    f = _note("Hypokalemia", ["E78"])
    nd = next(x for x in f if x["code"] == "NOTE_DX_NOT_CODED")
    assert nd["evidence"]["possible_cause_codes"] == []
    r = roles(rb(data_validation=f))
    assert r["NOTE_DX_NOT_CODED"] == "ROOT" and r["INVALID_ICD_CODE"] == "ROOT"


def test_rule_not_evaluable_only_caused_by_missing_discharge_for_filing_rules():
    miss = finding("MISSING_DISCHARGE_TIME", "BLOCKING", "x", field="dischtime")
    filing = finding("RULE_NOT_EVALUABLE", "REVIEW", "x", rule_id="SA26-010", rule_category="CLAIM_FILING_DEADLINE")
    waiting = finding("RULE_NOT_EVALUABLE", "REVIEW", "y", rule_id="SA26-001", rule_category="INITIAL_WAITING_PERIOD")
    out = rb(data_validation=[miss], eligibility=[filing, waiting])
    by_rule = {x.get("rule_id"): x["role"] for x in out["roots"] + out["consequences"]}
    assert by_rule["SA26-010"] == "CONSEQUENCE" and by_rule["SA26-001"] == "ROOT"


def test_historical_outlier_is_consequence_only_when_bill_total_is_inflated():
    bill = finding("DUPLICATE_BILL_ITEM", "BLOCKING", "dup", line_no=7)
    hist_total = finding("HISTORICAL_OUTLIER", "ADVISORY", "h", z_total=6.0, z_los=0.0, z_threshold=3.5)
    hist_los = finding("HISTORICAL_OUTLIER", "ADVISORY", "h", z_total=1.0, z_los=5.0, z_threshold=3.5)
    assert roles(rb(billing=[bill], historical=[hist_total]))["HISTORICAL_OUTLIER"] == "CONSEQUENCE"
    assert roles(rb(billing=[bill], historical=[hist_los]))["HISTORICAL_OUTLIER"] == "ADVISORY"


def test_every_causal_rule_documents_its_reason():
    assert all(len(r) == 4 and r[3] for r in CAUSAL_RULES)
    doc = open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "ROOT_BLOCKER_RULES.md"),
               encoding="utf-8").read()
    for cons, root, _, _ in CAUSAL_RULES:
        assert cons in doc and root.lstrip("*") in doc


# ---------------------------------------------------------------------------
# multiple faults, routing and safety
# ---------------------------------------------------------------------------

def test_multiple_independent_faults_are_each_a_root_with_a_task():
    out = rb(discharge_readiness=[finding("MISSING_CLAIM_DOCUMENT", "BLOCKING", "m", document="final_bill")],
             billing=[finding("DUPLICATE_BILL_ITEM", "BLOCKING", "d", line_no=4),
                      finding("BILL_ARITHMETIC_ERROR", "BLOCKING", "a", line_no=6)])
    assert {x["code"] for x in out["roots"]} == {"MISSING_CLAIM_DOCUMENT", "DUPLICATE_BILL_ITEM", "BILL_ARITHMETIC_ERROR"}
    rr = run_review_routing({"reviews": []}, {"root_blocker": out}, ctx())["data"]
    assert rr["open_count"] == 3
    # each root has its own rerun plan derived from the DAG
    plans = {x["code"]: x["rerun_stages"] for x in out["roots"]}
    assert "billing" not in plans["MISSING_CLAIM_DOCUMENT"][:1] and plans["DUPLICATE_BILL_ITEM"][1] == "billing"


def _claimgen(out, reviews=()):
    rr = run_review_routing({"reviews": list(reviews)}, {"root_blocker": out}, ctx())["data"]
    up = {"review_routing": rr, "root_blocker": out,
          "adjudication": dict(waterfall=[], gross=0, payable_if_eligible=0, estimated_eligible=0,
                               eligibility_blocked=False, label="x"),
          "billing": dict(lines_total=0, line_results=[]), "data_validation": dict(los_days=1)}
    inp = dict(encounter=dict(subject_id=1, admittime=None, dischtime=None, data_source="T"), policy=None,
               diagnoses=[], procedures=[], claim_admin=None, bill=dict(lines=[]))
    return run_claim_generation(inp, up, ctx())["data"]["status"], rr


def test_uncertain_result_goes_to_review_not_silent_approval():
    out = rb(billing=[finding("UNKNOWN_BILL_ITEM", "REVIEW", "u", line_no=3)])
    status, _ = _claimgen(out)
    assert status == "NEEDS_REVIEW"


def test_blocking_consequence_blocks_even_without_its_own_task():
    out = rb(billing=[finding("DUPLICATE_BILL_ITEM", "BLOCKING", "d", line_no=4)])
    # simulate a future causal rule producing a BLOCKING consequence
    out["consequences"].append(dict(finding("ADJUDICATION_ON_DISPUTED_BILL", "BLOCKING", "c"), stage="reconciliation",
                                    role="CONSEQUENCE", caused_by="SOMETHING_ELSE"))
    out["roots"] = []
    status, _ = _claimgen(out)
    assert status == "BLOCKED"


def test_accepting_root_with_justification_completes_only_if_nothing_blocking_remains():
    late = finding("LATE_FILING", "REVIEW", "late", rule_id="SA26-010")
    out = rb(eligibility=[late])
    status, rr = _claimgen(out)
    assert status == "NEEDS_REVIEW"
    key = rr["tasks"][0]["task_key"]
    status2, _ = _claimgen(out, reviews=[dict(task_key=key, code="LATE_FILING", decision="ACCEPT_WITH_JUSTIFICATION")])
    assert status2 == "PREPARATION_COMPLETE"


# ---------------------------------------------------------------------------
# integration: dependent reruns, cache invalidation, provenance
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db(tmp_path_factory):
    from app.clearclaim.etl import import_dataset
    d = tmp_path_factory.mktemp("rb_cohort")
    generate_synthetic_dataset(str(d), seed=77, n_encounters=40, error_rate=0.8)
    CCBase.metadata.drop_all(engine)
    CCBase.metadata.create_all(engine)
    invalidate_context()
    s = SessionLocal()
    assert import_dataset(s, str(d), str(d), "demo", "SYNTHETIC").status == "COMPLETED"
    o = Orchestrator(s, "parallel", "targeted")
    for c in s.query(ClaimCase):
        o.process(c)
    yield s
    s.close()
    CCBase.metadata.drop_all(engine)
    invalidate_context()


def test_discharge_time_correction_reruns_true_dependents_only(db):
    from app.clearclaim.corrections import apply_correction
    # create the fault deterministically on a case whose readiness stage is clean
    c = next(x for x in db.query(ClaimCase).order_by(ClaimCase.id)
             if not db.query(Finding).filter_by(case_id=x.id, stage="discharge_readiness", is_current=True).count())
    orig = c.encounter.dischtime
    c.encounter.dischtime = None
    db.flush()
    Orchestrator(db).process(c, trigger="MANUAL_RERUN", changed_domains=["encounter"])
    codes = {x.code: x.role for x in db.query(Finding).filter_by(case_id=c.id, is_current=True)}
    assert codes["MISSING_DISCHARGE_TIME"] == "ROOT" and codes.get("LOS_NOT_VERIFIABLE") == "CONSEQUENCE"
    adm = orig
    _, run = apply_correction(db, c, "set_discharge_time", {"value": adm.isoformat()},
                              "tester", "discharge time recorded")
    executed = {s.stage for s in db.query(StageExecution).filter_by(run_id=run.id, status="SUCCEEDED")}
    # encounter is read by data_validation and eligibility; billing depends on LOS from data_validation
    assert {"data_validation", "eligibility", "billing"} <= executed
    assert "discharge_readiness" not in executed     # documents/checklist unchanged
    codes = {x.code for x in db.query(Finding).filter_by(case_id=c.id, is_current=True)}
    assert "MISSING_DISCHARGE_TIME" not in codes and "LOS_NOT_VERIFIABLE" not in codes


def test_reused_stage_output_is_identical_and_settings_change_invalidates_cache(db):
    c = db.query(ClaimCase).order_by(ClaimCase.id).first()
    o = Orchestrator(db, "parallel", "targeted")
    run = o.process(c, trigger="MANUAL_RERUN", changed_domains=list(dag.ALL_INPUT_DOMAINS))
    reused = db.query(StageExecution).filter_by(run_id=run.id, status="REUSED").all()
    assert len(reused) == len(dag.STAGE_ORDER)                # nothing changed -> everything reused
    for r in reused:
        src = db.get(StageExecution, r.reused_from_id)
        assert src.output_hash == r.output_hash and src.is_current
    # changing a threshold changes the reference-context fingerprint -> stages must re-execute
    c2 = ctx()
    c2.rate_card = o.ctx.rate_card
    c2.historical = o.ctx.historical
    c2.settings["historical_z_threshold"] = 9.0
    run2 = Orchestrator(db, "parallel", "targeted", ctx=c2).process(
        c, trigger="MANUAL_RERUN", changed_domains=list(dag.ALL_INPUT_DOMAINS))
    assert run2.stages_reused == 0 and run2.stages_executed == len(dag.STAGE_ORDER)


def test_every_correction_and_decision_is_fully_recorded(db):
    from app.clearclaim.corrections import apply_correction
    f = db.query(Finding).filter(Finding.is_current.is_(True), Finding.role == "ROOT",
                                 Finding.code.in_(["DUPLICATE_BILL_ITEM", "MISSING_CLAIM_DOCUMENT"])).first()
    c = db.get(ClaimCase, f.case_id)
    op, payload = (("delete_bill_line", {"line_no": f.evidence["line_no"]}) if f.code == "DUPLICATE_BILL_ITEM"
                   else ("add_document", {"document": f.evidence["document"]}))
    corr, run = apply_correction(db, c, op, payload, "tester", "provenance check")
    assert corr.before is not None or op == "add_document"
    assert corr.after and corr.run_id == run.id and corr.domain
    ev = db.query(AuditEvent).filter_by(case_id=c.id, entity_type="correction", entity_id=corr.id).one()
    assert ev.event_type == "CORRECTION_APPLIED" and ev.details["after"] == corr.after
    assert db.query(StageExecution).filter_by(run_id=run.id).count() > 0
    # findings carry rule ids and citations where they come from a policy rule
    for x in db.query(Finding).filter(Finding.is_current.is_(True), Finding.stage.in_(["eligibility", "adjudication"]),
                                      Finding.code.in_(["WAITING_PERIOD_NOT_MET", "LATE_FILING", "LATE_NOTIFICATION",
                                                        "MISSING_KYC_DOCUMENT", "CO_PAYMENT_APPLIED"])):
        assert x.rule_id and x.source_document and x.source_page, (x.code, x.rule_id)
    # a review decision records decision row, audit event and a rerun
    t = db.query(ReviewTask).filter_by(status="OPEN").filter(ReviewTask.allowed_decisions.isnot(None)).all()
    t = next((x for x in t if "ACCEPT_WITH_JUSTIFICATION" in (x.allowed_decisions or [])), None)
    if t is not None:
        case = db.get(ClaimCase, t.case_id)
        db.add(ReviewDecision(case_id=case.id, task_id=t.id, code=t.code, task_key=t.task_key,
                              decision="ACCEPT_WITH_JUSTIFICATION", note="test justification", actor="tester"))
        db.flush()
        r = Orchestrator(db).process(case, trigger="REVIEW_DECISION", changed_domains=["reviews"])
        executed = {s.stage for s in db.query(StageExecution).filter_by(run_id=r.id, status="SUCCEEDED")}
        assert executed <= {"review_routing", "claim_generation"}
        db.refresh(t)
        assert t.status == "ACCEPTED_WITH_JUSTIFICATION"


# ---------------------------------------------------------------------------
# original deterministic evaluators are frozen
# ---------------------------------------------------------------------------

FROZEN = {  # sha256 with LF line endings; equal to the untouched backup in Documents\ (verified 2026-10-21)
    "app/rules/common.py": "f0faeada2f9b40f8535681d09b59e4612df15369931c634a1083bc8249b68767",
    "app/rules/copay.py": "798ec8586148f3878dcd01ab73e0e5dcfd7fad9fd8abebd110e297be86fb6a6e",
    "app/rules/deadline.py": "5ec59d36074169965217793b4bad250b3476faa4f3544587dac1bc9e6304708c",
    "app/rules/deductible.py": "8f8bedbdcff6f2ee480479633c3937aad3314d49bfbdcdb68f4ae24bd623ad5b",
    "app/rules/documentation.py": "e69661d922bf2ccb944dc38e442e3756ed906eea8b2dbda1e533b7eb2ae1fb64",
    "app/rules/eligibility.py": "756b7f1451b6a9c3708f2b75ec91152e2634ca56d2b28bbd28038713275bb52e",
    "app/rules/engine.py": "94fe0b32c61283a2e4e23a71f09390ca63e8afedb399ea7ded95403fe47ec505",
    "app/rules/exclusion.py": "0fad2944bcdd4b7bc4ca109462318580fbd8bdb0bff8be28c689593c779b7e38",
    "app/rules/policy_status.py": "53cde94cb1fe47a8a24c20d5257058f3e3728e3e764d85a4a3957ee58e4f1fb0",
    "app/rules/preauth.py": "f367119bedfaab307354d36b978163be929f42f76a4a5396220aa29499928aa7",
    "app/rules/room_rent.py": "1672ff766ba64102e2a17d4d34181b2bf0c06bb75bc5ddd742358c36dd1313d0",
    "app/rules/sublimit.py": "be367cec893801ff5a994fe16902652e30463ae9205a8bf5c739fbce5b80594e",
    "app/rules/waiting_period.py": "241a17ac05b2733391352d1516b51dd45acb90985e9ebc8443207d3c7fbfb6b9",
    "app/services/financial_calculator.py": "832e831b9e659c0df57d4338419cd2729313e0a37ad23b5f4f27f6b99925cbb1",
    "../data/structured/policy_rule_candidates.csv": "9fe45d27e4e6ac16564872d2cb7b7588d6d627d9e06a916d23ab8aa994bbccc9",
}


def test_original_evaluators_and_rules_unchanged():
    base = os.path.join(os.path.dirname(__file__), "..")
    for rel, want in FROZEN.items():
        data = open(os.path.join(base, rel), "rb").read().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == want, f"{rel} changed; document any intentional bug fix"


# ---------------------------------------------------------------------------
# fixes from the MIMIC-IV demo error analysis (2026-10-21)
# ---------------------------------------------------------------------------

def test_procedure_in_ed_before_admission_is_inside_the_encounter():
    """Real MIMIC case 20854119: procedure dated the day before admission, during the ED stay."""
    base = dict(admittime="2188-08-06T00:49:00", dischtime="2188-08-12T17:49:00", gender="M", admission_type="EW EMER.")
    proc = [dict(seq_num=1, icd_code="8604", icd_version=9, chartdate="2188-08-05", long_title="x")]
    dx = [dict(seq_num=1, icd_code="I10", icd_version=10, long_title=REF["I10"])]
    with_ed = run_data_validation(dict(encounter=dict(base, edregtime="2188-08-05T20:36:00"), diagnoses=dx,
                                       procedures=proc, discharge_note=None), {}, ctx())["findings"]
    assert "PROCEDURE_DATE_OUT_OF_RANGE" not in {x["code"] for x in with_ed}
    no_ed = run_data_validation(dict(encounter=base, diagnoses=dx, procedures=proc, discharge_note=None), {}, ctx())["findings"]
    assert "PROCEDURE_DATE_OUT_OF_RANGE" in {x["code"] for x in no_ed}   # without ED evidence it is still flagged
    after = run_data_validation(dict(encounter=dict(base, edregtime="2188-08-05T20:36:00"), diagnoses=dx,
                                     procedures=[dict(proc[0], chartdate="2188-08-30")], discharge_note=None),
                                {}, ctx())["findings"]
    assert "PROCEDURE_DATE_OUT_OF_RANGE" in {x["code"] for x in after}   # weeks after discharge still flagged


def test_missing_charge_detects_a_missing_repeat_of_the_same_code():
    """Real MIMIC case 28166872: 88.41 coded three times; one of three bill lines removed."""
    from app.clearclaim.stages.billing import run_billing
    procs = [dict(seq_num=i, icd_code="8841", long_title="angiography") for i in (1, 2, 3)]
    line = lambda n: dict(line_no=n, item_code="PROC-8841", category="PROCEDURE", description="", quantity=1,
                          unit_price=25000, amount=25000, service_date=f"2150-01-0{n}", linked_icd_code="8841")
    c = ctx()
    c.rate_card = {"PROC-GENERIC": dict(unit_price=25000)}
    out = run_billing(dict(bill=dict(lines=[line(1), line(2)]), procedures=procs), {"data_validation": {"los_days": 3}}, c)
    miss = [x for x in out["findings"] if x["code"] == "MISSING_CHARGE"]
    assert len(miss) == 1 and miss[0]["evidence"]["seq_num"] == 3
    full = run_billing(dict(bill=dict(lines=[line(1), line(2), line(3)]), procedures=procs),
                       {"data_validation": {"los_days": 3}}, c)
    assert not [x for x in full["findings"] if x["code"] == "MISSING_CHARGE"]


def test_kyc_triggered_only_by_overbilling_is_a_consequence_of_the_billing_root():
    over = finding("UNIT_PRICE_ABOVE_TARIFF", "BLOCKING", "price", line_no=5, amount=20000)
    kyc_cons = finding("MISSING_KYC_DOCUMENT", "BLOCKING", "kyc", rule_id="HDFC21-011", document="KYC",
                       billed_total=110000.0, verified_total=90000.0, kyc_threshold=100000.0)
    kyc_real = finding("MISSING_KYC_DOCUMENT", "BLOCKING", "kyc", rule_id="HDFC21-011", document="KYC",
                       billed_total=150000.0, verified_total=130000.0, kyc_threshold=100000.0)
    assert roles(rb(billing=[over], adjudication=[kyc_cons]))["MISSING_KYC_DOCUMENT"] == "CONSEQUENCE"
    assert roles(rb(billing=[over], adjudication=[kyc_real]))["MISSING_KYC_DOCUMENT"] == "ROOT"
    # and while the over-billing is unresolved, the case stays BLOCKED (safety rule)
    status, _ = _claimgen(rb(billing=[over], adjudication=[kyc_cons]))
    assert status == "BLOCKED"


def test_kyc_not_discounted_when_overbilling_valuation_is_uncertain():
    """Real-encounter case 24104168 (MIMIC seed 1): 4 extra WARD days on a bill that also has ICU days.
    Valued at the ICU rate the 'verified' bill fell below the KYC threshold and KYC was wrongly treated as
    a consequence. The causal rule now uses the minimum plausible dispute (cheapest room rate)."""
    from app.clearclaim.stages.billing import run_billing
    lines = [dict(line_no=1, item_code="ROOM-GW", category="ROOM", description="", quantity=11, unit_price=2500,
                  amount=27500, service_date="2150-01-01", linked_icd_code=None),
             dict(line_no=2, item_code="ICU-DAY", category="ICU", description="", quantity=2, unit_price=18000,
                  amount=36000, service_date="2150-01-01", linked_icd_code=None),
             dict(line_no=3, item_code="LAB-ADMIT", category="LAB", description="", quantity=1, unit_price=65260,
                  amount=65260, service_date="2150-01-01", linked_icd_code=None)]
    c = ctx()
    c.rate_card = {"ROOM-GW": dict(unit_price=2500), "ICU-DAY": dict(unit_price=18000), "LAB-ADMIT": dict(unit_price=65260)}
    b = run_billing(dict(bill=dict(lines=lines), procedures=[]), {"data_validation": {"los_days": 9}}, c)["data"]
    assert b["lines_total"] == 128760 and b["disputed_amount"] == 4 * 18000 and b["disputed_amount_min"] == 4 * 2500
    over = finding("LOS_BILLING_MISMATCH", "BLOCKING", "los", line_no=1, amount=b["disputed_amount"])
    kyc = finding("MISSING_KYC_DOCUMENT", "BLOCKING", "kyc", rule_id="HDFC21-011", document="KYC",
                  billed_total=b["lines_total"], verified_total=b["lines_total"] - b["disputed_amount_min"],
                  kyc_threshold=100000.0)
    assert roles(rb(billing=[over], adjudication=[kyc]))["MISSING_KYC_DOCUMENT"] == "ROOT"
