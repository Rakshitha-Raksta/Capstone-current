"""
Builds the immutable, JSON-serialisable input snapshot of a case, split into
input DOMAINS. Each stage declares which domains it reads; a correction
changes one domain, which is how the orchestrator knows which stages are
affected.
"""
from app.clearclaim.models import ClaimCase, Encounter, MemberPolicy, ReviewDecision
from app.models.models import PolicyRule, PolicyVersion

DOMAINS = ("encounter", "diagnoses", "procedures", "discharge_note", "documents", "checklist", "bill",
           "claim_admin", "policy", "reviews")


def _d(v):
    return v.isoformat() if v is not None else None


def build_snapshot(db, case: ClaimCase) -> dict:
    enc: Encounter = case.encounter
    ca = enc.claim_admin
    snap = {}
    snap["encounter"] = dict(
        hadm_id=enc.hadm_id, subject_id=enc.subject_id, data_source=enc.data_source, gender=enc.gender,
        anchor_age=enc.anchor_age, admittime=_d(enc.admittime), dischtime=_d(enc.dischtime), edregtime=_d(enc.edregtime),
        admission_type=enc.admission_type, discharge_location=enc.discharge_location,
        drg_code=enc.drg_code, drg_type=enc.drg_type, drg_description=enc.drg_description,
        transfers=[dict(careunit=t.careunit, intime=_d(t.intime), outtime=_d(t.outtime), eventtype=t.eventtype)
                   for t in enc.transfers],
    )
    snap["diagnoses"] = [dict(seq_num=d.seq_num, icd_code=d.icd_code, icd_version=d.icd_version,
                              long_title=d.long_title) for d in enc.diagnoses]
    snap["procedures"] = [dict(seq_num=p.seq_num, icd_code=p.icd_code, icd_version=p.icd_version,
                               chartdate=_d(p.chartdate), long_title=p.long_title) for p in enc.procedures]
    snap["discharge_note"] = (dict(text=enc.note.text, provenance=enc.note.provenance) if enc.note else None)
    snap["documents"] = sorted(x for x in (ca.documents or "").split(",") if x) if ca else []
    snap["checklist"] = [dict(item=c.item, status=c.status) for c in enc.checklist]
    snap["bill"] = dict(
        header_total=ca.bill_header_total if ca else None,
        lines=[dict(line_no=l.line_no, item_code=l.item_code, category=l.category, description=l.description,
                    quantity=l.quantity, unit_price=l.unit_price, amount=l.amount,
                    service_date=_d(l.service_date), linked_icd_code=l.linked_icd_code or None)
               for l in enc.bill_lines if not l.is_deleted],
    )
    snap["claim_admin"] = (dict(claim_type=ca.claim_type, room_category=ca.room_category,
                                notification_date=_d(ca.notification_date),
                                preauth_request_date=_d(ca.preauth_request_date),
                                claim_filed_date=_d(ca.claim_filed_date)) if ca else None)

    mp = db.query(MemberPolicy).filter_by(subject_id=enc.subject_id).first()
    if mp is None:
        snap["policy"] = None
    else:
        pv = db.query(PolicyVersion).filter_by(policy_version_id=mp.policy_version_id).first()
        rules = []
        if pv is not None:
            for r in db.query(PolicyRule).filter_by(policy_version_db_id=pv.id).order_by(PolicyRule.candidate_id):
                rules.append(dict(candidate_id=r.candidate_id, rule_type=r.rule_type, rule_name=r.rule_name,
                                  condition=r.condition, value=r.value, unit=r.unit, applies_to=r.applies_to,
                                  exception=r.exception, source_document=r.source_document,
                                  source_page=r.source_page, source_section=r.source_section,
                                  review_status=r.review_status, provenance=r.provenance))
        snap["policy"] = dict(
            member_id=mp.member_id, policy_version_id=mp.policy_version_id, policy_version_known=pv is not None,
            uin=pv.uin if pv else None, sum_insured=mp.sum_insured, policy_start_date=_d(mp.policy_start_date),
            policy_end_date=_d(mp.policy_end_date), policy_cancelled_date=_d(mp.policy_cancelled_date),
            insured_age_at_entry=mp.insured_age_at_entry, deductible_opted=bool(mp.deductible_opted),
            deductible_amount=mp.deductible_amount, provenance=mp.provenance, rules=rules,
        )
    snap["reviews"] = [dict(task_key=r.task_key, code=r.code, decision=r.decision)
                       for r in db.query(ReviewDecision).filter_by(case_id=case.id).order_by(ReviewDecision.id)]
    return snap
