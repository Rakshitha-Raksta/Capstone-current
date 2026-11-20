"""
Database connection. Uses SQLite by default
(see models/models.py scope note). Swap DATABASE_URL to a Postgres DSN
(e.g. postgresql+psycopg2://user:pass@localhost/dbname) to switch engines
without any other code changes -- the ORM layer is engine-agnostic.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./claim_validator.db")

# timeout: seconds a SQLite writer waits for another writer (runs of the same case are serialised by a
# write lock taken at the start of each pipeline run; see Orchestrator._lock_case)
connect_args = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
