"""Retain AMFI scheme details whose launch date is unknown.

Revision ID: 20260902_0028
Revises: 20260831_0027
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0028"
down_revision: str | None = "20260831_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ISSUE_CHECK = (
    "issue_code IN ('scheme_list_failure', 'scheme_detail_failure', "
    "'identity_mismatch', 'launch_date_conflict', 'catalog_member_removed', "
    "'name_changed_without_effective_date')"
)
_NEW_ISSUE_CHECK = (
    "issue_code IN ('scheme_list_failure', 'scheme_detail_failure', "
    "'identity_mismatch', 'launch_date_conflict', 'catalog_member_removed', "
    "'name_changed_without_effective_date', 'missing_launch_date')"
)


def upgrade() -> None:
    with op.batch_alter_table("amfi_scheme_detail_records") as batch_op:
        batch_op.alter_column(
            "launch_date",
            existing_type=sa.Date(),
            nullable=True,
        )
    with op.batch_alter_table("scheme_lifecycle_issues") as batch_op:
        batch_op.drop_constraint("ck_scheme_lifecycle_issue_code", type_="check")
        batch_op.create_check_constraint("ck_scheme_lifecycle_issue_code", _NEW_ISSUE_CHECK)


def downgrade() -> None:
    connection = op.get_bind()
    unsupported_rows = connection.scalar(
        sa.text(
            "SELECT "
            "(SELECT COUNT(*) FROM amfi_scheme_detail_records WHERE launch_date IS NULL) + "
            "(SELECT COUNT(*) FROM scheme_lifecycle_issues "
            "WHERE issue_code = 'missing_launch_date')"
        )
    )
    if unsupported_rows:
        raise RuntimeError(
            "cannot remove unknown-launch support while retained details or issues depend on it"
        )
    with op.batch_alter_table("scheme_lifecycle_issues") as batch_op:
        batch_op.drop_constraint("ck_scheme_lifecycle_issue_code", type_="check")
        batch_op.create_check_constraint("ck_scheme_lifecycle_issue_code", _OLD_ISSUE_CHECK)
    with op.batch_alter_table("amfi_scheme_detail_records") as batch_op:
        batch_op.alter_column(
            "launch_date",
            existing_type=sa.Date(),
            nullable=False,
        )
