"""Preserve the exact AMFI distribution value text.

Revision ID: 20260817_0008
Revises: 20260817_0007
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0008"
down_revision: str | None = "20260817_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.add_column(sa.Column("raw_source_value", sa.Text(), nullable=True))
    op.execute(sa.text("UPDATE amfi_distribution_records SET raw_source_value = source_value"))
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.alter_column("raw_source_value", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.drop_column("raw_source_value")
