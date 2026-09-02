"""Add immutable benchmark index and exchange ETF acquisition records.

Revision ID: 20260821_0021
Revises: 20260821_0020
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0021"
down_revision: str | None = "20260821_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_family", sa.String(length=24), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_start_date", sa.Date(), nullable=False),
        sa.Column("requested_end_date", sa.Date(), nullable=False),
        sa.Column("requests_total", sa.Integer(), nullable=False),
        sa.Column("requests_completed", sa.Integer(), nullable=False),
        sa.Column("requests_skipped", sa.Integer(), nullable=False),
        sa.Column("requests_failed", sa.Integer(), nullable=False),
        sa.Column("rows_received", sa.Integer(), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), nullable=False),
        sa.Column("rows_unchanged", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "source_family IN ('nifty_indices', 'exchange_etf')",
            name="ck_benchmark_sync_source_family",
        ),
        sa.CheckConstraint("mode IN ('full', 'refresh')", name="ck_benchmark_sync_mode"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
            name="ck_benchmark_sync_status",
        ),
        sa.CheckConstraint("requests_total >= 0", name="ck_benchmark_sync_requests_total"),
        sa.CheckConstraint("requests_completed >= 0", name="ck_benchmark_sync_requests_completed"),
        sa.CheckConstraint("requests_skipped >= 0", name="ck_benchmark_sync_requests_skipped"),
        sa.CheckConstraint("requests_failed >= 0", name="ck_benchmark_sync_requests_failed"),
        sa.CheckConstraint("rows_received >= 0", name="ck_benchmark_sync_rows_received"),
        sa.CheckConstraint("rows_inserted >= 0", name="ck_benchmark_sync_rows_inserted"),
        sa.CheckConstraint("rows_unchanged >= 0", name="ck_benchmark_sync_rows_unchanged"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_benchmark_sync_runs_status", "benchmark_sync_runs", ["status"], unique=False
    )
    op.create_table(
        "benchmark_instruments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("instrument_type", sa.String(length=32), nullable=False),
        sa.Column("source_identifier", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("benchmark_family", sa.String(length=160), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("source_symbol", sa.String(length=32), nullable=True),
        sa.Column("source_series", sa.String(length=8), nullable=True),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "instrument_type IN ('price_index', 'gross_total_return_index', "
            "'net_total_return_index', 'etf')",
            name="ck_benchmark_instrument_type",
        ),
        sa.CheckConstraint(
            "provider IN ('nifty_indices', 'nse', 'bse')",
            name="ck_benchmark_instrument_provider",
        ),
        sa.CheckConstraint("currency = 'INR'", name="ck_benchmark_instrument_currency"),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "instrument_type",
            "source_identifier",
            name="uq_benchmark_instrument_source_identity",
        ),
    )
    op.create_index("ix_benchmark_instrument_isin", "benchmark_instruments", ["isin"], unique=False)
    op.create_table(
        "benchmark_exchange_listings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("benchmark_instrument_id", sa.String(length=36), nullable=False),
        sa.Column("exchange", sa.String(length=3), nullable=False),
        sa.Column("source_security_id", sa.String(length=32), nullable=False),
        sa.Column("source_symbol", sa.String(length=32), nullable=False),
        sa.Column("source_series", sa.String(length=8), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("last_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("exchange IN ('NSE', 'BSE')", name="ck_benchmark_listing_exchange"),
        sa.ForeignKeyConstraint(
            ["benchmark_instrument_id"], ["benchmark_instruments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["last_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exchange", "source_security_id", name="uq_benchmark_listing_exchange_security"
        ),
    )
    op.create_index(
        "ix_benchmark_listing_instrument_exchange",
        "benchmark_exchange_listings",
        ["benchmark_instrument_id", "exchange"],
        unique=False,
    )
    op.create_table(
        "benchmark_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("benchmark_instrument_id", sa.String(length=36), nullable=False),
        sa.Column("exchange", sa.String(length=3), nullable=True),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("open_value", sa.Text(), nullable=True),
        sa.Column("high_value", sa.Text(), nullable=True),
        sa.Column("low_value", sa.Text(), nullable=True),
        sa.Column("close_value", sa.Text(), nullable=False),
        sa.Column("last_value", sa.Text(), nullable=True),
        sa.Column("previous_close", sa.Text(), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("traded_value", sa.Text(), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("identity_status", sa.String(length=32), nullable=False),
        sa.Column("content_signature", sa.String(length=64), nullable=False),
        sa.Column("first_observed_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "CAST(close_value AS NUMERIC) > 0",
            name="ck_benchmark_observation_positive_close",
        ),
        sa.CheckConstraint(
            "open_value IS NULL OR CAST(open_value AS NUMERIC) > 0",
            name="ck_benchmark_positive_open",
        ),
        sa.CheckConstraint(
            "high_value IS NULL OR CAST(high_value AS NUMERIC) > 0",
            name="ck_benchmark_positive_high",
        ),
        sa.CheckConstraint(
            "low_value IS NULL OR CAST(low_value AS NUMERIC) > 0",
            name="ck_benchmark_positive_low",
        ),
        sa.CheckConstraint(
            "previous_close IS NULL OR CAST(previous_close AS NUMERIC) >= 0",
            name="ck_benchmark_nonnegative_previous_close",
        ),
        sa.CheckConstraint(
            "last_value IS NULL OR CAST(last_value AS NUMERIC) >= 0",
            name="ck_benchmark_nonnegative_last",
        ),
        sa.CheckConstraint("volume IS NULL OR volume >= 0", name="ck_benchmark_nonnegative_volume"),
        sa.CheckConstraint(
            "traded_value IS NULL OR CAST(traded_value AS NUMERIC) >= 0",
            name="ck_benchmark_nonnegative_traded_value",
        ),
        sa.CheckConstraint("trade_count IS NULL OR trade_count >= 0", name="ck_benchmark_trades"),
        sa.CheckConstraint(
            "identity_status IN ('official', 'provisional_current_mapping')",
            name="ck_benchmark_identity_status",
        ),
        sa.CheckConstraint("length(content_signature) = 64", name="ck_benchmark_obs_signature"),
        sa.ForeignKeyConstraint(
            ["benchmark_instrument_id"], ["benchmark_instruments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_observed_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_signature", name="uq_benchmark_observation_signature"),
    )
    op.create_index(
        "ix_benchmark_observation_instrument_date",
        "benchmark_observations",
        ["benchmark_instrument_id", "exchange", "observation_date"],
        unique=False,
    )
    op.create_table(
        "benchmark_observation_sources",
        sa.Column("benchmark_observation_id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["benchmark_observation_id"], ["benchmark_observations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("benchmark_observation_id", "ingestion_batch_id"),
    )
    op.create_table(
        "benchmark_sync_checkpoints",
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("source_identifier", sa.String(length=160), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("last_batch_id", sa.String(length=36), nullable=False),
        sa.Column("last_sync_run_id", sa.String(length=36), nullable=False),
        sa.Column("rows_received", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["last_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["last_sync_run_id"], ["benchmark_sync_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("provider", "source_identifier", "period_start"),
    )
    op.create_table(
        "benchmark_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sync_run_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("source_identifier", sa.String(length=160), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=True),
        sa.Column("ingestion_batch_id", sa.String(length=36), nullable=True),
        sa.Column("issue_code", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
            "'identity_unresolved', "
            "'observation_revision', 'provisional_bse_identity')",
            name="ck_benchmark_issue_code",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name="ck_benchmark_issue_severity"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["sync_run_id"], ["benchmark_sync_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_benchmark_issue_run_code",
        "benchmark_issues",
        ["sync_run_id", "issue_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_benchmark_issue_run_code", table_name="benchmark_issues")
    op.drop_table("benchmark_issues")
    op.drop_table("benchmark_sync_checkpoints")
    op.drop_table("benchmark_observation_sources")
    op.drop_index("ix_benchmark_observation_instrument_date", table_name="benchmark_observations")
    op.drop_table("benchmark_observations")
    op.drop_index(
        "ix_benchmark_listing_instrument_exchange", table_name="benchmark_exchange_listings"
    )
    op.drop_table("benchmark_exchange_listings")
    op.drop_index("ix_benchmark_instrument_isin", table_name="benchmark_instruments")
    op.drop_table("benchmark_instruments")
    op.drop_index("ix_benchmark_sync_runs_status", table_name="benchmark_sync_runs")
    op.drop_table("benchmark_sync_runs")
