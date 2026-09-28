"""Give every active canonical classification a screener alias.

Revision ID: 20260904_0032
Revises: 20260903_0031
Create Date: 2026-09-04
"""

import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0032"
down_revision: str | None = "20260903_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASON = "Automatic singleton alias for one canonical AMFI classification"
_STRUCTURE_WRAPPER = re.compile(
    r"^(?:Open Ended Schemes|Close Ended Schemes|Interval Fund Schemes)\s*\(\s*(.*?)\s*\)$",
    flags=re.IGNORECASE,
)
_LEAF_PREFIXES = (
    "children",
    "debt scheme",
    "equity scheme",
    "exchange traded funds",
    "fund of funds scheme",
    "hybrid scheme",
    "income/debt oriented schemes",
    "index funds",
    "life cycle funds",
    "other scheme",
    "overseas fund of funds",
    "solution oriented scheme",
    "solution oriented schemes",
)


def upgrade() -> None:
    connection = op.get_bind()
    classification_rows = connection.execute(
        sa.text(
            "SELECT id, display_name FROM scheme_classifications "
            "WHERE status = 'active' ORDER BY display_name, id"
        )
    ).all()
    mapped_ids = {
        str(row[0])
        for row in connection.execute(
            sa.text("SELECT classification_id FROM screener_classification_alias_members")
        ).all()
    }
    occupied_names = {
        (str(structure_type), str(normalized_name))
        for structure_type, normalized_name in connection.execute(
            sa.text("SELECT structure_type, normalized_name FROM screener_classification_aliases")
        ).all()
    }
    simple_name_counts = Counter(
        (_structure(str(display_name)), _normalize(_simple_name(str(display_name))))
        for _, display_name in classification_rows
    )
    now = datetime.now(UTC)

    for classification_id_value, display_name_value in classification_rows:
        classification_id = str(classification_id_value)
        display_name = str(display_name_value)
        if classification_id in mapped_ids:
            continue
        structure_type = _structure(display_name)
        name = _unique_name(
            classification_id=classification_id,
            display_name=display_name,
            structure_type=structure_type,
            occupied_names=occupied_names,
            has_canonical_collision=(
                simple_name_counts[(structure_type, _normalize(_simple_name(display_name)))] > 1
            ),
        )
        alias_id = _singleton_alias_id(classification_id)
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
                "normalized_name": _normalize(name),
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
            {"classification_id": classification_id, "alias_id": alias_id, "added_at": now},
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
                "normalized_name": _normalize(name),
                "structure_type": structure_type,
                "member_ids": json.dumps([classification_id]),
                "reason": _REASON,
                "created_at": now,
            },
        )
        occupied_names.add((structure_type, _normalize(name)))


def downgrade() -> None:
    connection = op.get_bind()
    singleton_rows = connection.execute(
        sa.text(
            "SELECT a.id, a.version, a.status, COUNT(DISTINCT m.classification_id), "
            "COUNT(DISTINCT r.id) "
            "FROM screener_classification_aliases a "
            "LEFT JOIN screener_classification_alias_members m ON m.alias_id = a.id "
            "LEFT JOIN screener_classification_alias_revisions r ON r.alias_id = a.id "
            "WHERE a.id LIKE 'screener-singleton-%' "
            "GROUP BY a.id, a.version, a.status"
        )
    ).all()
    changed = [
        str(alias_id)
        for alias_id, version, status, member_count, revision_count in singleton_rows
        if int(version) != 1
        or str(status) != "active"
        or int(member_count) != 1
        or int(revision_count) != 1
    ]
    if changed:
        raise RuntimeError(
            "refusing to remove edited singleton classification aliases during downgrade: "
            f"{sorted(changed)}"
        )
    connection.execute(
        sa.text(
            "DELETE FROM screener_classification_alias_revisions "
            "WHERE alias_id LIKE 'screener-singleton-%' AND change_reason = :reason"
        ),
        {"reason": _REASON},
    )
    connection.execute(
        sa.text(
            "DELETE FROM screener_classification_alias_members "
            "WHERE alias_id LIKE 'screener-singleton-%'"
        )
    )
    connection.execute(
        sa.text("DELETE FROM screener_classification_aliases WHERE id LIKE 'screener-singleton-%'")
    )


def _singleton_alias_id(classification_id: str) -> str:
    return f"screener-singleton-{classification_id.removeprefix('amfi-')}"


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _structure(value: str) -> str:
    normalized = " ".join(value.split()).casefold()
    if normalized.startswith("open ended schemes"):
        return "open_ended"
    if normalized.startswith("close ended schemes"):
        return "close_ended"
    if normalized.startswith("interval fund schemes"):
        return "interval"
    return "other"


def _simple_name(value: str) -> str:
    inner = _context_name(value)
    for prefix in _LEAF_PREFIXES:
        if not inner.casefold().startswith(prefix):
            continue
        remainder = re.sub(r"^.*?\)\s*-\s*", "", inner, count=1)
        if remainder == inner:
            remainder = re.sub(r"^[^-]+\s+-\s+", "", inner, count=1)
        if remainder != inner:
            inner = remainder
        break
    inner = re.sub(r"\s*\(\s*", " (", inner)
    inner = re.sub(r"\s*\)\s*", ")", inner)
    return " ".join(inner.split())


def _context_name(value: str) -> str:
    normalized = " ".join(value.split())
    wrapper = _STRUCTURE_WRAPPER.match(normalized)
    inner = wrapper.group(1) if wrapper else normalized
    return " ".join(inner.split())


def _unique_name(
    *,
    classification_id: str,
    display_name: str,
    structure_type: str,
    occupied_names: set[tuple[str, str]],
    has_canonical_collision: bool,
) -> str:
    base_name = _simple_name(display_name)
    context_name = _context_name(display_name)
    preferred_names = (
        (context_name, base_name) if has_canonical_collision else (base_name, context_name)
    )
    for preferred_name in preferred_names:
        candidate = preferred_name[:100].rstrip()
        if (structure_type, _normalize(candidate)) not in occupied_names:
            return candidate
    suffix = f" [{classification_id[-8:]}]"
    return f"{context_name[: 100 - len(suffix)].rstrip()}{suffix}"
