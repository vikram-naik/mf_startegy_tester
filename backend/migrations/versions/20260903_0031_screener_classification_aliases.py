"""Add audited, user-facing screener classification aliases.

Revision ID: 20260903_0031
Revises: 20260902_0030
Create Date: 2026-09-03
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0031"
down_revision: str | None = "20260902_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _open(*labels: str) -> tuple[str, ...]:
    return tuple(f"Open Ended Schemes ( {label} )" for label in labels)


_DEFAULT_ALIASES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "screener-open-debt-banking-psu",
        "Banking and PSU Debt",
        "open_ended",
        _open(
            "Debt Scheme - Banking and PSU Fund",
            "Income/Debt Oriented Schemes - Banking and PSU Debt Fund",
        ),
    ),
    (
        "screener-open-debt-corporate-bond",
        "Corporate Bond",
        "open_ended",
        _open(
            "Debt Scheme - Corporate Bond Fund",
            "Income/Debt Oriented Schemes - Corporate Bond Fund",
        ),
    ),
    (
        "screener-open-debt-credit-risk",
        "Credit Risk",
        "open_ended",
        _open("Debt Scheme - Credit Risk Fund", "Income/Debt Oriented Schemes - Credit Risk Fund"),
    ),
    (
        "screener-open-debt-dynamic-term",
        "Dynamic Term",
        "open_ended",
        _open("Debt Scheme - Dynamic Bond", "Income/Debt Oriented Schemes - Dynamic Term Fund"),
    ),
    (
        "screener-open-debt-floating-rate",
        "Floating Interest Rates",
        "open_ended",
        _open(
            "Debt Scheme - Floater Fund",
            "Income/Debt Oriented Schemes - Floating Interest Rates Fund",
            "Floating Rate",
        ),
    ),
    (
        "screener-open-debt-gilt",
        "Gilt",
        "open_ended",
        _open("Debt Scheme - Gilt Fund", "Income/Debt Oriented Schemes - Gilt Fund", "Gilt"),
    ),
    (
        "screener-open-debt-gilt-10-year",
        "10-year Constant Maturity Gilt",
        "open_ended",
        _open(
            "Debt Scheme - Gilt Fund with 10 year constant duration",
            "Income/Debt Oriented Schemes - 10-year Constant Maturity Gilt Fund",
        ),
    ),
    (
        "screener-open-debt-liquid",
        "Liquid",
        "open_ended",
        _open("Debt Scheme - Liquid Fund", "Income/Debt Oriented Schemes - Liquid Fund", "Liquid"),
    ),
    (
        "screener-open-debt-long-term",
        "Long Term",
        "open_ended",
        _open("Debt Scheme - Long Duration Fund", "Income/Debt Oriented Schemes - Long Term Fund"),
    ),
    (
        "screener-open-debt-ultra-short-to-short",
        "Ultra Short to Short Term",
        "open_ended",
        _open(
            "Debt Scheme - Low Duration Fund",
            "Income/Debt Oriented Schemes - Ultra Short to Short Term Fund",
        ),
    ),
    (
        "screener-open-debt-medium-term",
        "Medium Term",
        "open_ended",
        _open(
            "Debt Scheme - Medium Duration Fund", "Income/Debt Oriented Schemes - Medium Term Fund"
        ),
    ),
    (
        "screener-open-debt-medium-long-term",
        "Medium to Long Term",
        "open_ended",
        _open(
            "Debt Scheme - Medium to Long Duration Fund",
            "Income/Debt Oriented Schemes - Medium to Long Term Fund",
        ),
    ),
    (
        "screener-open-debt-money-market",
        "Money Market",
        "open_ended",
        _open(
            "Debt Scheme - Money Market Fund",
            "Income/Debt Oriented Schemes - Money Market Fund",
            "Money Market",
        ),
    ),
    (
        "screener-open-debt-overnight",
        "Overnight",
        "open_ended",
        _open("Debt Scheme - Overnight Fund", "Income/Debt Oriented Schemes - Overnight Fund"),
    ),
    (
        "screener-open-debt-short-term",
        "Short Term",
        "open_ended",
        _open(
            "Debt Scheme - Short Duration Fund", "Income/Debt Oriented Schemes - Short Term Fund"
        ),
    ),
    (
        "screener-open-debt-ultra-short-term",
        "Ultra Short Term",
        "open_ended",
        _open(
            "Debt Scheme - Ultra Short Duration Fund",
            "Income/Debt Oriented Schemes - Ultra Short Term Fund",
        ),
    ),
    (
        "screener-open-equity-elss",
        "ELSS - Tax Saver",
        "open_ended",
        _open("Equity Scheme - ELSS", "Equity Scheme - ELSS- Tax Saver Fund"),
    ),
    (
        "screener-open-hybrid-dynamic-allocation",
        "Dynamic Asset Allocation",
        "open_ended",
        _open(
            "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage",
            "Hybrid Scheme - Balanced Advantage Fund/ Dynamic Asset Allocation",
        ),
    ),
    (
        "screener-open-hybrid-equity-savings",
        "Equity Savings",
        "open_ended",
        _open("Hybrid Scheme - Equity Savings", "Hybrid Scheme - Equity Savings Fund"),
    ),
    (
        "screener-open-hybrid-multi-asset",
        "Multi Asset Allocation",
        "open_ended",
        _open(
            "Hybrid Scheme - Multi Asset Allocation", "Hybrid Scheme - Multi Asset Allocation Fund"
        ),
    ),
    (
        "screener-open-solution-childrens",
        "Children's",
        "open_ended",
        _open("Children’s Fund - Childrens' Fund", "Solution Oriented Scheme - Children’s Fund"),  # noqa: RUF001 - exact AMFI labels
    ),
    (
        "screener-open-solution-retirement",
        "Retirement",
        "open_ended",
        _open(
            "Solution Oriented Scheme - Retirement Fund",
            "Solution Oriented Schemes ** - Retirement Fund",
        ),
    ),
    (
        "screener-open-fof-domestic",
        "FoF - Domestic",
        "open_ended",
        _open(
            "Fund of Funds - Domestic",
            "Fund of Funds Scheme ( Domestic )- Fund of Funds Scheme ( Domestic )",
            "Other Scheme - FoF Domestic",
        ),
    ),
    (
        "screener-open-fof-overseas",
        "FoF - Overseas",
        "open_ended",
        _open(
            "Fund of Funds - Overseas",
            "Other Scheme - FoF Overseas",
            "Overseas Fund of Funds - Fund of Funds investing overseas",
        ),
    ),
    (
        "screener-open-etf-gold",
        "Gold ETF",
        "open_ended",
        _open("Exchange Traded Funds ( ETFs )- Gold ETF", "GOLD ETFs", "Other Scheme - Gold ETF"),
    ),
)


def upgrade() -> None:
    op.create_table(
        "screener_classification_aliases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("structure_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "structure_type IN ('open_ended', 'close_ended', 'interval', 'other')",
            name="ck_screener_classification_alias_structure",
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_screener_alias_status"),
        sa.CheckConstraint("version > 0", name="ck_screener_alias_positive_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "structure_type", "normalized_name", name="uq_screener_alias_structure_name"
        ),
    )
    op.create_table(
        "screener_classification_alias_members",
        sa.Column("classification_id", sa.String(length=64), nullable=False),
        sa.Column("alias_id", sa.String(length=64), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["alias_id"], ["screener_classification_aliases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["classification_id"], ["scheme_classifications.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("classification_id"),
    )
    op.create_index(
        "ix_screener_alias_member_alias",
        "screener_classification_alias_members",
        ["alias_id"],
        unique=False,
    )
    op.create_table(
        "screener_classification_alias_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("alias_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("structure_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("member_classification_ids", sa.JSON(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_screener_alias_revision_positive_version"),
        sa.CheckConstraint(
            "structure_type IN ('open_ended', 'close_ended', 'interval', 'other')",
            name="ck_screener_alias_revision_structure",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_screener_alias_revision_status"
        ),
        sa.ForeignKeyConstraint(
            ["alias_id"], ["screener_classification_aliases.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias_id", "version", name="uq_screener_alias_revision_version"),
    )
    _seed_existing_classifications()


def downgrade() -> None:
    op.drop_table("screener_classification_alias_revisions")
    op.drop_index(
        "ix_screener_alias_member_alias", table_name="screener_classification_alias_members"
    )
    op.drop_table("screener_classification_alias_members")
    op.drop_table("screener_classification_aliases")


def _seed_existing_classifications() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, display_name FROM scheme_classifications")).all()
    identifiers_by_name = {str(display_name): str(identifier) for identifier, display_name in rows}
    now = datetime.now(UTC)
    for alias_id, name, structure_type, display_names in _DEFAULT_ALIASES:
        member_ids = sorted(
            identifiers_by_name[display_name]
            for display_name in display_names
            if display_name in identifiers_by_name
        )
        if not member_ids:
            continue
        normalized_name = " ".join(name.split()).casefold()
        connection.execute(
            sa.text(
                "INSERT INTO screener_classification_aliases "
                "(id, name, normalized_name, structure_type, status, version, "
                "created_at, updated_at) "
                "VALUES (:id, :name, :normalized_name, :structure_type, 'active', 1, :now, :now)"
            ),
            {
                "id": alias_id,
                "name": name,
                "normalized_name": normalized_name,
                "structure_type": structure_type,
                "now": now,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO screener_classification_alias_members "
                "(classification_id, alias_id, added_at) "
                "VALUES (:classification_id, :alias_id, :added_at)"
            ),
            [
                {"classification_id": member_id, "alias_id": alias_id, "added_at": now}
                for member_id in member_ids
            ],
        )
        connection.execute(
            sa.text(
                "INSERT INTO screener_classification_alias_revisions "
                "(id, alias_id, version, name, normalized_name, structure_type, status, "
                "member_classification_ids, change_reason, created_at) "
                "VALUES (:id, :alias_id, 1, :name, :normalized_name, :structure_type, "
                "'active', :member_ids, :reason, :created_at)"
            ),
            {
                "id": str(uuid4()),
                "alias_id": alias_id,
                "name": name,
                "normalized_name": normalized_name,
                "structure_type": structure_type,
                "member_ids": json.dumps(member_ids),
                "reason": "Default reviewed AMFI predecessor/successor mapping",
                "created_at": now,
            },
        )
