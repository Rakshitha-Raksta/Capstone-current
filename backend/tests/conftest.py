import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///./test_claim_validator.db")

from app.database.db import engine, SessionLocal
from app.models.models import Base
from app.database import seed as seed_module


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    seed_module.engine = engine
    seed_module.SessionLocal = SessionLocal
    seed_module.run()

    # Build the RAG index against this same test database so RAG tests can
    # rely on it existing -- uses the real extracted-text files, not test
    # fixtures, since the RAG corpus IS the real dataset.
    from app.models.models import Document, PolicyVersion
    from app.rag.chunking import build_all_chunks, DOCUMENT_TEXT_FILES
    from app.rag.embeddings import build_index

    db = SessionLocal()
    doc_to_pv = {}
    doc_to_prov = {}
    for doc in db.query(Document).all():
        if doc.document_id not in DOCUMENT_TEXT_FILES:
            continue
        pv_id = None
        if doc.policy_version_db_id:
            pv = db.query(PolicyVersion).get(doc.policy_version_db_id)
            pv_id = pv.policy_version_id if pv else None
        doc_to_pv[doc.document_id] = pv_id
        doc_to_prov[doc.document_id] = "REGULATORY_DOCUMENT" if pv_id is None else "INSURER_DOCUMENT"
    db.close()

    chunks = build_all_chunks(doc_to_pv, doc_to_prov)
    build_index(chunks)

    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
