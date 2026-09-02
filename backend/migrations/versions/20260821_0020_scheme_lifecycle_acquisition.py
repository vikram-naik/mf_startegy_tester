"""Add immutable AMFI scheme lifecycle acquisition records.

Revision ID: 20260821_0020
Revises: 20260821_0019
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0020"
down_revision: str | None = "20260821_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheme_lifecycle_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("funds_total", sa.Integer(), nullable=False),
        sa.Column("funds_completed", sa.Integer(), nullable=False),
        sa.Column("funds_failed", sa.Integer(), nullable=False),
        sa.Column("families_total", sa.Integer(), nullable=False),
        sa.Column("families_completed", sa.Integer(), nullable=False),
        sa.Column("families_skipped", sa.Integer(), nullable=False),
        sa.Column("families_failed", sa.Integer(), nullable=False),
        sa.Column("detail_rows_received", sa.Integer(), nullable=False),
        sa.Column("detail_rows_inserted", sa.Integer(), nullable=False),
        sa.Column("detail_rows_unchanged", sa.Integer(), nullable=False),
        sa.Column("detail_rows_rejected", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('full', 'refresh')", name="ck_lifecycle_sync_mode"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
            name="ck_lifecycle_sync_status",
        ),
        sa.CheckConstraint("funds_total >= 0", name="ck_lifecycle_funds_total"),
        sa.CheckConstraint("funds_completed >= 0", name="ck_lifecycle_funds_completed"),
        sa.CheckConstraint("funds_failed >= 0", name="ck_lifecycle_funds_failed"),
        sa.CheckConstraint("families_total >= 0", name="ck_lifecycle_families_total"),
        sa.CheckConstraint("families_completed >= 0", name="ck_lifecycle_families_completed"),
        sa.CheckConstraint("families_skipped >= 0", name="ck_lifecycle_families_skipped"),
        sa.CheckConstraint("families_failed >= 0", name="ck_lifecycle_families_failed"),
        sa.CheckConstraint("detail_rows_received >= 0", name="ck_lifecycle_rows_received"),
        sa.CheckConstraint("detail_rows_inserted >= 0", name="ck_lifecycle_rows_inserted"),
        sa.CheckConstraint("detail_rows_unchanged >= 0", name="ck_lifecycle_rows_unchanged"),
        sa.CheckConstraint("detail_rows_rejected >= 0", name="ck_lifecycle_rows_rejected"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scheme_lifecycle_sync_runs_status",
        "scheme_lifecycle_sync_runs",
        ["status"],
        unique=False,
    )
    op.create_table(
        "amfi_scheme_list_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=False),
        sa.Column("scheme_count", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("scheme_count >= 0", name="ck_scheme_list_snapshot_count"),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ingestion_batch_id"),
    )
    op.create_index(
        "ix_scheme_list_snapshot_fund_time",
        "amfi_scheme_list_snapshots",
        ["mutual_fund_id", "captured_at"],
        unique=False,
    )
    op.create_table(
        "amfi_scheme_list_memberships",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("source_scheme_id", sa.String(length=16), nullable=False),
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_name", sa.Text(), nullable=False),
        sa.Column("content_signature", sa.String(length=64), nullable=False),
        sa.CheckConstraint("length(content_signature) = 64", name="ck_scheme_membership_signature"),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["amfi_scheme_list_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "source_scheme_id"),
    )
    op.create_index(
        "ix_scheme_membership_family",
        "amfi_scheme_list_memberships",
        ["mutual_fund_id", "source_scheme_id"],
        unique=False,
    )
    op.create_table(
        "amfi_scheme_detail_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_id", sa.String(length=16), nullable=False),
        sa.Column("mutual_fund_name", sa.Text(), nullable=False),
        sa.Column("scheme_name", sa.Text(), nullable=False),
        sa.Column("scheme_type", sa.Text(), nullable=False),
        sa.Column("scheme_category", sa.Text(), nullable=False),
        sa.Column("launch_date", sa.Date(), nullable=False),
        sa.Column("content_signature", sa.String(length=64), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(content_signature) = 64", name="ck_scheme_detail_signature"),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature"),
    )
    op.create_index(
        "ix_scheme_detail_family",
        "amfi_scheme_detail_records",
        ["mutual_fund_id", "source_scheme_id"],
        unique=False,
    )
    op.create_table(
        "amfi_scheme_detail_record_sources",
        sa.Column("scheme_detail_record_id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scheme_detail_record_id"], ["amfi_scheme_detail_records.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("scheme_detail_record_id", "ingestion_batch_id"),
    )
    op.create_table(
        "scheme_lifecycle_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_id", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("prior_name", sa.Text(), nullable=True),
        sa.Column("new_name", sa.Text(), nullable=True),
        sa.Column("successor_mutual_fund_id", sa.String(length=16), nullable=True),
        sa.Column("successor_source_scheme_id", sa.String(length=16), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("content_signature", sa.String(length=64), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('launch', 'rename', 'merger', 'closure', 'maturity')",
            name="ck_scheme_lifecycle_event_type",
        ),
        sa.CheckConstraint("length(content_signature) = 64", name="ck_lifecycle_event_signature"),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature"),
    )
    op.create_index(
        "ix_lifecycle_event_family_date",
        "scheme_lifecycle_events",
        ["mutual_fund_id", "source_scheme_id", "effective_date"],
        unique=False,
    )
    op.create_table(
        "scheme_lifecycle_event_sources",
        sa.Column("scheme_lifecycle_event_id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scheme_lifecycle_event_id"], ["scheme_lifecycle_events.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("scheme_lifecycle_event_id", "ingestion_batch_id"),
    )
    op.create_table(
        "scheme_lifecycle_checkpoints",
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_name", sa.Text(), nullable=False),
        sa.Column("latest_detail_record_id", sa.String(length=36), nullable=False),
        sa.Column("last_batch_id", sa.String(length=36), nullable=False),
        sa.Column("last_sync_run_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["last_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["last_sync_run_id"], ["scheme_lifecycle_sync_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["latest_detail_record_id"], ["amfi_scheme_detail_records.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("mutual_fund_id", "source_scheme_id"),
    )
    op.create_table(
        "scheme_lifecycle_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sync_run_id", sa.String(length=36), nullable=False),
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_id", sa.String(length=16), nullable=True),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=True),
        sa.Column("issue_code", sa.String(length=48), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "issue_code IN ('scheme_list_failure', 'scheme_detail_failure', "
            "'identity_mismatch', 'launch_date_conflict', 'catalog_member_removed', "
            "'name_changed_without_effective_date')",
            name="ck_scheme_lifecycle_issue_code",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"], ["scheme_lifecycle_sync_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lifecycle_issue_run_code",
        "scheme_lifecycle_issues",
        ["sync_run_id", "issue_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_lifecycle_issue_run_code", table_name="scheme_lifecycle_issues")
    op.drop_table("scheme_lifecycle_issues")
    op.drop_table("scheme_lifecycle_checkpoints")
    op.drop_table("scheme_lifecycle_event_sources")
    op.drop_index("ix_lifecycle_event_family_date", table_name="scheme_lifecycle_events")
    op.drop_table("scheme_lifecycle_events")
    op.drop_table("amfi_scheme_detail_record_sources")
    op.drop_index("ix_scheme_detail_family", table_name="amfi_scheme_detail_records")
    op.drop_table("amfi_scheme_detail_records")
    op.drop_index("ix_scheme_membership_family", table_name="amfi_scheme_list_memberships")
    op.drop_table("amfi_scheme_list_memberships")
    op.drop_index("ix_scheme_list_snapshot_fund_time", table_name="amfi_scheme_list_snapshots")
    op.drop_table("amfi_scheme_list_snapshots")
    op.drop_index("ix_scheme_lifecycle_sync_runs_status", table_name="scheme_lifecycle_sync_runs")
    op.drop_table("scheme_lifecycle_sync_runs")
