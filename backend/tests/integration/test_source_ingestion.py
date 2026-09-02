from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import IngestionBatchRecord, SourceArtifactRecord
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.amfi import current_nav_request
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.errors import SourceParseError
from mf_strategy_tester.ingestion.http import DownloadedSource
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.source_ingestion import SourceIngestionService

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amfi"


class FakeDownloader:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def download(self, url: str) -> DownloadedSource:
        return DownloadedSource(
            content=self._content,
            media_type="text/plain",
            status_code=200,
            final_url=url,
        )


def create_service(tmp_path: Path, content: bytes) -> tuple[SourceIngestionService, Session]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'ingestion.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = session_factory()
    return (
        SourceIngestionService(
            IngestionRepository(session),
            ArtifactStore(tmp_path / "raw"),
            FakeDownloader(content),
        ),
        session,
    )


def test_repeated_source_reuses_immutable_artifact(tmp_path: Path) -> None:
    content = (FIXTURES / "nav_current.txt").read_bytes()
    service, session = create_service(tmp_path, content)

    first = service.ingest(current_nav_request())
    second = service.ingest(current_nav_request())

    assert first.rows_received == 2
    assert first.artifact_reused is False
    assert second.artifact_reused is True
    assert first.sha256 == second.sha256
    assert session.scalar(select(func.count()).select_from(SourceArtifactRecord)) == 1
    assert session.scalar(select(func.count()).select_from(IngestionBatchRecord)) == 2
    session.close()


def test_invalid_source_is_retained_and_batch_is_failed(tmp_path: Path) -> None:
    invalid_source = b"unexpected;header\n"
    service, session = create_service(tmp_path, invalid_source)

    with pytest.raises(SourceParseError, match="unexpected AMFI NAV header"):
        service.ingest(current_nav_request())

    batch = session.scalar(select(IngestionBatchRecord))
    assert batch is not None
    assert batch.status == "failed"
    assert batch.artifact_id is not None
    assert "SourceParseError" in (batch.error_details or "")
    assert session.scalar(select(func.count()).select_from(SourceArtifactRecord)) == 1
    session.close()


def test_stale_batch_reconciliation_is_explicit_and_guarded(tmp_path: Path) -> None:
    service, session = create_service(tmp_path, b"unused")
    del service
    repository = IngestionRepository(session)
    batch = repository.start_batch(
        provider="amfi",
        source_type="historical_nav",
        source_url="https://example.invalid/nav",
        request_parameters={"mf": "20"},
        parser_version="test",
    )
    batch.started_at = datetime(2026, 8, 16, tzinfo=UTC)
    session.commit()

    reconciled = repository.reconcile_stale_batch(
        batch.id,
        stale_before=datetime(2026, 8, 17, tzinfo=UTC),
        reason="worker process was verified absent",
    )

    assert reconciled.status == "failed"
    assert reconciled.completed_at is not None
    assert reconciled.error_details == ("StaleBatchReconciled: worker process was verified absent")
    with pytest.raises(ValueError, match="is not running"):
        repository.reconcile_stale_batch(
            batch.id,
            stale_before=datetime(2026, 8, 17, tzinfo=UTC),
            reason="second attempt",
        )
    session.close()


def test_stale_batch_reconciliation_rejects_recent_or_naive_cutoff(tmp_path: Path) -> None:
    service, session = create_service(tmp_path, b"unused")
    del service
    repository = IngestionRepository(session)
    batch = repository.start_batch(
        provider="amfi",
        source_type="historical_nav",
        source_url="https://example.invalid/nav",
        request_parameters={},
        parser_version="test",
    )
    batch.started_at = datetime(2026, 8, 17, tzinfo=UTC)
    session.commit()

    with pytest.raises(ValueError, match="must include a timezone offset"):
        repository.reconcile_stale_batch(
            batch.id,
            stale_before=datetime(2026, 8, 18),
            reason="worker absent",
        )
    with pytest.raises(ValueError, match="is not before the stale cutoff"):
        repository.reconcile_stale_batch(
            batch.id,
            stale_before=datetime(2026, 8, 17, tzinfo=UTC),
            reason="worker absent",
        )
    assert batch.status == "running"
    session.close()
