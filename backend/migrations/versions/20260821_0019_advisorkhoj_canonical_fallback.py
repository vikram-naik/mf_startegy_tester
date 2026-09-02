"""Add auditable AdvisorKhoj canonical fallback provenance.

Revision ID: 20260821_0019
Revises: 20260821_0018
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0019"
down_revision: str | None = "20260821_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "distribution_event_revision_advisorkhoj_sources",
        sa.Column("distribution_event_revision_id", sa.String(length=36), nullable=False),
        sa.Column("advisorkhoj_distribution_record_id", sa.String(length=36), nullable=False),
        sa.Column("mapping_review_id", sa.String(length=36), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_event_revision_id"],
            ["distribution_event_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["advisorkhoj_distribution_record_id"],
            ["advisorkhoj_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_review_id"],
            ["advisorkhoj_scheme_mapping_reviews.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "distribution_event_revision_id",
            "advisorkhoj_distribution_record_id",
        ),
        sa.UniqueConstraint(
            "advisorkhoj_distribution_record_id",
            name="uq_distribution_event_advisorkhoj_source_record",
        ),
    )
    op.create_table(
        "advisorkhoj_distribution_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("advisorkhoj_distribution_record_id", sa.String(length=36), nullable=False),
        sa.Column("mapping_review_id", sa.String(length=36), nullable=False),
        sa.Column("normalization_run_id", sa.String(length=36), nullable=False),
        sa.Column("issue_code", sa.String(length=32), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "issue_code IN ('higher_priority_conflict', 'same_priority_conflict')",
            name="ck_ak_distribution_issue_code",
        ),
        sa.ForeignKeyConstraint(
            ["advisorkhoj_distribution_record_id"],
            ["advisorkhoj_distribution_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_review_id"],
            ["advisorkhoj_scheme_mapping_reviews.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["normalization_run_id"],
            ["distribution_normalization_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "advisorkhoj_distribution_record_id",
            "mapping_review_id",
            "issue_code",
            name="uq_ak_distribution_issue_review",
        ),
    )
    op.create_index(
        "ix_advisorkhoj_distribution_issues_advisorkhoj_distribution_record_id",
        "advisorkhoj_distribution_issues",
        ["advisorkhoj_distribution_record_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_advisorkhoj_distribution_issues_advisorkhoj_distribution_record_id",
        table_name="advisorkhoj_distribution_issues",
    )
    op.drop_table("advisorkhoj_distribution_issues")
    op.drop_table("distribution_event_revision_advisorkhoj_sources")
