"""Add an explicit benchmark issue for unusable official close values.

Revision ID: 20260825_0026
Revises: 20260824_0025
Create Date: 2026-08-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260825_0026"
down_revision: str | None = "20260824_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CHECK = (
    "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
    "'identity_unresolved', 'observation_revision', 'provisional_bse_identity', "
    "'partial_bse_roster', 'invalid_ohlc', 'security_identity_change')"
)
_NEW_CHECK = (
    "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
    "'identity_unresolved', 'observation_revision', 'provisional_bse_identity', "
    "'partial_bse_roster', 'invalid_ohlc', 'invalid_close', 'security_identity_change')"
)


def upgrade() -> None:
    with op.batch_alter_table("benchmark_issues") as batch_op:
        batch_op.drop_constraint("ck_benchmark_issue_code", type_="check")
        batch_op.create_check_constraint("ck_benchmark_issue_code", _NEW_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("benchmark_issues") as batch_op:
        batch_op.drop_constraint("ck_benchmark_issue_code", type_="check")
        batch_op.create_check_constraint("ck_benchmark_issue_code", _OLD_CHECK)
