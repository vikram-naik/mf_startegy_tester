"""Represent AMFI percentage values with amount annotations.

Revision ID: 20260817_0010
Revises: 20260817_0009
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0010"
down_revision: str | None = "20260817_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.add_column(sa.Column("annotated_amount_per_unit_inr", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_distribution_annotated_amount",
            "(annotated_amount_per_unit_inr IS NULL "
            "OR (source_unit = 'percentage' "
            "AND CAST(annotated_amount_per_unit_inr AS NUMERIC) > 0))",
        )


def downgrade() -> None:
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.drop_constraint("ck_distribution_annotated_amount", type_="check")
        batch_op.drop_column("annotated_amount_per_unit_inr")
