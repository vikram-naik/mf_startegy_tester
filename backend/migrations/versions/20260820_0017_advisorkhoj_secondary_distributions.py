"""Add catalogued AdvisorKhoj secondary distribution observations.

Revision ID: 20260820_0017
Revises: 20260817_0016
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0017"
down_revision: str | None = "20260817_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "advisorkhoj_catalog_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(36), nullable=False),
        sa.Column("catalog_sha256", sa.String(64), nullable=False),
        sa.Column("amc_count", sa.Integer(), nullable=False),
        sa.Column("category_query_count", sa.Integer(), nullable=False),
        sa.Column("scheme_count", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(catalog_sha256) = 64", name="ck_ak_catalog_sha256_length"),
        sa.CheckConstraint("amc_count > 0", name="ck_ak_catalog_amc_count"),
        sa.CheckConstraint("category_query_count >= 0", name="ck_ak_catalog_category_count"),
        sa.CheckConstraint("scheme_count >= 0", name="ck_ak_catalog_scheme_count"),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_sha256"),
        sa.UniqueConstraint("ingestion_batch_id"),
    )
    op.create_table(
        "advisorkhoj_catalog_schemes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("catalog_snapshot_id", sa.String(36), nullable=False),
        sa.Column("amc_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(255), nullable=False),
        sa.Column("scheme_name", sa.Text(), nullable=False),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.CheckConstraint("length(content_signature) = 64", name="ck_ak_catalog_scheme_signature"),
        sa.ForeignKeyConstraint(
            ["catalog_snapshot_id"], ["advisorkhoj_catalog_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature"),
        sa.UniqueConstraint(
            "catalog_snapshot_id", "amc_name", "scheme_name", name="uq_ak_catalog_scheme_identity"
        ),
    )
    op.create_index(
        "ix_ak_catalog_scheme_amc",
        "advisorkhoj_catalog_schemes",
        ["catalog_snapshot_id", "amc_name"],
        unique=False,
    )
    op.create_table(
        "advisorkhoj_scheme_captures",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("catalog_scheme_id", sa.String(36), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_payload_sha256", sa.String(64), nullable=False),
        sa.Column("plan_type", sa.String(16), nullable=False),
        sa.Column("option_variant", sa.String(16), nullable=False),
        sa.Column("source_frequency", sa.String(24), nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("capture_signature", sa.String(64), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(36), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "plan_type IN ('direct', 'regular', 'unknown')", name="ck_ak_capture_plan_type"
        ),
        sa.CheckConstraint(
            "option_variant IN ('payout', 'reinvestment', 'mixed', 'unknown')",
            name="ck_ak_capture_option_variant",
        ),
        sa.CheckConstraint("source_row_count >= 0", name="ck_ak_capture_source_rows"),
        sa.CheckConstraint("length(source_payload_sha256) = 64", name="ck_ak_capture_payload_sha"),
        sa.CheckConstraint("length(capture_signature) = 64", name="ck_ak_capture_signature"),
        sa.ForeignKeyConstraint(
            ["catalog_scheme_id"], ["advisorkhoj_catalog_schemes.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capture_signature"),
    )
    op.create_index(
        "ix_ak_capture_catalog_scheme",
        "advisorkhoj_scheme_captures",
        ["catalog_scheme_id", "captured_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_advisorkhoj_scheme_captures_ingestion_batch_id"),
        "advisorkhoj_scheme_captures",
        ["ingestion_batch_id"],
        unique=False,
    )
    op.create_table(
        "advisorkhoj_scheme_mapping_reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scheme_capture_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(16), nullable=True),
        sa.Column("mapping_method", sa.String(32), nullable=False),
        sa.Column("evidence_details", sa.Text(), nullable=False),
        sa.Column("review_signature", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('mapped', 'unresolved', 'ambiguous')", name="ck_ak_mapping_status"
        ),
        sa.CheckConstraint(
            "((status = 'mapped' AND amfi_scheme_code IS NOT NULL) OR "
            "(status IN ('unresolved', 'ambiguous') AND amfi_scheme_code IS NULL))",
            name="ck_ak_mapping_shape",
        ),
        sa.CheckConstraint(
            "mapping_method IN ('nav_fingerprint', 'manual', 'none')",
            name="ck_ak_mapping_method",
        ),
        sa.CheckConstraint("length(review_signature) = 64", name="ck_ak_mapping_signature"),
        sa.ForeignKeyConstraint(
            ["scheme_capture_id"], ["advisorkhoj_scheme_captures.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_signature"),
    )
    op.create_index(
        op.f("ix_advisorkhoj_scheme_mapping_reviews_status"),
        "advisorkhoj_scheme_mapping_reviews",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_ak_mapping_capture_time",
        "advisorkhoj_scheme_mapping_reviews",
        ["scheme_capture_id", "reviewed_at"],
        unique=False,
    )
    op.create_table(
        "advisorkhoj_distribution_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scheme_capture_id", sa.String(36), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("raw_amount_per_unit_inr", sa.Text(), nullable=False),
        sa.Column("amount_per_unit_inr", sa.Text(), nullable=False),
        sa.Column("raw_reference_nav", sa.Text(), nullable=False),
        sa.Column("reference_nav", sa.Text(), nullable=False),
        sa.Column("raw_yield_percent", sa.Text(), nullable=False),
        sa.Column("yield_percent", sa.Text(), nullable=False),
        sa.Column("is_positive_cash_distribution", sa.Boolean(), nullable=False),
        sa.Column("quality_status", sa.String(24), nullable=False),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "CAST(reference_nav AS NUMERIC) >= 0", name="ck_ak_distribution_reference_nav"
        ),
        sa.CheckConstraint(
            "quality_status IN ('valid', 'zero_amount', 'negative_amount', 'zero_reference_nav')",
            name="ck_ak_distribution_quality_status",
        ),
        sa.CheckConstraint("length(content_signature) = 64", name="ck_ak_distribution_signature"),
        sa.ForeignKeyConstraint(
            ["scheme_capture_id"], ["advisorkhoj_scheme_captures.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature"),
    )
    op.create_index(
        "ix_ak_distribution_capture_date",
        "advisorkhoj_distribution_records",
        ["scheme_capture_id", "record_date"],
        unique=False,
    )
    op.create_table(
        "advisorkhoj_distribution_record_sources",
        sa.Column("advisorkhoj_distribution_record_id", sa.String(36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["advisorkhoj_distribution_record_id"],
            ["advisorkhoj_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("advisorkhoj_distribution_record_id", "ingestion_batch_id"),
    )


def downgrade() -> None:
    source_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM advisorkhoj_catalog_snapshots")
    )
    if source_count:
        raise RuntimeError("cannot downgrade while AdvisorKhoj catalog snapshots exist")
    op.drop_table("advisorkhoj_distribution_record_sources")
    op.drop_index("ix_ak_distribution_capture_date", table_name="advisorkhoj_distribution_records")
    op.drop_table("advisorkhoj_distribution_records")
    op.drop_index("ix_ak_mapping_capture_time", table_name="advisorkhoj_scheme_mapping_reviews")
    op.drop_index(
        op.f("ix_advisorkhoj_scheme_mapping_reviews_status"),
        table_name="advisorkhoj_scheme_mapping_reviews",
    )
    op.drop_table("advisorkhoj_scheme_mapping_reviews")
    op.drop_index(
        op.f("ix_advisorkhoj_scheme_captures_ingestion_batch_id"),
        table_name="advisorkhoj_scheme_captures",
    )
    op.drop_index("ix_ak_capture_catalog_scheme", table_name="advisorkhoj_scheme_captures")
    op.drop_table("advisorkhoj_scheme_captures")
    op.drop_index("ix_ak_catalog_scheme_amc", table_name="advisorkhoj_catalog_schemes")
    op.drop_table("advisorkhoj_catalog_schemes")
    op.drop_table("advisorkhoj_catalog_snapshots")
