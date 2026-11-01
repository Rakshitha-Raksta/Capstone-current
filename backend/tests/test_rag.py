"""
RAG test suite. Covers, per project requirement:
  A. Correct retrieval
  B. Policy isolation
  C. Missing evidence -> "Not found in the selected policy source."
  D. Citation correctness
  E. Cross-version leakage
  F. No-decision guarantee (RAG cannot change validation severity)

Uses the REAL TF-IDF index built from the real extracted documents
(scripts/build_rag_index.py must have been run -- conftest.py's session
fixture ensures this happens before these tests run).
"""
from app.rag.retrieve import retrieve
from app.rag.service import answer_question
from app.rules.engine import validate_claim


# ---------- A. Correct retrieval ----------

def test_retrieval_finds_room_rent_clause_for_star():
    results = retrieve("What is the room rent limit?", "star_assure_2026_v1", top_k=3)
    assert len(results) > 0
    assert any("room" in r.text.lower() or "sub-limit" in r.text.lower() for r in results)
    assert all(r.document_id.startswith("STAR_ASSURE") for r in results)


def test_retrieval_finds_specified_disease_waiting_period_for_star():
    results = retrieve("What is the waiting period for specified diseases?", "star_assure_2026_v1", top_k=3)
    assert len(results) > 0
    assert any("24 months" in r.text.lower() or "specified disease" in r.text.lower() for r in results)


# ---------- B. Policy isolation ----------

def test_hdfc_question_never_returns_star_chunks():
    results = retrieve("What is the initial waiting period?", "hdfc_optima_secure_2021_v1", top_k=5, threshold=0.0)
    assert len(results) > 0
    assert all(r.document_id.startswith("HDFC") for r in results)
    assert all("STAR" not in r.document_id for r in results)


def test_star_question_never_returns_hdfc_chunks():
    results = retrieve("What is the initial waiting period?", "star_assure_2026_v1", top_k=5, threshold=0.0)
    assert len(results) > 0
    assert all(r.document_id.startswith("STAR") for r in results)
    assert all("HDFC" not in r.document_id for r in results)


# ---------- C. Missing evidence ----------

def test_zero_domain_overlap_question_is_refused():
    """A question with genuinely zero vocabulary overlap with any insurance
    document must be refused, not answered with a spurious match."""
    result = answer_question("What is the recipe for chocolate chip cookies?", "star_assure_2026_v1")
    assert result["found"] is False
    assert result["answer"] == "Not found in the selected policy source."
    assert result["citations"] == []
    assert result["sources"] == []


def test_unrelated_but_present_topic_question():
    result = answer_question("Who won the cricket world cup in 2011?", "star_assure_2026_v1")
    assert result["found"] is False


# ---------- D. Citation correctness ----------

def test_citations_correspond_to_real_stored_chunks():
    from app.rag.embeddings import load_index
    chunks, _, _ = load_index()
    known_ids = {c.chunk_id for c in chunks}

    result = answer_question("What is the room rent limit?", "star_assure_2026_v1")
    assert result["found"] is True
    for citation in result["citations"]:
        assert citation["chunk_id"] in known_ids, f"citation references unknown chunk_id {citation['chunk_id']}"
        assert citation["page"] != "" and citation["page"] is not None
        assert "document" in citation
    for source in result["sources"]:
        assert source["document"].startswith("STAR_ASSURE")


def test_citations_have_correct_policy_version():
    from app.rag.embeddings import load_index
    chunks, _, _ = load_index()
    chunk_by_id = {c.chunk_id: c for c in chunks}

    result = answer_question("What is the initial waiting period?", "hdfc_optima_secure_2021_v1")
    assert result["found"] is True
    for citation in result["citations"]:
        chunk = chunk_by_id[citation["chunk_id"]]
        assert chunk.policy_version_id == "hdfc_optima_secure_2021_v1"


# ---------- E. Cross-version leakage (same question, ambiguous across insurers) ----------

def test_same_question_scoped_correctly_across_both_insurers():
    """'What is the initial waiting period?' is a question BOTH Star and
    HDFC documents can answer -- verify each policy_version_id only ever
    surfaces its own document's chunks, never the other's, even though
    the question itself doesn't name an insurer."""
    star_results = retrieve("What is the initial waiting period?", "star_assure_2026_v1", top_k=5, threshold=0.0)
    hdfc_results = retrieve("What is the initial waiting period?", "hdfc_optima_secure_2021_v1", top_k=5, threshold=0.0)

    star_doc_ids = {r.document_id for r in star_results}
    hdfc_doc_ids = {r.document_id for r in hdfc_results}
    assert star_doc_ids.isdisjoint(hdfc_doc_ids), (
        f"Leakage detected: {star_doc_ids} vs {hdfc_doc_ids} should not overlap"
    )


# ---------- F. No-decision guarantee ----------

def test_rag_cannot_change_validation_severity(db):
    """Asking a RAG question about a claim's policy must never alter the
    deterministic validation result for that same claim."""
    from app.models.models import Claim, PolicyVersion
    from datetime import date

    pv = db.query(PolicyVersion).filter_by(policy_version_id="star_assure_2026_v1").first()
    claim = Claim(
        policy_version_db_id=pv.id,
        claim_ref="RAG-NO-DECISION-TEST",
        policy_start_date=date(2024, 8, 23),
        admission_date=date(2026, 7, 20),
        diagnosis_description="Cataract surgery",
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)

    _, results_before, _ = validate_claim(db, claim)
    severities_before = sorted((r["rule_id"], r["severity"]) for r in results_before)

    # Ask several RAG questions in between -- must not touch validation state.
    answer_question("Does this policy cover cataract treatment?", "star_assure_2026_v1")
    answer_question("What is the room rent limit?", "star_assure_2026_v1")

    _, results_after, _ = validate_claim(db, claim)
    severities_after = sorted((r["rule_id"], r["severity"]) for r in results_after)

    assert severities_before == severities_after
