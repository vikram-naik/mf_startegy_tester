from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    DistributionEventRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
    OfficialDistributionRecord,
    OfficialDistributionRecordSource,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    new_id,
    utc_now,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.hdfc import (
    HdfcDistributionNoticeParser,
    HdfcNoticeDistribution,
    HdfcSchemeIdentity,
    HdfcSchemeSummaryParser,
    distribution_notice_request,
    scheme_summary_request,
)
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.source_ingestion import SourceIngestionService

logger = logging.getLogger(__name__)

OFFICIAL_NOTICE_NORMALIZATION_VERSION = "official-amc-notice-2026.08.1"
_EVENT_TYPE = "idcw_cash"


@dataclass(frozen=True)
class OfficialNoticePublicationResult:
    notice_batch_id: str
    identity_batch_id: str
    normalization_run_id: str
    status: str
    rows_received: int
    source_records_inserted: int
    source_records_reused: int
    events_inserted: int
    revisions_inserted: int
    rows_unchanged: int


class OfficialDistributionConflictError(RuntimeError):
    """An official notice contradicts the current canonical amount."""


class HdfcDistributionNoticeService:
    """Capture, resolve, and publish one official HDFC IDCW notice."""

    def __init__(
        self,
        session: Session,
        ingestion: SourceIngestionService,
        repository: IngestionRepository,
        artifacts: ArtifactStore,
    ) -> None:
        self._session = session
        self._ingestion = ingestion
        self._repository = repository
        self._artifacts = artifacts

    def publish(
        self, *, notice_url: str, scheme_summary_url: str
    ) -> OfficialNoticePublicationResult:
        identity_result = self._ingestion.ingest(scheme_summary_request(scheme_summary_url))
        notice_result = self._ingestion.ingest(distribution_notice_request(notice_url))
        identity_batch, identity_payload = self._batch_payload(identity_result.batch_id)
        notice_batch, notice_payload = self._batch_payload(notice_result.batch_id)

        identities = HdfcSchemeSummaryParser().parse_records(identity_payload)
        notice_rows = HdfcDistributionNoticeParser().parse_records(notice_payload)
        resolved = self._resolve_rows(notice_rows, identities)
        source_records, inserted = self._persist_source_records(
            resolved,
            notice_batch=notice_batch,
            identity_batch=identity_batch,
        )
        publication = self._publish_canonical(source_records)
        result = OfficialNoticePublicationResult(
            notice_batch_id=notice_batch.id,
            identity_batch_id=identity_batch.id,
            normalization_run_id=publication.run_id,
            status=publication.status,
            rows_received=len(notice_rows),
            source_records_inserted=inserted,
            source_records_reused=len(source_records) - inserted,
            events_inserted=publication.events_inserted,
            revisions_inserted=publication.revisions_inserted,
            rows_unchanged=publication.rows_unchanged,
        )
        logger.info("official_distribution_notice_published", extra={"event_data": result.__dict__})
        return result

    def _batch_payload(self, batch_id: str) -> tuple[IngestionBatchRecord, bytes]:
        batch = self._repository.get_batch(batch_id)
        if batch.status != "completed" or batch.artifact is None:
            raise RuntimeError(f"completed ingestion batch {batch_id} has no source artifact")
        return batch, self._artifacts.read(
            batch.artifact.storage_path, expected_sha256=batch.artifact.sha256
        )

    def _resolve_rows(
        self,
        notice_rows: tuple[HdfcNoticeDistribution, ...],
        identities: tuple[HdfcSchemeIdentity, ...],
    ) -> tuple[tuple[HdfcNoticeDistribution, HdfcSchemeIdentity], ...]:
        idcw_identities: dict[tuple[str, str], HdfcSchemeIdentity] = {}
        for identity in identities:
            if identity.option_type != "idcw":
                continue
            key = (_identity_name(identity.scheme_name), identity.plan_type)
            if key in idcw_identities:
                raise RuntimeError(f"scheme summary has duplicate IDCW identity for {key}")
            idcw_identities[key] = identity

        resolved: list[tuple[HdfcNoticeDistribution, HdfcSchemeIdentity]] = []
        seen_codes: set[str] = set()
        for row in notice_rows:
            key = (_identity_name(row.scheme_name), row.plan_type)
            resolved_identity = idcw_identities.get(key)
            if resolved_identity is None:
                raise RuntimeError(
                    "notice option has no exact AMFI-code mapping in the supplied HDFC "
                    f"scheme summary: scheme={row.scheme_name!r}, plan={row.plan_type}"
                )
            if resolved_identity.amfi_scheme_code in seen_codes:
                raise RuntimeError(
                    "notice resolves more than once to AMFI code "
                    f"{resolved_identity.amfi_scheme_code}"
                )
            self._validate_local_option(resolved_identity)
            seen_codes.add(resolved_identity.amfi_scheme_code)
            resolved.append((row, resolved_identity))
        return tuple(resolved)

    def _validate_local_option(self, identity: HdfcSchemeIdentity) -> None:
        option = self._session.get(SchemeOptionRecord, identity.amfi_scheme_code)
        if option is None:
            raise RuntimeError(
                f"scheme summary maps to unknown AMFI code {identity.amfi_scheme_code}"
            )
        metadata = self._session.scalar(
            select(SchemeMetadataVersionRecord)
            .join(
                NavRevisionRecord,
                NavRevisionRecord.metadata_version_id == SchemeMetadataVersionRecord.id,
            )
            .where(
                NavRevisionRecord.amfi_scheme_code == identity.amfi_scheme_code,
                NavRevisionRecord.is_current.is_(True),
            )
            .order_by(NavRevisionRecord.nav_date.desc())
            .limit(1)
        )
        if metadata is None:
            raise RuntimeError(f"AMFI code {identity.amfi_scheme_code} has no current NAV metadata")
        plan_conflicts = metadata.plan_type not in {"unknown", identity.plan_type}
        if metadata.option_type != "idcw" or plan_conflicts:
            raise RuntimeError(
                f"local metadata conflicts with official identity for {identity.amfi_scheme_code}: "
                f"local={metadata.plan_type}/{metadata.option_type}, "
                f"official={identity.plan_type}/{identity.option_type}"
            )

    def _persist_source_records(
        self,
        resolved: tuple[tuple[HdfcNoticeDistribution, HdfcSchemeIdentity], ...],
        *,
        notice_batch: IngestionBatchRecord,
        identity_batch: IngestionBatchRecord,
    ) -> tuple[tuple[OfficialDistributionRecord, ...], int]:
        if notice_batch.artifact is None or identity_batch.artifact is None:
            raise RuntimeError("official source batches must have attached artifacts")
        records: list[OfficialDistributionRecord] = []
        inserted = 0
        for row, identity in resolved:
            signature = _source_signature(
                notice_batch.artifact.sha256,
                identity.amfi_scheme_code,
                row.record_date.isoformat(),
                row.amount_per_unit_inr,
            )
            record = self._session.scalar(
                select(OfficialDistributionRecord).where(
                    OfficialDistributionRecord.content_signature == signature
                )
            )
            if record is None:
                record = OfficialDistributionRecord(
                    provider="hdfc_amc",
                    amfi_scheme_code=identity.amfi_scheme_code,
                    source_scheme_name=row.scheme_name,
                    source_plan_type=row.plan_type,
                    source_option_label=row.option_label,
                    record_date=row.record_date,
                    raw_amount_per_unit_inr=row.raw_amount_per_unit_inr,
                    amount_per_unit_inr=row.amount_per_unit_inr,
                    source_unit="inr_per_unit",
                    content_signature=signature,
                    first_notice_batch_id=notice_batch.id,
                    first_identity_batch_id=identity_batch.id,
                )
                self._session.add(record)
                self._session.flush()
                inserted += 1
            observation = self._session.get(
                OfficialDistributionRecordSource,
                (record.id, notice_batch.id, identity_batch.id),
            )
            if observation is None:
                self._session.add(
                    OfficialDistributionRecordSource(
                        distribution_record_id=record.id,
                        notice_batch_id=notice_batch.id,
                        identity_batch_id=identity_batch.id,
                    )
                )
            records.append(record)
        self._session.commit()
        return tuple(records), inserted

    def _publish_canonical(
        self, records: tuple[OfficialDistributionRecord, ...]
    ) -> _CanonicalPublication:
        run = DistributionNormalizationRunRecord(
            status="running",
            normalization_version=OFFICIAL_NOTICE_NORMALIZATION_VERSION,
        )
        self._session.add(run)
        self._session.commit()
        try:
            result = self._publish_records(run, records)
            self._session.commit()
            return result
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed_run = self._session.get(DistributionNormalizationRunRecord, run.id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.source_rows_examined = len(records)
                failed_run.candidate_rows = 0
                failed_run.blocked_rows = len(records)
                failed_run.completed_at = utc_now()
                failed_run.error_details = f"{type(error).__name__}: {error!s}"[:4000]
                self._session.commit()
            raise

    def _publish_records(
        self,
        run: DistributionNormalizationRunRecord,
        records: tuple[OfficialDistributionRecord, ...],
    ) -> _CanonicalPublication:
        existing_links = {
            link.official_distribution_record_id
            for link in self._session.scalars(
                select(DistributionEventRevisionOfficialSourceRecord).where(
                    DistributionEventRevisionOfficialSourceRecord.official_distribution_record_id.in_(
                        tuple(record.id for record in records)
                    )
                )
            )
        }
        events_inserted = revisions_inserted = rows_unchanged = 0
        now = utc_now()
        for source in records:
            if source.id in existing_links:
                rows_unchanged += 1
                continue
            event = self._session.scalar(
                select(DistributionEventRecord).where(
                    DistributionEventRecord.amfi_scheme_code == source.amfi_scheme_code,
                    DistributionEventRecord.record_date == source.record_date,
                    DistributionEventRecord.event_type == _EVENT_TYPE,
                )
            )
            if event is None:
                event = DistributionEventRecord(
                    id=new_id(),
                    amfi_scheme_code=source.amfi_scheme_code,
                    record_date=source.record_date,
                    event_type=_EVENT_TYPE,
                    created_at=now,
                )
                self._session.add(event)
                events_inserted += 1
            current = self._session.scalar(
                select(DistributionEventRevisionRecord).where(
                    DistributionEventRevisionRecord.distribution_event_id == event.id,
                    DistributionEventRevisionRecord.is_current.is_(True),
                )
            )
            if current is not None and current.amount_per_unit_inr != source.amount_per_unit_inr:
                raise OfficialDistributionConflictError(
                    "official notice amount conflicts with the current canonical revision: "
                    f"AMFI code={source.amfi_scheme_code}, record_date={source.record_date}, "
                    f"current={current.amount_per_unit_inr}, notice={source.amount_per_unit_inr}"
                )
            if current is None:
                current = DistributionEventRevisionRecord(
                    id=new_id(),
                    distribution_event_id=event.id,
                    amount_per_unit_inr=source.amount_per_unit_inr,
                    revision_number=1,
                    content_signature=_revision_signature(
                        source.amfi_scheme_code,
                        source.record_date.isoformat(),
                        source.amount_per_unit_inr,
                        1,
                    ),
                    normalization_version=OFFICIAL_NOTICE_NORMALIZATION_VERSION,
                    normalization_run_id=run.id,
                    is_current=True,
                    normalized_at=now,
                )
                self._session.add(current)
                self._session.flush()
                revisions_inserted += 1
            else:
                rows_unchanged += 1
            self._session.add(
                DistributionEventRevisionOfficialSourceRecord(
                    distribution_event_revision_id=current.id,
                    official_distribution_record_id=source.id,
                    linked_at=now,
                )
            )

        run.status = "completed"
        run.source_rows_examined = len(records)
        run.candidate_rows = len(records)
        run.blocked_rows = 0
        run.events_inserted = events_inserted
        run.revisions_inserted = revisions_inserted
        run.rows_unchanged = rows_unchanged
        run.completed_at = utc_now()
        return _CanonicalPublication(
            run_id=run.id,
            status=run.status,
            events_inserted=events_inserted,
            revisions_inserted=revisions_inserted,
            rows_unchanged=rows_unchanged,
        )


@dataclass(frozen=True)
class _CanonicalPublication:
    run_id: str
    status: str
    events_inserted: int
    revisions_inserted: int
    rows_unchanged: int


def _identity_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _source_signature(
    artifact_sha256: str, scheme_code: str, record_date: str, amount: Decimal
) -> str:
    serialized = json.dumps(
        ("hdfc_amc", artifact_sha256, scheme_code, record_date, str(amount)),
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _revision_signature(
    scheme_code: str, record_date: str, amount: Decimal, revision_number: int
) -> str:
    serialized = json.dumps(
        (scheme_code, record_date, _EVENT_TYPE, str(amount), revision_number),
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
