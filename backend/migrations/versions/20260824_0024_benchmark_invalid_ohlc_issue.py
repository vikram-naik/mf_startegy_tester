"""Add an explicit benchmark issue for inconsistent official OHLC values.

Revision ID: 20260824_0024
Revises: 20260821_0023
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0024"
down_revision: str | None = "20260821_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CHECK = (
    "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
    "'identity_unresolved', 'observation_revision', 'provisional_bse_identity', "
    "'partial_bse_roster')"
)
_NEW_CHECK = (
    "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
    "'identity_unresolved', 'observation_revision', 'provisional_bse_identity', "
    "'partial_bse_roster', 'invalid_ohlc')"
)


def upgrade() -> None:
    with op.batch_alter_table("benchmark_issues") as batch_op:
        batch_op.drop_constraint("ck_benchmark_issue_code", type_="check")
        batch_op.create_check_constraint("ck_benchmark_issue_code", _NEW_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("benchmark_issues") as batch_op:
        batch_op.drop_constraint("ck_benchmark_issue_code", type_="check")
        batch_op.create_check_constraint("ck_benchmark_issue_code", _OLD_CHECK)
