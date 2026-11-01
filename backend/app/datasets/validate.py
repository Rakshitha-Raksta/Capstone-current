"""
Integrity checks for an encounter dataset directory (synthetic demo, or a
real MIMIC-format directory plus its synthetic admin layer).

Returns a report dict {errors: [...], warnings: [...], stats: {...}};
never raises on data problems, so scripts and tests can assert on it.
"""
import csv
import hashlib
import json
import os
from typing import Optional

from app.datasets import catalog as C
from app.datasets.mimic import (
    REQUIRED_HOSP_TABLES, table_path, read_table, load_mimic_dir,
    MIMIC_SUBJECT_ID_RANGE, MIMIC_HADM_ID_RANGE, SYNTHETIC_SUBJECT_ID_START, SYNTHETIC_HADM_ID_START,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ICD10_REFERENCE = os.path.join(REPO_ROOT, "data", "reference", "icd10cm_codes.csv")


def load_icd10_reference(path: str = ICD10_REFERENCE) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", newline="") as f:
        return {r["icd_code"]: r["long_title"] for r in csv.DictReader(f)}


def _rows(root, module, table):
    p = table_path(root, module, table)
    return read_table(p) if p else None


def validate_dataset(clinical_root: str, admin_root: Optional[str] = None, include_ground_truth: bool = True) -> dict:
    """include_ground_truth=False is used by the runtime import: evaluation labels are then never
    opened, and nothing derived from them appears in the stored report."""
    admin_root = admin_root or clinical_root
    errors, warnings, stats = [], [], {}

    for t in REQUIRED_HOSP_TABLES:
        if table_path(clinical_root, "hosp", t) is None:
            errors.append(f"missing required table hosp/{t}")
    if errors:
        return dict(errors=errors, warnings=warnings, stats=stats)

    admissions = _rows(clinical_root, "hosp", "admissions")
    hadm_ids = {int(a["hadm_id"]) for a in admissions}
    stats["admissions"] = len(admissions)
    stats["patients"] = len(_rows(clinical_root, "hosp", "patients"))
    synthetic = bool(admissions) and "data_provenance" in admissions[0]
    stats["clinical_provenance"] = "SYNTHETIC" if synthetic else "REAL_MIMIC_FORMAT"

    # Every synthetic clinical row must be labelled and use non-MIMIC id ranges.
    if synthetic:
        for t in ("patients", "admissions", "diagnoses_icd", "procedures_icd", "transfers", "prescriptions",
                  "drgcodes"):
            rows = _rows(clinical_root, "hosp", t) or []
            unlabelled = [r for r in rows if not r.get("data_provenance")]
            if unlabelled:
                errors.append(f"hosp/{t}: {len(unlabelled)} row(s) without data_provenance")
            for r in rows:
                if "subject_id" in r and r["subject_id"] and int(r["subject_id"]) < SYNTHETIC_SUBJECT_ID_START:
                    errors.append(f"hosp/{t}: synthetic subject_id {r['subject_id']} inside MIMIC range")
                    break
                if "hadm_id" in r and r["hadm_id"] and int(float(r["hadm_id"])) < SYNTHETIC_HADM_ID_START:
                    errors.append(f"hosp/{t}: synthetic hadm_id {r['hadm_id']} inside MIMIC range")
                    break
    else:
        lo, hi = MIMIC_HADM_ID_RANGE
        out = [h for h in hadm_ids if not lo <= h <= hi]
        if out:
            warnings.append(f"{len(out)} hadm_id(s) outside the documented MIMIC-IV range {MIMIC_HADM_ID_RANGE}")
        lo, hi = MIMIC_SUBJECT_ID_RANGE

    # ICD-10-CM codes vs the reference list.
    ref = load_icd10_reference()
    dx = _rows(clinical_root, "hosp", "diagnoses_icd")
    icd10 = [r for r in dx if r["icd_version"] == "10"]
    stats["diagnoses"] = len(dx)
    stats["diagnoses_icd10"] = len(icd10)
    if ref is None:
        warnings.append("data/reference/icd10cm_codes.csv not found; ICD-10-CM validity not checked")
    else:
        not_in_ref = sorted({r["icd_code"] for r in icd10 if r["icd_code"] not in ref})
        stats["icd10_codes_not_in_reference"] = not_in_ref
        d_dx = _rows(clinical_root, "hosp", "d_icd_diagnoses") or []
        differ = [r["icd_code"] for r in d_dx
                  if r["icd_version"] == "10" and r["icd_code"] in ref and ref[r["icd_code"]] != r["long_title"]]
        stats["d_icd_titles_differing_from_reference"] = len(differ)
        if differ:
            warnings.append(f"{len(differ)} d_icd_diagnoses title(s) differ from the FY2025 reference wording "
                            f"(expected for multi-year source dictionaries), e.g. {differ[:5]}")
        used_v10 = {r["icd_code"] for r in icd10}
        own = {r["icd_code"] for r in d_dx if r["icd_version"] == "10"}
        stats["icd10_codes_not_in_reference_but_in_source_dictionary"] = len((used_v10 - set(ref)) & own)

    # Catalog PCS titles vs a real d_icd_procedures (only meaningful on real MIMIC data).
    if not synthetic:
        d_px = {(r["icd_code"], r["icd_version"]): r["long_title"]
                for r in (_rows(clinical_root, "hosp", "d_icd_procedures") or [])}
        if d_px:
            checked = mismatched = 0
            for code, title in C.ICD10PCS_TITLES.items():
                real = d_px.get((code, "10"))
                if real is None:
                    continue
                checked += 1
                if real.strip().lower() != title.strip().lower():
                    mismatched += 1
                    warnings.append(f"catalog PCS title for {code} differs from MIMIC d_icd_procedures: {real!r}")
            stats["catalog_pcs_titles_checked_against_mimic"] = checked
            stats["catalog_pcs_titles_mismatched"] = mismatched

    # Admin layer
    def admin_rows(name):
        p = os.path.join(admin_root, "admin", name)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    claim_admin = admin_rows("claim_admin.csv")
    if claim_admin is None:
        warnings.append("admin/claim_admin.csv not found (no synthetic insurance layer)")
    else:
        bills = admin_rows("hospital_bill_items.csv") or []
        members = admin_rows("member_policies.csv") or []
        stats["claim_admin"] = len(claim_admin)
        stats["bill_items"] = len(bills)
        stats["member_policies"] = len(members)
        for name, rows in (("claim_admin", claim_admin), ("hospital_bill_items", bills),
                           ("member_policies", members)):
            if any(not (r.get("data_provenance") or "").startswith("SYNTHETIC") for r in rows):
                errors.append(f"admin/{name}: row(s) not labelled SYNTHETIC")
        orphan = {int(r["hadm_id"]) for r in claim_admin} - hadm_ids
        if orphan:
            errors.append(f"admin/claim_admin: {len(orphan)} hadm_id(s) not in admissions")
        orphan = {int(r["hadm_id"]) for r in bills} - hadm_ids
        if orphan:
            errors.append(f"admin/hospital_bill_items: {len(orphan)} hadm_id(s) not in admissions")
        known_pv = set()
        rules_csv = os.path.join(REPO_ROOT, "data", "structured", "policy_rule_candidates.csv")
        with open(rules_csv, encoding="utf-8") as f:
            known_pv = {r["policy_version"] for r in csv.DictReader(f)}
        bad_pv = {m["policy_version_id"] for m in members} - known_pv
        if bad_pv:
            errors.append(f"admin/member_policies: unknown policy_version_id(s) {sorted(bad_pv)}")

    gt_path = os.path.join(admin_root, "ground_truth", "injected_errors.csv")
    if include_ground_truth and os.path.exists(gt_path):
        with open(gt_path, encoding="utf-8", newline="") as f:
            gt = list(csv.DictReader(f))
        stats["ground_truth_issues"] = len(gt)
        if {int(g["hadm_id"]) for g in gt} - hadm_ids:
            errors.append("ground_truth/injected_errors: hadm_id(s) not in admissions")
        if ref is not None:
            # Every injected INVALID_ICD_CODE must really be absent from the reference.
            injected_invalid = {g["detail"].split("non-billable ")[-1] for g in gt
                                if g["error_type"] == "INVALID_ICD_CODE"}
            wrongly_valid = sorted(c for c in injected_invalid if c in ref)
            if wrongly_valid:
                errors.append(f"injected INVALID_ICD_CODE value(s) are actually valid: {wrongly_valid}")

    # Manifest hashes
    man_path = os.path.join(admin_root, "MANIFEST.json")
    if os.path.exists(man_path):
        with open(man_path, encoding="utf-8") as f:
            manifest = json.load(f)
        for rel, info in manifest["files"].items():
            p = os.path.join(admin_root, rel)
            if rel.startswith("ground_truth/") and not include_ground_truth:
                continue
            if not os.path.exists(p):
                if rel.startswith("ground_truth/"):
                    # runtime/deployment copies may (and should) omit evaluation labels
                    warnings.append(f"{rel} not present (ground truth omitted; fine for runtime use)")
                else:
                    errors.append(f"MANIFEST lists missing file {rel}")
                continue
            with open(p, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != info["sha256"]:
                    errors.append(f"MANIFEST sha256 mismatch for {rel} (file edited after generation?)")
        stats["manifest_mode"] = manifest.get("mode")
    else:
        warnings.append("MANIFEST.json not found")

    # Loader round trip
    try:
        recs = load_mimic_dir(clinical_root, source_label="VALIDATION")
        stats["loaded_encounters"] = len(recs)
        stats["encounters_without_dischtime"] = sum(1 for r in recs if r.dischtime is None)
    except Exception as e:  # pragma: no cover - reported, not raised
        errors.append(f"loader failed: {e}")

    return dict(errors=errors, warnings=warnings, stats=stats)
