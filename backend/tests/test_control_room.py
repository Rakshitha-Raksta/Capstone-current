"""
Agent Control Room: timing metrics, sequential vs parallel execution semantics, output
equivalence, failure handling, isolation of comparisons from live data, and the API.
"""
import time

import pytest

from app.clearclaim import dag
from app.clearclaim import orchestrator as orch_mod
from app.clearclaim.control_room import final_fingerprint
from app.clearclaim.models import ClaimCase, ExperimentRun, PipelineRun
from app.clearclaim.orchestrator import Orchestrator
from app.clearclaim.timing import critical_path, intervals, max_concurrency, overlapping_pairs, run_metrics, union_length
from app.database.db import engine
from tests.test_isolation import _fresh_cases

SQLITE = engine.url.get_backend_name() == "sqlite"


def _fin(stage, s, e, status="SUCCEEDED"):
    return dict(type="stage_finished", stage=stage, start_ms=s, end_ms=e, duration_ms=e - s, status=status)


# ---------------------------------------------------------------------------
# metric definitions (hand-checked numbers)
# ---------------------------------------------------------------------------

def test_metric_formulas_on_known_intervals():
    ev = [_fin("ingestion", 0, 1), _fin("data_validation", 1, 3), _fin("discharge_readiness", 3, 4),
          _fin("billing", 4, 9), _fin("eligibility", 5, 7), _fin("adjudication", 9, 10),
          dict(type="run_finished", status="SUCCEEDED", outcome_status="BLOCKED", wall_ms=12.0, commit_ms=13.5)]
    iv = intervals(ev)
    assert max_concurrency(iv) == 2
    assert overlapping_pairs(iv) == [["billing", "eligibility"]]
    assert max_concurrency({"a": dict(start=0, end=1), "b": dict(start=1, end=2)}) == 1   # touching is not overlap
    assert union_length(iv) == 10                                                         # [0,10] covered once
    m = run_metrics(ev)
    assert m["stage_sum_ms"] == 1 + 2 + 1 + 5 + 2 + 1 == 12
    assert m["busy_ms"] == 10 and m["overhead_ms"] == 2.0 and m["end_to_end_ms"] == 13.5
    # longest path: ingestion 1 + validation 2 + readiness 1 + billing 5 + adjudication 1 = 10 (eligibility branch is 8)
    cp = critical_path({k: v["duration"] for k, v in iv.items()})
    assert cp["ms"] == 10 and cp["path"][:5] == ["ingestion", "data_validation", "discharge_readiness", "billing", "adjudication"]
    assert cp["path"][-1] == "claim_generation" and "eligibility" not in cp["path"]


# ---------------------------------------------------------------------------
# real orchestrator runs
# ---------------------------------------------------------------------------

def _run(db, case, mode, sleep_ms=0, monkeypatch=None):
    if sleep_ms and monkeypatch is not None:
        for s in ("billing", "eligibility"):
            real = orch_mod.STAGE_FUNCTIONS[s]

            def slow(i, u, c, _real=real):
                time.sleep(sleep_ms / 1000)          # test-only load so overlap is observable above timer noise
                return _real(i, u, c)
            monkeypatch.setitem(orch_mod.STAGE_FUNCTIONS, s, slow)
    events = []
    Orchestrator(db, mode, "full", on_event=events.append).process(case, trigger="MANUAL_RERUN",
                                                                    changed_domains=list(dag.ALL_INPUT_DOMAINS))
    return events, run_metrics(events)


def test_sequential_never_overlaps_and_respects_dependencies():
    db = _fresh_cases(n=4, seed=61)
    c = db.query(ClaimCase).first()
    events, m = _run(db, c, "sequential")
    iv = intervals(events)
    assert set(iv) == set(dag.STAGE_ORDER) and m["max_concurrency"] == 1 and m["overlapping_pairs"] == []
    for s in dag.STAGE_ORDER:
        for d in dag.STAGES[s]["deps"]:
            assert iv[s]["start"] >= iv[d]["end"], (d, s)
    assert m["stage_sum_ms"] <= m["busy_ms"] + 1e-6 <= m["wall_ms"] + 1e-6     # sequential: no double counting
    assert m["wall_ms"] <= m["end_to_end_ms"]
    db.close()


def test_parallel_overlaps_only_independent_stages_and_is_faster_when_work_is_long(monkeypatch):
    db = _fresh_cases(n=4, seed=62)
    c = db.query(ClaimCase).first()
    ev_seq, seq = _run(db, c, "sequential", sleep_ms=60, monkeypatch=monkeypatch)
    ev_par, par = _run(db, c, "parallel", sleep_ms=0)          # stage functions already slowed by the patch
    iv = intervals(ev_par)
    for s in dag.STAGE_ORDER:                                    # dependencies always respected
        for d in dag.STAGES[s]["deps"]:
            assert iv[s]["start"] >= iv[d]["end"], (d, s)
    same_wave = {frozenset(w) for w in dag.waves() if len(w) > 1}
    assert par["overlapping_pairs"] and all(frozenset(p) in same_wave for p in par["overlapping_pairs"])
    assert par["max_concurrency"] == 2 and seq["max_concurrency"] == 1
    assert par["wall_ms"] < seq["wall_ms"] - 30                  # ~60 ms of concurrent sleeping saved
    assert par["stage_sum_ms"] > par["busy_ms"]                  # in parallel the sum exceeds elapsed busy time
    db.close()


def test_sequential_and_parallel_produce_identical_final_outputs():
    db = _fresh_cases(n=6, seed=63)
    for c in db.query(ClaimCase).limit(3):
        _run(db, c, "sequential")
        a = final_fingerprint(db, c.id)
        _run(db, c, "parallel")
        b = final_fingerprint(db, c.id)
        assert a == b and all(a["per_stage"].values())
    db.close()


def test_failed_stage_metrics(monkeypatch):
    db = _fresh_cases(n=3, seed=64)
    c = db.query(ClaimCase).first()

    def boom(*a, **k):
        raise RuntimeError("injected")
    monkeypatch.setitem(orch_mod.STAGE_FUNCTIONS, "eligibility", boom)
    _, m = _run(db, c, "parallel")
    assert m["status"] == "FAILED" and m["stages_failed"] == 1 and m["stages_skipped"] >= 1
    db.close()


# ---------------------------------------------------------------------------
# API (comparisons run on isolated copies and leave live data untouched)
# ---------------------------------------------------------------------------

def _wait(client, token, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/v1/control-room/jobs/{token}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_control_room_api_validation_and_single_run(client):
    db = _fresh_cases(n=4, seed=65)
    hadm = db.query(ClaimCase).first().encounter.hadm_id
    db.close()
    assert client.post("/api/v1/control-room/runs", json={"hadm_id": "1", "mode": "parallel"}).status_code == 404
    assert client.post("/api/v1/control-room/runs", json={"hadm_id": "x", "mode": "parallel"}).status_code == 422
    st = client.get("/api/v1/control-room/stages").json()
    assert [s["id"] for s in st["stages"]] == dag.STAGE_ORDER and "not independent autonomous AI" in st["disclosure"]
    j = _wait(client, client.post("/api/v1/control-room/runs", json={"hadm_id": str(hadm), "mode": "sequential"}).json()["token"])
    assert j["status"] == "finished", j
    rec = client.get(f"/api/v1/control-room/history/{j['record_id']}").json()
    run = rec["summary"]["runs"][0]
    assert run["metrics"]["stages_executed"] == 11 and run["metrics"]["max_concurrency"] == 1
    assert any(r["id"] == j["record_id"] for r in client.get("/api/v1/control-room/history").json())
    assert all(e.get("kind") is None or not str(e.get("kind")).startswith("cr_") for e in client.get("/api/v1/experiments").json())
    csv_text = client.get(f"/api/v1/control-room/history/{j['record_id']}/export?format=csv").text
    assert csv_text.count("\n") == 12                              # header + 11 stage rows


@pytest.mark.skipif(not SQLITE, reason="isolated comparisons copy the SQLite database")
def test_comparison_is_isolated_reports_equivalence_and_stats(client):
    db = _fresh_cases(n=4, seed=66)
    c = db.query(ClaimCase).first()
    hadm, cid, runs_before = c.encounter.hadm_id, c.id, db.query(PipelineRun).filter_by(case_id=c.id).count()
    db.close()
    j = _wait(client, client.post("/api/v1/control-room/comparisons", json={"hadm_id": str(hadm), "repetitions": 2}).json()["token"])
    assert j["status"] == "finished", j
    s = client.get(f"/api/v1/control-room/history/{j['record_id']}").json()["summary"]
    assert len(s["runs"]) == 6 and sum(r["warmup"] for r in s["runs"]) == 2       # 1 warm-up pair + 2 reps x 2 modes
    assert s["outputs_match"] and s["same_input_snapshot"] and s["failed"] == 0
    assert s["stats"]["sequential"]["wall_ms"]["n"] == 2 and s["stats"]["sequential"]["max_concurrency"]["max"] == 1
    from app.database.db import SessionLocal
    db = SessionLocal()
    assert db.query(PipelineRun).filter_by(case_id=cid).count() == runs_before    # live data untouched
    db.close()


@pytest.mark.skipif(not SQLITE, reason="isolated comparisons copy the SQLite database")
def test_correction_comparison_full_vs_targeted_matches_and_saves_executions(client):
    db = _fresh_cases(n=12, seed=41)
    c = next(c for c in db.query(ClaimCase) if c.encounter.claim_admin)
    hadm, version, cid = c.encounter.hadm_id, c.version, c.id
    db.close()
    body = {"hadm_id": str(hadm), "correction": {"op": "add_document", "payload": {"document": "extra_note"},
                                                 "reason": "control room test", "expected_version": version}}
    j = _wait(client, client.post("/api/v1/control-room/correction-comparisons", json=body).json()["token"])
    assert j["status"] == "finished", j
    s = client.get(f"/api/v1/control-room/history/{j['record_id']}").json()["summary"]
    full, targeted = s["runs"]
    assert full["rerun_policy"] == "full" and targeted["rerun_policy"] == "targeted"
    assert s["outputs_match"] and s["mismatched_stages"] == []
    assert full["metrics"]["stages_executed"] == 11 and s["executions_avoided"] > 0
    assert targeted["metrics"]["stages_not_rerun"] >= 1
    from app.database.db import SessionLocal
    db = SessionLocal()
    assert db.get(ClaimCase, cid).version == version                            # the live case was not corrected
    assert db.query(ExperimentRun).filter_by(id=j["record_id"]).count() == 1
    db.close()


def test_finished_events_carry_evidence_and_a_safe_summary():
    db = _fresh_cases(n=6, seed=67)
    c = next(c for c in db.query(ClaimCase) if c.status == "BLOCKED")
    events, _ = _run(db, c, "parallel")
    fin = {e["stage"]: e for e in events if e["type"] == "stage_finished"}
    assert "subject_id" not in fin["ingestion"]["output_summary"]                  # identifiers stay out
    assert "packet" not in fin["claim_generation"]["output_summary"]
    assert fin["claim_generation"]["output_summary"]["status"] == c.status
    with_findings = [f for e in fin.values() for f in e["findings"]]
    assert with_findings and all({"message", "expected", "actual", "rule_id"} <= set(f) for f in with_findings)
    db.close()


@pytest.mark.skipif(not SQLITE, reason="isolated comparisons copy the SQLite database")
def test_cancel_stops_further_runs_and_keeps_completed_ones(client):
    db = _fresh_cases(n=4, seed=68)
    hadm = db.query(ClaimCase).first().encounter.hadm_id
    db.close()
    token = client.post("/api/v1/control-room/comparisons", json={"hadm_id": str(hadm), "repetitions": 10}).json()["token"]
    assert client.post(f"/api/v1/control-room/jobs/{token}/cancel").status_code == 200
    j = _wait(client, token)
    assert j["status"] in ("cancelled", "finished")                             # it may finish before the flag is seen
    if j["status"] == "cancelled":
        rec = client.get(f"/api/v1/control-room/history/{j['record_id']}").json()
        assert rec["status"] == "CANCELLED" and rec["summary"]["cancelled"] and len(rec["summary"]["runs"]) < 22
    assert client.post("/api/v1/control-room/jobs/nope/cancel").status_code == 404
