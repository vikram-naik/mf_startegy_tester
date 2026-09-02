"""Quarantine distribution dates predating Indian mutual funds.

Revision ID: 20260831_0027
Revises: 20260825_0026
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0027"
down_revision: str | None = "20260825_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CUTOFF = "1964-01-01"
_OLD_RTA_CHECK = "issue_code IN ('unmapped_scheme', 'ambiguous_scheme', 'amount_conflict')"
_NEW_RTA_CHECK = (
    "issue_code IN ('unmapped_scheme', 'ambiguous_scheme', 'amount_conflict', "
    "'implausible_record_date')"
)
_OLD_ADVISORKHOJ_CHECK = "issue_code IN ('higher_priority_conflict', 'same_priority_conflict')"
_NEW_ADVISORKHOJ_CHECK = (
    "issue_code IN ('higher_priority_conflict', 'same_priority_conflict', "
    "'implausible_record_date')"
)


def upgrade() -> None:
    connection = op.get_bind()
    unsupported_links = connection.scalar(
        sa.text(
            """
            SELECT
              (SELECT COUNT(*)
               FROM distribution_event_revision_sources source
               JOIN distribution_event_revisions revision
                 ON revision.id = source.distribution_event_revision_id
               JOIN distribution_events event
                 ON event.id = revision.distribution_event_id
               WHERE event.record_date < :cutoff)
              +
              (SELECT COUNT(*)
               FROM distribution_event_revision_official_sources source
               JOIN distribution_event_revisions revision
                 ON revision.id = source.distribution_event_revision_id
               JOIN distribution_events event
                 ON event.id = revision.distribution_event_id
               WHERE event.record_date < :cutoff)
            """
        ),
        {"cutoff": _CUTOFF},
    )
    if unsupported_links:
        raise RuntimeError(
            "pre-1964 canonical distributions have AMFI/AMC provenance; "
            "refusing automatic quarantine"
        )

    with op.batch_alter_table("rta_distribution_issues") as batch_op:
        batch_op.drop_constraint("ck_rta_distribution_issue_code", type_="check")
        batch_op.create_check_constraint("ck_rta_distribution_issue_code", _NEW_RTA_CHECK)
    with op.batch_alter_table("advisorkhoj_distribution_issues") as batch_op:
        batch_op.drop_constraint("ck_ak_distribution_issue_code", type_="check")
        batch_op.create_check_constraint("ck_ak_distribution_issue_code", _NEW_ADVISORKHOJ_CHECK)

    rta_rows = connection.execute(
        sa.text(
            """
            SELECT source.rta_distribution_record_id, event.record_date
            FROM distribution_event_revision_rta_sources source
            JOIN distribution_event_revisions revision
              ON revision.id = source.distribution_event_revision_id
            JOIN distribution_events event
              ON event.id = revision.distribution_event_id
            WHERE event.record_date < :cutoff
            """
        ),
        {"cutoff": _CUTOFF},
    ).all()
    advisorkhoj_rows = connection.execute(
        sa.text(
            """
            SELECT
              source.advisorkhoj_distribution_record_id,
              source.mapping_review_id,
              event.record_date
            FROM distribution_event_revision_advisorkhoj_sources source
            JOIN distribution_event_revisions revision
              ON revision.id = source.distribution_event_revision_id
            JOIN distribution_events event
              ON event.id = revision.distribution_event_id
            WHERE event.record_date < :cutoff
            """
        ),
        {"cutoff": _CUTOFF},
    ).all()
    blocked_rows = len(rta_rows) + len(advisorkhoj_rows)
    if blocked_rows:
        _quarantine_existing_rows(connection, rta_rows, advisorkhoj_rows)
        _delete_pre_inception_canonical_rows(connection)

    with op.batch_alter_table("distribution_events") as batch_op:
        batch_op.create_check_constraint(
            "ck_distribution_event_record_date_plausible",
            "record_date >= '1964-01-01'",
        )


def _quarantine_existing_rows(
    connection: sa.Connection,
    rta_rows: Sequence[sa.Row[tuple[str, str]]],
    advisorkhoj_rows: Sequence[sa.Row[tuple[str, str, str]]],
) -> None:
    run_id = str(uuid4())
    observed_at = datetime.now(UTC)
    blocked_rows = len(rta_rows) + len(advisorkhoj_rows)
    connection.execute(
        sa.text(
            """
            INSERT INTO distribution_normalization_runs (
              id, status, normalization_version, source_rows_examined, candidate_rows,
              blocked_rows, events_inserted, revisions_inserted, rows_unchanged,
              started_at, completed_at, error_details
            ) VALUES (
              :id, 'completed', 'distribution-date-quarantine-2026.08.1', :blocked_rows,
              0, :blocked_rows, 0, 0, 0, :observed_at, :observed_at, NULL
            )
            """
        ),
        {"id": run_id, "blocked_rows": blocked_rows, "observed_at": observed_at},
    )
    for source_id, record_date in rta_rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO rta_distribution_issues (
                  id, rta_distribution_record_id, normalization_run_id, issue_code,
                  details, observed_at
                ) VALUES (
                  :id, :source_id, :run_id, 'implausible_record_date', :details,
                  :observed_at
                )
                """
            ),
            {
                "id": str(uuid4()),
                "source_id": source_id,
                "run_id": run_id,
                "details": (
                    "Literal RTA record date predates Indian mutual funds and was removed "
                    f"from derived canonical events: record_date={record_date}"
                ),
                "observed_at": observed_at,
            },
        )
    for source_id, mapping_review_id, record_date in advisorkhoj_rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO advisorkhoj_distribution_issues (
                  id, advisorkhoj_distribution_record_id, mapping_review_id,
                  normalization_run_id, issue_code, details, observed_at
                ) VALUES (
                  :id, :source_id, :mapping_review_id, :run_id,
                  'implausible_record_date', :details, :observed_at
                )
                """
            ),
            {
                "id": str(uuid4()),
                "source_id": source_id,
                "mapping_review_id": mapping_review_id,
                "run_id": run_id,
                "details": (
                    "Literal AdvisorKhoj record date predates Indian mutual funds and was "
                    f"removed from derived canonical events: record_date={record_date}"
                ),
                "observed_at": observed_at,
            },
        )


def _delete_pre_inception_canonical_rows(connection: sa.Connection) -> None:
    invalid_revision_ids = """
        SELECT revision.id
        FROM distribution_event_revisions revision
        JOIN distribution_events event ON event.id = revision.distribution_event_id
        WHERE event.record_date < :cutoff
    """
    for table in (
        "distribution_event_revision_sources",
        "distribution_event_revision_official_sources",
        "distribution_event_revision_rta_sources",
        "distribution_event_revision_advisorkhoj_sources",
    ):
        connection.execute(
            sa.text(
                f"DELETE FROM {table} "
                f"WHERE distribution_event_revision_id IN ({invalid_revision_ids})"
            ),
            {"cutoff": _CUTOFF},
        )
    connection.execute(
        sa.text(
            """
            DELETE FROM distribution_event_revisions
            WHERE distribution_event_id IN (
              SELECT id FROM distribution_events WHERE record_date < :cutoff
            )
            """
        ),
        {"cutoff": _CUTOFF},
    )
    connection.execute(
        sa.text("DELETE FROM distribution_events WHERE record_date < :cutoff"),
        {"cutoff": _CUTOFF},
    )


def downgrade() -> None:
    with op.batch_alter_table("distribution_events") as batch_op:
        batch_op.drop_constraint("ck_distribution_event_record_date_plausible", type_="check")
    op.execute("DELETE FROM rta_distribution_issues WHERE issue_code = 'implausible_record_date'")
    op.execute(
        "DELETE FROM advisorkhoj_distribution_issues WHERE issue_code = 'implausible_record_date'"
    )
    op.execute(
        "DELETE FROM distribution_normalization_runs "
        "WHERE normalization_version = 'distribution-date-quarantine-2026.08.1'"
    )
    with op.batch_alter_table("rta_distribution_issues") as batch_op:
        batch_op.drop_constraint("ck_rta_distribution_issue_code", type_="check")
        batch_op.create_check_constraint("ck_rta_distribution_issue_code", _OLD_RTA_CHECK)
    with op.batch_alter_table("advisorkhoj_distribution_issues") as batch_op:
        batch_op.drop_constraint("ck_ak_distribution_issue_code", type_="check")
        batch_op.create_check_constraint("ck_ak_distribution_issue_code", _OLD_ADVISORKHOJ_CHECK)
