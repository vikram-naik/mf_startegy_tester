from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AmfiFundRecord,
    NavSyncCheckpointRecord,
    NavSyncCoverageRecord,
    NavSyncRunRecord,
    utc_now,
)
from mf_strategy_tester.ingestion.amfi import (
    AmfiFundListParser,
    AmfiNavParser,
    SchemeType,
    current_nav_request,
    fund_list_request,
    historical_nav_request,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.errors import SourceTemporarilyUnavailableError
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.nav_publication import (
    FundCatalogPublisher,
    NavPublicationStats,
    NormalizedNavPublisher,
)
from mf_strategy_tester.services.source_ingestion import (
    IngestionResult,
    SourceIngestionService,
    SourceRequest,
)

logger = logging.getLogger(__name__)

EARLIEST_AMFI_NAV_DATE = date(2006, 4, 1)
MAX_AMFI_REQUEST_DAYS = 90
AMFI_TRANSIENT_RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0, 20.0)


@dataclass(frozen=True)
class NavSyncResult:
    run_id: str
    status: str
    mode: str
    requested_start_date: str
    requested_end_date: str
    funds_total: int
    funds_completed: int
    chunks_completed: int
    rows_received: int
    rows_inserted: int
    rows_unchanged: int
    rows_revised: int
    rows_quarantined: int
    latest_nav_date_found: str | None


class NavSyncService:
    """Resumable, idempotent AMFI historical NAV synchronization."""

    def __init__(
        self,
        session: Session,
        ingestion_service: SourceIngestionService,
        ingestion_repository: IngestionRepository,
        artifact_store: ArtifactStore,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session
        self._ingestion = ingestion_service
        self._repository = ingestion_repository
        self._artifacts = artifact_store
        self._sleep = sleep
        self._nav_publisher = NormalizedNavPublisher(session)
        self._fund_publisher = FundCatalogPublisher(session)

    def synchronize(
        self,
        *,
        mode: str,
        requested_start_date: date,
        requested_end_date: date,
        overlap_days: int = 7,
        fund_ids: frozenset[str] | None = None,
        include_current_feed: bool = True,
    ) -> NavSyncResult:
        if mode not in {"full", "incremental"}:
            raise ValueError("sync mode must be 'full' or 'incremental'")
        if requested_end_date < requested_start_date:
            raise ValueError("sync end date must be on or after start date")
        if overlap_days < 0:
            raise ValueError("overlap_days cannot be negative")
        stale_runs = self._session.scalars(
            select(NavSyncRunRecord).where(NavSyncRunRecord.status == "running")
        ).all()
        for stale_run in stale_runs:
            stale_run.status = "failed"
            stale_run.completed_at = utc_now()
            stale_run.error_details = "Interrupted before completion; superseded by a resumed run"
        self._session.commit()
        catalog_result = self._ingestion.ingest(fund_list_request())
        fund_records = AmfiFundListParser().parse_records(self._payload(catalog_result))
        self._fund_publisher.publish(fund_records, batch_id=catalog_result.batch_id)

        statement = select(AmfiFundRecord).where(AmfiFundRecord.is_active.is_(True))
        if fund_ids:
            statement = statement.where(AmfiFundRecord.mutual_fund_id.in_(fund_ids))
        funds = list(self._session.scalars(statement.order_by(AmfiFundRecord.mutual_fund_id)).all())
        if fund_ids and {fund.mutual_fund_id for fund in funds} != set(fund_ids):
            missing = sorted(set(fund_ids) - {fund.mutual_fund_id for fund in funds})
            raise ValueError(f"unknown or inactive AMFI fund IDs: {', '.join(missing)}")

        run = NavSyncRunRecord(
            mode=mode,
            status="running",
            requested_start_date=requested_start_date,
            requested_end_date=requested_end_date,
            overlap_days=overlap_days,
            funds_total=len(funds),
        )
        self._session.add(run)
        self._session.commit()
        try:
            for fund in funds:
                self._synchronize_fund(
                    run,
                    fund,
                    mode=mode,
                    requested_start_date=requested_start_date,
                    requested_end_date=requested_end_date,
                    overlap_days=overlap_days,
                )
            if include_current_feed:
                current_result = self._ingest_nav_request(current_nav_request())
                current_records = AmfiNavParser().parse_records(self._payload(current_result))
                stats = self._nav_publisher.publish(
                    current_records, batch_id=current_result.batch_id
                )
                self._accumulate(run, stats)
                run.chunks_completed += 1
            run.status = "completed"
            run.completed_at = utc_now()
            self._session.commit()
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed_run = self._session.get(NavSyncRunRecord, run.id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.completed_at = utc_now()
                error_message = str(error) or "interrupted by user"
                failed_run.error_details = f"{type(error).__name__}: {error_message}"[:4000]
                self._session.commit()
            raise
        return _result(run)

    def _synchronize_fund(
        self,
        run: NavSyncRunRecord,
        fund: AmfiFundRecord,
        *,
        mode: str,
        requested_start_date: date,
        requested_end_date: date,
        overlap_days: int,
    ) -> None:
        checkpoint = self._session.get(NavSyncCheckpointRecord, fund.mutual_fund_id)
        chunk_start = requested_start_date
        if checkpoint is not None and mode == "incremental":
            resume_date = checkpoint.completed_through + timedelta(days=1)
            if overlap_days:
                resume_date = checkpoint.completed_through - timedelta(days=overlap_days - 1)
            chunk_start = max(requested_start_date, resume_date)
        while chunk_start <= requested_end_date:
            uncovered_end = requested_end_date
            if mode == "full":
                uncovered = self._next_uncovered_interval(
                    fund.mutual_fund_id, chunk_start, requested_end_date
                )
                if uncovered is None:
                    break
                chunk_start, uncovered_end = uncovered
            chunk_end = min(
                uncovered_end,
                chunk_start + timedelta(days=MAX_AMFI_REQUEST_DAYS - 1),
            )
            request = historical_nav_request(
                mutual_fund_id=fund.mutual_fund_id,
                from_date=chunk_start,
                to_date=chunk_end,
                scheme_type=SchemeType.ALL,
            )
            ingestion_result = self._ingest_nav_request(request)
            records = AmfiNavParser(allow_empty_report=True).parse_records(
                self._payload(ingestion_result)
            )
            stats = self._nav_publisher.publish(records, batch_id=ingestion_result.batch_id)
            self._update_checkpoint(
                fund.mutual_fund_id,
                chunk_end,
                stats.latest_nav_date,
                ingestion_result.batch_id,
            )
            self._record_coverage(
                fund.mutual_fund_id, chunk_start, chunk_end, ingestion_result.batch_id
            )
            self._accumulate(run, stats)
            run.chunks_completed += 1
            self._session.commit()
            logger.info(
                "nav_sync_chunk_completed",
                extra={
                    "event_data": {
                        "run_id": run.id,
                        "mutual_fund_id": fund.mutual_fund_id,
                        "from_date": chunk_start.isoformat(),
                        "to_date": chunk_end.isoformat(),
                        "rows_received": stats.rows_received,
                        "rows_revised": stats.rows_revised,
                    }
                },
            )
            chunk_start = chunk_end + timedelta(days=1)
        run.funds_completed += 1
        self._session.commit()

    def _ingest_nav_request(self, request: SourceRequest) -> IngestionResult:
        for attempt, delay_seconds in enumerate(
            (*AMFI_TRANSIENT_RETRY_DELAYS_SECONDS, None), start=1
        ):
            try:
                return self._ingestion.ingest(request)
            except SourceTemporarilyUnavailableError:
                if delay_seconds is None:
                    raise
                logger.warning(
                    "amfi_nav_transient_response_retry",
                    extra={
                        "event_data": {
                            "attempt": attempt,
                            "retry_delay_seconds": delay_seconds,
                            "source_type": request.source_type.value,
                            "request_parameters": request.parameters,
                        }
                    },
                )
                self._sleep(delay_seconds)
        raise AssertionError("AMFI transient retry loop terminated unexpectedly")

    def _next_uncovered_interval(
        self, fund_id: str, requested_start: date, requested_end: date
    ) -> tuple[date, date] | None:
        ranges = self._session.scalars(
            select(NavSyncCoverageRecord)
            .where(
                NavSyncCoverageRecord.mutual_fund_id == fund_id,
                NavSyncCoverageRecord.end_date >= requested_start,
                NavSyncCoverageRecord.start_date <= requested_end,
            )
            .order_by(NavSyncCoverageRecord.start_date)
        ).all()
        cursor = requested_start
        for covered in ranges:
            if covered.start_date > cursor:
                return cursor, min(requested_end, covered.start_date - timedelta(days=1))
            cursor = max(cursor, covered.end_date + timedelta(days=1))
            if cursor > requested_end:
                return None
        return (cursor, requested_end) if cursor <= requested_end else None

    def _record_coverage(
        self, fund_id: str, start_date: date, end_date: date, batch_id: str
    ) -> None:
        adjacent = self._session.scalars(
            select(NavSyncCoverageRecord).where(
                NavSyncCoverageRecord.mutual_fund_id == fund_id,
                NavSyncCoverageRecord.end_date >= start_date - timedelta(days=1),
                NavSyncCoverageRecord.start_date <= end_date + timedelta(days=1),
            )
        ).all()
        merged_start = min([start_date, *(item.start_date for item in adjacent)])
        merged_end = max([end_date, *(item.end_date for item in adjacent)])
        for item in adjacent:
            self._session.delete(item)
        self._session.add(
            NavSyncCoverageRecord(
                mutual_fund_id=fund_id,
                start_date=merged_start,
                end_date=merged_end,
                last_batch_id=batch_id,
            )
        )

    def _update_checkpoint(
        self,
        fund_id: str,
        completed_through: date,
        latest_nav_date: date | None,
        batch_id: str,
    ) -> None:
        checkpoint = self._session.get(NavSyncCheckpointRecord, fund_id)
        if checkpoint is None:
            self._session.add(
                NavSyncCheckpointRecord(
                    mutual_fund_id=fund_id,
                    completed_through=completed_through,
                    latest_nav_date_found=latest_nav_date,
                    last_batch_id=batch_id,
                )
            )
            return
        checkpoint.completed_through = max(checkpoint.completed_through, completed_through)
        if latest_nav_date is not None:
            checkpoint.latest_nav_date_found = max(
                checkpoint.latest_nav_date_found or latest_nav_date, latest_nav_date
            )
        checkpoint.last_batch_id = batch_id
        checkpoint.updated_at = utc_now()

    @staticmethod
    def _accumulate(run: NavSyncRunRecord, stats: NavPublicationStats) -> None:
        run.rows_received += stats.rows_received
        run.rows_inserted += stats.rows_inserted
        run.rows_unchanged += stats.rows_unchanged
        run.rows_revised += stats.rows_revised
        run.rows_quarantined += stats.rows_quarantined
        if stats.latest_nav_date is not None:
            run.latest_nav_date_found = max(
                run.latest_nav_date_found or stats.latest_nav_date, stats.latest_nav_date
            )

    def _payload(self, result: IngestionResult) -> bytes:
        batch = self._repository.get_batch(result.batch_id)
        if batch.artifact is None:
            raise RuntimeError(f"completed ingestion batch {batch.id} has no artifact")
        return self._artifacts.read(
            batch.artifact.storage_path, expected_sha256=batch.artifact.sha256
        )


def _result(run: NavSyncRunRecord) -> NavSyncResult:
    return NavSyncResult(
        run_id=run.id,
        status=run.status,
        mode=run.mode,
        requested_start_date=run.requested_start_date.isoformat(),
        requested_end_date=run.requested_end_date.isoformat(),
        funds_total=run.funds_total,
        funds_completed=run.funds_completed,
        chunks_completed=run.chunks_completed,
        rows_received=run.rows_received,
        rows_inserted=run.rows_inserted,
        rows_unchanged=run.rows_unchanged,
        rows_revised=run.rows_revised,
        rows_quarantined=run.rows_quarantined,
        latest_nav_date_found=(
            run.latest_nav_date_found.isoformat() if run.latest_nav_date_found else None
        ),
    )
