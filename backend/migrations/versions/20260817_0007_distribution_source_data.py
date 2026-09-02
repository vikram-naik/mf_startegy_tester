"""Add resumable AMFI distribution source snapshots.

Revision ID: 20260817_0007
Revises: 20260816_0006
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0007"
down_revision: str | None = "20260816_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "amfi_distribution_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_id", sa.String(length=16), nullable=False),
        sa.Column("source_option_id", sa.String(length=16), nullable=False),
        sa.Column("scheme_name", sa.Text(), nullable=False),
        sa.Column("nav_name", sa.Text(), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("source_value", sa.Text(), nullable=False),
        sa.Column("source_unit", sa.String(length=16), nullable=False),
        sa.Column("content_signature", sa.String(length=64), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_unit IN ('percentage', 'amount')", name="ck_distribution_source_unit"
        ),
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
        "ix_distribution_option_record_date",
        "amfi_distribution_records",
        ["source_option_id", "record_date"],
        unique=False,
    )
    op.create_index(
        "ix_distribution_family_record_date",
        "amfi_distribution_records",
        ["mutual_fund_id", "source_scheme_id", "record_date"],
        unique=False,
    )
    op.create_table(
        "amfi_distribution_record_sources",
        sa.Column("distribution_record_id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_record_id"], ["amfi_distribution_records.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("distribution_record_id", "ingestion_batch_id"),
    )
    op.create_table(
        "distribution_sync_checkpoints",
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_id", sa.String(length=16), nullable=False),
        sa.Column("source_scheme_name", sa.Text(), nullable=False),
        sa.Column("rows_received", sa.Integer(), nullable=False),
        sa.Column("last_batch_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rows_received >= 0", name="ck_distribution_checkpoint_rows"),
        sa.ForeignKeyConstraint(["last_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("mutual_fund_id", "source_scheme_id"),
    )
    op.create_table(
        "distribution_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("funds_total", sa.Integer(), nullable=False),
        sa.Column("funds_completed", sa.Integer(), nullable=False),
        sa.Column("schemes_total", sa.Integer(), nullable=False),
        sa.Column("schemes_completed", sa.Integer(), nullable=False),
        sa.Column("rows_received", sa.Integer(), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), nullable=False),
        sa.Column("rows_unchanged", sa.Integer(), nullable=False),
        sa.Column("rows_unresolved", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('full', 'refresh')", name="ck_distribution_sync_mode"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_distribution_sync_status",
        ),
        sa.CheckConstraint("funds_total >= 0", name="ck_distribution_run_funds_total"),
        sa.CheckConstraint("funds_completed >= 0", name="ck_distribution_run_funds_completed"),
        sa.CheckConstraint("schemes_total >= 0", name="ck_distribution_run_schemes_total"),
        sa.CheckConstraint("schemes_completed >= 0", name="ck_distribution_run_schemes_completed"),
        sa.CheckConstraint("rows_received >= 0", name="ck_distribution_run_rows_received"),
        sa.CheckConstraint("rows_inserted >= 0", name="ck_distribution_run_rows_inserted"),
        sa.CheckConstraint("rows_unchanged >= 0", name="ck_distribution_run_rows_unchanged"),
        sa.CheckConstraint("rows_unresolved >= 0", name="ck_distribution_run_rows_unresolved"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_distribution_sync_runs_status"),
        "distribution_sync_runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_distribution_sync_runs_status"), table_name="distribution_sync_runs")
    op.drop_table("distribution_sync_runs")
    op.drop_table("distribution_sync_checkpoints")
    op.drop_table("amfi_distribution_record_sources")
    op.drop_index("ix_distribution_family_record_date", table_name="amfi_distribution_records")
    op.drop_index("ix_distribution_option_record_date", table_name="amfi_distribution_records")
    op.drop_table("amfi_distribution_records")
