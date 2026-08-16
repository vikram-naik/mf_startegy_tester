from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Select, and_, distinct, func, select

from mf_strategy_tester.api.dependencies import DatabaseSession
from mf_strategy_tester.api.schemas import (
    DataCoverageResponse,
    FundHouseResponse,
    NavSyncRunResponse,
    SchemeBrowserItemResponse,
    SchemeBrowserResponse,
    SchemeCategoryResponse,
)
from mf_strategy_tester.db.models import (
    AmfiFundRecord,
    NavDatasetStatsRecord,
    NavRevisionRecord,
    NavSyncCheckpointRecord,
    NavSyncCoverageRecord,
    NavSyncRunRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
)
from mf_strategy_tester.services.nav_sync import EARLIEST_AMFI_NAV_DATE

router = APIRouter(prefix="/data", tags=["data"])


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
    scheme_snapshot = _scheme_snapshot().subquery()
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
    snapshot = _scheme_snapshot(fund.mutual_fund_name).subquery()
    rows = session.execute(
        select(snapshot.c.scheme_classification, func.count())
        .group_by(snapshot.c.scheme_classification)
        .order_by(snapshot.c.scheme_classification)
    ).all()
    return tuple(
        SchemeCategoryResponse(classification=classification, scheme_options=count)
        for classification, count in rows
    )


@router.get("/schemes", response_model=SchemeBrowserResponse)
def list_schemes(
    session: DatabaseSession,
    fund_house_id: str = Query(min_length=1),
    category: str | None = None,
    plan_type: Literal["direct", "regular", "unknown"] | None = None,
    option_type: Literal["growth", "idcw", "bonus", "unknown"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SchemeBrowserResponse:
    fund = _fund_or_404(fund_house_id, session)
    snapshot = _scheme_snapshot(fund.mutual_fund_name).subquery()
    filters = []
    if category is not None:
        filters.append(snapshot.c.scheme_classification == category)
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


def _fund_or_404(mutual_fund_id: str, session: DatabaseSession) -> AmfiFundRecord:
    fund = session.get(AmfiFundRecord, mutual_fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="AMFI fund house not found")
    return fund


def _scheme_snapshot(fund_house_name: str | None = None) -> Select[tuple[object, ...]]:
    revision_match = (
        (NavRevisionRecord.amfi_scheme_code == SchemeOptionRecord.amfi_scheme_code)
        & (NavRevisionRecord.nav_date == SchemeOptionRecord.last_observed_nav_date)
        & NavRevisionRecord.is_current.is_(True)
    )
    latest_metadata_id = (
        select(NavRevisionRecord.metadata_version_id)
        .where(revision_match)
        .correlate(SchemeOptionRecord)
        .scalar_subquery()
    )
    latest_nav_value = (
        select(NavRevisionRecord.nav_value)
        .where(revision_match)
        .correlate(SchemeOptionRecord)
        .scalar_subquery()
    )
    latest_quality_status = (
        select(NavRevisionRecord.quality_status)
        .where(revision_match)
        .correlate(SchemeOptionRecord)
        .scalar_subquery()
    )
    statement = select(
        SchemeOptionRecord.amfi_scheme_code.label("amfi_scheme_code"),
        SchemeMetadataVersionRecord.scheme_name.label("scheme_name"),
        SchemeMetadataVersionRecord.fund_house_name.label("fund_house_name"),
        SchemeMetadataVersionRecord.scheme_classification.label("scheme_classification"),
        SchemeMetadataVersionRecord.plan_type.label("plan_type"),
        SchemeMetadataVersionRecord.option_type.label("option_type"),
        SchemeMetadataVersionRecord.isin_payout_or_growth.label("isin_payout_or_growth"),
        SchemeMetadataVersionRecord.isin_reinvestment.label("isin_reinvestment"),
        SchemeOptionRecord.first_observed_nav_date.label("first_nav_date"),
        SchemeOptionRecord.last_observed_nav_date.label("latest_nav_date"),
        latest_nav_value.label("latest_nav_value"),
        latest_quality_status.label("quality_status"),
    ).join(
        SchemeMetadataVersionRecord,
        SchemeMetadataVersionRecord.id == latest_metadata_id,
    )
    if fund_house_name is not None:
        statement = statement.where(SchemeMetadataVersionRecord.fund_house_name == fund_house_name)
    return statement
