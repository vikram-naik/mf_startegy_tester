"""Add append-only IDCW distribution coverage assessments.

Revision ID: 20260817_0014
Revises: 20260817_0013
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0014"
down_revision: str | None = "20260817_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "distribution_coverage_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("assessment_version", sa.String(64), nullable=False),
        sa.Column("options_examined", sa.Integer(), nullable=False),
        sa.Column("events_present_options", sa.Integer(), nullable=False),
        sa.Column("blocked_source_options", sa.Integer(), nullable=False),
        sa.Column("unverified_empty_options", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_distribution_coverage_run_status",
        ),
        sa.CheckConstraint("options_examined >= 0", name="ck_distribution_coverage_options"),
        sa.CheckConstraint("events_present_options >= 0", name="ck_distribution_coverage_events"),
        sa.CheckConstraint("blocked_source_options >= 0", name="ck_distribution_coverage_blocked"),
        sa.CheckConstraint("unverified_empty_options >= 0", name="ck_distribution_coverage_empty"),
        sa.CheckConstraint(
            "options_examined = events_present_options + blocked_source_options + "
            "unverified_empty_options",
            name="ck_distribution_coverage_run_totals",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_distribution_coverage_runs_status"),
        "distribution_coverage_runs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "distribution_coverage_assessments",
        sa.Column("coverage_run_id", sa.String(36), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(16), nullable=False),
        sa.Column("metadata_version_id", sa.String(64), nullable=False),
        sa.Column("coverage_status", sa.String(24), nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("canonical_source_row_count", sa.Integer(), nullable=False),
        sa.Column("blocked_source_row_count", sa.Integer(), nullable=False),
        sa.Column("canonical_event_count", sa.Integer(), nullable=False),
        sa.Column("first_source_record_date", sa.Date(), nullable=True),
        sa.Column("last_source_record_date", sa.Date(), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "coverage_status IN ('events_present', 'blocked_source_rows', 'unverified_empty')",
            name="ck_distribution_coverage_status",
        ),
        sa.CheckConstraint("source_row_count >= 0", name="ck_distribution_coverage_source_rows"),
        sa.CheckConstraint(
            "canonical_source_row_count >= 0",
            name="ck_distribution_coverage_canonical_sources",
        ),
        sa.CheckConstraint(
            "blocked_source_row_count >= 0",
            name="ck_distribution_coverage_blocked_sources",
        ),
        sa.CheckConstraint(
            "canonical_event_count >= 0", name="ck_distribution_coverage_event_count"
        ),
        sa.CheckConstraint(
            "source_row_count = canonical_source_row_count + blocked_source_row_count",
            name="ck_distribution_coverage_source_totals",
        ),
        sa.CheckConstraint(
            "((source_row_count = 0 AND first_source_record_date IS NULL "
            "AND last_source_record_date IS NULL) OR "
            "(source_row_count > 0 AND first_source_record_date IS NOT NULL "
            "AND last_source_record_date IS NOT NULL))",
            name="ck_distribution_coverage_source_dates",
        ),
        sa.CheckConstraint(
            "((coverage_status = 'events_present' AND canonical_event_count > 0) OR "
            "(coverage_status = 'blocked_source_rows' AND canonical_event_count = 0 "
            "AND source_row_count > 0) OR "
            "(coverage_status = 'unverified_empty' AND canonical_event_count = 0 "
            "AND source_row_count = 0))",
            name="ck_distribution_coverage_status_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["coverage_run_id"], ["distribution_coverage_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["metadata_version_id"], ["scheme_metadata_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("coverage_run_id", "amfi_scheme_code"),
    )
    op.create_index(
        op.f("ix_distribution_coverage_assessments_coverage_status"),
        "distribution_coverage_assessments",
        ["coverage_status"],
        unique=False,
    )
    op.create_index(
        "ix_distribution_coverage_option_run",
        "distribution_coverage_assessments",
        ["amfi_scheme_code", "coverage_run_id"],
        unique=False,
    )


def downgrade() -> None:
    assessment_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM distribution_coverage_assessments")
    )
    if assessment_count:
        raise RuntimeError("cannot downgrade while distribution coverage assessments exist")
    op.drop_index(
        "ix_distribution_coverage_option_run",
        table_name="distribution_coverage_assessments",
    )
    op.drop_index(
        op.f("ix_distribution_coverage_assessments_coverage_status"),
        table_name="distribution_coverage_assessments",
    )
    op.drop_table("distribution_coverage_assessments")
    op.drop_index(
        op.f("ix_distribution_coverage_runs_status"),
        table_name="distribution_coverage_runs",
    )
    op.drop_table("distribution_coverage_runs")
