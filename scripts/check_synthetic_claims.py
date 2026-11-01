#!/usr/bin/env python3
"""
Run every claim in data/synthetic/claims.json through the real deterministic
rule engine and confirm the actual severity for `derived_from_rule` matches
`fields.expected_severity`. This is an end-to-end consistency check, not a
new source of truth -- it must agree with backend/tests/test_rule_engine.py.
"""
import json
import os
import sys
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite:///./synthetic_check.db")

from app.database.db import engine, SessionLocal
from app.models.models import Claim, PolicyVersion
from app.database import seed as seed_module
from app.rules.engine import validate_claim


def main():
    seed_module.engine = engine
    seed_module.SessionLocal = SessionLocal
    seed_module.run()
    db = SessionLocal()

    with open(os.path.join(BASE, "data", "synthetic", "claims.json")) as f:
        synthetic_claims = json.load(f)

    passed, failed = 0, 0
    for i, sc in enumerate(synthetic_claims):
        pv = db.query(PolicyVersion).filter_by(policy_version_id=sc["policy_version_id"]).first()
        fields = dict(sc["fields"])
        expected_severity = fields.pop("expected_severity")
        for k in list(fields.keys()):
            if k.endswith("_date") and fields[k]:
                fields[k] = date.fromisoformat(fields[k])
        claim = Claim(
            policy_version_db_id=pv.id,
            claim_ref=f"{sc['claim_ref']}-{i}",
            policy_start_date=fields.pop("policy_start_date", date(2020, 1, 1)),
            admission_date=fields.pop("admission_date", date(2026, 1, 1)),
            **fields,
        )
        db.add(claim)
        db.commit()
        db.refresh(claim)

        _, results, _ = validate_claim(db, claim)
        matches = [r for r in results if r["rule_id"] == sc["derived_from_rule"]]

        if expected_severity == "NOT_APPLICABLE":
            ok = len(matches) == 0
            actual = "NOT_EVALUATED" if ok else matches[0]["severity"]
        else:
            ok = len(matches) == 1 and matches[0]["severity"] == expected_severity
            actual = matches[0]["severity"] if matches else "MISSING"

        status = "OK" if ok else "MISMATCH"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"[{status}] {sc['claim_ref']} ({sc['derived_from_rule']}): "
              f"expected={expected_severity} actual={actual}")

    print(f"\n{passed} passed, {failed} failed out of {len(synthetic_claims)} synthetic claims")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
