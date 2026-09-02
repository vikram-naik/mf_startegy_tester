from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Select, and_, distinct, func, select

from mf_strategy_tester.api.dependencies import DatabaseSession
from mf_strategy_tester.api.schemas import (
    DataCoverageResponse,
    DistributionCoverageResponse,
    DistributionEventBrowserResponse,
    DistributionEventResponse,
    DistributionEventRevisionResponse,
    DistributionSourceProvenanceResponse,
    DrawdownSummaryResponse,
    FundHouseResponse,
    NavReturnResponse,
    NavSyncRunResponse,
    RollingReturnSummaryResponse,
    SchemeBrowserItemResponse,
    SchemeBrowserResponse,
    SchemeCategoryResponse,
    SchemePerformanceResponse,
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
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    SourceArtifactRecord,
)
from mf_strategy_tester.db.models import (
    DistributionEventRevisionAdvisorkhojSourceRecord as AdvisorkhojRevisionSource,
)
from mf_strategy_tester.services.nav_performance import NavPoint, calculate_nav_performance
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
