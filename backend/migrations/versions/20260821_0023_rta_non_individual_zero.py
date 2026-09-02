"""Preserve zero RTA non-individual distribution amounts.

Revision ID: 20260821_0023
Revises: 20260821_0022
Create Date: 2026-08-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0023"
down_revision: str | None = "20260821_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CHECK = (
    "non_individual_amount_per_unit_inr IS NULL OR "
    "CAST(non_individual_amount_per_unit_inr AS NUMERIC) > 0"
)
_NEW_CHECK = (
    "non_individual_amount_per_unit_inr IS NULL OR "
    "CAST(non_individual_amount_per_unit_inr AS NUMERIC) >= 0"
)


def upgrade() -> None:
    with op.batch_alter_table("rta_distribution_records") as batch_op:
        batch_op.drop_constraint("ck_rta_distribution_non_individual_amount", type_="check")
        batch_op.create_check_constraint("ck_rta_distribution_non_individual_amount", _NEW_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("rta_distribution_records") as batch_op:
        batch_op.drop_constraint("ck_rta_distribution_non_individual_amount", type_="check")
        batch_op.create_check_constraint("ck_rta_distribution_non_individual_amount", _OLD_CHECK)
