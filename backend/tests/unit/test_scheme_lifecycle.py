import json
from collections import defaultdict, deque
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiFundRecord,
    AmfiSchemeDetailRecord,
    AmfiSchemeListSnapshotRecord,
    IngestionBatchRecord,
    SchemeLifecycleCheckpointRecord,
    SchemeLifecycleEventRecord,
    SchemeLifecycleIssueRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.amfi import AmfiSchemeDetailsParser
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.errors import SourceDownloadError, SourceParseError
from mf_strategy_tester.ingestion.http import DownloadedSource
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.scheme_lifecycle import SchemeLifecycleSyncService


class _QueuedDownloader:
    def __init__(self) -> None:
        self._responses: defaultdict[str, deque[bytes | Exception]] = defaultdict(deque)
        self.requested_urls: list[str] = []

    def add(self, marker: str, *responses: bytes | Exception) -> None:
        self._responses[marker].extend(responses)

    def download(self, url: str) -> DownloadedSource:
        self.requested_urls.append(url)
        marker = next((item for item in self._responses if item in url), None)
        if marker is None or not self._responses[marker]:
            raise AssertionError(f"unexpected lifecycle URL {url}")
        response = self._responses[marker].popleft()
        if isinstance(response, Exception):
            raise response
        return DownloadedSource(
            content=response,
            media_type="application/json",
            status_code=200,
            final_url=url,
        )


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    batch = IngestionBatchRecord(
        id="fund-catalog-batch",
        provider="amfi",
        source_type="fund_list",
        source_url="https://www.amfiindia.com/otherdata/scheme-details",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    session.add(batch)
    session.flush()
    session.add(
        AmfiFundRecord(
            mutual_fund_id="3",
            mutual_fund_name="Aditya Birla Sun Life Mutual Fund",
            catalog_batch_id=batch.id,
        )
    )
    session.commit()
    return session


def _list_payload(*, name: str = "Aditya Birla Sun Life Multi-Cap Fund") -> bytes:
    return json.dumps([{"scheme_id": "12233", "scheme_name": name}]).encode()


def _detail_payload(
    *,
    fund_id: int = 3,
    scheme_id: int = 12233,
    name: str = "Aditya Birla Sun Life Multi-Cap Fund",
    launch_date: str = "2021-04-19T00:00:00+05:30",
) -> bytes:
    return json.dumps(
        {
            "data": [
                {
                    "MF_Name": "Aditya Birla Sun Life Mutual Fund",
                    "Scheme_Name": name,
                    "Scheme_Objective": "Long-term capital growth.",
                    "SchemeType_Desc": "Open Ended",
                    "SchemeCat_Desc": "Equity Scheme - Multi Cap Fund",
                    "Launch_Date": launch_date,
                    "Scheme_load": "",
                    "Scheme_min_amt": "500",
                    "AMC_Website": "https://mutualfund.adityabirlacapital.com/",
                    "scheme_Id": scheme_id,
                    "MF_Id": fund_id,
                }
            ]
        }
    ).encode()


def _service(
    tmp_path: Path, session: Session, downloader: _QueuedDownloader
) -> SchemeLifecycleSyncService:
    return SchemeLifecycleSyncService(
        session,
        IngestionRepository(session),
        ArtifactStore(tmp_path / "raw"),
        downloader,
        sleep_function=lambda _: None,
    )


def test_full_lifecycle_sync_is_resumable_and_reports_complete_family_coverage(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    downloader = _QueuedDownloader()
    downloader.add("populate-scheme", _list_payload(), _list_payload())
    downloader.add("scheme-details", _detail_payload())
    service = _service(tmp_path, session, downloader)

    first = service.sync(mode="full")
    second = service.sync(mode="full")
    report = service.coverage_report()

    assert first.status == "completed"
    assert first.families_completed == 1
    assert first.detail_rows_inserted == 1
    assert second.families_completed == 0
    assert second.families_skipped == 1
    assert len(downloader.requested_urls) == 3
    assert session.scalar(select(func.count()).select_from(AmfiSchemeListSnapshotRecord)) == 2
    assert session.scalar(select(func.count()).select_from(AmfiSchemeDetailRecord)) == 1
    assert session.scalar(select(func.count()).select_from(SchemeLifecycleEventRecord)) == 1
    assert session.scalar(select(func.count()).select_from(SchemeLifecycleCheckpointRecord)) == 1
    assert report.latest_catalog_families == 1
    assert report.detail_checkpoints == 1
    assert report.families_with_launch_event == 1
    assert report.latest_catalog_families_without_detail == 0


def test_refresh_retains_conflicting_launch_dates_and_undated_name_change_as_issues(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    downloader = _QueuedDownloader()
    changed_name = "Aditya Birla Sun Life Multicap Fund"
    downloader.add("populate-scheme", _list_payload(), _list_payload(name=changed_name))
    downloader.add(
        "scheme-details",
        _detail_payload(),
        _detail_payload(name=changed_name, launch_date="2021-04-20T00:00:00+05:30"),
    )
    service = _service(tmp_path, session, downloader)
    service.sync(mode="full")

    refreshed = service.sync(mode="refresh")
    report = service.coverage_report()

    assert refreshed.status == "completed_with_issues"
    assert refreshed.issue_counts == {
        "name_changed_without_effective_date": 1,
        "launch_date_conflict": 1,
    }
    assert session.scalar(select(func.count()).select_from(AmfiSchemeDetailRecord)) == 2
    assert session.scalar(select(func.count()).select_from(SchemeLifecycleEventRecord)) == 2
    assert report.families_with_conflicting_launch_dates == 1


def test_identity_mismatch_is_rejected_without_corrupting_checkpoint(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedDownloader()
    downloader.add("populate-scheme", _list_payload())
    downloader.add("scheme-details", _detail_payload(scheme_id=99999))

    result = _service(tmp_path, session, downloader).sync(mode="full")

    assert result.status == "completed_with_issues"
    assert result.families_failed == 1
    assert result.detail_rows_received == 1
    assert result.detail_rows_rejected == 1
    assert session.scalar(select(func.count()).select_from(AmfiSchemeDetailRecord)) == 0
    assert session.scalar(select(func.count()).select_from(SchemeLifecycleCheckpointRecord)) == 0
    issue = session.scalar(select(SchemeLifecycleIssueRecord))
    assert issue is not None and issue.issue_code == "identity_mismatch"


def test_removed_catalog_member_is_not_inferred_as_closure(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedDownloader()
    downloader.add("populate-scheme", _list_payload(), b"[]")
    downloader.add("scheme-details", _detail_payload())
    service = _service(tmp_path, session, downloader)
    service.sync(mode="full")

    result = service.sync(mode="refresh")

    assert result.issue_counts == {"catalog_member_removed": 1}
    assert (
        session.scalar(
            select(func.count())
            .select_from(SchemeLifecycleEventRecord)
            .where(SchemeLifecycleEventRecord.event_type == "closure")
        )
        == 0
    )


def test_detail_transport_failure_is_audited_and_does_not_abort_other_funds(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    downloader = _QueuedDownloader()
    downloader.add("populate-scheme", _list_payload())
    downloader.add("scheme-details", SourceDownloadError("source unavailable"))

    result = _service(tmp_path, session, downloader).sync(mode="full")

    assert result.status == "completed_with_issues"
    assert result.funds_completed == 1
    assert result.families_failed == 1
    issue = session.scalar(select(SchemeLifecycleIssueRecord))
    assert issue is not None and issue.issue_code == "scheme_detail_failure"
    failed_batch = session.scalar(
        select(IngestionBatchRecord).where(IngestionBatchRecord.status == "failed")
    )
    assert failed_batch is not None
    assert "SourceDownloadError" in (failed_batch.error_details or "")


def test_scheme_details_parser_rejects_timezone_naive_launch_date() -> None:
    with pytest.raises(SourceParseError, match="timezone offset"):
        AmfiSchemeDetailsParser().parse_records(_detail_payload(launch_date="2021-04-19T00:00:00"))


def test_scheme_details_parser_rejects_wrong_timezone_and_structural_drift() -> None:
    with pytest.raises(SourceParseError, match="Asia/Kolkata"):
        AmfiSchemeDetailsParser().parse_records(
            _detail_payload(launch_date="2021-04-19T00:00:00+00:00")
        )
    document = json.loads(_detail_payload())
    document["data"][0]["unexpected"] = "field"
    with pytest.raises(SourceParseError, match="unexpected scheme-details structure"):
        AmfiSchemeDetailsParser().parse_records(json.dumps(document).encode())
