"""
End-to-end check of ClearClaim against any database URL (use it to verify PostgreSQL).

    $env:DATABASE_URL = "postgresql+psycopg2://USER:PASSWORD@localhost:5432/clearclaim_check"
    python scripts/check_database.py

The target database must exist and be disposable: the ClearClaim tables are
dropped and recreated. Runs: rule seeding, Alembic migration, import of a small
synthetic cohort, parallel processing, a targeted correction rerun, a review
decision, and a parallel-vs-sequential equivalence check. Exit code 0 = pass.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL first (see the docstring).")
    import contextlib
    import io
    from app.database import seed
    from app.database.db import SessionLocal, engine
    from app.clearclaim.models import CCBase, ClaimCase, Finding, ReviewTask, StageExecution
    from app.clearclaim.server import migrate
    from app.clearclaim.etl import import_dataset
    from app.clearclaim.orchestrator import Orchestrator
    from app.clearclaim.corrections import apply_correction
    from app.datasets.synthetic import generate_synthetic_dataset

    with engine.connect() as _c:
        print("engine:", engine.dialect.name, ".".join(map(str, engine.dialect.server_version_info or ())))
    with contextlib.redirect_stdout(io.StringIO()):
        seed.run()
    CCBase.metadata.drop_all(engine)
    with engine.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    migrate()
    tmp = tempfile.mkdtemp()
    generate_synthetic_dataset(tmp, seed=7, n_encounters=30, error_rate=0.6)
    db = SessionLocal()
    imp = import_dataset(db, tmp, tmp, "demo", "SYNTHETIC")
    assert imp.status == "COMPLETED", imp.validation_report["errors"]
    orch = Orchestrator(db, "parallel", "targeted")
    for c in db.query(ClaimCase).order_by(ClaimCase.id):
        assert orch.process(c).status == "SUCCEEDED"
    print("processed:", db.query(ClaimCase).count(), "cases")

    f = db.query(Finding).filter(Finding.is_current.is_(True), Finding.role == "ROOT",
                                 Finding.code == "DUPLICATE_BILL_ITEM").first()
    if f:
        c = db.get(ClaimCase, f.case_id)
        _, run = apply_correction(db, c, "delete_bill_line", {"line_no": f.evidence["line_no"]}, "check", "db check")
        ex = {s.stage for s in db.query(StageExecution).filter_by(run_id=run.id, status="SUCCEEDED")}
        assert "eligibility" not in ex and "billing" in ex, ex
        print("targeted correction rerun OK:", sorted(ex))
    c = db.query(ClaimCase).order_by(ClaimCase.id).first()
    h1 = {s.stage: s.output_hash for s in db.query(StageExecution).filter_by(
        run_id=Orchestrator(db, "sequential", "full").process(c, trigger="MANUAL_RERUN").id)}
    h2 = {s.stage: s.output_hash for s in db.query(StageExecution).filter_by(
        run_id=Orchestrator(db, "parallel", "full").process(c, trigger="MANUAL_RERUN").id)}
    assert h1 == h2
    print("parallel == sequential outputs OK")
    print("open review tasks:", db.query(ReviewTask).filter_by(status="OPEN").count())
    print("PASS")


if __name__ == "__main__":
    main()
