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


class SchemeClassificationRecord(Base):
    """Canonical research classification; AMFI source text remains in metadata versions."""

    __tablename__ = "scheme_classifications"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive')", name="ck_scheme_classification_status"),
        UniqueConstraint("normalized_key", name="uq_scheme_classification_normalized_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SchemeClassificationAliasRecord(Base):
    """Approved mapping from immutable source classification text to a canonical ID."""

    __tablename__ = "scheme_classification_aliases"
    __table_args__ = (
        CheckConstraint("source_provider = 'amfi'", name="ck_classification_alias_provider"),
        CheckConstraint(
            "match_type IN ('exact', 'formatting', 'terminology', 'manual')",
            name="ck_classification_alias_match_type",
        ),
        Index("ix_classification_alias_canonical", "classification_id"),
    )

    source_provider: Mapped[str] = mapped_column(String(16), primary_key=True, default="amfi")
    raw_classification: Mapped[str] = mapped_column(Text, primary_key=True)
    classification_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_classifications.id", ondelete="RESTRICT"), nullable=False
    )
    match_type: Mapped[str] = mapped_column(String(16), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ScreenerClassificationAliasRecord(Base):
    """Locally managed, short classification shown by the research UI."""

    __tablename__ = "screener_classification_aliases"
    __table_args__ = (
        CheckConstraint(
            "structure_type IN ('open_ended', 'close_ended', 'interval', 'other')",
            name="ck_screener_classification_alias_structure",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_screener_alias_status"),
        CheckConstraint("version > 0", name="ck_screener_alias_positive_version"),
        UniqueConstraint(
            "structure_type", "normalized_name", name="uq_screener_alias_structure_name"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False)
    structure_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ScreenerClassificationAliasMemberRecord(Base):
    """Map one canonical AMFI classification to at most one screener alias."""

    __tablename__ = "screener_classification_alias_members"
    __table_args__ = (Index("ix_screener_alias_member_alias", "alias_id"),)

    classification_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_classifications.id", ondelete="RESTRICT"), primary_key=True
    )
    alias_id: Mapped[str] = mapped_column(
        ForeignKey("screener_classification_aliases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ScreenerClassificationAliasRevisionRecord(Base):
    """Immutable audit snapshot for a local classification-alias change."""

    __tablename__ = "screener_classification_alias_revisions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_screener_alias_revision_positive_version"),
        CheckConstraint(
            "structure_type IN ('open_ended', 'close_ended', 'interval', 'other')",
            name="ck_screener_alias_revision_structure",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_screener_alias_revision_status"
        ),
        UniqueConstraint("alias_id", "version", name="uq_screener_alias_revision_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    alias_id: Mapped[str] = mapped_column(
        ForeignKey("screener_classification_aliases.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False)
    structure_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    member_classification_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


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


class AmfiDistributionRecord(Base):
    """Immutable, queryable row from an AMFI distribution snapshot."""

    __tablename__ = "amfi_distribution_records"
    __table_args__ = (
        CheckConstraint(
            "source_unit IN ('percentage', 'amount', 'ratio')",
            name="ck_distribution_source_unit",
        ),
        CheckConstraint(
            "((source_unit IN ('percentage', 'amount') "
            "AND source_value IS NOT NULL "
            "AND ratio_numerator IS NULL "
            "AND ratio_denominator IS NULL) "
            "OR (source_unit = 'ratio' "
            "AND source_value IS NULL "
            "AND ratio_numerator > 0 "
            "AND ratio_denominator > 0))",
            name="ck_distribution_value_shape",
        ),
        CheckConstraint(
            "(annotated_amount_per_unit_inr IS NULL "
            "OR (source_unit = 'percentage' "
            "AND CAST(annotated_amount_per_unit_inr AS NUMERIC) > 0))",
            name="ck_distribution_annotated_amount",
        ),
        Index("ix_distribution_option_record_date", "source_option_id", "record_date"),
        Index(
            "ix_distribution_family_record_date",
            "mutual_fund_id",
            "source_scheme_id",
            "record_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    source_scheme_id: Mapped[str] = mapped_column(String(16), nullable=False)
    source_option_id: Mapped[str] = mapped_column(String(16), nullable=False)
    scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    nav_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_option: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_source_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_value: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    ratio_numerator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ratio_denominator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annotated_amount_per_unit_inr: Mapped[Decimal | None] = mapped_column(
        DecimalText(), nullable=True
    )
    source_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AmfiDistributionRecordSource(Base):
    __tablename__ = "amfi_distribution_record_sources"

    distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_distribution_records.id", ondelete="RESTRICT"), primary_key=True
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class OfficialDistributionRecord(Base):
    """Immutable, exact-code distribution row extracted from an official AMC notice."""

    __tablename__ = "official_distribution_records"
    __table_args__ = (
        CheckConstraint("provider <> ''", name="ck_official_distribution_provider"),
        CheckConstraint(
            "source_plan_type IN ('regular', 'direct')",
            name="ck_official_distribution_plan_type",
        ),
        CheckConstraint(
            "source_unit = 'inr_per_unit'",
            name="ck_official_distribution_source_unit",
        ),
        CheckConstraint(
            "CAST(amount_per_unit_inr AS NUMERIC) > 0",
            name="ck_official_distribution_amount_positive",
        ),
        CheckConstraint(
            "length(content_signature) = 64",
            name="ck_official_distribution_signature_length",
        ),
        Index(
            "ix_official_distribution_option_date",
            "amfi_scheme_code",
            "record_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amfi_scheme_code: Mapped[str] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), nullable=False
    )
    source_scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_plan_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_option_label: Mapped[str] = mapped_column(Text, nullable=False)
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_amount_per_unit_inr: Mapped[str] = mapped_column(Text, nullable=False)
    amount_per_unit_inr: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    source_unit: Mapped[str] = mapped_column(String(24), nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    first_notice_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    first_identity_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class OfficialDistributionRecordSource(Base):
    """Every captured notice/identity-artifact pair observing an official row."""

    __tablename__ = "official_distribution_record_sources"

    distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("official_distribution_records.id", ondelete="RESTRICT"), primary_key=True
    )
    notice_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    identity_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionIdentifierReviewRecord(Base):
    """Append-only evidence review for an AMFI distribution option identifier."""

    __tablename__ = "distribution_identifier_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('source_only', 'mapped', 'source_error')",
            name="ck_distribution_review_status",
        ),
        CheckConstraint(
            "((status = 'mapped' AND matched_amfi_scheme_code IS NOT NULL) "
            "OR (status IN ('source_only', 'source_error') "
            "AND matched_amfi_scheme_code IS NULL))",
            name="ck_distribution_review_mapping_shape",
        ),
        CheckConstraint(
            "length(review_signature) = 64",
            name="ck_distribution_review_signature_length",
        ),
        Index("ix_distribution_review_option_time", "source_option_id", "reviewed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_option_id: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    matched_amfi_scheme_code: Mapped[str | None] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), nullable=True
    )
    evidence_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_details: Mapped[str] = mapped_column(Text, nullable=False)
    review_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionNormalizationRunRecord(Base):
    """Audited execution of the source-to-canonical distribution gate."""

    __tablename__ = "distribution_normalization_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_distribution_normalization_run_status",
        ),
        CheckConstraint("source_rows_examined >= 0", name="ck_distribution_norm_rows_examined"),
        CheckConstraint("candidate_rows >= 0", name="ck_distribution_norm_candidate_rows"),
        CheckConstraint("blocked_rows >= 0", name="ck_distribution_norm_blocked_rows"),
        CheckConstraint("events_inserted >= 0", name="ck_distribution_norm_events_inserted"),
        CheckConstraint("revisions_inserted >= 0", name="ck_distribution_norm_revisions_inserted"),
        CheckConstraint("rows_unchanged >= 0", name="ck_distribution_norm_rows_unchanged"),
        CheckConstraint(
            "source_rows_examined = candidate_rows + blocked_rows",
            name="ck_distribution_norm_source_totals",
        ),
        CheckConstraint(
            "candidate_rows = revisions_inserted + rows_unchanged",
            name="ck_distribution_norm_candidate_totals",
        ),
        CheckConstraint(
            "events_inserted <= revisions_inserted",
            name="ck_distribution_norm_event_revision_totals",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_rows_examined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revisions_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)


class DistributionEventRecord(Base):
    """Stable logical identity of a canonical mutual-fund distribution event."""

    __tablename__ = "distribution_events"
    __table_args__ = (
        CheckConstraint("event_type = 'idcw_cash'", name="ck_distribution_event_type"),
        CheckConstraint(
            "record_date >= '1964-01-01'",
            name="ck_distribution_event_record_date_plausible",
        ),
        UniqueConstraint(
            "amfi_scheme_code",
            "record_date",
            "event_type",
            name="uq_distribution_event_identity",
        ),
        Index("ix_distribution_event_option_date", "amfi_scheme_code", "record_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    amfi_scheme_code: Mapped[str] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), nullable=False
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionEventRevisionRecord(Base):
    """Immutable amount revision for a canonical distribution event."""

    __tablename__ = "distribution_event_revisions"
    __table_args__ = (
        CheckConstraint(
            "CAST(amount_per_unit_inr AS NUMERIC) > 0",
            name="ck_distribution_event_amount_positive",
        ),
        CheckConstraint(
            "revision_number > 0", name="ck_distribution_event_revision_number_positive"
        ),
        CheckConstraint(
            "length(content_signature) = 64",
            name="ck_distribution_event_revision_signature_length",
        ),
        UniqueConstraint(
            "distribution_event_id",
            "revision_number",
            name="uq_distribution_event_revision_number",
        ),
        Index(
            "uq_distribution_event_current_revision",
            "distribution_event_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    distribution_event_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_events.id", ondelete="RESTRICT"), nullable=False
    )
    amount_per_unit_inr: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_run_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_normalization_runs.id", ondelete="RESTRICT"), nullable=False
    )
    is_current: Mapped[bool] = mapped_column(nullable=False)
    normalized_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionEventRevisionSourceRecord(Base):
    """Exact immutable AMFI source row used by a canonical event revision."""

    __tablename__ = "distribution_event_revision_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_distribution_record_id",
            name="uq_distribution_event_source_record",
        ),
    )

    distribution_event_revision_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_event_revisions.id", ondelete="RESTRICT"), primary_key=True
    )
    source_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_distribution_records.id", ondelete="RESTRICT"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionEventRevisionOfficialSourceRecord(Base):
    """Exact immutable official-AMC notice row supporting a canonical revision."""

    __tablename__ = "distribution_event_revision_official_sources"
    __table_args__ = (
        UniqueConstraint(
            "official_distribution_record_id",
            name="uq_distribution_event_official_source_record",
        ),
    )

    distribution_event_revision_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_event_revisions.id", ondelete="RESTRICT"), primary_key=True
    )
    official_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("official_distribution_records.id", ondelete="RESTRICT"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class RtaSchemeCaptureRecord(Base):
    """Immutable option-level observation captured from an official RTA website."""

    __tablename__ = "rta_scheme_captures"
    __table_args__ = (
        CheckConstraint("provider IN ('cams', 'kfintech')", name="ck_rta_capture_provider"),
        CheckConstraint(
            "plan_type IN ('direct', 'regular', 'unknown')", name="ck_rta_capture_plan_type"
        ),
        CheckConstraint(
            "option_variant IN ('payout', 'reinvestment', 'unknown')",
            name="ck_rta_capture_option_variant",
        ),
        CheckConstraint("source_row_count >= 0", name="ck_rta_capture_source_rows"),
        CheckConstraint(
            "((latest_nav_date IS NULL AND latest_nav_value IS NULL) OR "
            "(latest_nav_date IS NOT NULL AND latest_nav_value IS NOT NULL "
            "AND CAST(latest_nav_value AS NUMERIC) >= 0))",
            name="ck_rta_capture_latest_nav_shape",
        ),
        CheckConstraint("length(capture_signature) = 64", name="ck_rta_capture_signature_length"),
        CheckConstraint(
            "length(source_payload_sha256) = 64",
            name="ck_rta_capture_source_payload_sha256_length",
        ),
        Index(
            "ix_rta_capture_identifier",
            "provider",
            "rta_fund_code",
            "rta_scheme_code",
            "captured_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rta_fund_code: Mapped[str] = mapped_column(String(32), nullable=False)
    rta_fund_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rta_scheme_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_type: Mapped[str] = mapped_column(String(16), nullable=False)
    option_variant: Mapped[str] = mapped_column(String(16), nullable=False)
    latest_nav_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_nav_value: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    capture_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class RtaSchemeMappingReviewRecord(Base):
    """Append-only AMFI identity conclusion for one immutable RTA capture."""

    __tablename__ = "rta_scheme_mapping_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('mapped', 'unresolved', 'ambiguous')", name="ck_rta_mapping_status"
        ),
        CheckConstraint(
            "((status = 'mapped' AND amfi_scheme_code IS NOT NULL) OR "
            "(status IN ('unresolved', 'ambiguous') AND amfi_scheme_code IS NULL))",
            name="ck_rta_mapping_shape",
        ),
        CheckConstraint(
            "mapping_method IN ('exact_name_plan_nav', 'nav_fingerprint', 'manual', 'none')",
            name="ck_rta_mapping_method",
        ),
        CheckConstraint("length(review_signature) = 64", name="ck_rta_mapping_signature_length"),
        Index("ix_rta_mapping_capture_time", "scheme_capture_id", "reviewed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scheme_capture_id: Mapped[str] = mapped_column(
        ForeignKey("rta_scheme_captures.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    amfi_scheme_code: Mapped[str | None] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), nullable=True
    )
    mapping_method: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_details: Mapped[str] = mapped_column(Text, nullable=False)
    review_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class RtaDistributionRecord(Base):
    """Immutable distribution row rendered by an official CAMS or KFintech source."""

    __tablename__ = "rta_distribution_records"
    __table_args__ = (
        CheckConstraint(
            "CAST(individual_amount_per_unit_inr AS NUMERIC) > 0",
            name="ck_rta_distribution_individual_amount",
        ),
        CheckConstraint(
            "non_individual_amount_per_unit_inr IS NULL OR "
            "CAST(non_individual_amount_per_unit_inr AS NUMERIC) >= 0",
            name="ck_rta_distribution_non_individual_amount",
        ),
        CheckConstraint("source_unit = 'inr_per_unit'", name="ck_rta_distribution_source_unit"),
        CheckConstraint(
            "length(content_signature) = 64", name="ck_rta_distribution_signature_length"
        ),
        Index("ix_rta_distribution_capture_date", "scheme_capture_id", "record_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scheme_capture_id: Mapped[str] = mapped_column(
        ForeignKey("rta_scheme_captures.id", ondelete="RESTRICT"), nullable=False
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_individual_amount: Mapped[str] = mapped_column(Text, nullable=False)
    individual_amount_per_unit_inr: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    raw_non_individual_amount: Mapped[str | None] = mapped_column(Text, nullable=True)
    non_individual_amount_per_unit_inr: Mapped[Decimal | None] = mapped_column(
        DecimalText(), nullable=True
    )
    ex_nav: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    cum_nav: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    source_unit: Mapped[str] = mapped_column(String(24), nullable=False)
    source_terminology: Mapped[str] = mapped_column(String(64), nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class RtaDistributionRecordSource(Base):
    __tablename__ = "rta_distribution_record_sources"

    rta_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("rta_distribution_records.id", ondelete="RESTRICT"), primary_key=True
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionEventRevisionRtaSourceRecord(Base):
    """Exact immutable RTA row supporting a canonical distribution revision."""

    __tablename__ = "distribution_event_revision_rta_sources"
    __table_args__ = (
        UniqueConstraint(
            "rta_distribution_record_id", name="uq_distribution_event_rta_source_record"
        ),
    )

    distribution_event_revision_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_event_revisions.id", ondelete="RESTRICT"), primary_key=True
    )
    rta_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("rta_distribution_records.id", ondelete="RESTRICT"), primary_key=True
    )
    mapping_review_id: Mapped[str] = mapped_column(
        ForeignKey("rta_scheme_mapping_reviews.id", ondelete="RESTRICT"), nullable=False
    )
    linked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionEventRevisionAdvisorkhojSourceRecord(Base):
    """Exact tertiary AdvisorKhoj row supporting a canonical distribution revision."""

    __tablename__ = "distribution_event_revision_advisorkhoj_sources"
    __table_args__ = (
        UniqueConstraint(
            "advisorkhoj_distribution_record_id",
            name="uq_distribution_event_advisorkhoj_source_record",
        ),
    )

    distribution_event_revision_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_event_revisions.id", ondelete="RESTRICT"), primary_key=True
    )
    advisorkhoj_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_distribution_records.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    mapping_review_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_scheme_mapping_reviews.id", ondelete="RESTRICT"),
        nullable=False,
    )
    linked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class RtaDistributionIssueRecord(Base):
    """Append-only publication issue for an immutable RTA distribution row."""

    __tablename__ = "rta_distribution_issues"
    __table_args__ = (
        CheckConstraint(
            "issue_code IN ('unmapped_scheme', 'ambiguous_scheme', 'amount_conflict', "
            "'implausible_record_date')",
            name="ck_rta_distribution_issue_code",
        ),
        UniqueConstraint(
            "rta_distribution_record_id",
            "normalization_run_id",
            "issue_code",
            name="uq_rta_distribution_issue_run",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rta_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("rta_distribution_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    normalization_run_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_normalization_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    issue_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AdvisorkhojCatalogSnapshotRecord(Base):
    """Immutable AdvisorKhoj AMC/category/scheme universe captured in one artifact."""

    __tablename__ = "advisorkhoj_catalog_snapshots"
    __table_args__ = (
        CheckConstraint("length(catalog_sha256) = 64", name="ck_ak_catalog_sha256_length"),
        CheckConstraint("amc_count > 0", name="ck_ak_catalog_amc_count"),
        CheckConstraint("category_query_count >= 0", name="ck_ak_catalog_category_count"),
        CheckConstraint("scheme_count >= 0", name="ck_ak_catalog_scheme_count"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    catalog_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    amc_count: Mapped[int] = mapped_column(Integer, nullable=False)
    category_query_count: Mapped[int] = mapped_column(Integer, nullable=False)
    scheme_count: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AdvisorkhojCatalogSchemeRecord(Base):
    """One exact scheme short name in an immutable AdvisorKhoj catalog snapshot."""

    __tablename__ = "advisorkhoj_catalog_schemes"
    __table_args__ = (
        UniqueConstraint(
            "catalog_snapshot_id",
            "amc_name",
            "scheme_name",
            name="uq_ak_catalog_scheme_identity",
        ),
        CheckConstraint("length(content_signature) = 64", name="ck_ak_catalog_scheme_signature"),
        Index("ix_ak_catalog_scheme_amc", "catalog_snapshot_id", "amc_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    catalog_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_catalog_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    amc_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class AdvisorkhojSchemeCaptureRecord(Base):
    """One immutable AdvisorKhoj scheme-history response tied to its catalog entry."""

    __tablename__ = "advisorkhoj_scheme_captures"
    __table_args__ = (
        CheckConstraint(
            "plan_type IN ('direct', 'regular', 'unknown')", name="ck_ak_capture_plan_type"
        ),
        CheckConstraint(
            "option_variant IN ('payout', 'reinvestment', 'mixed', 'unknown')",
            name="ck_ak_capture_option_variant",
        ),
        CheckConstraint("source_row_count >= 0", name="ck_ak_capture_source_rows"),
        CheckConstraint("length(source_payload_sha256) = 64", name="ck_ak_capture_payload_sha"),
        CheckConstraint("length(capture_signature) = 64", name="ck_ak_capture_signature"),
        Index("ix_ak_capture_catalog_scheme", "catalog_scheme_id", "captured_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    catalog_scheme_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_catalog_schemes.id", ondelete="RESTRICT"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_type: Mapped[str] = mapped_column(String(16), nullable=False)
    option_variant: Mapped[str] = mapped_column(String(16), nullable=False)
    source_frequency: Mapped[str] = mapped_column(String(24), nullable=False)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    capture_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AdvisorkhojSchemeMappingReviewRecord(Base):
    """Append-only AMFI identity conclusion for an AdvisorKhoj scheme capture."""

    __tablename__ = "advisorkhoj_scheme_mapping_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('mapped', 'unresolved', 'ambiguous')", name="ck_ak_mapping_status"
        ),
        CheckConstraint(
            "((status = 'mapped' AND amfi_scheme_code IS NOT NULL) OR "
            "(status IN ('unresolved', 'ambiguous') AND amfi_scheme_code IS NULL))",
            name="ck_ak_mapping_shape",
        ),
        CheckConstraint(
            "mapping_method IN ('nav_fingerprint', 'manual', 'none')",
            name="ck_ak_mapping_method",
        ),
        CheckConstraint("length(review_signature) = 64", name="ck_ak_mapping_signature"),
        Index("ix_ak_mapping_capture_time", "scheme_capture_id", "reviewed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scheme_capture_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_scheme_captures.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    amfi_scheme_code: Mapped[str | None] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), nullable=True
    )
    mapping_method: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_details: Mapped[str] = mapped_column(Text, nullable=False)
    review_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AdvisorkhojDistributionRecord(Base):
    """Immutable secondary-source distribution observation from AdvisorKhoj."""

    __tablename__ = "advisorkhoj_distribution_records"
    __table_args__ = (
        CheckConstraint(
            "CAST(reference_nav AS NUMERIC) >= 0", name="ck_ak_distribution_reference_nav"
        ),
        CheckConstraint(
            "quality_status IN ('valid', 'zero_amount', 'negative_amount', 'zero_reference_nav')",
            name="ck_ak_distribution_quality_status",
        ),
        CheckConstraint("length(content_signature) = 64", name="ck_ak_distribution_signature"),
        Index("ix_ak_distribution_capture_date", "scheme_capture_id", "record_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scheme_capture_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_scheme_captures.id", ondelete="RESTRICT"), nullable=False
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_amount_per_unit_inr: Mapped[str] = mapped_column(Text, nullable=False)
    amount_per_unit_inr: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    raw_reference_nav: Mapped[str] = mapped_column(Text, nullable=False)
    reference_nav: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    raw_yield_percent: Mapped[str] = mapped_column(Text, nullable=False)
    yield_percent: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    is_positive_cash_distribution: Mapped[bool] = mapped_column(nullable=False)
    quality_status: Mapped[str] = mapped_column(String(24), nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AdvisorkhojDistributionRecordSource(Base):
    __tablename__ = "advisorkhoj_distribution_record_sources"

    advisorkhoj_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_distribution_records.id", ondelete="RESTRICT"), primary_key=True
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AdvisorkhojDistributionIssueRecord(Base):
    """Append-only reason a mapped tertiary row was not canonically published."""

    __tablename__ = "advisorkhoj_distribution_issues"
    __table_args__ = (
        CheckConstraint(
            "issue_code IN ('higher_priority_conflict', 'same_priority_conflict', "
            "'implausible_record_date')",
            name="ck_ak_distribution_issue_code",
        ),
        UniqueConstraint(
            "advisorkhoj_distribution_record_id",
            "mapping_review_id",
            "issue_code",
            name="uq_ak_distribution_issue_review",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    advisorkhoj_distribution_record_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_distribution_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mapping_review_id: Mapped[str] = mapped_column(
        ForeignKey("advisorkhoj_scheme_mapping_reviews.id", ondelete="RESTRICT"),
        nullable=False,
    )
    normalization_run_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_normalization_runs.id", ondelete="RESTRICT"), nullable=False
    )
    issue_code: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionCoverageRunRecord(Base):
    """Immutable summary of one option-level distribution coverage assessment."""

    __tablename__ = "distribution_coverage_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_distribution_coverage_run_status",
        ),
        CheckConstraint("options_examined >= 0", name="ck_distribution_coverage_options"),
        CheckConstraint("events_present_options >= 0", name="ck_distribution_coverage_events"),
        CheckConstraint("blocked_source_options >= 0", name="ck_distribution_coverage_blocked"),
        CheckConstraint("unverified_empty_options >= 0", name="ck_distribution_coverage_empty"),
        CheckConstraint(
            "options_examined = events_present_options + blocked_source_options + "
            "unverified_empty_options",
            name="ck_distribution_coverage_run_totals",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    assessment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    options_examined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_present_options: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_source_options: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unverified_empty_options: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)


class DistributionCoverageAssessmentRecord(Base):
    """Append-only evidence state for one IDCW option in one coverage run."""

    __tablename__ = "distribution_coverage_assessments"
    __table_args__ = (
        CheckConstraint(
            "coverage_status IN ('events_present', 'blocked_source_rows', 'unverified_empty')",
            name="ck_distribution_coverage_status",
        ),
        CheckConstraint("source_row_count >= 0", name="ck_distribution_coverage_source_rows"),
        CheckConstraint(
            "canonical_source_row_count >= 0",
            name="ck_distribution_coverage_canonical_sources",
        ),
        CheckConstraint(
            "blocked_source_row_count >= 0",
            name="ck_distribution_coverage_blocked_sources",
        ),
        CheckConstraint("canonical_event_count >= 0", name="ck_distribution_coverage_event_count"),
        CheckConstraint(
            "source_row_count = canonical_source_row_count + blocked_source_row_count",
            name="ck_distribution_coverage_source_totals",
        ),
        CheckConstraint(
            "((source_row_count = 0 AND first_source_record_date IS NULL "
            "AND last_source_record_date IS NULL) OR "
            "(source_row_count > 0 AND first_source_record_date IS NOT NULL "
            "AND last_source_record_date IS NOT NULL))",
            name="ck_distribution_coverage_source_dates",
        ),
        CheckConstraint(
            "((coverage_status = 'events_present' AND canonical_event_count > 0) OR "
            "(coverage_status = 'blocked_source_rows' AND canonical_event_count = 0 "
            "AND source_row_count > 0) OR "
            "(coverage_status = 'unverified_empty' AND canonical_event_count = 0 "
            "AND source_row_count = 0))",
            name="ck_distribution_coverage_status_evidence",
        ),
        Index("ix_distribution_coverage_option_run", "amfi_scheme_code", "coverage_run_id"),
    )

    coverage_run_id: Mapped[str] = mapped_column(
        ForeignKey("distribution_coverage_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    amfi_scheme_code: Mapped[str] = mapped_column(
        ForeignKey("scheme_options.amfi_scheme_code", ondelete="RESTRICT"), primary_key=True
    )
    metadata_version_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_metadata_versions.id", ondelete="RESTRICT"), nullable=False
    )
    coverage_status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blocked_source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_source_record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_source_record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assessed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionParseIssueRecord(Base):
    __tablename__ = "distribution_parse_issues"
    __table_args__ = (
        CheckConstraint("record_number > 0", name="ck_distribution_issue_record_number"),
        CheckConstraint("status IN ('open', 'resolved')", name="ck_distribution_issue_status"),
        CheckConstraint(
            "((status = 'open' AND resolved_at IS NULL AND resolved_batch_id IS NULL) "
            "OR (status = 'resolved' "
            "AND resolved_at IS NOT NULL "
            "AND resolved_batch_id IS NOT NULL))",
            name="ck_distribution_issue_resolution",
        ),
        UniqueConstraint(
            "ingestion_batch_id",
            "record_number",
            name="uq_distribution_issue_batch_record",
        ),
        Index(
            "ix_distribution_issue_family_status",
            "mutual_fund_id",
            "source_scheme_id",
            "status",
        ),
        Index("ix_distribution_issue_signature_status", "source_record_signature", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    source_scheme_id: Mapped[str] = mapped_column(String(16), nullable=False)
    record_number: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    error_details: Mapped[str] = mapped_column(Text, nullable=False)
    raw_record: Mapped[Any] = mapped_column(JSON, nullable=False)
    source_record_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=True
    )


class DistributionSyncCheckpointRecord(Base):
    __tablename__ = "distribution_sync_checkpoints"
    __table_args__ = (
        CheckConstraint("rows_received >= 0", name="ck_distribution_checkpoint_rows"),
        CheckConstraint("rows_accepted >= 0", name="ck_distribution_checkpoint_accepted"),
        CheckConstraint("rows_rejected >= 0", name="ck_distribution_checkpoint_rejected"),
        CheckConstraint(
            "rows_received = rows_accepted + rows_rejected",
            name="ck_distribution_checkpoint_row_totals",
        ),
    )

    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), primary_key=True
    )
    source_scheme_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_accepted: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False)
    last_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class DistributionSyncRunRecord(Base):
    __tablename__ = "distribution_sync_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('full', 'refresh')", name="ck_distribution_sync_mode"),
        CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
            name="ck_distribution_sync_status",
        ),
        CheckConstraint(
            "record_error_policy IN ('fail', 'quarantine')",
            name="ck_distribution_record_error_policy",
        ),
        CheckConstraint("funds_total >= 0", name="ck_distribution_run_funds_total"),
        CheckConstraint("funds_completed >= 0", name="ck_distribution_run_funds_completed"),
        CheckConstraint("schemes_total >= 0", name="ck_distribution_run_schemes_total"),
        CheckConstraint("schemes_completed >= 0", name="ck_distribution_run_schemes_completed"),
        CheckConstraint("rows_received >= 0", name="ck_distribution_run_rows_received"),
        CheckConstraint("rows_inserted >= 0", name="ck_distribution_run_rows_inserted"),
        CheckConstraint("rows_unchanged >= 0", name="ck_distribution_run_rows_unchanged"),
        CheckConstraint("rows_unresolved >= 0", name="ck_distribution_run_rows_unresolved"),
        CheckConstraint("rows_rejected >= 0", name="ck_distribution_run_rows_rejected"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    record_error_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="fail")
    funds_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schemes_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schemes_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unresolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)


class SchemeLifecycleSyncRunRecord(Base):
    """Audited, resumable acquisition of AMFI family-level lifecycle facts."""

    __tablename__ = "scheme_lifecycle_sync_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('full', 'refresh')", name="ck_lifecycle_sync_mode"),
        CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
            name="ck_lifecycle_sync_status",
        ),
        CheckConstraint("funds_total >= 0", name="ck_lifecycle_funds_total"),
        CheckConstraint("funds_completed >= 0", name="ck_lifecycle_funds_completed"),
        CheckConstraint("funds_failed >= 0", name="ck_lifecycle_funds_failed"),
        CheckConstraint("families_total >= 0", name="ck_lifecycle_families_total"),
        CheckConstraint("families_completed >= 0", name="ck_lifecycle_families_completed"),
        CheckConstraint("families_skipped >= 0", name="ck_lifecycle_families_skipped"),
        CheckConstraint("families_failed >= 0", name="ck_lifecycle_families_failed"),
        CheckConstraint("detail_rows_received >= 0", name="ck_lifecycle_rows_received"),
        CheckConstraint("detail_rows_inserted >= 0", name="ck_lifecycle_rows_inserted"),
        CheckConstraint("detail_rows_unchanged >= 0", name="ck_lifecycle_rows_unchanged"),
        CheckConstraint("detail_rows_rejected >= 0", name="ck_lifecycle_rows_rejected"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    funds_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funds_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    families_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    families_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    families_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    families_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_rows_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)


class AmfiSchemeListSnapshotRecord(Base):
    """One immutable observation of a fund house's current AMFI family catalog."""

    __tablename__ = "amfi_scheme_list_snapshots"
    __table_args__ = (
        CheckConstraint("scheme_count >= 0", name="ck_scheme_list_snapshot_count"),
        Index("ix_scheme_list_snapshot_fund_time", "mutual_fund_id", "captured_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    scheme_count: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AmfiSchemeListMembershipRecord(Base):
    """Exact source family identifier/name present in one list snapshot."""

    __tablename__ = "amfi_scheme_list_memberships"
    __table_args__ = (
        CheckConstraint("length(content_signature) = 64", name="ck_scheme_membership_signature"),
        Index("ix_scheme_membership_family", "mutual_fund_id", "source_scheme_id"),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_scheme_list_snapshots.id", ondelete="RESTRICT"), primary_key=True
    )
    source_scheme_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    source_scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False)


class AmfiSchemeDetailRecord(Base):
    """Immutable normalized row from AMFI's family-level scheme-details API."""

    __tablename__ = "amfi_scheme_detail_records"
    __table_args__ = (
        CheckConstraint("length(content_signature) = 64", name="ck_scheme_detail_signature"),
        Index("ix_scheme_detail_family", "mutual_fund_id", "source_scheme_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    source_scheme_id: Mapped[str] = mapped_column(String(16), nullable=False)
    mutual_fund_name: Mapped[str] = mapped_column(Text, nullable=False)
    scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    scheme_type: Mapped[str] = mapped_column(Text, nullable=False)
    scheme_category: Mapped[str] = mapped_column(Text, nullable=False)
    launch_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class AmfiSchemeDetailRecordSource(Base):
    __tablename__ = "amfi_scheme_detail_record_sources"

    scheme_detail_record_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_scheme_detail_records.id", ondelete="RESTRICT"), primary_key=True
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SchemeLifecycleEventRecord(Base):
    """Immutable explicit lifecycle fact; conflicts remain multiple facts plus issues."""

    __tablename__ = "scheme_lifecycle_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('launch', 'rename', 'merger', 'closure', 'maturity')",
            name="ck_scheme_lifecycle_event_type",
        ),
        CheckConstraint("length(content_signature) = 64", name="ck_lifecycle_event_signature"),
        Index(
            "ix_lifecycle_event_family_date",
            "mutual_fund_id",
            "source_scheme_id",
            "effective_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    source_scheme_id: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    prior_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    successor_mutual_fund_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    successor_source_scheme_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SchemeLifecycleEventSourceRecord(Base):
    __tablename__ = "scheme_lifecycle_event_sources"

    scheme_lifecycle_event_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_lifecycle_events.id", ondelete="RESTRICT"), primary_key=True
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SchemeLifecycleCheckpointRecord(Base):
    __tablename__ = "scheme_lifecycle_checkpoints"

    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), primary_key=True
    )
    source_scheme_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_scheme_name: Mapped[str] = mapped_column(Text, nullable=False)
    latest_detail_record_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_scheme_detail_records.id", ondelete="RESTRICT"), nullable=False
    )
    last_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    last_sync_run_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_lifecycle_sync_runs.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SchemeLifecycleIssueRecord(Base):
    """Append-only acquisition or semantic issue that must not become an inferred event."""

    __tablename__ = "scheme_lifecycle_issues"
    __table_args__ = (
        CheckConstraint(
            "issue_code IN ('scheme_list_failure', 'scheme_detail_failure', "
            "'identity_mismatch', 'launch_date_conflict', 'catalog_member_removed', "
            "'name_changed_without_effective_date', 'missing_launch_date')",
            name="ck_scheme_lifecycle_issue_code",
        ),
        Index("ix_lifecycle_issue_run_code", "sync_run_id", "issue_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sync_run_id: Mapped[str] = mapped_column(
        ForeignKey("scheme_lifecycle_sync_runs.id", ondelete="RESTRICT"), nullable=False
    )
    mutual_fund_id: Mapped[str] = mapped_column(
        ForeignKey("amfi_funds.mutual_fund_id", ondelete="RESTRICT"), nullable=False
    )
    source_scheme_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ingestion_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=True
    )
    issue_code: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class BenchmarkSyncRunRecord(Base):
    """One resumable acquisition run for official benchmark observations."""

    __tablename__ = "benchmark_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "source_family IN ('nifty_indices', 'exchange_etf')",
            name="ck_benchmark_sync_source_family",
        ),
        CheckConstraint("mode IN ('full', 'refresh')", name="ck_benchmark_sync_mode"),
        CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
            name="ck_benchmark_sync_status",
        ),
        CheckConstraint("requests_total >= 0", name="ck_benchmark_sync_requests_total"),
        CheckConstraint("requests_completed >= 0", name="ck_benchmark_sync_requests_completed"),
        CheckConstraint("requests_skipped >= 0", name="ck_benchmark_sync_requests_skipped"),
        CheckConstraint("requests_failed >= 0", name="ck_benchmark_sync_requests_failed"),
        CheckConstraint("rows_received >= 0", name="ck_benchmark_sync_rows_received"),
        CheckConstraint("rows_inserted >= 0", name="ck_benchmark_sync_rows_inserted"),
        CheckConstraint("rows_unchanged >= 0", name="ck_benchmark_sync_rows_unchanged"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_family: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requested_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    requests_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)


class BenchmarkInstrumentRecord(Base):
    """Stable source identity for an index return series or exchange-listed ETF."""

    __tablename__ = "benchmark_instruments"
    __table_args__ = (
        CheckConstraint(
            "instrument_type IN ('price_index', 'gross_total_return_index', "
            "'net_total_return_index', 'etf')",
            name="ck_benchmark_instrument_type",
        ),
        CheckConstraint(
            "provider IN ('nifty_indices', 'nse', 'bse')",
            name="ck_benchmark_instrument_provider",
        ),
        CheckConstraint("currency = 'INR'", name="ck_benchmark_instrument_currency"),
        UniqueConstraint(
            "provider",
            "instrument_type",
            "source_identifier",
            name="uq_benchmark_instrument_source_identity",
        ),
        Index("ix_benchmark_instrument_isin", "isin"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_identifier: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    benchmark_family: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    source_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_series: Mapped[str | None] = mapped_column(String(8), nullable=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class BenchmarkExchangeListingRecord(Base):
    """Observed exchange identity for an ETF; BSE and NSE listings stay distinct."""

    __tablename__ = "benchmark_exchange_listings"
    __table_args__ = (
        CheckConstraint("exchange IN ('NSE', 'BSE')", name="ck_benchmark_listing_exchange"),
        UniqueConstraint(
            "exchange",
            "source_security_id",
            "benchmark_instrument_id",
            name="uq_benchmark_listing_exchange_security_instrument",
        ),
        Index("ix_benchmark_listing_exchange_security", "exchange", "source_security_id"),
        Index("ix_benchmark_listing_instrument_exchange", "benchmark_instrument_id", "exchange"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    benchmark_instrument_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_instruments.id", ondelete="RESTRICT"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(3), nullable=False)
    source_security_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    source_series: Mapped[str] = mapped_column(String(8), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    last_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    first_observed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )


class BenchmarkObservationRecord(Base):
    """Immutable revision of one official benchmark or ETF daily observation."""

    __tablename__ = "benchmark_observations"
    __table_args__ = (
        CheckConstraint(
            "CAST(close_value AS NUMERIC) > 0",
            name="ck_benchmark_observation_positive_close",
        ),
        CheckConstraint(
            "open_value IS NULL OR CAST(open_value AS NUMERIC) > 0",
            name="ck_benchmark_positive_open",
        ),
        CheckConstraint(
            "high_value IS NULL OR CAST(high_value AS NUMERIC) > 0",
            name="ck_benchmark_positive_high",
        ),
        CheckConstraint(
            "low_value IS NULL OR CAST(low_value AS NUMERIC) > 0",
            name="ck_benchmark_positive_low",
        ),
        CheckConstraint(
            "previous_close IS NULL OR CAST(previous_close AS NUMERIC) >= 0",
            name="ck_benchmark_nonnegative_previous_close",
        ),
        CheckConstraint(
            "last_value IS NULL OR CAST(last_value AS NUMERIC) >= 0",
            name="ck_benchmark_nonnegative_last",
        ),
        CheckConstraint("volume IS NULL OR volume >= 0", name="ck_benchmark_nonnegative_volume"),
        CheckConstraint(
            "traded_value IS NULL OR CAST(traded_value AS NUMERIC) >= 0",
            name="ck_benchmark_nonnegative_traded_value",
        ),
        CheckConstraint("trade_count IS NULL OR trade_count >= 0", name="ck_benchmark_trades"),
        CheckConstraint(
            "identity_status IN ('official', 'provisional_current_mapping')",
            name="ck_benchmark_identity_status",
        ),
        CheckConstraint("length(content_signature) = 64", name="ck_benchmark_obs_signature"),
        UniqueConstraint("content_signature", name="uq_benchmark_observation_signature"),
        Index(
            "ix_benchmark_observation_instrument_date",
            "benchmark_instrument_id",
            "exchange",
            "observation_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    benchmark_instrument_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_instruments.id", ondelete="RESTRICT"), nullable=False
    )
    exchange: Mapped[str | None] = mapped_column(String(3), nullable=True)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_value: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    high_value: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    low_value: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    close_value: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    last_value: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    previous_close: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traded_value: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_status: Mapped[str] = mapped_column(String(32), nullable=False, default="official")
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    first_observed_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class BenchmarkObservationSourceRecord(Base):
    __tablename__ = "benchmark_observation_sources"

    benchmark_observation_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_observations.id", ondelete="RESTRICT"), primary_key=True
    )
    ingestion_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class BenchmarkSyncCheckpointRecord(Base):
    __tablename__ = "benchmark_sync_checkpoints"

    provider: Mapped[str] = mapped_column(String(24), primary_key=True)
    source_identifier: Mapped[str] = mapped_column(String(160), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    last_batch_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    last_sync_run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_sync_runs.id", ondelete="RESTRICT"), nullable=False
    )
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class BenchmarkIssueRecord(Base):
    """Append-only acquisition or revision issue; source facts are never overwritten."""

    __tablename__ = "benchmark_issues"
    __table_args__ = (
        CheckConstraint(
            "issue_code IN ('download_failure', 'empty_trading_day', 'empty_source_period', "
            "'identity_unresolved', "
            "'observation_revision', 'provisional_bse_identity', 'partial_bse_roster', "
            "'invalid_ohlc', 'invalid_close', 'security_identity_change')",
            name="ck_benchmark_issue_code",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name="ck_benchmark_issue_severity"
        ),
        Index("ix_benchmark_issue_run_code", "sync_run_id", "issue_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sync_run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_sync_runs.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    source_identifier: Mapped[str] = mapped_column(String(160), nullable=False)
    observation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ingestion_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=True
    )
    issue_code: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
