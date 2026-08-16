"""Track resumable NAV coverage intervals rather than a single high-water mark.

Revision ID: 20260816_0005
Revises: 20260816_0004
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0005"
down_revision: str | None = "20260816_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nav_sync_coverage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mutual_fund_id", sa.String(length=16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("last_batch_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("start_date <= end_date", name="ck_nav_sync_coverage_dates"),
        sa.ForeignKeyConstraint(["last_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nav_sync_coverage_fund_dates",
        "nav_sync_coverage",
        ["mutual_fund_id", "start_date", "end_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_nav_sync_coverage_fund_dates", table_name="nav_sync_coverage")
    op.drop_table("nav_sync_coverage")
