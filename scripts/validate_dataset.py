#!/usr/bin/env python3
"""Validate the Phase A dataset CSVs. Prints real counts and genuine errors/warnings.
Also writes data/structured/dataset_validation_report.json with the same real
numbers, for machine/mentor consumption alongside the printed output."""
import csv
import os
import json
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRUCT = os.path.join(BASE, "data", "structured")


def load(name):
    with open(os.path.join(STRUCT, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    docs = load("source_documents.csv")
    inv = load("policy_inventory.csv")
    rules = load("policy_rule_candidates.csv")

    errors, warnings = [], []

    for d in docs:
        if not d["source_url"]:
            errors.append(f"{d['document_id']}: missing source_url")
        if d["hash_type"] not in ("ORIGINAL_FILE", "EXTRACTED_TEXT"):
            errors.append(f"{d['document_id']}: invalid hash_type {d['hash_type']!r}")
        if d["hash_type"] == "EXTRACTED_TEXT":
            warnings.append(
                f"{d['document_id']}: hash_type=EXTRACTED_TEXT (genuine environment limitation -- "
                f"original PDF bytes were not archived; see source_documents.csv notes)"
            )
        if not d["sha256"] or len(d["sha256"]) != 64:
            errors.append(f"{d['document_id']}: sha256 missing or malformed")
        if not d["page_count"] or not d["page_count"].isdigit():
            errors.append(f"{d['document_id']}: missing/invalid page_count")

    known_doc_ids = {d["document_id"] for d in docs}
    known_pv_ids = {r["policy_version_id"] for r in inv}

    for r in rules:
        if r["source_document"] != "NOT_SPECIFIED_IN_SOURCE" and r["source_document"] not in known_doc_ids:
            errors.append(f"{r['candidate_id']}: source_document {r['source_document']!r} not in source_documents.csv")
        if not r["source_page"]:
            errors.append(f"{r['candidate_id']}: missing source_page")
        if not r["source_text"]:
            errors.append(f"{r['candidate_id']}: missing source_text")
        if r["provenance"] not in ("INSURER_DOCUMENT", "REGULATORY_DOCUMENT"):
            errors.append(f"{r['candidate_id']}: unexpected provenance {r['provenance']!r}")
        if r["review_status"] not in ("PENDING", "APPROVED", "REJECTED", "NEEDS_REVIEW"):
            errors.append(f"{r['candidate_id']}: invalid review_status {r['review_status']!r}")
        if r["provenance"] == "INSURER_DOCUMENT" and r["policy_version"] not in known_pv_ids:
            errors.append(f"{r['candidate_id']}: policy_version {r['policy_version']!r} not in policy_inventory.csv")

    # Cross-version leakage check: no candidate_id prefix mismatched to a rule
    # from a different insurer/version than its own prefix implies.
    for r in rules:
        if r["candidate_id"].startswith("SA26-") and r["policy_version"] != "star_assure_2026_v1":
            errors.append(f"{r['candidate_id']}: prefix/version mismatch (cross-version leakage risk)")
        if r["candidate_id"].startswith("HDFC21-") and r["policy_version"] != "hdfc_optima_secure_2021_v1":
            errors.append(f"{r['candidate_id']}: prefix/version mismatch (cross-version leakage risk)")

    print(f"documents: {len(docs)}")
    print(f"policy_versions: {len(inv)}")
    print(f"insurers: {len(set(d['insurer'] for d in inv))}")
    print(f"rule_candidates: {len(rules)}")
    print(f"  insurer-document rules: {sum(1 for r in rules if r['provenance']=='INSURER_DOCUMENT')}")
    print(f"  regulatory rules: {sum(1 for r in rules if r['provenance']=='REGULATORY_DOCUMENT')}")
    print(f"ERRORS: {len(errors)}")
    for e in errors:
        print("  ERROR:", e)
    print(f"WARNINGS: {len(warnings)}")
    for w in warnings:
        print("  WARNING:", w)

    review_status_counts = {}
    for r in rules:
        review_status_counts[r["review_status"]] = review_status_counts.get(r["review_status"], 0) + 1
    rule_type_counts = {}
    for r in rules:
        rule_type_counts[r["rule_type"]] = rule_type_counts.get(r["rule_type"], 0) + 1

    report = {
        "generated_at_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "counts": {
            "documents": len(docs),
            "policy_versions": len(inv),
            "insurers": len(set(d["insurer"] for d in inv)),
            "rule_candidates_total": len(rules),
            "rule_candidates_insurer_document": sum(1 for r in rules if r["provenance"] == "INSURER_DOCUMENT"),
            "rule_candidates_regulatory_document": sum(1 for r in rules if r["provenance"] == "REGULATORY_DOCUMENT"),
            "rule_candidates_by_review_status": review_status_counts,
            "rule_candidates_by_rule_type": rule_type_counts,
        },
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    report_path = os.path.join(STRUCT, "dataset_validation_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nWrote {report_path}")

    return len(errors)


if __name__ == "__main__":
    raise SystemExit(main())
