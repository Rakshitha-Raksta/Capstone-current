"""
MIMIC-IV (hosp module) schema definitions and a loader that turns a
MIMIC-format directory into encounter records keyed on (subject_id, hadm_id).

The SAME loader reads:
  * the real MIMIC-IV Clinical Database Demo v2.2 (files are *.csv.gz), and
  * the SYNTHETIC demo produced by scripts/generate_synthetic_data.py
    (files are *.csv with an extra data_provenance column).

Column lists follow the MIMIC-IV v2.2 hosp tables and MIMIC-IV-Note
discharge table. Extra columns are tolerated and missing optional tables
are skipped, so the loader also works on MIMIC-IV v3.x.

No pandas dependency: plain csv + gzip, so the backend keeps its small
requirements.txt.
"""
import csv
import gzip
import io
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

MIMIC_SUBJECT_ID_RANGE = (10_000_000, 19_999_999)
MIMIC_HADM_ID_RANGE = (20_000_000, 29_999_999)
SYNTHETIC_SUBJECT_ID_START = 90_000_001
SYNTHETIC_HADM_ID_START = 98_000_001

HOSP_COLUMNS = {
    "patients": ["subject_id", "gender", "anchor_age", "anchor_year", "anchor_year_group", "dod"],
    "admissions": [
        "subject_id", "hadm_id", "admittime", "dischtime", "deathtime", "admission_type",
        "admit_provider_id", "admission_location", "discharge_location", "insurance", "language",
        "marital_status", "race", "edregtime", "edouttime", "hospital_expire_flag",
    ],
    "diagnoses_icd": ["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version"],
    "procedures_icd": ["subject_id", "hadm_id", "seq_num", "chartdate", "icd_code", "icd_version"],
    "drgcodes": ["subject_id", "hadm_id", "drg_type", "drg_code", "description", "drg_severity", "drg_mortality"],
    "d_icd_diagnoses": ["icd_code", "icd_version", "long_title"],
    "d_icd_procedures": ["icd_code", "icd_version", "long_title"],
    "transfers": ["subject_id", "hadm_id", "transfer_id", "eventtype", "careunit", "intime", "outtime"],
    "services": ["subject_id", "hadm_id", "transfertime", "prev_service", "curr_service"],
    "prescriptions": [
        "subject_id", "hadm_id", "pharmacy_id", "poe_id", "poe_seq", "order_provider_id", "starttime",
        "stoptime", "drug_type", "drug", "formulary_drug_cd", "gsn", "ndc", "prod_strength", "form_rx",
        "dose_val_rx", "dose_unit_rx", "form_val_disp", "form_unit_disp", "doses_per_24_hrs", "route",
    ],
}
NOTE_COLUMNS = {
    "discharge": ["note_id", "subject_id", "hadm_id", "note_type", "note_seq", "charttime", "storetime", "text"],
}
REQUIRED_HOSP_TABLES = ("patients", "admissions", "diagnoses_icd")

DT_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# File IO
# ---------------------------------------------------------------------------

def table_path(root: str, module: str, table: str) -> Optional[str]:
    """Return the path of <root>/<module>/<table>.csv(.gz), or None."""
    for ext in (".csv", ".csv.gz"):
        p = os.path.join(root, module, table + ext)
        if os.path.exists(p):
            return p
    return None


def read_table(path: str) -> List[dict]:
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_table(path: str, columns: List[str], rows: List[dict]):
    """Write rows deterministically (fixed column order, '\\n' line endings)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="raise", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def parse_dt(value) -> Optional[datetime]:
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip()
    for fmt in (DT_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unparseable datetime: {value!r}")


def parse_date(value) -> Optional[date]:
    d = parse_dt(value)
    return d.date() if d else None


def fmt_dt(d: Optional[datetime]) -> str:
    return d.strftime(DT_FMT) if d else ""


def is_synthetic_subject_id(subject_id: int) -> bool:
    return subject_id >= SYNTHETIC_SUBJECT_ID_START


# ---------------------------------------------------------------------------
# Encounter records
# ---------------------------------------------------------------------------

@dataclass
class Diagnosis:
    seq_num: int
    icd_code: str
    icd_version: int
    long_title: Optional[str]


@dataclass
class Procedure:
    seq_num: int
    icd_code: str
    icd_version: int
    chartdate: Optional[date]
    long_title: Optional[str]


@dataclass
class Transfer:
    transfer_id: str
    eventtype: str
    careunit: Optional[str]
    intime: Optional[datetime]
    outtime: Optional[datetime]


@dataclass
class Medication:
    drug: str
    dose_val_rx: Optional[str]
    dose_unit_rx: Optional[str]
    route: Optional[str]
    starttime: Optional[datetime]
    stoptime: Optional[datetime]


@dataclass
class EncounterRecord:
    subject_id: int
    hadm_id: int
    data_source: str
    gender: Optional[str]
    anchor_age: Optional[int]
    admittime: Optional[datetime]
    dischtime: Optional[datetime]
    admission_type: Optional[str]
    admission_location: Optional[str]
    discharge_location: Optional[str]
    insurance: Optional[str]
    hospital_expire_flag: Optional[int]
    edregtime: Optional[datetime] = None
    drg_code: Optional[str] = None
    drg_type: Optional[str] = None
    drg_description: Optional[str] = None
    diagnoses: List[Diagnosis] = field(default_factory=list)
    procedures: List[Procedure] = field(default_factory=list)
    transfers: List[Transfer] = field(default_factory=list)
    medications: List[Medication] = field(default_factory=list)
    service: Optional[str] = None
    discharge_note: Optional[str] = None
    discharge_note_provenance: Optional[str] = None

    @property
    def key(self):
        return (self.subject_id, self.hadm_id)

    @property
    def los_days(self) -> Optional[int]:
        """Billable days (calendar-day difference, minimum 1)."""
        if not self.admittime or not self.dischtime:
            return None
        return max(1, (self.dischtime.date() - self.admittime.date()).days)


def _int(v):
    return int(v) if v not in (None, "") else None


def _provenance(row, default_label):
    return row.get("data_provenance") or default_label


def load_mimic_dir(root: str, source_label: str, hadm_ids: Optional[set] = None,
                   max_encounters: Optional[int] = None) -> List[EncounterRecord]:
    """
    Load a MIMIC-format directory (<root>/hosp/*.csv[.gz], optional
    <root>/note/discharge.csv[.gz]) into EncounterRecords sorted by hadm_id.

    source_label: provenance label used when a row has no data_provenance
                  column, e.g. "MIMIC_IV_DEMO_2.2".
    """
    missing = [t for t in REQUIRED_HOSP_TABLES if table_path(root, "hosp", t) is None]
    if missing:
        raise FileNotFoundError(
            f"MIMIC-format directory {root!r} is missing required hosp table(s): {missing}. "
            f"See the MIMIC-IV section of README.md for download instructions."
        )

    tables = {}
    for t in HOSP_COLUMNS:
        p = table_path(root, "hosp", t)
        tables[t] = read_table(p) if p else []
    p = table_path(root, "note", "discharge")
    tables["discharge"] = read_table(p) if p else []
    return build_records(tables, source_label, hadm_ids, max_encounters)


def build_records(tables: Dict[str, List[dict]], source_label: str, hadm_ids: Optional[set] = None,
                  max_encounters: Optional[int] = None) -> List[EncounterRecord]:
    """Build EncounterRecords from in-memory MIMIC-format rows (values may be str or int)."""
    def load(module, table):
        return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in tables.get(table, [])]

    patients = {int(r["subject_id"]): r for r in load("hosp", "patients")}
    d_dx = {(r["icd_code"].strip(), int(r["icd_version"])): r["long_title"] for r in load("hosp", "d_icd_diagnoses")}
    d_px = {(r["icd_code"].strip(), int(r["icd_version"])): r["long_title"] for r in load("hosp", "d_icd_procedures")}

    admissions = sorted(load("hosp", "admissions"), key=lambda r: int(r["hadm_id"]))
    if hadm_ids is not None:
        admissions = [a for a in admissions if int(a["hadm_id"]) in hadm_ids]
    if max_encounters is not None:
        admissions = admissions[:max_encounters]

    records: Dict[int, EncounterRecord] = {}
    for a in admissions:
        sid, hid = int(a["subject_id"]), int(a["hadm_id"])
        p = patients.get(sid, {})
        records[hid] = EncounterRecord(
            subject_id=sid, hadm_id=hid,
            data_source=_provenance(a, source_label),
            gender=p.get("gender") or None,
            anchor_age=_int(p.get("anchor_age")),
            admittime=parse_dt(a.get("admittime")),
            dischtime=parse_dt(a.get("dischtime")),
            admission_type=a.get("admission_type") or None,
            admission_location=a.get("admission_location") or None,
            discharge_location=a.get("discharge_location") or None,
            insurance=a.get("insurance") or None,
            hospital_expire_flag=_int(a.get("hospital_expire_flag")),
            edregtime=parse_dt(a.get("edregtime")),
        )

    for r in load("hosp", "diagnoses_icd"):
        rec = records.get(int(r["hadm_id"]))
        if rec is None:
            continue
        code, ver = r["icd_code"].strip(), int(r["icd_version"])
        rec.diagnoses.append(Diagnosis(int(r["seq_num"]), code, ver, d_dx.get((code, ver))))

    for r in load("hosp", "procedures_icd"):
        rec = records.get(int(r["hadm_id"]))
        if rec is None:
            continue
        code, ver = r["icd_code"].strip(), int(r["icd_version"])
        rec.procedures.append(Procedure(int(r["seq_num"]), code, ver, parse_date(r.get("chartdate")),
                                        d_px.get((code, ver))))

    for r in load("hosp", "drgcodes"):
        rec = records.get(int(r["hadm_id"]))
        if rec is None:
            continue
        # Prefer the CMS MS-DRG (drg_type HCFA) when both HCFA and APR rows exist.
        if rec.drg_code is None or r.get("drg_type") == "HCFA":
            rec.drg_code = r.get("drg_code")
            rec.drg_type = r.get("drg_type")
            rec.drg_description = r.get("description")

    for r in load("hosp", "transfers"):
        hid = r.get("hadm_id")
        if not hid:
            continue
        rec = records.get(int(float(hid)))
        if rec is None:
            continue
        rec.transfers.append(Transfer(r["transfer_id"], r["eventtype"], r.get("careunit") or None,
                                      parse_dt(r.get("intime")), parse_dt(r.get("outtime"))))

    for r in load("hosp", "prescriptions"):
        hid = r.get("hadm_id")
        if not hid:
            continue
        rec = records.get(int(float(hid)))
        if rec is None:
            continue
        rec.medications.append(Medication(r["drug"], r.get("dose_val_rx"), r.get("dose_unit_rx"),
                                          r.get("route"), parse_dt(r.get("starttime")),
                                          parse_dt(r.get("stoptime"))))

    for r in sorted(load("hosp", "services"), key=lambda r: r.get("transfertime") or ""):
        hid = r.get("hadm_id")
        rec = records.get(int(float(hid))) if hid else None
        if rec is not None and rec.service is None:
            rec.service = r.get("curr_service") or None

    for r in load("note", "discharge"):
        rec = records.get(int(r["hadm_id"]))
        if rec is None:
            continue
        rec.discharge_note = r["text"]
        rec.discharge_note_provenance = r.get("data_provenance") or source_label

    out = []
    for hid in sorted(records):
        rec = records[hid]
        rec.diagnoses.sort(key=lambda d: d.seq_num)
        rec.procedures.sort(key=lambda p: p.seq_num)
        rec.transfers.sort(key=lambda t: (t.intime or datetime.min, t.transfer_id))
        out.append(rec)
    return out
