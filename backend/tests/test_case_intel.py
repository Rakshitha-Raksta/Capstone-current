"""
Historical-case intelligence: retrieval, similarity explanation, honest outcome accounting,
insufficient-data states, and the guarantee that predictions never touch deterministic results.
"""
import pytest

from app.clearclaim import case_intel as CI
from app.clearclaim.context import invalidate_context
from app.clearclaim.models import AuditEvent, CCBase, ClaimCase, Finding, IssueEvent, ReviewTask, TrackedIssue
from app.clearclaim.orchestrator import Orchestrator
from app.database.db import SessionLocal, engine
from app.datasets.synthetic import generate_synthetic_dataset


def feat(case_id=1, **kw):
    """A complete feature dict as produced by case_intel._features, with overrides."""
    base = dict(hadm_id=1000 + case_id, encounter_id=case_id, subject_id=case_id, data_source="SYNTHETIC",
                dx_codes=frozenset({"I10", "E119"}), dx_cats=frozenset({"I10", "E11"}), principal_cat="I10", principal="I10",
                px_codes=frozenset(), drg_code="291", drg_type="HCFA", drg_description="Heart failure", los=4,
                bill_items=frozenset({"ROOM-GW", "LAB-ADMIT"}), bill_mix={"ROOM": 0.8, "LAB": 0.2}, bill_total=12000.0,
                policy_version="POL-A", has_policy=True, claim_type="CASHLESS", room_category="GENERAL",
                admission_type="EW EMER.", case_id=case_id, case_ref=f"CC-{case_id}", processed=True,
                status="NEEDS_REVIEW", observed=frozenset(), occurrences=[])
    base.update(kw)
    if "dx_codes" in kw and "dx_cats" not in kw:
        base["dx_cats"] = frozenset(c[:3] for c in base["dx_codes"])
    return base


def corpus_of(*hs, n_corrections=0, n_decisions=0):
    return dict(encounters={h["case_id"]: h for h in hs}, n_corrections=n_corrections, n_decisions=n_decisions,
                data_sources={"SYNTHETIC": len(hs)})


# ---------------------------------------------------------------------------
# 2. similarity scores and matching features are correct
# ---------------------------------------------------------------------------

def test_identical_encounters_score_one_and_procedures_not_compared_when_absent():
    s = CI.similarity(feat(1), feat(2), "general")
    assert s["score"] == 1.0
    px = next(f for f in s["features"] if f["feature"] == "procedures")
    assert px["used"] is False and "neither encounter" in px["why"]
    assert s["coverage"] == pytest.approx(0.85)          # procedures weight (0.15) left out of the denominator


def test_partial_similarity_matches_hand_computation():
    q = feat(1)
    h = feat(2, dx_codes=frozenset({"I10", "N179"}), principal_cat="I10", los=8, drg_code="292",
             bill_items=frozenset({"ROOM-GW", "ICU-DAY"}), bill_mix={"ROOM": 0.5, "ICU": 0.5},
             policy_version="POL-B", room_category="PRIVATE")
    fs = CI.feature_similarity(q, h)
    # diagnoses: categories {I10,E11} vs {I10,N17} -> 1/3; codes {I10,E119} vs {I10,N179} -> 1/3; same principal -> 1
    assert fs["diagnoses"][0] == pytest.approx(0.5 / 3 + 0.3 / 3 + 0.2)
    assert fs["drg"][0] == 0.0 and fs["length_of_stay"][0] == pytest.approx(0.5)
    cos = 0.8 * 0.5 / ((0.8 ** 2 + 0.2 ** 2) ** 0.5 * (0.5 ** 2 * 2) ** 0.5)
    assert fs["bill"][0] == pytest.approx(0.5 * (1 / 3) + 0.5 * cos)
    assert fs["policy"][0] == 0.0 and fs["admin"][0] == pytest.approx(2 / 3)
    s = CI.similarity(q, h, "general")
    w = CI.PROFILES["general"]
    used = {k: v[0] for k, v in fs.items() if v[0] is not None}
    expected = sum(w[k] * v for k, v in used.items()) / sum(w[k] for k in used)
    assert s["score"] == pytest.approx(expected, abs=1e-4)
    assert any("same principal diagnosis category I10" in m for m in s["matched"])
    assert sum(f["contribution"] for f in s["features"]) == pytest.approx(s["score"], abs=0.01)


def test_profiles_weight_the_domains_each_check_reads():
    assert CI.profile_for_code("UNIT_PRICE_ABOVE_TARIFF") == "billing"
    assert CI.profile_for_code("INVALID_ICD_CODE") == "coding"
    assert CI.profile_for_code("MISSING_CLAIM_DOCUMENT") == "readiness"
    assert CI.profile_for_code("WAITING_PERIOD_NOT_MET") == "insurance"
    for w in CI.PROFILES.values():
        assert sum(w.values()) == pytest.approx(1.0)


def test_wilson_interval_values():
    assert CI.wilson(0, 10) == (0.0, pytest.approx(0.2775, abs=1e-3))
    lo, hi = CI.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)


# ---------------------------------------------------------------------------
# 3 + 4. unreviewed is not resolved; insufficient data is explicit
# ---------------------------------------------------------------------------

def _hist(cid, outcome, op=None, code="DUPLICATE_BILL_ITEM"):
    action = dict(kind="CORRECTION", op=op, domain="bill") if op else None
    return feat(cid, observed=frozenset({code}), occurrences=[dict(code=code, outcome=outcome, action=action, reopened=0)])


def test_unreviewed_history_is_not_counted_as_resolved():
    hist = [_hist(i, "UNREVIEWED") for i in range(2, 14)]
    rec = CI.recommend(corpus_of(feat(1), *hist), feat(1), ["DUPLICATE_BILL_ITEM"], exclude=[1])[0]
    assert rec["status"] == "INSUFFICIENT_DATA"
    assert rec["outcomes"]["resolved"] == 0 and rec["outcomes"]["unreviewed"] == 10 and rec["n_reviewed"] == 0
    assert "not counted as resolved" in rec["reason"]
    assert "action" not in rec and rec["rule_guidance_label"].startswith("Deterministic rule guidance")


def test_recorded_resolutions_produce_an_evidenced_recommendation():
    hist = ([_hist(i, "RESOLVED", op="delete_bill_line") for i in range(2, 6)]
            + [_hist(6, "DISMISSED"), _hist(7, "UNREVIEWED"), _hist(8, "UNKNOWN")])
    rec = CI.recommend(corpus_of(feat(1), *hist), feat(1), ["DUPLICATE_BILL_ITEM"], exclude=[1])[0]
    assert rec["status"] == "RECOMMENDED"
    assert rec["action"]["op"] == "delete_bill_line"
    assert rec["support"] == dict(resolved_with_action=4, resolved_total=4, reviewed=5)    # unreviewed/unknown excluded
    assert rec["outcomes"]["unreviewed"] == 1 and rec["outcomes"]["unknown"] == 1
    assert len(rec["evidence"]) == 7 and all("similarity" in e for e in rec["evidence"])


def test_too_few_historical_encounters_is_insufficient_data():
    hist = [feat(i, observed=frozenset({"LATE_FILING"})) for i in range(2, 2 + CI.MIN_CORPUS - 1)]
    out = CI.predict(corpus_of(feat(1), *hist), feat(1), exclude=[1])
    assert out["status"] == "INSUFFICIENT_DATA" and out["items"] == []


def test_small_neighbourhood_gives_no_likelihood():
    hist = [feat(i, observed=frozenset({"LATE_FILING"})) for i in range(2, 14)]
    out = CI.predict(corpus_of(feat(1), *hist), feat(1), exclude=[1], k_pred=CI.MIN_COMPARABLE - 1)
    item = next(i for i in out["items"] if i["code"] == "LATE_FILING")
    assert item["status"] == "INSUFFICIENT_DATA" and item["likelihood"] is None and item["interval"] is None


def test_prediction_flags_only_statistically_elevated_categories():
    near = [feat(i, observed=frozenset({"MISSING_CLAIM_DOCUMENT"})) for i in range(2, 12)]        # identical admin
    far = [feat(i, claim_type="REIMBURSEMENT", room_category="PRIVATE", admission_type="ELECTIVE", policy_version="POL-Z",
                observed=frozenset()) for i in range(12, 60)]
    out = CI.predict(corpus_of(feat(1), *near, *far), feat(1), exclude=[1], k_pred=10, current_codes=frozenset())
    item = next(i for i in out["items"] if i["code"] == "MISSING_CLAIM_DOCUMENT")
    assert item["n_comparable"] == 10 and item["n_observed"] == 10
    assert item["base_rate"] == pytest.approx(10 / 58, abs=1e-4)
    assert item["signal"] == "ELEVATED_VS_BASE_RATE" and "MISSING_CLAIM_DOCUMENT" in out["predicted_codes"]
    assert item["deterministic_status"] == "PREDICTION_ONLY" and "not a verified finding" in item["label"]
    out2 = CI.predict(corpus_of(feat(1), *near, *far), feat(1), exclude=[1], k_pred=10,
                      current_codes=frozenset({"MISSING_CLAIM_DOCUMENT"}))
    assert next(i for i in out2["items"] if i["code"] == "MISSING_CLAIM_DOCUMENT")["deterministic_status"] == "CONFIRMED_BY_RULE"


# ---------------------------------------------------------------------------
# 1 + 5. retrieval on a real processed cohort; prediction never changes deterministic results
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    from app.clearclaim.etl import import_dataset
    d = tmp_path_factory.mktemp("case_intel")
    generate_synthetic_dataset(str(d), seed=41, n_encounters=30, error_rate=0.6)
    CCBase.metadata.drop_all(engine)
    CCBase.metadata.create_all(engine)
    invalidate_context()
    CI._cache.clear()
    db = SessionLocal()
    assert import_dataset(db, str(d), str(d), "demo", "SYNTHETIC").status == "COMPLETED"
    orch = Orchestrator(db, "parallel", "targeted")
    for c in db.query(ClaimCase).order_by(ClaimCase.id):
        orch.process(c)
    db.close()
    yield d
    CCBase.metadata.drop_all(engine)
    invalidate_context()
    CI._cache.clear()


def test_similar_cases_are_retrieved_and_explained(processed):
    db = SessionLocal()
    corpus = CI.build_corpus(db, use_cache=False)
    c = db.query(ClaimCase).order_by(ClaimCase.id).first()
    out = CI.case_intelligence(db, c.id, k=5, corpus=corpus)
    assert out["status"] == "OK" and len(out["similar"]) == 5
    scores = [s["similarity"] for s in out["similar"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)
    assert c.id not in {s["case_id"] for s in out["similar"]}                       # leave-one-out
    for s in out["similar"]:
        assert s["features"] and all({"feature", "weight", "used", "why"} <= set(f) for f in s["features"])
        again = CI.similarity(corpus["encounters"][c.id], corpus["encounters"][s["case_id"]], "general")["score"]
        assert again == s["similarity"]
    assert out["data_basis"]["historical_encounters"] == 29
    assert out["predictions"]["status"] in ("OK", "INSUFFICIENT_DATA")
    assert out["limitations"] and "not a trained" in " ".join(out["limitations"])
    db.close()


def test_prediction_api_does_not_create_or_change_deterministic_results(processed, client):
    db = SessionLocal()
    def snap():
        return (db.query(Finding).count(), db.query(ReviewTask).count(), db.query(TrackedIssue).count(),
                db.query(IssueEvent).count(), db.query(AuditEvent).count(),
                tuple((c.id, c.status, c.root_blocker_code, c.open_tasks) for c in db.query(ClaimCase).order_by(ClaimCase.id)))
    before = snap()
    ids = [c.id for c in db.query(ClaimCase).order_by(ClaimCase.id).limit(6)]
    db.close()
    for cid in ids:
        r = client.get(f"/api/v1/intel/cases/{cid}/similar-cases?k=5&k_pred=10")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "PREDICTION_AND_HISTORICAL_EVIDENCE" and "never creates findings" in body["notice"]
        for item in (body.get("predictions") or {}).get("items", []):
            assert item["deterministic_status"] in ("PREDICTION_ONLY", "CONFIRMED_BY_RULE")
            if item["status"] == "ESTIMATED":
                assert item["n_comparable"] >= CI.MIN_COMPARABLE and item["interval"]
    db = SessionLocal()
    assert snap() == before
    db.close()
    assert client.get("/api/v1/intel/cases/999999/similar-cases").status_code == 404
