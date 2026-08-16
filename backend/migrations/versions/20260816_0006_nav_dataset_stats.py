"""Add transactionally maintained NAV dataset statistics.

Revision ID: 20260816_0006
Revises: 20260816_0005
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0006"
down_revision: str | None = "20260816_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nav_dataset_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scheme_options", sa.Integer(), nullable=False),
        sa.Column("valid_current_rows", sa.Integer(), nullable=False),
        sa.Column("error_current_rows", sa.Integer(), nullable=False),
        sa.Column("earliest_valid_nav_date", sa.Date(), nullable=True),
        sa.Column("latest_valid_nav_date", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_nav_dataset_stats_singleton"),
        sa.CheckConstraint("scheme_options >= 0", name="ck_nav_stats_schemes_nonnegative"),
        sa.CheckConstraint("valid_current_rows >= 0", name="ck_nav_stats_valid_nonnegative"),
        sa.CheckConstraint("error_current_rows >= 0", name="ck_nav_stats_error_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO nav_dataset_stats (
                id, scheme_options, valid_current_rows, error_current_rows,
                earliest_valid_nav_date, latest_valid_nav_date, updated_at
            )
            SELECT
                1,
                (SELECT COUNT(*) FROM scheme_options),
                COALESCE(SUM(CASE WHEN quality_status = 'valid' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN quality_status = 'error' THEN 1 ELSE 0 END), 0),
                MIN(CASE WHEN quality_status = 'valid' THEN nav_date END),
                MAX(CASE WHEN quality_status = 'valid' THEN nav_date END),
                CURRENT_TIMESTAMP
            FROM nav_revisions
            WHERE is_current = 1
            """
        )
    )


def downgrade() -> None:
    op.drop_table("nav_dataset_stats")
