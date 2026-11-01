"""
Ground-truth isolation: the runtime (import, pipeline, every read endpoint)
must never open a file under ground_truth/, even when that folder is present
next to the dataset, and nothing label-derived may appear in API responses.
"""
import builtins
import csv
import os


from app.clearclaim.context import invalidate_context
from app.clearclaim.models import CCBase, ClaimCase
from app.database.db import SessionLocal, engine
from app.datasets.synthetic import generate_synthetic_dataset


def test_runtime_never_reads_ground_truth(tmp_path, client, monkeypatch):
    d = tmp_path / "cohort"
    # the generator legitimately WRITES ground truth, so it runs before the guard is installed
    generate_synthetic_dataset(str(d), seed=31, n_encounters=20, error_rate=0.7)
    gt_details = [r["detail"] for r in csv.DictReader(open(d / "ground_truth" / "injected_errors.csv", encoding="utf-8"))
                  if r["detail"]]
    assert os.path.isdir(d / "ground_truth")

    # now guard every open() and run the whole runtime path
    real_open = builtins.open

    def guard(file, *a, **kw):
        if "ground_truth" in str(file).replace("\\", "/"):
            raise AssertionError(f"runtime opened a ground-truth file: {file}")
        return real_open(file, *a, **kw)
    monkeypatch.setattr(builtins, "open", guard)

    from app.clearclaim.etl import import_dataset
    from app.clearclaim.orchestrator import Orchestrator
    CCBase.metadata.drop_all(engine)
    CCBase.metadata.create_all(engine)
    invalidate_context()
    db = SessionLocal()
    imp = import_dataset(db, str(d), str(d), "demo", "SYNTHETIC")
    assert imp.status == "COMPLETED"
    assert "ground_truth_issues" not in imp.validation_report["stats"]
    for c in db.query(ClaimCase):
        Orchestrator(db).process(c)
    ids = [c.id for c in db.query(ClaimCase)]
    db.close()

    bodies = []
    for path in ["/api/v1/overview", "/api/v1/meta", "/api/v1/data/status", "/api/v1/settings",
                 "/api/v1/workflow/graph", "/api/v1/audit-events", "/api/v1/review-tasks?status=ALL&page_size=100",
                 "/api/v1/cases?page_size=100", "/api/v1/experiments"]:
        r = client.get(path)
        assert r.status_code == 200, path
        bodies.append(r.text)
    for cid in ids:
        for suffix in ("", "/runs", "/events", "/packet"):
            r = client.get(f"/api/v1/cases/{cid}{suffix}")
            assert r.status_code == 200, (cid, suffix)
            bodies.append(r.text)
    blob = "\n".join(bodies)
    for marker in ("ground_truth", "injected_errors", "encounter_labels", "scenario_class", "legitimate_outlier"):
        assert marker not in blob, marker
    for det in gt_details:            # injector detail strings (e.g. "... replaced with non-billable E78")
        assert det not in blob, det
    CCBase.metadata.drop_all(engine)
    invalidate_context()


def test_import_summary_rejects_paths_outside_results(client):
    for p in ["../README.md", "results/experiments_evil/summary.json", "results/experiments/../../.env",
              "C:/Windows/win.ini"]:
        r = client.post("/api/v1/experiments/import-summary", params={"path": p})
        assert r.status_code == 404, (p, r.status_code)


# ---------------------------------------------------------------------------
# hardening: auth defaults, conflicts, request limits, audit-chain integrity
# ---------------------------------------------------------------------------

def _fresh_cases(n=12, seed=41):
    import tempfile
    from app.clearclaim.etl import import_dataset
    from app.clearclaim.orchestrator import Orchestrator
    d = tempfile.mkdtemp()
    generate_synthetic_dataset(d, seed=seed, n_encounters=n, error_rate=0.8)
    CCBase.metadata.drop_all(engine)
    CCBase.metadata.create_all(engine)
    invalidate_context()
    db = SessionLocal()
    import_dataset(db, d, d, "demo", "SYNTHETIC")
    for c in db.query(ClaimCase):
        Orchestrator(db).process(c)
    return db


def test_production_without_token_is_read_only_and_wrong_token_rejected(client, monkeypatch):
    from app.clearclaim.config import settings
    db = _fresh_cases()
    cid = db.query(ClaimCase).first().id
    monkeypatch.setattr(settings, "write_token", "")
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.post(f"/api/v1/cases/{cid}/process").status_code == 503
    assert client.get(f"/api/v1/cases/{cid}").status_code == 200          # reads still work
    monkeypatch.setattr(settings, "write_token", "correct-horse")
    assert client.post(f"/api/v1/cases/{cid}/process", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(f"/api/v1/cases/{cid}/process", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.post(f"/api/v1/cases/{cid}/process",
                       headers={"Authorization": "Bearer correct-horse"}).status_code == 200
    db.close()


def test_stale_or_duplicate_correction_returns_409(client):
    db = _fresh_cases()
    c = next(c for c in db.query(ClaimCase) if c.encounter.claim_admin)
    body = dict(op="add_document", payload={"document": "extra_note"}, reason="first submit",
                expected_version=c.version)
    assert client.post(f"/api/v1/cases/{c.id}/corrections", json=body).status_code == 200
    again = client.post(f"/api/v1/cases/{c.id}/corrections", json=body)    # double-click / stale form
    assert again.status_code == 409 and "reload" in again.json()["detail"]
    db.close()


def test_chunked_body_without_length_is_rejected(client):
    def gen():
        yield b'{"values": {}}'
    r = client.put("/api/v1/settings", content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 411


def test_audit_chain_verifies_and_detects_tampering(client):
    from app.clearclaim.models import AuditEvent
    db = _fresh_cases(n=6)
    v = client.get("/api/v1/audit-events/verify").json()
    assert v["ok"] is True and v["checked"] > 10 and v["unchained_legacy_events"] == 0
    ev = db.query(AuditEvent).order_by(AuditEvent.id).offset(5).first()
    ev.summary = ev.summary + " (edited)"                                  # simulate tampering
    db.commit()
    v2 = client.get("/api/v1/audit-events/verify").json()
    assert v2["ok"] is False and v2["first_broken_event_id"] == ev.id
    db.close()
    CCBase.metadata.drop_all(engine)
    invalidate_context()
