"""issue memory: persistent issues, issue history, decision evidence fingerprints, idempotent tasks

Additive only: two new tables, two nullable columns (plain ADD COLUMN, no table rebuild on SQLite)
and two partial unique indexes. No existing row is modified or deleted.

Revision ID: c4e8a1f2b9d3
Revises: 6cddb53e533a
Create Date: 2026-11-20 20:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e8a1f2b9d3'
down_revision: Union[str, Sequence[str], None] = '6cddb53e533a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Refuse (rather than silently change data) if existing rows would violate the new uniqueness rule.
    dups = bind.execute(sa.text(
        "SELECT case_id, task_key, COUNT(*) FROM cc_review_tasks WHERE status = 'OPEN' "
        "GROUP BY case_id, task_key HAVING COUNT(*) > 1")).fetchall()
    if dups:
        raise RuntimeError(f"{len(dups)} (case_id, task_key) pairs have more than one OPEN review task; resolve them "
                           f"before applying the issue-memory migration (first: {list(dups[0])}).")

    op.create_table(
        'cc_issues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('encounter_id', sa.Integer(), nullable=False),
        sa.Column('issue_key', sa.String(length=80), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('issue_type', sa.String(length=32), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('identity', sa.JSON(), nullable=True),
        sa.Column('evidence_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('evidence', sa.JSON(), nullable=True),
        sa.Column('severity', sa.String(length=16), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('review_outcome', sa.String(length=32), nullable=True),
        sa.Column('is_present', sa.Boolean(), nullable=False),
        sa.Column('owner_stage', sa.String(length=32), nullable=True),
        sa.Column('input_domain', sa.String(length=32), nullable=True),
        sa.Column('reviewer', sa.String(length=64), nullable=True),
        sa.Column('reviewer_notes', sa.JSON(), nullable=True),
        sa.Column('previous_actions', sa.JSON(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('first_detected_at', sa.DateTime(), nullable=True),
        sa.Column('last_detected_at', sa.DateTime(), nullable=True),
        sa.Column('first_run_id', sa.Integer(), nullable=True),
        sa.Column('last_run_id', sa.Integer(), nullable=True),
        sa.Column('detection_count', sa.Integer(), nullable=False),
        sa.Column('review_attempts', sa.Integer(), nullable=False),
        sa.Column('reopen_count', sa.Integer(), nullable=False),
        sa.Column('last_dedup_run_id', sa.Integer(), nullable=True),
        sa.Column('resolution_action', sa.JSON(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by', sa.String(length=64), nullable=True),
        sa.Column('backfilled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['case_id'], ['cc_cases.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['encounter_id'], ['cc_encounters.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('case_id', 'issue_key', name='uq_issue_case_key'),
    )
    op.create_index('ix_cc_issues_case_id', 'cc_issues', ['case_id'])
    op.create_index('ix_cc_issues_encounter_id', 'cc_issues', ['encounter_id'])
    op.create_index('ix_cc_issues_code', 'cc_issues', ['code'])
    op.create_index('ix_cc_issues_status', 'cc_issues', ['status'])

    op.create_table(
        'cc_issue_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('event', sa.String(length=32), nullable=False),
        sa.Column('from_status', sa.String(length=24), nullable=True),
        sa.Column('to_status', sa.String(length=24), nullable=True),
        sa.Column('actor', sa.String(length=64), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('ts', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['issue_id'], ['cc_issues.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cc_issue_events_issue_id', 'cc_issue_events', ['issue_id'])
    op.create_index('ix_cc_issue_events_case_id', 'cc_issue_events', ['case_id'])

    # nullable columns: plain ALTER TABLE ADD COLUMN (no SQLite table rebuild, existing rows untouched)
    op.add_column('cc_review_tasks', sa.Column('issue_id', sa.Integer(), nullable=True))
    if dialect != 'sqlite':
        op.create_foreign_key('fk_task_issue', 'cc_review_tasks', 'cc_issues', ['issue_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_cc_review_tasks_issue_id', 'cc_review_tasks', ['issue_id'])
    op.add_column('cc_review_decisions', sa.Column('evidence_fingerprint', sa.String(length=64), nullable=True))

    # database-level idempotency for review tasks
    op.create_index('uq_task_open_key', 'cc_review_tasks', ['case_id', 'task_key'], unique=True,
                    sqlite_where=sa.text("status = 'OPEN'"), postgresql_where=sa.text("status = 'OPEN'"))
    op.create_index('uq_task_open_issue', 'cc_review_tasks', ['issue_id'], unique=True,
                    sqlite_where=sa.text("status = 'OPEN' AND issue_id IS NOT NULL"),
                    postgresql_where=sa.text("status = 'OPEN' AND issue_id IS NOT NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index('uq_task_open_issue', table_name='cc_review_tasks')
    op.drop_index('uq_task_open_key', table_name='cc_review_tasks')
    op.drop_index('ix_cc_review_tasks_issue_id', table_name='cc_review_tasks')
    if bind.dialect.name != 'sqlite':
        op.drop_constraint('fk_task_issue', 'cc_review_tasks', type_='foreignkey')
    with op.batch_alter_table('cc_review_decisions') as b:
        b.drop_column('evidence_fingerprint')
    with op.batch_alter_table('cc_review_tasks') as b:
        b.drop_column('issue_id')
    op.drop_index('ix_cc_issue_events_case_id', table_name='cc_issue_events')
    op.drop_index('ix_cc_issue_events_issue_id', table_name='cc_issue_events')
    op.drop_table('cc_issue_events')
    for ix in ('ix_cc_issues_status', 'ix_cc_issues_code', 'ix_cc_issues_encounter_id', 'ix_cc_issues_case_id'):
        op.drop_index(ix, table_name='cc_issues')
    op.drop_table('cc_issues')
