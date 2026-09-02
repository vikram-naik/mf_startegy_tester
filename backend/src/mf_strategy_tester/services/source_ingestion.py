import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mf_strategy_tester.db.models import IngestionBatchRecord
from mf_strategy_tester.ingestion.amfi import AmfiDistributionParser, ArtifactParser
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.http import DownloadedSource
from mf_strategy_tester.repositories.ingestion import IngestionRepository

logger = logging.getLogger(__name__)


class Downloader(Protocol):
    def download(self, url: str) -> DownloadedSource: ...


class SourceRequest(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def source_type(self) -> StrEnum: ...

    @property
    def url(self) -> str: ...

    @property
    def parameters(self) -> dict[str, str]: ...

    @property
    def parser(self) -> ArtifactParser: ...


@dataclass(frozen=True)
class IngestionResult:
    batch_id: str
    status: str
    source_type: str
    sha256: str
    artifact_reused: bool
    rows_received: int
    rows_accepted: int
    rows_rejected: int


class SourceIngestionService:
    """Capture first, then validate; failed source payloads remain auditable."""

    def __init__(
        self,
        repository: IngestionRepository,
        artifact_store: ArtifactStore,
        downloader: Downloader,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._downloader = downloader

    def ingest(
        self,
        request: SourceRequest,
        *,
        quarantine_record_errors: bool = False,
    ) -> IngestionResult:
        batch = self._repository.start_batch(
            provider=request.provider,
            source_type=request.source_type.value,
            source_url=request.url,
            request_parameters=request.parameters,
            parser_version=request.parser.version,
        )
        logger.info(
            "ingestion_started",
            extra={
                "event_data": {
                    "batch_id": batch.id,
                    "provider": batch.provider,
                    "source_type": batch.source_type,
                    "parser_version": batch.parser_version,
                }
            },
        )
        try:
            downloaded = self._downloader.download(request.url)
            stored = self._artifact_store.store(downloaded.content)
            attachment = self._repository.attach_artifact(
                batch,
                stored,
                media_type=downloaded.media_type,
                http_status=downloaded.status_code,
                final_url=downloaded.final_url,
            )
            if quarantine_record_errors:
                if not isinstance(request.parser, AmfiDistributionParser):
                    raise ValueError(
                        "record-error quarantine is supported only for AMFI distributions"
                    )
                parsed = request.parser.parse_with_issues(downloaded.content)
                rows_received = parsed.rows_received
                rows_accepted = len(parsed.records)
                rows_rejected = len(parsed.issues)
            else:
                rows_received = request.parser.validate(downloaded.content)
                rows_accepted = rows_received
                rows_rejected = 0
            self._repository.complete_batch(
                batch,
                rows_received,
                rows_accepted=rows_accepted,
                rows_rejected=rows_rejected,
            )
            logger.info(
                "ingestion_completed",
                extra={
                    "event_data": {
                        "batch_id": batch.id,
                        "source_type": batch.source_type,
                        "artifact_sha256": attachment.artifact.sha256,
                        "artifact_reused": attachment.reused,
                        "rows_received": rows_received,
                        "rows_accepted": rows_accepted,
                        "rows_rejected": rows_rejected,
                    }
                },
            )
            return IngestionResult(
                batch_id=batch.id,
                status=batch.status,
                source_type=batch.source_type,
                sha256=attachment.artifact.sha256,
                artifact_reused=attachment.reused,
                rows_received=rows_received,
                rows_accepted=rows_accepted,
                rows_rejected=rows_rejected,
            )
        except (Exception, KeyboardInterrupt) as error:
            self._mark_failed(batch, error)
            logger.exception(
                "ingestion_failed",
                extra={
                    "event_data": {
                        "batch_id": batch.id,
                        "source_type": batch.source_type,
                        "failure_type": type(error).__name__,
                    }
                },
            )
            raise

    def _mark_failed(self, batch: IngestionBatchRecord, error: BaseException) -> None:
        message = str(error) or "interrupted by user"
        self._repository.fail_batch(batch, f"{type(error).__name__}: {message}")
