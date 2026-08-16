from datetime import date
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiFundRecord,
    IngestionBatchRecord,
    NavSyncCoverageRecord,
    NavSyncRunRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.http import DownloadedSource
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.nav_sync import NavSyncService
from mf_strategy_tester.services.source_ingestion import SourceIngestionService


class InterruptingDownloader:
    def __init__(self) -> None:
        self.calls = 0

    def download(self, url: str) -> DownloadedSource:
        self.calls += 1
        if self.calls > 1:
            raise KeyboardInterrupt
        records = "".join(
            f'{{\\"mf_id\\":\\"{identifier}\\",\\"mf_name\\":\\"Fund {identifier}\\",'
            '\\"amc_name\\":\\"Example AMC\\"}'
            for identifier in range(1, 11)
        )
        return DownloadedSource(
            content=records.encode(),
            media_type="text/html",
            status_code=200,
            final_url=url,
        )


def test_coverage_intervals_expose_gaps_and_merge_adjacent_windows(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'coverage.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    batch = IngestionBatchRecord(
        provider="amfi",
        source_type="historical_nav",
        source_url="https://example.invalid/source",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    session.add(batch)
    session.flush()
    session.add(
        AmfiFundRecord(
            mutual_fund_id="3",
            mutual_fund_name="Example Fund",
            catalog_batch_id=batch.id,
        )
    )
    session.flush()
    session.add_all(
        [
            NavSyncCoverageRecord(
                mutual_fund_id="3",
                start_date=date(2020, 1, 1),
                end_date=date(2020, 1, 31),
                last_batch_id=batch.id,
            ),
            NavSyncCoverageRecord(
                mutual_fund_id="3",
                start_date=date(2020, 3, 1),
                end_date=date(2020, 3, 31),
                last_batch_id=batch.id,
            ),
        ]
    )
    session.commit()
    service = NavSyncService(
        session,
        cast(SourceIngestionService, object()),
        IngestionRepository(session),
        ArtifactStore(tmp_path / "raw"),
    )

    assert service._next_uncovered_interval("3", date(2020, 1, 1), date(2020, 3, 31)) == (
        date(2020, 2, 1),
        date(2020, 2, 29),
    )
    service._record_coverage("3", date(2020, 2, 1), date(2020, 2, 29), batch.id)
    session.commit()

    ranges = list(session.scalars(select(NavSyncCoverageRecord)).all())
    assert [(item.start_date, item.end_date) for item in ranges] == [
        (date(2020, 1, 1), date(2020, 3, 31))
    ]
    session.close()


def test_keyboard_interrupt_marks_sync_failed_and_preserves_resume_state(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'interrupt.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    artifact_store = ArtifactStore(tmp_path / "raw")
    repository = IngestionRepository(session)
    ingestion = SourceIngestionService(repository, artifact_store, InterruptingDownloader())

    with pytest.raises(KeyboardInterrupt):
        NavSyncService(session, ingestion, repository, artifact_store).synchronize(
            mode="full",
            requested_start_date=date(2026, 8, 1),
            requested_end_date=date(2026, 8, 1),
            include_current_feed=False,
        )

    run = session.scalar(select(NavSyncRunRecord))
    assert run is not None
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error_details == "KeyboardInterrupt: interrupted by user"
    session.close()
    engine.dispose()
