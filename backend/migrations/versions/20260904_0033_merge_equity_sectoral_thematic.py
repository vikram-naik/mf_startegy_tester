"""Merge reviewed equity sectoral and thematic screener aliases.

Revision ID: 20260904_0033
Revises: 20260904_0032
Create Date: 2026-09-04
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0033"
down_revision: str | None = "20260904_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TARGET_ALIAS_ID = "screener-open-equity-sectoral-thematic"
_TARGET_NAME = "Sectoral / Thematic Equity"
_TARGET_NORMALIZED_NAME = "sectoral / thematic equity"
_REASON = (
    "Reviewed merge of the broad and specific equity sectoral/thematic AMFI classifications"
)
_DISPLAY_NAMES = (
    "Open Ended Schemes ( Equity Scheme - Sectoral Fund )",
    "Open Ended Schemes ( Equity Scheme - Sectoral/ Thematic )",
    "Open Ended Schemes ( Equity Scheme - Thematic Fund )",
)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, display_name FROM scheme_classifications "
            "WHERE display_name IN (:sectoral, :combined, :thematic)"
        ),
        {
            "sectoral": _DISPLAY_NAMES[0],
            "combined": _DISPLAY_NAMES[1],
            "thematic": _DISPLAY_NAMES[2],
        },
    ).all()
    if not rows:
        # A new local database has no classifications until the first AMFI publication.
        # The runtime reviewed defaults create this merged alias during that publication.
        return
    identifiers_by_name = {str(display_name): str(identifier) for identifier, display_name in rows}
    missing_names = sorted(set(_DISPLAY_NAMES) - identifiers_by_name.keys())
    if missing_names:
        raise RuntimeError(
            "cannot merge equity sectoral/thematic aliases; missing canonical classifications: "
            f"{missing_names}"
        )
    member_ids = sorted(identifiers_by_name.values())
    assignments = connection.execute(
        sa.text(
            "SELECT m.classification_id, m.alias_id, a.status, a.version "
            "FROM screener_classification_alias_members m "
            "JOIN screener_classification_aliases a ON a.id = m.alias_id "
            "WHERE m.classification_id IN (:first_id, :second_id, :third_id)"
        ),
        {
            "first_id": member_ids[0],
            "second_id": member_ids[1],
            "third_id": member_ids[2],
        },
    ).all()
    expected_assignments = {
        classification_id: f"screener-singleton-{classification_id.removeprefix('amfi-')}"
        for classification_id in member_ids
    }
    actual_assignments = {
        str(classification_id): (str(alias_id), str(status), int(version))
        for classification_id, alias_id, status, version in assignments
    }
    unexpected = {
        classification_id: actual_assignments.get(classification_id)
        for classification_id, alias_id in expected_assignments.items()
        if actual_assignments.get(classification_id) != (alias_id, "active", 1)
    }
    if unexpected:
        raise RuntimeError(
            "cannot replace locally edited equity sectoral/thematic aliases: "
            f"{unexpected}"
        )
    if connection.execute(
        sa.text("SELECT 1 FROM screener_classification_aliases WHERE id = :alias_id"),
        {"alias_id": _TARGET_ALIAS_ID},
    ).first() is not None:
        raise RuntimeError(f"classification alias already exists: {_TARGET_ALIAS_ID}")

    now = datetime.now(UTC)
    for source_alias_id in sorted(expected_assignments.values()):
        connection.execute(
            sa.text(
                "DELETE FROM screener_classification_alias_members WHERE alias_id = :alias_id"
            ),
            {"alias_id": source_alias_id},
        )
        connection.execute(
            sa.text(
                "UPDATE screener_classification_aliases "
                "SET status = 'inactive', version = 2, updated_at = :now "
                "WHERE id = :alias_id"
            ),
            {"alias_id": source_alias_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO screener_classification_alias_revisions "
                "(id, alias_id, version, name, normalized_name, structure_type, status, "
                "member_classification_ids, change_reason, created_at) "
                "SELECT :id, id, 2, name, normalized_name, structure_type, 'inactive', "
                ":member_ids, :reason, :created_at "
                "FROM screener_classification_aliases WHERE id = :alias_id"
            ),
            {
                "id": str(uuid4()),
                "alias_id": source_alias_id,
                "member_ids": json.dumps([]),
                "reason": _REASON,
                "created_at": now,
            },
        )

    connection.execute(
        sa.text(
            "INSERT INTO screener_classification_aliases "
            "(id, name, normalized_name, structure_type, status, version, created_at, updated_at) "
            "VALUES (:id, :name, :normalized_name, 'open_ended', 'active', 1, :now, :now)"
        ),
        {
            "id": _TARGET_ALIAS_ID,
            "name": _TARGET_NAME,
            "normalized_name": _TARGET_NORMALIZED_NAME,
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
            {
                "classification_id": classification_id,
                "alias_id": _TARGET_ALIAS_ID,
                "added_at": now,
            }
            for classification_id in member_ids
        ],
    )
    connection.execute(
        sa.text(
            "INSERT INTO screener_classification_alias_revisions "
            "(id, alias_id, version, name, normalized_name, structure_type, status, "
            "member_classification_ids, change_reason, created_at) "
            "VALUES (:id, :alias_id, 1, :name, :normalized_name, 'open_ended', 'active', "
            ":member_ids, :reason, :created_at)"
        ),
        {
            "id": str(uuid4()),
            "alias_id": _TARGET_ALIAS_ID,
            "name": _TARGET_NAME,
            "normalized_name": _TARGET_NORMALIZED_NAME,
            "member_ids": json.dumps(member_ids),
            "reason": _REASON,
            "created_at": now,
        },
    )


def downgrade() -> None:
    connection = op.get_bind()
    target = connection.execute(
        sa.text(
            "SELECT status, version FROM screener_classification_aliases WHERE id = :alias_id"
        ),
        {"alias_id": _TARGET_ALIAS_ID},
    ).first()
    if target != ("active", 1):
        raise RuntimeError(
            "refusing to remove an edited equity sectoral/thematic alias during downgrade"
        )
    member_ids = [
        str(value)
        for value in connection.execute(
            sa.text(
                "SELECT classification_id FROM screener_classification_alias_members "
                "WHERE alias_id = :alias_id ORDER BY classification_id"
            ),
            {"alias_id": _TARGET_ALIAS_ID},
        ).scalars()
    ]
    if len(member_ids) != 3:
        raise RuntimeError(
            "refusing to downgrade an equity sectoral/thematic alias with changed members"
        )
    source_alias_ids = {
        classification_id: f"screener-singleton-{classification_id.removeprefix('amfi-')}"
        for classification_id in member_ids
    }
    source_rows = connection.execute(
        sa.text(
            "SELECT id, status, version FROM screener_classification_aliases "
            "WHERE id IN (:first_id, :second_id, :third_id)"
        ),
        {
            "first_id": sorted(source_alias_ids.values())[0],
            "second_id": sorted(source_alias_ids.values())[1],
            "third_id": sorted(source_alias_ids.values())[2],
        },
    ).all()
    actual_source_states = {
        (str(identifier), str(status), int(version))
        for identifier, status, version in source_rows
    }
    if actual_source_states != {
        (alias_id, "inactive", 2) for alias_id in source_alias_ids.values()
    }:
        raise RuntimeError(
            "refusing to restore edited equity sectoral/thematic source aliases during downgrade"
        )

    connection.execute(
        sa.text(
            "DELETE FROM screener_classification_alias_revisions WHERE alias_id = :alias_id"
        ),
        {"alias_id": _TARGET_ALIAS_ID},
    )
    connection.execute(
        sa.text("DELETE FROM screener_classification_alias_members WHERE alias_id = :alias_id"),
        {"alias_id": _TARGET_ALIAS_ID},
    )
    connection.execute(
        sa.text("DELETE FROM screener_classification_aliases WHERE id = :alias_id"),
        {"alias_id": _TARGET_ALIAS_ID},
    )
    now = datetime.now(UTC)
    for classification_id, source_alias_id in sorted(source_alias_ids.items()):
        connection.execute(
            sa.text(
                "DELETE FROM screener_classification_alias_revisions "
                "WHERE alias_id = :alias_id AND version = 2 AND change_reason = :reason"
            ),
            {"alias_id": source_alias_id, "reason": _REASON},
        )
        connection.execute(
            sa.text(
                "UPDATE screener_classification_aliases "
                "SET status = 'active', version = 1, updated_at = :now WHERE id = :alias_id"
            ),
            {"alias_id": source_alias_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO screener_classification_alias_members "
                "(classification_id, alias_id, added_at) "
                "VALUES (:classification_id, :alias_id, :added_at)"
            ),
            {
                "classification_id": classification_id,
                "alias_id": source_alias_id,
                "added_at": now,
            },
        )
