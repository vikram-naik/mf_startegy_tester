"""Represent non-scalar AMFI distribution ratios.

Revision ID: 20260817_0009
Revises: 20260817_0008
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0009"
down_revision: str | None = "20260817_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.drop_constraint("ck_distribution_source_unit", type_="check")
        batch_op.alter_column("source_value", existing_type=sa.Text(), nullable=True)
        batch_op.add_column(sa.Column("ratio_numerator", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ratio_denominator", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_distribution_source_unit",
            "source_unit IN ('percentage', 'amount', 'ratio')",
        )
        batch_op.create_check_constraint(
            "ck_distribution_value_shape",
            "((source_unit IN ('percentage', 'amount') "
            "AND source_value IS NOT NULL "
            "AND ratio_numerator IS NULL "
            "AND ratio_denominator IS NULL) "
            "OR (source_unit = 'ratio' "
            "AND source_value IS NULL "
            "AND ratio_numerator > 0 "
            "AND ratio_denominator > 0))",
        )


def downgrade() -> None:
    ratio_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM amfi_distribution_records WHERE source_unit = 'ratio'")
    )
    if ratio_count:
        raise RuntimeError(
            "cannot downgrade distribution ratio support while ratio source rows exist"
        )
    with op.batch_alter_table("amfi_distribution_records") as batch_op:
        batch_op.drop_constraint("ck_distribution_value_shape", type_="check")
        batch_op.drop_constraint("ck_distribution_source_unit", type_="check")
        batch_op.drop_column("ratio_denominator")
        batch_op.drop_column("ratio_numerator")
        batch_op.alter_column("source_value", existing_type=sa.Text(), nullable=False)
        batch_op.create_check_constraint(
            "ck_distribution_source_unit",
            "source_unit IN ('percentage', 'amount')",
        )
