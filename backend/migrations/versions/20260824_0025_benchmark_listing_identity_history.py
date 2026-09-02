"""Version exchange listing identity across security conversions.

Revision ID: 20260824_0025
Revises: 20260824_0024
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0025"
down_revision: str | None = "20260824_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CHECK = (
    "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
    "'identity_unresolved', 'observation_revision', 'provisional_bse_identity', "
    "'partial_bse_roster', 'invalid_ohlc')"
)
_NEW_CHECK = (
    "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
    "'identity_unresolved', 'observation_revision', 'provisional_bse_identity', "
    "'partial_bse_roster', 'invalid_ohlc', 'security_identity_change')"
)


def upgrade() -> None:
    with op.batch_alter_table("benchmark_exchange_listings") as batch_op:
        batch_op.drop_constraint("uq_benchmark_listing_exchange_security", type_="unique")
        batch_op.create_unique_constraint(
            "uq_benchmark_listing_exchange_security_instrument",
            ("exchange", "source_security_id", "benchmark_instrument_id"),
        )
        batch_op.create_index(
            "ix_benchmark_listing_exchange_security",
            ("exchange", "source_security_id"),
            unique=False,
        )
    with op.batch_alter_table("benchmark_issues") as batch_op:
        batch_op.drop_constraint("ck_benchmark_issue_code", type_="check")
        batch_op.create_check_constraint("ck_benchmark_issue_code", _NEW_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("benchmark_issues") as batch_op:
        batch_op.drop_constraint("ck_benchmark_issue_code", type_="check")
        batch_op.create_check_constraint("ck_benchmark_issue_code", _OLD_CHECK)
    with op.batch_alter_table("benchmark_exchange_listings") as batch_op:
        batch_op.drop_index("ix_benchmark_listing_exchange_security")
        batch_op.drop_constraint(
            "uq_benchmark_listing_exchange_security_instrument", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_benchmark_listing_exchange_security", ("exchange", "source_security_id")
        )
