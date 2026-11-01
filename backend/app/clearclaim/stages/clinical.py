"""
Stages: ingestion, data_validation, discharge_readiness.

Administrative only. Nothing here assesses clinical fitness for discharge.
"""
import re
from datetime import date, datetime

from app.clearclaim.findings import finding


def _dt(s):
    return datetime.fromisoformat(s) if s else None


def _date(s):
    return date.fromisoformat(s[:10]) if s else None


# ---------------------------------------------------------------------------
# ingestion
# ---------------------------------------------------------------------------

def run_ingestion(inp, up, ctx):
    f = []
    enc = inp["encounter"]
    if not inp["bill"]["lines"]:
        f.append(finding("MISSING_SOURCE_RECORD", "BLOCKING", "No hospital bill lines were imported for this encounter.",
                         record="bill"))
    if inp["claim_admin"] is None:
        f.append(finding("MISSING_SOURCE_RECORD", "BLOCKING", "No claim administration record for this encounter.",
                         record="claim_admin"))
    return dict(data=dict(hadm_id=enc["hadm_id"], subject_id=enc["subject_id"], data_source=enc["data_source"],
                          n_diagnoses=len(inp["diagnoses"]), n_procedures=len(inp["procedures"]),
                          n_bill_lines=len(inp["bill"]["lines"]), has_note=inp["discharge_note"] is not None,
                          has_policy=inp["policy"] is not None),
                findings=f)


# ---------------------------------------------------------------------------
# data_validation
# ---------------------------------------------------------------------------

# ICD-10-CM chapter blocks for sex-specific codes:
#   N40-N53 diseases of male genital organs, C60-C63 male genital neoplasms;
#   N70-N98 female pelvic/genital disorders, C51-C58 female genital neoplasms,
#   O00-O9A pregnancy and childbirth, Z33-Z39 encounters related to reproduction.
def _in_block(code, letter, lo, hi):
    if not code.startswith(letter):
        return False
    try:
        n = int(code[1:3])
    except ValueError:
        return False
    return lo <= n <= hi


def sex_specificity(code: str):
    c = code.upper()
    if _in_block(c, "N", 40, 53) or _in_block(c, "C", 60, 63):
        return "M"
    if (c.startswith("O") or _in_block(c, "N", 70, 98) or _in_block(c, "C", 51, 58)
            or _in_block(c, "Z", 33, 39)):
        return "F"
    return None


_ABBREV = {
    "nstemi": "non st elevation myocardial infarction", "stemi": "st elevation myocardial infarction",
    "chf": "congestive heart failure", "htn": "hypertension", "hld": "hyperlipidemia",
    "t2dm": "type 2 diabetes mellitus", "dm2": "type 2 diabetes mellitus", "cad": "coronary artery disease",
    "afib": "atrial fibrillation", "gerd": "gastro esophageal reflux disease", "osa": "obstructive sleep apnea",
    "aki": "acute kidney failure", "uti": "urinary tract infection", "bph": "benign prostatic hyperplasia",
    "copd": "chronic obstructive pulmonary disease", "tia": "transient ischemic attack", "ckd": "chronic kidney disease",
}
# Common clinical synonyms between note language and ICD-10-CM titles.
_SYNONYMS = [
    ("former smoker", "history of nicotine dependence"),
    ("ischemic stroke", "cerebral infarction"),
    ("stroke", "cerebral infarction"),
    ("blood loss anemia", "posthemorrhagic anemia"),
    ("kidney injury", "kidney failure"),
    ("gastroesophageal", "gastro esophageal"),
    ("cesarean delivery", "cesarean delivery"),
    ("vaginal delivery", "full term uncomplicated delivery"),
]
_STOP = {"with", "without", "unspecified", "other", "of", "the", "and", "or", "site", "not", "specified",
         "organism", "acute", "chronic", "initial", "encounter", "for", "type", "primary", "right", "left",
         "due", "to", "in", "on", "a", "an", "elsewhere", "classified", "status", "personal", "history"}


def _norm_tokens(text):
    t = (text or "").lower().replace("-", " ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [(_ABBREV.get(w, w)) for w in t.split()]
    t = " " + " ".join(words) + " "
    for a, b in _SYNONYMS:
        t = t.replace(" " + a + " ", " " + b + " ")
    return {w for w in t.split() if len(w) >= 3 and w not in _STOP}


def phrase_matches_title(phrase, title, threshold=0.5) -> float:
    a, b = _norm_tokens(phrase), _norm_tokens(title)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    score = max(inter / len(a), inter / len(b))
    return score if score >= threshold else 0.0


_SECTION_HEADERS = ("discharge condition", "followup instructions", "discharge instructions",
                    "discharge disposition", "discharge medications")


def extract_note_diagnoses(text: str):
    """Return diagnosis phrases listed under 'Discharge Diagnosis:' (MIMIC-IV-Note style)."""
    if not text:
        return []
    lines = text.splitlines()
    out, inside = [], False
    for raw in lines:
        line = raw.strip()
        low = line.lower().rstrip(":")
        if low.startswith("discharge diagnosis") or low.startswith("discharge diagnoses"):
            inside = True
            continue
        if not inside:
            continue
        if any(low.startswith(h) for h in _SECTION_HEADERS):
            break
        if not line or low in ("primary", "secondary", "none", "___"):
            continue
        item = re.sub(r"^[-*\d.)\s]+", "", line).strip()
        if item and item != "___":
            out.append(item)
    return out


def run_data_validation(inp, up, ctx):
    f = []
    enc = inp["encounter"]
    admit, disch = _dt(enc["admittime"]), _dt(enc["dischtime"])
    if admit is None:
        f.append(finding("MISSING_REQUIRED_FIELD", "BLOCKING", "Admission time is missing.", field="admittime"))
    if disch is None:
        f.append(finding("MISSING_DISCHARGE_TIME", "BLOCKING",
                         "Discharge time is missing, so length of stay and filing deadlines cannot be computed.",
                         field="dischtime", expected="discharge timestamp", actual="missing"))
    if admit and disch and disch < admit:
        f.append(finding("DISCHARGE_BEFORE_ADMISSION", "BLOCKING", "Discharge time is earlier than admission time.",
                         expected=f">= {admit}", actual=str(disch)))
    for fld in ("admission_type", "gender"):
        if not enc.get(fld):
            f.append(finding("MISSING_REQUIRED_FIELD", "REVIEW", f"Encounter field '{fld}' is missing.", field=fld))

    dx = inp["diagnoses"]
    if not dx:
        f.append(finding("MISSING_REQUIRED_FIELD", "BLOCKING", "No coded diagnoses for this encounter.",
                         field="diagnoses"))
    ref = ctx.icd10 or {}
    invalid_codes = []
    for d in dx:
        if d["icd_version"] == 10 and ref and d["icd_code"] not in ref and d.get("long_title"):
            # Present in the source dataset's own code dictionary (e.g. MIMIC d_icd_diagnoses, which
            # spans 2008-2019) but not a billable code in the FY2025 ICD-10-CM list: valid for its
            # period, later expanded or retired. Advisory, never a blocking "invalid code".
            f.append(finding("ICD_CODE_VERSION_MISMATCH", "ADVISORY",
                             f"Diagnosis {d['icd_code']} ({d['long_title']}) is valid in the source code set but "
                             f"is not a billable code in the FY2025 ICD-10-CM reference; a current submission "
                             f"would need the updated, more specific code.",
                             expected="FY2025 billable ICD-10-CM code", actual=d["icd_code"], seq_num=d["seq_num"],
                             icd_code=d["icd_code"], reference="data/reference/icd10cm_codes.csv"))
        elif d["icd_version"] == 10 and ref and d["icd_code"] not in ref:
            invalid_codes.append(d["icd_code"])
            f.append(finding("INVALID_ICD_CODE", "BLOCKING",
                             f"Diagnosis {d['icd_code']} (sequence {d['seq_num']}) is neither a billable "
                             f"ICD-10-CM code in the reference list nor in the source dataset's code dictionary.",
                             expected="billable ICD-10-CM code", actual=d["icd_code"], seq_num=d["seq_num"],
                             icd_code=d["icd_code"], reference="data/reference/icd10cm_codes.csv"))
        sex = sex_specificity(d["icd_code"]) if d["icd_version"] == 10 else None
        if sex and enc.get("gender") in ("M", "F") and sex != enc["gender"]:
            f.append(finding("GENDER_DX_CONFLICT", "BLOCKING",
                             f"Diagnosis {d['icd_code']} is specific to sex {sex} but the patient is recorded as "
                             f"{enc['gender']}.", expected=f"sex {sex}", actual=enc["gender"], seq_num=d["seq_num"],
                             icd_code=d["icd_code"], rule="ICD-10-CM chapter block sex specificity"))

    admit_d, disch_d = (admit.date() if admit else None), (disch.date() if disch else None)
    # Procedures performed in the emergency department before formal admission belong to the
    # encounter, so the window opens at ED registration when it is recorded (MIMIC edregtime).
    edreg = _dt(enc.get("edregtime"))
    window_start = min(admit_d, edreg.date()) if admit_d and edreg else admit_d
    for p in inp["procedures"]:
        cd = _date(p["chartdate"])
        if cd and window_start and disch_d and not (window_start <= cd <= disch_d):
            f.append(finding("PROCEDURE_DATE_OUT_OF_RANGE", "BLOCKING",
                             f"Procedure {p['icd_code']} is dated {cd}, outside the encounter "
                             f"{window_start} to {disch_d}{' (from ED registration)' if window_start != admit_d else ''}.",
                             expected=f"{window_start}..{disch_d}", actual=str(cd),
                             seq_num=p["seq_num"], icd_code=p["icd_code"]))

    note = inp["discharge_note"]
    note_dx = []
    if note is None:
        f.append(finding("DISCHARGE_NOTE_MISSING", "REVIEW", "No discharge note is available for this encounter."))
    else:
        note_dx = extract_note_diagnoses(note["text"])
        titled = [d for d in dx if d.get("long_title")]
        for phrase in note_dx:
            best = max((phrase_matches_title(phrase, d["long_title"]) for d in titled), default=0.0)
            if best == 0.0:
                # Evidence for root-blocker attribution: invalid codes whose own ICD-10-CM category
                # contains a valid code whose title matches this phrase (i.e. the phrase is plausibly
                # the diagnosis that was mis-coded). Empty list = independent documentation gap.
                causes = sorted({c for c in invalid_codes
                                 if any(phrase_matches_title(phrase, t) for _, t in ctx.icd10_children(c))})
                f.append(finding("NOTE_DX_NOT_CODED", "REVIEW",
                                 f"The discharge note lists '{phrase}' but no coded diagnosis matches it.",
                                 expected="a coded diagnosis matching the note", actual="no match", phrase=phrase,
                                 possible_cause_codes=causes))
        if dx:
            principal = dx[0]
            if principal.get("long_title") and not any(
                    phrase_matches_title(p, principal["long_title"]) for p in note_dx):
                f.append(finding("CODED_DX_NOT_IN_NOTE", "REVIEW",
                                 f"Principal diagnosis {principal['icd_code']} ({principal['long_title']}) is not "
                                 f"documented in the discharge note's diagnosis section.",
                                 expected=principal["long_title"], actual="; ".join(note_dx),
                                 seq_num=principal["seq_num"], icd_code=principal["icd_code"]))

    los = max(1, (disch_d - admit_d).days) if admit_d and disch_d and disch_d >= admit_d else None
    return dict(data=dict(los_days=los, admit_date=str(admit_d) if admit_d else None,
                          disch_date=str(disch_d) if disch_d else None, discharge_known=disch_d is not None,
                          principal_dx=dx[0]["icd_code"] if dx else None,
                          dx_description="; ".join(d["long_title"] or d["icd_code"] for d in dx),
                          note_diagnoses=note_dx),
                findings=f)


# ---------------------------------------------------------------------------
# discharge_readiness (administrative checklist)
# ---------------------------------------------------------------------------

def run_discharge_readiness(inp, up, ctx):
    f = []
    docs = set(inp["documents"])
    for d in ctx.settings["readiness_required_documents"]:
        if d not in docs:
            f.append(finding("MISSING_CLAIM_DOCUMENT", "BLOCKING", f"Required claim document '{d}' has not been submitted.",
                             expected=d, actual="not submitted", document=d,
                             rule="ClearClaim readiness checklist (configurable; not a policy clause)"))
    status = {c["item"]: c["status"] for c in inp["checklist"]}
    for item in ctx.settings["readiness_required_checklist"]:
        if status.get(item) != "COMPLETE":
            f.append(finding("READINESS_ITEM_INCOMPLETE", "BLOCKING",
                             f"Administrative discharge checklist item {item} is {status.get(item, 'MISSING')}.",
                             expected="COMPLETE", actual=status.get(item, "MISSING"), item=item))
    return dict(data=dict(documents=sorted(docs), checklist=status, ready=not f), findings=f)
