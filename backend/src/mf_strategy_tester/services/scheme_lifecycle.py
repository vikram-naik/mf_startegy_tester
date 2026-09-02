from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from time import sleep
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AmfiFundRecord,
    AmfiSchemeDetailRecord,
    AmfiSchemeDetailRecordSource,
    AmfiSchemeListMembershipRecord,
    AmfiSchemeListSnapshotRecord,
    IngestionBatchRecord,
    SchemeLifecycleCheckpointRecord,
    SchemeLifecycleEventRecord,
    SchemeLifecycleEventSourceRecord,
    SchemeLifecycleIssueRecord,
    SchemeLifecycleSyncRunRecord,
    utc_now,
)
from mf_strategy_tester.ingestion.amfi import (
    AmfiSchemeDetailsParser,
    AmfiSchemeListParser,
    AmfiSourceRequest,
    SchemeDetailRecord,
    SchemeListRecord,
    scheme_details_request,
    scheme_list_request,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.source_ingestion import Downloader

logger = logging.getLogger(__name__)

LIFECYCLE_NORMALIZATION_VERSION = "amfi-scheme-lifecycle-2026.08.1"


@dataclass(frozen=True)
class SchemeLifecycleSyncResult:
    run_id: str
    mode: str
    status: str
    funds_total: int
    funds_completed: int
    funds_failed: int
    families_total: int
    families_completed: int
    families_skipped: int
    families_failed: int
    detail_rows_received: int
    detail_rows_inserted: int
    detail_rows_unchanged: int
    detail_rows_rejected: int
    issue_counts: dict[str, int]


@dataclass(frozen=True)
class SchemeLifecycleCoverageReport:
    normalization_version: str
    active_funds: int
    funds_with_scheme_list_snapshot: int
    latest_catalog_families: int
    detail_checkpoints: int
    families_with_launch_event: int
    families_with_conflicting_launch_dates: int
    latest_catalog_families_without_detail: int
    lifecycle_event_counts: dict[str, int]
    issue_counts: dict[str, int]
    latest_sync_run: dict[str, object] | None


class _CaptureError(RuntimeError):
    def __init__(self, batch_id: str, error: Exception) -> None:
        super().__init__(f"{type(error).__name__}: {error!s}")
        self.batch_id = batch_id


class SchemeLifecycleSyncService:
    """Acquire explicit AMFI family facts without inferring closure or merger events."""

    def __init__(
        self,
        session: Session,
        repository: IngestionRepository,
        artifacts: ArtifactStore,
        downloader: Downloader,
        request_delay_seconds: float = 0,
        sleep_function: Callable[[float], None] = sleep,
    ) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds cannot be negative")
        self._session = session
        self._repository = repository
        self._artifacts = artifacts
        self._downloader = downloader
        self._request_delay_seconds = request_delay_seconds
        self._sleep = sleep_function

    def sync(
        self, *, mode: str = "full", fund_ids: tuple[str, ...] | None = None
    ) -> SchemeLifecycleSyncResult:
        if mode not in {"full", "refresh"}:
            raise ValueError("scheme lifecycle mode must be full or refresh")
        self._fail_stale_runs()
        funds = self._funds(fund_ids)
        run = SchemeLifecycleSyncRunRecord(
            mode=mode,
            status="running",
            funds_total=len(funds),
        )
        self._session.add(run)
        self._session.commit()
        issue_counts: dict[str, int] = {}
        try:
            for fund in funds:
                try:
                    list_rows, list_batch = self._capture_scheme_list(fund.mutual_fund_id)
                except _CaptureError as error:
                    self._record_issue(
                        run,
                        fund.mutual_fund_id,
                        None,
                        error.batch_id,
                        "scheme_list_failure",
                        str(error),
                        issue_counts,
                    )
                    run.funds_failed += 1
                    self._session.commit()
                    continue
                previous_memberships = self._latest_memberships(fund.mutual_fund_id)
                self._persist_list_snapshot(fund.mutual_fund_id, list_rows, list_batch)
                self._record_catalog_changes(
                    run,
                    fund.mutual_fund_id,
                    previous_memberships,
                    list_rows,
                    list_batch,
                    issue_counts,
                )
                run.families_total += len(list_rows)
                for family in list_rows:
                    checkpoint = self._session.get(
                        SchemeLifecycleCheckpointRecord,
                        (fund.mutual_fund_id, family.scheme_id),
                    )
                    if (
                        checkpoint is not None
                        and checkpoint.source_scheme_name != family.scheme_name
                    ):
                        self._record_issue(
                            run,
                            fund.mutual_fund_id,
                            family.scheme_id,
                            list_batch.id,
                            "name_changed_without_effective_date",
                            "AMFI catalog name changed without an explicit effective date: "
                            f"prior={checkpoint.source_scheme_name!r}, "
                            f"current={family.scheme_name!r}",
                            issue_counts,
                        )
                    if mode == "full" and checkpoint is not None:
                        run.families_skipped += 1
                        self._update_run(run)
                        self._session.commit()
                        continue
                    try:
                        detail_rows, detail_batch = self._capture_scheme_details(
                            fund.mutual_fund_id, family.scheme_id
                        )
                    except _CaptureError as error:
                        self._record_issue(
                            run,
                            fund.mutual_fund_id,
                            family.scheme_id,
                            error.batch_id,
                            "scheme_detail_failure",
                            str(error),
                            issue_counts,
                        )
                        run.families_failed += 1
                        self._update_run(run)
                        self._session.commit()
                        continue
                    run.detail_rows_received += len(detail_rows)
                    if len(detail_rows) != 1 or (
                        detail_rows[0].mutual_fund_id != fund.mutual_fund_id
                        or detail_rows[0].scheme_id != family.scheme_id
                    ):
                        self._record_issue(
                            run,
                            fund.mutual_fund_id,
                            family.scheme_id,
                            detail_batch.id,
                            "identity_mismatch",
                            "AMFI scheme-details identity does not exactly match its request: "
                            f"requested=({fund.mutual_fund_id}, {family.scheme_id}), "
                            "returned="
                            f"{[(row.mutual_fund_id, row.scheme_id) for row in detail_rows]}",
                            issue_counts,
                        )
                        run.families_failed += 1
                        run.detail_rows_rejected += len(detail_rows)
                        self._update_run(run)
                        self._session.commit()
                        continue
                    detail, inserted = self._persist_detail(detail_rows[0], detail_batch)
                    run.detail_rows_inserted += int(inserted)
                    run.detail_rows_unchanged += int(not inserted)
                    self._persist_launch_event(run, detail, detail_batch, issue_counts)
                    self._upsert_checkpoint(run, family, detail, detail_batch)
                    run.families_completed += 1
                    self._update_run(run)
                    self._session.commit()
                run.funds_completed += 1
                self._update_run(run)
                self._session.commit()
            run.status = "completed_with_issues" if issue_counts else "completed"
            run.completed_at = utc_now()
            self._update_run(run)
            self._session.commit()
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed = self._session.get(SchemeLifecycleSyncRunRecord, run.id)
            if failed is not None:
                failed.status = "failed"
                failed.completed_at = utc_now()
                failed.error_details = f"{type(error).__name__}: {error!s}"[:4000]
                self._session.commit()
            raise
        result = SchemeLifecycleSyncResult(
            run_id=run.id,
            mode=run.mode,
            status=run.status,
            funds_total=run.funds_total,
            funds_completed=run.funds_completed,
            funds_failed=run.funds_failed,
            families_total=run.families_total,
            families_completed=run.families_completed,
            families_skipped=run.families_skipped,
            families_failed=run.families_failed,
            detail_rows_received=run.detail_rows_received,
            detail_rows_inserted=run.detail_rows_inserted,
            detail_rows_unchanged=run.detail_rows_unchanged,
            detail_rows_rejected=run.detail_rows_rejected,
            issue_counts=issue_counts,
        )
        logger.info("scheme_lifecycle_sync_completed", extra={"event_data": asdict(result)})
        return result

    def coverage_report(self) -> SchemeLifecycleCoverageReport:
        active_funds = (
            self._session.scalar(
                select(func.count())
                .select_from(AmfiFundRecord)
                .where(AmfiFundRecord.is_active.is_(True))
            )
            or 0
        )
        latest_snapshots = self._latest_snapshots()
        latest_snapshot_ids = tuple(snapshot.id for snapshot in latest_snapshots.values())
        latest_memberships = (
            set(
                self._session.execute(
                    select(
                        AmfiSchemeListMembershipRecord.mutual_fund_id,
                        AmfiSchemeListMembershipRecord.source_scheme_id,
                    ).where(AmfiSchemeListMembershipRecord.snapshot_id.in_(latest_snapshot_ids))
                )
                .tuples()
                .all()
            )
            if latest_snapshot_ids
            else set()
        )
        checkpoints = set(
            self._session.execute(
                select(
                    SchemeLifecycleCheckpointRecord.mutual_fund_id,
                    SchemeLifecycleCheckpointRecord.source_scheme_id,
                )
            )
            .tuples()
            .all()
        )
        launch_date_counts = self._session.execute(
            select(
                SchemeLifecycleEventRecord.mutual_fund_id,
                SchemeLifecycleEventRecord.source_scheme_id,
                func.count(func.distinct(SchemeLifecycleEventRecord.effective_date)),
            )
            .where(SchemeLifecycleEventRecord.event_type == "launch")
            .group_by(
                SchemeLifecycleEventRecord.mutual_fund_id,
                SchemeLifecycleEventRecord.source_scheme_id,
            )
        ).all()
        event_counts = {
            event_type: int(count)
            for event_type, count in self._session.execute(
                select(SchemeLifecycleEventRecord.event_type, func.count()).group_by(
                    SchemeLifecycleEventRecord.event_type
                )
            )
        }
        issue_counts = {
            issue_code: int(count)
            for issue_code, count in self._session.execute(
                select(SchemeLifecycleIssueRecord.issue_code, func.count()).group_by(
                    SchemeLifecycleIssueRecord.issue_code
                )
            )
        }
        latest_run = self._session.scalar(
            select(SchemeLifecycleSyncRunRecord).order_by(
                SchemeLifecycleSyncRunRecord.started_at.desc(),
                SchemeLifecycleSyncRunRecord.id.desc(),
            )
        )
        return SchemeLifecycleCoverageReport(
            normalization_version=LIFECYCLE_NORMALIZATION_VERSION,
            active_funds=int(active_funds),
            funds_with_scheme_list_snapshot=len(latest_snapshots),
            latest_catalog_families=len(latest_memberships),
            detail_checkpoints=len(checkpoints),
            families_with_launch_event=len(launch_date_counts),
            families_with_conflicting_launch_dates=sum(row[2] > 1 for row in launch_date_counts),
            latest_catalog_families_without_detail=len(latest_memberships - checkpoints),
            lifecycle_event_counts=event_counts,
            issue_counts=issue_counts,
            latest_sync_run=(
                {
                    "run_id": latest_run.id,
                    "mode": latest_run.mode,
                    "status": latest_run.status,
                    "funds_total": latest_run.funds_total,
                    "funds_completed": latest_run.funds_completed,
                    "funds_failed": latest_run.funds_failed,
                    "families_total": latest_run.families_total,
                    "families_completed": latest_run.families_completed,
                    "families_skipped": latest_run.families_skipped,
                    "families_failed": latest_run.families_failed,
                    "detail_rows_received": latest_run.detail_rows_received,
                    "detail_rows_inserted": latest_run.detail_rows_inserted,
                    "detail_rows_unchanged": latest_run.detail_rows_unchanged,
                    "detail_rows_rejected": latest_run.detail_rows_rejected,
                    "started_at": latest_run.started_at.isoformat(),
                    "completed_at": (
                        latest_run.completed_at.isoformat()
                        if latest_run.completed_at is not None
                        else None
                    ),
                }
                if latest_run is not None
                else None
            ),
        )

    def _funds(self, fund_ids: tuple[str, ...] | None) -> tuple[AmfiFundRecord, ...]:
        statement = select(AmfiFundRecord)
        if fund_ids:
            requested = set(fund_ids)
            statement = statement.where(AmfiFundRecord.mutual_fund_id.in_(requested))
        else:
            requested = None
            statement = statement.where(AmfiFundRecord.is_active.is_(True))
        funds = tuple(
            self._session.scalars(statement.order_by(AmfiFundRecord.mutual_fund_id)).all()
        )
        if requested is not None:
            found = {fund.mutual_fund_id for fund in funds}
            if found != requested:
                raise ValueError(f"unknown AMFI fund IDs: {sorted(requested - found)}")
        return funds

    def _capture_scheme_list(
        self, mutual_fund_id: str
    ) -> tuple[tuple[SchemeListRecord, ...], IngestionBatchRecord]:
        request = scheme_list_request(mutual_fund_id)
        rows, batch = self._capture(request)
        return cast(tuple[SchemeListRecord, ...], rows), batch

    def _capture_scheme_details(
        self, mutual_fund_id: str, scheme_id: str
    ) -> tuple[tuple[SchemeDetailRecord, ...], IngestionBatchRecord]:
        request = scheme_details_request(mutual_fund_id, scheme_id)
        rows, batch = self._capture(request)
        return cast(tuple[SchemeDetailRecord, ...], rows), batch

    def _capture(
        self, request: AmfiSourceRequest
    ) -> tuple[tuple[SchemeListRecord, ...] | tuple[SchemeDetailRecord, ...], IngestionBatchRecord]:
        batch = self._repository.start_batch(
            provider=request.provider,
            source_type=request.source_type.value,
            source_url=request.url,
            request_parameters=request.parameters,
            parser_version=request.parser.version,
        )
        try:
            downloaded = self._downloader.download(request.url)
            stored = self._artifacts.store(downloaded.content)
            self._repository.attach_artifact(
                batch,
                stored,
                media_type=downloaded.media_type,
                http_status=downloaded.status_code,
                final_url=downloaded.final_url,
            )
            if isinstance(request.parser, AmfiSchemeListParser):
                rows: tuple[SchemeListRecord, ...] | tuple[SchemeDetailRecord, ...] = (
                    request.parser.parse_records(downloaded.content)
                )
            elif isinstance(request.parser, AmfiSchemeDetailsParser):
                rows = request.parser.parse_records(downloaded.content)
            else:
                raise TypeError("scheme lifecycle capture received an unsupported parser")
            self._repository.complete_batch(batch, len(rows))
            return rows, batch
        except KeyboardInterrupt:
            self._repository.fail_batch(batch, "KeyboardInterrupt: interrupted by user")
            raise
        except Exception as error:
            self._repository.fail_batch(batch, f"{type(error).__name__}: {error!s}")
            raise _CaptureError(batch.id, error) from error
        finally:
            if self._request_delay_seconds:
                self._sleep(self._request_delay_seconds)

    def _persist_list_snapshot(
        self,
        mutual_fund_id: str,
        rows: tuple[SchemeListRecord, ...],
        batch: IngestionBatchRecord,
    ) -> AmfiSchemeListSnapshotRecord:
        snapshot = AmfiSchemeListSnapshotRecord(
            mutual_fund_id=mutual_fund_id,
            ingestion_batch_id=batch.id,
            scheme_count=len(rows),
            captured_at=batch.started_at,
        )
        self._session.add(snapshot)
        self._session.flush()
        for row in rows:
            self._session.add(
                AmfiSchemeListMembershipRecord(
                    snapshot_id=snapshot.id,
                    mutual_fund_id=mutual_fund_id,
                    source_scheme_id=row.scheme_id,
                    source_scheme_name=row.scheme_name,
                    content_signature=_signature(
                        "membership", mutual_fund_id, row.scheme_id, row.scheme_name
                    ),
                )
            )
        self._session.commit()
        return snapshot

    def _persist_detail(
        self, source: SchemeDetailRecord, batch: IngestionBatchRecord
    ) -> tuple[AmfiSchemeDetailRecord, bool]:
        signature = _signature(
            "detail",
            source.mutual_fund_id,
            source.scheme_id,
            source.mutual_fund_name,
            source.scheme_name,
            source.scheme_type,
            source.scheme_category,
            source.launch_date.date().isoformat(),
        )
        record = self._session.scalar(
            select(AmfiSchemeDetailRecord).where(
                AmfiSchemeDetailRecord.content_signature == signature
            )
        )
        inserted = record is None
        if record is None:
            record = AmfiSchemeDetailRecord(
                mutual_fund_id=source.mutual_fund_id,
                source_scheme_id=source.scheme_id,
                mutual_fund_name=source.mutual_fund_name,
                scheme_name=source.scheme_name,
                scheme_type=source.scheme_type,
                scheme_category=source.scheme_category,
                launch_date=source.launch_date.date(),
                content_signature=signature,
                first_observed_batch_id=batch.id,
            )
            self._session.add(record)
            self._session.flush()
        self._session.add(
            AmfiSchemeDetailRecordSource(
                scheme_detail_record_id=record.id,
                ingestion_batch_id=batch.id,
            )
        )
        return record, inserted

    def _persist_launch_event(
        self,
        run: SchemeLifecycleSyncRunRecord,
        detail: AmfiSchemeDetailRecord,
        batch: IngestionBatchRecord,
        issue_counts: dict[str, int],
    ) -> None:
        signature = _signature(
            "event",
            detail.mutual_fund_id,
            detail.source_scheme_id,
            "launch",
            detail.launch_date.isoformat(),
            "amfi_scheme_details",
        )
        event = self._session.scalar(
            select(SchemeLifecycleEventRecord).where(
                SchemeLifecycleEventRecord.content_signature == signature
            )
        )
        if event is None:
            prior_dates = set(
                self._session.scalars(
                    select(SchemeLifecycleEventRecord.effective_date).where(
                        SchemeLifecycleEventRecord.mutual_fund_id == detail.mutual_fund_id,
                        SchemeLifecycleEventRecord.source_scheme_id == detail.source_scheme_id,
                        SchemeLifecycleEventRecord.event_type == "launch",
                    )
                )
            )
            if prior_dates and detail.launch_date not in prior_dates:
                self._record_issue(
                    run,
                    detail.mutual_fund_id,
                    detail.source_scheme_id,
                    batch.id,
                    "launch_date_conflict",
                    "AMFI scheme-details launch date conflicts with prior official observations: "
                    f"prior={sorted(item.isoformat() for item in prior_dates)}, "
                    f"current={detail.launch_date.isoformat()}",
                    issue_counts,
                )
            event = SchemeLifecycleEventRecord(
                mutual_fund_id=detail.mutual_fund_id,
                source_scheme_id=detail.source_scheme_id,
                event_type="launch",
                effective_date=detail.launch_date,
                new_name=detail.scheme_name,
                source_kind="amfi_scheme_details",
                content_signature=signature,
                first_observed_batch_id=batch.id,
            )
            self._session.add(event)
            self._session.flush()
        self._session.add(
            SchemeLifecycleEventSourceRecord(
                scheme_lifecycle_event_id=event.id,
                ingestion_batch_id=batch.id,
            )
        )

    def _upsert_checkpoint(
        self,
        run: SchemeLifecycleSyncRunRecord,
        family: SchemeListRecord,
        detail: AmfiSchemeDetailRecord,
        batch: IngestionBatchRecord,
    ) -> None:
        checkpoint = self._session.get(
            SchemeLifecycleCheckpointRecord,
            (detail.mutual_fund_id, detail.source_scheme_id),
        )
        if checkpoint is None:
            checkpoint = SchemeLifecycleCheckpointRecord(
                mutual_fund_id=detail.mutual_fund_id,
                source_scheme_id=detail.source_scheme_id,
                source_scheme_name=family.scheme_name,
                latest_detail_record_id=detail.id,
                last_batch_id=batch.id,
                last_sync_run_id=run.id,
            )
            self._session.add(checkpoint)
        else:
            checkpoint.source_scheme_name = family.scheme_name
            checkpoint.latest_detail_record_id = detail.id
            checkpoint.last_batch_id = batch.id
            checkpoint.last_sync_run_id = run.id
            checkpoint.updated_at = utc_now()

    def _record_catalog_changes(
        self,
        run: SchemeLifecycleSyncRunRecord,
        mutual_fund_id: str,
        previous: dict[str, str],
        current: tuple[SchemeListRecord, ...],
        batch: IngestionBatchRecord,
        issue_counts: dict[str, int],
    ) -> None:
        current_ids = {row.scheme_id for row in current}
        for removed_id in sorted(set(previous) - current_ids):
            self._record_issue(
                run,
                mutual_fund_id,
                removed_id,
                batch.id,
                "catalog_member_removed",
                "Scheme family disappeared from the current AMFI list; this is not proof of "
                f"closure or merger. Last observed name={previous[removed_id]!r}",
                issue_counts,
            )

    def _record_issue(
        self,
        run: SchemeLifecycleSyncRunRecord,
        mutual_fund_id: str,
        source_scheme_id: str | None,
        batch_id: str | None,
        issue_code: str,
        details: str,
        issue_counts: dict[str, int],
    ) -> None:
        self._session.add(
            SchemeLifecycleIssueRecord(
                sync_run_id=run.id,
                mutual_fund_id=mutual_fund_id,
                source_scheme_id=source_scheme_id,
                ingestion_batch_id=batch_id,
                issue_code=issue_code,
                details=details,
            )
        )
        issue_counts[issue_code] = issue_counts.get(issue_code, 0) + 1

    def _latest_memberships(self, mutual_fund_id: str) -> dict[str, str]:
        snapshot = self._session.scalar(
            select(AmfiSchemeListSnapshotRecord)
            .where(AmfiSchemeListSnapshotRecord.mutual_fund_id == mutual_fund_id)
            .order_by(
                AmfiSchemeListSnapshotRecord.captured_at.desc(),
                AmfiSchemeListSnapshotRecord.id.desc(),
            )
            .limit(1)
        )
        if snapshot is None:
            return {}
        return dict(
            self._session.execute(
                select(
                    AmfiSchemeListMembershipRecord.source_scheme_id,
                    AmfiSchemeListMembershipRecord.source_scheme_name,
                ).where(AmfiSchemeListMembershipRecord.snapshot_id == snapshot.id)
            )
            .tuples()
            .all()
        )

    def _latest_snapshots(self) -> dict[str, AmfiSchemeListSnapshotRecord]:
        latest: dict[str, AmfiSchemeListSnapshotRecord] = {}
        for snapshot in self._session.scalars(
            select(AmfiSchemeListSnapshotRecord).order_by(
                AmfiSchemeListSnapshotRecord.captured_at,
                AmfiSchemeListSnapshotRecord.id,
            )
        ):
            latest[snapshot.mutual_fund_id] = snapshot
        return latest

    def _fail_stale_runs(self) -> None:
        stale = self._session.scalars(
            select(SchemeLifecycleSyncRunRecord).where(
                SchemeLifecycleSyncRunRecord.status == "running"
            )
        ).all()
        for run in stale:
            run.status = "failed"
            run.completed_at = utc_now()
            run.error_details = "Interrupted before completion; superseded by a later lifecycle run"
        self._session.commit()

    @staticmethod
    def _update_run(run: SchemeLifecycleSyncRunRecord) -> None:
        if run.funds_completed + run.funds_failed > run.funds_total:
            raise RuntimeError("scheme lifecycle fund counters exceed the selected universe")
        processed = run.families_completed + run.families_skipped + run.families_failed
        if processed > run.families_total:
            raise RuntimeError("scheme lifecycle family counters exceed discovered families")
        if run.detail_rows_received != (
            run.detail_rows_inserted + run.detail_rows_unchanged + run.detail_rows_rejected
        ):
            raise RuntimeError("scheme lifecycle detail row counters do not reconcile")


def _signature(*values: str) -> str:
    encoded = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
