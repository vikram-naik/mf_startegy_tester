"""Add official-AMC notice observations and canonical provenance.

Revision ID: 20260817_0015
Revises: 20260817_0014
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0015"
down_revision: str | None = "20260817_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "official_distribution_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(16), nullable=False),
        sa.Column("source_scheme_name", sa.Text(), nullable=False),
        sa.Column("source_plan_type", sa.String(16), nullable=False),
        sa.Column("source_option_label", sa.Text(), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("raw_amount_per_unit_inr", sa.Text(), nullable=False),
        sa.Column("amount_per_unit_inr", sa.Text(), nullable=False),
        sa.Column("source_unit", sa.String(24), nullable=False),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.Column("first_notice_batch_id", sa.String(36), nullable=False),
        sa.Column("first_identity_batch_id", sa.String(36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider <> ''", name="ck_official_distribution_provider"),
        sa.CheckConstraint(
            "source_plan_type IN ('regular', 'direct')",
            name="ck_official_distribution_plan_type",
        ),
        sa.CheckConstraint(
            "source_unit = 'inr_per_unit'",
            name="ck_official_distribution_source_unit",
        ),
        sa.CheckConstraint(
            "CAST(amount_per_unit_inr AS NUMERIC) > 0",
            name="ck_official_distribution_amount_positive",
        ),
        sa.CheckConstraint(
            "length(content_signature) = 64",
            name="ck_official_distribution_signature_length",
        ),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_notice_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_identity_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature"),
    )
    op.create_index(
        op.f("ix_official_distribution_records_provider"),
        "official_distribution_records",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_official_distribution_option_date",
        "official_distribution_records",
        ["amfi_scheme_code", "record_date"],
        unique=False,
    )

    op.create_table(
        "official_distribution_record_sources",
        sa.Column("distribution_record_id", sa.String(36), nullable=False),
        sa.Column("notice_batch_id", sa.String(36), nullable=False),
        sa.Column("identity_batch_id", sa.String(36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_record_id"],
            ["official_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["notice_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["identity_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("distribution_record_id", "notice_batch_id", "identity_batch_id"),
    )

    op.create_table(
        "distribution_event_revision_official_sources",
        sa.Column("distribution_event_revision_id", sa.String(36), nullable=False),
        sa.Column("official_distribution_record_id", sa.String(36), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_event_revision_id"],
            ["distribution_event_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["official_distribution_record_id"],
            ["official_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "distribution_event_revision_id", "official_distribution_record_id"
        ),
        sa.UniqueConstraint(
            "official_distribution_record_id",
            name="uq_distribution_event_official_source_record",
        ),
    )


def downgrade() -> None:
    record_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM official_distribution_records")
    )
    if record_count:
        raise RuntimeError("cannot downgrade while official distribution records exist")
    op.drop_table("distribution_event_revision_official_sources")
    op.drop_table("official_distribution_record_sources")
    op.drop_index(
        "ix_official_distribution_option_date", table_name="official_distribution_records"
    )
    op.drop_index(
        op.f("ix_official_distribution_records_provider"),
        table_name="official_distribution_records",
    )
    op.drop_table("official_distribution_records")
