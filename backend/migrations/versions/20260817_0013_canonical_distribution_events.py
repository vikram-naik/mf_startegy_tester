"""Add provenance-linked canonical distribution events.

Revision ID: 20260817_0013
Revises: 20260817_0012
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0013"
down_revision: str | None = "20260817_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "distribution_normalization_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("normalization_version", sa.String(64), nullable=False),
        sa.Column("source_rows_examined", sa.Integer(), nullable=False),
        sa.Column("candidate_rows", sa.Integer(), nullable=False),
        sa.Column("blocked_rows", sa.Integer(), nullable=False),
        sa.Column("events_inserted", sa.Integer(), nullable=False),
        sa.Column("revisions_inserted", sa.Integer(), nullable=False),
        sa.Column("rows_unchanged", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_distribution_normalization_run_status",
        ),
        sa.CheckConstraint("source_rows_examined >= 0", name="ck_distribution_norm_rows_examined"),
        sa.CheckConstraint("candidate_rows >= 0", name="ck_distribution_norm_candidate_rows"),
        sa.CheckConstraint("blocked_rows >= 0", name="ck_distribution_norm_blocked_rows"),
        sa.CheckConstraint("events_inserted >= 0", name="ck_distribution_norm_events_inserted"),
        sa.CheckConstraint(
            "revisions_inserted >= 0", name="ck_distribution_norm_revisions_inserted"
        ),
        sa.CheckConstraint("rows_unchanged >= 0", name="ck_distribution_norm_rows_unchanged"),
        sa.CheckConstraint(
            "source_rows_examined = candidate_rows + blocked_rows",
            name="ck_distribution_norm_source_totals",
        ),
        sa.CheckConstraint(
            "candidate_rows = revisions_inserted + rows_unchanged",
            name="ck_distribution_norm_candidate_totals",
        ),
        sa.CheckConstraint(
            "events_inserted <= revisions_inserted",
            name="ck_distribution_norm_event_revision_totals",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_distribution_normalization_runs_status"),
        "distribution_normalization_runs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "distribution_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("amfi_scheme_code", sa.String(16), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_type = 'idcw_cash'", name="ck_distribution_event_type"),
        sa.ForeignKeyConstraint(
            ["amfi_scheme_code"], ["scheme_options.amfi_scheme_code"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "amfi_scheme_code",
            "record_date",
            "event_type",
            name="uq_distribution_event_identity",
        ),
    )
    op.create_index(
        "ix_distribution_event_option_date",
        "distribution_events",
        ["amfi_scheme_code", "record_date"],
        unique=False,
    )

    op.create_table(
        "distribution_event_revisions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("distribution_event_id", sa.String(36), nullable=False),
        sa.Column("amount_per_unit_inr", sa.Text(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.Column("normalization_version", sa.String(64), nullable=False),
        sa.Column("normalization_run_id", sa.String(36), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("normalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "CAST(amount_per_unit_inr AS NUMERIC) > 0",
            name="ck_distribution_event_amount_positive",
        ),
        sa.CheckConstraint(
            "revision_number > 0", name="ck_distribution_event_revision_number_positive"
        ),
        sa.CheckConstraint(
            "length(content_signature) = 64",
            name="ck_distribution_event_revision_signature_length",
        ),
        sa.ForeignKeyConstraint(
            ["distribution_event_id"], ["distribution_events.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["normalization_run_id"], ["distribution_normalization_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature"),
        sa.UniqueConstraint(
            "distribution_event_id",
            "revision_number",
            name="uq_distribution_event_revision_number",
        ),
    )
    op.create_index(
        "uq_distribution_event_current_revision",
        "distribution_event_revisions",
        ["distribution_event_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "distribution_event_revision_sources",
        sa.Column("distribution_event_revision_id", sa.String(36), nullable=False),
        sa.Column("source_distribution_record_id", sa.String(36), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_event_revision_id"],
            ["distribution_event_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_distribution_record_id"],
            ["amfi_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("distribution_event_revision_id", "source_distribution_record_id"),
        sa.UniqueConstraint(
            "source_distribution_record_id", name="uq_distribution_event_source_record"
        ),
    )


def downgrade() -> None:
    event_count = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM distribution_events"))
    if event_count:
        raise RuntimeError("cannot downgrade while canonical distribution events exist")
    op.drop_table("distribution_event_revision_sources")
    op.drop_index(
        "uq_distribution_event_current_revision", table_name="distribution_event_revisions"
    )
    op.drop_table("distribution_event_revisions")
    op.drop_index("ix_distribution_event_option_date", table_name="distribution_events")
    op.drop_table("distribution_events")
    op.drop_index(
        op.f("ix_distribution_normalization_runs_status"),
        table_name="distribution_normalization_runs",
    )
    op.drop_table("distribution_normalization_runs")
