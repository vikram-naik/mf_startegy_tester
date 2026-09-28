from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, case, distinct, exists, func, select
from sqlalchemy.sql.elements import ColumnElement

from mf_strategy_tester.api.dependencies import DatabaseSession
from mf_strategy_tester.api.schemas import (
    BenchmarkPerformanceResponse,
    BenchmarkSeriesResponse,
    ClassificationAliasCreateRequest,
    ClassificationAliasManagementResponse,
    ClassificationAliasResponse,
    ClassificationAliasRevisionResponse,
    ClassificationAliasSourceResponse,
    ClassificationAliasUpdateRequest,
    ComparisonDistributionEventResponse,
    ComparisonNavPointResponse,
    DataCoverageResponse,
    DistributionCoverageResponse,
    DistributionEventBrowserResponse,
    DistributionEventResponse,
    DistributionEventRevisionResponse,
    DistributionSourceProvenanceResponse,
    DrawdownSummaryResponse,
    FundComparisonResponse,
    FundComparisonSeriesResponse,
    FundHouseResponse,
    FundScreenerResponse,
    HeatmapResponse,
    NavReturnResponse,
    NavSyncRunResponse,
    RollingReturnSummaryResponse,
    SchemeBrowserItemResponse,
    SchemeBrowserResponse,
    SchemeCategoryResponse,
    SchemePerformanceResponse,
    ScreenerClassificationResponse,
    ScreenerExclusionResponse,
    ScreenerFundResponse,
)
from mf_strategy_tester.db.models import (
    AdvisorkhojCatalogSchemeRecord,
    AdvisorkhojDistributionRecord,
    AdvisorkhojSchemeCaptureRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    AmfiDistributionRecord,
    AmfiDistributionRecordSource,
    AmfiFundRecord,
    DistributionCoverageAssessmentRecord,
    DistributionCoverageRunRecord,
    DistributionEventRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionEventRevisionSourceRecord,
    IngestionBatchRecord,
    NavDatasetStatsRecord,
    NavRevisionRecord,
    NavSyncCheckpointRecord,
    NavSyncCoverageRecord,
    NavSyncRunRecord,
    OfficialDistributionRecord,
    OfficialDistributionRecordSource,
    RtaDistributionRecord,
    RtaSchemeCaptureRecord,
    RtaSchemeMappingReviewRecord,
    SchemeClassificationAliasRecord,
    SchemeClassificationRecord,
    SchemeOptionRecord,
    ScreenerClassificationAliasMemberRecord,
    ScreenerClassificationAliasRecord,
    ScreenerClassificationAliasRevisionRecord,
    SourceArtifactRecord,
)
from mf_strategy_tester.db.models import (
    DistributionEventRevisionAdvisorkhojSourceRecord as AdvisorkhojRevisionSource,
)
from mf_strategy_tester.repositories.scheme_options import current_scheme_snapshot
from mf_strategy_tester.services.benchmark_performance import BenchmarkPerformanceService
from mf_strategy_tester.services.classification_reference import (
    SchemeProductType,
    SchemeStructure,
    scheme_classification_product_type,
    scheme_classification_structure,
)
from mf_strategy_tester.services.fund_screener import (
    ScreenerHorizon,
    calculate_trailing_return,
    horizon_start_date,
    is_annualized_horizon,
)
from mf_strategy_tester.services.heatmap import HeatmapPeriod, HeatmapService, HeatmapUniverse
from mf_strategy_tester.services.idcw_income_research import (
    IDCW_RANKING_METHOD,
    IDCWPayoutRankCandidate,
    calculate_payout_yield,
    rank_idcw_payouts,
)
from mf_strategy_tester.services.nav_performance import NavPoint, calculate_nav_performance
from mf_strategy_tester.services.nav_sync import EARLIEST_AMFI_NAV_DATE
from mf_strategy_tester.services.screener_classification_alias import (
    ClassificationSelection,
    alias_assignments,
    create_screener_alias,
    resolve_classification_selection,
    update_screener_alias,
)

router = APIRouter(prefix="/data", tags=["data"])


@dataclass(frozen=True)
class _CalculatedScreenerFund:
    metric: Decimal
    amfi_scheme_code: str
    scheme_name: str
    fund_house_name: str
    scheme_classification: str
    isin: str | None
    start_date: date
    start_nav: Decimal
    end_date: date
    end_nav: Decimal
    elapsed_days: int
    endpoint_staleness_days: int
    total_return_pct: Decimal
    annualized_return_pct: Decimal | None
    payout_amount_per_unit_inr: Decimal | None
    payout_yield_pct: Decimal | None
    payout_event_count: int
    latest_payout_record_date: date | None
    payout_yield_rank: int | None = None
    payout_frequency_rank: int | None = None
    idcw_rank_score: Decimal | None = None


@dataclass(frozen=True)
class _ExcludedScreenerFund:
    amfi_scheme_code: str
    scheme_name: str
    fund_house_name: str
    scheme_classification: str
    isin: str | None
    reason: Literal["stale_endpoint", "insufficient_history", "no_payout_events"]
    reason_detail: str
    first_nav_date: date
    latest_nav_date: date | None
    latest_nav: Decimal | None
    endpoint_staleness_days: int | None


@dataclass
class _AggregatedClassification:
    classification_id: str
    display_name: str
    structure_type: SchemeStructure
    mapping_version: str
    candidate_options: int = 0
    eligible_options: int = 0
    product_types: set[SchemeProductType] = field(default_factory=set)


def _aggregate_classification_counts(
    session: DatabaseSession,
    rows: tuple[tuple[str, str, str, int, int], ...],
) -> tuple[_AggregatedClassification, ...]:
    classification_ids = {row[0] for row in rows}
    assignments = alias_assignments(session, classification_ids)
    aggregated: dict[str, _AggregatedClassification] = {}
    for classification_id, _display_name, _mapping_version, candidate_count, eligible_count in rows:
        alias = assignments.get(classification_id)
        if alias is None:
            continue
        key = alias.id
        item = aggregated.get(key)
        if item is None:
            item = _AggregatedClassification(
                classification_id=key,
                display_name=alias.name,
                structure_type=alias.structure_type,  # type: ignore[arg-type]
                mapping_version=f"{alias.id}:v{alias.version}",
            )
            aggregated[key] = item
        item.candidate_options += int(candidate_count)
        item.eligible_options += int(eligible_count)
        item.product_types.add(scheme_classification_product_type(_display_name))
    return tuple(
        sorted(
            aggregated.values(),
            key=lambda item: (
                item.structure_type,
                item.display_name.casefold(),
                item.classification_id,
            ),
        )
    )


@dataclass(frozen=True)
class _ScreenerWindow:
    as_of_date: date
    target_start: date
    start_deadline: date
    stale_cutoff: date


def _aggregated_product_type(
    item: _AggregatedClassification,
) -> Literal["mutual_fund", "index_fund", "etf", "mixed"]:
    if len(item.product_types) == 1:
        return next(iter(item.product_types))
    return "mixed"


@router.get("/coverage", response_model=DataCoverageResponse)
def get_data_coverage(session: DatabaseSession) -> DataCoverageResponse:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    active_funds = session.scalar(
        select(func.count()).select_from(AmfiFundRecord).where(AmfiFundRecord.is_active.is_(True))
    )
    fully_covered_funds = session.scalar(
        select(func.count(distinct(NavSyncCoverageRecord.mutual_fund_id))).where(
            NavSyncCoverageRecord.start_date <= EARLIEST_AMFI_NAV_DATE,
            NavSyncCoverageRecord.end_date >= today,
        )
    )
    dataset_stats = session.get(NavDatasetStatsRecord, 1)
    latest_run = session.scalar(
        select(NavSyncRunRecord).order_by(NavSyncRunRecord.started_at.desc()).limit(1)
    )
    return DataCoverageResponse(
        active_funds=active_funds or 0,
        fully_covered_funds=fully_covered_funds or 0,
        scheme_options=dataset_stats.scheme_options if dataset_stats else 0,
        valid_nav_rows=dataset_stats.valid_current_rows if dataset_stats else 0,
        quarantined_nav_rows=dataset_stats.error_current_rows if dataset_stats else 0,
        earliest_nav_date=dataset_stats.earliest_valid_nav_date if dataset_stats else None,
        latest_nav_date=dataset_stats.latest_valid_nav_date if dataset_stats else None,
        latest_sync_run=NavSyncRunResponse.from_record(latest_run) if latest_run else None,
    )


@router.get("/fund-houses", response_model=tuple[FundHouseResponse, ...])
def list_fund_houses(session: DatabaseSession) -> tuple[FundHouseResponse, ...]:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    scheme_snapshot = current_scheme_snapshot().subquery()
    scheme_counts = (
        select(
            scheme_snapshot.c.fund_house_name,
            func.count().label("scheme_options"),
        )
        .group_by(scheme_snapshot.c.fund_house_name)
        .subquery()
    )
    fully_covered = (
        select(NavSyncCoverageRecord.mutual_fund_id)
        .where(
            NavSyncCoverageRecord.start_date <= EARLIEST_AMFI_NAV_DATE,
            NavSyncCoverageRecord.end_date >= today,
        )
        .distinct()
        .subquery()
    )
    rows = session.execute(
        select(
            AmfiFundRecord,
            func.coalesce(scheme_counts.c.scheme_options, 0),
            NavSyncCheckpointRecord.completed_through,
            NavSyncCheckpointRecord.latest_nav_date_found,
            fully_covered.c.mutual_fund_id.is_not(None),
        )
        .outerjoin(
            scheme_counts,
            scheme_counts.c.fund_house_name == AmfiFundRecord.mutual_fund_name,
        )
        .outerjoin(
            NavSyncCheckpointRecord,
            NavSyncCheckpointRecord.mutual_fund_id == AmfiFundRecord.mutual_fund_id,
        )
        .outerjoin(
            fully_covered,
            fully_covered.c.mutual_fund_id == AmfiFundRecord.mutual_fund_id,
        )
        .order_by(AmfiFundRecord.mutual_fund_name)
    ).all()
    return tuple(
        FundHouseResponse(
            mutual_fund_id=fund.mutual_fund_id,
            name=fund.mutual_fund_name,
            is_active=fund.is_active,
            scheme_options=scheme_options,
            completed_through=completed_through,
            latest_nav_date_found=latest_nav_date_found,
            fully_covered=is_fully_covered,
        )
        for fund, scheme_options, completed_through, latest_nav_date_found, is_fully_covered in rows
    )


@router.get(
    "/fund-houses/{mutual_fund_id}/categories",
    response_model=tuple[SchemeCategoryResponse, ...],
)
def list_scheme_categories(
    mutual_fund_id: str, session: DatabaseSession
) -> tuple[SchemeCategoryResponse, ...]:
    fund = _fund_or_404(mutual_fund_id, session)
    snapshot = current_scheme_snapshot(fund.mutual_fund_name).subquery()
    rows = session.execute(
        select(
            SchemeClassificationRecord.id,
            SchemeClassificationRecord.display_name,
            SchemeClassificationRecord.mapping_version,
            func.count(),
        )
        .select_from(snapshot)
        .join(
            SchemeClassificationAliasRecord,
            and_(
                SchemeClassificationAliasRecord.source_provider == "amfi",
                SchemeClassificationAliasRecord.raw_classification
                == snapshot.c.scheme_classification,
            ),
        )
        .join(
            SchemeClassificationRecord,
            SchemeClassificationRecord.id == SchemeClassificationAliasRecord.classification_id,
        )
        .where(SchemeClassificationRecord.status == "active")
        .group_by(
            SchemeClassificationRecord.id,
            SchemeClassificationRecord.display_name,
            SchemeClassificationRecord.mapping_version,
        )
        .order_by(SchemeClassificationRecord.display_name)
    ).all()
    aggregated = _aggregate_classification_counts(
        session,
        tuple(
            (classification_id, display_name, mapping_version, count, count)
            for classification_id, display_name, mapping_version, count in rows
        ),
    )
    return tuple(
        SchemeCategoryResponse(
            classification_id=item.classification_id,
            classification=item.display_name,
            structure_type=item.structure_type,
            scheme_options=item.candidate_options,
        )
        for item in aggregated
    )


@router.get("/classifications", response_model=tuple[ScreenerClassificationResponse, ...])
def list_classifications(
    session: DatabaseSession,
    plan_type: Literal["direct", "regular"] = "direct",
    option_type: Literal["growth", "idcw"] = "growth",
    horizon: ScreenerHorizon = "1y",
    as_of: date | None = None,
    endpoint_tolerance_days: int = Query(default=7, ge=0, le=31),
    fund_house: Annotated[list[str] | None, Query()] = None,
) -> tuple[ScreenerClassificationResponse, ...]:
    _validate_screener_mode(option_type=option_type, horizon=horizon)
    window = _screener_window_or_error(
        session,
        horizon=horizon,
        as_of=as_of,
        endpoint_tolerance_days=endpoint_tolerance_days,
    )
    snapshot = current_scheme_snapshot().subquery()
    nav_eligible = and_(
        exists(
            select(NavRevisionRecord.id).where(
                NavRevisionRecord.amfi_scheme_code == snapshot.c.amfi_scheme_code,
                NavRevisionRecord.is_current.is_(True),
                NavRevisionRecord.quality_status == "valid",
                NavRevisionRecord.nav_date >= window.stale_cutoff,
                NavRevisionRecord.nav_date <= window.as_of_date,
            )
        ),
        exists(
            select(NavRevisionRecord.id).where(
                NavRevisionRecord.amfi_scheme_code == snapshot.c.amfi_scheme_code,
                NavRevisionRecord.is_current.is_(True),
                NavRevisionRecord.quality_status == "valid",
                NavRevisionRecord.nav_date >= window.target_start,
                NavRevisionRecord.nav_date <= window.start_deadline,
            )
        ),
    )
    eligible = nav_eligible
    if option_type == "idcw":
        eligible = and_(
            nav_eligible,
            snapshot.c.isin_payout_or_growth.is_not(None),
            exists(
                select(DistributionEventRecord.id)
                .join(
                    DistributionEventRevisionRecord,
                    DistributionEventRevisionRecord.distribution_event_id
                    == DistributionEventRecord.id,
                )
                .where(
                    DistributionEventRecord.amfi_scheme_code
                    == snapshot.c.amfi_scheme_code,
                    DistributionEventRecord.record_date > window.target_start,
                    DistributionEventRecord.record_date <= window.as_of_date,
                    DistributionEventRevisionRecord.is_current.is_(True),
                )
            ),
        )
    eligible_count = func.sum(case((eligible, 1), else_=0))
    statement = (
        select(
            SchemeClassificationRecord.id,
            SchemeClassificationRecord.display_name,
            SchemeClassificationRecord.mapping_version,
            func.count().label("candidate_options"),
            eligible_count.label("eligible_options"),
        )
        .select_from(snapshot)
        .join(
            SchemeClassificationAliasRecord,
            and_(
                SchemeClassificationAliasRecord.source_provider == "amfi",
                SchemeClassificationAliasRecord.raw_classification
                == snapshot.c.scheme_classification,
            ),
        )
        .join(
            SchemeClassificationRecord,
            SchemeClassificationRecord.id == SchemeClassificationAliasRecord.classification_id,
        )
        .where(
            snapshot.c.plan_type == plan_type,
            snapshot.c.option_type == option_type,
            SchemeClassificationRecord.status == "active",
        )
        .group_by(
            SchemeClassificationRecord.id,
            SchemeClassificationRecord.display_name,
            SchemeClassificationRecord.mapping_version,
        )
        .having(eligible_count > 0)
        .order_by(SchemeClassificationRecord.display_name)
    )
    if fund_house:
        statement = statement.where(snapshot.c.fund_house_name.in_(fund_house))
    if option_type == "idcw":
        statement = statement.where(snapshot.c.isin_payout_or_growth.is_not(None))
    result_rows = session.execute(statement).all()
    rows = _aggregate_classification_counts(
        session,
        tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                int(row[3]),
                int(row[4]),
            )
            for row in result_rows
        ),
    )
    return tuple(
        ScreenerClassificationResponse(
            classification_id=item.classification_id,
            classification=item.display_name,
            structure_type=item.structure_type,
            product_type=_aggregated_product_type(item),
            candidate_options=item.candidate_options,
            eligible_options=item.eligible_options,
            excluded_options=item.candidate_options - item.eligible_options,
            mapping_version=item.mapping_version,
        )
        for item in rows
    )


@router.get("/benchmark-series", response_model=tuple[BenchmarkSeriesResponse, ...])
def list_benchmark_series(session: DatabaseSession) -> tuple[BenchmarkSeriesResponse, ...]:
    return tuple(
        BenchmarkSeriesResponse.model_validate(item, from_attributes=True)
        for item in BenchmarkPerformanceService(session).list_series()
    )


@router.get(
    "/benchmark-series/{instrument_id}/performance",
    response_model=BenchmarkPerformanceResponse,
)
def get_benchmark_performance(
    instrument_id: str,
    session: DatabaseSession,
    as_of: date,
    horizon: ScreenerHorizon = "1y",
    endpoint_tolerance_days: int = Query(default=7, ge=0, le=31),
) -> BenchmarkPerformanceResponse:
    try:
        result = BenchmarkPerformanceService(session).calculate(
            instrument_id=instrument_id,
            as_of=as_of,
            horizon=horizon,
            endpoint_tolerance_days=endpoint_tolerance_days,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return BenchmarkPerformanceResponse.model_validate(result, from_attributes=True)


@router.get("/heatmap", response_model=HeatmapResponse)
def get_heatmap(
    session: DatabaseSession,
    universe: HeatmapUniverse = "funds",
    period: HeatmapPeriod = "1m",
    plan_type: Literal["direct", "regular"] = "direct",
    as_of: date | None = None,
    endpoint_tolerance_days: int = Query(default=7, ge=0, le=31),
) -> HeatmapResponse:
    try:
        result = HeatmapService(session).calculate(
            universe=universe,
            period=period,
            plan_type=plan_type,
            as_of=as_of,
            endpoint_tolerance_days=endpoint_tolerance_days,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return HeatmapResponse.model_validate(result, from_attributes=True)


@router.get(
    "/classification-aliases",
    response_model=ClassificationAliasManagementResponse,
)
def list_classification_aliases(session: DatabaseSession) -> ClassificationAliasManagementResponse:
    aliases = tuple(
        session.scalars(
            select(ScreenerClassificationAliasRecord).order_by(
                ScreenerClassificationAliasRecord.structure_type,
                ScreenerClassificationAliasRecord.name,
                ScreenerClassificationAliasRecord.id,
            )
        ).all()
    )
    members = session.execute(
        select(
            ScreenerClassificationAliasMemberRecord.classification_id,
            ScreenerClassificationAliasMemberRecord.alias_id,
        )
    ).all()
    alias_by_classification = {
        classification_id: alias_id for classification_id, alias_id in members
    }
    member_ids_by_alias: dict[str, list[str]] = {alias.id: [] for alias in aliases}
    for classification_id, alias_id in members:
        member_ids_by_alias.setdefault(alias_id, []).append(classification_id)

    classifications = tuple(
        session.scalars(
            select(SchemeClassificationRecord)
            .where(SchemeClassificationRecord.status == "active")
            .order_by(SchemeClassificationRecord.display_name, SchemeClassificationRecord.id)
        ).all()
    )
    raw_rows = session.execute(
        select(
            SchemeClassificationAliasRecord.classification_id,
            SchemeClassificationAliasRecord.raw_classification,
        )
        .where(SchemeClassificationAliasRecord.source_provider == "amfi")
        .order_by(SchemeClassificationAliasRecord.raw_classification)
    ).all()
    raw_by_classification: dict[str, list[str]] = {}
    for classification_id, raw_label in raw_rows:
        raw_by_classification.setdefault(classification_id, []).append(raw_label)

    revisions = tuple(
        session.scalars(
            select(ScreenerClassificationAliasRevisionRecord).order_by(
                ScreenerClassificationAliasRevisionRecord.created_at.desc(),
                ScreenerClassificationAliasRevisionRecord.alias_id,
                ScreenerClassificationAliasRevisionRecord.version.desc(),
            )
        ).all()
    )
    return ClassificationAliasManagementResponse(
        aliases=tuple(
            _classification_alias_response(alias, member_ids_by_alias.get(alias.id, []))
            for alias in aliases
        ),
        source_classifications=tuple(
            ClassificationAliasSourceResponse(
                classification_id=classification.id,
                amfi_classification=classification.display_name,
                structure_type=scheme_classification_structure(classification.display_name),
                raw_labels=tuple(raw_by_classification.get(classification.id, [])),
                alias_id=alias_by_classification.get(classification.id),
            )
            for classification in classifications
        ),
        revisions=tuple(
            ClassificationAliasRevisionResponse(
                alias_id=revision.alias_id,
                version=revision.version,
                name=revision.name,
                structure_type=cast(SchemeStructure, revision.structure_type),
                status=cast(Literal["active", "inactive"], revision.status),
                classification_ids=tuple(revision.member_classification_ids),
                reason=revision.change_reason,
                created_at=revision.created_at,
            )
            for revision in revisions
        ),
    )


@router.post(
    "/classification-aliases",
    response_model=ClassificationAliasResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_classification_alias(
    request: ClassificationAliasCreateRequest,
    session: DatabaseSession,
) -> ClassificationAliasResponse:
    try:
        alias = create_screener_alias(
            session,
            name=request.name,
            structure_type=request.structure_type,
            status=request.status,
            classification_ids=request.classification_ids,
            reason=request.reason,
        )
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _classification_alias_response(alias, request.classification_ids)


@router.put(
    "/classification-aliases/{alias_id}",
    response_model=ClassificationAliasResponse,
)
def update_classification_alias(
    alias_id: str,
    request: ClassificationAliasUpdateRequest,
    session: DatabaseSession,
) -> ClassificationAliasResponse:
    try:
        alias = update_screener_alias(
            session,
            alias_id=alias_id,
            expected_version=request.expected_version,
            name=request.name,
            structure_type=request.structure_type,
            status=request.status,
            classification_ids=request.classification_ids,
            reason=request.reason,
        )
    except LookupError as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _classification_alias_response(alias, request.classification_ids)


@router.get("/screener", response_model=FundScreenerResponse)
def screen_funds(
    session: DatabaseSession,
    classification_id: Annotated[str | None, Query(min_length=1)] = None,
    classification: Annotated[str | None, Query(min_length=1)] = None,
    horizon: ScreenerHorizon = "1y",
    plan_type: Literal["direct", "regular"] = "direct",
    option_type: Literal["growth", "idcw"] = "growth",
    fund_house: Annotated[list[str] | None, Query()] = None,
    search: str | None = Query(default=None, max_length=100),
    as_of: date | None = None,
    endpoint_tolerance_days: int = Query(default=7, ge=0, le=31),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    exclusion_limit: int = Query(default=25, ge=1, le=100),
    exclusion_offset: int = Query(default=0, ge=0),
) -> FundScreenerResponse:
    _validate_screener_mode(option_type=option_type, horizon=horizon)
    classification_selection = _classification_reference_or_422(
        session, classification_id=classification_id, classification=classification
    )
    window = _screener_window_or_error(
        session,
        horizon=horizon,
        as_of=as_of,
        endpoint_tolerance_days=endpoint_tolerance_days,
    )
    classification_aliases = select(SchemeClassificationAliasRecord.raw_classification).where(
        SchemeClassificationAliasRecord.source_provider == "amfi",
        SchemeClassificationAliasRecord.classification_id.in_(
            classification_selection.classification_ids
        ),
    )
    snapshot = current_scheme_snapshot().subquery()

    end_date = (
        select(NavRevisionRecord.nav_date)
        .where(
            NavRevisionRecord.amfi_scheme_code == snapshot.c.amfi_scheme_code,
            NavRevisionRecord.is_current.is_(True),
            NavRevisionRecord.quality_status == "valid",
            NavRevisionRecord.nav_date <= window.as_of_date,
        )
        .order_by(NavRevisionRecord.nav_date.desc())
        .limit(1)
        .correlate(snapshot)
        .scalar_subquery()
    )
    end_nav = (
        select(NavRevisionRecord.nav_value)
        .where(
            NavRevisionRecord.amfi_scheme_code == snapshot.c.amfi_scheme_code,
            NavRevisionRecord.is_current.is_(True),
            NavRevisionRecord.quality_status == "valid",
            NavRevisionRecord.nav_date == end_date,
        )
        .limit(1)
        .correlate(snapshot)
        .scalar_subquery()
    )
    start_date = (
        select(NavRevisionRecord.nav_date)
        .where(
            NavRevisionRecord.amfi_scheme_code == snapshot.c.amfi_scheme_code,
            NavRevisionRecord.is_current.is_(True),
            NavRevisionRecord.quality_status == "valid",
            NavRevisionRecord.nav_date >= window.target_start,
            NavRevisionRecord.nav_date <= window.start_deadline,
        )
        .order_by(NavRevisionRecord.nav_date)
        .limit(1)
        .correlate(snapshot)
        .scalar_subquery()
    )
    start_nav = (
        select(NavRevisionRecord.nav_value)
        .where(
            NavRevisionRecord.amfi_scheme_code == snapshot.c.amfi_scheme_code,
            NavRevisionRecord.is_current.is_(True),
            NavRevisionRecord.quality_status == "valid",
            NavRevisionRecord.nav_date == start_date,
        )
        .limit(1)
        .correlate(snapshot)
        .scalar_subquery()
    )
    statement = (
        select(
            snapshot.c.amfi_scheme_code,
            snapshot.c.scheme_name,
            snapshot.c.fund_house_name,
            snapshot.c.scheme_classification,
            snapshot.c.plan_type,
            snapshot.c.option_type,
            snapshot.c.isin_payout_or_growth,
            snapshot.c.first_nav_date.label("first_observed_nav_date"),
            start_date.label("start_date"),
            start_nav.label("start_nav"),
            end_date.label("end_date"),
            end_nav.label("end_nav"),
        )
        .select_from(snapshot)
        .where(
            snapshot.c.scheme_classification.in_(classification_aliases),
            snapshot.c.plan_type == plan_type,
            snapshot.c.option_type == option_type,
        )
    )
    if option_type == "idcw":
        statement = statement.where(snapshot.c.isin_payout_or_growth.is_not(None))
    if fund_house:
        statement = statement.where(snapshot.c.fund_house_name.in_(fund_house))
    normalized_search = search.strip().lower() if search else None
    if normalized_search:
        pattern = f"%{normalized_search}%"
        statement = statement.where(
            func.lower(snapshot.c.scheme_name).like(pattern)
            | func.lower(snapshot.c.amfi_scheme_code).like(pattern)
            | func.lower(func.coalesce(snapshot.c.isin_payout_or_growth, "")).like(pattern)
        )
    rows = session.execute(statement).mappings().all()
    payout_amounts: dict[str, Decimal] = {}
    payout_event_counts: dict[str, int] = {}
    latest_payout_dates: dict[str, date] = {}
    if option_type == "idcw" and rows:
        payout_rows = session.execute(
            select(
                DistributionEventRecord.amfi_scheme_code,
                DistributionEventRecord.record_date,
                DistributionEventRevisionRecord.amount_per_unit_inr,
            )
            .join(
                DistributionEventRevisionRecord,
                DistributionEventRevisionRecord.distribution_event_id
                == DistributionEventRecord.id,
            )
            .where(
                DistributionEventRecord.amfi_scheme_code.in_(
                    tuple(row["amfi_scheme_code"] for row in rows)
                ),
                DistributionEventRecord.record_date > window.target_start,
                DistributionEventRecord.record_date <= window.as_of_date,
                DistributionEventRevisionRecord.is_current.is_(True),
            )
        ).all()
        for scheme_code, record_date, amount in payout_rows:
            payout_amounts[scheme_code] = payout_amounts.get(scheme_code, Decimal(0)) + amount
            payout_event_counts[scheme_code] = payout_event_counts.get(scheme_code, 0) + 1
            latest_payout_dates[scheme_code] = max(
                record_date, latest_payout_dates.get(scheme_code, record_date)
            )
    stale_count = 0
    insufficient_count = 0
    no_payout_count = 0
    calculated: list[_CalculatedScreenerFund] = []
    excluded: list[_ExcludedScreenerFund] = []
    annualize = is_annualized_horizon(horizon)
    for row in rows:
        row_end_date = row["end_date"]
        if row_end_date is None or row_end_date < window.stale_cutoff:
            stale_count += 1
            staleness = (
                (window.as_of_date - row_end_date).days if row_end_date is not None else None
            )
            detail = (
                f"Latest valid NAV {row_end_date.isoformat()} precedes required endpoint cutoff "
                f"{window.stale_cutoff.isoformat()}"
                if row_end_date is not None
                else f"No valid NAV exists on or before {window.as_of_date.isoformat()}"
            )
            excluded.append(
                _ExcludedScreenerFund(
                    amfi_scheme_code=row["amfi_scheme_code"],
                    scheme_name=row["scheme_name"],
                    fund_house_name=row["fund_house_name"],
                    scheme_classification=classification_selection.display_name,
                    isin=row["isin_payout_or_growth"],
                    reason="stale_endpoint",
                    reason_detail=detail,
                    first_nav_date=row["first_observed_nav_date"],
                    latest_nav_date=row_end_date,
                    latest_nav=row["end_nav"],
                    endpoint_staleness_days=staleness,
                )
            )
            continue
        row_start_date = row["start_date"]
        row_start_nav = row["start_nav"]
        row_end_nav = row["end_nav"]
        if row_start_date is None or row_start_nav is None or row_end_nav is None:
            insufficient_count += 1
            excluded.append(
                _ExcludedScreenerFund(
                    amfi_scheme_code=row["amfi_scheme_code"],
                    scheme_name=row["scheme_name"],
                    fund_house_name=row["fund_house_name"],
                    scheme_classification=classification_selection.display_name,
                    isin=row["isin_payout_or_growth"],
                    reason="insufficient_history",
                    reason_detail=(
                        f"No valid start NAV exists from {window.target_start.isoformat()} through "
                        f"{window.start_deadline.isoformat()}"
                    ),
                    first_nav_date=row["first_observed_nav_date"],
                    latest_nav_date=row_end_date,
                    latest_nav=row_end_nav,
                    endpoint_staleness_days=(window.as_of_date - row_end_date).days,
                )
            )
            continue
        total_return, annualized_return = calculate_trailing_return(
            start_date=row_start_date,
            start_nav=row_start_nav,
            end_date=row_end_date,
            end_nav=row_end_nav,
            annualize=annualize,
        )
        payout_amount = payout_amounts.get(row["amfi_scheme_code"])
        payout_yield = None
        if option_type == "idcw":
            if payout_amount is None:
                no_payout_count += 1
                excluded.append(
                    _ExcludedScreenerFund(
                        amfi_scheme_code=row["amfi_scheme_code"],
                        scheme_name=row["scheme_name"],
                        fund_house_name=row["fund_house_name"],
                        scheme_classification=classification_selection.display_name,
                        isin=row["isin_payout_or_growth"],
                        reason="no_payout_events",
                        reason_detail=(
                            "No current canonical payout declaration has a record date after "
                            f"{window.target_start.isoformat()} and on or before "
                            f"{window.as_of_date.isoformat()}"
                        ),
                        first_nav_date=row["first_observed_nav_date"],
                        latest_nav_date=row_end_date,
                        latest_nav=row_end_nav,
                        endpoint_staleness_days=(window.as_of_date - row_end_date).days,
                    )
                )
                continue
            payout_yield = calculate_payout_yield(
                payout_amount_per_unit_inr=payout_amount,
                nav_value=row_end_nav,
            )
        metric = payout_yield if option_type == "idcw" else (
            annualized_return if annualize else total_return
        )
        assert metric is not None
        calculated.append(
            _CalculatedScreenerFund(
                metric=metric,
                amfi_scheme_code=row["amfi_scheme_code"],
                scheme_name=row["scheme_name"],
                fund_house_name=row["fund_house_name"],
                scheme_classification=classification_selection.display_name,
                isin=row["isin_payout_or_growth"],
                start_date=row_start_date,
                start_nav=row_start_nav,
                end_date=row_end_date,
                end_nav=row_end_nav,
                elapsed_days=(row_end_date - row_start_date).days,
                endpoint_staleness_days=(window.as_of_date - row_end_date).days,
                total_return_pct=total_return,
                annualized_return_pct=annualized_return,
                payout_amount_per_unit_inr=payout_amount,
                payout_yield_pct=payout_yield,
                payout_event_count=payout_event_counts.get(row["amfi_scheme_code"], 0),
                latest_payout_record_date=latest_payout_dates.get(row["amfi_scheme_code"]),
            )
        )
    if option_type == "idcw":
        idcw_rankings = rank_idcw_payouts(
            tuple(
                IDCWPayoutRankCandidate(
                    scheme_code=item.amfi_scheme_code,
                    payout_yield_pct=cast(Decimal, item.payout_yield_pct),
                    payout_event_count=item.payout_event_count,
                )
                for item in calculated
                if item.payout_yield_pct is not None
            )
        )
        ranking_by_code = {item.scheme_code: item for item in idcw_rankings}
        calculated = [
            replace(
                item,
                payout_yield_rank=ranking_by_code[item.amfi_scheme_code].payout_yield_rank,
                payout_frequency_rank=ranking_by_code[item.amfi_scheme_code].payout_frequency_rank,
                idcw_rank_score=ranking_by_code[item.amfi_scheme_code].combined_score,
            )
            for item in calculated
        ]
        order_by_code = {item.scheme_code: index for index, item in enumerate(idcw_rankings)}
        calculated.sort(key=lambda item: order_by_code[item.amfi_scheme_code])
    else:
        calculated.sort(key=lambda item: (-item.metric, item.amfi_scheme_code))
    excluded.sort(key=lambda item: (item.reason, item.scheme_name, item.amfi_scheme_code))
    page = calculated[offset : offset + limit]
    items = tuple(
        ScreenerFundResponse(
            rank=offset + index + 1,
            amfi_scheme_code=row.amfi_scheme_code,
            scheme_name=row.scheme_name,
            fund_house_name=row.fund_house_name,
            scheme_classification=row.scheme_classification,
            plan_type=plan_type,
            option_type=option_type,
            isin=row.isin,
            start_date=row.start_date,
            end_date=row.end_date,
            start_nav=row.start_nav,
            end_nav=row.end_nav,
            elapsed_days=row.elapsed_days,
            endpoint_staleness_days=row.endpoint_staleness_days,
            total_return_pct=row.total_return_pct,
            annualized_return_pct=row.annualized_return_pct,
            payout_amount_per_unit_inr=row.payout_amount_per_unit_inr,
            payout_yield_pct=row.payout_yield_pct,
            payout_event_count=row.payout_event_count,
            latest_payout_record_date=row.latest_payout_record_date,
            payout_yield_rank=row.payout_yield_rank,
            payout_frequency_rank=row.payout_frequency_rank,
            idcw_rank_score=row.idcw_rank_score,
        )
        for index, row in enumerate(page)
    )
    excluded_page = tuple(
        ScreenerExclusionResponse(
            amfi_scheme_code=row.amfi_scheme_code,
            scheme_name=row.scheme_name,
            fund_house_name=row.fund_house_name,
            scheme_classification=row.scheme_classification,
            plan_type=plan_type,
            option_type=option_type,
            isin=row.isin,
            reason=row.reason,
            reason_detail=row.reason_detail,
            first_nav_date=row.first_nav_date,
            latest_nav_date=row.latest_nav_date,
            latest_nav=row.latest_nav,
            endpoint_staleness_days=row.endpoint_staleness_days,
        )
        for row in excluded[exclusion_offset : exclusion_offset + exclusion_limit]
    )
    return FundScreenerResponse(
        classification_id=classification_selection.selection_id,
        classification=classification_selection.display_name,
        classification_mapping_version=classification_selection.mapping_version,
        items=items,
        total=len(calculated),
        limit=limit,
        offset=offset,
        as_of_date=window.as_of_date,
        target_start_date=window.target_start,
        horizon=horizon,
        ranking_metric=(
            "payout_yield_frequency_score"
            if option_type == "idcw"
            else "annualized_return_pct" if annualize else "total_return_pct"
        ),
        return_basis="nav_only",
        distribution_treatment=(
            "record_date_payout_yield" if option_type == "idcw" else "excluded"
        ),
        day_count_convention="actual/365",
        ranking_method=(IDCW_RANKING_METHOD if option_type == "idcw" else None),
        endpoint_tolerance_days=endpoint_tolerance_days,
        candidate_options=len(rows),
        excluded_stale_endpoint=stale_count,
        excluded_insufficient_history=insufficient_count,
        excluded_no_payout_events=no_payout_count,
        excluded_items=excluded_page,
        exclusion_limit=exclusion_limit,
        exclusion_offset=exclusion_offset,
    )


@router.get("/fund-comparison", response_model=FundComparisonResponse)
def compare_funds(
    session: DatabaseSession,
    scheme_code: Annotated[list[str], Query(min_length=1, max_length=5)],
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
) -> FundComparisonResponse:
    unique_codes = tuple(dict.fromkeys(scheme_code))
    if len(unique_codes) != len(scheme_code):
        raise HTTPException(status_code=422, detail="scheme_code values must be unique")
    if end_date <= start_date:
        raise HTTPException(status_code=422, detail="end_date must be after start_date")
    snapshot = current_scheme_snapshot().subquery()
    metadata_rows = (
        session.execute(select(snapshot).where(snapshot.c.amfi_scheme_code.in_(unique_codes)))
        .mappings()
        .all()
    )
    if len(metadata_rows) != len(unique_codes):
        found = {str(row["amfi_scheme_code"]) for row in metadata_rows}
        missing = sorted(set(unique_codes) - found)
        raise HTTPException(status_code=404, detail=f"AMFI scheme options not found: {missing}")
    raw_classifications = {str(row["scheme_classification"]) for row in metadata_rows}
    reference_rows = session.execute(
        select(
            SchemeClassificationAliasRecord.raw_classification,
            SchemeClassificationRecord.id,
            SchemeClassificationRecord.display_name,
        )
        .join(
            SchemeClassificationRecord,
            SchemeClassificationRecord.id == SchemeClassificationAliasRecord.classification_id,
        )
        .where(
            SchemeClassificationAliasRecord.source_provider == "amfi",
            SchemeClassificationAliasRecord.raw_classification.in_(raw_classifications),
            SchemeClassificationRecord.status == "active",
        )
    ).all()
    references_by_raw = {
        raw: (identifier, display_name) for raw, identifier, display_name in reference_rows
    }
    unmapped = sorted(raw_classifications - references_by_raw.keys())
    if unmapped:
        raise HTTPException(
            status_code=422,
            detail=f"comparison contains unmapped AMFI classifications: {unmapped}",
        )
    classification_ids = {identifier for identifier, _ in references_by_raw.values()}
    assignments = alias_assignments(session, classification_ids)
    selection_ids = {
        assignments[identifier].id if identifier in assignments else identifier
        for identifier in classification_ids
    }
    if len(selection_ids) != 1:
        raise HTTPException(
            status_code=422,
            detail="comparison requires scheme options from the same screener classification",
        )
    selection_id = next(iter(selection_ids))
    selection = resolve_classification_selection(
        session, classification_id=selection_id, classification=None
    )
    if selection is None:
        raise HTTPException(status_code=422, detail="comparison classification is inactive")
    canonical_display_name = selection.display_name
    nav_rows = session.execute(
        select(
            NavRevisionRecord.amfi_scheme_code,
            NavRevisionRecord.nav_date,
            NavRevisionRecord.nav_value,
        )
        .where(
            NavRevisionRecord.amfi_scheme_code.in_(unique_codes),
            NavRevisionRecord.is_current.is_(True),
            NavRevisionRecord.quality_status == "valid",
            NavRevisionRecord.nav_date >= start_date,
            NavRevisionRecord.nav_date <= end_date,
        )
        .order_by(NavRevisionRecord.amfi_scheme_code, NavRevisionRecord.nav_date)
    ).all()
    nav_by_code: dict[str, list[tuple[date, Decimal]]] = {code: [] for code in unique_codes}
    for code, nav_date, nav_value in nav_rows:
        nav_by_code[code].append((nav_date, nav_value))
    empty_codes = sorted(code for code, points in nav_by_code.items() if not points)
    if empty_codes:
        raise HTTPException(
            status_code=404,
            detail=f"No valid NAV observations in comparison range: {empty_codes}",
        )
    event_rows = session.execute(
        select(
            DistributionEventRecord.amfi_scheme_code,
            DistributionEventRecord.record_date,
            DistributionEventRevisionRecord.amount_per_unit_inr,
        )
        .join(
            DistributionEventRevisionRecord,
            DistributionEventRevisionRecord.distribution_event_id == DistributionEventRecord.id,
        )
        .where(
            DistributionEventRecord.amfi_scheme_code.in_(unique_codes),
            DistributionEventRecord.record_date >= start_date,
            DistributionEventRecord.record_date <= end_date,
            DistributionEventRevisionRecord.is_current.is_(True),
        )
        .order_by(DistributionEventRecord.amfi_scheme_code, DistributionEventRecord.record_date)
    ).all()
    events_by_code: dict[str, list[ComparisonDistributionEventResponse]] = {
        code: [] for code in unique_codes
    }
    for code, record_date, amount in event_rows:
        events_by_code[code].append(
            ComparisonDistributionEventResponse(
                record_date=record_date,
                amount_per_unit_inr=amount,
            )
        )
    metadata_by_code = {str(row["amfi_scheme_code"]): row for row in metadata_rows}
    series: list[FundComparisonSeriesResponse] = []
    for code in unique_codes:
        metadata = metadata_by_code[code]
        points = nav_by_code[code]
        first_nav = points[0][1]
        normalized_points = tuple(
            ComparisonNavPointResponse(
                nav_date=nav_date,
                nav_value=nav_value,
                normalized_value=nav_value / first_nav * Decimal("100"),
            )
            for nav_date, nav_value in points
        )
        series.append(
            FundComparisonSeriesResponse(
                amfi_scheme_code=code,
                scheme_name=str(metadata["scheme_name"]),
                fund_house_name=str(metadata["fund_house_name"]),
                scheme_classification=canonical_display_name,
                plan_type=str(metadata["plan_type"]),
                option_type=str(metadata["option_type"]),
                points=normalized_points,
                distributions=tuple(events_by_code[code]),
            )
        )
    return FundComparisonResponse(
        start_date=start_date,
        end_date=end_date,
        normalization_base="first_observation_100",
        return_basis="nav_only",
        distribution_markers="record_date",
        series=tuple(series),
    )


@router.get("/schemes", response_model=SchemeBrowserResponse)
def list_schemes(
    session: DatabaseSession,
    fund_house_id: str = Query(min_length=1),
    category_id: str | None = None,
    category: str | None = None,
    plan_type: Literal["direct", "regular", "unknown"] | None = None,
    option_type: Literal["growth", "idcw", "bonus", "unknown"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SchemeBrowserResponse:
    fund = _fund_or_404(fund_house_id, session)
    snapshot = current_scheme_snapshot(fund.mutual_fund_name).subquery()
    filters: list[ColumnElement[bool]] = []
    if category_id is not None or category is not None:
        classification_selection = _classification_reference_or_422(
            session, classification_id=category_id, classification=category
        )
        category_aliases = select(SchemeClassificationAliasRecord.raw_classification).where(
            SchemeClassificationAliasRecord.source_provider == "amfi",
            SchemeClassificationAliasRecord.classification_id.in_(
                classification_selection.classification_ids
            ),
        )
        filters.append(snapshot.c.scheme_classification.in_(category_aliases))
    if plan_type is not None:
        filters.append(snapshot.c.plan_type == plan_type)
    if option_type is not None:
        filters.append(snapshot.c.option_type == option_type)
    normalized_search = search.strip() if search else None
    if normalized_search:
        pattern = f"%{normalized_search.lower()}%"
        filters.append(
            func.lower(snapshot.c.scheme_name).like(pattern)
            | func.lower(snapshot.c.amfi_scheme_code).like(pattern)
            | func.lower(func.coalesce(snapshot.c.isin_payout_or_growth, "")).like(pattern)
            | func.lower(func.coalesce(snapshot.c.isin_reinvestment, "")).like(pattern)
        )
    filtered = select(snapshot)
    if filters:
        filtered = filtered.where(and_(*filters))
    filtered_snapshot = filtered.subquery()
    total = session.scalar(select(func.count()).select_from(filtered_snapshot)) or 0
    rows = session.execute(
        select(filtered_snapshot)
        .order_by(filtered_snapshot.c.scheme_name, filtered_snapshot.c.amfi_scheme_code)
        .limit(limit)
        .offset(offset)
    ).mappings()
    return SchemeBrowserResponse(
        items=tuple(SchemeBrowserItemResponse.model_validate(row) for row in rows),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/schemes/{amfi_scheme_code}/performance",
    response_model=SchemePerformanceResponse,
)
def get_scheme_performance(
    amfi_scheme_code: str, session: DatabaseSession
) -> SchemePerformanceResponse:
    _scheme_or_404(amfi_scheme_code, session)
    rows = session.execute(
        select(NavRevisionRecord.nav_date, NavRevisionRecord.nav_value)
        .where(
            NavRevisionRecord.amfi_scheme_code == amfi_scheme_code,
            NavRevisionRecord.is_current.is_(True),
            NavRevisionRecord.quality_status == "valid",
        )
        .order_by(NavRevisionRecord.nav_date)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No valid NAV observations found")
    result = calculate_nav_performance(
        tuple(NavPoint(nav_date=row.nav_date, nav_value=row.nav_value) for row in rows)
    )
    return SchemePerformanceResponse(
        amfi_scheme_code=amfi_scheme_code,
        return_basis="nav_only",
        distribution_treatment="excluded",
        day_count_convention="actual/365",
        rolling_start_rule=(
            "first valid NAV on or after each calendar anniversary, within 7 calendar days"
        ),
        observation_count=result.observation_count,
        since_inception=NavReturnResponse.from_result(result.since_inception),
        rolling_returns=tuple(
            RollingReturnSummaryResponse.from_result(item) for item in result.rolling_returns
        ),
        drawdown=DrawdownSummaryResponse.from_result(result.drawdown),
    )


@router.get(
    "/schemes/{amfi_scheme_code}/distributions",
    response_model=DistributionEventBrowserResponse,
)
def list_scheme_distributions(
    amfi_scheme_code: str,
    session: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DistributionEventBrowserResponse:
    _scheme_or_404(amfi_scheme_code, session)
    total = (
        session.scalar(
            select(func.count())
            .select_from(DistributionEventRecord)
            .where(DistributionEventRecord.amfi_scheme_code == amfi_scheme_code)
        )
        or 0
    )
    events = session.scalars(
        select(DistributionEventRecord)
        .where(DistributionEventRecord.amfi_scheme_code == amfi_scheme_code)
        .order_by(DistributionEventRecord.record_date.desc(), DistributionEventRecord.id)
        .limit(limit)
        .offset(offset)
    ).all()
    event_ids = tuple(event.id for event in events)
    revisions = (
        session.scalars(
            select(DistributionEventRevisionRecord)
            .where(DistributionEventRevisionRecord.distribution_event_id.in_(event_ids))
            .order_by(
                DistributionEventRevisionRecord.distribution_event_id,
                DistributionEventRevisionRecord.revision_number.desc(),
            )
        ).all()
        if event_ids
        else ()
    )
    revision_ids = tuple(revision.id for revision in revisions)
    source_rows = (
        session.execute(
            select(
                DistributionEventRevisionSourceRecord.distribution_event_revision_id,
                AmfiDistributionRecord,
                IngestionBatchRecord,
                SourceArtifactRecord.sha256,
            )
            .join(
                AmfiDistributionRecord,
                AmfiDistributionRecord.id
                == DistributionEventRevisionSourceRecord.source_distribution_record_id,
            )
            .join(
                AmfiDistributionRecordSource,
                AmfiDistributionRecordSource.distribution_record_id == AmfiDistributionRecord.id,
            )
            .join(
                IngestionBatchRecord,
                IngestionBatchRecord.id == AmfiDistributionRecordSource.ingestion_batch_id,
            )
            .outerjoin(
                SourceArtifactRecord, SourceArtifactRecord.id == IngestionBatchRecord.artifact_id
            )
            .where(
                DistributionEventRevisionSourceRecord.distribution_event_revision_id.in_(
                    revision_ids
                )
            )
            .order_by(
                DistributionEventRevisionSourceRecord.distribution_event_revision_id,
                IngestionBatchRecord.started_at,
                IngestionBatchRecord.id,
            )
        ).all()
        if revision_ids
        else ()
    )
    provenance_by_revision: dict[str, list[DistributionSourceProvenanceResponse]] = {}
    for revision_id, source, batch, artifact_sha256 in source_rows:
        provenance_by_revision.setdefault(revision_id, []).append(
            DistributionSourceProvenanceResponse(
                provider="amfi",
                source_kind="amfi_distribution_api",
                source_record_id=source.id,
                mutual_fund_id=source.mutual_fund_id,
                source_scheme_id=source.source_scheme_id,
                source_option_id=source.source_option_id,
                scheme_name=source.scheme_name,
                nav_name=source.nav_name,
                record_date=source.record_date,
                raw_source_value=source.raw_source_value,
                source_unit=source.source_unit,
                source_content_signature=source.content_signature,
                ingestion_batch_id=batch.id,
                source_url=batch.final_url or batch.source_url,
                retrieved_at=batch.started_at,
                parser_version=batch.parser_version,
                artifact_sha256=artifact_sha256,
            )
        )
    official_source_rows = (
        session.execute(
            select(
                DistributionEventRevisionOfficialSourceRecord.distribution_event_revision_id,
                OfficialDistributionRecord,
                IngestionBatchRecord,
                SourceArtifactRecord.sha256,
                OfficialDistributionRecordSource.identity_batch_id,
            )
            .join(
                OfficialDistributionRecord,
                OfficialDistributionRecord.id
                == DistributionEventRevisionOfficialSourceRecord.official_distribution_record_id,
            )
            .join(
                OfficialDistributionRecordSource,
                OfficialDistributionRecordSource.distribution_record_id
                == OfficialDistributionRecord.id,
            )
            .join(
                IngestionBatchRecord,
                IngestionBatchRecord.id == OfficialDistributionRecordSource.notice_batch_id,
            )
            .outerjoin(
                SourceArtifactRecord, SourceArtifactRecord.id == IngestionBatchRecord.artifact_id
            )
            .where(
                DistributionEventRevisionOfficialSourceRecord.distribution_event_revision_id.in_(
                    revision_ids
                )
            )
            .order_by(
                DistributionEventRevisionOfficialSourceRecord.distribution_event_revision_id,
                IngestionBatchRecord.started_at,
                IngestionBatchRecord.id,
            )
        ).all()
        if revision_ids
        else ()
    )
    identity_batch_ids = tuple(row.identity_batch_id for row in official_source_rows)
    identity_batches = {
        batch.id: batch
        for batch in (
            session.scalars(
                select(IngestionBatchRecord).where(IngestionBatchRecord.id.in_(identity_batch_ids))
            ).all()
            if identity_batch_ids
            else ()
        )
    }
    identity_artifact_ids = tuple(
        batch.artifact_id for batch in identity_batches.values() if batch.artifact_id is not None
    )
    identity_artifacts = {
        artifact.id: artifact.sha256
        for artifact in (
            session.scalars(
                select(SourceArtifactRecord).where(
                    SourceArtifactRecord.id.in_(identity_artifact_ids)
                )
            ).all()
            if identity_artifact_ids
            else ()
        )
    }
    for revision_id, source, batch, artifact_sha256, identity_batch_id in official_source_rows:
        identity_batch = identity_batches[identity_batch_id]
        provenance_by_revision.setdefault(revision_id, []).append(
            DistributionSourceProvenanceResponse(
                provider=source.provider,
                source_kind="amc_distribution_notice",
                source_record_id=source.id,
                mutual_fund_id=None,
                source_scheme_id=None,
                source_option_id=source.amfi_scheme_code,
                scheme_name=source.source_scheme_name,
                nav_name=(
                    f"{source.source_scheme_name} - {source.source_plan_type.title()} Plan - "
                    f"{source.source_option_label}"
                ),
                record_date=source.record_date,
                raw_source_value=source.raw_amount_per_unit_inr,
                source_unit=source.source_unit,
                source_content_signature=source.content_signature,
                ingestion_batch_id=batch.id,
                source_url=batch.final_url or batch.source_url,
                retrieved_at=batch.started_at,
                parser_version=batch.parser_version,
                artifact_sha256=artifact_sha256,
                identity_evidence_batch_id=identity_batch.id,
                identity_evidence_url=identity_batch.final_url or identity_batch.source_url,
                identity_artifact_sha256=(
                    identity_artifacts.get(identity_batch.artifact_id)
                    if identity_batch.artifact_id is not None
                    else None
                ),
            )
        )
    rta_source_rows = (
        session.execute(
            select(
                DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id,
                RtaDistributionRecord,
                RtaSchemeCaptureRecord,
                RtaSchemeMappingReviewRecord,
                IngestionBatchRecord,
                SourceArtifactRecord.sha256,
            )
            .join(
                RtaDistributionRecord,
                RtaDistributionRecord.id
                == DistributionEventRevisionRtaSourceRecord.rta_distribution_record_id,
            )
            .join(
                RtaSchemeCaptureRecord,
                RtaSchemeCaptureRecord.id == RtaDistributionRecord.scheme_capture_id,
            )
            .join(
                RtaSchemeMappingReviewRecord,
                RtaSchemeMappingReviewRecord.id
                == DistributionEventRevisionRtaSourceRecord.mapping_review_id,
            )
            .join(
                IngestionBatchRecord,
                IngestionBatchRecord.id == RtaSchemeCaptureRecord.ingestion_batch_id,
            )
            .outerjoin(
                SourceArtifactRecord, SourceArtifactRecord.id == IngestionBatchRecord.artifact_id
            )
            .where(
                DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id.in_(
                    revision_ids
                )
            )
            .order_by(
                DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id,
                RtaSchemeCaptureRecord.captured_at,
                RtaDistributionRecord.id,
            )
        ).all()
        if revision_ids
        else ()
    )
    for revision_id, source, capture, mapping, batch, artifact_sha256 in rta_source_rows:
        provenance_by_revision.setdefault(revision_id, []).append(
            DistributionSourceProvenanceResponse(
                provider=capture.provider,
                source_kind="rta_distribution_history",
                source_record_id=source.id,
                mutual_fund_id=None,
                source_scheme_id=capture.rta_fund_code,
                source_option_id=capture.rta_scheme_code,
                scheme_name=capture.source_scheme_name,
                nav_name=(
                    f"{capture.source_scheme_name} - "
                    f"{capture.plan_type.title()} - {capture.option_variant.title()}"
                ),
                record_date=source.record_date,
                raw_source_value=source.raw_individual_amount,
                source_unit=source.source_unit,
                source_content_signature=source.content_signature,
                ingestion_batch_id=batch.id,
                source_url=capture.source_url,
                retrieved_at=capture.captured_at,
                parser_version=batch.parser_version,
                artifact_sha256=artifact_sha256,
                identity_evidence_batch_id=batch.id,
                identity_evidence_url=capture.source_url,
                identity_artifact_sha256=artifact_sha256,
                identity_evidence_details=mapping.evidence_details,
            )
        )
    advisorkhoj_source_rows = (
        session.execute(
            select(
                AdvisorkhojRevisionSource.distribution_event_revision_id,
                AdvisorkhojDistributionRecord,
                AdvisorkhojSchemeCaptureRecord,
                AdvisorkhojCatalogSchemeRecord,
                AdvisorkhojSchemeMappingReviewRecord,
                IngestionBatchRecord,
                SourceArtifactRecord.sha256,
            )
            .join(
                AdvisorkhojDistributionRecord,
                AdvisorkhojDistributionRecord.id
                == AdvisorkhojRevisionSource.advisorkhoj_distribution_record_id,
            )
            .join(
                AdvisorkhojSchemeCaptureRecord,
                AdvisorkhojSchemeCaptureRecord.id
                == AdvisorkhojDistributionRecord.scheme_capture_id,
            )
            .join(
                AdvisorkhojCatalogSchemeRecord,
                AdvisorkhojCatalogSchemeRecord.id
                == AdvisorkhojSchemeCaptureRecord.catalog_scheme_id,
            )
            .join(
                AdvisorkhojSchemeMappingReviewRecord,
                AdvisorkhojSchemeMappingReviewRecord.id
                == AdvisorkhojRevisionSource.mapping_review_id,
            )
            .join(
                IngestionBatchRecord,
                IngestionBatchRecord.id == AdvisorkhojSchemeCaptureRecord.ingestion_batch_id,
            )
            .outerjoin(
                SourceArtifactRecord, SourceArtifactRecord.id == IngestionBatchRecord.artifact_id
            )
            .where(AdvisorkhojRevisionSource.distribution_event_revision_id.in_(revision_ids))
            .order_by(
                AdvisorkhojRevisionSource.distribution_event_revision_id,
                AdvisorkhojSchemeCaptureRecord.captured_at,
                AdvisorkhojDistributionRecord.id,
            )
        ).all()
        if revision_ids
        else ()
    )
    for (
        revision_id,
        source,
        capture,
        catalog_scheme,
        mapping,
        batch,
        artifact_sha256,
    ) in advisorkhoj_source_rows:
        provenance_by_revision.setdefault(revision_id, []).append(
            DistributionSourceProvenanceResponse(
                provider="advisorkhoj",
                source_kind="third_party_distribution_history",
                source_record_id=source.id,
                mutual_fund_id=None,
                source_scheme_id=None,
                source_option_id=catalog_scheme.id,
                scheme_name=catalog_scheme.scheme_name,
                nav_name=(
                    f"{catalog_scheme.scheme_name} - {capture.plan_type.title()} - "
                    f"{capture.option_variant.title()} - {capture.source_frequency.title()}"
                ),
                record_date=source.record_date,
                raw_source_value=source.raw_amount_per_unit_inr,
                source_unit="inr_per_unit",
                source_content_signature=source.content_signature,
                ingestion_batch_id=batch.id,
                source_url=capture.source_url,
                retrieved_at=capture.captured_at,
                parser_version=batch.parser_version,
                artifact_sha256=artifact_sha256,
                identity_evidence_batch_id=batch.id,
                identity_evidence_url=capture.source_url,
                identity_artifact_sha256=artifact_sha256,
                identity_evidence_details=mapping.evidence_details,
            )
        )
    revisions_by_event: dict[str, list[DistributionEventRevisionResponse]] = {}
    for revision in revisions:
        revisions_by_event.setdefault(revision.distribution_event_id, []).append(
            DistributionEventRevisionResponse(
                revision_id=revision.id,
                revision_number=revision.revision_number,
                amount_per_unit_inr=revision.amount_per_unit_inr,
                is_current=revision.is_current,
                normalization_version=revision.normalization_version,
                normalized_at=revision.normalized_at,
                sources=tuple(provenance_by_revision.get(revision.id, ())),
            )
        )
    return DistributionEventBrowserResponse(
        items=tuple(
            DistributionEventResponse(
                event_id=event.id,
                record_date=event.record_date,
                event_type="idcw_cash",
                revisions=tuple(revisions_by_event.get(event.id, ())),
            )
            for event in events
        ),
        total=total,
        limit=limit,
        offset=offset,
        coverage=_latest_distribution_coverage(amfi_scheme_code, session),
    )


def _fund_or_404(mutual_fund_id: str, session: DatabaseSession) -> AmfiFundRecord:
    fund = session.get(AmfiFundRecord, mutual_fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="AMFI fund house not found")
    return fund


def _screener_window_or_error(
    session: DatabaseSession,
    *,
    horizon: ScreenerHorizon,
    as_of: date | None,
    endpoint_tolerance_days: int,
) -> _ScreenerWindow:
    dataset_stats = session.get(NavDatasetStatsRecord, 1)
    dataset_date = dataset_stats.latest_valid_nav_date if dataset_stats else None
    if dataset_date is None:
        raise HTTPException(status_code=404, detail="No valid NAV dataset is available")
    as_of_date = as_of or dataset_date
    if as_of_date > dataset_date:
        raise HTTPException(
            status_code=422,
            detail=f"as_of cannot exceed latest valid NAV date {dataset_date.isoformat()}",
        )
    target_start = horizon_start_date(as_of_date, horizon)
    return _ScreenerWindow(
        as_of_date=as_of_date,
        target_start=target_start,
        start_deadline=target_start + timedelta(days=endpoint_tolerance_days),
        stale_cutoff=as_of_date - timedelta(days=endpoint_tolerance_days),
    )


def _validate_screener_mode(
    *, option_type: Literal["growth", "idcw"], horizon: ScreenerHorizon
) -> None:
    if option_type == "idcw" and horizon != "1y":
        raise HTTPException(
            status_code=422,
            detail="IDCW payout-yield screening supports only the trailing 12-month horizon",
        )


def _classification_reference_or_422(
    session: DatabaseSession,
    *,
    classification_id: str | None,
    classification: str | None,
) -> ClassificationSelection:
    if (classification_id is None) == (classification is None):
        raise HTTPException(
            status_code=422,
            detail="provide exactly one of classification_id or classification",
        )
    selection = resolve_classification_selection(
        session,
        classification_id=classification_id,
        classification=classification,
    )
    if selection is None:
        raise HTTPException(status_code=422, detail="unknown or inactive scheme classification")
    return selection


def _classification_alias_response(
    alias: ScreenerClassificationAliasRecord,
    classification_ids: list[str] | tuple[str, ...],
) -> ClassificationAliasResponse:
    return ClassificationAliasResponse(
        id=alias.id,
        name=alias.name,
        structure_type=cast(SchemeStructure, alias.structure_type),
        status=cast(Literal["active", "inactive"], alias.status),
        version=alias.version,
        classification_ids=tuple(sorted(classification_ids)),
        updated_at=alias.updated_at,
    )


def _scheme_or_404(amfi_scheme_code: str, session: DatabaseSession) -> SchemeOptionRecord:
    scheme = session.get(SchemeOptionRecord, amfi_scheme_code)
    if scheme is None:
        raise HTTPException(status_code=404, detail="AMFI scheme option not found")
    return scheme


def _latest_distribution_coverage(
    amfi_scheme_code: str, session: DatabaseSession
) -> DistributionCoverageResponse | None:
    row = session.execute(
        select(DistributionCoverageAssessmentRecord, DistributionCoverageRunRecord)
        .join(
            DistributionCoverageRunRecord,
            DistributionCoverageRunRecord.id
            == DistributionCoverageAssessmentRecord.coverage_run_id,
        )
        .where(
            DistributionCoverageAssessmentRecord.amfi_scheme_code == amfi_scheme_code,
            DistributionCoverageRunRecord.status == "completed",
        )
        .order_by(
            DistributionCoverageRunRecord.completed_at.desc(),
            DistributionCoverageRunRecord.id.desc(),
        )
        .limit(1)
    ).first()
    if row is None:
        return None
    assessment, run = row
    return DistributionCoverageResponse(
        assessment_run_id=run.id,
        assessment_version=run.assessment_version,
        coverage_status=assessment.coverage_status,
        source_row_count=assessment.source_row_count,
        canonical_source_row_count=assessment.canonical_source_row_count,
        blocked_source_row_count=assessment.blocked_source_row_count,
        canonical_event_count=assessment.canonical_event_count,
        first_source_record_date=assessment.first_source_record_date,
        last_source_record_date=assessment.last_source_record_date,
        assessed_at=assessment.assessed_at,
    )
