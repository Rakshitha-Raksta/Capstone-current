"""Policy and historical intelligence: read-only, consistent with the recorded stage outputs."""
import csv
import os

from app.clearclaim.models import ClaimCase, PipelineRun, StageExecution
from tests.test_isolation import _fresh_cases

RULES_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "structured", "policy_rule_candidates.csv")


def _recorded(db, case_id, stage):
    x = db.query(StageExecution).filter_by(case_id=case_id, stage=stage, is_current=True).first()
    return (x.output or {}).get("data") or {}


def test_catalog_lists_all_59_cited_rules_without_a_case(client):
    r = client.get("/api/v1/intel/rules")
    assert r.status_code == 200
    body = r.json()
    with open(RULES_CSV, encoding="utf-8") as fh:
        ids = [row["candidate_id"] for row in csv.DictReader(fh)]
    assert body["total"] == 59 == len(ids)
    assert [x["rule_id"] for x in body["rules"]] == ids
    for x in body["rules"]:
        assert x["source_document"] and x["source_text"]
        assert x["document"] is not None, f"{x['rule_id']} cites an unknown document"
        assert "outcome" not in x


def test_case_outcomes_equal_recorded_stage_results_and_change_nothing(client):
    db = _fresh_cases(n=10, seed=43)
    runs = db.query(PipelineRun).count()
    for c in db.query(ClaimCase):
        body = client.get("/api/v1/intel/rules", params={"case_id": c.id}).json()
        rec = {}
        for stage in ("eligibility", "adjudication"):
            for res in _recorded(db, c.id, stage).get("rule_results", []):
                rec.setdefault(res["rule_id"], []).append(res)
        for x in body["rules"]:
            o = x["outcome"]
            if x["rule_id"] in rec:
                assert o["results"] == rec[x["rule_id"]]
                assert o["status"] in {r["severity"] for r in rec[x["rule_id"]]}
            else:
                assert o["status"] in ("NOT_APPLICABLE", "QUARANTINED", "NO_RESULT")   # never a fabricated PASS
    assert db.query(PipelineRun).count() == runs
    assert client.get("/api/v1/intel/rules", params={"case_id": 999999}).status_code == 404


def test_historical_view_matches_the_stage_and_labels_limitations(client):
    db = _fresh_cases(n=12, seed=44)
    seen = set()
    for c in db.query(ClaimCase):
        v = client.get(f"/api/v1/intel/cases/{c.id}/historical").json()
        seen.add(v["status"])
        assert any("SYNTHETIC" in s for s in v["method"]["limitations"])
        rec = _recorded(db, c.id, "historical")
        assert v["recorded"] == rec
        if v["status"] == "COMPARED":
            assert v["matches_recorded"] and v["outlier"] == rec["outlier"]
            assert v["n_peers"] == rec["n_peers"] and len(v["peers"]) <= 40
            assert all(p["drg_code"] == v["current"]["drg_code"] for p in v["peers"])
    assert seen
    assert client.get("/api/v1/intel/cases/999999/historical").status_code == 404


def test_research_summary_reads_recorded_files_verbatim(client):
    import json
    body = client.get("/api/v1/intel/research").json()
    for r in body["runs"]:
        if not r["available"]:
            continue
        with open(os.path.join(os.path.dirname(__file__), "..", "..", r["path"]), encoding="utf-8") as fh:
            agg = json.load(fh)["aggregate"]
        for arm, metrics in r["arms"].items():
            for k, v in metrics.items():
                assert v == agg[arm][k]
