"""
ClearClaim persistence model.

Uses its OWN declarative base (CCBase), separate from the legacy rule-engine
Base in app/models/models.py. The legacy seeder runs Base.metadata.drop_all()
every time the 59 rules are re-seeded; keeping ClearClaim tables on a
separate metadata means re-seeding rules can never wipe cases, corrections or
the audit trail. Schema changes for these tables go through Alembic
(backend/alembic/).

Ground-truth labels are deliberately NOT modelled here: they stay in
data/.../ground_truth/ and are only read by the offline experiment harness.
"""
import datetime as dt

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

CCBase = declarative_base()


def _now():
    return dt.datetime.utcnow()


class DatasetImport(CCBase):
    __tablename__ = "cc_dataset_imports"
    id = Column(Integer, primary_key=True)
    mode = Column(String(16), nullable=False)            # demo | mimic
    source_label = Column(String(64), nullable=False)    # SYNTHETIC | MIMIC_IV_DEMO_2.2 | ...
    clinical_root = Column(Text, nullable=False)
    admin_root = Column(Text, nullable=False)
    manifest_mode = Column(String(64))
    manifest_sha256 = Column(String(64))
    generator_version = Column(String(32))
    parameters = Column(JSON)
    n_encounters = Column(Integer, default=0)
    status = Column(String(16), default="COMPLETED")     # COMPLETED | FAILED
    validation_report = Column(JSON)
    imported_at = Column(DateTime, default=_now)


class Encounter(CCBase):
    __tablename__ = "cc_encounters"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, nullable=False, index=True)
    hadm_id = Column(Integer, nullable=False, unique=True)
    data_source = Column(String(64), nullable=False)
    import_id = Column(Integer, ForeignKey("cc_dataset_imports.id"))
    gender = Column(String(4))
    anchor_age = Column(Integer)
    admittime = Column(DateTime)
    dischtime = Column(DateTime)
    edregtime = Column(DateTime)          # MIMIC admissions.edregtime (ED registration), when present
    admission_type = Column(String(64))
    admission_location = Column(String(64))
    discharge_location = Column(String(64))
    insurance_category = Column(String(32))     # MIMIC admissions.insurance (US category), kept for provenance
    drg_code = Column(String(8))
    drg_type = Column(String(8))
    drg_description = Column(Text)
    created_at = Column(DateTime, default=_now)

    diagnoses = relationship("EncounterDiagnosis", cascade="all, delete-orphan", order_by="EncounterDiagnosis.seq_num")
    procedures = relationship("EncounterProcedure", cascade="all, delete-orphan", order_by="EncounterProcedure.seq_num")
    transfers = relationship("EncounterTransfer", cascade="all, delete-orphan", order_by="EncounterTransfer.intime")
    medications = relationship("EncounterMedication", cascade="all, delete-orphan")
    note = relationship("DischargeNote", uselist=False, cascade="all, delete-orphan")
    claim_admin = relationship("ClaimAdmin", uselist=False, cascade="all, delete-orphan")
    checklist = relationship("ChecklistItem", cascade="all, delete-orphan", order_by="ChecklistItem.id")
    bill_lines = relationship("BillLine", cascade="all, delete-orphan", order_by="BillLine.line_no")
    case = relationship("ClaimCase", uselist=False, back_populates="encounter")


class EncounterDiagnosis(CCBase):
    __tablename__ = "cc_diagnoses"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, index=True)
    seq_num = Column(Integer, nullable=False)
    icd_code = Column(String(16), nullable=False)
    icd_version = Column(Integer, nullable=False)
    long_title = Column(Text)
    __table_args__ = (UniqueConstraint("encounter_id", "seq_num", name="uq_dx_seq"),)


class EncounterProcedure(CCBase):
    __tablename__ = "cc_procedures"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, index=True)
    seq_num = Column(Integer, nullable=False)
    icd_code = Column(String(16), nullable=False)
    icd_version = Column(Integer, nullable=False)
    chartdate = Column(Date)
    long_title = Column(Text)
    __table_args__ = (UniqueConstraint("encounter_id", "seq_num", name="uq_px_seq"),)


class EncounterTransfer(CCBase):
    __tablename__ = "cc_transfers"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, index=True)
    eventtype = Column(String(16))
    careunit = Column(String(128))
    intime = Column(DateTime)
    outtime = Column(DateTime)


class EncounterMedication(CCBase):
    __tablename__ = "cc_medications"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, index=True)
    drug = Column(Text, nullable=False)
    dose_val_rx = Column(String(64))
    dose_unit_rx = Column(String(64))
    route = Column(String(32))
    starttime = Column(DateTime)
    stoptime = Column(DateTime)


class DischargeNote(CCBase):
    __tablename__ = "cc_discharge_notes"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, unique=True)
    text = Column(Text, nullable=False)
    provenance = Column(String(64), nullable=False)


class MemberPolicy(CCBase):
    __tablename__ = "cc_member_policies"
    id = Column(Integer, primary_key=True)
    member_id = Column(String(32), nullable=False, unique=True)
    subject_id = Column(Integer, nullable=False, unique=True)
    policy_version_id = Column(String(64), nullable=False)
    sum_insured = Column(Float, nullable=False)
    policy_start_date = Column(Date, nullable=False)
    policy_end_date = Column(Date)
    policy_cancelled_date = Column(Date)
    insured_age_at_entry = Column(Integer)
    deductible_opted = Column(Boolean, default=False)
    deductible_amount = Column(Float)
    provenance = Column(String(64), nullable=False)


class ClaimAdmin(CCBase):
    __tablename__ = "cc_claim_admin"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, unique=True)
    claim_type = Column(String(16))
    room_category = Column(String(16))
    notification_date = Column(Date)
    preauth_request_date = Column(Date)
    claim_filed_date = Column(Date)
    documents = Column(Text, default="")            # comma separated
    bill_header_total = Column(Float)
    provenance = Column(String(64), nullable=False)


class ChecklistItem(CCBase):
    __tablename__ = "cc_checklist_items"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, index=True)
    item = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)      # COMPLETE | PENDING
    completed_at = Column(DateTime)
    __table_args__ = (UniqueConstraint("encounter_id", "item", name="uq_checklist_item"),)


class BillLine(CCBase):
    __tablename__ = "cc_bill_lines"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    item_code = Column(String(64), nullable=False)
    category = Column(String(32))
    description = Column(Text)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    service_date = Column(Date)
    linked_icd_code = Column(String(16))
    is_deleted = Column(Boolean, default=False, nullable=False)   # soft delete keeps the audit trail
    provenance = Column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("encounter_id", "line_no", name="uq_bill_line_no"),)


class RateCardItem(CCBase):
    __tablename__ = "cc_rate_card"
    item_code = Column(String(64), primary_key=True)
    category = Column(String(32))
    description = Column(Text)
    unit_price = Column(Float, nullable=False)
    provenance = Column(String(64), nullable=False)


class HistoricalClaim(CCBase):
    __tablename__ = "cc_historical_claims"
    id = Column(Integer, primary_key=True)
    hist_claim_id = Column(String(32), unique=True, nullable=False)
    drg_code = Column(String(8), index=True)
    claim_type = Column(String(16))
    los_days = Column(Integer)
    room_category = Column(String(16))
    total_billed = Column(Float)
    provenance = Column(String(64), nullable=False)


class ClaimCase(CCBase):
    __tablename__ = "cc_cases"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cc_encounters.id", ondelete="CASCADE"), nullable=False, unique=True)
    case_ref = Column(String(32), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="NEW")
    # NEW | PROCESSING | PREPARATION_COMPLETE | NEEDS_REVIEW | BLOCKED | ERROR  (internal statuses,
    # never an insurer decision)
    priority = Column(Float, default=0.0)
    root_blocker_code = Column(String(64))
    root_blocker_stage = Column(String(32))
    amount_billed = Column(Float)
    amount_at_risk = Column(Float)
    estimated_eligible = Column(Float)
    open_tasks = Column(Integer, default=0)
    last_run_id = Column(Integer)
    version = Column(Integer, default=0, nullable=False)  # optimistic concurrency for corrections
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    encounter = relationship("Encounter", back_populates="case")


class PipelineRun(CCBase):
    __tablename__ = "cc_pipeline_runs"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cc_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger = Column(String(32), nullable=False)      # INITIAL | CORRECTION | REVIEW_DECISION | RETRY | MANUAL_RERUN
    execution_mode = Column(String(16), nullable=False)   # sequential | parallel
    rerun_policy = Column(String(16), nullable=False)     # targeted | full
    changed_domains = Column(JSON)
    invalidated_stages = Column(JSON)
    status = Column(String(16), nullable=False, default="RUNNING")   # RUNNING | SUCCEEDED | FAILED
    outcome_status = Column(String(32))
    stages_executed = Column(Integer, default=0)
    stages_reused = Column(Integer, default=0)
    started_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime)
    wall_ms = Column(Float)
    error = Column(Text)


class StageExecution(CCBase):
    __tablename__ = "cc_stage_executions"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("cc_pipeline_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("cc_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    # SUCCEEDED | FAILED | REUSED (inputs unchanged; previous output kept) | SKIPPED_DEPENDENCY_FAILED
    attempts = Column(Integer, default=1)
    input_hash = Column(String(64))
    output_hash = Column(String(64))
    output = Column(JSON)
    stage_status = Column(String(16))     # OK | WARN | BLOCKED | ERROR (stage's own verdict)
    started_at = Column(DateTime, default=_now)
    duration_ms = Column(Float)
    error = Column(Text)
    is_current = Column(Boolean, default=True, nullable=False)
    reused_from_id = Column(Integer)
    __table_args__ = (Index("ix_stage_current", "case_id", "stage", "is_current"),)


class Finding(CCBase):
    __tablename__ = "cc_findings"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cc_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_execution_id = Column(Integer, ForeignKey("cc_stage_executions.id", ondelete="CASCADE"), nullable=False)
    stage = Column(String(32), nullable=False)
    code = Column(String(64), nullable=False)
    severity = Column(String(16), nullable=False)     # BLOCKING | REVIEW | ADVISORY | INFO
    title = Column(Text)
    message = Column(Text)
    rule_id = Column(String(64))
    source_document = Column(String(128))
    source_page = Column(String(32))
    expected = Column(Text)
    actual = Column(Text)
    amount = Column(Float)
    evidence = Column(JSON)
    role = Column(String(16))            # ROOT | CONSEQUENCE | ADVISORY | INFO (set by root_blocker stage)
    caused_by = Column(String(64))
    is_current = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_now)


class ReviewTask(CCBase):
    __tablename__ = "cc_review_tasks"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cc_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    task_key = Column(String(128), nullable=False)
    code = Column(String(64), nullable=False)
    severity = Column(String(16), nullable=False)
    owner_stage = Column(String(32))
    input_domain = Column(String(32))
    title = Column(Text)
    reason = Column(Text)
    evidence = Column(JSON)
    recommended_action = Column(Text)
    required_reruns = Column(JSON)
    allowed_decisions = Column(JSON)
    status = Column(String(32), nullable=False, default="OPEN")
    # OPEN | RESOLVED_BY_CORRECTION | ACCEPTED_WITH_JUSTIFICATION | CONFIRMED_BLOCKED | SUPERSEDED
    assignee = Column(String(64))
    created_run_id = Column(Integer)
    created_at = Column(DateTime, default=_now)
    resolved_at = Column(DateTime)
    resolved_by = Column(String(64))
    resolution_note = Column(Text)
    __table_args__ = (Index("ix_task_case_key", "case_id", "task_key"),)


class Correction(CCBase):
    __tablename__ = "cc_corrections"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cc_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    op = Column(String(48), nullable=False)
    payload = Column(JSON, nullable=False)
    domain = Column(String(32), nullable=False)
    before = Column(JSON)
    after = Column(JSON)
    actor = Column(String(64), nullable=False)
    reason = Column(Text)
    review_task_id = Column(Integer)
    run_id = Column(Integer)
    created_at = Column(DateTime, default=_now)


class ReviewDecision(CCBase):
    __tablename__ = "cc_review_decisions"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cc_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("cc_review_tasks.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(64), nullable=False)
    task_key = Column(String(128), nullable=False)
    decision = Column(String(32), nullable=False)    # ACCEPT_WITH_JUSTIFICATION | CONFIRM_BLOCKED
    note = Column(Text, nullable=False)
    actor = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=_now)


class ClaimPacket(CCBase):
    __tablename__ = "cc_claim_packets"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cc_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    packet = Column(JSON, nullable=False)
    is_current = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_now)


class AuditEvent(CCBase):
    """Append-only event log. No code path updates or deletes rows of this table."""
    __tablename__ = "cc_audit_events"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=_now, nullable=False, index=True)
    actor = Column(String(64), nullable=False)
    case_id = Column(Integer, index=True)
    event_type = Column(String(48), nullable=False)
    entity_type = Column(String(32))
    entity_id = Column(Integer)
    summary = Column(Text)
    details = Column(JSON)
    # Tamper evidence: hash = sha256(prev_hash + canonical event content). Any edit or deletion of an
    # earlier row breaks the chain; GET /api/v1/audit-events/verify recomputes it.
    prev_hash = Column(String(64))
    hash = Column(String(64))


class AppSetting(CCBase):
    __tablename__ = "cc_settings"
    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ExperimentRun(CCBase):
    __tablename__ = "cc_experiment_runs"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    config = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="RUNNING")
    started_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime)
    summary = Column(JSON)
    raw_results_path = Column(Text)
    error = Column(Text)
