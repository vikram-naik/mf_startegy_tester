from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from mf_strategy_tester.db.models import (
    IngestionBatchRecord,
    NavSyncRunRecord,
    StrategyRecord,
    StrategyVersionRecord,
)
from mf_strategy_tester.domain.strategy import StrategyDefinition
from mf_strategy_tester.services.nav_performance import (
    DrawdownSummary,
    NavReturn,
    RollingReturnSummary,
)


class StrategyWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: StrategyDefinition


class StrategyVersionResponse(BaseModel):
    id: str
    version: int
    definition: StrategyDefinition
    created_at: datetime

    @classmethod
    def from_record(cls, record: StrategyVersionRecord) -> "StrategyVersionResponse":
        return cls(
            id=record.id,
            version=record.version,
            definition=StrategyDefinition.model_validate(record.definition),
            created_at=record.created_at,
        )


class StrategySummaryResponse(BaseModel):
    id: str
    name: str
    description: str
    latest_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: StrategyRecord) -> "StrategySummaryResponse":
        return cls(
            id=record.id,
            name=record.name,
            description=record.description,
            latest_version=max(version.version for version in record.versions),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class StrategyDetailResponse(StrategySummaryResponse):
    versions: tuple[StrategyVersionResponse, ...]

    @classmethod
    def from_record(cls, record: StrategyRecord) -> "StrategyDetailResponse":
        summary = StrategySummaryResponse.from_record(record)
        return cls(
            **summary.model_dump(),
            versions=tuple(
                StrategyVersionResponse.from_record(version) for version in record.versions
            ),
        )


class HealthResponse(BaseModel):
    status: str
    database: str


class IngestionBatchResponse(BaseModel):
    id: str
    provider: str
    source_type: str
    status: str
    parser_version: str
    started_at: datetime
    completed_at: datetime | None
    artifact_sha256: str | None
    artifact_byte_size: int | None
    artifact_reused: bool
    rows_received: int
    rows_accepted: int
    rows_rejected: int
    error_details: str | None

    @classmethod
    def from_record(cls, record: IngestionBatchRecord) -> "IngestionBatchResponse":
        return cls(
            id=record.id,
            provider=record.provider,
            source_type=record.source_type,
            status=record.status,
            parser_version=record.parser_version,
            started_at=record.started_at,
            completed_at=record.completed_at,
            artifact_sha256=record.artifact.sha256 if record.artifact else None,
            artifact_byte_size=record.artifact.byte_size if record.artifact else None,
            artifact_reused=record.artifact_reused,
            rows_received=record.rows_received,
            rows_accepted=record.rows_accepted,
            rows_rejected=record.rows_rejected,
            error_details=record.error_details,
        )


class NavSyncRunResponse(BaseModel):
    id: str
    mode: str
    status: str
    requested_start_date: date
    requested_end_date: date
    funds_total: int
    funds_completed: int
    chunks_completed: int
    rows_received: int
    rows_inserted: int
    rows_unchanged: int
    rows_revised: int
    rows_quarantined: int
    latest_nav_date_found: date | None
    started_at: datetime
    completed_at: datetime | None
    error_details: str | None

    @classmethod
    def from_record(cls, record: NavSyncRunRecord) -> "NavSyncRunResponse":
        return cls.model_validate(record, from_attributes=True)


class DataCoverageResponse(BaseModel):
    active_funds: int
    fully_covered_funds: int
    scheme_options: int
    valid_nav_rows: int
    quarantined_nav_rows: int
    earliest_nav_date: date | None
    latest_nav_date: date | None
    latest_sync_run: NavSyncRunResponse | None


class FundHouseResponse(BaseModel):
    mutual_fund_id: str
    name: str
    is_active: bool
    scheme_options: int
    completed_through: date | None
    latest_nav_date_found: date | None
    fully_covered: bool


class SchemeCategoryResponse(BaseModel):
    classification: str
    scheme_options: int


class SchemeBrowserItemResponse(BaseModel):
    amfi_scheme_code: str
    scheme_name: str
    fund_house_name: str
    scheme_classification: str
    plan_type: str
    option_type: str
    isin_payout_or_growth: str | None
    isin_reinvestment: str | None
    first_nav_date: date
    latest_nav_date: date
    latest_nav_value: Decimal
    quality_status: str


class SchemeBrowserResponse(BaseModel):
    items: tuple[SchemeBrowserItemResponse, ...]
    total: int
    limit: int
    offset: int


class NavReturnResponse(BaseModel):
    start_date: date
    end_date: date
    start_nav: Decimal
    end_nav: Decimal
    elapsed_days: int
    total_return_pct: Decimal
    annualized_return_pct: Decimal | None

    @classmethod
    def from_result(cls, result: NavReturn) -> "NavReturnResponse":
        return cls.model_validate(result, from_attributes=True)


class RollingReturnSummaryResponse(BaseModel):
    window_years: int
    sample_count: int
    latest: NavReturnResponse | None
    minimum_annualized_return_pct: Decimal | None
    median_annualized_return_pct: Decimal | None
    mean_annualized_return_pct: Decimal | None
    maximum_annualized_return_pct: Decimal | None
    positive_periods_pct: Decimal | None

    @classmethod
    def from_result(cls, result: RollingReturnSummary) -> "RollingReturnSummaryResponse":
        return cls(
            window_years=result.window_years,
            sample_count=result.sample_count,
            latest=(NavReturnResponse.from_result(result.latest) if result.latest else None),
            minimum_annualized_return_pct=result.minimum_annualized_return_pct,
            median_annualized_return_pct=result.median_annualized_return_pct,
            mean_annualized_return_pct=result.mean_annualized_return_pct,
            maximum_annualized_return_pct=result.maximum_annualized_return_pct,
            positive_periods_pct=result.positive_periods_pct,
        )


class DrawdownSummaryResponse(BaseModel):
    maximum_drawdown_pct: Decimal
    peak_date: date
    trough_date: date

    @classmethod
    def from_result(cls, result: DrawdownSummary) -> "DrawdownSummaryResponse":
        return cls.model_validate(result, from_attributes=True)


class SchemePerformanceResponse(BaseModel):
    amfi_scheme_code: str
    return_basis: Literal["nav_only"]
    distribution_treatment: Literal["excluded"]
    day_count_convention: Literal["actual/365"]
    rolling_start_rule: str
    observation_count: int
    since_inception: NavReturnResponse
    rolling_returns: tuple[RollingReturnSummaryResponse, ...]
    drawdown: DrawdownSummaryResponse


class DistributionSourceProvenanceResponse(BaseModel):
    provider: str
    source_kind: Literal[
        "amfi_distribution_api",
        "amc_distribution_notice",
        "rta_distribution_history",
        "third_party_distribution_history",
    ]
    source_record_id: str
    mutual_fund_id: str | None
    source_scheme_id: str | None
    source_option_id: str
    scheme_name: str
    nav_name: str
    record_date: date
    raw_source_value: str
    source_unit: str
    source_content_signature: str
    ingestion_batch_id: str
    source_url: str
    retrieved_at: datetime
    parser_version: str
    artifact_sha256: str | None
    identity_evidence_batch_id: str | None = None
    identity_evidence_url: str | None = None
    identity_artifact_sha256: str | None = None
    identity_evidence_details: str | None = None


class DistributionEventRevisionResponse(BaseModel):
    revision_id: str
    revision_number: int
    amount_per_unit_inr: Decimal
    is_current: bool
    normalization_version: str
    normalized_at: datetime
    sources: tuple[DistributionSourceProvenanceResponse, ...]


class DistributionEventResponse(BaseModel):
    event_id: str
    record_date: date
    event_type: Literal["idcw_cash"]
    revisions: tuple[DistributionEventRevisionResponse, ...]


class DistributionCoverageResponse(BaseModel):
    assessment_run_id: str
    assessment_version: str
    coverage_status: Literal["events_present", "blocked_source_rows", "unverified_empty"]
    source_row_count: int
    canonical_source_row_count: int
    blocked_source_row_count: int
    canonical_event_count: int
    first_source_record_date: date | None
    last_source_record_date: date | None
    assessed_at: datetime


class DistributionEventBrowserResponse(BaseModel):
    items: tuple[DistributionEventResponse, ...]
    total: int
    limit: int
    offset: int
    coverage: DistributionCoverageResponse | None
