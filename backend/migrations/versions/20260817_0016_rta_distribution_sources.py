"""Add official RTA distribution captures, mappings, and canonical provenance.

Revision ID: 20260817_0016
Revises: 20260817_0015
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0016"
down_revision: str | None = "20260817_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rta_scheme_captures",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("rta_fund_code", sa.String(32), nullable=False),
        sa.Column("rta_fund_name", sa.String(255), nullable=False),
        sa.Column("rta_scheme_code", sa.String(64), nullable=False),
        sa.Column("source_scheme_name", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_payload_sha256", sa.String(64), nullable=False),
        sa.Column("plan_type", sa.String(16), nullable=False),
        sa.Column("option_variant", sa.String(16), nullable=False),
        sa.Column("latest_nav_date", sa.Date(), nullable=True),
        sa.Column("latest_nav_value", sa.Text(), nullable=True),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("capture_signature", sa.String(64), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(36), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('cams', 'kfintech')", name="ck_rta_capture_provider"),
        sa.CheckConstraint(
            "plan_type IN ('direct', 'regular', 'unknown')",
            name="ck_rta_capture_plan_type",
        ),
        sa.CheckConstraint(
            "option_variant IN ('payout', 'reinvestment', 'unknown')",
            name="ck_rta_capture_option_variant",
        ),
        sa.CheckConstraint("source_row_count >= 0", name="ck_rta_capture_source_rows"),
        sa.CheckConstraint(
            "((latest_nav_date IS NULL AND latest_nav_value IS NULL) OR "
            "(latest_nav_date IS NOT NULL AND latest_nav_value IS NOT NULL "
            "AND CAST(latest_nav_value AS NUMERIC) >= 0))",
            name="ck_rta_capture_latest_nav_shape",
        ),
        sa.CheckConstraint(
            "length(capture_signature) = 64", name="ck_rta_capture_signature_length"
        ),
        sa.CheckConstraint(
            "length(source_payload_sha256) = 64",
            name="ck_rta_capture_source_payload_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capture_signature"),
    )
    op.create_index(
        op.f("ix_rta_scheme_captures_provider"),
        "rta_scheme_captures",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rta_scheme_captures_ingestion_batch_id"),
        "rta_scheme_captures",
        ["ingestion_batch_id"],
        unique=False,
    )
    op.create_index(
        "ix_rta_capture_identifier",
        "rta_scheme_captures",
        ["provider", "rta_fund_code", "rta_scheme_code", "captured_at"],
        unique=False,
    )

    op.create_table(
        "rta_scheme_mapping_reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scheme_capture_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(16), nullable=True),
        sa.Column("mapping_method", sa.String(32), nullable=False),
        sa.Column("evidence_details", sa.Text(), nullable=False),
        sa.Column("review_signature", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('mapped', 'unresolved', 'ambiguous')", name="ck_rta_mapping_status"
        ),
        sa.CheckConstraint(
            "((status = 'mapped' AND amfi_scheme_code IS NOT NULL) OR "
            "(status IN ('unresolved', 'ambiguous') AND amfi_scheme_code IS NULL))",
            name="ck_rta_mapping_shape",
        ),
        sa.CheckConstraint(
            "mapping_method IN ('exact_name_plan_nav', 'manual', 'none')",
            name="ck_rta_mapping_method",
        ),
        sa.CheckConstraint("length(review_signature) = 64", name="ck_rta_mapping_signature_length"),
        sa.ForeignKeyConstraint(
            ["scheme_capture_id"], ["rta_scheme_captures.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_signature"),
    )
    op.create_index(
        op.f("ix_rta_scheme_mapping_reviews_status"),
        "rta_scheme_mapping_reviews",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_rta_mapping_capture_time",
        "rta_scheme_mapping_reviews",
        ["scheme_capture_id", "reviewed_at"],
        unique=False,
    )

    op.create_table(
        "rta_distribution_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scheme_capture_id", sa.String(36), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("raw_individual_amount", sa.Text(), nullable=False),
        sa.Column("individual_amount_per_unit_inr", sa.Text(), nullable=False),
        sa.Column("raw_non_individual_amount", sa.Text(), nullable=True),
        sa.Column("non_individual_amount_per_unit_inr", sa.Text(), nullable=True),
        sa.Column("ex_nav", sa.Text(), nullable=True),
        sa.Column("cum_nav", sa.Text(), nullable=True),
        sa.Column("source_unit", sa.String(24), nullable=False),
        sa.Column("source_terminology", sa.String(64), nullable=False),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "CAST(individual_amount_per_unit_inr AS NUMERIC) > 0",
            name="ck_rta_distribution_individual_amount",
        ),
        sa.CheckConstraint(
            "non_individual_amount_per_unit_inr IS NULL OR "
            "CAST(non_individual_amount_per_unit_inr AS NUMERIC) > 0",
            name="ck_rta_distribution_non_individual_amount",
        ),
        sa.CheckConstraint("source_unit = 'inr_per_unit'", name="ck_rta_distribution_source_unit"),
        sa.CheckConstraint(
            "length(content_signature) = 64", name="ck_rta_distribution_signature_length"
        ),
        sa.ForeignKeyConstraint(
            ["scheme_capture_id"], ["rta_scheme_captures.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature"),
    )
    op.create_index(
        "ix_rta_distribution_capture_date",
        "rta_distribution_records",
        ["scheme_capture_id", "record_date"],
        unique=False,
    )

    op.create_table(
        "rta_distribution_record_sources",
        sa.Column("rta_distribution_record_id", sa.String(36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rta_distribution_record_id"],
            ["rta_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("rta_distribution_record_id", "ingestion_batch_id"),
    )

    op.create_table(
        "distribution_event_revision_rta_sources",
        sa.Column("distribution_event_revision_id", sa.String(36), nullable=False),
        sa.Column("rta_distribution_record_id", sa.String(36), nullable=False),
        sa.Column("mapping_review_id", sa.String(36), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_event_revision_id"],
            ["distribution_event_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rta_distribution_record_id"],
            ["rta_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_review_id"],
            ["rta_scheme_mapping_reviews.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("distribution_event_revision_id", "rta_distribution_record_id"),
        sa.UniqueConstraint(
            "rta_distribution_record_id", name="uq_distribution_event_rta_source_record"
        ),
    )
    op.create_table(
        "rta_distribution_issues",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("rta_distribution_record_id", sa.String(36), nullable=False),
        sa.Column("normalization_run_id", sa.String(36), nullable=False),
        sa.Column("issue_code", sa.String(32), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "issue_code IN ('unmapped_scheme', 'ambiguous_scheme', 'amount_conflict')",
            name="ck_rta_distribution_issue_code",
        ),
        sa.ForeignKeyConstraint(
            ["rta_distribution_record_id"],
            ["rta_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["normalization_run_id"],
            ["distribution_normalization_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rta_distribution_record_id",
            "normalization_run_id",
            "issue_code",
            name="uq_rta_distribution_issue_run",
        ),
    )
    op.create_index(
        op.f("ix_rta_distribution_issues_rta_distribution_record_id"),
        "rta_distribution_issues",
        ["rta_distribution_record_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rta_distribution_issues_normalization_run_id"),
        "rta_distribution_issues",
        ["normalization_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rta_distribution_issues_issue_code"),
        "rta_distribution_issues",
        ["issue_code"],
        unique=False,
    )


def downgrade() -> None:
    source_count = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM rta_scheme_captures"))
    if source_count:
        raise RuntimeError("cannot downgrade while RTA distribution captures exist")
    op.drop_index(
        op.f("ix_rta_distribution_issues_issue_code"),
        table_name="rta_distribution_issues",
    )
    op.drop_index(
        op.f("ix_rta_distribution_issues_normalization_run_id"),
        table_name="rta_distribution_issues",
    )
    op.drop_index(
        op.f("ix_rta_distribution_issues_rta_distribution_record_id"),
        table_name="rta_distribution_issues",
    )
    op.drop_table("rta_distribution_issues")
    op.drop_table("distribution_event_revision_rta_sources")
    op.drop_table("rta_distribution_record_sources")
    op.drop_index("ix_rta_distribution_capture_date", table_name="rta_distribution_records")
    op.drop_table("rta_distribution_records")
    op.drop_index("ix_rta_mapping_capture_time", table_name="rta_scheme_mapping_reviews")
    op.drop_index(
        op.f("ix_rta_scheme_mapping_reviews_status"),
        table_name="rta_scheme_mapping_reviews",
    )
    op.drop_table("rta_scheme_mapping_reviews")
    op.drop_index("ix_rta_capture_identifier", table_name="rta_scheme_captures")
    op.drop_index(
        op.f("ix_rta_scheme_captures_ingestion_batch_id"), table_name="rta_scheme_captures"
    )
    op.drop_index(op.f("ix_rta_scheme_captures_provider"), table_name="rta_scheme_captures")
    op.drop_table("rta_scheme_captures")
