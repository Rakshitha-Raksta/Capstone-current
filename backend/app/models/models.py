"""
SQLAlchemy models for the Provenance-First Indian Health Insurance Claim Validator.

SCOPE NOTE: Uses SQLite by default; PostgreSQL is supported through DATABASE_URL.
Rationale: no separate DB server process needed, same SQLAlchemy ORM layer,
trivially swappable to Postgres later by changing the connection string in
database/db.py.
"""
from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime, ForeignKey, Numeric, Boolean
)
from sqlalchemy.orm import relationship, declarative_base
import datetime

Base = declarative_base()


class Insurer(Base):
    __tablename__ = "insurers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    products = relationship("Policy", back_populates="insurer")


class Policy(Base):
    __tablename__ = "policies"
    id = Column(Integer, primary_key=True)
    insurer_id = Column(Integer, ForeignKey("insurers.id"), nullable=False)
    product_name = Column(String, nullable=False)
    insurer = relationship("Insurer", back_populates="products")
    versions = relationship("PolicyVersion", back_populates="policy")


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id = Column(Integer, primary_key=True)
    policy_id = Column(Integer, ForeignKey("policies.id"), nullable=False)
    policy_version_id = Column(String, nullable=False, unique=True)  # e.g. star_assure_2026_v1
    uin = Column(String, nullable=False)
    status = Column(String, default="ACTIVE")
    uin_conflict_flag = Column(Boolean, default=False)
    policy = relationship("Policy", back_populates="versions")
    rules = relationship("PolicyRule", back_populates="policy_version")
    documents = relationship("Document", back_populates="policy_version")


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    document_id = Column(String, nullable=False, unique=True)
    policy_version_db_id = Column(Integer, ForeignKey("policy_versions.id"), nullable=True)
    document_type = Column(String)
    source_url = Column(Text)
    sha256 = Column(String)
    hash_type = Column(String)  # ORIGINAL_FILE | EXTRACTED_TEXT
    original_file_available = Column(Boolean, default=False)
    page_count = Column(Integer)
    status = Column(String)
    notes = Column(Text)
    policy_version = relationship("PolicyVersion", back_populates="documents")


class PolicyRule(Base):
    __tablename__ = "policy_rules"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(String, nullable=False, unique=True)
    policy_version_db_id = Column(Integer, ForeignKey("policy_versions.id"), nullable=True)
    rule_type = Column(String, nullable=False)
    rule_name = Column(String)
    condition = Column(Text)
    value = Column(String)
    unit = Column(String)
    applies_to = Column(Text)
    exception = Column(Text)
    source_document = Column(String)
    source_page = Column(String)
    source_section = Column(String)
    source_text = Column(Text)
    extraction_method = Column(String)
    confidence = Column(String)
    review_status = Column(String, default="PENDING")
    provenance = Column(String)
    policy_version = relationship("PolicyVersion", back_populates="rules")


class RegulatoryRule(Base):
    __tablename__ = "regulatory_rules"
    id = Column(Integer, primary_key=True)
    regulation_id = Column(String, nullable=False, unique=True)
    topic = Column(String)
    requirement = Column(Text)
    value = Column(String)
    unit = Column(String)
    applicability = Column(Text)
    effective_date = Column(String)
    source_document = Column(String)
    source_page = Column(String)
    source_section = Column(String)
    source_text = Column(Text)
    source_url = Column(Text)
    provenance = Column(String)


class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    synthetic_patient_id = Column(String, unique=True, nullable=False)
    age = Column(Integer)
    gender = Column(String)


class Claim(Base):
    __tablename__ = "claims"
    id = Column(Integer, primary_key=True)
    claim_ref = Column(String, unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    policy_version_db_id = Column(Integer, ForeignKey("policy_versions.id"), nullable=False)
    policy_start_date = Column(Date, nullable=False)
    policy_end_date = Column(Date)  # optional; enables POLICY_EXPIRED evaluation when supplied
    patient_ref = Column(String)  # display-only synthetic patient identifier, no real PII
    date_of_birth = Column(Date)
    gender = Column(String)
    hospital_id = Column(String)
    procedure_description = Column(Text)
    procedure_code = Column(String)
    admission_date = Column(Date)
    discharge_date = Column(Date)
    room_type = Column(String)
    room_rent_per_day = Column(Numeric)
    diagnosis_code = Column(String)
    diagnosis_description = Column(Text)
    billed_amount = Column(Numeric)
    preauth_status = Column(String, default="NONE")
    claim_type = Column(String)  # EMERGENCY | PLANNED -- needed for notification/preauth deadline rules
    preauth_request_date = Column(Date)
    claim_filed_date = Column(Date)  # date reimbursement documents were submitted
    notification_date = Column(Date)  # date insurer was notified of hospitalization
    sum_insured = Column(Numeric)
    insured_age_at_entry = Column(Integer)
    treatment_category = Column(String)  # free text matched against SUB_LIMIT applies_to, e.g. "Home Care Treatment"
    category_billed_amount = Column(Numeric)  # amount billed specifically for treatment_category
    deductible_opted = Column(Boolean, default=False)
    deductible_amount_opted = Column(Numeric)
    documents_submitted = Column(Text)  # comma-separated free text, e.g. "claim_form,photo_id,KYC"
    policy_cancelled_date = Column(Date)  # if policyholder cancelled before admission
    claim_provenance = Column(String, default="SYNTHETIC")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    validation_results = relationship("ValidationResult", back_populates="claim")


class ValidationRun(Base):
    __tablename__ = "validation_runs"
    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"))
    run_at = Column(DateTime, default=datetime.datetime.utcnow)
    overall_result = Column(String)  # SUBMISSION_READY | FIX_BEFORE_SUBMISSION | HUMAN_REVIEW_NEEDED


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"))
    validation_run_id = Column(Integer, ForeignKey("validation_runs.id"))
    rule_id = Column(String)
    category = Column(String)
    severity = Column(String)  # PASS | WARNING | PARTIAL_DEDUCTION | FAIL
    reason = Column(Text)
    expected = Column(String)
    actual = Column(String)
    source_document = Column(String)
    source_page = Column(String)
    source_section = Column(String)
    provenance = Column(String)
    claim = relationship("Claim", back_populates="validation_results")
