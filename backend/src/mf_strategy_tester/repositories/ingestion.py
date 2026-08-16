from dataclasses import dataclass

from sqlalchemy import select
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

    def complete_batch(self, batch: IngestionBatchRecord, rows_received: int) -> None:
        batch.status = "completed"
        batch.completed_at = utc_now()
        batch.rows_received = rows_received
        batch.rows_accepted = rows_received
        batch.rows_rejected = 0
        self._session.commit()

    def fail_batch(self, batch: IngestionBatchRecord, error_details: str) -> None:
        self._session.rollback()
        batch.status = "failed"
        batch.completed_at = utc_now()
        batch.rows_accepted = 0
        batch.error_details = error_details[:4000]
        self._session.commit()
