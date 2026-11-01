"""Finding construction helpers shared by all stages."""
from typing import Optional

SEVERITIES = ("BLOCKING", "REVIEW", "ADVISORY", "INFO")
SEVERITY_RANK = {"BLOCKING": 3, "REVIEW": 2, "ADVISORY": 1, "INFO": 0}

# Short human titles for finding codes (UI and review tasks).
TITLES = {
    "MISSING_SOURCE_RECORD": "Required source record missing",
    "MISSING_REQUIRED_FIELD": "Required encounter field missing",
    "MISSING_DISCHARGE_TIME": "Discharge time missing",
    "DISCHARGE_BEFORE_ADMISSION": "Discharge recorded before admission",
    "INVALID_ICD_CODE": "Invalid ICD-10-CM code",
    "ICD_CODE_VERSION_MISMATCH": "Code not in current ICD-10-CM release",
    "NO_COMPARABLE_DRG": "No comparable MS-DRG for history",
    "GENDER_DX_CONFLICT": "Sex-specific diagnosis conflict",
    "PROCEDURE_DATE_OUT_OF_RANGE": "Procedure date outside admission",
    "NOTE_DX_NOT_CODED": "Documented diagnosis not coded",
    "CODED_DX_NOT_IN_NOTE": "Principal diagnosis not documented",
    "DISCHARGE_NOTE_MISSING": "Discharge note missing",
    "MISSING_CLAIM_DOCUMENT": "Claim document missing",
    "READINESS_ITEM_INCOMPLETE": "Discharge checklist item pending",
    "LOS_BILLING_MISMATCH": "Bed-days billed differ from length of stay",
    "LOS_NOT_VERIFIABLE": "Bed-days could not be verified",
    "BILLED_PROCEDURE_NOT_DOCUMENTED": "Billed procedure not documented",
    "MISSING_CHARGE": "Documented procedure not billed",
    "DUPLICATE_BILL_ITEM": "Duplicate bill line",
    "BILL_ARITHMETIC_ERROR": "Line amount arithmetic error",
    "UNIT_PRICE_ABOVE_TARIFF": "Unit price above rate card",
    "UNKNOWN_BILL_ITEM": "Bill item not on rate card",
    "WAITING_PERIOD_NOT_MET": "Policy waiting period not met",
    "POLICY_INACTIVE": "Policy inactive at admission",
    "POLICY_EXPIRED": "Policy expired at admission",
    "LATE_FILING": "Claim filed after deadline",
    "LATE_NOTIFICATION": "Insurer notified late",
    "PREAUTH_LEAD_TIME_SHORT": "Pre-authorisation requested late",
    "EXCLUSION_KEYWORD_MATCH": "Possible policy exclusion",
    "RULE_NOT_EVALUABLE": "Policy rule could not be evaluated",
    "RULES_QUARANTINED": "Policy version rules under review",
    "NO_MEMBER_POLICY": "No insurance policy linked",
    "MISSING_KYC_DOCUMENT": "KYC documents missing",
    "ROOM_RENT_LIMIT_EXCEEDED": "Room rent above policy limit",
    "SUB_LIMIT_EXCEEDED": "Sub-limit exceeded",
    "CO_PAYMENT_APPLIED": "Co-payment applies",
    "DEDUCTIBLE_APPLIED": "Deductible applies",
    "SUM_INSURED_CAP_APPLIED": "Sum insured cap applies",
    "HISTORICAL_OUTLIER": "Unusual versus historical peers",
    "INSUFFICIENT_PEERS": "Too few historical peers",
    "RECONCILIATION_RESIDUAL": "Bill header total does not reconcile",
    "WATERFALL_RESIDUAL": "Financial waterfall does not balance",
    "ADJUDICATION_ON_DISPUTED_BILL": "Coverage computed on a disputed bill",
    "CONFLICTING_ASSESSMENT": "Billing and historical assessments disagree",
}


def finding(code: str, severity: str, message: str, *, rule_id: Optional[str] = None,
            source_document: Optional[str] = None, source_page: Optional[str] = None,
            expected=None, actual=None, amount: Optional[float] = None, **evidence) -> dict:
    assert severity in SEVERITIES, severity
    return dict(
        code=code, severity=severity, title=TITLES.get(code, code.replace("_", " ").title()), message=message,
        rule_id=rule_id, source_document=source_document, source_page=source_page,
        expected=None if expected is None else str(expected), actual=None if actual is None else str(actual),
        amount=None if amount is None else round(float(amount), 2), evidence=evidence or {},
    )


def stage_status(findings) -> str:
    sev = {f["severity"] for f in findings}
    if "BLOCKING" in sev:
        return "BLOCKED"
    if "REVIEW" in sev:
        return "WARN"
    return "OK"
