"""Add append-only distribution identifier review evidence.

Revision ID: 20260817_0012
Revises: 20260817_0011
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0012"
down_revision: str | None = "20260817_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "distribution_identifier_reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_option_id", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("matched_amfi_scheme_code", sa.String(16), nullable=True),
        sa.Column("evidence_batch_id", sa.String(36), nullable=False),
        sa.Column("evidence_details", sa.Text(), nullable=False),
        sa.Column("review_signature", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('source_only', 'mapped', 'source_error')",
            name="ck_distribution_review_status",
        ),
        sa.CheckConstraint(
            "((status = 'mapped' AND matched_amfi_scheme_code IS NOT NULL) "
            "OR (status IN ('source_only', 'source_error') "
            "AND matched_amfi_scheme_code IS NULL))",
            name="ck_distribution_review_mapping_shape",
        ),
        sa.CheckConstraint(
            "length(review_signature) = 64",
            name="ck_distribution_review_signature_length",
        ),
        sa.ForeignKeyConstraint(
            ["matched_amfi_scheme_code"],
            ["scheme_options.amfi_scheme_code"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_signature"),
    )
    op.create_index(
        "ix_distribution_review_option_time",
        "distribution_identifier_reviews",
        ["source_option_id", "reviewed_at"],
        unique=False,
    )


def downgrade() -> None:
    review_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM distribution_identifier_reviews")
    )
    if review_count:
        raise RuntimeError("cannot downgrade while distribution identifier reviews exist")
    op.drop_index(
        "ix_distribution_review_option_time",
        table_name="distribution_identifier_reviews",
    )
    op.drop_table("distribution_identifier_reviews")
