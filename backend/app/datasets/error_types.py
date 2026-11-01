"""
Registry of the issue types that the synthetic generator can inject and the
pipeline stages are expected to detect.

The SAME codes are used as Finding.code by the stages, so evaluation can
compare pipeline output with ground truth by (hadm_id, code). The ground
truth itself is written to data/.../ground_truth/ and is never read by the
runtime pipeline or the API.

Fields: category, detecting_stage, input_domain (what a human must correct),
correctable (can an authorised data correction resolve it?), description.
"""

ERROR_TYPES = {
    # --- data validation (clinical record consistency) ---
    "NOTE_DX_NOT_CODED": ("documentation", "data_validation", "diagnoses", True,
                          "Discharge note documents a diagnosis that has no matching coded diagnosis"),
    "CODED_DX_NOT_IN_NOTE": ("documentation", "data_validation", "discharge_note", True,
                             "Principal coded diagnosis is not documented in the discharge note"),
    "INVALID_ICD_CODE": ("coding", "data_validation", "diagnoses", True,
                         "Diagnosis code is not a valid billable ICD-10-CM code"),
    "GENDER_DX_CONFLICT": ("coding", "data_validation", "diagnoses", True,
                           "Sex-specific diagnosis is inconsistent with the patient's recorded sex"),
    "MISSING_DISCHARGE_TIME": ("missing_information", "data_validation", "encounter", True,
                               "Discharge time is missing"),
    "PROCEDURE_DATE_OUT_OF_RANGE": ("data_quality", "data_validation", "procedures", True,
                                    "Procedure date falls outside the admission window"),
    # --- administrative discharge readiness ---
    "MISSING_CLAIM_DOCUMENT": ("missing_information", "discharge_readiness", "documents", True,
                               "A standard claim document (discharge summary or final bill) was not submitted"),
    "READINESS_ITEM_INCOMPLETE": ("missing_information", "discharge_readiness", "checklist", True,
                                  "An administrative discharge checklist item is still pending"),
    # --- billing verification ---
    "LOS_BILLING_MISMATCH": ("billing", "billing", "bill", True,
                             "Room/ICU days billed do not match the length of stay"),
    "BILLED_PROCEDURE_NOT_DOCUMENTED": ("billing", "billing", "bill", True,
                                        "Bill charges a procedure that is not in the coded procedure record"),
    "MISSING_CHARGE": ("billing", "billing", "bill", True,
                       "A coded procedure has no corresponding bill line"),
    "DUPLICATE_BILL_ITEM": ("billing", "billing", "bill", True, "Identical bill line appears more than once"),
    "BILL_ARITHMETIC_ERROR": ("billing", "billing", "bill", True,
                              "Line amount differs from quantity x unit price"),
    "UNIT_PRICE_ABOVE_TARIFF": ("billing", "billing", "bill", True, "Unit price is above the rate card"),
    # --- insurance eligibility (amount independent) ---
    "WAITING_PERIOD_NOT_MET": ("policy", "eligibility", "policy", False,
                               "Admission falls inside a policy waiting period"),
    "POLICY_INACTIVE": ("policy", "eligibility", "policy", False,
                        "Policy was cancelled before the admission"),
    "LATE_FILING": ("policy", "eligibility", "claim_admin", False,
                    "Reimbursement claim filed after the policy deadline"),
    "LATE_NOTIFICATION": ("policy", "eligibility", "claim_admin", False,
                          "Emergency admission notified to the insurer after the policy deadline"),
    "PREAUTH_LEAD_TIME_SHORT": ("policy", "eligibility", "claim_admin", False,
                                "Planned admission pre-authorisation requested too late"),
    # --- insurance adjudication (amount dependent) ---
    "MISSING_KYC_DOCUMENT": ("policy", "adjudication", "documents", True,
                             "KYC documents missing for a claim above the policy threshold"),
    # --- reconciliation ---
    "RECONCILIATION_RESIDUAL": ("billing", "reconciliation", "bill", True,
                                "Bill header total does not equal the sum of bill lines"),
}

CLINICAL_ERROR_TYPES = {
    "INVALID_ICD_CODE", "GENDER_DX_CONFLICT", "MISSING_DISCHARGE_TIME", "PROCEDURE_DATE_OUT_OF_RANGE",
}
NOTE_ERROR_TYPES = {"NOTE_DX_NOT_CODED", "CODED_DX_NOT_IN_NOTE"}
BILL_ERROR_TYPES = {
    "LOS_BILLING_MISMATCH", "BILLED_PROCEDURE_NOT_DOCUMENTED", "MISSING_CHARGE", "DUPLICATE_BILL_ITEM",
    "BILL_ARITHMETIC_ERROR", "UNIT_PRICE_ABOVE_TARIFF", "RECONCILIATION_RESIDUAL",
}
ADMIN_ERROR_TYPES = {"MISSING_CLAIM_DOCUMENT", "READINESS_ITEM_INCOMPLETE"}
POLICY_ERROR_TYPES = {
    "WAITING_PERIOD_NOT_MET", "POLICY_INACTIVE", "LATE_FILING", "LATE_NOTIFICATION",
    "PREAUTH_LEAD_TIME_SHORT", "MISSING_KYC_DOCUMENT",
}


def category_of(code: str) -> str:
    return ERROR_TYPES[code][0] if code in ERROR_TYPES else "other"


def detecting_stage(code: str) -> str:
    return ERROR_TYPES[code][1] if code in ERROR_TYPES else "unknown"
