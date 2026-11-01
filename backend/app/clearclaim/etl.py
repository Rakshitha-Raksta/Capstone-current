"""
Import an encounter dataset into the ClearClaim tables.

Reads MIMIC-format clinical tables (synthetic demo, or real MIMIC-IV) plus
the synthetic admin layer. It NEVER reads ground_truth/: evaluation labels
must not influence runtime decisions.

Idempotent: encounters whose hadm_id already exists are skipped.
"""
import csv
import hashlib
import json
import os
from datetime import date, datetime

from app.clearclaim.context import invalidate_context
from app.clearclaim.models import (
    BillLine, ChecklistItem, ClaimAdmin, ClaimCase, DatasetImport, DischargeNote, Encounter, EncounterDiagnosis,
    EncounterMedication, EncounterProcedure, EncounterTransfer, HistoricalClaim, MemberPolicy, RateCardItem,
)
from app.clearclaim.orchestrator import audit
from app.datasets.mimic import load_mimic_dir
from app.datasets.validate import validate_dataset


def _rows(admin_root, name):
    p = os.path.join(admin_root, "admin", name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _d(s):
    return date.fromisoformat(s) if s else None


def _dt(s):
    return datetime.fromisoformat(s) if s else None


def _f(s):
    return float(s) if s not in (None, "") else None


def import_dataset(db, clinical_root: str, admin_root: str, mode: str, source_label: str,
                   max_encounters=None, actor="system") -> DatasetImport:
    assert mode in ("demo", "mimic")
    report = validate_dataset(clinical_root, admin_root, include_ground_truth=False)
    man_path = os.path.join(admin_root, "MANIFEST.json")
    manifest, man_sha = {}, None
    if os.path.exists(man_path):
        raw = open(man_path, "rb").read()
        man_sha = hashlib.sha256(raw).hexdigest()
        manifest = json.loads(raw)
    imp = DatasetImport(mode=mode, source_label=source_label, clinical_root=os.path.abspath(clinical_root),
                        admin_root=os.path.abspath(admin_root), manifest_mode=manifest.get("mode"),
                        manifest_sha256=man_sha, generator_version=manifest.get("generator_version"),
                        parameters=manifest.get("parameters"), validation_report=report)
    db.add(imp)
    db.flush()
    if report["errors"]:
        imp.status = "FAILED"
        audit(db, "DATASET_IMPORT_FAILED", f"{len(report['errors'])} validation error(s)", actor,
              entity_type="dataset_import", entity_id=imp.id, errors=report["errors"][:20])
        db.commit()
        return imp

    for r in _rows(admin_root, "tariff_inr.csv"):
        db.merge(RateCardItem(item_code=r["item_code"], category=r["category"], description=r["description"],
                              unit_price=float(r["unit_price_inr"]), provenance=r["data_provenance"]))
    existing_hist = {h for (h,) in db.query(HistoricalClaim.hist_claim_id).all()}
    for r in _rows(admin_root, "historical_claims.csv"):
        if r["hist_claim_id"] not in existing_hist:
            db.add(HistoricalClaim(hist_claim_id=r["hist_claim_id"], drg_code=r["drg_code"] or None,
                                   claim_type=r["claim_type"], los_days=int(r["los_days"]) if r["los_days"] else None,
                                   room_category=r["room_category"], total_billed=float(r["total_billed_inr"]),
                                   provenance=r["data_provenance"]))
    existing_members = {m for (m,) in db.query(MemberPolicy.subject_id).all()}
    for r in _rows(admin_root, "member_policies.csv"):
        sid = int(r["subject_id"])
        if sid in existing_members:
            continue
        db.add(MemberPolicy(member_id=r["member_id"], subject_id=sid, policy_version_id=r["policy_version_id"],
                            sum_insured=float(r["sum_insured_inr"]), policy_start_date=_d(r["policy_start_date"]),
                            policy_end_date=_d(r["policy_end_date"]), policy_cancelled_date=_d(r["policy_cancelled_date"]),
                            insured_age_at_entry=int(r["insured_age_at_entry"]) if r["insured_age_at_entry"] else None,
                            deductible_opted=r["deductible_opted"] == "true",
                            deductible_amount=_f(r["deductible_amount_inr"]), provenance=r["data_provenance"]))
    db.flush()

    admin_by = {int(r["hadm_id"]): r for r in _rows(admin_root, "claim_admin.csv")}
    check_by, bill_by = {}, {}
    for r in _rows(admin_root, "discharge_checklist.csv"):
        check_by.setdefault(int(r["hadm_id"]), []).append(r)
    for r in _rows(admin_root, "hospital_bill_items.csv"):
        bill_by.setdefault(int(r["hadm_id"]), []).append(r)
    notes_by = {}
    note_path = os.path.join(admin_root, "note", "discharge.csv")
    if os.path.exists(note_path) and os.path.abspath(admin_root) != os.path.abspath(clinical_root):
        with open(note_path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                notes_by[int(r["hadm_id"])] = (r["text"], r["data_provenance"])

    existing = {h for (h,) in db.query(Encounter.hadm_id).all()}
    records = load_mimic_dir(clinical_root, source_label=source_label, max_encounters=max_encounters)
    n = 0
    for rec in records:
        if rec.hadm_id in existing:
            continue
        enc = Encounter(subject_id=rec.subject_id, hadm_id=rec.hadm_id, data_source=rec.data_source, import_id=imp.id,
                        gender=rec.gender, anchor_age=rec.anchor_age, admittime=rec.admittime, dischtime=rec.dischtime,
                        edregtime=rec.edregtime,
                        admission_type=rec.admission_type, admission_location=rec.admission_location,
                        discharge_location=rec.discharge_location, insurance_category=rec.insurance,
                        drg_code=rec.drg_code, drg_type=rec.drg_type, drg_description=rec.drg_description)
        enc.diagnoses = [EncounterDiagnosis(seq_num=d.seq_num, icd_code=d.icd_code, icd_version=d.icd_version,
                                            long_title=d.long_title) for d in rec.diagnoses]
        enc.procedures = [EncounterProcedure(seq_num=p.seq_num, icd_code=p.icd_code, icd_version=p.icd_version,
                                             chartdate=p.chartdate, long_title=p.long_title) for p in rec.procedures]
        enc.transfers = [EncounterTransfer(eventtype=t.eventtype, careunit=t.careunit, intime=t.intime,
                                           outtime=t.outtime) for t in rec.transfers]
        enc.medications = [EncounterMedication(drug=m.drug, dose_val_rx=m.dose_val_rx, dose_unit_rx=m.dose_unit_rx,
                                               route=m.route, starttime=m.starttime, stoptime=m.stoptime)
                           for m in rec.medications]
        if rec.discharge_note:
            enc.note = DischargeNote(text=rec.discharge_note, provenance=rec.discharge_note_provenance)
        elif rec.hadm_id in notes_by:
            enc.note = DischargeNote(text=notes_by[rec.hadm_id][0], provenance=notes_by[rec.hadm_id][1])
        a = admin_by.get(rec.hadm_id)
        if a:
            enc.claim_admin = ClaimAdmin(claim_type=a["claim_type"], room_category=a["room_category"],
                                         notification_date=_d(a["notification_date"]),
                                         preauth_request_date=_d(a["preauth_request_date"]),
                                         claim_filed_date=_d(a["claim_filed_date"]), documents=a["documents_submitted"],
                                         bill_header_total=_f(a["bill_header_total_inr"]),
                                         provenance=a["data_provenance"])
        enc.checklist = [ChecklistItem(item=c["item"], status=c["status"], completed_at=_dt(c["completed_at"]))
                         for c in check_by.get(rec.hadm_id, [])]
        enc.bill_lines = [BillLine(line_no=int(b["line_no"]), item_code=b["item_code"], category=b["category"],
                                   description=b["description"], quantity=float(b["quantity"]),
                                   unit_price=float(b["unit_price_inr"]), amount=float(b["amount_inr"]),
                                   service_date=_d(b["service_date"]), linked_icd_code=b["linked_icd_code"] or None,
                                   provenance=b["data_provenance"]) for b in bill_by.get(rec.hadm_id, [])]
        db.add(enc)
        db.flush()
        db.add(ClaimCase(encounter_id=enc.id, case_ref=f"CC-{rec.hadm_id}", status="NEW"))
        n += 1
    imp.n_encounters = n
    imp.status = "COMPLETED"
    audit(db, "DATASET_IMPORTED", f"Imported {n} encounter(s) from {source_label} ({mode} mode)", actor,
          entity_type="dataset_import", entity_id=imp.id, warnings=report["warnings"][:20])
    db.commit()
    invalidate_context()
    return imp
