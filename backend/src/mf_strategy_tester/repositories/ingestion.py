from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, joinedload

from mf_strategy_tester.db.models import (
    IngestionBatchRecord,
    SourceArtifactRecord,
    utc_now,
)
from mf_strategy_tester.ingestion.artifacts import StoredArtifact


@dataclass(frozen=True)
class ArtifactAttachment:
    artifact: SourceArtifactRecord
    reused: bool


class IngestionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start_batch(
        self,
        *,
        provider: str,
        source_type: str,
        source_url: str,
        request_parameters: dict[str, str],
        parser_version: str,
    ) -> IngestionBatchRecord:
        batch = IngestionBatchRecord(
            provider=provider,
            source_type=source_type,
            source_url=source_url,
            request_parameters=request_parameters,
            parser_version=parser_version,
            status="running",
        )
        self._session.add(batch)
        self._session.commit()
        return batch

    def list_batches(self, *, limit: int, offset: int) -> list[IngestionBatchRecord]:
        statement = (
            select(IngestionBatchRecord)
            .options(joinedload(IngestionBatchRecord.artifact))
            .order_by(IngestionBatchRecord.started_at.desc(), IngestionBatchRecord.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(statement).all())

    def get_batch(self, batch_id: str) -> IngestionBatchRecord:
        batch = self._session.scalar(
            select(IngestionBatchRecord)
            .options(joinedload(IngestionBatchRecord.artifact))
            .where(IngestionBatchRecord.id == batch_id)
        )
        if batch is None:
            raise LookupError(f"ingestion batch {batch_id} does not exist")
        return batch

    def attach_artifact(
        self,
        batch: IngestionBatchRecord,
        stored: StoredArtifact,
        *,
        media_type: str,
        http_status: int,
        final_url: str,
    ) -> ArtifactAttachment:
        artifact = self._session.scalar(
            select(SourceArtifactRecord).where(SourceArtifactRecord.sha256 == stored.sha256)
        )
        reused = artifact is not None
        if artifact is None:
            artifact = SourceArtifactRecord(
                sha256=stored.sha256,
                byte_size=stored.byte_size,
                media_type=media_type,
                storage_path=stored.relative_path,
            )
            self._session.add(artifact)
            self._session.flush()
        elif (
            artifact.byte_size != stored.byte_size or artifact.storage_path != stored.relative_path
        ):
            raise ValueError("stored artifact metadata conflicts with the existing checksum")

        batch.artifact_id = artifact.id
        batch.artifact_reused = reused
        batch.http_status = http_status
        batch.final_url = final_url
        self._session.commit()
        return ArtifactAttachment(artifact=artifact, reused=reused)

    def complete_batch(
        self,
        batch: IngestionBatchRecord,
        rows_received: int,
        *,
        rows_accepted: int | None = None,
        rows_rejected: int = 0,
    ) -> None:
        accepted = rows_received if rows_accepted is None else rows_accepted
        if rows_received != accepted + rows_rejected:
            raise ValueError("ingestion row counts must satisfy received = accepted + rejected")
        batch.status = "completed"
        batch.completed_at = utc_now()
        batch.rows_received = rows_received
        batch.rows_accepted = accepted
        batch.rows_rejected = rows_rejected
        self._session.commit()

    def fail_batch(self, batch: IngestionBatchRecord, error_details: str) -> None:
        self._session.rollback()
        batch.status = "failed"
        batch.completed_at = utc_now()
        batch.rows_accepted = 0
        batch.error_details = error_details[:4000]
        self._session.commit()

    def reconcile_stale_batch(
        self,
        batch_id: str,
        *,
        stale_before: datetime,
        reason: str,
    ) -> IngestionBatchRecord:
        """Close one abandoned batch after an operator verifies that no worker owns it."""
        if stale_before.tzinfo is None or stale_before.utcoffset() is None:
            raise ValueError("stale batch cutoff must include a timezone offset")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("stale batch reconciliation reason cannot be empty")

        batch = self.get_batch(batch_id)
        if batch.status != "running":
            raise ValueError(f"ingestion batch {batch_id} is not running (status={batch.status})")
        if batch.started_at >= stale_before:
            raise ValueError(
                f"ingestion batch {batch_id} started at {batch.started_at.isoformat()}, "
                f"which is not before the stale cutoff {stale_before.isoformat()}"
            )

        completed_at = utc_now()
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(IngestionBatchRecord)
                .where(
                    IngestionBatchRecord.id == batch_id,
                    IngestionBatchRecord.status == "running",
                    IngestionBatchRecord.started_at < stale_before,
                )
                .values(
                    status="failed",
                    completed_at=completed_at,
                    error_details=f"StaleBatchReconciled: {normalized_reason}"[:4000],
                )
            ),
        )
        if result.rowcount != 1:
            self._session.rollback()
            raise RuntimeError(
                f"ingestion batch {batch_id} changed during stale-batch reconciliation"
            )
        self._session.commit()
        return self.get_batch(batch_id)
