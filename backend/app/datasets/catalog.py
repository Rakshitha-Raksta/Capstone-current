"""
Static catalogs used by the SYNTHETIC demo-data generator.

Provenance of each catalog (stated precisely, nothing more is claimed):

ICD10CM_TITLES
    Diagnosis code -> long title, copied VERBATIM from
    data/reference/icd10cm_codes.csv (USER_SUPPLIED_REFERENCE, see its
    .meta.json). tests/test_datasets.py asserts every entry still matches
    that file exactly.

ICD10PCS_TITLES, MS_DRG_TITLES
    Written from the generator author's knowledge. There is NO procedure-
    code or DRG reference file in this repository to check them against, so
    they are tagged GENERATOR_CATALOG_UNVERIFIED in the synthetic
    d_icd_procedures / drgcodes files. Once the MIMIC-IV demo is downloaded,
    scripts/validate_encounter_data.py cross-checks the PCS titles against
    its d_icd_procedures table.

TARIFF_INR
    Invented round-number prices for the demo. They are NOT real hospital
    prices and every row written from this table carries
    data_provenance=SYNTHETIC_TARIFF_NOT_REAL_PRICES.

SCENARIOS
    Synthetic admission templates. Each one is a plausible combination of
    codes, not a reproduction of any real patient.
"""

ICD10CM_TITLES = {
    "A419": "Sepsis, unspecified organism",
    "C61": "Malignant neoplasm of prostate",
    "D62": "Acute posthemorrhagic anemia",
    "D649": "Anemia, unspecified",
    "E1122": "Type 2 diabetes mellitus with diabetic chronic kidney disease",
    "E1165": "Type 2 diabetes mellitus with hyperglycemia",
    "E119": "Type 2 diabetes mellitus without complications",
    "E785": "Hyperlipidemia, unspecified",
    "E871": "Hypo-osmolality and hyponatremia",
    "E876": "Hypokalemia",
    "F10239": "Alcohol dependence with withdrawal, unspecified",
    "G4733": "Obstructive sleep apnea (adult) (pediatric)",
    "I10": "Essential (primary) hypertension",
    "I214": "Non-ST elevation (NSTEMI) myocardial infarction",
    "I2510": "Atherosclerotic heart disease of native coronary artery without angina pectoris",
    "I4891": "Unspecified atrial fibrillation",
    "I5023": "Acute on chronic systolic (congestive) heart failure",
    "I639": "Cerebral infarction, unspecified",
    "J189": "Pneumonia, unspecified organism",
    "J441": "Chronic obstructive pulmonary disease with (acute) exacerbation",
    "J9601": "Acute respiratory failure with hypoxia",
    "K219": "Gastro-esophageal reflux disease without esophagitis",
    "K3580": "Unspecified acute appendicitis",
    "K8000": "Calculus of gallbladder with acute cholecystitis without obstruction",
    "L905": "Scar conditions and fibrosis of skin",
    "M1611": "Unilateral primary osteoarthritis, right hip",
    "M1711": "Unilateral primary osteoarthritis, right knee",
    "N179": "Acute kidney failure, unspecified",
    "N390": "Urinary tract infection, site not specified",
    "N400": "Benign prostatic hyperplasia without lower urinary tract symptoms",
    "N979": "Female infertility, unspecified",
    "O80": "Encounter for full-term uncomplicated delivery",
    "O82": "Encounter for cesarean delivery without indication",
    "R079": "Chest pain, unspecified",
    "R6520": "Severe sepsis without septic shock",
    "S72001A": "Fracture of unspecified part of neck of right femur, initial encounter for closed fracture",
    "Z370": "Single live birth",
    "Z411": "Encounter for cosmetic surgery",
    "Z8673": "Personal history of transient ischemic attack (TIA), and cerebral infarction without residual deficits",
    "Z87891": "Personal history of nicotine dependence",
}

# Category-header codes that are NOT billable ICD-10-CM codes (verified absent
# from data/reference/icd10cm_codes.csv). Used to inject INVALID_ICD_CODE
# errors: a coder truncating a code to its category is a realistic mistake.
NON_BILLABLE_HEADER_CODES = ["A41", "E11", "F10", "I48", "J18", "K35", "N39", "S7200"]

ICD10PCS_TITLES = {
    "027034Z": "Dilation of Coronary Artery, One Artery with Drug-eluting Intraluminal Device, Percutaneous Approach",
    "0DTJ4ZZ": "Resection of Appendix, Percutaneous Endoscopic Approach",
    "0FT44ZZ": "Resection of Gallbladder, Percutaneous Endoscopic Approach",
    "0QS604Z": "Reposition Right Upper Femur with Internal Fixation Device, Open Approach",
    "0SR90J9": "Replacement of Right Hip Joint with Synthetic Substitute, Cemented, Open Approach",
    "0SRC0J9": "Replacement of Right Knee Joint with Synthetic Substitute, Cemented, Open Approach",
    "10D00Z1": "Extraction of Products of Conception, Low, Open Approach",
    "10E0XZZ": "Delivery of Products of Conception, External Approach",
    "30233N1": "Transfusion of Nonautologous Red Blood Cells into Peripheral Vein, Percutaneous Approach",
    "4A023N7": "Measurement of Cardiac Sampling and Pressure, Left Heart, Percutaneous Approach",
    "5A1945Z": "Respiratory Ventilation, 24-96 Consecutive Hours",
    "5A1D70Z": "Performance of Urinary Filtration, Intermittent, Less than 6 Hours Per Day",
}

MS_DRG_TITLES = {
    "065": "INTRACRANIAL HEMORRHAGE OR CEREBRAL INFARCTION WITH CC OR TPA IN 24 HRS",
    "190": "CHRONIC OBSTRUCTIVE PULMONARY DISEASE WITH MCC",
    "194": "SIMPLE PNEUMONIA AND PLEURISY WITH CC",
    "247": "PERCUTANEOUS CARDIOVASCULAR PROCEDURES WITH DRUG-ELUTING STENT WITHOUT MCC",
    "291": "HEART FAILURE AND SHOCK WITH MCC",
    "313": "CHEST PAIN",
    "343": "APPENDECTOMY WITHOUT COMPLICATED PRINCIPAL DIAG WITHOUT CC/MCC",
    "419": "LAPAROSCOPIC CHOLECYSTECTOMY WITHOUT C.D.E. WITHOUT CC/MCC",
    "470": "MAJOR HIP AND KNEE JOINT REPLACEMENT OR REATTACHMENT OF LOWER EXTREMITY WITHOUT MCC",
    "481": "HIP AND FEMUR PROCEDURES EXCEPT MAJOR JOINT WITH CC",
    "638": "DIABETES WITH CC",
    "690": "KIDNEY AND URINARY TRACT INFECTIONS WITHOUT MCC",
    "788": "CESAREAN SECTION WITHOUT STERILIZATION WITHOUT CC/MCC",
    "807": "VAGINAL DELIVERY WITHOUT STERILIZATION OR D&C WITHOUT CC/MCC",
    "871": "SEPTICEMIA OR SEVERE SEPSIS WITHOUT MV >96 HOURS WITH MCC",
    "897": "ALCOHOL, DRUG ABUSE OR DEPENDENCE WITHOUT REHABILITATION THERAPY WITHOUT MCC",
}

# Phrases a clinician might write in a discharge note for a coded diagnosis.
# The generator picks one at random so notes are not always the exact ICD title.
NOTE_DX_PHRASES = {
    "A419": ["Sepsis", "Sepsis, organism unspecified"],
    "D62": ["Acute blood loss anemia", "Acute posthemorrhagic anemia"],
    "D649": ["Anemia"],
    "E1122": ["Type 2 diabetes with diabetic CKD"],
    "E1165": ["Type 2 diabetes mellitus with hyperglycemia", "T2DM with hyperglycemia"],
    "E119": ["Type 2 diabetes mellitus", "T2DM"],
    "E785": ["Hyperlipidemia", "HLD"],
    "E871": ["Hyponatremia"],
    "E876": ["Hypokalemia"],
    "F10239": ["Alcohol withdrawal", "Alcohol dependence with withdrawal"],
    "G4733": ["Obstructive sleep apnea", "OSA"],
    "I10": ["Hypertension", "HTN", "Essential hypertension"],
    "I214": ["NSTEMI", "Non-ST elevation myocardial infarction"],
    "I2510": ["Coronary artery disease", "CAD"],
    "I4891": ["Atrial fibrillation", "Afib"],
    "I5023": ["Acute on chronic systolic heart failure", "Acute on chronic systolic CHF"],
    "I639": ["Ischemic stroke", "Cerebral infarction"],
    "J189": ["Pneumonia", "Community acquired pneumonia"],
    "J441": ["COPD exacerbation", "Chronic obstructive pulmonary disease with acute exacerbation"],
    "J9601": ["Acute hypoxic respiratory failure", "Acute respiratory failure with hypoxia"],
    "K219": ["GERD", "Gastroesophageal reflux disease"],
    "K3580": ["Acute appendicitis"],
    "K8000": ["Acute cholecystitis with cholelithiasis", "Calculus of gallbladder with acute cholecystitis"],
    "L905": ["Scar of skin", "Scar conditions of skin"],
    "M1611": ["Right hip osteoarthritis", "Primary osteoarthritis, right hip"],
    "M1711": ["Right knee osteoarthritis", "Primary osteoarthritis, right knee"],
    "N179": ["Acute kidney injury", "AKI"],
    "N390": ["Urinary tract infection", "UTI"],
    "N400": ["Benign prostatic hyperplasia", "BPH"],
    "N979": ["Female infertility"],
    "O80": ["Full-term uncomplicated delivery", "Normal spontaneous vaginal delivery at term"],
    "O82": ["Cesarean delivery", "Elective cesarean delivery"],
    "R079": ["Chest pain"],
    "R6520": ["Severe sepsis"],
    "S72001A": ["Right femoral neck fracture", "Fracture of neck of right femur"],
    "Z370": ["Single live birth"],
    "Z411": ["Cosmetic surgery"],
    "Z8673": ["History of TIA", "History of stroke without residual deficits"],
    "Z87891": ["Former smoker", "History of nicotine dependence"],
}

PCS_NOTE_PHRASES = {
    "027034Z": "Cardiac catheterization with drug-eluting stent to one coronary artery",
    "0DTJ4ZZ": "Laparoscopic appendectomy",
    "0FT44ZZ": "Laparoscopic cholecystectomy",
    "0QS604Z": "Open reduction internal fixation, right femur",
    "0SR90J9": "Right total hip arthroplasty",
    "0SRC0J9": "Right total knee arthroplasty",
    "10D00Z1": "Low transverse cesarean section",
    "10E0XZZ": "Spontaneous vaginal delivery",
    "30233N1": "Packed red blood cell transfusion",
    "4A023N7": "Left heart catheterization",
    "5A1945Z": "Mechanical ventilation",
    "5A1D70Z": "Hemodialysis",
}

# Diagnoses that can be added to a note WITHOUT being coded, to inject
# NOTE_DX_NOT_CODED errors (documentation says it, coding missed it).
UNCODED_NOTE_DX_POOL = ["N179", "E876", "I4891", "E871", "D649", "J9601"]

# Sex-specific codes used to inject GENDER_DX_CONFLICT errors.
MALE_ONLY_INJECT = ["N400", "C61"]
FEMALE_ONLY_INJECT = ["N979"]

# Medications: (drug, dose_val_rx, dose_unit_rx, route, unit_price_inr per day)
MEDS = {
    "Acetaminophen": ("650", "mg", "PO", 40),
    "Albuterol": ("2.5", "mg", "IH", 90),
    "Apixaban": ("5", "mg", "PO", 180),
    "Aspirin": ("81", "mg", "PO", 15),
    "Atorvastatin": ("80", "mg", "PO", 60),
    "Azithromycin": ("500", "mg", "PO", 120),
    "Ceftriaxone": ("1", "g", "IV", 450),
    "Clopidogrel": ("75", "mg", "PO", 50),
    "Enoxaparin": ("40", "mg", "SC", 380),
    "Folic Acid": ("1", "mg", "PO", 10),
    "Furosemide": ("40", "mg", "IV", 60),
    "Heparin": ("5000", "UNIT", "SC", 250),
    "Ibuprofen": ("600", "mg", "PO", 20),
    "Insulin": ("10", "UNIT", "SC", 300),
    "Lisinopril": ("10", "mg", "PO", 25),
    "Lorazepam": ("1", "mg", "IV", 90),
    "Metformin": ("500", "mg", "PO", 15),
    "Metoprolol Tartrate": ("25", "mg", "PO", 20),
    "Metronidazole": ("500", "mg", "IV", 150),
    "Nitrofurantoin": ("100", "mg", "PO", 45),
    "Oxycodone": ("5", "mg", "PO", 70),
    "Oxytocin": ("10", "UNIT", "IV", 200),
    "Piperacillin-Tazobactam": ("4.5", "g", "IV", 1100),
    "Potassium Chloride": ("20", "mEq", "PO", 30),
    "Prednisone": ("40", "mg", "PO", 20),
    "Sodium Chloride 0.9%": ("1000", "mL", "IV", 120),
    "Thiamine": ("100", "mg", "PO", 15),
    "Vancomycin": ("1", "g", "IV", 900),
}

# Synthetic tariff. item_code -> (category, description, unit_price_inr)
TARIFF_INR = {
    "ROOM-GW": ("ROOM", "General ward bed charge per day", 2500),
    "ROOM-SP": ("ROOM", "Semi-private room charge per day", 4500),
    "ROOM-PVT": ("ROOM", "Private room charge per day", 7500),
    "ICU-DAY": ("ICU", "Intensive care unit charge per day", 18000),
    "ED-VISIT": ("EMERGENCY", "Emergency department assessment", 1500),
    "CONSULT-DAY": ("CONSULTATION", "Treating physician visit per day", 1200),
    "NURSING-DAY": ("NURSING", "Nursing care per day", 800),
    "LAB-ADMIT": ("LAB", "Admission laboratory panel", 2500),
    "LAB-DAILY": ("LAB", "Daily laboratory monitoring", 900),
    "IMG-XRAY": ("IMAGING", "Plain radiograph", 800),
    "IMG-CT": ("IMAGING", "CT scan", 6500),
    "IMG-ECHO": ("IMAGING", "Echocardiogram", 3500),
    "OT-MINOR": ("PROCEDURE", "Minor operation theatre charges", 15000),
    "PROC-027034Z": ("PROCEDURE", "Coronary angioplasty with drug-eluting stent (single vessel)", 180000),
    "PROC-0DTJ4ZZ": ("PROCEDURE", "Laparoscopic appendectomy package", 65000),
    "PROC-0FT44ZZ": ("PROCEDURE", "Laparoscopic cholecystectomy package", 70000),
    "PROC-0QS604Z": ("PROCEDURE", "Femur fracture internal fixation", 120000),
    "PROC-0SR90J9": ("PROCEDURE", "Total hip replacement (cemented)", 280000),
    "PROC-0SRC0J9": ("PROCEDURE", "Total knee replacement (cemented)", 250000),
    "PROC-10D00Z1": ("PROCEDURE", "Lower segment cesarean section", 55000),
    "PROC-10E0XZZ": ("PROCEDURE", "Normal vaginal delivery", 30000),
    "PROC-30233N1": ("PROCEDURE", "Packed red cell transfusion", 3500),
    "PROC-4A023N7": ("PROCEDURE", "Left heart catheterization", 35000),
    "PROC-5A1945Z": ("PROCEDURE", "Mechanical ventilation 24-96 hours", 30000),
    "PROC-5A1D70Z": ("PROCEDURE", "Hemodialysis session", 4000),
}

PHARMACY_ITEM_PREFIX = "PHARM-"

ICU_CAREUNITS = {
    "Coronary Care Unit (CCU)",
    "Medical Intensive Care Unit (MICU)",
    "Surgical Intensive Care Unit (SICU)",
    "Cardiac Vascular Intensive Care Unit (CVICU)",
}
# Real MIMIC-IV care-unit names vary (e.g. "Trauma SICU (TSICU)", "Neuro Surgical Intensive Care
# Unit (Neuro SICU)", "Medical/Surgical Intensive Care Unit (MICU/SICU)"); matched by pattern.
ICU_CAREUNIT_PATTERN = r"Intensive Care Unit|\b(?:MICU|SICU|TSICU|CVICU|CCU)\b"

# Synthetic admission templates.
#   principal: list of required dx codes (first = principal)
#   secondary_pool: optional extra dx codes (0-3 drawn)
#   procedures: PCS codes always performed
#   optional_procedures: (code, probability)
#   los: (min_days, max_days)
#   admission_types: MIMIC-IV admission_type values
#   icu: (careunit, min_days, max_days) or None
#   ward: ward careunit name
#   sex: "F", "M" or None; age: (min, max)
#   imaging: tariff item codes billed once
SCENARIOS = [
    dict(key="AMI_PCI", principal=["I214"], secondary_pool=["I10", "E785", "E119", "I2510", "Z87891", "I4891"],
         procedures=["4A023N7", "027034Z"], optional_procedures=[], drg="247", los=(2, 5),
         admission_types=["EW EMER.", "URGENT"], icu=("Coronary Care Unit (CCU)", 1, 2), ward="Medicine/Cardiology",
         sex=None, age=(45, 85), meds=["Aspirin", "Clopidogrel", "Atorvastatin", "Metoprolol Tartrate", "Heparin"],
         imaging=["IMG-ECHO"], service="MEDICINE", complaint="Chest pain", weight=8),
    dict(key="PNEUMONIA", principal=["J189"], secondary_pool=["J441", "E119", "I10", "J9601", "D649", "E871"],
         procedures=[], optional_procedures=[], drg="194", los=(3, 7),
         admission_types=["EW EMER."], icu=None, ward="Medicine",
         sex=None, age=(30, 90), meds=["Ceftriaxone", "Azithromycin", "Acetaminophen"],
         imaging=["IMG-XRAY"], service="MEDICINE", complaint="Cough and fever", weight=10),
    dict(key="HEART_FAILURE", principal=["I5023"], secondary_pool=["I10", "N179", "E119", "I4891", "E876", "I2510"],
         procedures=[], optional_procedures=[], drg="291", los=(4, 8),
         admission_types=["EW EMER.", "URGENT"], icu=None, ward="Medicine/Cardiology",
         sex=None, age=(50, 90), meds=["Furosemide", "Lisinopril", "Potassium Chloride"],
         imaging=["IMG-XRAY", "IMG-ECHO"], service="MEDICINE", complaint="Shortness of breath", weight=8),
    dict(key="SEPSIS", principal=["A419", "R6520", "N390"], secondary_pool=["N179", "E119", "D649", "E871"],
         procedures=[], optional_procedures=[("5A1945Z", 0.3)], drg="871", los=(5, 10),
         admission_types=["EW EMER."], icu=("Medical Intensive Care Unit (MICU)", 2, 3), ward="Medicine",
         sex=None, age=(40, 90), meds=["Piperacillin-Tazobactam", "Vancomycin", "Sodium Chloride 0.9%"],
         imaging=["IMG-XRAY", "IMG-CT"], service="MEDICINE", complaint="Fever and confusion", weight=6),
    dict(key="UTI", principal=["N390"], secondary_pool=["E119", "I10", "E876"],
         procedures=[], optional_procedures=[], drg="690", los=(2, 4),
         admission_types=["EW EMER.", "OBSERVATION ADMIT"], icu=None, ward="Medicine",
         sex=None, age=(25, 90), meds=["Ceftriaxone", "Nitrofurantoin"],
         imaging=[], service="MEDICINE", complaint="Dysuria and fever", weight=6),
    dict(key="APPENDICITIS", principal=["K3580"], secondary_pool=["I10", "K219"],
         procedures=["0DTJ4ZZ"], optional_procedures=[], drg="343", los=(1, 3),
         admission_types=["EW EMER.", "URGENT"], icu=None, ward="Med/Surg",
         sex=None, age=(18, 60), meds=["Ceftriaxone", "Metronidazole", "Oxycodone", "Acetaminophen"],
         imaging=["IMG-CT"], service="SURGERY", complaint="Right lower quadrant abdominal pain", weight=7),
    dict(key="CHOLECYSTITIS", principal=["K8000"], secondary_pool=["E119", "I10", "K219", "E785"],
         procedures=["0FT44ZZ"], optional_procedures=[], drg="419", los=(2, 4),
         admission_types=["URGENT", "ELECTIVE"], icu=None, ward="Med/Surg",
         sex=None, age=(30, 75), meds=["Ceftriaxone", "Oxycodone", "Acetaminophen"],
         imaging=["IMG-CT"], service="SURGERY", complaint="Right upper quadrant pain", weight=6),
    dict(key="KNEE_REPLACEMENT", principal=["M1711"], secondary_pool=["I10", "E785", "E119", "G4733"],
         procedures=["0SRC0J9"], optional_procedures=[], drg="470", los=(2, 4),
         admission_types=["ELECTIVE", "SURGICAL SAME DAY ADMISSION"], icu=None, ward="Med/Surg",
         sex=None, age=(55, 85), meds=["Enoxaparin", "Oxycodone", "Acetaminophen"],
         imaging=["IMG-XRAY"], service="ORTHOPAEDICS", complaint="Right knee pain", weight=5),
    dict(key="HIP_REPLACEMENT", principal=["M1611"], secondary_pool=["I10", "E785", "E119"],
         procedures=["0SR90J9"], optional_procedures=[], drg="470", los=(2, 4),
         admission_types=["ELECTIVE", "SURGICAL SAME DAY ADMISSION"], icu=None, ward="Med/Surg",
         sex=None, age=(55, 85), meds=["Enoxaparin", "Oxycodone", "Acetaminophen"],
         imaging=["IMG-XRAY"], service="ORTHOPAEDICS", complaint="Right hip pain", weight=4),
    dict(key="HIP_FRACTURE", principal=["S72001A"], secondary_pool=["I10", "D62", "Z8673", "E871", "I4891"],
         procedures=["0QS604Z"], optional_procedures=[("30233N1", 0.4)], drg="481", los=(4, 8),
         admission_types=["EW EMER."], icu=None, ward="Med/Surg/Trauma",
         sex=None, age=(65, 95), meds=["Enoxaparin", "Oxycodone", "Acetaminophen"],
         imaging=["IMG-XRAY", "IMG-CT"], service="ORTHOPAEDICS", complaint="Fall with right hip pain", weight=4),
    dict(key="STROKE", principal=["I639"], secondary_pool=["I10", "I4891", "E785", "E119"],
         procedures=[], optional_procedures=[], drg="065", los=(3, 7),
         admission_types=["EW EMER."], icu=None, ward="Neurology",
         sex=None, age=(50, 90), meds=["Aspirin", "Atorvastatin", "Apixaban"],
         imaging=["IMG-CT", "IMG-ECHO"], service="NEUROLOGY", complaint="Left sided weakness", weight=5),
    dict(key="CESAREAN", principal=["O82", "Z370"], secondary_pool=[],
         procedures=["10D00Z1"], optional_procedures=[], drg="788", los=(2, 4),
         admission_types=["ELECTIVE", "URGENT"], icu=None, ward="Obstetrics (Postpartum & Antepartum)",
         sex="F", age=(20, 40), meds=["Oxytocin", "Ibuprofen", "Acetaminophen"],
         imaging=[], service="OBSTETRICS/GYNECOLOGY", complaint="Scheduled cesarean delivery", weight=4),
    dict(key="VAGINAL_DELIVERY", principal=["O80", "Z370"], secondary_pool=[],
         procedures=["10E0XZZ"], optional_procedures=[], drg="807", los=(1, 3),
         admission_types=["URGENT"], icu=None, ward="Labor & Delivery",
         sex="F", age=(19, 40), meds=["Oxytocin", "Ibuprofen"],
         imaging=[], service="OBSTETRICS/GYNECOLOGY", complaint="Labor", weight=4),
    dict(key="ALCOHOL_WITHDRAWAL", principal=["F10239"], secondary_pool=["K219", "E876", "D649"],
         procedures=[], optional_procedures=[], drg="897", los=(2, 5),
         admission_types=["EW EMER."], icu=None, ward="Medicine",
         sex=None, age=(25, 70), meds=["Lorazepam", "Thiamine", "Folic Acid"],
         imaging=[], service="MEDICINE", complaint="Tremors and agitation", weight=3),
    dict(key="COSMETIC_SCAR_REVISION", principal=["Z411", "L905"], secondary_pool=[],
         procedures=[], optional_procedures=[], drg=None, los=(1, 1),
         admission_types=["SURGICAL SAME DAY ADMISSION"], icu=None, ward="Med/Surg",
         sex=None, age=(20, 60), meds=["Acetaminophen"],
         imaging=[], service="PLASTIC", complaint="Elective scar revision", weight=2,
         extra_bill_items=["OT-MINOR"]),
    dict(key="COPD_EXACERBATION", principal=["J441"], secondary_pool=["J9601", "I10", "Z87891"],
         procedures=[], optional_procedures=[], drg="190", los=(3, 6),
         admission_types=["EW EMER."], icu=None, ward="Medicine",
         sex=None, age=(50, 90), meds=["Prednisone", "Albuterol", "Azithromycin"],
         imaging=["IMG-XRAY"], service="MEDICINE", complaint="Worsening shortness of breath", weight=5),
    dict(key="DIABETES_HYPERGLYCEMIA", principal=["E1165"], secondary_pool=["I10", "E785", "E1122", "E876"],
         procedures=[], optional_procedures=[], drg="638", los=(2, 4),
         admission_types=["EW EMER.", "URGENT"], icu=None, ward="Medicine",
         sex=None, age=(30, 85), meds=["Insulin", "Metformin"],
         imaging=[], service="MEDICINE", complaint="Hyperglycemia", weight=5),
    dict(key="CHEST_PAIN_OBS", principal=["R079"], secondary_pool=["I10", "E785", "K219"],
         procedures=[], optional_procedures=[], drg="313", los=(1, 2),
         admission_types=["EU OBSERVATION", "OBSERVATION ADMIT"], icu=None, ward="Medicine",
         sex=None, age=(30, 80), meds=["Aspirin"],
         imaging=["IMG-XRAY"], service="MEDICINE", complaint="Chest pain", weight=5),
]
