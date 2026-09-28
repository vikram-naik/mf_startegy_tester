"""Add the audited AMFI scheme-classification reference.

Revision ID: 20260902_0030
Revises: 20260902_0029
Create Date: 2026-09-02
"""

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0030"
down_revision: str | None = "20260902_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAPPING_VERSION = "amfi-classification-reference-2026.09.1"


def upgrade() -> None:
    op.create_table(
        "scheme_classifications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.String(length=512), nullable=False),
        sa.Column("mapping_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_scheme_classification_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_key", name="uq_scheme_classification_normalized_key"),
    )
    op.create_table(
        "scheme_classification_aliases",
        sa.Column("source_provider", sa.String(length=16), nullable=False),
        sa.Column("raw_classification", sa.Text(), nullable=False),
        sa.Column("classification_id", sa.String(length=64), nullable=False),
        sa.Column("match_type", sa.String(length=16), nullable=False),
        sa.Column("mapping_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_note", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_provider = 'amfi'", name="ck_classification_alias_provider"),
        sa.CheckConstraint(
            "match_type IN ('exact', 'formatting', 'terminology', 'manual')",
            name="ck_classification_alias_match_type",
        ),
        sa.ForeignKeyConstraint(
            ["classification_id"], ["scheme_classifications.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("source_provider", "raw_classification"),
    )
    op.create_index(
        "ix_classification_alias_canonical",
        "scheme_classification_aliases",
        ["classification_id"],
        unique=False,
    )
    _backfill_existing_amfi_labels()


def downgrade() -> None:
    op.drop_index("ix_classification_alias_canonical", table_name="scheme_classification_aliases")
    op.drop_table("scheme_classification_aliases")
    op.drop_table("scheme_classifications")


def _backfill_existing_amfi_labels() -> None:
    connection = op.get_bind()
    raw_labels = tuple(
        str(row[0])
        for row in connection.execute(
            sa.text("SELECT DISTINCT scheme_classification FROM scheme_metadata_versions")
        )
    )
    definitions: dict[str, tuple[str, str, str]] = {}
    for raw in sorted(raw_labels):
        definition = _classification_definition(raw)
        definitions.setdefault(definition[0], definition)
    now = datetime.now(UTC)
    if definitions:
        connection.execute(
            sa.text(
                "INSERT INTO scheme_classifications "
                "(id, display_name, normalized_key, mapping_version, status, "
                "created_at, updated_at) "
                "VALUES (:id, :display_name, :normalized_key, :mapping_version, 'active', "
                ":created_at, :updated_at)"
            ),
            [
                {
                    "id": item[0],
                    "display_name": item[1],
                    "normalized_key": item[2],
                    "mapping_version": _MAPPING_VERSION,
                    "created_at": now,
                    "updated_at": now,
                }
                for item in definitions.values()
            ],
        )
    if raw_labels:
        connection.execute(
            sa.text(
                "INSERT INTO scheme_classification_aliases "
                "(source_provider, raw_classification, classification_id, match_type, "
                "mapping_version, evidence_note, approved_at) "
                "VALUES ('amfi', :raw_classification, :classification_id, :match_type, "
                ":mapping_version, :evidence_note, :approved_at)"
            ),
            [
                {
                    "raw_classification": raw,
                    "classification_id": _classification_definition(raw)[0],
                    "match_type": _match_type(raw),
                    "mapping_version": _MAPPING_VERSION,
                    "evidence_note": _evidence_note(raw),
                    "approved_at": now,
                }
                for raw in sorted(raw_labels)
            ],
        )


def _classification_definition(raw: str) -> tuple[str, str, str]:
    display_name = _canonicalize(raw)
    normalized_key = display_name.casefold()
    identifier = f"amfi-{hashlib.sha256(normalized_key.encode('utf-8')).hexdigest()[:32]}"
    return identifier, display_name, normalized_key


def _canonicalize(value: str) -> str:
    normalized = " ".join(value.split())
    normalized = re.sub(r"\s*\(\s*", " ( ", normalized)
    normalized = re.sub(r"\s*\)\s*", " )", normalized)
    normalized = " ".join(normalized.split())
    normalized = re.sub(
        r"\b(Equity|Hybrid) Schemes(?=\s*-)",
        r"\1 Scheme",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\bMidcap\b", "Mid Cap", normalized, flags=re.IGNORECASE)
    return re.sub(
        r"\bLarge\s+and\s+Mid\s+Cap\b",
        "Large & Mid Cap",
        normalized,
        flags=re.IGNORECASE,
    )


def _match_type(raw: str) -> str:
    canonical = _canonicalize(raw)
    if raw == canonical:
        return "exact"
    lowered_raw = raw.casefold()
    if any(
        marker in lowered_raw
        for marker in ("equity schemes -", "hybrid schemes -", "midcap", " large and mid cap")
    ):
        return "terminology"
    return "formatting"


def _evidence_note(raw: str) -> str:
    if _match_type(raw) == "exact":
        return "raw AMFI label already equals the canonical display label"
    if _match_type(raw) == "terminology":
        return (
            "approved deterministic terminology equivalence: Scheme/Schemes, Midcap/Mid Cap, "
            "or and/&; all other normalized classification tokens are identical"
        )
    return "approved deterministic whitespace, capitalization, or parenthesis normalization"
