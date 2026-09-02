from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    AmfiDistributionRecordSource,
    AmfiFundRecord,
    DistributionParseIssueRecord,
    DistributionSyncCheckpointRecord,
    DistributionSyncRunRecord,
    SchemeOptionRecord,
    new_id,
    utc_now,
)
from mf_strategy_tester.ingestion.amfi import (
    DISTRIBUTION_PERCENTAGE_THROUGH,
    AmfiDistributionParser,
    AmfiSchemeListParser,
    DistributionParseIssue,
    DistributionSourceRecord,
    SchemeListRecord,
    distribution_request,
    scheme_list_request,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.source_ingestion import IngestionResult, SourceIngestionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DistributionPublicationStats:
    rows_received: int
    rows_accepted: int
    rows_rejected: int
    rows_inserted: int
    rows_unchanged: int
    rows_unresolved: int


@dataclass(frozen=True)
class DistributionSyncResult:
    run_id: str
    status: str
    mode: str
    funds_total: int
    funds_completed: int
    schemes_total: int
    schemes_completed: int
    rows_received: int
    rows_inserted: int
    rows_unchanged: int
    rows_unresolved: int
    rows_rejected: int


class AmfiDistributionPublisher:
    """Publish exact AMFI payout rows without inventing canonical cash-flow semantics."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def publish(
        self,
        records: tuple[DistributionSourceRecord, ...],
        *,
        issues: tuple[DistributionParseIssue, ...] = (),
        mutual_fund_id: str,
        source_scheme_id: str,
        batch_id: str,
    ) -> DistributionPublicationStats:
        self._validate_request_identity(records, mutual_fund_id, source_scheme_id)

        signatures = {self._content_signature(record): record for record in records}
        existing: dict[str, AmfiDistributionRecord] = {}
        for signature_chunk in _chunks(tuple(signatures), 900):
            rows = self._session.scalars(
                select(AmfiDistributionRecord).where(
                    AmfiDistributionRecord.content_signature.in_(signature_chunk)
                )
            ).all()
            existing.update({row.content_signature: row for row in rows})

        option_ids = tuple({record.source_option_id for record in records})
        resolved_option_ids: set[str] = set()
        for option_chunk in _chunks(option_ids, 900):
            resolved_option_ids.update(
                self._session.scalars(
                    select(SchemeOptionRecord.amfi_scheme_code).where(
                        SchemeOptionRecord.amfi_scheme_code.in_(option_chunk)
                    )
                ).all()
            )

        inserted = unchanged = 0
        source_links: list[dict[str, object]] = []
        now = utc_now()
        self._resolve_issues(records, batch_id, now)
        for signature, record in signatures.items():
            stored = existing.get(signature)
            if stored is None:
                inserted += 1
                record_id = new_id()
                self._session.add(
                    AmfiDistributionRecord(
                        id=record_id,
                        mutual_fund_id=record.mutual_fund_id,
                        source_scheme_id=record.source_scheme_id,
                        source_option_id=record.source_option_id,
                        scheme_name=record.scheme_name,
                        nav_name=record.nav_name,
                        source_plan=record.source_plan,
                        source_option=record.source_option,
                        record_date=record.record_date,
                        raw_source_value=record.raw_source_value,
                        source_value=record.source_value,
                        ratio_numerator=record.ratio_numerator,
                        ratio_denominator=record.ratio_denominator,
                        annotated_amount_per_unit_inr=(record.annotated_amount_per_unit_inr),
                        source_unit=_source_unit(record),
                        content_signature=signature,
                        first_observed_batch_id=batch_id,
                        observed_at=now,
                    )
                )
            else:
                unchanged += 1
                record_id = stored.id
            source_links.append(
                {
                    "distribution_record_id": record_id,
                    "ingestion_batch_id": batch_id,
                    "observed_at": now,
                }
            )

        self._persist_issues(
            issues,
            mutual_fund_id=mutual_fund_id,
            source_scheme_id=source_scheme_id,
            batch_id=batch_id,
            observed_at=now,
        )
        self._session.flush()
        if source_links:
            self._session.execute(insert(AmfiDistributionRecordSource), source_links)
        return DistributionPublicationStats(
            rows_received=len(records) + len(issues),
            rows_accepted=len(records),
            rows_rejected=len(issues),
            rows_inserted=inserted,
            rows_unchanged=unchanged,
            rows_unresolved=sum(
                record.source_option_id not in resolved_option_ids for record in records
            ),
        )

    def _resolve_issues(
        self,
        records: tuple[DistributionSourceRecord, ...],
        batch_id: str,
        resolved_at: datetime,
    ) -> None:
        signatures = tuple({record.source_record_signature for record in records})
        for signature_chunk in _chunks(signatures, 900):
            issues = self._session.scalars(
                select(DistributionParseIssueRecord).where(
                    DistributionParseIssueRecord.status == "open",
                    DistributionParseIssueRecord.source_record_signature.in_(signature_chunk),
                )
            ).all()
            for issue in issues:
                issue.status = "resolved"
                issue.resolved_batch_id = batch_id
                issue.resolved_at = resolved_at

    def _persist_issues(
        self,
        issues: tuple[DistributionParseIssue, ...],
        *,
        mutual_fund_id: str,
        source_scheme_id: str,
        batch_id: str,
        observed_at: datetime,
    ) -> None:
        for issue in issues:
            self._session.add(
                DistributionParseIssueRecord(
                    ingestion_batch_id=batch_id,
                    mutual_fund_id=mutual_fund_id,
                    source_scheme_id=source_scheme_id,
                    record_number=issue.record_number,
                    issue_code=issue.issue_code,
                    error_details=issue.error_details,
                    raw_record=issue.raw_record,
                    source_record_signature=issue.source_record_signature,
                    status="open",
                    created_at=observed_at,
                )
            )

    @staticmethod
    def _validate_request_identity(
        records: tuple[DistributionSourceRecord, ...],
        mutual_fund_id: str,
        source_scheme_id: str,
    ) -> None:
        for record in records:
            if record.mutual_fund_id != mutual_fund_id:
                raise ValueError(
                    "AMFI distribution row fund ID does not match the requested fund: "
                    f"{record.mutual_fund_id} != {mutual_fund_id}"
                )
            if record.source_scheme_id != source_scheme_id:
                raise ValueError(
                    "AMFI distribution row scheme ID does not match the requested scheme: "
                    f"{record.source_scheme_id} != {source_scheme_id}"
                )

    @staticmethod
    def _content_signature(record: DistributionSourceRecord) -> str:
        serialized = json.dumps(
            (
                record.mutual_fund_id,
                record.source_scheme_id,
                record.source_option_id,
                record.scheme_name,
                record.nav_name,
                record.source_plan,
                record.source_option,
                record.record_date.isoformat(),
                record.raw_source_value,
                _source_unit(record),
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class DistributionSyncService:
    """Resume initial AMFI distribution capture or refresh every current source family."""

    def __init__(
        self,
        session: Session,
        ingestion_service: SourceIngestionService,
        ingestion_repository: IngestionRepository,
        artifact_store: ArtifactStore,
    ) -> None:
        self._session = session
        self._ingestion = ingestion_service
        self._repository = ingestion_repository
        self._artifacts = artifact_store
        self._publisher = AmfiDistributionPublisher(session)

    def synchronize(
        self,
        *,
        mode: str,
        fund_ids: frozenset[str] | None = None,
        scheme_ids: frozenset[str] | None = None,
        quarantine_record_errors: bool = False,
        retry_quarantined: bool = False,
    ) -> DistributionSyncResult:
        if mode not in {"full", "refresh"}:
            raise ValueError("distribution sync mode must be 'full' or 'refresh'")
        if scheme_ids and not fund_ids:
            raise ValueError("scheme_ids require an explicit fund_ids selection")
        if scheme_ids and any(not scheme_id.isdigit() for scheme_id in scheme_ids):
            raise ValueError("distribution scheme IDs must be numeric")
        self._fail_stale_runs()
        funds = self._selected_funds(fund_ids)
        run = DistributionSyncRunRecord(
            mode=mode,
            status="running",
            record_error_policy="quarantine" if quarantine_record_errors else "fail",
            funds_total=len(funds),
        )
        self._session.add(run)
        self._session.commit()
        try:
            scheme_universe = self._discover_scheme_universe(funds, run, scheme_ids)
            completed = self._completed_schemes() if mode == "full" else {}
            quarantined = self._open_issue_families() if retry_quarantined else set()
            for fund, schemes in scheme_universe:
                for scheme in schemes:
                    key = (fund.mutual_fund_id, scheme.scheme_id)
                    if completed.get(key) != scheme.scheme_name or key in quarantined:
                        self._synchronize_scheme(
                            run,
                            fund,
                            scheme,
                            quarantine_record_errors=quarantine_record_errors,
                        )
                    run.schemes_completed += 1
                    self._session.commit()
                run.funds_completed += 1
                self._session.commit()
            selected_fund_ids = frozenset(fund.mutual_fund_id for fund in funds)
            run.status = (
                "completed_with_issues"
                if self._has_open_issues(selected_fund_ids, scheme_ids)
                else "completed"
            )
            run.completed_at = utc_now()
            self._session.commit()
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed_run = self._session.get(DistributionSyncRunRecord, run.id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.completed_at = utc_now()
                message = str(error) or "interrupted by user"
                failed_run.error_details = f"{type(error).__name__}: {message}"[:4000]
                self._session.commit()
            raise
        return _result(run)

    def _discover_scheme_universe(
        self,
        funds: list[AmfiFundRecord],
        run: DistributionSyncRunRecord,
        scheme_ids: frozenset[str] | None,
    ) -> list[tuple[AmfiFundRecord, tuple[SchemeListRecord, ...]]]:
        universe: list[tuple[AmfiFundRecord, tuple[SchemeListRecord, ...]]] = []
        found_scheme_ids: set[str] = set()
        for fund in funds:
            result = self._ingestion.ingest(scheme_list_request(fund.mutual_fund_id))
            schemes = AmfiSchemeListParser().parse_records(self._payload(result))
            if scheme_ids:
                schemes = tuple(scheme for scheme in schemes if scheme.scheme_id in scheme_ids)
                found_scheme_ids.update(scheme.scheme_id for scheme in schemes)
            universe.append((fund, schemes))
            run.schemes_total += len(schemes)
            self._session.commit()
        if scheme_ids and found_scheme_ids != set(scheme_ids):
            missing = sorted(set(scheme_ids) - found_scheme_ids)
            raise ValueError(
                f"unknown AMFI distribution scheme IDs for the selected funds: {', '.join(missing)}"
            )
        return universe

    def _synchronize_scheme(
        self,
        run: DistributionSyncRunRecord,
        fund: AmfiFundRecord,
        scheme: SchemeListRecord,
        *,
        quarantine_record_errors: bool,
    ) -> None:
        result = self._ingestion.ingest(
            distribution_request(fund.mutual_fund_id, scheme.scheme_id, "All"),
            quarantine_record_errors=quarantine_record_errors,
        )
        parser = AmfiDistributionParser()
        if quarantine_record_errors:
            parsed = parser.parse_with_issues(self._payload(result))
            records = parsed.records
            issues = parsed.issues
        else:
            records = parser.parse_records(self._payload(result))
            issues = ()
        stats = self._publisher.publish(
            records,
            issues=issues,
            mutual_fund_id=fund.mutual_fund_id,
            source_scheme_id=scheme.scheme_id,
            batch_id=result.batch_id,
        )
        checkpoint = self._session.get(
            DistributionSyncCheckpointRecord,
            (fund.mutual_fund_id, scheme.scheme_id),
        )
        if checkpoint is None:
            checkpoint = DistributionSyncCheckpointRecord(
                mutual_fund_id=fund.mutual_fund_id,
                source_scheme_id=scheme.scheme_id,
                source_scheme_name=scheme.scheme_name,
                rows_received=stats.rows_received,
                rows_accepted=stats.rows_accepted,
                rows_rejected=stats.rows_rejected,
                last_batch_id=result.batch_id,
            )
            self._session.add(checkpoint)
        else:
            checkpoint.source_scheme_name = scheme.scheme_name
            checkpoint.rows_received = stats.rows_received
            checkpoint.rows_accepted = stats.rows_accepted
            checkpoint.rows_rejected = stats.rows_rejected
            checkpoint.last_batch_id = result.batch_id
            checkpoint.updated_at = utc_now()
        run.rows_received += stats.rows_received
        run.rows_inserted += stats.rows_inserted
        run.rows_unchanged += stats.rows_unchanged
        run.rows_unresolved += stats.rows_unresolved
        run.rows_rejected += stats.rows_rejected
        self._session.commit()
        logger.info(
            "distribution_sync_scheme_completed",
            extra={
                "event_data": {
                    "run_id": run.id,
                    "mutual_fund_id": fund.mutual_fund_id,
                    "source_scheme_id": scheme.scheme_id,
                    "rows_received": stats.rows_received,
                    "rows_accepted": stats.rows_accepted,
                    "rows_rejected": stats.rows_rejected,
                    "rows_unresolved": stats.rows_unresolved,
                }
            },
        )

    def _fail_stale_runs(self) -> None:
        stale_runs = self._session.scalars(
            select(DistributionSyncRunRecord).where(DistributionSyncRunRecord.status == "running")
        ).all()
        for stale_run in stale_runs:
            stale_run.status = "failed"
            stale_run.completed_at = utc_now()
            stale_run.error_details = "Interrupted before completion; superseded by a resumed run"
        self._session.commit()

    def _selected_funds(self, fund_ids: frozenset[str] | None) -> list[AmfiFundRecord]:
        statement = select(AmfiFundRecord).where(AmfiFundRecord.is_active.is_(True))
        if fund_ids:
            statement = statement.where(AmfiFundRecord.mutual_fund_id.in_(fund_ids))
        funds = list(self._session.scalars(statement.order_by(AmfiFundRecord.mutual_fund_id)).all())
        if fund_ids and {fund.mutual_fund_id for fund in funds} != set(fund_ids):
            missing = sorted(set(fund_ids) - {fund.mutual_fund_id for fund in funds})
            raise ValueError(f"unknown or inactive AMFI fund IDs: {', '.join(missing)}")
        return funds

    def _completed_schemes(self) -> dict[tuple[str, str], str]:
        return {
            (mutual_fund_id, source_scheme_id): source_scheme_name
            for mutual_fund_id, source_scheme_id, source_scheme_name in self._session.execute(
                select(
                    DistributionSyncCheckpointRecord.mutual_fund_id,
                    DistributionSyncCheckpointRecord.source_scheme_id,
                    DistributionSyncCheckpointRecord.source_scheme_name,
                )
            ).tuples()
        }

    def _open_issue_families(self) -> set[tuple[str, str]]:
        return set(
            self._session.execute(
                select(
                    DistributionParseIssueRecord.mutual_fund_id,
                    DistributionParseIssueRecord.source_scheme_id,
                )
                .where(DistributionParseIssueRecord.status == "open")
                .distinct()
            ).tuples()
        )

    def _has_open_issues(
        self,
        fund_ids: frozenset[str],
        scheme_ids: frozenset[str] | None,
    ) -> bool:
        statement = select(DistributionParseIssueRecord.id).where(
            DistributionParseIssueRecord.status == "open",
            DistributionParseIssueRecord.mutual_fund_id.in_(fund_ids),
        )
        if scheme_ids:
            statement = statement.where(
                DistributionParseIssueRecord.source_scheme_id.in_(scheme_ids)
            )
        return self._session.scalar(statement.limit(1)) is not None

    def _payload(self, result: IngestionResult) -> bytes:
        batch = self._repository.get_batch(result.batch_id)
        if batch.artifact is None:
            raise RuntimeError(f"completed ingestion batch {batch.id} has no artifact")
        return self._artifacts.read(
            batch.artifact.storage_path, expected_sha256=batch.artifact.sha256
        )


def _source_unit(record: DistributionSourceRecord) -> str:
    if record.ratio_numerator is not None:
        return "ratio"
    if record.annotated_amount_per_unit_inr is not None:
        return "percentage"
    if record.raw_source_value.endswith("%"):
        return "percentage"
    return "percentage" if record.record_date <= DISTRIBUTION_PERCENTAGE_THROUGH else "amount"


def _result(run: DistributionSyncRunRecord) -> DistributionSyncResult:
    return DistributionSyncResult(
        run_id=run.id,
        status=run.status,
        mode=run.mode,
        funds_total=run.funds_total,
        funds_completed=run.funds_completed,
        schemes_total=run.schemes_total,
        schemes_completed=run.schemes_completed,
        rows_received=run.rows_received,
        rows_inserted=run.rows_inserted,
        rows_unchanged=run.rows_unchanged,
        rows_unresolved=run.rows_unresolved,
        rows_rejected=run.rows_rejected,
    )


def _chunks[T](values: tuple[T, ...], size: int) -> Iterator[tuple[T, ...]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]
