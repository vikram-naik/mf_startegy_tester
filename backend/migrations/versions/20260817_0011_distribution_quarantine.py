"""Add opt-in distribution record quarantine and issue tracking.

Revision ID: 20260817_0011
Revises: 20260817_0010
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0011"
down_revision: str | None = "20260817_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("distribution_sync_checkpoints") as batch_op:
        batch_op.add_column(sa.Column("rows_accepted", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rows_rejected", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE distribution_sync_checkpoints "
            "SET rows_accepted = rows_received, rows_rejected = 0"
        )
    )
    with op.batch_alter_table("distribution_sync_checkpoints") as batch_op:
        batch_op.alter_column("rows_accepted", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("rows_rejected", existing_type=sa.Integer(), nullable=False)
        batch_op.create_check_constraint(
            "ck_distribution_checkpoint_accepted", "rows_accepted >= 0"
        )
        batch_op.create_check_constraint(
            "ck_distribution_checkpoint_rejected", "rows_rejected >= 0"
        )
        batch_op.create_check_constraint(
            "ck_distribution_checkpoint_row_totals",
            "rows_received = rows_accepted + rows_rejected",
        )

    with op.batch_alter_table("distribution_sync_runs") as batch_op:
        batch_op.drop_constraint("ck_distribution_sync_status", type_="check")
        batch_op.add_column(sa.Column("record_error_policy", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("rows_rejected", sa.Integer(), nullable=True))
    op.execute(
        sa.text("UPDATE distribution_sync_runs SET record_error_policy = 'fail', rows_rejected = 0")
    )
    with op.batch_alter_table("distribution_sync_runs") as batch_op:
        batch_op.alter_column("record_error_policy", existing_type=sa.String(16), nullable=False)
        batch_op.alter_column("rows_rejected", existing_type=sa.Integer(), nullable=False)
        batch_op.create_check_constraint(
            "ck_distribution_sync_status",
            "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
        )
        batch_op.create_check_constraint(
            "ck_distribution_record_error_policy",
            "record_error_policy IN ('fail', 'quarantine')",
        )
        batch_op.create_check_constraint("ck_distribution_run_rows_rejected", "rows_rejected >= 0")

    op.create_table(
        "distribution_parse_issues",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(36), nullable=False),
        sa.Column("mutual_fund_id", sa.String(16), nullable=False),
        sa.Column("source_scheme_id", sa.String(16), nullable=False),
        sa.Column("record_number", sa.Integer(), nullable=False),
        sa.Column("issue_code", sa.String(64), nullable=False),
        sa.Column("error_details", sa.Text(), nullable=False),
        sa.Column("raw_record", sa.JSON(), nullable=False),
        sa.Column("source_record_signature", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_batch_id", sa.String(36), nullable=True),
        sa.CheckConstraint("record_number > 0", name="ck_distribution_issue_record_number"),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_distribution_issue_status"),
        sa.CheckConstraint(
            "((status = 'open' AND resolved_at IS NULL AND resolved_batch_id IS NULL) "
            "OR (status = 'resolved' "
            "AND resolved_at IS NOT NULL "
            "AND resolved_batch_id IS NOT NULL))",
            name="ck_distribution_issue_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["mutual_fund_id"], ["amfi_funds.mutual_fund_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ingestion_batch_id",
            "record_number",
            name="uq_distribution_issue_batch_record",
        ),
    )
    op.create_index(
        "ix_distribution_issue_family_status",
        "distribution_parse_issues",
        ["mutual_fund_id", "source_scheme_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_distribution_issue_signature_status",
        "distribution_parse_issues",
        ["source_record_signature", "status"],
        unique=False,
    )


def downgrade() -> None:
    issue_count = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM distribution_parse_issues"))
    if issue_count:
        raise RuntimeError("cannot downgrade while distribution parse issues exist")
    op.drop_index("ix_distribution_issue_signature_status", table_name="distribution_parse_issues")
    op.drop_index("ix_distribution_issue_family_status", table_name="distribution_parse_issues")
    op.drop_table("distribution_parse_issues")

    with op.batch_alter_table("distribution_sync_runs") as batch_op:
        batch_op.drop_constraint("ck_distribution_run_rows_rejected", type_="check")
        batch_op.drop_constraint("ck_distribution_record_error_policy", type_="check")
        batch_op.drop_constraint("ck_distribution_sync_status", type_="check")
        batch_op.drop_column("rows_rejected")
        batch_op.drop_column("record_error_policy")
        batch_op.create_check_constraint(
            "ck_distribution_sync_status",
            "status IN ('running', 'completed', 'failed')",
        )

    with op.batch_alter_table("distribution_sync_checkpoints") as batch_op:
        batch_op.drop_constraint("ck_distribution_checkpoint_row_totals", type_="check")
        batch_op.drop_constraint("ck_distribution_checkpoint_rejected", type_="check")
        batch_op.drop_constraint("ck_distribution_checkpoint_accepted", type_="check")
        batch_op.drop_column("rows_rejected")
        batch_op.drop_column("rows_accepted")
