"""
The independent verifier may treat a BLOCKED case as an expected outcome only for a documented
source anomaly that is re-confirmed from the source files, and only when every other answer-key
root of the case is an injected fault. Anything else must stay a completeness problem.
"""
import csv
import importlib.util
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("verify_results", os.path.join(ROOT, "scripts", "verify_results.py"))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

ANOMALY = dict(clinical_source="MIMIC_IV_DEMO_2.2", hadm_id="1", finding="PROCEDURE_DATE_OUT_OF_RANGE",
               check="procedure_chartdate_outside_admission")


def _raw(roots):
    return {"1": dict(roots=roots)}


def test_documented_and_confirmed_anomaly_is_expected():
    raw = _raw(["PROCEDURE_DATE_OUT_OF_RANGE", "LOS_BILLING_MISMATCH", "EXCLUSION_KEYWORD_MATCH"])
    unexpected, expected = vr.classify_blocked(["1"], raw, {"1": {"LOS_BILLING_MISMATCH"}},
                                               {"1": ANOMALY}, {"1": True})
    assert (unexpected, expected) == ([], ["1"])


@pytest.mark.parametrize("roots,gt,known,confirmed", [
    (["PROCEDURE_DATE_OUT_OF_RANGE"], {}, {"1": ANOMALY}, {"1": False}),               # not confirmed in source
    (["PROCEDURE_DATE_OUT_OF_RANGE"], {}, {}, {}),                                       # not documented
    (["LOS_BILLING_MISMATCH"], {}, {"1": ANOMALY}, {"1": True}),                         # anomaly finding absent
    (["PROCEDURE_DATE_OUT_OF_RANGE", "DUPLICATE_BILL_ITEM"], {}, {"1": ANOMALY}, {"1": True}),  # unexplained root
])
def test_everything_else_stays_a_problem(roots, gt, known, confirmed):
    unexpected, expected = vr.classify_blocked(["1"], _raw(roots), gt, known, confirmed)
    assert (unexpected, expected) == (["1"], [])


def _write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_source_confirmation_reads_the_files(tmp_path):
    _write(str(tmp_path / "hosp" / "admissions.csv"),
           [dict(hadm_id="1", admittime="2155-05-17 00:02:00", dischtime="2155-05-19 18:27:00", edregtime="")])
    _write(str(tmp_path / "hosp" / "procedures_icd.csv"),
           [dict(hadm_id="1", chartdate="2155-05-18"), dict(hadm_id="1", chartdate="2155-05-28")])
    assert vr.confirm_in_source(str(tmp_path), ANOMALY)[0] is True
    _write(str(tmp_path / "hosp" / "procedures_icd.csv"), [dict(hadm_id="1", chartdate="2155-05-18")])
    assert vr.confirm_in_source(str(tmp_path), ANOMALY)[0] is False      # anomaly gone -> not confirmed
    assert vr.confirm_in_source(str(tmp_path / "missing"), ANOMALY)[0] is False
