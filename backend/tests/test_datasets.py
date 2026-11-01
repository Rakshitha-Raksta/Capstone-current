"""
Phase 1 tests: MIMIC-format loader, synthetic generator, ground truth and
dataset validator. No database needed.
"""
import csv
import gzip
import json
import os

import pytest

from app.datasets import catalog as C
from app.datasets.error_types import ERROR_TYPES, CLINICAL_ERROR_TYPES
from app.datasets.mimic import load_mimic_dir, SYNTHETIC_HADM_ID_START, SYNTHETIC_SUBJECT_ID_START
from app.datasets.synthetic import (
    generate_synthetic_dataset, generate_admin_layer_for_mimic, load_policy_profiles,
)
from app.datasets.validate import validate_dataset, load_icd10_reference

N = 80


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    manifest = generate_synthetic_dataset(str(out), seed=11, n_encounters=N, error_rate=0.5)
    return out, manifest


def _csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_catalog_titles_match_icd10_reference():
    ref = load_icd10_reference()
    assert ref is not None, "data/reference/icd10cm_codes.csv missing"
    for code, title in C.ICD10CM_TITLES.items():
        assert ref.get(code) == title, code
    for code in C.NON_BILLABLE_HEADER_CODES:
        assert code not in ref, code


def test_policy_profiles_come_from_rule_csv():
    p = load_policy_profiles()
    assert p["star_assure_2026_v1"]["filing_days"] == 15          # SA26-010
    assert p["hdfc_optima_secure_2021_v1"]["filing_days"] == 30   # HDFC21-007
    assert p["star_assure_2026_v1"]["planned_preauth_hours"] == 48  # SA26-013
    assert p["hdfc_optima_secure_2021_v1"]["kyc_threshold"] == 100000  # HDFC21-011
    assert p["star_assure_2026_v1"]["waiting_days"] == 1080        # SA26-003, 36 months
    assert p["hdfc_optima_secure_2026_v1"]["active_rule_ids"] == []  # all NEEDS_REVIEW


def test_generation_is_deterministic(tmp_path):
    a = generate_synthetic_dataset(str(tmp_path / "a"), seed=5, n_encounters=30)
    b = generate_synthetic_dataset(str(tmp_path / "b"), seed=5, n_encounters=30)
    c = generate_synthetic_dataset(str(tmp_path / "c"), seed=6, n_encounters=30)
    assert a["files"] == b["files"]
    assert a["files"] != c["files"]


def test_dataset_validates_cleanly(ds):
    out, _ = ds
    rep = validate_dataset(str(out))
    assert rep["errors"] == []
    assert rep["stats"]["loaded_encounters"] == N


def test_ids_are_outside_mimic_ranges_and_labelled(ds):
    out, _ = ds
    for row in _csv(out / "hosp" / "admissions.csv"):
        assert int(row["hadm_id"]) >= SYNTHETIC_HADM_ID_START
        assert int(row["subject_id"]) >= SYNTHETIC_SUBJECT_ID_START
        assert row["data_provenance"] == "SYNTHETIC"
    for row in _csv(out / "note" / "discharge.csv"):
        assert row["text"].startswith("*** SYNTHETIC")
    for row in _csv(out / "admin" / "tariff_inr.csv"):
        assert row["data_provenance"] == "SYNTHETIC_TARIFF_NOT_REAL_PRICES"


def test_every_ground_truth_issue_has_a_resolution(ds):
    out, _ = ds
    gt = _csv(out / "ground_truth" / "injected_errors.csv")
    corr = json.load(open(out / "ground_truth" / "corrections.json"))
    assert gt, "expected some injected issues at error_rate=0.5"
    for g in gt:
        assert g["error_type"] in ERROR_TYPES
        ops = [o for o in corr[g["hadm_id"]] if o["error_type"] == g["error_type"]]
        assert ops, (g["hadm_id"], g["error_type"])


def test_bill_header_and_lines_consistent_except_residual_fault(ds):
    out, _ = ds
    gt = {(g["hadm_id"], g["error_type"]) for g in _csv(out / "ground_truth" / "injected_errors.csv")}
    lines = {}
    for l in _csv(out / "admin" / "hospital_bill_items.csv"):
        lines[l["hadm_id"]] = lines.get(l["hadm_id"], 0) + int(l["amount_inr"])
    for a in _csv(out / "admin" / "claim_admin.csv"):
        residual = int(a["bill_header_total_inr"]) != lines[a["hadm_id"]]
        assert residual == ((a["hadm_id"], "RECONCILIATION_RESIDUAL") in gt), a["hadm_id"]


def test_scenario_classes_present(ds):
    out, _ = ds
    classes = {l["scenario_class"] for l in _csv(out / "ground_truth" / "encounter_labels.csv")}
    assert {"normal", "single_fault"} <= classes


def test_historical_claims_are_separate_population(ds):
    out, _ = ds
    hist = _csv(out / "admin" / "historical_claims.csv")
    assert len(hist) >= 500
    assert all(h["data_provenance"] == "SYNTHETIC_HISTORICAL_CLAIMS" for h in hist)


def test_real_mode_reads_gz_and_never_touches_clinical_tables(ds, tmp_path):
    """Simulate a real MIMIC directory: gzipped hosp tables, no provenance column, no notes."""
    out, _ = ds
    real = tmp_path / "real" / "hosp"
    real.mkdir(parents=True)
    for t in ("patients", "admissions", "diagnoses_icd", "procedures_icd", "drgcodes", "transfers",
              "prescriptions", "d_icd_diagnoses", "d_icd_procedures"):
        rows = _csv(out / "hosp" / f"{t}.csv")
        cols = [c for c in rows[0].keys() if c != "data_provenance"] if rows else []
        with gzip.open(real / f"{t}.csv.gz", "wt", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    before = {p.name: p.read_bytes() for p in real.iterdir()}

    recs = load_mimic_dir(str(tmp_path / "real"), source_label="MIMIC_IV_DEMO_2.2")
    assert len(recs) == N and all(r.data_source == "MIMIC_IV_DEMO_2.2" for r in recs)

    admin_out = tmp_path / "admin_layer"
    m = generate_admin_layer_for_mimic(str(tmp_path / "real"), str(admin_out), seed=3, error_rate=0.6)
    assert m["mode"] == "admin_layer_for_real_mimic"
    assert {p.name: p.read_bytes() for p in real.iterdir()} == before  # clinical files unchanged
    injected = {g["error_type"] for g in _csv(admin_out / "ground_truth" / "injected_errors.csv")}
    assert not (injected & CLINICAL_ERROR_TYPES)
    notes = _csv(admin_out / "note" / "discharge.csv")
    assert all(n["data_provenance"] == "SYNTHETIC_NOTE_DERIVED_FROM_STRUCTURED_DATA" for n in notes)
    assert validate_dataset(str(tmp_path / "real"), str(admin_out))["errors"] == []


def test_loader_rejects_incomplete_directory(tmp_path):
    (tmp_path / "hosp").mkdir()
    with pytest.raises(FileNotFoundError):
        load_mimic_dir(str(tmp_path), source_label="X")


@pytest.mark.parametrize("unit,expected", [
    ("Trauma SICU (TSICU)", True),
    ("Neuro Surgical Intensive Care Unit (Neuro SICU)", True),
    ("Medical/Surgical Intensive Care Unit (MICU/SICU)", True),
    ("Coronary Care Unit (CCU)", True),
    ("Med/Surg", False),
    ("Emergency Department", False),
    ("Discharge Lounge", False),
])
def test_real_icu_care_unit_names(unit, expected):
    from app.datasets.synthetic import is_icu
    assert is_icu(unit) is expected


def test_no_stray_control_characters_in_source():
    """Guards against escape sequences (e.g. a word boundary or a backreference) that were
    written out as raw control characters, which silently break regexes and commands."""
    import re
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    bad = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]|\r(?!\n)")
    dirs = ["backend/app/clearclaim", "backend/app/datasets", "backend/app/experiments", "backend/tests",
            "scripts", "docs", "e2e", "frontend/src"]
    hits = []
    for d in dirs:
        for dp, _, files in os.walk(os.path.join(root, d)):
            for f in files:
                if f.endswith((".py", ".md", ".ts", ".tsx", ".css", ".ps1")):
                    with open(os.path.join(dp, f), encoding="utf-8", newline="") as fh:
                        if bad.search(fh.read()):
                            hits.append(os.path.relpath(os.path.join(dp, f), root))
    assert hits == []


def test_setup_demo_regenerates_stale_generated_data(tmp_path):
    import importlib.util
    import json as _json
    from app.datasets.synthetic import GENERATOR_VERSION
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cwd = os.getcwd()
    spec = importlib.util.spec_from_file_location("setup_demo", os.path.join(root, "scripts", "setup_demo.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)   # the script chdirs to backend/ on import
    assert mod.needs_generation(str(tmp_path)) is True                      # missing
    (tmp_path / "MANIFEST.json").write_text(_json.dumps({"generator_version": "0.0.1"}), encoding="utf-8")
    assert mod.needs_generation(str(tmp_path)) is True                      # stale
    (tmp_path / "MANIFEST.json").write_text(_json.dumps({"generator_version": GENERATOR_VERSION}), encoding="utf-8")
    assert mod.needs_generation(str(tmp_path)) is False                     # current
    assert mod.mask_url("postgresql://user:secret@host/db") == "postgresql://user:***@host/db"
