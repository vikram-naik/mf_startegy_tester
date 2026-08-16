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
