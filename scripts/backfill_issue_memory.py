"""
Link review tasks recorded before issue memory existed to persistent issues (additive, idempotent).

    cd backend
    python ../scripts/backfill_issue_memory.py            # uses DATABASE_URL or backend/claim_validator.db
    python ../scripts/backfill_issue_memory.py --dry-run  # report what would be linked, change nothing

Only recorded rows are used: each pre-existing task becomes (or is added to) an issue; detection counts
are reconstructed from the case's recorded successful pipeline runs while the task was active; a task
closed without a correction is recorded as NO_LONGER_DETECTED, never as RESOLVED. Tasks that already
have an issue are skipped, so running it twice changes nothing. Back up the database first.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from app.clearclaim.issues import backfill_all, backfill_case
    from app.clearclaim.models import ClaimCase, ReviewTask, TrackedIssue
    from app.clearclaim.orchestrator import audit
    from app.database.db import SessionLocal

    db = SessionLocal()
    try:
        pending = db.query(ReviewTask).filter(ReviewTask.issue_id.is_(None)).count()
        print(f"review tasks without an issue: {pending}; issues already tracked: {db.query(TrackedIssue).count()}")
        if args.dry_run:
            from collections import Counter
            rep = Counter()
            for c in db.query(ClaimCase).order_by(ClaimCase.id):
                rep.update(backfill_case(db, c, actor="backfill.dry_run"))
            db.rollback()
            print("dry run (rolled back):", json.dumps(dict(rep)))
            return
        rep = backfill_all(db, actor="backfill")
        if rep:
            audit(db, "ISSUE_MEMORY_BACKFILLED", f"linked {rep.get('tasks_linked', 0)} pre-existing review task(s) to "
                  f"{rep.get('issues_created', 0)} issue(s)", "backfill", entity_type="issue", **rep)
            db.commit()
        print("backfill:", json.dumps(rep))
        print(f"review tasks without an issue now: {db.query(ReviewTask).filter(ReviewTask.issue_id.is_(None)).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
