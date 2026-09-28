"""Remove the superseded strategy catalog.

Revision ID: 20260902_0029
Revises: 20260902_0028
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0029"
down_revision: str | None = "20260902_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    saved_rows = connection.scalar(
        sa.text(
            "SELECT (SELECT COUNT(*) FROM strategies) + (SELECT COUNT(*) FROM strategy_versions)"
        )
    )
    if saved_rows:
        raise RuntimeError(
            "strategy catalog contains saved records; export or back them up before removing "
            "the superseded strategy tables"
        )

    op.drop_index(op.f("ix_strategy_versions_strategy_id"), table_name="strategy_versions")
    op.drop_table("strategy_versions")
    op.drop_index(op.f("ix_strategies_name"), table_name="strategies")
    op.drop_table("strategies")


def downgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_strategies_name"), "strategies", ["name"], unique=False)
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("strategy_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_strategy_versions_positive_version"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
    )
    op.create_index(
        op.f("ix_strategy_versions_strategy_id"),
        "strategy_versions",
        ["strategy_id"],
        unique=False,
    )
