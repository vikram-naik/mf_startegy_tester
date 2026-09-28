"""Allow RTA identity reviews concluded from the RTA's own published NAV fingerprint.

Revision ID: 20260928_0034
Revises: 20260904_0033
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0034"
down_revision: str | None = "20260904_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CHECK = "mapping_method IN ('exact_name_plan_nav', 'manual', 'none')"
_NEW_CHECK = "mapping_method IN ('exact_name_plan_nav', 'nav_fingerprint', 'manual', 'none')"


def upgrade() -> None:
    with op.batch_alter_table("rta_scheme_mapping_reviews") as batch_op:
        batch_op.drop_constraint("ck_rta_mapping_method", type_="check")
        batch_op.create_check_constraint("ck_rta_mapping_method", _NEW_CHECK)


def downgrade() -> None:
    fingerprint_reviews = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM rta_scheme_mapping_reviews "
            "WHERE mapping_method = 'nav_fingerprint'"
        )
    )
    if fingerprint_reviews:
        raise RuntimeError(
            "Refusing downgrade: append-only RTA mapping reviews use 'nav_fingerprint' "
            f"({fingerprint_reviews} rows). Restore a pre-upgrade database backup instead."
        )
    with op.batch_alter_table("rta_scheme_mapping_reviews") as batch_op:
        batch_op.drop_constraint("ck_rta_mapping_method", type_="check")
        batch_op.create_check_constraint("ck_rta_mapping_method", _OLD_CHECK)
