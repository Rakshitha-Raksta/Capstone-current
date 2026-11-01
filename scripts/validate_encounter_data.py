"""
Validate an encounter dataset directory.

    python scripts/validate_encounter_data.py                       # synthetic demo
    python scripts/validate_encounter_data.py --clinical data/mimic_iv/demo \
        --admin data/synthetic/admin_for_mimic_demo                 # real demo + synthetic layer

Exit code 1 if any ERROR is found.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.datasets.validate import validate_dataset  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinical", default=os.path.join(ROOT, "data", "synthetic", "demo"))
    ap.add_argument("--admin", default=None)
    args = ap.parse_args()
    rep = validate_dataset(args.clinical, args.admin)
    print(json.dumps(rep["stats"], indent=2, default=str))
    print(f"ERRORS: {len(rep['errors'])}")
    for e in rep["errors"]:
        print("  ERROR:", e)
    print(f"WARNINGS: {len(rep['warnings'])}")
    for w in rep["warnings"]:
        print("  WARNING:", w)
    sys.exit(1 if rep["errors"] else 0)


if __name__ == "__main__":
    main()
