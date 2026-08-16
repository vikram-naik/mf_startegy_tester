"""Add provenance-linked normalized NAV history and sync state.

Revision ID: 20260816_0003
Revises: 20260816_0002
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0003"
down_revision: str | None = "20260816_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "amfi_funds",
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("mutual_fund_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("catalog_batch_id", sa.String(length=36), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("mutual_fund_id"),
    )
    op.create_table(
        "scheme_options",
        sa.Column("amfi_scheme_code", sa.String(length=16), nullable=False),
        sa.Column("first_observed_nav_date", sa.Date(), nullable=False),
        sa.Column("last_observed_nav_date", sa.Date(), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("amfi_scheme_code"),
    )
    op.create_index(
        op.f("ix_scheme_options_last_observed_nav_date"),
        "scheme_options",
        ["last_observed_nav_date"],
        unique=False,
    )
    op.create_table(
        "scheme_metadata_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(length=16), nullable=False),
        sa.Column("scheme_name", sa.Text(), nullable=False),
        sa.Column("fund_house_name", sa.String(length=255), nullable=False),
        sa.Column("scheme_classification", sa.Text(), nullable=False),
        sa.Column("isin_payout_or_growth", sa.String(length=32), nullable=True),
        sa.Column("isin_reinvestment", sa.String(length=32), nullable=True),
        sa.Column("plan_type", sa.String(length=16), nullable=False),
        sa.Column("option_type", sa.String(length=16), nullable=False),
        sa.Column("classification_method", sa.String(length=64), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "plan_type IN ('direct', 'regular', 'unknown')", name="ck_metadata_plan"
        ),
        sa.CheckConstraint(
            "option_type IN ('growth', 'idcw', 'bonus', 'unknown')",
            name="ck_metadata_option",
        ),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_scheme_metadata_versions_amfi_scheme_code"),
        "scheme_metadata_versions",
        ["amfi_scheme_code"],
        unique=False,
    )
    op.create_table(
        "nav_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(length=16), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("nav_value", sa.Text(), nullable=False),
        sa.Column("metadata_version_id", sa.String(length=64), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("content_signature", sa.String(length=64), nullable=False),
        sa.Column("quality_status", sa.String(length=16), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quality_status IN ('valid', 'error')", name="ck_nav_quality_status"),
        sa.CheckConstraint("revision_number > 0", name="ck_nav_revision_number_positive"),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["metadata_version_id"], ["scheme_metadata_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "amfi_scheme_code", "nav_date", "revision_number", name="uq_nav_revision_number"
        ),
    )
    op.create_index(
        "ix_nav_current_date",
        "nav_revisions",
        ["nav_date"],
        unique=False,
        sqlite_where=sa.text("is_current = 1"),
    )
    op.create_index(
        "uq_nav_current_scheme_date",
        "nav_revisions",
        ["amfi_scheme_code", "nav_date"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )
    op.create_table(
        "nav_revision_sources",
        sa.Column("nav_revision_id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["nav_revision_id"], ["nav_revisions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("nav_revision_id", "ingestion_batch_id"),
    )
    op.create_table(
        "data_quality_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=False),
        sa.Column("issue_code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(length=16), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("metadata_version_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('error', 'warning', 'info')", name="ck_dq_severity"),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["metadata_version_id"], ["scheme_metadata_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ingestion_batch_id",
            "issue_code",
            "amfi_scheme_code",
            "nav_date",
            name="uq_dq_batch_issue_scheme_date",
        ),
    )
    op.create_index(
        op.f("ix_data_quality_issues_ingestion_batch_id"),
        "data_quality_issues",
        ["ingestion_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_data_quality_issues_issue_code"),
        "data_quality_issues",
        ["issue_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_data_quality_issues_severity"),
        "data_quality_issues",
        ["severity"],
        unique=False,
    )
    op.create_table(
        "nav_sync_checkpoints",
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("completed_through", sa.Date(), nullable=False),
        sa.Column("latest_nav_date_found", sa.Date(), nullable=True),
        sa.Column("last_batch_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("mutual_fund_id"),
    )
    op.create_table(
        "nav_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_start_date", sa.Date(), nullable=False),
        sa.Column("requested_end_date", sa.Date(), nullable=False),
        sa.Column("overlap_days", sa.Integer(), nullable=False),
        sa.Column("funds_total", sa.Integer(), nullable=False),
        sa.Column("funds_completed", sa.Integer(), nullable=False),
        sa.Column("chunks_completed", sa.Integer(), nullable=False),
        sa.Column("rows_received", sa.Integer(), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), nullable=False),
        sa.Column("rows_unchanged", sa.Integer(), nullable=False),
        sa.Column("rows_revised", sa.Integer(), nullable=False),
        sa.Column("rows_quarantined", sa.Integer(), nullable=False),
        sa.Column("latest_nav_date_found", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('full', 'incremental')", name="ck_nav_sync_mode"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')", name="ck_nav_sync_status"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_nav_sync_runs_status"), "nav_sync_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_nav_sync_runs_status"), table_name="nav_sync_runs")
    op.drop_table("nav_sync_runs")
    op.drop_table("nav_sync_checkpoints")
    op.drop_index(op.f("ix_data_quality_issues_severity"), table_name="data_quality_issues")
    op.drop_index(op.f("ix_data_quality_issues_issue_code"), table_name="data_quality_issues")
    op.drop_index(
        op.f("ix_data_quality_issues_ingestion_batch_id"), table_name="data_quality_issues"
    )
    op.drop_table("data_quality_issues")
    op.drop_table("nav_revision_sources")
    op.drop_index("uq_nav_current_scheme_date", table_name="nav_revisions")
    op.drop_index("ix_nav_current_date", table_name="nav_revisions")
    op.drop_table("nav_revisions")
    op.drop_index(
        op.f("ix_scheme_metadata_versions_amfi_scheme_code"),
        table_name="scheme_metadata_versions",
    )
    op.drop_table("scheme_metadata_versions")
    op.drop_index(op.f("ix_scheme_options_last_observed_nav_date"), table_name="scheme_options")
    op.drop_table("scheme_options")
    op.drop_table("amfi_funds")
