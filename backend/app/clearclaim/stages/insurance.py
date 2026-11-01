"""
Stages: eligibility (amount independent) and adjudication (amount dependent).

Both call the EXISTING evaluator functions in app/rules/* unchanged. The
legacy engine (app/rules/engine.py) runs all evaluators in one pass; here the
same functions are split by whether they need the final billed amount, so
eligibility can run in parallel with billing.

Adjudication waterfall (no overlapping deductions; each step applies to what
remains after the previous step):
    gross recorded bill
  - billing disputed amount       (over-charges found by billing; excluded pending correction)
  - sub-limit excess              (SUB_LIMIT rules, e.g. SA26-006/007)
  - room-rent excess              ((billed rate - limit) x ward days; SA26-005. The proportionate
                                   deduction on associated charges is NOT applied: that clause was not
                                   captured in the dataset -- see app/rules/room_rent.py)
  - deductible                    (SA26-009, only if opted)
  - co-payment                    (pct x remaining; SA26-008)
  - sum-insured cap
  = estimated eligible amount     (RULE-BASED ESTIMATE, NOT AN INSURER DECISION)
Note: the legacy financial_calculator computes co-pay on the gross bill;
this waterfall computes it on the remaining base to avoid deducting the
same rupee twice. The legacy calculator is left unchanged for the legacy API.
"""
import re
from datetime import date
from types import SimpleNamespace

from app.clearclaim.findings import finding
from app.rules.common import parse_number
from app.rules.waiting_period import evaluate_waiting_periods
from app.rules.exclusion import evaluate_exclusions
from app.rules.deadline import evaluate_deadlines
from app.rules.preauth import evaluate_preauth
from app.rules.eligibility import evaluate_eligibility
from app.rules.policy_status import evaluate_policy_status
from app.rules.room_rent import evaluate_room_rent
from app.rules.sublimit import evaluate_sublimits
from app.rules.copay import evaluate_copay
from app.rules.deductible import evaluate_deductible
from app.rules.documentation import evaluate_documentation

WAITING = ("INITIAL_WAITING_PERIOD", "PED_WAITING_PERIOD", "SPECIFIED_DISEASE_WAITING_PERIOD")


def _date(s):
    return date.fromisoformat(s[:10]) if s else None


def _rules(policy):
    active = [SimpleNamespace(**r) for r in policy["rules"] if r["review_status"] in ("PENDING", "APPROVED")]
    quarantined = [r for r in policy["rules"] if r["review_status"] == "NEEDS_REVIEW"]
    return active, quarantined


def _claim_ns(inp, up, billed_amount=None, room_rate=None, documents=None):
    enc, ca, pol = inp["encounter"], inp["claim_admin"] or {}, inp["policy"]
    return SimpleNamespace(
        policy_start_date=_date(pol["policy_start_date"]), policy_end_date=_date(pol["policy_end_date"]),
        policy_cancelled_date=_date(pol["policy_cancelled_date"]),
        admission_date=_date(enc["admittime"]), discharge_date=_date(enc["dischtime"]),
        diagnosis_description=up.get("data_validation", {}).get("dx_description")
        or "; ".join(d["long_title"] or d["icd_code"] for d in inp.get("diagnoses", [])),
        claim_type=ca.get("claim_type"), claim_filed_date=_date(ca.get("claim_filed_date")),
        notification_date=_date(ca.get("notification_date")),
        preauth_request_date=_date(ca.get("preauth_request_date")),
        sum_insured=pol["sum_insured"], room_rent_per_day=room_rate, billed_amount=billed_amount,
        insured_age_at_entry=pol["insured_age_at_entry"], treatment_category=None, category_billed_amount=None,
        deductible_opted=pol["deductible_opted"], deductible_amount_opted=pol["deductible_amount"],
        documents_submitted=",".join(documents or []),
    )


def _evidence(r):
    return dict(rule_id=r["rule_id"], source_document=r["source_document"], source_page=r["source_page"],
                expected=r["expected"], actual=r["actual"])


def _rule_result_row(r):
    return dict(rule_id=r["rule_id"], category=r["category"], severity=r["severity"], reason=r["reason"],
                expected=r["expected"], actual=r["actual"], source_document=r["source_document"],
                source_page=r["source_page"], source_section=r.get("source_section"), provenance=r["provenance"])


# ---------------------------------------------------------------------------
# eligibility
# ---------------------------------------------------------------------------

def run_eligibility(inp, up, ctx):
    pol = inp["policy"]
    if pol is None or not pol.get("policy_version_known"):
        return dict(data=dict(blocked=True, rule_results=[], policy_version_id=pol and pol["policy_version_id"]),
                    findings=[finding("NO_MEMBER_POLICY", "BLOCKING",
                                      "No insurance policy (with a known policy version) is linked to this patient.")])
    active, quarantined = _rules(pol)
    claim = _claim_ns(inp, up)
    results = []
    if claim.admission_date and claim.policy_start_date:
        results += evaluate_waiting_periods(active, claim.policy_start_date, claim.admission_date)
    results += evaluate_exclusions(active, claim.diagnosis_description)
    results += evaluate_deadlines(active, claim)
    results += evaluate_preauth(active, claim)
    results += evaluate_eligibility(active, claim)
    results += evaluate_policy_status(active, claim)

    f = []
    for r in results:
        cat, sev, not_provided = r["category"], r["severity"], r["actual"] == "NOT_PROVIDED"
        ev = _evidence(r)
        if sev == "PASS":
            continue
        if not_provided:
            f.append(finding("RULE_NOT_EVALUABLE", "REVIEW", r["reason"], **ev, rule_category=cat))
        elif cat in WAITING and sev == "FAIL":
            f.append(finding("WAITING_PERIOD_NOT_MET", "BLOCKING", r["reason"], **ev, rule_category=cat))
        elif cat in WAITING:
            f.append(finding("RULE_NOT_EVALUABLE", "REVIEW", r["reason"], **ev, rule_category=cat))
        elif cat == "POLICY_INACTIVE" and sev == "FAIL":
            f.append(finding("POLICY_INACTIVE", "BLOCKING", r["reason"], **ev))
        elif cat == "POLICY_EXPIRED" and sev == "FAIL":
            f.append(finding("POLICY_EXPIRED", "BLOCKING", r["reason"], **ev))
        elif cat == "CLAIM_FILING_DEADLINE":
            f.append(finding("LATE_FILING", "REVIEW", r["reason"], **ev))
        elif cat == "CLAIM_NOTIFICATION_DEADLINE":
            f.append(finding("LATE_NOTIFICATION", "REVIEW", r["reason"], **ev))
        elif cat == "PREAUTH_REQUIREMENT":
            f.append(finding("PREAUTH_LEAD_TIME_SHORT", "REVIEW", r["reason"], **ev))
        elif cat in ("EXCLUSION", "NON_COVERED_TREATMENT"):
            f.append(finding("EXCLUSION_KEYWORD_MATCH", "REVIEW", r["reason"], **ev))
        else:
            f.append(finding("RULE_NOT_EVALUABLE", "REVIEW", r["reason"], **ev, rule_category=cat))
    if quarantined:
        f.append(finding("RULES_QUARANTINED", "REVIEW",
                         f"{len(quarantined)} rule(s) for policy version {pol['policy_version_id']} are flagged "
                         f"NEEDS_REVIEW (unresolved UIN conflict) and were NOT evaluated automatically.",
                         expected="manual review of source before these rules can be trusted",
                         actual="NOT_EVALUATED", rule_ids=[r["candidate_id"] for r in quarantined],
                         source_document=quarantined[0]["source_document"],
                         reference="docs/HDFC_ERGO_UIN_REVIEW.md"))
    blocked = any(x["severity"] == "BLOCKING" for x in f)
    return dict(data=dict(blocked=blocked, policy_version_id=pol["policy_version_id"], uin=pol["uin"],
                          n_active_rules=len(active), n_quarantined_rules=len(quarantined),
                          rule_results=[_rule_result_row(r) for r in results]),
                findings=f)


# ---------------------------------------------------------------------------
# adjudication
# ---------------------------------------------------------------------------

def _rs(s):
    m = re.search(r"Rs\.([\d.]+)", s or "")
    return float(m.group(1)) if m else None


def run_adjudication(inp, up, ctx):
    pol = inp["policy"]
    bill = up["billing"]
    elig = up["eligibility"]
    gross = float(bill["lines_total"])
    if pol is None or not pol.get("policy_version_known"):
        wf = [dict(step="gross_bill", amount=gross, basis="recorded bill total")]
        return dict(data=dict(waterfall=wf, gross=gross, estimated_eligible=0.0, payable_if_eligible=0.0,
                              eligibility_blocked=True, rule_results=[],
                              label="RULE-BASED ESTIMATE - NOT AN INSURER DECISION"), findings=[])
    active, _ = _rules(pol)
    docs = up["discharge_readiness"]["documents"]
    claim = _claim_ns(inp, up, billed_amount=gross, room_rate=bill["room_rate"], documents=docs)
    room = evaluate_room_rent(active, claim)
    sub = evaluate_sublimits(active, claim)
    copay = evaluate_copay(active, claim)
    deduct = evaluate_deductible(active, claim)
    docres = evaluate_documentation(active, claim)
    results = room + sub + copay + deduct + docres
    f = []

    steps = [dict(step="gross_bill", amount=gross, basis="sum of recorded bill lines")]
    remaining = gross

    def take(step, amount, basis, rule=None):
        nonlocal remaining
        amount = round(max(0.0, min(amount, remaining)), 2)
        remaining = round(remaining - amount, 2)
        steps.append(dict(step=step, amount=-amount, basis=basis,
                          rule_id=rule["rule_id"] if rule else None,
                          source_document=rule["source_document"] if rule else None,
                          source_page=rule["source_page"] if rule else None))
        return amount

    take("billing_disputed", float(bill["disputed_amount"]),
         "over-charges identified by billing verification (excluded until corrected)")

    for r in sub:
        if r["severity"] == "PARTIAL_DEDUCTION":
            excess = (_rs(r["actual"]) or 0) - (_rs(r["expected"]) or 0)
            amt = take("sub_limit_excess", excess, r["reason"], r)
            f.append(finding("SUB_LIMIT_EXCEEDED", "INFO", r["reason"], amount=amt, **_evidence(r)))
        elif r["severity"] == "WARNING":
            f.append(finding("RULE_NOT_EVALUABLE", "REVIEW", r["reason"], **_evidence(r)))

    for r in room:
        if r["severity"] == "PARTIAL_DEDUCTION":
            limit = _rs(r["expected"]) or 0
            excess = (float(bill["room_rate"]) - limit) * float(bill["ward_days"])
            amt = take("room_rent_excess", excess,
                       f"(room rate {bill['room_rate']:.2f} - limit {limit:.2f}) x {bill['ward_days']:g} ward day(s); "
                       f"proportionate deduction clause not captured in dataset, not applied", r)
            f.append(finding("ROOM_RENT_LIMIT_EXCEEDED", "INFO", r["reason"], amount=amt, **_evidence(r)))
        elif r["severity"] == "WARNING":
            f.append(finding("RULE_NOT_EVALUABLE", "REVIEW", r["reason"], **_evidence(r)))

    for r in deduct:
        if r["severity"] == "PARTIAL_DEDUCTION":
            amt = take("deductible", float(pol["deductible_amount"] or 0), r["reason"], r)
            f.append(finding("DEDUCTIBLE_APPLIED", "INFO", r["reason"], amount=amt, **_evidence(r)))
        elif r["severity"] == "WARNING":
            f.append(finding("RULE_NOT_EVALUABLE", "REVIEW", r["reason"], **_evidence(r)))

    for r in copay:
        if r["severity"] == "PARTIAL_DEDUCTION":
            rule = next(x for x in active if x.candidate_id == r["rule_id"])
            pct = parse_number(rule.value) or 0
            amt = take("co_payment", remaining * pct / 100.0, f"{pct:g}% of remaining base {remaining:.2f}", r)
            f.append(finding("CO_PAYMENT_APPLIED", "INFO",
                             f"{pct:g}% co-payment applies (insured age at entry {pol['insured_age_at_entry']}).",
                             amount=amt, **_evidence(r)))
        elif r["severity"] == "WARNING":
            f.append(finding("RULE_NOT_EVALUABLE", "REVIEW", r["reason"], **_evidence(r)))

    si = float(pol["sum_insured"])
    if remaining > si:
        amt = take("sum_insured_cap", remaining - si, f"sum insured {si:.2f}")
        f.append(finding("SUM_INSURED_CAP_APPLIED", "INFO", f"Estimate capped at the sum insured {si:.2f}.",
                         amount=amt))

    for r in docres:
        if r["severity"] == "WARNING":
            rule = next((x for x in active if x.candidate_id == r["rule_id"]), None)
            f.append(finding("MISSING_KYC_DOCUMENT", "BLOCKING", r["reason"], document="KYC",
                             billed_total=gross,
                             # upper bound of the verified bill (least favourable reading of disputes), so a
                             # causal rule only discounts KYC when the bill is below the threshold for certain
                             verified_total=round(gross - float(bill.get("disputed_amount_min",
                                                                         bill["disputed_amount"])), 2),
                             kyc_threshold=parse_number(rule.value) if rule else None, **_evidence(r)))

    steps.append(dict(step="estimated_eligible", amount=remaining, basis="remaining after all steps"))
    blocked = bool(elig["blocked"])
    return dict(data=dict(waterfall=steps, gross=gross, payable_if_eligible=remaining,
                          estimated_eligible=0.0 if blocked else remaining, eligibility_blocked=blocked,
                          rule_results=[_rule_result_row(r) for r in results],
                          label="RULE-BASED ESTIMATE - NOT AN INSURER DECISION"),
                findings=f)
