"""
Retrieval. CRITICAL RULE (per project requirement): the policy_version_id
filter happens BEFORE similarity scoring, never as a post-hoc filter on
globally-ranked results. This module's chunk_indices selection step runs
first; cosine similarity is computed ONLY on the pre-filtered subset, so
a chunk from a different policy version is never even scored against the
question, let alone returned.
"""
from dataclasses import dataclass
from typing import List

from sklearn.metrics.pairwise import cosine_similarity

from app.rag.embeddings import load_index

DEFAULT_SIMILARITY_THRESHOLD = 0.08  # calibrated empirically: 0.10 was too strict
# and rejected a genuinely-correct match (HDFC 2021's initial-waiting-period
# clause, bundled together with other exclusions in one chunk, scored 0.0947
# against "What is the initial waiting period?" -- just under a 0.10 cutoff).
# 0.08 keeps that true positive while zero-domain-overlap questions (e.g.
# "What is the recipe for chocolate chip cookies?") still score exactly 0.0
# and are correctly refused. Known remaining
# limitation: insurance-domain-ADJACENT-but-unsupported questions (e.g.
# "does this cover skydiving/pet insurance") can still occasionally score
# above this threshold on lexical overlap alone (e.g. a header containing
# "what the policy does not cover" scoring ~0.15-0.3 against almost any
# "does this policy cover X" phrasing) -- this is a disclosed, not hidden,
# weakness of bag-of-words TF-IDF retrieval on a small domain-narrow corpus.


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    document_id: str
    page: str
    policy_version_id: str
    similarity_score: float


def retrieve(question: str, policy_version_id: str, top_k: int = 3,
             threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> List[RetrievedChunk]:
    """
    Returns up to top_k chunks belonging ONLY to policy_version_id, ranked
    by similarity to `question`, above `threshold`. Empty list if nothing
    clears the threshold -- caller (service.py) turns that into the
    "Not found in the selected policy source." response.
    """
    chunks, vectorizer, matrix = load_index()

    # STEP 1 -- filter indices to this policy_version_id BEFORE any scoring.
    candidate_indices = [
        i for i, c in enumerate(chunks) if c.policy_version_id == policy_version_id
    ]
    if not candidate_indices:
        return []

    # STEP 2 -- score only the pre-filtered subset.
    question_vec = vectorizer.transform([question])
    candidate_matrix = matrix[candidate_indices]
    scores = cosine_similarity(question_vec, candidate_matrix)[0]

    scored = list(zip(candidate_indices, scores))
    scored.sort(key=lambda pair: pair[1], reverse=True)

    results = []
    for idx, score in scored[:top_k]:
        if score < threshold:
            continue
        c = chunks[idx]
        results.append(RetrievedChunk(
            chunk_id=c.chunk_id,
            text=c.text,
            document_id=c.document_id,
            page=c.page,
            policy_version_id=c.policy_version_id,
            similarity_score=float(score),
        ))
    return results


def retrieve_regulatory(question: str, top_k: int = 3,
                         threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> List[RetrievedChunk]:
    """Separate path for the regulatory (IRDAI) corpus, which is not scoped
    to any single policy_version_id. Kept as a distinct function so it can
    never be accidentally mixed into a policy-scoped retrieve() call."""
    chunks, vectorizer, matrix = load_index()
    candidate_indices = [i for i, c in enumerate(chunks) if c.provenance == "REGULATORY_DOCUMENT"]
    if not candidate_indices:
        return []
    question_vec = vectorizer.transform([question])
    candidate_matrix = matrix[candidate_indices]
    scores = cosine_similarity(question_vec, candidate_matrix)[0]
    scored = sorted(zip(candidate_indices, scores), key=lambda p: p[1], reverse=True)
    results = []
    for idx, score in scored[:top_k]:
        if score < threshold:
            continue
        c = chunks[idx]
        results.append(RetrievedChunk(
            chunk_id=c.chunk_id, text=c.text, document_id=c.document_id,
            page=c.page, policy_version_id="", similarity_score=float(score),
        ))
    return results
