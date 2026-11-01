"""
Local, offline "embedding" index using TF-IDF + cosine similarity
(scikit-learn). Chosen deliberately over sentence-transformers/a hosted
embedding API because:
  - no API key or network call is required (works fully offline)
  - the corpus here is tiny (a few dozen page-chunks), where TF-IDF
    performs perfectly adequately and avoids pulling in a multi-hundred-MB
    torch/transformers dependency for a project this size
  - it keeps the explainer local and adds no extra infrastructure

This module ONLY builds/loads the similarity index. It has no knowledge
of "answers" or LLMs -- that's answer.py's job.
"""
import json
import os
import pickle
from typing import List

from sklearn.feature_extraction.text import TfidfVectorizer

from app.rag.chunking import Chunk, retrieval_text

_INDEX_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "processed", "chunks"
))
_CHUNKS_JSON_PATH = os.path.join(_INDEX_DIR, "chunks.json")
_VECTORIZER_PATH = os.path.join(_INDEX_DIR, "tfidf_vectorizer.pkl")
_MATRIX_PATH = os.path.join(_INDEX_DIR, "tfidf_matrix.pkl")


def build_index(chunks: List[Chunk]):
    """Fit a TF-IDF vectorizer over all chunk texts and persist everything
    needed to reload the index later without re-fitting."""
    os.makedirs(_INDEX_DIR, exist_ok=True)
    texts = [retrieval_text(c.text) for c in chunks]
    vectorizer = TfidfVectorizer(stop_words="english", max_df=0.85, sublinear_tf=True)
    matrix = vectorizer.fit_transform(texts)

    with open(_CHUNKS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump([c.to_dict() for c in chunks], f, indent=2)
    with open(_VECTORIZER_PATH, "wb") as f:
        pickle.dump(vectorizer, f)
    with open(_MATRIX_PATH, "wb") as f:
        pickle.dump(matrix, f)

    return vectorizer, matrix


def load_index():
    """Load a previously-built index. Raises FileNotFoundError with a clear
    message if build_index() (via scripts/build_rag_index.py) hasn't been
    run yet -- never silently falls back to fabricated data."""
    if not (os.path.exists(_CHUNKS_JSON_PATH) and os.path.exists(_VECTORIZER_PATH)
            and os.path.exists(_MATRIX_PATH)):
        raise FileNotFoundError(
            "RAG index not found. Run `python scripts/build_rag_index.py` first."
        )
    with open(_CHUNKS_JSON_PATH, "r", encoding="utf-8") as f:
        chunk_dicts = json.load(f)
    chunks = [Chunk(**d) for d in chunk_dicts]
    with open(_VECTORIZER_PATH, "rb") as f:
        vectorizer = pickle.load(f)
    with open(_MATRIX_PATH, "rb") as f:
        matrix = pickle.load(f)
    return chunks, vectorizer, matrix
