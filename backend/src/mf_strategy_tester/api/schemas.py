from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from mf_strategy_tester.db.models import (
    IngestionBatchRecord,
    NavSyncRunRecord,
)
from mf_strategy_tester.services.nav_performance import (
    DrawdownSummary,
    NavReturn,
    RollingReturnSummary,
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
    classification_id: str
    classification: str
    structure_type: Literal["open_ended", "close_ended", "interval", "other"]
    scheme_options: int


class ScreenerClassificationResponse(BaseModel):
    classification_id: str
    classification: str
    structure_type: Literal["open_ended", "close_ended", "interval", "other"]
    product_type: Literal["mutual_fund", "index_fund", "etf", "mixed"]
    candidate_options: int
    eligible_options: int
    excluded_options: int
    mapping_version: str


class ClassificationAliasSourceResponse(BaseModel):
    classification_id: str
    amfi_classification: str
    structure_type: Literal["open_ended", "close_ended", "interval", "other"]
    raw_labels: tuple[str, ...]
    alias_id: str | None


class ClassificationAliasResponse(BaseModel):
    id: str
    name: str
    structure_type: Literal["open_ended", "close_ended", "interval", "other"]
    status: Literal["active", "inactive"]
    version: int
    classification_ids: tuple[str, ...]
    updated_at: datetime


class ClassificationAliasRevisionResponse(BaseModel):
    alias_id: str
    version: int
    name: str
    structure_type: Literal["open_ended", "close_ended", "interval", "other"]
    status: Literal["active", "inactive"]
    classification_ids: tuple[str, ...]
    reason: str
    created_at: datetime


class ClassificationAliasManagementResponse(BaseModel):
    aliases: tuple[ClassificationAliasResponse, ...]
    source_classifications: tuple[ClassificationAliasSourceResponse, ...]
    revisions: tuple[ClassificationAliasRevisionResponse, ...]


class ClassificationAliasCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    structure_type: Literal["open_ended", "close_ended", "interval", "other"]
    status: Literal["active", "inactive"] = "active"
    classification_ids: tuple[str, ...] = Field(max_length=200)
    reason: str = Field(min_length=3, max_length=500)


class ClassificationAliasUpdateRequest(ClassificationAliasCreateRequest):
    expected_version: int = Field(ge=1)


class ScreenerFundResponse(BaseModel):
    rank: int
    amfi_scheme_code: str
    scheme_name: str
    fund_house_name: str
    scheme_classification: str
    plan_type: Literal["direct", "regular"]
    option_type: Literal["growth", "idcw"]
    isin: str | None
    start_date: date
    end_date: date
    start_nav: Decimal
    end_nav: Decimal
    elapsed_days: int
    endpoint_staleness_days: int
    total_return_pct: Decimal
    annualized_return_pct: Decimal | None
    payout_amount_per_unit_inr: Decimal | None
    payout_yield_pct: Decimal | None
    payout_event_count: int
    latest_payout_record_date: date | None
    payout_yield_rank: int | None
    payout_frequency_rank: int | None
    idcw_rank_score: Decimal | None


class ScreenerExclusionResponse(BaseModel):
    amfi_scheme_code: str
    scheme_name: str
    fund_house_name: str
    scheme_classification: str
    plan_type: Literal["direct", "regular"]
    option_type: Literal["growth", "idcw"]
    isin: str | None
    reason: Literal["stale_endpoint", "insufficient_history", "no_payout_events"]
    reason_detail: str
    first_nav_date: date
    latest_nav_date: date | None
    latest_nav: Decimal | None
    endpoint_staleness_days: int | None


class FundScreenerResponse(BaseModel):
    classification_id: str
    classification: str
    classification_mapping_version: str
    items: tuple[ScreenerFundResponse, ...]
    total: int
    limit: int
    offset: int
    as_of_date: date
    target_start_date: date
    horizon: Literal["1m", "3m", "6m", "1y", "3y", "5y", "10y"]
    ranking_metric: Literal[
        "total_return_pct", "annualized_return_pct", "payout_yield_frequency_score"
    ]
    ranking_method: str | None
    return_basis: Literal["nav_only"]
    distribution_treatment: Literal["excluded", "record_date_payout_yield"]
    day_count_convention: Literal["actual/365"]
    endpoint_tolerance_days: int
    candidate_options: int
    excluded_stale_endpoint: int
    excluded_insufficient_history: int
    excluded_no_payout_events: int
    excluded_items: tuple[ScreenerExclusionResponse, ...]
    exclusion_limit: int
    exclusion_offset: int


class BenchmarkSeriesResponse(BaseModel):
    instrument_id: str
    display_name: str
    benchmark_family: str
    provider: Literal["nifty_indices"]
    instrument_type: Literal["price_index", "gross_total_return_index", "net_total_return_index"]
    return_basis: Literal["price", "gross_total_return", "net_total_return"]
    observation_count: int
    first_observation_date: date
    latest_observation_date: date


class BenchmarkPerformanceResponse(BaseModel):
    instrument_id: str
    display_name: str
    benchmark_family: str
    provider: Literal["nifty_indices"]
    instrument_type: Literal["price_index", "gross_total_return_index", "net_total_return_index"]
    return_basis: Literal["price", "gross_total_return", "net_total_return"]
    status: Literal["available", "stale_endpoint", "insufficient_history"]
    reason_detail: str | None
    requested_as_of_date: date
    target_start_date: date
    endpoint_tolerance_days: int
    start_date: date | None
    end_date: date | None
    start_value: Decimal | None
    end_value: Decimal | None
    elapsed_days: int | None
    endpoint_staleness_days: int | None
    total_return_pct: Decimal | None
    annualized_return_pct: Decimal | None
    day_count_convention: Literal["actual/365"]


class HeatmapTileResponse(BaseModel):
    tile_id: str
    label: str
    status: Literal["available", "stale_endpoint", "insufficient_history"]
    reason_detail: str | None
    value_pct: Decimal | None
    minimum_constituent_pct: Decimal | None
    maximum_constituent_pct: Decimal | None
    candidate_count: int
    constituent_count: int
    excluded_count: int
    excluded_stale_endpoint: int
    excluded_insufficient_history: int
    sample_count: int
    return_basis: Literal["nav_only", "price", "gross_total_return", "net_total_return"]
    structure_type: Literal["open_ended", "close_ended", "interval", "other"] | None
    product_type: Literal["mutual_fund", "index_fund", "etf", "mixed"] | None
    classification_mapping_version: str | None
    period_start_date_min: date | None
    period_start_date_max: date | None
    period_end_date_min: date | None
    period_end_date_max: date | None
    maximum_endpoint_staleness_days: int | None


class HeatmapResponse(BaseModel):
    universe: Literal["funds", "benchmarks", "indices"]
    period: Literal[
        "1m",
        "3m",
        "6m",
        "1y",
        "3y",
        "5y",
        "10y",
        "rolling_1y",
        "rolling_3y",
        "rolling_5y",
        "rolling_10y",
    ]
    mode: Literal["trailing", "rolling"]
    requested_as_of_date: date
    target_start_date: date | None
    endpoint_tolerance_days: int
    metric: Literal[
        "median_constituent_return_pct",
        "series_return_pct",
        "median_rolling_annualized_return_pct",
    ]
    aggregation_method: str
    observation_frequency: str
    day_count_convention: Literal["actual/365"]
    distribution_treatment: str
    rolling_start_rule: str | None
    current_universe_limitation: str
    tiles: tuple[HeatmapTileResponse, ...]


class ComparisonNavPointResponse(BaseModel):
    nav_date: date
    nav_value: Decimal
    normalized_value: Decimal


class ComparisonDistributionEventResponse(BaseModel):
    record_date: date
    amount_per_unit_inr: Decimal


class FundComparisonSeriesResponse(BaseModel):
    amfi_scheme_code: str
    scheme_name: str
    fund_house_name: str
    scheme_classification: str
    plan_type: str
    option_type: str
    points: tuple[ComparisonNavPointResponse, ...]
    distributions: tuple[ComparisonDistributionEventResponse, ...]


class FundComparisonResponse(BaseModel):
    start_date: date
    end_date: date
    normalization_base: Literal["first_observation_100"]
    return_basis: Literal["nav_only"]
    distribution_markers: Literal["record_date"]
    series: tuple[FundComparisonSeriesResponse, ...]


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
