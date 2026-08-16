"""Add immutable source artifacts and ingestion batches.

Revision ID: 20260816_0002
Revises: 20260816_0001
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0002"
down_revision: str | None = "20260816_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("first_retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_source_artifacts_byte_size_nonnegative"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_source_artifacts_sha256_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
        sa.UniqueConstraint("storage_path"),
    )
    op.create_table(
        "ingestion_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("request_parameters", sa.JSON(), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_reused", sa.Boolean(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("rows_received", sa.Integer(), nullable=False),
        sa.Column("rows_accepted", sa.Integer(), nullable=False),
        sa.Column("rows_rejected", sa.Integer(), nullable=False),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.CheckConstraint("rows_accepted >= 0", name="ck_batch_rows_accepted_nonnegative"),
        sa.CheckConstraint("rows_received >= 0", name="ck_batch_rows_received_nonnegative"),
        sa.CheckConstraint("rows_rejected >= 0", name="ck_batch_rows_rejected_nonnegative"),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_batch_status"),
        sa.ForeignKeyConstraint(["artifact_id"], ["source_artifacts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ingestion_batches_artifact_id"),
        "ingestion_batches",
        ["artifact_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ingestion_batches_provider"),
        "ingestion_batches",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ingestion_batches_source_type"),
        "ingestion_batches",
        ["source_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ingestion_batches_status"),
        "ingestion_batches",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ingestion_batches_status"), table_name="ingestion_batches")
    op.drop_index(op.f("ix_ingestion_batches_source_type"), table_name="ingestion_batches")
    op.drop_index(op.f("ix_ingestion_batches_provider"), table_name="ingestion_batches")
    op.drop_index(op.f("ix_ingestion_batches_artifact_id"), table_name="ingestion_batches")
    op.drop_table("ingestion_batches")
    op.drop_table("source_artifacts")
