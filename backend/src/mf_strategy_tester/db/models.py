from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from mf_strategy_tester.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Preserve UTC semantics when SQLite returns a naive datetime value."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("audit timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class DecimalText(TypeDecorator[Decimal]):
    """Lossless decimal storage for SQLite financial values."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        del dialect
        return None if value is None else str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        del dialect
        return None if value is None else Decimal(value)


class StrategyRecord(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
    versions: Mapped[list[StrategyVersionRecord]] = relationship(
        back_populates="strategy",
        cascade="save-update, merge",
        order_by="StrategyVersionRecord.version",
    )


class StrategyVersionRecord(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_strategy_versions_positive_version"),
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    strategy: Mapped[StrategyRecord] = relationship(back_populates="versions")


class SourceArtifactRecord(Base):
    __tablename__ = "source_artifacts"
    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_source_artifacts_byte_size_nonnegative"),
        CheckConstraint("length(sha256) = 64", name="ck_source_artifacts_sha256_length"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    first_retrieved_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )


class IngestionBatchRecord(Base):
    __tablename__ = "ingestion_batches"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_batch_status"),
        CheckConstraint("rows_received >= 0", name="ck_batch_rows_received_nonnegative"),
        CheckConstraint("rows_accepted >= 0", name="ck_batch_rows_accepted_nonnegative"),
        CheckConstraint("rows_rejected >= 0", name="ck_batch_rows_rejected_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    artifact_reused: Mapped[bool] = mapped_column(nullable=False, default=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact: Mapped[SourceArtifactRecord | None] = relationship()


class AmfiFundRecord(Base):
    __tablename__ = "amfi_funds"

    mutual_fund_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    mutual_fund_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    catalog_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SchemeOptionRecord(Base):
    __tablename__ = "scheme_options"

    amfi_scheme_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_observed_nav_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_observed_nav_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SchemeMetadataVersionRecord(Base):
    __tablename__ = "scheme_metadata_versions"
    __table_args__ = (
        CheckConstraint("plan_type IN ('direct', 'regular', 'unknown')", name="ck_metadata_plan"),
        CheckConstraint(
            "option_type IN ('growth', 'idcw', 'bonus', 'unknown')", name="ck_metadata_option"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    amfi_scheme_code: Mapped[str] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    fund_house_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scheme_classification: Mapped[str] = mapped_column(Text, nullable=False)
    isin_payout_or_growth: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isin_reinvestment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_type: Mapped[str] = mapped_column(String(16), nullable=False)
    option_type: Mapped[str] = mapped_column(String(16), nullable=False)
    classification_method: Mapped[str] = mapped_column(String(64), nullable=False)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class NavRevisionRecord(Base):
    __tablename__ = "nav_revisions"
    __table_args__ = (
        CheckConstraint("revision_number > 0", name="ck_nav_revision_number_positive"),
        CheckConstraint("quality_status IN ('valid', 'error')", name="ck_nav_quality_status"),
        UniqueConstraint(
            "amfi_scheme_code",
            "nav_date",
            "revision_number",
            name="uq_nav_revision_number",
        ),
        Index(
            "uq_nav_current_scheme_date",
            "amfi_scheme_code",
            "nav_date",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
        Index("ix_nav_current_date", "nav_date", sqlite_where=text("is_current = 1")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    amfi_scheme_code: Mapped[str] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), nullable=False
    )
    nav_date: Mapped[date] = mapped_column(Date, nullable=False)
    nav_value: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    metadata_version_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_metadata_versions.id", ondelete="RESTRICT"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_current: Mapped[bool] = mapped_column(nullable=False)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class NavRevisionSourceRecord(Base):
    __tablename__ = "nav_revision_sources"

    nav_revision_id: Mapped[str] = mapped_column(
        ForeignKey("nav_revisions.id", ondelete="RESTRICT"), primary_key=True
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_metadata_version_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_metadata_versions.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DataQualityIssueRecord(Base):
    __tablename__ = "data_quality_issues"
    __table_args__ = (
        CheckConstraint("severity IN ('error', 'warning', 'info')", name="ck_dq_severity"),
        UniqueConstraint(
            "ingestion_batch_id",
            "issue_code",
            "amfi_scheme_code",
            "nav_date",
            name="uq_dq_batch_issue_scheme_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    amfi_scheme_code: Mapped[str] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), nullable=False
    )
    nav_date: Mapped[date] = mapped_column(Date, nullable=False)
    metadata_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("scheme_metadata_versions.id", ondelete="RESTRICT"), nullable=True
    )
    details: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class NavSyncCheckpointRecord(Base):
    __tablename__ = "nav_sync_checkpoints"

    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), primary_key=True
    )
    completed_through: Mapped[date] = mapped_column(Date, nullable=False)
    latest_nav_date_found: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class NavSyncCoverageRecord(Base):
    __tablename__ = "nav_sync_coverage"
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="ck_nav_sync_coverage_dates"),
        Index("ix_nav_sync_coverage_fund_dates", "mutual_fund_id", "start_date", "end_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class NavDatasetStatsRecord(Base):
    __tablename__ = "nav_dataset_stats"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_nav_dataset_stats_singleton"),
        CheckConstraint("scheme_options >= 0", name="ck_nav_stats_schemes_nonnegative"),
        CheckConstraint("valid_current_rows >= 0", name="ck_nav_stats_valid_nonnegative"),
        CheckConstraint("error_current_rows >= 0", name="ck_nav_stats_error_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheme_options: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_current_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    error_current_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    earliest_valid_nav_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_valid_nav_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class NavSyncRunRecord(Base):
    __tablename__ = "nav_sync_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('full', 'incremental')", name="ck_nav_sync_mode"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_nav_sync_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    requested_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    overlap_days: Mapped[int] = mapped_column(Integer, nullable=False)
    funds_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_revised: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_quarantined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_nav_date_found: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
