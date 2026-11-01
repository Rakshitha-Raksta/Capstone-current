#!/usr/bin/env python3
"""
Runs the 7 required demo questions against the real RAG index and prints
actual results. Expected values below were derived by inspecting the real
source documents (see comments per question) -- not invented.

Run: python scripts/test_rag.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(BASE, "backend", "claim_validator.db").replace("\\", "/"))

from app.rag.service import answer_question

# Each entry: (question, policy_version_id, expected_found,
#              expected_source_document, expected_page_or_None, note)
DEMO_QUESTIONS = [
    (
        "Does this policy cover cataract treatment?",
        "star_assure_2026_v1",
        True,
        "STAR_ASSURE_2026_CIS",
        "8",
        "Star CIS page 8 lists 'Cataract and diseases of anterior/posterior "
        "chamber of Eye' under the 24-month specified disease list.",
    ),
    (
        "What is the waiting period for specified diseases?",
        "star_assure_2026_v1",
        True,
        "STAR_ASSURE_2026_CIS",
        "8",
        "Star CIS page 8 states the 24-month specified disease waiting period.",
    ),
    (
        "What is the policy's initial waiting period?",
        "star_assure_2026_v1",
        True,
        "STAR_ASSURE_2026_CIS",
        "7",
        "Star CIS page 7 states the 30-day initial waiting period (Excl 03).",
    ),
    (
        "Is preauthorization required?",
        "star_assure_2026_v1",
        True,
        "STAR_ASSURE_2026_CIS",
        None,  # page varies by which preauth clause is retrieved
        "Star CIS pages 13-15 describe cashless preauthorization procedure/TAT.",
    ),
    (
        "What is the room rent limit?",
        "star_assure_2026_v1",
        True,
        "STAR_ASSURE_2026_CIS",
        "10",
        "Star CIS page 10 states the room rent sub-limit table by Sum Insured band.",
    ),
    (
        "What is the initial waiting period?",
        "hdfc_optima_secure_2021_v1",
        True,
        "HDFC_OPTIMA_SECURE_2021_HISTORICAL",
        None,
        "HDFC 2021 combined doc states the 30-day initial waiting period in "
        "its CIS summary section (page 2 in the extracted text).",
    ),
    (
        "What is the recipe for chocolate chip cookies?",
        "star_assure_2026_v1",
        False,
        None,
        None,
        "Deliberately unsupported question -- zero vocabulary overlap with "
        "any insurance document; must be refused.",
    ),
]


def main():
    results_summary = []
    for question, pv_id, expected_found, expected_doc, expected_page, note in DEMO_QUESTIONS:
        result = answer_question(question, pv_id)
        found_ok = result["found"] == expected_found
        doc_ok = True
        if expected_found and expected_doc:
            doc_ok = any(c["document"] == expected_doc for c in result["citations"])

        print(f"Q: {question!r} (policy_version={pv_id})")
        print(f"   note: {note}")
        print(f"   expected_found={expected_found}  actual_found={result['found']}  [{'OK' if found_ok else 'MISMATCH'}]")
        if result["found"]:
            print(f"   answer: {result['answer'][:150]}...")
            print(f"   citations: {[(c['document'], c['page'], c['relevance_score']) for c in result['citations']]}")
            print(f"   expected_document_present={doc_ok}")
        print()
        results_summary.append({
            "question": question, "found_correct": found_ok,
            "document_correct": doc_ok if expected_found else "N/A",
        })

    total = len(results_summary)
    found_correct = sum(1 for r in results_summary if r["found_correct"])
    doc_correct = sum(1 for r in results_summary if r["document_correct"] in (True, "N/A"))
    print(f"SUMMARY: {found_correct}/{total} found/refused correctly, "
          f"{doc_correct}/{total} document-attribution correct")


if __name__ == "__main__":
    main()
