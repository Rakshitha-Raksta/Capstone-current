"""
Semantic coverage of all 59 rules in data/structured/policy_rule_candidates.csv.

Runs the EXISTING evaluators (unchanged) against claims built to trigger
every claim-evaluable rule, and pins exactly which rule ids produce a result.
Every one of the 59 rules must be either evaluated or listed below with the
reason it is not evaluated per claim. This documents behaviour; it does not
change it.
"""
import csv
import os
from datetime import date
from types import SimpleNamespace

from app.models.models import PolicyRule, PolicyVersion, RegulatoryRule
from app.rules.copay import evaluate_copay
from app.rules.deadline import evaluate_deadlines
from app.rules.deductible import evaluate_deductible
from app.rules.documentation import evaluate_documentation
from app.rules.exclusion import evaluate_exclusions
from app.rules.preauth import evaluate_preauth
from app.rules.room_rent import evaluate_room_rent
from app.rules.sublimit import evaluate_sublimits
from app.rules.waiting_period import evaluate_waiting_periods

CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "structured", "policy_rule_candidates.csv")

NOT_EVALUATED_PER_CLAIM = {
    # Star Assure 2026
    "SA26-011": "post-hospitalisation filing deadline: claim has no post-hospitalisation date field",
    "SA26-017": "moratorium period: policy-administration rule, not a claim check",
    "SA26-018": "free-look period: policy-administration rule",
    "SA26-019": "portability lead time: policy-administration rule",
    "SA26-020": "insurer-side cashless TAT: an insurer obligation, not verifiable from claim fields",
    # HDFC Optima Secure 2021
    "HDFC21-004": "moratorium period: policy-administration rule",
    "HDFC21-005": "free-look period: policy-administration rule",
    "HDFC21-006": "portability lead time: policy-administration rule",
    "HDFC21-008": "post-hospitalisation filing deadline: no field",
    "HDFC21-009": "cancellation notice period: administrative; claim-level inactive check is derived logic",
    "HDFC21-012": "cumulative bonus tagged SUB_LIMIT in the dataset; no claim carries this category, so it is "
                  "never evaluated in practice (evaluating it as a cap would misread the clause)",
}
QUARANTINED_VERSION = "hdfc_optima_secure_2026_v1"   # 17 rules, NEEDS_REVIEW (UIN conflict)
REGULATORY_VERSION = "irdai_master_circular_2024"    # 10 rules, regulatory table, not per-claim

KEYWORDS = "cosmetic plastic surgery; refractive error dioptres; alcoholism substance abuse; sterility infertility"


def _claims():
    base = dict(policy_start_date=date(2026, 3, 1), admission_date=date(2026, 3, 10), discharge_date=date(2026, 3, 12),
                diagnosis_description=KEYWORDS, sum_insured=500000, room_rent_per_day=9000.0, billed_amount=250000.0,
                insured_age_at_entry=66, deductible_opted=True, deductible_amount_opted=50000.0,
                documents_submitted="claim_form", claim_filed_date=date(2026, 4, 30), notification_date=date(2026, 3, 14),
                preauth_request_date=date(2026, 3, 9), policy_end_date=None, policy_cancelled_date=None,
                category_billed_amount=90000.0, treatment_category=None)
    out = [SimpleNamespace(**dict(base, claim_type="EMERGENCY")), SimpleNamespace(**dict(base, claim_type="PLANNED"))]
    for cat in ("Home Care Treatment", "Air Ambulance"):
        out.append(SimpleNamespace(**dict(base, claim_type="EMERGENCY", treatment_category=cat)))
    return out


def _evaluate(rules):
    ids = set()
    for c in _claims():
        res = (evaluate_waiting_periods(rules, c.policy_start_date, c.admission_date)
               + evaluate_exclusions(rules, c.diagnosis_description) + evaluate_room_rent(rules, c)
               + evaluate_sublimits(rules, c) + evaluate_copay(rules, c) + evaluate_deductible(rules, c)
               + evaluate_deadlines(rules, c) + evaluate_preauth(rules, c) + evaluate_documentation(rules, c))
        ids |= {r["rule_id"] for r in res}
    return ids


def test_every_one_of_the_59_rules_is_accounted_for(db):
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    assert len(rows) == 59
    evaluated = set()
    for pv in db.query(PolicyVersion).all():
        rules = db.query(PolicyRule).filter_by(policy_version_db_id=pv.id).all()
        active = [r for r in rules if r.review_status in ("PENDING", "APPROVED")]
        evaluated |= _evaluate(active)
    regulatory = {r.regulation_id for r in db.query(RegulatoryRule).all()}

    expected_eval = {r["candidate_id"] for r in rows
                     if r["policy_version"] not in (QUARANTINED_VERSION, REGULATORY_VERSION)
                     and r["candidate_id"] not in NOT_EVALUATED_PER_CLAIM}
    assert evaluated == expected_eval

    for r in rows:
        cid = r["candidate_id"]
        if r["policy_version"] == QUARANTINED_VERSION:
            assert r["review_status"] == "NEEDS_REVIEW"
        elif r["policy_version"] == REGULATORY_VERSION:
            assert cid in regulatory
        else:
            assert cid in evaluated or cid in NOT_EVALUATED_PER_CLAIM, cid
    # Star 20 rules (15 evaluated, 5 not) + HDFC 2021 12 rules (6 evaluated, 6 not) = 32;
    # + 17 quarantined HDFC 2026 + 10 IRDAI regulatory = 59
    assert len(expected_eval) == 21
    assert len(NOT_EVALUATED_PER_CLAIM) + len(expected_eval) == 32
