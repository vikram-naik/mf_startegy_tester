"""Preserve explicit AMFI distribution plan and option qualifiers.

Revision ID: 20260821_0018
Revises: 20260820_0017
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0018"
down_revision: str | None = "20260820_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.add_column(sa.Column("source_plan", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("source_option", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.drop_column("source_option")
        batch_op.drop_column("source_plan")
