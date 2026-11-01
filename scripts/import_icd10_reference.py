"""
Extract the ICD-10-CM code table from the team's PostgreSQL dump
(claims_validation.sql) into data/reference/icd10cm_codes.csv.

Provenance: USER_SUPPLIED_REFERENCE. The dump's icd10_codes table carries
fiscal_year=2025 and its rows match the public CMS ICD-10-CM code list
format (codes stored without the dot, as MIMIC-IV also stores them), but
the dump itself does not record where the rows were downloaded from.
Confirm the origin before citing it in the report.

Only the icd10_codes table is read; nothing else in the dump is used.

Usage:
    python scripts/import_icd10_reference.py --sql path/to/claims_validation.sql
"""
import argparse
import csv
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "reference")
OUT_CSV = os.path.join(OUT_DIR, "icd10cm_codes.csv")
OUT_META = os.path.join(OUT_DIR, "icd10cm_codes.meta.json")

COPY_HEADER = "COPY public.icd10_codes (code, description, is_valid, fiscal_year) FROM stdin;"


def extract(sql_path):
    rows = []
    in_block = False
    with open(sql_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not in_block:
                if line.strip() == COPY_HEADER:
                    in_block = True
                continue
            if line == "\\.":
                break
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            code, desc, is_valid, fy = parts
            rows.append({
                "icd_code": code,
                "icd_version": 10,
                "long_title": desc,
                "is_valid": is_valid == "t",
                "fiscal_year": fy,
            })
    if not in_block:
        raise SystemExit(f"icd10_codes COPY block not found in {sql_path}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", required=True)
    args = ap.parse_args()

    rows = extract(args.sql)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["icd_code", "icd_version", "long_title", "is_valid", "fiscal_year"])
        w.writeheader()
        w.writerows(rows)

    with open(args.sql, "rb") as f:
        src_sha = hashlib.sha256(f.read()).hexdigest()
    with open(OUT_CSV, "rb") as f:
        out_sha = hashlib.sha256(f.read()).hexdigest()
    meta = {
        "provenance": "USER_SUPPLIED_REFERENCE",
        "description": "ICD-10-CM diagnosis codes extracted from team pg_dump table public.icd10_codes",
        "origin_note": "Dump does not record its download source; rows appear to be the CMS ICD-10-CM list "
                       "(fiscal_year column = 2025). Verify before citing.",
        "source_file": os.path.basename(args.sql),
        "source_sha256": src_sha,
        "output_sha256": out_sha,
        "row_count": len(rows),
    }
    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {len(rows)} ICD-10-CM rows to {OUT_CSV}")


if __name__ == "__main__":
    main()
