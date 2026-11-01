"""
Reproducible SYNTHETIC linked-dataset generator.

Entry points
------------
generate_synthetic_dataset(out_dir, seed, n_encounters, error_rate)
    Full demo dataset: MIMIC-IV-format clinical tables (hosp/, note/), the
    synthetic insurance/administrative layer (admin/), and ground truth
    (ground_truth/). subject_ids start at 90,000,001 and hadm_ids at
    98,000,001 (outside MIMIC-IV ranges); every row has data_provenance.

generate_admin_layer_for_mimic(mimic_root, out_dir, seed, error_rate, source_label)
    For REAL MIMIC-IV data. Reads the real clinical tables (never modifies
    them) and writes only the synthetic layer MIMIC-IV lacks. Clinical-table
    faults are never injected into real data.

Linked records per encounter (hadm_id)
--------------------------------------
admin/member_policies.csv       one synthetic policy per subject_id -> one of the real policy versions
admin/claim_admin.csv           claim type, insurer notification/preauth/filing dates, documents, bill header total
admin/discharge_checklist.csv   administrative (not clinical) discharge-readiness items
admin/hospital_bill_items.csv   line-level hospital bill priced from admin/tariff_inr.csv
admin/tariff_inr.csv            documented synthetic rate card
admin/historical_claims.csv     synthetic prior claims used as historical peer groups
ground_truth/*                  injected issues, scenario labels, and the corrections that
                                would undo each injected fault. NEVER read by the runtime.

Determinism
-----------
Seeded random.Random instances only; sorted iteration; no timestamps in
output. Same seed and parameters => byte-identical files (tested).

Policy thresholds used for ground-truth labels come from
data/structured/policy_rule_candidates.csv (the same cited rules the rule
engine uses) -- the generator never hard-codes a policy number.
"""
import csv
import hashlib
import io
import json
import os
import random
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from app.datasets import catalog as C
from app.datasets.error_types import (
    ERROR_TYPES, CLINICAL_ERROR_TYPES, NOTE_ERROR_TYPES, BILL_ERROR_TYPES,
)
from app.datasets.mimic import (
    HOSP_COLUMNS, NOTE_COLUMNS, SYNTHETIC_SUBJECT_ID_START, SYNTHETIC_HADM_ID_START,
    EncounterRecord, write_table, fmt_dt, load_mimic_dir, build_records,
)

GENERATOR_NAME = "clearclaim-synthetic-encounters"
GENERATOR_VERSION = "2.2.1"

PROV_CLINICAL = "SYNTHETIC"
PROV_NOTE = "SYNTHETIC_NOTE"
PROV_NOTE_DERIVED = "SYNTHETIC_NOTE_DERIVED_FROM_STRUCTURED_DATA"
PROV_ADMIN = "SYNTHETIC_INSURANCE_LAYER"
PROV_BILL = "SYNTHETIC_HOSPITAL_BILL"
PROV_TARIFF = "SYNTHETIC_TARIFF_NOT_REAL_PRICES"
PROV_HIST = "SYNTHETIC_HISTORICAL_CLAIMS"
PROV_GT = "SYNTHETIC_GROUND_TRUTH"
PROV_D_ICD_DX = "ICD10CM_REFERENCE_SUBSET"
PROV_D_ICD_PX = "GENERATOR_CATALOG_UNVERIFIED"

SYNTHETIC_NOTE_BANNER = "*** SYNTHETIC DISCHARGE NOTE - generated for software testing - NOT A REAL PATIENT ***"
DERIVED_NOTE_BANNER = ("*** SYNTHETIC NOTE DERIVED FROM STRUCTURED DATA - not written by a clinician - "
                       "for software testing only ***")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RULES_CSV = os.path.join(REPO_ROOT, "data", "structured", "policy_rule_candidates.csv")

POLICY_VERSION_WEIGHTS = [
    ("star_assure_2026_v1", 55),
    ("hdfc_optima_secure_2021_v1", 30),
    ("hdfc_optima_secure_2026_v1", 15),  # rules quarantined (NEEDS_REVIEW) -> exercises review path
]
SUM_INSURED_CHOICES = {
    "star_assure_2026_v1": [500000, 750000, 1000000, 1500000],
    "hdfc_optima_secure_2021_v1": [500000, 1000000, 2000000],
    "hdfc_optima_secure_2026_v1": [500000, 1000000, 2000000],
}
ROOM_CHOICES = [("ROOM-GW", 45), ("ROOM-SP", 30), ("ROOM-PVT", 25)]

# Member-behaviour issue probabilities (independent of error_rate).
P_SHORT_POLICY_TENURE = 0.12
P_POLICY_CANCELLED = 0.03
P_LATE_FILING = 0.06
P_LATE_NOTIFICATION = 0.08
P_PREAUTH_SHORT = 0.10
P_KYC_MISSING = 0.30
P_LEGITIMATE_OUTLIER = 0.06

BASE_DOCUMENTS = ["claim_form", "discharge_summary", "final_bill", "photo_id"]
CHECKLIST_ITEMS = ["DISCHARGE_SUMMARY_FINALISED", "FINAL_BILL_GENERATED", "PHARMACY_RETURNS_CLEARED",
                   "INSURANCE_DESK_NOTIFIED", "PATIENT_ID_VERIFIED"]

INJECTABLE_WEIGHTS = {
    "NOTE_DX_NOT_CODED": 10, "CODED_DX_NOT_IN_NOTE": 8,
    "LOS_BILLING_MISMATCH": 10, "BILLED_PROCEDURE_NOT_DOCUMENTED": 8, "MISSING_CHARGE": 6,
    "DUPLICATE_BILL_ITEM": 8, "BILL_ARITHMETIC_ERROR": 6, "UNIT_PRICE_ABOVE_TARIFF": 6,
    "RECONCILIATION_RESIDUAL": 5,
    "INVALID_ICD_CODE": 8, "GENDER_DX_CONFLICT": 5, "MISSING_DISCHARGE_TIME": 4,
    "PROCEDURE_DATE_OUT_OF_RANGE": 5, "MISSING_CLAIM_DOCUMENT": 5, "READINESS_ITEM_INCOMPLETE": 5,
}
INCOMPATIBLE = [
    {"MISSING_DISCHARGE_TIME", "LOS_BILLING_MISMATCH"},
    {"MISSING_DISCHARGE_TIME", "PROCEDURE_DATE_OUT_OF_RANGE"},
    {"INVALID_ICD_CODE", "CODED_DX_NOT_IN_NOTE"},
    {"MISSING_CHARGE", "PROCEDURE_DATE_OUT_OF_RANGE"},
]
COMPLICATION_DX = ["J9601", "D62", "E871", "N179"]

# Review-only resolutions for faults no data correction can fix.
REVIEW_RESOLUTION = {
    "WAITING_PERIOD_NOT_MET": "CONFIRM_BLOCKED",
    "POLICY_INACTIVE": "CONFIRM_BLOCKED",
    "LATE_FILING": "ACCEPT_WITH_JUSTIFICATION",
    "LATE_NOTIFICATION": "ACCEPT_WITH_JUSTIFICATION",
    "PREAUTH_LEAD_TIME_SHORT": "ACCEPT_WITH_JUSTIFICATION",
}


# ---------------------------------------------------------------------------
# Policy profiles derived from the real rule CSV
# ---------------------------------------------------------------------------

def _num(s):
    m = re.search(r"(\d+(?:\.\d+)?)", str(s or ""))
    return float(m.group(1)) if m else None


def _days(value, unit):
    n = _num(value)
    unit = (unit or "").upper()
    if n is None:
        return None
    return {"DAYS": n, "MONTHS": n * 30, "YEARS": n * 365, "HOURS": n / 24}.get(unit)


def load_policy_profiles(rules_csv: str = RULES_CSV) -> Dict[str, dict]:
    """Summarise, per policy version, the rule thresholds needed for labels."""
    profiles: Dict[str, dict] = {}
    with open(rules_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pv = r["policy_version"]
            p = profiles.setdefault(pv, dict(active_rule_ids=[], quarantined_rule_ids=[],
                                             waiting_days=None, filing_days=None, notify_hours=None,
                                             planned_preauth_hours=None, kyc_threshold=None))
            active = r["review_status"] in ("PENDING", "APPROVED")
            (p["active_rule_ids"] if active else p["quarantined_rule_ids"]).append(r["candidate_id"])
            if not active:
                continue
            rt, applies = r["rule_type"], (r["applies_to"] or "").lower()
            if rt in ("INITIAL_WAITING_PERIOD", "PED_WAITING_PERIOD", "SPECIFIED_DISEASE_WAITING_PERIOD"):
                d = _days(r["value"], r["unit"])
                if d is not None:
                    p["waiting_days"] = max(p["waiting_days"] or 0, d)
            elif rt == "CLAIM_FILING_DEADLINE" and "post" not in applies:
                p["filing_days"] = _days(r["value"], r["unit"])
            elif rt == "CLAIM_NOTIFICATION_DEADLINE" and "emergency" in applies:
                p["notify_hours"] = _days(r["value"], r["unit"]) * 24
            elif rt == "PREAUTH_REQUIREMENT" and "planned" in applies:
                p["planned_preauth_hours"] = _days(r["value"], r["unit"]) * 24
            elif rt == "DOCUMENTATION_MISSING":
                p["kyc_threshold"] = _num(r["value"])
    return profiles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weighted(rng, pairs):
    total = sum(w for _, w in pairs)
    x = rng.uniform(0, total)
    acc = 0
    for item, w in pairs:
        acc += w
        if x <= acc:
            return item
    return pairs[-1][0]


_GENERIC_WORDS = {"unspecified", "acute", "chronic", "with", "without", "other", "type", "site",
                  "specified", "organism", "primary", "initial", "encounter", "history", "personal"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())} - _GENERIC_WORDS


def claim_type_for(admission_type: Optional[str]) -> str:
    at = (admission_type or "").upper()
    return "PLANNED" if at in ("ELECTIVE", "SURGICAL SAME DAY ADMISSION") else "EMERGENCY"


def pharmacy_item_code(drug: str) -> str:
    return C.PHARMACY_ITEM_PREFIX + re.sub(r"[^A-Z0-9]+", "_", drug.upper()).strip("_")[:40]


def tariff_rows() -> List[dict]:
    rows = []
    for code in sorted(C.TARIFF_INR):
        cat, desc, price = C.TARIFF_INR[code]
        rows.append(dict(item_code=code, category=cat, description=desc, unit_price_inr=price))
    for drug in sorted(C.MEDS):
        rows.append(dict(item_code=pharmacy_item_code(drug), category="PHARMACY",
                         description=f"{drug} per day", unit_price_inr=C.MEDS[drug][3]))
    rows.append(dict(item_code="PROC-GENERIC", category="PROCEDURE",
                     description="Procedure not in tariff (flat demo rate)", unit_price_inr=25000))
    rows.append(dict(item_code="PHARM-GENERIC", category="PHARMACY",
                     description="Medication not in tariff, per day (flat demo rate)", unit_price_inr=100))
    for r in rows:
        r["data_provenance"] = PROV_TARIFF
    return rows


TARIFF_COLUMNS = ["item_code", "category", "description", "unit_price_inr", "data_provenance"]
MEMBER_COLUMNS = ["member_id", "subject_id", "policy_version_id", "sum_insured_inr", "policy_start_date",
                  "policy_end_date", "policy_cancelled_date", "insured_age_at_entry", "deductible_opted",
                  "deductible_amount_inr", "data_provenance"]
CLAIM_ADMIN_COLUMNS = ["hadm_id", "subject_id", "claim_type", "room_category", "notification_date",
                       "preauth_request_date", "claim_filed_date", "documents_submitted",
                       "bill_header_total_inr", "data_provenance"]
CHECKLIST_COLUMNS = ["hadm_id", "item", "status", "completed_at", "data_provenance"]
BILL_COLUMNS = ["hadm_id", "subject_id", "line_no", "item_code", "category", "description", "quantity",
                "unit_price_inr", "amount_inr", "service_date", "linked_icd_code", "data_provenance"]
HIST_COLUMNS = ["hist_claim_id", "drg_code", "claim_type", "los_days", "room_category", "total_billed_inr",
                "data_provenance"]
GT_ERROR_COLUMNS = ["hadm_id", "subject_id", "error_type", "category", "expected_stage", "correctable",
                    "detail", "data_provenance"]
GT_LABEL_COLUMNS = ["hadm_id", "subject_id", "scenario", "scenario_class", "policy_version_id", "n_issues",
                    "issue_types", "legitimate_outlier", "data_provenance"]


# ---------------------------------------------------------------------------
# Clinical (MIMIC-format) generation -- synthetic mode only
# ---------------------------------------------------------------------------

def _generate_clinical(rng: random.Random, n_encounters: int, base_date: date, span_days: int,
                       subject_start: int = SYNTHETIC_SUBJECT_ID_START, hadm_start: int = SYNTHETIC_HADM_ID_START,
                       allow_outliers: bool = True):
    tables = {t: [] for t in HOSP_COLUMNS}
    scenario_by_hadm = {}
    patients = []
    next_subject, next_hadm, next_transfer = subject_start, hadm_start, 39_000_001
    scen_pairs = [(s, s["weight"]) for s in C.SCENARIOS]

    for _ in range(n_encounters):
        s = dict(_weighted(rng, scen_pairs))
        s["legitimate_outlier"] = False
        reuse = None
        if patients and rng.random() < 0.15:
            cands = [p for p in patients
                     if (s["sex"] is None or p["gender"] == s["sex"]) and s["age"][0] <= p["anchor_age"] <= s["age"][1]]
            if cands:
                reuse = rng.choice(cands)
        if reuse:
            pat = reuse
            admit_day = pat["last_disch"].date() + timedelta(days=rng.randint(20, 120))
        else:
            gender = s["sex"] or rng.choice(["F", "M"])
            pat = dict(subject_id=next_subject, gender=gender, anchor_age=rng.randint(*s["age"]), last_disch=None)
            next_subject += 1
            patients.append(pat)
            tables["patients"].append(dict(subject_id=pat["subject_id"], gender=gender,
                                           anchor_age=pat["anchor_age"], anchor_year=base_date.year,
                                           anchor_year_group="SYNTHETIC", dod=""))
            admit_day = base_date + timedelta(days=rng.randint(0, span_days))

        hadm = next_hadm
        next_hadm += 1
        sid = pat["subject_id"]
        adm_type = rng.choice(s["admission_types"])
        emergency = claim_type_for(adm_type) == "EMERGENCY"
        admittime = datetime.combine(admit_day, datetime.min.time()) + timedelta(
            hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        los = rng.randint(*s["los"])
        # Legitimate outlier: a genuinely longer, complicated stay. Not an error.
        if allow_outliers and s["los"][1] >= 3 and rng.random() < P_LEGITIMATE_OUTLIER:
            s["legitimate_outlier"] = True
            los = s["los"][1] * 2 + rng.randint(1, 3)
        scenario_by_hadm[hadm] = s
        dischtime = datetime.combine(admit_day + timedelta(days=los), datetime.min.time()) + timedelta(
            hours=rng.randint(10, 17), minutes=rng.randint(0, 59))
        pat["last_disch"] = dischtime
        edreg = admittime - timedelta(hours=rng.randint(3, 6)) if emergency else None

        if adm_type in ("EW EMER.", "EU OBSERVATION", "OBSERVATION ADMIT"):
            adm_loc = "EMERGENCY ROOM"
        elif adm_type == "URGENT":
            adm_loc = rng.choice(["TRANSFER FROM HOSPITAL", "PHYSICIAN REFERRAL", "WALK-IN/SELF REFERRAL"])
        else:
            adm_loc = "PHYSICIAN REFERRAL"
        if pat["anchor_age"] >= 75:
            dis_loc = _weighted(rng, [("HOME", 40), ("HOME HEALTH CARE", 30), ("SKILLED NURSING FACILITY", 30)])
        else:
            dis_loc = _weighted(rng, [("HOME", 75), ("HOME HEALTH CARE", 20), ("SKILLED NURSING FACILITY", 5)])

        tables["admissions"].append(dict(
            subject_id=sid, hadm_id=hadm, admittime=fmt_dt(admittime), dischtime=fmt_dt(dischtime), deathtime="",
            admission_type=adm_type, admit_provider_id=f"SYN{rng.randint(100, 999)}", admission_location=adm_loc,
            discharge_location=dis_loc,
            insurance="Medicare" if pat["anchor_age"] >= 65 else rng.choice(["Medicaid", "Other"]),
            language="ENGLISH", marital_status=rng.choice(["MARRIED", "SINGLE", "WIDOWED", "DIVORCED"]),
            race="UNKNOWN", edregtime=fmt_dt(edreg), edouttime=fmt_dt(admittime if edreg else None),
            hospital_expire_flag=0,
        ))

        dx = list(s["principal"])
        pool = [c for c in s["secondary_pool"] if c not in dx]
        dx += rng.sample(pool, k=rng.randint(0, min(3, len(pool))))
        if s["legitimate_outlier"]:
            comp = [c for c in COMPLICATION_DX if c not in dx]
            if comp:
                dx.append(rng.choice(comp))
        for i, code in enumerate(dx, start=1):
            tables["diagnoses_icd"].append(dict(subject_id=sid, hadm_id=hadm, seq_num=i, icd_code=code, icd_version=10))

        procs = list(s["procedures"]) + [c for c, p in s["optional_procedures"] if rng.random() < p]
        for i, code in enumerate(procs, start=1):
            pday = admit_day + timedelta(days=min(los - 1, rng.randint(0, 1)) if los > 1 else 0)
            tables["procedures_icd"].append(dict(subject_id=sid, hadm_id=hadm, seq_num=i,
                                                 chartdate=pday.isoformat(), icd_code=code, icd_version=10))

        if s["drg"]:
            tables["drgcodes"].append(dict(subject_id=sid, hadm_id=hadm, drg_type="HCFA", drg_code=s["drg"],
                                           description=C.MS_DRG_TITLES[s["drg"]], drg_severity="", drg_mortality=""))

        def add_transfer(eventtype, unit, tin, tout):
            nonlocal next_transfer
            tables["transfers"].append(dict(subject_id=sid, hadm_id=hadm, transfer_id=next_transfer,
                                            eventtype=eventtype, careunit=unit or "",
                                            intime=fmt_dt(tin), outtime=fmt_dt(tout)))
            next_transfer += 1
        if edreg:
            add_transfer("ed", "Emergency Department", edreg, admittime)
        t, first = admittime, True
        if s["icu"] and los >= 2:
            unit, lo, hi = s["icu"]
            icu_days = min(rng.randint(lo, hi), los - 1)
            t_out = t + timedelta(days=icu_days)
            add_transfer("admit", unit, t, t_out)
            t, first = t_out, False
        add_transfer("admit" if first else "transfer", s["ward"], t, dischtime)
        add_transfer("discharge", None, dischtime, None)

        for drug in s["meds"]:
            dv, du, route, _ = C.MEDS[drug]
            tables["prescriptions"].append(dict(
                subject_id=sid, hadm_id=hadm, drug_type="MAIN", drug=drug, dose_val_rx=dv, dose_unit_rx=du,
                route=route, starttime=fmt_dt(admittime + timedelta(hours=rng.randint(1, 6))),
                stoptime=fmt_dt(dischtime)))

    for code in sorted(C.ICD10CM_TITLES):
        tables["d_icd_diagnoses"].append(dict(icd_code=code, icd_version=10, long_title=C.ICD10CM_TITLES[code],
                                              data_provenance=PROV_D_ICD_DX))
    for code in sorted(C.ICD10PCS_TITLES):
        tables["d_icd_procedures"].append(dict(icd_code=code, icd_version=10, long_title=C.ICD10PCS_TITLES[code],
                                               data_provenance=PROV_D_ICD_PX))
    for t in tables:
        if t not in ("d_icd_diagnoses", "d_icd_procedures"):
            for r in tables[t]:
                r["data_provenance"] = PROV_CLINICAL
    return tables, scenario_by_hadm


def _write_hosp(out_dir, tables):
    for t, rows in tables.items():
        write_table(os.path.join(out_dir, "hosp", f"{t}.csv"), HOSP_COLUMNS[t] + ["data_provenance"], rows)


# ---------------------------------------------------------------------------
# Discharge note
# ---------------------------------------------------------------------------

def _dx_phrase(rng, code, title):
    phrases = C.NOTE_DX_PHRASES.get(code)
    if phrases:
        return rng.choice(phrases)
    return title or code


def build_discharge_note(rec: EncounterRecord, rng: random.Random, scenario: Optional[dict],
                         extra_dx_codes=(), omit_principal=False, derived=False) -> str:
    dx = rec.diagnoses
    primary = [] if omit_principal else dx[:1]
    secondary = dx[1:]
    if omit_principal and secondary:
        primary, secondary = secondary[:1], secondary[1:]
    prim_lines = [f"- {_dx_phrase(rng, d.icd_code, d.long_title)}" for d in primary]
    sec_lines = [f"- {_dx_phrase(rng, d.icd_code, d.long_title)}" for d in secondary]
    for code in extra_dx_codes:
        sec_lines.append(f"- {_dx_phrase(rng, code, C.ICD10CM_TITLES.get(code))}")

    proc_lines = [f"- {C.PCS_NOTE_PHRASES.get(p.icd_code, p.long_title or p.icd_code)}" for p in rec.procedures]
    meds, seen = [], set()
    for m in rec.medications:
        if m.drug in seen:
            continue
        seen.add(m.drug)
        meds.append(f"{len(meds) + 1}. {m.drug} {m.dose_val_rx or ''} {m.dose_unit_rx or ''} "
                    f"{m.route or ''} DAILY".replace("  ", " ").strip())
    sex = {"F": "female", "M": "male"}.get(rec.gender or "", "patient")
    complaint = scenario["complaint"] if scenario else "___"
    service = scenario["service"] if scenario else (rec.service or "___")
    ward = scenario["ward"] if scenario else "the ward"
    course = (f"Patient was admitted to {ward} and managed per standard protocols. "
              f"Length of stay {rec.los_days if rec.los_days is not None else '___'} day(s).")
    if scenario and scenario.get("legitimate_outlier"):
        course += " Hospital course was prolonged by a documented complication requiring extended inpatient care."

    lines = [
        DERIVED_NOTE_BANNER if derived else SYNTHETIC_NOTE_BANNER,
        "Name:  ___                     Unit No:   ___",
        "Admission Date:  ___              Discharge Date:   ___",
        f"Date of Birth:  ___             Sex:   {rec.gender or '___'}",
        f"Service: {service}",
        "",
        "Chief Complaint:", complaint, "",
        "Major Surgical or Invasive Procedure:", *(proc_lines or ["None"]), "",
        "History of Present Illness:",
        f"___ is a {rec.anchor_age or '___'} year old {sex} admitted with {complaint.lower()}.", "",
        "Brief Hospital Course:", course, "",
        "Discharge Medications:", *(meds or ["None"]), "",
        "Discharge Disposition:", (rec.discharge_location or "___").title(), "",
        "Discharge Diagnosis:", "Primary:", *(prim_lines or ["___"]), "",
        "Secondary:", *(sec_lines or ["None"]), "",
        "Discharge Condition:", "Mental Status: Clear and coherent.",
        "Level of Consciousness: Alert and interactive.", "",
        "Followup Instructions:", "___",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Hospital bill
# ---------------------------------------------------------------------------

def is_icu(careunit) -> bool:
    return bool(careunit) and (careunit in C.ICU_CAREUNITS or re.search(C.ICU_CAREUNIT_PATTERN, careunit) is not None)


_TARIFF_PRICE = None


def tariff_price(item_code: str):
    """Price an item exactly as billing verification will: rate card entry, else the generic rate."""
    global _TARIFF_PRICE
    if _TARIFF_PRICE is None:
        _TARIFF_PRICE = {r["item_code"]: r["unit_price_inr"] for r in tariff_rows()}
    if item_code in _TARIFF_PRICE:
        return _TARIFF_PRICE[item_code]
    return _TARIFF_PRICE["PHARM-GENERIC" if item_code.startswith(C.PHARMACY_ITEM_PREFIX) else "PROC-GENERIC"]


def icu_days(rec: EncounterRecord) -> int:
    los = rec.los_days or 1
    total = 0
    for t in rec.transfers:
        if is_icu(t.careunit) and t.intime and t.outtime:
            total += max(1, (t.outtime.date() - t.intime.date()).days)
    return min(total, los)


def build_bill(rec: EncounterRecord, room_code: str, scenario: Optional[dict]) -> List[dict]:
    los = rec.los_days or 1
    admit_day = rec.admittime.date().isoformat()
    lines = []

    def add(item_code, qty, unit_price, service_date=admit_day, linked=""):
        if item_code in C.TARIFF_INR:
            cat, desc, _ = C.TARIFF_INR[item_code]
        elif item_code.startswith(C.PHARMACY_ITEM_PREFIX):
            cat, desc = "PHARMACY", item_code[len(C.PHARMACY_ITEM_PREFIX):].replace("_", " ").title()
        else:
            cat, desc = "PROCEDURE", f"Procedure {linked}"
        lines.append(dict(item_code=item_code, category=cat, description=desc, quantity=qty,
                          unit_price_inr=unit_price, amount_inr=qty * unit_price,
                          service_date=service_date, linked_icd_code=linked))

    icu = icu_days(rec)
    ward_days = los - icu
    if claim_type_for(rec.admission_type) == "EMERGENCY":
        add("ED-VISIT", 1, C.TARIFF_INR["ED-VISIT"][2])
    if ward_days > 0:
        add(room_code, ward_days, C.TARIFF_INR[room_code][2])
    if icu > 0:
        add("ICU-DAY", icu, C.TARIFF_INR["ICU-DAY"][2])
    add("NURSING-DAY", los, C.TARIFF_INR["NURSING-DAY"][2])
    add("CONSULT-DAY", los, C.TARIFF_INR["CONSULT-DAY"][2])
    add("LAB-ADMIT", 1, C.TARIFF_INR["LAB-ADMIT"][2])
    add("LAB-DAILY", los, C.TARIFF_INR["LAB-DAILY"][2])
    for item in (scenario or {}).get("imaging", []):
        add(item, 1, C.TARIFF_INR[item][2])
    for item in (scenario or {}).get("extra_bill_items", []):
        add(item, 1, C.TARIFF_INR[item][2])
    for p in rec.procedures:
        code = f"PROC-{p.icd_code}"
        price = tariff_price(code)
        add(code, 1, price, (p.chartdate or rec.admittime.date()).isoformat(), p.icd_code)
    seen = set()
    for m in rec.medications:
        code = pharmacy_item_code(m.drug)
        if code in seen:
            continue
        seen.add(code)
        price = tariff_price(code)  # case-insensitive via the item code (real MIMIC writes e.g. 'LORazepam')
        days = los
        if m.starttime and m.stoptime:
            days = max(1, (m.stoptime.date() - m.starttime.date()).days)
        add(code, days, price)
    for i, l in enumerate(lines, start=1):
        l["line_no"] = i
    return lines


# ---------------------------------------------------------------------------
# Fault planning / injection (each injection records the correction that undoes it)
# ---------------------------------------------------------------------------

def _plan_errors(rng, rec: EncounterRecord, error_rate: float, allow_clinical: bool) -> List[str]:
    if rng.random() >= error_rate:
        return []
    allowed = {k: w for k, w in INJECTABLE_WEIGHTS.items() if allow_clinical or k not in CLINICAL_ERROR_TYPES}
    if not rec.procedures:
        for k in ("PROCEDURE_DATE_OUT_OF_RANGE", "MISSING_CHARGE"):
            allowed.pop(k, None)
    if not rec.diagnoses:
        for k in ("CODED_DX_NOT_IN_NOTE", "INVALID_ICD_CODE"):
            allowed.pop(k, None)
    if (rec.gender or "") not in ("F", "M"):
        allowed.pop("GENDER_DX_CONFLICT", None)
    if rec.dischtime is None:
        allowed.pop("MISSING_DISCHARGE_TIME", None)
        allowed.pop("LOS_BILLING_MISMATCH", None)
    k = 1 if rng.random() < 0.7 else 2
    chosen = []
    for _ in range(k):
        pairs = [(c, w) for c, w in sorted(allowed.items())
                 if c not in chosen and not any({c, x} <= s for x in chosen for s in INCOMPATIBLE)]
        if not pairs:
            break
        chosen.append(_weighted(rng, pairs))
    return sorted(chosen)


def _apply_bill_errors(rng, lines, errors, rec, details, corrections):
    """Mutates lines in place. Returns the header-total delta for RECONCILIATION_RESIDUAL (0 if none)."""
    room_items = {"ROOM-GW", "ROOM-SP", "ROOM-PVT", "ICU-DAY"}
    touched = set()

    # MISSING_CHARGE first: removing a line renumbers the bill before other faults reference line_nos.
    if "MISSING_CHARGE" in errors:
        procs = [l for l in lines if l["item_code"].startswith("PROC-")]
        if procs:
            victim = rng.choice(procs)
            lines.remove(victim)
            for i, l in enumerate(lines, start=1):
                l["line_no"] = i
            restored = {k: v for k, v in victim.items() if k != "line_no"}
            corrections.append(dict(error_type="MISSING_CHARGE", op="add_bill_line", line=restored))
            details["MISSING_CHARGE"] = f"coded procedure {victim['linked_icd_code']} has no bill line"
        else:
            errors.discard("MISSING_CHARGE")

    def pick(pred):
        cands = [l for l in lines if pred(l) and l["line_no"] not in touched]
        return rng.choice(cands) if cands else None

    for err in sorted(errors & (BILL_ERROR_TYPES - {"MISSING_CHARGE", "RECONCILIATION_RESIDUAL"})):
        if err == "LOS_BILLING_MISMATCH":
            l = pick(lambda l: l["item_code"] in room_items)
            if l is None:
                errors.discard(err)
                continue
            orig_q, orig_a = l["quantity"], l["amount_inr"]
            l["quantity"] += rng.randint(2, 4)
            l["amount_inr"] = l["quantity"] * l["unit_price_inr"]
            touched.add(l["line_no"])
            bed_days = sum(x["quantity"] for x in lines if x["item_code"] in room_items)
            details[err] = f"{bed_days} room/ICU day(s) billed; length of stay is {rec.los_days}"
            corrections.append(dict(error_type=err, op="update_bill_line", line_no=l["line_no"],
                                    fields=dict(quantity=orig_q, amount_inr=orig_a)))
        elif err == "BILLED_PROCEDURE_NOT_DOCUMENTED":
            coded = {p.icd_code for p in rec.procedures}
            options = sorted(k[5:] for k in C.TARIFF_INR if k.startswith("PROC-") and k[5:] not in coded)
            code = rng.choice(options)
            price = C.TARIFF_INR[f"PROC-{code}"][2]
            n = len(lines) + 1
            lines.append(dict(item_code=f"PROC-{code}", category="PROCEDURE",
                              description=C.TARIFF_INR[f"PROC-{code}"][1], quantity=1, unit_price_inr=price,
                              amount_inr=price, service_date=rec.admittime.date().isoformat(),
                              linked_icd_code=code, line_no=n))
            touched.add(n)
            details[err] = f"bill line PROC-{code} has no matching coded procedure"
            corrections.append(dict(error_type=err, op="delete_bill_line", line_no=n))
        elif err == "DUPLICATE_BILL_ITEM":
            l = pick(lambda l: l["quantity"] == 1 and l["item_code"] not in room_items)
            if l is None:
                errors.discard(err)
                continue
            dup = dict(l)
            dup["line_no"] = len(lines) + 1
            lines.append(dup)
            touched.update({l["line_no"], dup["line_no"]})
            details[err] = f"{l['item_code']} on {l['service_date']} billed twice"
            corrections.append(dict(error_type=err, op="delete_bill_line", line_no=dup["line_no"]))
        elif err == "BILL_ARITHMETIC_ERROR":
            l = pick(lambda l: l["item_code"] not in room_items)
            if l is None:
                errors.discard(err)
                continue
            orig_a = l["amount_inr"]
            l["amount_inr"] += rng.choice([1000, 2500, 5000, 9000])
            touched.add(l["line_no"])
            details[err] = (f"line {l['line_no']} {l['item_code']}: amount {l['amount_inr']} != "
                            f"{l['quantity']} x {l['unit_price_inr']}")
            corrections.append(dict(error_type=err, op="update_bill_line", line_no=l["line_no"],
                                    fields=dict(amount_inr=orig_a)))
        elif err == "UNIT_PRICE_ABOVE_TARIFF":
            l = pick(lambda l: l["item_code"] not in room_items and l["item_code"] in C.TARIFF_INR)
            if l is None:
                errors.discard(err)
                continue
            orig_p, orig_a = l["unit_price_inr"], l["amount_inr"]
            l["unit_price_inr"] = int(round(orig_p * rng.choice([1.2, 1.35, 1.5]), -1))
            l["amount_inr"] = l["quantity"] * l["unit_price_inr"]
            touched.add(l["line_no"])
            details[err] = f"{l['item_code']} unit price {l['unit_price_inr']} vs rate card {orig_p}"
            corrections.append(dict(error_type=err, op="update_bill_line", line_no=l["line_no"],
                                    fields=dict(unit_price_inr=orig_p, amount_inr=orig_a)))

    delta = 0
    if "RECONCILIATION_RESIDUAL" in errors:
        delta = rng.choice([-7500, -1200, 850, 4300, 12000])
        details["RECONCILIATION_RESIDUAL"] = f"bill header total differs from sum of lines by {delta}"
        corrections.append(dict(error_type="RECONCILIATION_RESIDUAL", op="set_bill_header_total",
                                value="SUM_OF_LINES"))
    return delta


def _apply_clinical_errors(rng, tables, rec: EncounterRecord, errors, details, corrections):
    hid = rec.hadm_id
    if "INVALID_ICD_CODE" in errors:
        rows = [r for r in tables["diagnoses_icd"] if r["hadm_id"] == hid]
        # Truncate a secondary code to its 3-character category. ICD-10-CM codes are
        # billable only at full specificity, so a category with longer child codes
        # (len(code) > 3) is not itself billable.
        truncatable = [r for r in rows[1:] if len(r["icd_code"]) > 3] or [r for r in rows if len(r["icd_code"]) > 3]
        if truncatable:
            target = rng.choice(truncatable)
            new = target["icd_code"][:3]
        else:
            target = rows[-1]
            new = rng.choice(C.NON_BILLABLE_HEADER_CODES)
        details["INVALID_ICD_CODE"] = f"seq {target['seq_num']}: {target['icd_code']} replaced with non-billable {new}"
        corrections.append(dict(error_type="INVALID_ICD_CODE", op="update_diagnosis", seq_num=target["seq_num"],
                                icd_code=target["icd_code"]))
        target["icd_code"] = new
    if "GENDER_DX_CONFLICT" in errors:
        code = rng.choice(C.MALE_ONLY_INJECT if rec.gender == "F" else C.FEMALE_ONLY_INJECT)
        seq = max([r["seq_num"] for r in tables["diagnoses_icd"] if r["hadm_id"] == hid] or [0]) + 1
        tables["diagnoses_icd"].append(dict(subject_id=rec.subject_id, hadm_id=hid, seq_num=seq, icd_code=code,
                                            icd_version=10, data_provenance=PROV_CLINICAL))
        details["GENDER_DX_CONFLICT"] = f"{code} coded for patient with sex {rec.gender}"
        corrections.append(dict(error_type="GENDER_DX_CONFLICT", op="delete_diagnosis", seq_num=seq))
    if "MISSING_DISCHARGE_TIME" in errors:
        for r in tables["admissions"]:
            if r["hadm_id"] == hid:
                corrections.append(dict(error_type="MISSING_DISCHARGE_TIME", op="set_discharge_time",
                                        value=r["dischtime"]))
                r["dischtime"] = ""
        details["MISSING_DISCHARGE_TIME"] = "admissions.dischtime blanked"
    if "PROCEDURE_DATE_OUT_OF_RANGE" in errors:
        r = [r for r in tables["procedures_icd"] if r["hadm_id"] == hid][0]
        new = rec.dischtime.date() + timedelta(days=rng.randint(3, 10))
        details["PROCEDURE_DATE_OUT_OF_RANGE"] = f"{r['icd_code']} chartdate {new} is after discharge"
        corrections.append(dict(error_type="PROCEDURE_DATE_OUT_OF_RANGE", op="update_procedure",
                                seq_num=r["seq_num"], chartdate=r["chartdate"]))
        r["chartdate"] = new.isoformat()


# ---------------------------------------------------------------------------
# Administrative / insurance layer (works for synthetic AND real records)
# ---------------------------------------------------------------------------

def generate_admin_layer(records: List[EncounterRecord], rng: random.Random, error_rate: float,
                         scenario_by_hadm: Optional[dict] = None, allow_clinical: bool = False,
                         note_mode: str = "synthetic", profiles: Optional[dict] = None):
    """
    note_mode: "synthetic" -> write SYNTHETIC_NOTE for every encounter
               "derived"   -> write a derived note only where the record has no real note
    Returns admin tables, notes, ground truth, corrections and the clinical
    error plan (applied by the caller in synthetic mode only).
    """
    profiles = profiles or load_policy_profiles()
    scenario_by_hadm = scenario_by_hadm or {}
    members, claim_admin, checklist, bill_rows, notes = {}, [], [], [], []
    gt_errors, gt_labels, corrections, clinical_plan = [], [], {}, {}

    last_admit = {}
    for r in records:
        if r.admittime is not None:
            last_admit[r.subject_id] = max(last_admit.get(r.subject_id, r.admittime.date()), r.admittime.date())

    for rec in sorted(records, key=lambda r: r.hadm_id):
        if rec.admittime is None:
            continue
        scenario = scenario_by_hadm.get(rec.hadm_id)
        outlier = bool(scenario and scenario.get("legitimate_outlier"))
        errors = set() if outlier else set(_plan_errors(rng, rec, error_rate, allow_clinical))
        details: Dict[str, str] = {}
        enc_corrections: List[dict] = []

        # --- member policy (one per subject, anchored on its first admission) ---
        if rec.subject_id not in members:
            pv = _weighted(rng, POLICY_VERSION_WEIGHTS)
            if rng.random() < P_SHORT_POLICY_TENURE and not outlier:
                tenure_days = rng.choice([rng.randint(3, 28), rng.randint(40, 1000)])
            else:
                tenure_days = rng.randint(37 * 30, 96 * 30)
            start = rec.admittime.date() - timedelta(days=tenure_days)
            years_elapsed = tenure_days // 365
            end = start + timedelta(days=365 * (years_elapsed + 2))
            # policies renew annually: keep cover through the subject's last admission (no RNG used)
            end = max(end, last_admit[rec.subject_id] + timedelta(days=180))
            cancelled = ""
            if rng.random() < P_POLICY_CANCELLED and not outlier:
                cancelled = (rec.admittime.date() - timedelta(days=rng.randint(5, 60))).isoformat()
            deductible = pv == "star_assure_2026_v1" and rng.random() < 0.10
            members[rec.subject_id] = dict(
                member_id=f"SYN-MEM-{rec.subject_id}", subject_id=rec.subject_id, policy_version_id=pv,
                sum_insured_inr=rng.choice(SUM_INSURED_CHOICES[pv]), policy_start_date=start.isoformat(),
                policy_end_date=end.isoformat(), policy_cancelled_date=cancelled,
                insured_age_at_entry=max(0, (rec.anchor_age or 40) - years_elapsed),
                deductible_opted=str(deductible).lower(),
                deductible_amount_inr=rng.choice([50000, 100000]) if deductible else "",
                data_provenance=PROV_ADMIN)
        member = members[rec.subject_id]
        pv = member["policy_version_id"]
        prof = profiles.get(pv, {})
        active = bool(prof.get("active_rule_ids"))

        # --- claim admin dates ---
        ctype = claim_type_for(rec.admission_type)
        admit_day = rec.admittime.date()
        disch_day = rec.dischtime.date() if rec.dischtime else admit_day
        late_notify = ctype == "EMERGENCY" and rng.random() < P_LATE_NOTIFICATION and not outlier
        short_preauth = ctype == "PLANNED" and rng.random() < P_PREAUTH_SHORT and not outlier
        late_filing = rng.random() < P_LATE_FILING and not outlier
        if ctype == "PLANNED":
            preauth = admit_day - timedelta(days=rng.randint(0, 1) if short_preauth else rng.randint(3, 10))
            notification = preauth
        else:
            preauth = admit_day
            notification = admit_day + timedelta(days=rng.randint(2, 4) if late_notify else rng.choice([0, 0, 1]))
        filed = disch_day + timedelta(days=rng.randint(32, 45) if late_filing else rng.randint(1, 7))
        room_code = _weighted(rng, ROOM_CHOICES)

        # --- hospital bill ---
        lines = build_bill(rec, room_code, scenario)
        clean_total = sum(l["amount_inr"] for l in lines)   # the true bill, before any injected fault
        delta = _apply_bill_errors(rng, lines, errors, rec, details, enc_corrections)
        lines_total = sum(l["amount_inr"] for l in lines)
        header_total = lines_total + delta

        # --- documents and checklist ---
        docs = list(BASE_DOCUMENTS)
        kyc_threshold = prof.get("kyc_threshold") if active else None
        # KYC is required by the TRUE bill (v2.2.0): deciding it on the faulted bill created answer-key
        # errors when a fault moved the total across the threshold (found in the MIMIC error analysis).
        kyc_applicable = kyc_threshold is not None and clean_total > kyc_threshold
        kyc_missing = kyc_applicable and rng.random() < P_KYC_MISSING and not outlier
        if kyc_applicable and not kyc_missing:
            docs.append("KYC")
        if kyc_missing:
            enc_corrections.append(dict(error_type="MISSING_KYC_DOCUMENT", op="add_document", document="KYC"))
        if "MISSING_CLAIM_DOCUMENT" in errors:
            dropped = rng.choice(["discharge_summary", "final_bill"])
            docs.remove(dropped)
            details["MISSING_CLAIM_DOCUMENT"] = f"{dropped} not in documents_submitted"
            enc_corrections.append(dict(error_type="MISSING_CLAIM_DOCUMENT", op="add_document", document=dropped))
        pending_item = None
        if "READINESS_ITEM_INCOMPLETE" in errors:
            pending_item = rng.choice(CHECKLIST_ITEMS)
            details["READINESS_ITEM_INCOMPLETE"] = f"{pending_item} still PENDING"
            enc_corrections.append(dict(error_type="READINESS_ITEM_INCOMPLETE", op="complete_checklist_item",
                                        item=pending_item))
        for i, item in enumerate(CHECKLIST_ITEMS):
            done = item != pending_item
            checklist.append(dict(hadm_id=rec.hadm_id, item=item, status="COMPLETE" if done else "PENDING",
                                  completed_at=fmt_dt((rec.dischtime or rec.admittime) - timedelta(minutes=30 * (5 - i)))
                                  if done else "", data_provenance=PROV_ADMIN))

        claim_admin.append(dict(
            hadm_id=rec.hadm_id, subject_id=rec.subject_id, claim_type=ctype, room_category=room_code,
            notification_date=notification.isoformat(), preauth_request_date=preauth.isoformat(),
            claim_filed_date=filed.isoformat(), documents_submitted=",".join(docs),
            bill_header_total_inr=header_total, data_provenance=PROV_ADMIN))
        for l in lines:
            bill_rows.append(dict(hadm_id=rec.hadm_id, subject_id=rec.subject_id, data_provenance=PROV_BILL, **l))

        # --- discharge note ---
        extra_dx = []
        if "NOTE_DX_NOT_CODED" in errors:
            coded_tokens = set().union(*[_tokens(d.long_title) for d in rec.diagnoses]) if rec.diagnoses else set()
            coded_codes = {d.icd_code for d in rec.diagnoses}
            options = [c for c in C.UNCODED_NOTE_DX_POOL
                       if c not in coded_codes and not (_tokens(C.ICD10CM_TITLES[c]) & coded_tokens)]
            if options:
                extra_dx = [rng.choice(options)]
                details["NOTE_DX_NOT_CODED"] = (f"note lists {C.ICD10CM_TITLES[extra_dx[0]]!r} ({extra_dx[0]}) "
                                                f"which is not coded")
                enc_corrections.append(dict(error_type="NOTE_DX_NOT_CODED", op="add_diagnosis",
                                            icd_code=extra_dx[0]))
            else:
                errors.discard("NOTE_DX_NOT_CODED")
        writes_note = note_mode == "synthetic" or (note_mode == "derived" and not rec.discharge_note)
        if not writes_note:
            errors -= NOTE_ERROR_TYPES  # never edit a real note
            extra_dx = []
        omit = "CODED_DX_NOT_IN_NOTE" in errors
        if omit:
            d0 = rec.diagnoses[0]
            details["CODED_DX_NOT_IN_NOTE"] = f"principal {d0.icd_code} omitted from note"
            enc_corrections.append(dict(error_type="CODED_DX_NOT_IN_NOTE", op="amend_note_add_diagnosis",
                                        text=d0.long_title or d0.icd_code))
        if writes_note:
            text = build_discharge_note(rec, rng, scenario, extra_dx, omit, derived=(note_mode == "derived"))
            notes.append(dict(note_id=f"SYN-{rec.hadm_id}-DS-1", subject_id=rec.subject_id, hadm_id=rec.hadm_id,
                              note_type="DS", note_seq=1, charttime=fmt_dt(rec.dischtime),
                              storetime=fmt_dt(rec.dischtime), text=text,
                              data_provenance=PROV_NOTE if note_mode == "synthetic" else PROV_NOTE_DERIVED))

        clin = errors & CLINICAL_ERROR_TYPES
        if clin:
            clinical_plan[rec.hadm_id] = sorted(clin)

        # --- policy-compliance labels, from the real rule thresholds ---
        policy_issues = {}
        if member["policy_cancelled_date"] and admit_day >= date.fromisoformat(member["policy_cancelled_date"]):
            policy_issues["POLICY_INACTIVE"] = f"cancelled {member['policy_cancelled_date']}, admitted {admit_day}"
        if active:
            tenure = (admit_day - date.fromisoformat(member["policy_start_date"])).days
            if prof.get("waiting_days") and tenure < prof["waiting_days"]:
                policy_issues["WAITING_PERIOD_NOT_MET"] = f"tenure {tenure} days < {int(prof['waiting_days'])} days"
            if (prof.get("filing_days") and "MISSING_DISCHARGE_TIME" not in errors
                    and (filed - disch_day).days > prof["filing_days"]):
                policy_issues["LATE_FILING"] = f"filed {(filed - disch_day).days} days after discharge"
            if (ctype == "EMERGENCY" and prof.get("notify_hours")
                    and (notification - admit_day).days * 24 > prof["notify_hours"]):
                policy_issues["LATE_NOTIFICATION"] = f"notified {(notification - admit_day).days} day(s) after admission"
            if (ctype == "PLANNED" and prof.get("planned_preauth_hours")
                    and (admit_day - preauth).days * 24 < prof["planned_preauth_hours"]):
                policy_issues["PREAUTH_LEAD_TIME_SHORT"] = f"preauth {(admit_day - preauth).days} day(s) before admission"
            if kyc_missing:
                policy_issues["MISSING_KYC_DOCUMENT"] = f"true bill {clean_total} > {int(kyc_threshold)} without KYC"
        for code in sorted(policy_issues):
            if code in REVIEW_RESOLUTION:
                enc_corrections.append(dict(error_type=code, op="review_decision", decision=REVIEW_RESOLUTION[code]))

        all_issues = {**{e: details.get(e, "") for e in errors}, **policy_issues}
        for code in sorted(all_issues):
            cat, stage, _domain, correctable, _ = ERROR_TYPES[code]
            gt_errors.append(dict(hadm_id=rec.hadm_id, subject_id=rec.subject_id, error_type=code, category=cat,
                                  expected_stage=stage, correctable=str(correctable).lower(),
                                  detail=all_issues[code], data_provenance=PROV_GT))
        cats = {ERROR_TYPES[c][0] for c in all_issues}
        if outlier:
            sclass = "legitimate_outlier"
        elif not all_issues:
            sclass = "normal"
        elif "missing_information" in cats:
            sclass = "missing_information"
        elif len(all_issues) == 1:
            sclass = "single_fault"
        else:
            sclass = "multi_fault"
        gt_labels.append(dict(hadm_id=rec.hadm_id, subject_id=rec.subject_id,
                              scenario=scenario["key"] if scenario else "REAL_RECORD", scenario_class=sclass,
                              policy_version_id=pv, n_issues=len(all_issues),
                              issue_types=";".join(sorted(all_issues)), legitimate_outlier=int(outlier),
                              data_provenance=PROV_GT))
        corrections[rec.hadm_id] = enc_corrections

    return dict(
        member_policies=[members[k] for k in sorted(members)], claim_admin=claim_admin, checklist=checklist,
        bill_items=bill_rows, notes=notes, gt_errors=gt_errors, gt_labels=gt_labels, corrections=corrections,
        clinical_plan=clinical_plan, tariff=tariff_rows(),
    )


def generate_historical_claims(seed: int, n: int = 900, base_date: date = date(2024, 1, 1)) -> List[dict]:
    """Synthetic prior claims (no injected faults) used as historical peer groups."""
    rng = random.Random(f"{seed}-historical")
    tables, scen = _generate_clinical(rng, n, base_date, 700, subject_start=95_000_001, hadm_start=97_000_001,
                                      allow_outliers=False)
    recs = build_records(tables, PROV_HIST)
    rows = []
    for i, rec in enumerate(recs, start=1):
        room = _weighted(rng, ROOM_CHOICES)
        total = sum(l["amount_inr"] for l in build_bill(rec, room, scen[rec.hadm_id]))
        # +/-10% price noise so peer distributions are not degenerate
        total = int(round(total * rng.uniform(0.9, 1.1), -1))
        rows.append(dict(hist_claim_id=f"SYN-HIST-{i:05d}", drg_code=rec.drg_code or "",
                         claim_type=claim_type_for(rec.admission_type), los_days=rec.los_days,
                         room_category=room, total_billed_inr=total, data_provenance=PROV_HIST))
    return rows


def _write_admin(out_dir, admin, historical=None):
    write_table(os.path.join(out_dir, "admin", "member_policies.csv"), MEMBER_COLUMNS, admin["member_policies"])
    write_table(os.path.join(out_dir, "admin", "claim_admin.csv"), CLAIM_ADMIN_COLUMNS, admin["claim_admin"])
    write_table(os.path.join(out_dir, "admin", "discharge_checklist.csv"), CHECKLIST_COLUMNS, admin["checklist"])
    write_table(os.path.join(out_dir, "admin", "hospital_bill_items.csv"), BILL_COLUMNS, admin["bill_items"])
    write_table(os.path.join(out_dir, "admin", "tariff_inr.csv"), TARIFF_COLUMNS, admin["tariff"])
    if historical is not None:
        write_table(os.path.join(out_dir, "admin", "historical_claims.csv"), HIST_COLUMNS, historical)
    write_table(os.path.join(out_dir, "ground_truth", "injected_errors.csv"), GT_ERROR_COLUMNS, admin["gt_errors"])
    write_table(os.path.join(out_dir, "ground_truth", "encounter_labels.csv"), GT_LABEL_COLUMNS, admin["gt_labels"])
    with open(os.path.join(out_dir, "ground_truth", "corrections.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({str(k): v for k, v in sorted(admin["corrections"].items())}, f, indent=1, sort_keys=True)
        f.write("\n")
    if admin["notes"]:
        write_table(os.path.join(out_dir, "note", "discharge.csv"), NOTE_COLUMNS["discharge"] + ["data_provenance"],
                    admin["notes"])


def write_manifest(out_dir, params: dict, mode: str):
    files = {}
    for dirpath, _, filenames in os.walk(out_dir):
        for fn in sorted(filenames):
            if not (fn.endswith(".csv") or fn == "corrections.json"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, out_dir).replace(os.sep, "/")
            with open(p, "rb") as f:
                data = f.read()
            n_rows = (sum(1 for _ in csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
                      if fn.endswith(".csv") else len(json.loads(data)))
            files[rel] = dict(sha256=hashlib.sha256(data).hexdigest(), rows=n_rows)
    manifest = dict(
        generator=GENERATOR_NAME, generator_version=GENERATOR_VERSION, mode=mode, parameters=params,
        provenance_notice=("ALL ROWS IN THIS DIRECTORY ARE SYNTHETIC unless stated otherwise in 'mode'. "
                           "They are not MIMIC-IV data, not real patients, not real hospital prices and "
                           "not real insurance claims."),
        ground_truth_notice="ground_truth/ is for offline evaluation only and must never be loaded by the runtime.",
        unverified_catalogs=["hosp/d_icd_procedures.csv (ICD-10-PCS titles)", "hosp/drgcodes.csv (MS-DRG titles)"]
        if mode == "synthetic_full" else [],
        files=dict(sorted(files.items())),
    )
    with open(os.path.join(out_dir, "MANIFEST.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def generate_synthetic_dataset(out_dir: str, seed: int = 42, n_encounters: int = 200, error_rate: float = 0.35,
                               base_date: date = date(2026, 1, 5), span_days: int = 240) -> dict:
    rng_clin = random.Random(f"{seed}-clinical")
    rng_admin = random.Random(f"{seed}-admin")
    rng_inject = random.Random(f"{seed}-inject")

    tables, scenario_by_hadm = _generate_clinical(rng_clin, n_encounters, base_date, span_days)
    records = build_records(tables, PROV_CLINICAL)
    admin = generate_admin_layer(records, rng_admin, error_rate, scenario_by_hadm, allow_clinical=True,
                                 note_mode="synthetic")

    gt_by_key = {(g["hadm_id"], g["error_type"]): g for g in admin["gt_errors"]}
    rec_by_hadm = {r.hadm_id: r for r in records}
    for hid in sorted(admin["clinical_plan"]):
        details = {}
        _apply_clinical_errors(rng_inject, tables, rec_by_hadm[hid], set(admin["clinical_plan"][hid]), details,
                               admin["corrections"][hid])
        for code, det in details.items():
            gt_by_key[(hid, code)]["detail"] = det
    _write_hosp(out_dir, tables)
    _write_admin(out_dir, admin, generate_historical_claims(seed))
    params = dict(seed=seed, n_encounters=n_encounters, error_rate=error_rate,
                  base_date=base_date.isoformat(), span_days=span_days)
    return write_manifest(out_dir, params, mode="synthetic_full")


def generate_admin_layer_for_mimic(mimic_root: str, out_dir: str, seed: int = 42, error_rate: float = 0.35,
                                   source_label: str = "MIMIC_IV_DEMO_2.2",
                                   max_encounters: Optional[int] = None) -> dict:
    records = load_mimic_dir(mimic_root, source_label=source_label, max_encounters=max_encounters)
    rng_admin = random.Random(f"{seed}-admin")
    admin = generate_admin_layer(records, rng_admin, error_rate, None, allow_clinical=False, note_mode="derived")
    _write_admin(out_dir, admin, generate_historical_claims(seed))
    params = dict(seed=seed, error_rate=error_rate, clinical_source=source_label,
                  max_encounters=max_encounters)
    return write_manifest(out_dir, params, mode="admin_layer_for_real_mimic")
