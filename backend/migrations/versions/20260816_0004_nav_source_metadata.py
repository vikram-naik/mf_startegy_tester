"""Separate source metadata observations from NAV value revisions.

Revision ID: 20260816_0004
Revises: 20260816_0003
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0004"
down_revision: str | None = "20260816_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("nav_revision_sources") as batch_op:
        batch_op.add_column(
            sa.Column("observed_metadata_version_id", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_nav_source_observed_metadata",
            "scheme_metadata_versions",
            ["observed_metadata_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.execute(
        sa.text(
            """
            UPDATE nav_revision_sources
            SET observed_metadata_version_id = (
                SELECT metadata_version_id
                FROM nav_revisions
                WHERE nav_revisions.id = nav_revision_sources.nav_revision_id
            )
            """
        )
    )
    with op.batch_alter_table("nav_revision_sources") as batch_op:
        batch_op.alter_column(
            "observed_metadata_version_id", existing_type=sa.String(length=64), nullable=False
        )

    # Version 0003 briefly treated source metadata text changes as NAV revisions.
    # Collapse only a current revision whose immediately preceding revision has
    # the exact same NAV and quality status. Genuine value changes remain intact.
    connection = op.get_bind()
    while True:
        duplicate = (
            connection.execute(
                sa.text(
                    """
                SELECT current.id AS current_id, previous.id AS previous_id
                FROM nav_revisions AS current
                JOIN nav_revisions AS previous
                  ON previous.amfi_scheme_code = current.amfi_scheme_code
                 AND previous.nav_date = current.nav_date
                 AND previous.revision_number = current.revision_number - 1
                WHERE current.is_current = 1
                  AND current.nav_value = previous.nav_value
                  AND current.quality_status = previous.quality_status
                LIMIT 1
                """
                )
            )
            .mappings()
            .first()
        )
        if duplicate is None:
            break
        current_id = duplicate["current_id"]
        previous_id = duplicate["previous_id"]
        sources = connection.execute(
            sa.text(
                """
                SELECT ingestion_batch_id, observed_metadata_version_id, observed_at
                FROM nav_revision_sources
                WHERE nav_revision_id = :current_id
                """
            ),
            {"current_id": current_id},
        ).mappings()
        for source in sources:
            exists = connection.execute(
                sa.text(
                    """
                    SELECT 1 FROM nav_revision_sources
                    WHERE nav_revision_id = :previous_id
                      AND ingestion_batch_id = :batch_id
                    """
                ),
                {"previous_id": previous_id, "batch_id": source["ingestion_batch_id"]},
            ).first()
            if exists is None:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO nav_revision_sources (
                            nav_revision_id, ingestion_batch_id,
                            observed_metadata_version_id, observed_at
                        ) VALUES (
                            :previous_id, :batch_id, :metadata_id, :observed_at
                        )
                        """
                    ),
                    {
                        "previous_id": previous_id,
                        "batch_id": source["ingestion_batch_id"],
                        "metadata_id": source["observed_metadata_version_id"],
                        "observed_at": source["observed_at"],
                    },
                )
        connection.execute(
            sa.text("DELETE FROM nav_revision_sources WHERE nav_revision_id = :current_id"),
            {"current_id": current_id},
        )
        connection.execute(
            sa.text("DELETE FROM nav_revisions WHERE id = :current_id"),
            {"current_id": current_id},
        )
        connection.execute(
            sa.text("UPDATE nav_revisions SET is_current = 1 WHERE id = :previous_id"),
            {"previous_id": previous_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("nav_revision_sources") as batch_op:
        batch_op.drop_constraint("fk_nav_source_observed_metadata", type_="foreignkey")
        batch_op.drop_column("observed_metadata_version_id")
